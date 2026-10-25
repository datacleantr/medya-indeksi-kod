"""Vaka korpusu: yerel arşivden aday haberleri çeker, ayıklar ve veri kapsamı raporunu yazar.

    venv/bin/python -m cases.corpus <vaka>

Çıktılar (dosyalar/<vaka>/): korpus.parquet, korpus.csv, kapsam.md.
Korpus iki kaynaktan gelir (`kaynak` kolonu): "gdelt" (arşiv) ve "ek" (arşiv dışı toplama: site haritaları ve RSS;
yalnızca adres, başlık, tarih). Ek kayıtlar `ek_baslangic`'tan (varsayılan 2000-01-01) itibaren alınır; arşiv
başlangıcından önceki ek kayıtlar "arşiv öncesi" dönemine düşer.
kapsam.md panelin "Veri kapsamı ve boşluklar" denetimiyle aynı hesaptır (app.coverage_report); rapor aracı
bunu her PDF'e ek olarak koyar, böylece eksik veri hiçbir raporda sessiz kalmaz.
"""
import sys

import duckdb
import pandas as pd

from cases.common import ARCHIVE, app, load_case


def build(cfg: dict) -> pd.DataFrame:
    clean_title, fold_tr = app("clean_title"), app("fold_tr")
    start, end = pd.Timestamp(cfg["baslangic"]), pd.Timestamp(cfg["bitis"])
    with duckdb.connect() as con:
        df = con.execute(
            f"""SELECT GKGRECORDID, pub_timestamp, gkg_day, SourceCommonName, DocumentIdentifier, PageTitle,
                       Themes, V2Persons, Organizations, V2Locations, AvgTone
                FROM read_parquet('{ARCHIVE}/articles/*.parquet')
                WHERE gkg_day BETWEEN ? AND ?
                  AND regexp_matches(coalesce(DocumentIdentifier,'') || ' ' || coalesce(PageTitle,'') || ' ' ||
                                     coalesce(V2Persons,'') || ' ' || coalesce(AllNames,''), ?)""",
            [start.date(), end.date(), cfg["on_suzgec"]],
        ).df()
    df["DocumentIdentifier"] = df["DocumentIdentifier"].str.replace(app("URL_ATTR_JUNK"), "", regex=True)
    df = df.sort_values("pub_timestamp").drop_duplicates("DocumentIdentifier")
    df["PageTitle"] = df["PageTitle"].fillna("").map(clean_title)
    df["pub_timestamp"] = df["pub_timestamp"].dt.tz_convert(app("LOCAL_TZ")).dt.tz_localize(None)
    slug = df["DocumentIdentifier"].str.extract(r"/([^/]+?)(?:[-_]\d+)?(?:\.html?)?/?$")[0].fillna("")
    df["baslik"] = df["PageTitle"].where(df["PageTitle"] != "", slug.str.replace(r"[-_]", " ", regex=True))
    blob = (df["PageTitle"] + " " + df["DocumentIdentifier"].str.replace(r"[-_/.]", " ", regex=True) + " "
            + df["V2Persons"].fillna("")).map(fold_tr)
    keep = blob.str.contains(cfg["dahil"], regex=True)
    if cfg.get("haric"):
        keep &= ~blob.str.contains(cfg["haric"], regex=True)
    df = df[keep].copy()
    df["kaynak"] = "gdelt"
    df = pd.concat([df, extra_rows(cfg, blob_filter=True)], ignore_index=True).sort_values("pub_timestamp")
    if cfg.get("donemler"):
        first = pd.Timestamp(cfg["donemler"][0][1])
        archive_start = pd.Timestamp(app("ARCHIVE_START"))
        edges, labels = [pd.Timestamp.min], []
        if first > archive_start:  # arşiv var ama vaka dönemleri daha geç başlıyor
            edges.append(archive_start)
            labels.append("arşiv öncesi")
            labels.append("vaka dönemi öncesi")
        else:
            labels.append("arşiv öncesi")
        edges += [pd.Timestamp(s) for _, s in cfg["donemler"]] + [pd.Timestamp.max]
        labels += [lbl for lbl, _ in cfg["donemler"]]
        df["donem"] = pd.cut(df["pub_timestamp"], edges, labels=labels, right=False).astype(str)
    return df.reset_index(drop=True)


