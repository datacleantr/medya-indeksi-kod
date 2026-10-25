"""gdelt_tr tablolarını yerel Parquet arşivine indirir (data/archive/<tablo>/<gün>.parquet).

Kullanım (proje kökünden):
  venv/bin/python pipeline/export.py --plan                    # neyin indirileceğini ve boyutunu gösterir
  venv/bin/python pipeline/export.py                           # eksik veya BigQuery'de değişmiş günleri indirir
  venv/bin/python pipeline/export.py --tables articles --since 2026-01-01

Okuma BigQuery Storage Read API ile yapılır: sorgu çalıştırmaz, günlük sorgu kotasına sayılmaz ve ayda ilk
300 TiB ücretsizdir. Verinin internete çıkışı ise ücretlidir (~0,12 $/GB). Veri LZ4 ile sıkıştırılmış gelir
(ağdan geçen ~BigQuery boyutunun %40'ı): 1 yıl ~1,5 $, tüm geçmiş tahminen 5-7 $. Tek sorgu, bölüm listesini almak için INFORMATION_SCHEMA.PARTITIONS'tır (~10 MB).
Her gün ayrı dosyadır; BigQuery'de bir gün yeniden yüklenirse (last_modified_time) o gün tekrar indirilir.
İndirilen günler data/archive/manifest.json'da tutulur.

Yerelde sorgulamak (DuckDB, ücretsiz):
  venv/bin/python -c "import duckdb; print(duckdb.sql(\"SELECT COUNT(*) FROM 'data/archive/articles/*.parquet'\"))"
"""
import argparse
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
from google.cloud import bigquery
from google.cloud.bigquery_storage import BigQueryReadClient, types

PROJECT = os.environ.get("GDELT_BQ_PROJECT", "banded-scion-476220-r0")
DATASET = "gdelt_tr"
ARCHIVE = Path(__file__).resolve().parent.parent / "data" / "archive"
MANIFEST = ARCHIVE / "manifest.json"
# Tablo -> gün bölümü kolonu (None: bölümsüz, tek dosya)
TABLES = {
    "articles": "gkg_day",
    "daily_totals": "day",
    "quotes": "gqg_day",
    "page_changes": "check_day",
    "event_mentions": "mention_day",
    "events": "event_day",
    "topics": None,
}
GB = 1024 ** 3

bq = bigquery.Client(project=PROJECT)
reader = BigQueryReadClient()


def partitions(tables, since):
    """BigQuery'deki bölümler: (tablo, gün 'YYYY-MM-DD' veya None, satır, bayt, son değişiklik)."""
    rows = bq.query(f"""
        SELECT table_name, partition_id, total_rows, total_logical_bytes, last_modified_time
        FROM `{PROJECT}.{DATASET}.INFORMATION_SCHEMA.PARTITIONS`
        WHERE table_name IN UNNEST(@t) AND total_rows > 0""",
        job_config=bigquery.QueryJobConfig(query_parameters=[bigquery.ArrayQueryParameter("t", "STRING", tables)]),
    ).result()
    out = []
    for r in rows:
        pid = r.partition_id
        day = None if pid in (None, "__UNPARTITIONED__") else f"{pid[:4]}-{pid[4:6]}-{pid[6:]}"
        if since and day and day < since:
            continue
        out.append((r.table_name, day, r.total_rows, r.total_logical_bytes, r.last_modified_time.isoformat()))
    return sorted(out)


def download(table, day):
    """Bir günü (veya bölümsüz tabloyu) Arrow olarak okuyup Parquet'e yazar; yazma atomiktir."""
    col = TABLES[table]
    options = types.ReadSession.TableReadOptions(
        row_restriction=f"{col} = DATE '{day}'" if col and day else "",
        # Ağ üzerinden sıkıştırılmış gelir: internete çıkış ücreti ve süre azalır (pyarrow kendisi açar)
        arrow_serialization_options=types.ArrowSerializationOptions(
            buffer_compression=types.ArrowSerializationOptions.CompressionCodec.LZ4_FRAME
        ),
    )
    session = reader.create_read_session(
        parent=f"projects/{PROJECT}",
        read_session=types.ReadSession(
            table=f"projects/{PROJECT}/datasets/{DATASET}/tables/{table}",
            data_format=types.DataFormat.ARROW,
            read_options=options,
        ),
        max_stream_count=4,
    )
    parts = [reader.read_rows(s.name).to_arrow(session) for s in session.streams]
    data = pa.concat_tables(parts) if parts else None
    target = ARCHIVE / table / f"{day or table}.parquet"
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".tmp")
    if data is not None:
        pq.write_table(data, tmp, compression="zstd")
        tmp.replace(target)
    return target, (data.num_rows if data is not None else 0)


def refresh_source_index(tables) -> None:
    """Haberler indirildiyse kaynak dizinini (source_daily.parquet) günceller; panelin kapsam denetimi bunu okur."""
    if "articles" not in tables:
        return
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import source_index

    n = source_index.build()
    print(f"[OK] kaynak dizini güncellendi ({n} gün)")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--tables", default=",".join(TABLES), help="virgülle ayrılmış tablo listesi")
    p.add_argument("--since", help="bu günden (YYYY-MM-DD) eski bölümleri atla")
    p.add_argument("--plan", action="store_true", help="indirmeden yalnızca planı göster")
    p.add_argument("--workers", type=int, default=4)
    a = p.parse_args()
    tables = [t for t in a.tables.split(",") if t]
    unknown = set(tables) - set(TABLES)
    if unknown:
        sys.exit(f"Bilinmeyen tablo: {', '.join(unknown)}")

    manifest = json.loads(MANIFEST.read_text()) if MANIFEST.exists() else {}
    todo = [
        (t, d, n, b, m) for t, d, n, b, m in partitions(tables, a.since)
        if manifest.get(f"{t}/{d}") != m or not (ARCHIVE / t / f"{d or t}.parquet").exists()
    ]
    by_table = {}
    for t, _, n, b, _ in todo:
        c = by_table.setdefault(t, [0, 0, 0])
        c[0] += 1; c[1] += n; c[2] += b
    for t, (k, n, b) in by_table.items():
        print(f"  {t:<15} {k:5} gün  {n:>12,} satır  {b / GB:8.2f} GB (BigQuery boyutu)")
    if not todo:
        print("Arşiv güncel.")
        if not a.plan:
            refresh_source_index(tables)
        return
    if a.plan:
        return

    done = 0
    with ThreadPoolExecutor(a.workers) as pool:
        futures = {pool.submit(download, t, d): (t, d, m) for t, d, _, _, m in todo}
        for f in as_completed(futures):
            t, d, m = futures[f]
            try:
                _, rows = f.result()
            except Exception as e:
                print(f"[HATA] {t} {d}: {e}")
                continue
            manifest[f"{t}/{d}"] = m
            done += 1
            if done % 20 == 0 or done == len(todo):
                MANIFEST.write_text(json.dumps(manifest, indent=0, sort_keys=True))
                print(f"  {done}/{len(todo)} dosya indirildi")
    MANIFEST.write_text(json.dumps(manifest, indent=0, sort_keys=True))
    size = sum(f.stat().st_size for f in ARCHIVE.rglob("*.parquet"))
    print(f"[OK] arşiv: {ARCHIVE} ({size / GB:.2f} GB)")
    refresh_source_index(tables)


if __name__ == "__main__":
    main()
