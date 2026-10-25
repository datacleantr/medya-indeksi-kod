"""Günlük özet katmanı (N1): gün × çerçeve / tema ailesi × yayın için haber sayısı, pay ve ortalama ton.

Neden: Panel her aramada her şeyi baştan hesaplıyor. Haftalık bülten (B1), günlük brifing / anlatıcı (N2),
sapma tespiti (N4) ve MCP sunucusu (M1) aynı özete ihtiyaç duyuyor. Bu betik o özeti bir kez üretir.

Neden SQL değil Python: Çerçeveler başlıktan `assign_frames` ile atanır, başlık `clean_title` ile temizlenir.
Bunları BigQuery SQL'inde yeniden yazmak panelle sapma riski yaratırdı. Burada `cases/common.py:app_ns()` ile
app.py'nin kendi fonksiyonları kullanılır; panel ve özet her zaman aynı tanımı paylaşır.

    venv/bin/python pipeline/ozet.py kur              # değişen günleri işler (ilk çalışma: tüm arşiv ~20 dk)
    venv/bin/python pipeline/ozet.py kur --full       # baştan kurar
    venv/bin/python pipeline/ozet.py durum            # tablonun kapsamı ve boyutu
    venv/bin/python pipeline/ozet.py yukle            # BigQuery'ye yazar (gdelt_tr.ozet_gunluk)
    venv/bin/python pipeline/ozet.py yukle --plan     # ne yazılacağını gösterir, yazmaz

Maliyet: Hiç sorgu çalıştırılmaz. Yerel arşivden okunur; BigQuery'ye yükleme işi (load job) ücretsizdir,
yalnızca depolama ücretlidir (ölçüm 23.10.2026: 1,29 milyon satır = 120 MB, ~0,0024 $/ay). Panel Storage Read
API ile okur; sorgu kotasına sayılmaz.

Tanımlar (tabloyu okuyan herkesin bilmesi gerekenler):
- `gun` arşiv dosyasının günüdür (GKG bölüm günü, UTC) — `daily_totals` ve `source_daily` ile aynı anahtar.
  Haberlerin Türkiye saatine çevrilmiş tarihi değildir; gün sınırındaki haberler GKG gününde sayılır.
- `haber` tekil URL sayısıdır (`preprocess` URL'leri tekilleştirir), ham satır sayısı değil.
- Bir haber birden fazla çerçeveye girebilir; bu yüzden çerçeve payları toplamı 1'i aşar. Hiçbirine uymayan
  haber "Diğer" çerçevesindedir. Aynı şey tema aileleri için de geçerlidir.
- `pay` = `haber` / aynı gün ve aynı `yayin` kapsamındaki `boyut_tur = 'toplam'` satırının `haber` değeri.
  Payda tablonun kendi içindedir; dışarıdan bir tabloya bağlı değildir.
- `daily_totals.unique_urls` ile arasında **~%1 fark** vardır (ölçüm: ortalama oran 0,9999; %97,2–%112 aralığı).
  İki zıt etkiden doğar: (a) `preprocess` URL'deki HTML kuyruğunu silip tekilleştirir, bu sayıyı **azaltır**
  (20.10.2026'da 253 kayıt); (b) burada dil süzgeci yoktur, `daily_totals` ise yalnızca `srclc:tur` sayar, bu
  sayıyı **artırır** (aynı gün 87 kayıt). 2024'e kadar (a) baskındı ve özet biraz yüksekti; sonrasında tersine
  döndü. Yani özet `daily_totals`'tan sistematik olarak büyük veya küçük değildir.
- Yayın satırları yalnızca `KEY_OUTLETS` alan adları içindir. Diğer siteler yalnızca `yayin = ''`
  toplamlarına girer (site bazında ham sayı zaten `source_daily`de var).
- `cerceve_seti` hangi çerçeve setiyle üretildiğini söyler. Set değişirse tablo baştan kurulmalıdır.
"""
import argparse
import json
import logging
import sys
from datetime import date
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from cases.common import ARCHIVE, app_ns  # noqa: E402

# Panel çalışmadan çağrılan @st.cache_data fonksiyonları "No runtime found" uyarısı basar. Streamlit kendi
# log düzeyini import sırasında kuruyor; bu yüzden önce import edilip sonra susturulur (app_ns'den önce).
import streamlit  # noqa: E402,F401
# Streamlit her alt loggera kendi düzeyini yazdığı için üst logger yetmez; ilgili olan adıyla susturulur
logging.getLogger("streamlit.runtime.caching.cache_data_api").setLevel(logging.ERROR)

ARTICLES = ARCHIVE / "articles"
OUT = ARCHIVE / "ozet_gunluk.parquet"
MANIFEST = ARCHIVE / "ozet_gunluk.json"

PROJECT = __import__("os").environ.get("GDELT_BQ_PROJECT", "banded-scion-476220-r0")
DATASET = "gdelt_tr"
TABLE = "ozet_gunluk"
FRAME_SET = "Genel"
# preprocess'in istediği kolonlar + çerçeve ve tema için gerekenler
COLS = ["pub_timestamp", "SourceCommonName", "DocumentIdentifier", "Themes", "V2Tone", "PageTitle",
        "TranslationInfo"]


