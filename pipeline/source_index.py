"""Yerel arşivin kaynak dizini: her gün, her site için haber sayısı ve başlıklı haber sayısı.
`kaynak` kolonu: "gdelt" (data/archive/articles) veya "ek" (arşiv dışı toplama, data/archive/extra;
pipeline/extra_collect.py).

Panelin "Veri kapsamı ve boşluklar" bölümü bu dizinden okur: bir sitenin arşivde hiç olmaması (kapsam boşluğu)
ile arşivde olup aranan konuda haber yapmaması (içerik bilgisi) ancak böyle ayırt edilir.

Artımlıdır: yalnızca dizinde olmayan veya dosyası dizinden yeni olan günler yeniden sayılır.
    venv/bin/python pipeline/source_index.py          # güncelle
    venv/bin/python pipeline/source_index.py --full   # baştan kur
"""
import argparse
import os
import sys
from pathlib import Path

import duckdb
import pandas as pd

ARCHIVE = Path(os.environ.get("GDELT_ARCHIVE", Path(__file__).resolve().parent.parent / "data" / "archive"))
ARTICLES = ARCHIVE / "articles"
EXTRA = ARCHIVE / "extra"
INDEX = ARCHIVE / "source_daily.parquet"


def build(full: bool = False) -> int:
    """Dizini günceller; yeniden sayılan GDELT gün sayısını döndürür. Ek toplama bölümü küçük olduğu için
    her seferinde baştan sayılır."""
    files = {p.stem: p for p in ARTICLES.glob("*.parquet")}
    old = pd.DataFrame()
    if INDEX.exists() and not full:
        old = pd.read_parquet(INDEX)
        if "kaynak" not in old:
            old["kaynak"] = "gdelt"
        old = old[old["kaynak"] == "gdelt"]
        built = INDEX.stat().st_mtime
        done = set(old["gkg_day"].astype(str).str[:10])
        todo = [d for d, p in files.items() if d not in done or p.stat().st_mtime > built]
        old = old[~old["gkg_day"].astype(str).str[:10].isin(todo) & old["gkg_day"].astype(str).str[:10].isin(files)]
    else:
        todo = list(files)
    with duckdb.connect() as con:
        parts = [old]
        if todo:
            parts.append(con.execute(
                """SELECT CAST(gkg_day AS DATE) AS gkg_day, SourceCommonName AS site, COUNT(*) AS n,
                          COUNT(*) FILTER (WHERE COALESCE(PageTitle, '') <> '') AS n_title, 'gdelt' AS kaynak
                   FROM read_parquet(?) GROUP BY 1, 2""",
                [[str(files[d]) for d in sorted(todo)]],
            ).df())
        if any(EXTRA.glob("*/*.parquet")):
            parts.append(con.execute(
                f"""SELECT CAST(timezone('UTC', pub_timestamp) AS DATE) AS gkg_day, site, COUNT(*) AS n,
                           COUNT(*) FILTER (WHERE COALESCE(title, '') <> '') AS n_title, 'ek' AS kaynak
                    FROM read_parquet('{EXTRA}/*/*.parquet') GROUP BY 1, 2"""
            ).df())
    out = pd.concat([p for p in parts if len(p)], ignore_index=True).sort_values(["gkg_day", "site"])
    out["gkg_day"] = pd.to_datetime(out["gkg_day"])
    out["n"] = out["n"].astype("int32")
    out["n_title"] = out["n_title"].astype("int32")
    tmp = INDEX.with_suffix(".tmp")
    out.to_parquet(tmp, index=False, compression="zstd")
    tmp.replace(INDEX)
    return len(todo)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--full", action="store_true", help="dizini baştan kur")
    a = p.parse_args()
    if not ARTICLES.is_dir():
        sys.exit(f"Arşiv bulunamadı: {ARTICLES}")
    n = build(a.full)
    print(f"[OK] kaynak dizini: {INDEX} ({n} gün yeniden sayıldı)")


if __name__ == "__main__":
    main()
