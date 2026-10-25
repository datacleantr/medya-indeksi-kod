"""Haber metinleri: vakanın kilit haberlerinin metnini ortak depoya (data/store/vaka.duckdb, article_texts) çeker
ve kilit cümlelerden bir özet üretir.

    venv/bin/python -m cases.texts <vaka> sec       # metni çekilecek haberleri seç → hedef_urls.csv
    venv/bin/python -m cases.texts <vaka> cek       # depoda olmayanları çek (önce canlı sayfa, sonra Wayback)
    venv/bin/python -m cases.texts <vaka> ozet      # [ozet].kalip ile cümle seç → metin_ozeti.md
    venv/bin/python -m cases.texts ice-aktar <klasör>   # eski vaka klasörlerindeki metinler/*.json'u depoya al

Depo vakalar arasında ortaktır: bir haberin metni bir kez çekilir, sonraki vakalar yeniden kullanır.
Metinler yalnızca iç araştırma içindir (telif); depodan dışarıya tam metin verilmez.
"""
import glob
import warnings
import json
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pandas as pd

from cases.common import app, load_case, store

# yapılandırmadaki kalıplarda gruplar olabilir; pandas bunun için uyarı verir
warnings.filterwarnings("ignore", "This pattern is interpreted as a regular expression")

UA = {"User-Agent": "Mozilla/5.0 (research; news archive case study)"}
TOPIC_PICKS = 6        # her ek konu için en erken bu kadar farklı site
EARLY_ALL_MAX = 200    # ilk dönemin haber sayısı bunu aşmazsa hepsi seçilir
MIN_TEXT_CHARS = 300


def select(cfg: dict) -> pd.DataFrame:
    out = cfg["_dir"]
    df = pd.read_parquet(out / "korpus.parquet")
    fold_tr = app("fold_tr")
    parts = []
    if (out / "kumeler.csv").exists():
        heads = pd.read_csv(out / "kumeler.csv")["url"]
        parts.append(df[df["DocumentIdentifier"].isin(heads)].assign(neden="küme başı"))
    if "donem" in df and cfg.get("donemler"):
        early = df[df["donem"] == cfg["donemler"][0][0]]
        if len(early) <= EARLY_ALL_MAX:
            parts.append(early.assign(neden=cfg["donemler"][0][0]))
    text = (df["baslik"] + " " + df["DocumentIdentifier"]).map(fold_tr)
    for name, pat in cfg.get("metin_konulari", {}).items():
        hit = df[text.str.contains(pat, regex=True)].sort_values("pub_timestamp").drop_duplicates("SourceCommonName")
        parts.append(hit.head(TOPIC_PICKS).assign(neden=name))
    t = pd.concat(parts).drop_duplicates("DocumentIdentifier")
    t = t[["neden", "pub_timestamp", "SourceCommonName", "baslik", "DocumentIdentifier"]]
    t.to_csv(out / "hedef_urls.csv", index=False)
    print(f"[OK] {len(t)} haber seçildi → {out / 'hedef_urls.csv'}")
    print(t["neden"].value_counts().to_string())
    return t


def _extract(html: str, url: str) -> tuple:
    import trafilatura

    text = trafilatura.extract(html, url=url, include_comments=False, favor_recall=True) or ""
    meta = trafilatura.extract_metadata(html)
    return text, (meta.title if meta else "") or ""


def _get(url: str, timeout: int = 20) -> tuple:
    import requests

    r = requests.get(url, headers=UA, timeout=timeout, allow_redirects=True)
    r.raise_for_status()
    if not r.encoding or r.encoding.lower() == "iso-8859-1":
        r.encoding = r.apparent_encoding
    return r.text, r.url


def _relevant(text: str, pat: re.Pattern) -> bool:
    return len(text) >= MIN_TEXT_CHARS and bool(pat.search(app("fold_tr")(text)))


def _live(url: str, pat: re.Pattern):
    try:
        html, final = _get(url)
        text, title = _extract(html, final)
        return ("canlı", final, title, text) if _relevant(text, pat) else None
    except Exception:
        return None


def _wayback(url: str, ts, pat: re.Pattern):
    import requests

    try:
        stamp = pd.Timestamp(ts).strftime("%Y%m%d%H%M%S")
        a = requests.get("https://archive.org/wayback/available", params={"url": url, "timestamp": stamp},
                         headers=UA, timeout=30).json()
        snap = a.get("archived_snapshots", {}).get("closest")
        if not snap or str(snap.get("status")) != "200":
            return None
        raw = re.sub(r"/web/(\d+)/", r"/web/\1id_/", snap["url"])
        html, _ = _get(raw, timeout=40)
        text, title = _extract(html, url)
        return (f"wayback {snap['timestamp']}", raw, title, text) if _relevant(text, pat) else None
    except Exception:
        return None