def outlet_index(ns: dict) -> dict:
    """KEY_OUTLETS alan adı -> (yayın adı, yayın grubu). Eşleşme tam alan adıdır (panelin kapsam denetimiyle aynı)."""
    return {d: (name, group)
            for group, outlets in ns["KEY_OUTLETS"].items()
            for name, domains in outlets.items()
            for d in domains}


def day_rows(path: Path, ns: dict, frame_key: tuple, outlets: dict) -> list:
    """Bir arşiv gününün özet satırları."""
    gun = date.fromisoformat(path.stem)
    raw = pd.read_parquet(path, columns=[c for c in COLS if c])
    if raw.empty:
        return []
    df = ns["preprocess"](raw)
    if df.empty:
        return []
    df["Frames"] = ns["assign_frames"](df[["TitleCore", "DocumentIdentifier"]], frame_key)

    get_family = ns["get_theme_family"]
    families = df["Themes"].map(
        lambda t: sorted({get_family(c) for c in t.split(";") if c}) or ["Temasız"]
    )
    frames = df["Frames"].str.split(";")

    rows = []

    def add(scope: str, yayin: str, grup: str, sub: pd.DataFrame, labels: pd.Series | None, tur: str):
        """Bir kapsam (tüm yayınlar veya tek yayın) için boyut satırları."""
        if sub.empty:
            return
        if labels is None:   # toplam satırı
            rows.append(dict(gun=gun, boyut_tur="toplam", boyut="", yayin=yayin, yayin_grubu=grup,
                             haber=len(sub), pay=1.0, ort_ton=float(sub["AvgTone"].mean()),
                             kaynak_sayisi=int(sub["SourceCommonName"].nunique())))
            return
        long = labels.loc[sub.index].explode()
        tone = sub["AvgTone"].reindex(long.index)
        g = tone.groupby(long.to_numpy())
        for boyut, n in g.size().items():
            rows.append(dict(gun=gun, boyut_tur=tur, boyut=str(boyut), yayin=yayin, yayin_grubu=grup,
                             haber=int(n), pay=float(n) / len(sub), ort_ton=float(g.mean()[boyut]),
                             kaynak_sayisi=None))
        _ = scope

    add("", "", "", df, None, "")
    add("", "", "", df, frames, "cerceve")
    add("", "", "", df, families, "tema_ailesi")

    # Yayın satırları: yalnızca KEY_OUTLETS alan adları, yalnızca çerçeve (tema ailesi yayın bazında taşınmıyor)
    for site, sub in df[df["SourceCommonName"].isin(outlets)].groupby("SourceCommonName"):
        name, group = outlets[str(site)]
        add("", str(site), group, sub, None, "")
        add("", str(site), group, sub, frames, "cerceve")
        _ = name
    return rows


def signatures() -> dict:
    """Gün -> kaynak dosyanın değişme zamanı. Yalnızca imzası değişen günler yeniden işlenir."""
    return {f.stem: f.stat().st_mtime_ns for f in ARTICLES.glob("*.parquet")}


def build(full: bool = False, limit: int | None = None) -> int:
    if not ARTICLES.is_dir():
        sys.exit(f"Yerel arşiv yok: {ARTICLES}")
    ns = app_ns()
    frame_key = ns["frame_set_key"](ns["FRAME_SETS"][FRAME_SET])
    outlets = outlet_index(ns)

    old = json.loads(MANIFEST.read_text()) if MANIFEST.exists() and not full else {}
    # Çerçeve seti değiştiyse eski satırlar karşılaştırılamaz; tablo baştan kurulur
    if old.get("_cerceve_seti") not in (None, str(frame_key)):
        print("[!] Çerçeve seti değişmiş; tablo baştan kuruluyor.")
        old = {}
    new = signatures()
    changed = sorted(d for d, s in new.items() if old.get(d) != s)
    if limit:
        changed = changed[:limit]
    if not changed:
        print("[OK] özet güncel")
        return 0

    kept = pd.DataFrame()
    if old and OUT.exists():
        kept = pd.read_parquet(OUT)
        kept = kept[~kept["gun"].astype(str).isin(changed)]

    print(f"{len(changed)} gün işlenecek" + (f" ({changed[0]} .. {changed[-1]})" if changed else ""))
    rows, done = [], 0
    for d in changed:
        rows += day_rows(ARTICLES / f"{d}.parquet", ns, frame_key, outlets)
        done += 1
        if done % 200 == 0:
            print(f"  {done}/{len(changed)} gün, {len(rows):,} satır")
    fresh = pd.DataFrame(rows)
    if not fresh.empty:
        fresh["gun"] = pd.to_datetime(fresh["gun"]).dt.date
        fresh["kaynak_sayisi"] = fresh["kaynak_sayisi"].astype("Int64")
        fresh["cerceve_seti"] = FRAME_SET
    out = pd.concat([kept, fresh], ignore_index=True) if len(kept) else fresh
    out = out.sort_values(["gun", "yayin", "boyut_tur", "boyut"]).reset_index(drop=True)
    out.to_parquet(OUT, index=False)
    # Yalnızca işlenen günler imzalanır: --limit ile yarım kalan çalışma kalan günleri "yapıldı" saymaz
    done_sig = {d: s for d, s in old.items() if d != "_cerceve_seti" and d in new}
    done_sig.update({d: new[d] for d in changed})
    MANIFEST.write_text(json.dumps({**done_sig, "_cerceve_seti": str(frame_key)}, indent=0, sort_keys=True))
    print(f"[OK] {OUT.name}: {len(out):,} satır, {OUT.stat().st_size / 1e6:.1f} MB")
    return len(out)