def extra_rows(cfg: dict, blob_filter: bool = True) -> pd.DataFrame:
    """Arşiv dışı toplamadan (data/archive/extra) vakayla eşleşen künyeler, korpus kolonlarıyla."""
    extra = ARCHIVE / "extra"
    if not any(extra.glob("*/*.parquet")):
        return pd.DataFrame()
    fold_tr = app("fold_tr")
    start = pd.Timestamp(cfg.get("ek_baslangic", "2000-01-01"), tz="UTC")
    end = pd.Timestamp(cfg["bitis"], tz="UTC") + pd.Timedelta(days=1)
    with duckdb.connect() as con:
        ex = con.execute(
            f"""SELECT site, url, title, pub_timestamp, precision FROM read_parquet('{extra}/*/*.parquet')
                WHERE pub_timestamp >= ? AND pub_timestamp < ?
                  AND regexp_matches(url || ' ' || COALESCE(title, ''), ?)""",
            [start, end, cfg["on_suzgec"]]).df()
    if ex.empty:
        return pd.DataFrame()
    ex["pub_timestamp"] = pd.to_datetime(ex["pub_timestamp"], utc=True).dt.tz_convert(app("LOCAL_TZ")).dt.tz_localize(None)
    slug = ex["url"].map(app("url_slug"))
    ex["baslik"] = ex["title"].fillna("").where(ex["title"].fillna("") != "", slug)
    blob = (ex["title"].fillna("") + " " + ex["url"].str.replace(r"[-_/.]", " ", regex=True)).map(fold_tr)
    keep = blob.str.contains(cfg["dahil"], regex=True)
    if cfg.get("haric"):
        keep &= ~blob.str.contains(cfg["haric"], regex=True)
    ex = ex[keep]
    return pd.DataFrame({"pub_timestamp": ex["pub_timestamp"], "gkg_day": ex["pub_timestamp"].dt.normalize(),
                         "SourceCommonName": ex["site"], "DocumentIdentifier": ex["url"],
                         "PageTitle": ex["title"].fillna(""), "baslik": ex["baslik"], "AvgTone": float("nan"),
                         "tarih_hassasiyeti": ex["precision"], "kaynak": "ek"})


def coverage_md(cfg: dict, df: pd.DataFrame) -> str:
    """Korpusun veri kapsamı raporu (Markdown)."""
    start, end = pd.Timestamp(cfg["baslangic"]), pd.Timestamp(cfg["bitis"]) + pd.Timedelta(days=1)
    gd = df[df["kaynak"] == "gdelt"]
    ek = df[(df["kaynak"] == "ek") & (df["pub_timestamp"] >= start) & (df["pub_timestamp"] < end)]
    ek = ek.rename(columns={"SourceCommonName": "site"})
    cov = app("coverage_report")(gd, start.date(), pd.Timestamp(cfg["bitis"]).date(), ek)
    before = df[(df["kaynak"] == "ek") & (df["pub_timestamp"] < pd.Timestamp(app("ARCHIVE_START")))]
    lines = [f"### Veri kapsamı ve boşluklar: {cov['level']}", ""]
    lines += [f"- **{sev.capitalize()}:** {text}" for sev, text in cov["warnings"]] or ["- Belirgin bir boşluk yok."]
    if len(before):
        sites = ", ".join(f"{s} {n}" for s, n in before["SourceCommonName"].value_counts().items())
        lines.append(f"- **Bilgi:** Arşiv başlangıcından önceki dönem için arşiv dışı toplamada {len(before)} haber "
                     f"var ({sites}); korpusta \"arşiv öncesi\" dönemindedir (yalnızca başlık ve tarih).")
    lines += ["", "| Grup | Yayın | Arşivdeki haber | Vakadaki haber | Ek toplamada vakadaki | Durum |",
              "|---|---|---|---|---|---|"]
    for _, r in cov["outlets"].iterrows():
        n_arch = "—" if pd.isna(r["Arşivdeki haber"]) else f"{int(r['Arşivdeki haber']):,}".replace(",", ".")
        lines.append(f"| {r['Grup']} | {r['Yayın']} | {n_arch} | {r['Konudaki haber']} | "
                     f"{r['Ek toplamada konudaki'] or '—'} | {r['Durum']} |")
    lines += ["", "*\"Arşivde yok\": kapsam boşluğu, o yayının haberleri ölçülemez. \"Arşivde var, konuda haber "
              "yok\": içerik bilgisi. \"Yalnızca ek toplamada\": yayın GDELT'te yok, künyeleri site haritası/RSS ile "
              "toplandı. Kişi alanı Türkçede makine çevirisinden gelir ve bozuk olabilir.*"]
    return "\n".join(lines) + "\n"


def main():
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    cfg = load_case(sys.argv[1])
    df = build(cfg)
    out = cfg["_dir"]
    df.to_parquet(out / "korpus.parquet", index=False)
    df.drop(columns=["Themes", "V2Locations"], errors="ignore").to_csv(out / "korpus.csv", index=False)
    (out / "kapsam.md").write_text(coverage_md(cfg, df))
    print(f"[OK] {len(df)} haber ({(df['kaynak'] == 'ek').sum()} arşiv dışı), {df['SourceCommonName'].nunique()} site "
          f"→ {out / 'korpus.parquet'}")
    if "donem" in df:
        print(df["donem"].value_counts().reindex([d for d, _ in cfg["donemler"]]).to_string())
    print((out / "kapsam.md").read_text().split("\n\n| Grup")[0])


if __name__ == "__main__":
    main()
