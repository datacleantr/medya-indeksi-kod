"""Arşiv dışı künyeleri (ek toplama) BigQuery'ye yükler: gdelt_tr.extra_articles. Yayındaki panel buradan okur.

Yerel arşiv (data/archive/extra/, pipeline/extra_collect.py) asıl kaynaktır; BigQuery onun kopyasıdır. Tablo
aylık bölümlüdür (pub_timestamp) ve site ile kümelenir. Başlıktan çıkarılan adlar (pipeline/entities.py)
`adlar` kolonunda "Ad|tür;Ad|tür" biçimindedir.

Maliyet: Hiç sorgu çalıştırılmaz. Yükleme işi (load job) ve bölüm değiştirme ücretsizdir; yalnızca depolama
ücretlidir (~0,4 GB, ayda ~0,01 $). Panel Storage Read API ile okur (ayda 300 TiB'a kadar ücretsiz).

Artımlıdır: data/archive/extra_upload.json her ayın imzasını (o aya ait dosyaların ve ad dosyasının değişme
zamanı) tutar; yalnızca imzası değişen aylar yeniden yazılır (`tablo$YYYYAA`, WRITE_TRUNCATE). Çok sayıda ay
değiştiyse (ilk yükleme, ad sözlüğünün yenilenmesi) tablo tek işte baştan yazılır.

    venv/bin/python pipeline/extra_upload.py            # değişen ayları yükle
    venv/bin/python pipeline/extra_upload.py --plan     # neyin yükleneceğini göster, yükleme
    venv/bin/python pipeline/extra_upload.py --full     # tabloyu baştan yaz

extra_collect.py her toplamadan ve ad çıkarmadan sonra çağırır. Kimlik: gcloud ADC.
"""
import argparse
import collections
import json
import os
import sys
from pathlib import Path

import duckdb
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from source_index import ARCHIVE  # noqa: E402

PROJECT = os.environ.get("GDELT_BQ_PROJECT", "banded-scion-476220-r0")
DATASET = "gdelt_tr"
TABLE = "extra_articles"
EXTRA = ARCHIVE / "extra"
ENTITIES = ARCHIVE / "extra_entities.parquet"
MANIFEST = ARCHIVE / "extra_upload.json"
FULL_REWRITE_MIN_MONTHS = 24   # bundan çok ay değiştiyse bölüm bölüm değil, tablo tek işte yazılır


def schema():
    from google.cloud import bigquery

    S = bigquery.SchemaField
    return [
        S("site", "STRING", description="Yayının alan adı (KEY_OUTLETS ile aynı)"),
        S("url", "STRING"),
        S("title", "STRING", description="Başlık; site haritasından gelen kayıtlarda çoğunlukla boş"),
        S("pub_timestamp", "TIMESTAMP"),
        S("precision", "STRING", description="Tarih hassasiyeti: saat / gün / ay"),
        S("method", "STRING", description="Toplama yolu: rss / haber-haritası / arşiv-haritası"),
        S("collected_at", "TIMESTAMP"),
        S("adlar", "STRING", description="Başlıktan çıkarılan adlar: 'Ad|tür;Ad|tür' (pipeline/entities.py)"),
    ]


def month_signatures() -> dict:
    """Ay (YYYY-AA) -> imza. İmza, o aya ait tüm yayın dosyalarının değişme zamanları ve ad dosyasınınkidir."""
    ent = ENTITIES.stat().st_mtime_ns if ENTITIES.exists() else 0
    sig = collections.defaultdict(list)
    for f in EXTRA.glob("*/*.parquet"):
        sig[f.stem].append(f"{f.parent.name}:{f.stat().st_mtime_ns}")
    return {m: f"{ent}|" + ",".join(sorted(v)) for m, v in sig.items()}


def month_frame(months: list | None) -> pd.DataFrame:
    """Verilen ayların (None: tümü) künyeleri ve adları; BigQuery şemasındaki sırayla."""
    files = f"'{EXTRA}/*/*.parquet'" if months is None else \
        "[" + ", ".join(f"'{p}'" for m in months for p in EXTRA.glob(f"*/{m}.parquet")) + "]"
    names = (f"LEFT JOIN (SELECT url, string_agg(ad || '|' || tur, ';') AS adlar FROM read_parquet('{ENTITIES}') "
             "WHERE ad IS NOT NULL GROUP BY url) e USING (url)") if ENTITIES.exists() else ""
    adlar = "e.adlar" if names else "CAST(NULL AS VARCHAR)"
    return duckdb.sql(
        f"""SELECT k.site, k.url, NULLIF(k.title, '') AS title, k.pub_timestamp, k.precision, k.method,
                   k.collected_at, {adlar} AS adlar
            FROM read_parquet({files}) k {names}""").df()


def upload(full: bool = False, plan: bool = False) -> int:
    from google.cloud import bigquery

    if not EXTRA.is_dir():
        print("[ATLANDI] ek toplama yok")
        return 0
    client = bigquery.Client(project=PROJECT)
    ref = f"{PROJECT}.{DATASET}.{TABLE}"
    table = bigquery.Table(ref, schema=schema())
    table.time_partitioning = bigquery.TimePartitioning(type_=bigquery.TimePartitioningType.MONTH,
                                                        field="pub_timestamp")
    table.clustering_fields = ["site"]
    table.description = ("Arşiv dışı yayınların künyeleri (site haritası ve RSS; pipeline/extra_collect.py). "
                         "Yerel arşivin kopyasıdır; pipeline/extra_upload.py yazar.")
    old = json.loads(MANIFEST.read_text()) if MANIFEST.exists() and not full else {}
    new = month_signatures()
    changed = sorted(m for m, s in new.items() if old.get(m) != s)
    removed = sorted(set(old) - set(new))
    whole = full or not old or len(changed) >= FULL_REWRITE_MIN_MONTHS
    if plan:
        print(f"{'Tüm tablo' if whole else f'{len(changed)} ay'} yazılacak"
              + (f": {', '.join(changed[:12])}{' …' if len(changed) > 12 else ''}" if not whole else "")
              + (f"; silinecek ay: {', '.join(removed)}" if removed else ""))
        return 0
    if not changed and not removed:
        print("[OK] BigQuery ek tablo güncel")
        return 0
    client.create_table(table, exists_ok=True)
    cfg = bigquery.LoadJobConfig(schema=schema(), write_disposition="WRITE_TRUNCATE")
    total = 0
    if whole:
        cfg.time_partitioning = table.time_partitioning
        cfg.clustering_fields = table.clustering_fields
        df = month_frame(None)
        client.load_table_from_dataframe(df, ref, job_config=cfg).result()
        total = len(df)
    else:
        for m in changed:
            df = month_frame([m])
            client.load_table_from_dataframe(df, f"{ref}${m.replace('-', '')}", job_config=cfg).result()
            total += len(df)
        for m in removed:  # yerelde silinen ay: bölüm silinir (sorgu değil, tablo işlemi)
            client.delete_table(f"{ref}${m.replace('-', '')}", not_found_ok=True)
    MANIFEST.write_text(json.dumps(new, indent=0, sort_keys=True))
    print(f"[OK] BigQuery ek tablo: {'tamamı' if whole else f'{len(changed)} ay'} yazıldı, {total:,} satır → {ref}")
    return total


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--full", action="store_true", help="tabloyu baştan yaz")
    p.add_argument("--plan", action="store_true", help="yalnızca neyin yazılacağını göster")
    a = p.parse_args()
    upload(a.full, a.plan)


if __name__ == "__main__":
    main()