def fetch(cfg: dict) -> None:
    targets = pd.read_csv(cfg["_dir"] / "hedef_urls.csv", parse_dates=["pub_timestamp"])
    con = store()
    have = set(con.sql("SELECT url FROM article_texts").df()["url"])
    todo = targets[~targets["DocumentIdentifier"].isin(have)]
    print(f"seçilen {len(targets)}, depoda {len(targets) - len(todo)}, çekilecek {len(todo)}")
    # metnin vakayla ilgili olduğunu doğrulamak için: korpusa giriş kalıbı
    pat = re.compile(cfg["dahil"])
    rows = list(todo.itertuples())
    with ThreadPoolExecutor(8) as ex:
        results = list(ex.map(lambda r: _live(r.DocumentIdentifier, pat), rows))
    stats = {"canlı": 0, "wayback": 0, "yok": 0}
    for r, res in zip(rows, results):
        if res is None:
            res = _wayback(r.DocumentIdentifier, r.pub_timestamp, pat)
            time.sleep(1.5)  # Wayback'e nazik davran
        origin = res[0] if res else "yok"
        stats[origin.split()[0]] += 1
        con.execute("INSERT OR REPLACE INTO article_texts VALUES (?, ?, ?, current_timestamp, ?, ?, ?, ?)",
                    [r.DocumentIdentifier, r.SourceCommonName, r.pub_timestamp, origin,
                     res[1] if res else "", res[2] if res else "", res[3] if res else ""])
    print(stats)


def summary(cfg: dict) -> None:
    """Hedef haberlerin metinlerinden [ozet].kalip'e uyan cümleler (tekrarlar elenir) → metin_ozeti.md."""
    pat = re.compile(cfg.get("ozet", {}).get("kalip", cfg["dahil"]), re.I)
    targets = pd.read_csv(cfg["_dir"] / "hedef_urls.csv")
    con = store()
    texts = con.execute("SELECT * FROM article_texts WHERE url IN (SELECT UNNEST(?)) AND text <> '' ORDER BY published",
                        [targets["DocumentIdentifier"].tolist()]).df()
    seen, out = set(), [f"# Metin özeti · {cfg['ad']}", "",
                        f"{len(texts)} metin; kalıp: `{pat.pattern}`. Cümleler kaynağından aynen alınmıştır.", ""]
    for r in texts.itertuples():
        hits = []
        for s in re.split(r"(?<=[.!?\"”])\s+|\n+", r.text):
            s = s.strip()
            if pat.search(s) and 40 < len(s) < 600 and s[:80] not in seen:
                seen.add(s[:80])
                hits.append(s)
        if hits:
            out.append(f"### {r.published:%d.%m.%Y %H:%M} · {r.site} · {(r.title or '')[:120]}\n{r.url}\n"
                       + "\n".join("- " + h for h in hits[:5]) + "\n")
    (cfg["_dir"] / "metin_ozeti.md").write_text("\n".join(out))
    print(f"[OK] {cfg['_dir'] / 'metin_ozeti.md'}")


def import_legacy(folder: str) -> None:
    """Eski vaka klasörlerindeki metinler/*.json kayıtlarını depoya alır (depoda olanlar atlanır)."""
    con = store()
    have = set(con.sql("SELECT url FROM article_texts").df()["url"])
    n = 0
    for f in glob.glob(str(Path(folder) / "metinler" / "*.json")):
        rec = json.load(open(f))
        if rec["url"] in have:
            continue
        con.execute("INSERT INTO article_texts VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    [rec["url"], rec.get("site", ""), pd.Timestamp(rec["ts_tr"]),
                     pd.Timestamp(Path(f).stat().st_mtime, unit="s"), rec.get("kaynak", ""),
                     rec.get("cekilen", ""), rec.get("baslik", ""), rec.get("metin", "")])
        have.add(rec["url"])
        n += 1
    print(f"[OK] {n} metin depoya alındı ({folder})")


def main():
    if len(sys.argv) == 3 and sys.argv[1] == "ice-aktar":
        return import_legacy(sys.argv[2])
    if len(sys.argv) != 3 or sys.argv[2] not in {"sec", "cek", "ozet"}:
        raise SystemExit(__doc__)
    cfg = load_case(sys.argv[1])
    {"sec": select, "cek": fetch, "ozet": summary}[sys.argv[2]](cfg)


if __name__ == "__main__":
    main()