def status():
    if not OUT.exists():
        sys.exit("Özet henüz kurulmadı (pipeline/ozet.py kur).")
    df = pd.read_parquet(OUT)
    print(f"  {OUT}  {len(df):,} satır, {OUT.stat().st_size / 1e6:.1f} MB")
    print(f"  gün aralığı: {df['gun'].min()} .. {df['gun'].max()}  ({df['gun'].nunique():,} gün)")
    print(f"  çerçeve seti: {df['cerceve_seti'].iloc[0]}")
    for tur, sub in df.groupby("boyut_tur"):
        kapsam = "yayın bazında" if (sub["yayin"] != "").any() else "tüm yayınlar"
        print(f"  {tur:<12} {len(sub):>10,} satır  {sub['boyut'].nunique():>4} boyut  ({kapsam})")


def schema():
    from google.cloud import bigquery

    S = bigquery.SchemaField
    return [
        S("gun", "DATE", description="GKG bölüm günü (UTC); daily_totals ve source_daily ile aynı anahtar"),
        S("boyut_tur", "STRING", description="toplam | cerceve | tema_ailesi"),
        S("boyut", "STRING", description="Çerçeve veya tema ailesi adı; toplam satırlarında boş"),
        S("yayin", "STRING", description="KEY_OUTLETS alan adı; boş ise tüm yayınlar"),
        S("yayin_grubu", "STRING", description="KEY_OUTLETS yayın grubu (yayın çizgisi)"),
        S("haber", "INT64", description="Tekil URL sayısı; bir haber birden fazla boyutta sayılabilir"),
        S("pay", "FLOAT64", description="haber / aynı gün ve yayın kapsamındaki toplam; çerçeve payları 1'i aşabilir"),
        S("ort_ton", "FLOAT64", description="GKG AvgTone ortalaması"),
        S("kaynak_sayisi", "INT64", description="Tekil site sayısı; yalnızca toplam satırlarında doludur"),
        S("cerceve_seti", "STRING", description="Çerçevelerin üretildiği FRAME_SETS anahtarı"),
    ]


def upload(plan: bool = False) -> int:
    from google.cloud import bigquery

    if not OUT.exists():
        sys.exit("Özet henüz kurulmadı (pipeline/ozet.py kur).")
    df = pd.read_parquet(OUT)
    df["gun"] = pd.to_datetime(df["gun"])
    ref = f"{PROJECT}.{DATASET}.{TABLE}"
    if plan:
        print(f"{len(df):,} satır → {ref} (tablo baştan yazılır, yükleme işi ücretsiz)")
        return 0
    table = bigquery.Table(ref, schema=schema())
    table.time_partitioning = bigquery.TimePartitioning(type_=bigquery.TimePartitioningType.MONTH, field="gun")
    table.clustering_fields = ["boyut_tur", "boyut"]
    table.description = ("Günlük özet katmanı: gün × çerçeve / tema ailesi × yayın (pipeline/ozet.py). "
                         "Yerel data/archive/ozet_gunluk.parquet'in kopyasıdır.")
    client = bigquery.Client(project=PROJECT)
    client.create_table(table, exists_ok=True)
    cfg = bigquery.LoadJobConfig(schema=schema(), write_disposition="WRITE_TRUNCATE",
                                 time_partitioning=table.time_partitioning,
                                 clustering_fields=table.clustering_fields)
    client.load_table_from_dataframe(df[[f.name for f in schema()]], ref, job_config=cfg).result()
    print(f"[OK] {len(df):,} satır → {ref}")
    return len(df)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    sp = sub.add_parser("kur", help="yerel özeti üret / güncelle")
    sp.add_argument("--full", action="store_true", help="baştan kur")
    sp.add_argument("--limit", type=int, help="yalnızca ilk N günü işle (deneme)")
    sp.set_defaults(fn=lambda a: build(a.full, a.limit))
    sub.add_parser("durum", help="özetin kapsamı").set_defaults(fn=lambda a: status())
    sp = sub.add_parser("yukle", help="BigQuery'ye yaz")
    sp.add_argument("--plan", action="store_true", help="yalnızca göster")
    sp.set_defaults(fn=lambda a: upload(a.plan))
    a = p.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
