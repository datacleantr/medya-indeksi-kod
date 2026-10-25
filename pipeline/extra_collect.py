"""Ek toplama: GDELT'te olmayan yayınların haber künyeleri (adres, başlık, tarih) → data/archive/extra/.

    venv/bin/python pipeline/extra_collect.py guncel                         # son haberler (6 saatte bir, launchd)
    venv/bin/python pipeline/extra_collect.py gecmis --since 2010-01 [--until 2026-09] [--yayin Cumhuriyet]
    venv/bin/python pipeline/extra_collect.py durum

Yayınlar ve uç noktalar pipeline/extra_sources.toml'dadır. Yalnızca künye toplanır, tam metin toplanmaz.
robots.txt'ye uyulur; site başına saniyede en fazla bir istek gider (yayınlar paralel, her yayın kendi içinde sıralı).
Kayıtlar yayın ve ay bazında Parquet'tir: data/archive/extra/<site>/<YYYY-MM>.parquet. Aynı adres tekrar gelirse
daha iyi bilgi korunur: başlıklı kayıt başlıksıza, saat hassasiyetli tarih ay hassasiyetlisine üstün gelir.
Her çalışmadan sonra kaynak dizini (source_daily.parquet) güncellenir; panel kapsam denetiminde bu yayınları
"ek toplamada var" olarak gösterir.
"""
import argparse
import fcntl
import os
import html
import re
import sys
import threading
import time
import tomllib
import urllib.robotparser
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

import pandas as pd
import requests

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from source_index import ARCHIVE  # noqa: E402

EXTRA = ARCHIVE / "extra"
CONFIG = HERE / "extra_sources.toml"
UA = "Mozilla/5.0 (compatible; news-archive-research/0.1)"
HOST_DELAY = 1.0          # aynı siteye iki istek arası en az saniye
TIMEOUT = int(os.environ.get("EXTRA_TIMEOUT", "30"))  # yavaş siteler için EXTRA_TIMEOUT=90
PRECISION_RANK = {"saat": 3, "gün": 2, "ay": 1}
COLUMNS = ["site", "url", "title", "pub_timestamp", "precision", "method", "collected_at"]


class Fetcher:
    """Site başına hız sınırı ve robots.txt denetimi olan HTTP istemcisi."""

    def __init__(self):
        self.session = requests.Session()
        self.session.headers["User-Agent"] = UA
        self.last, self.robots, self.lock = {}, {}, threading.Lock()

    def allowed(self, url: str) -> bool:
        host = url.split("/")[2]
        if host not in self.robots:
            rp = urllib.robotparser.RobotFileParser()
            try:
                r = self.session.get(f"https://{host}/robots.txt", timeout=TIMEOUT)
                rp.parse(r.text.splitlines() if r.ok else [])
            except requests.RequestException:
                rp.parse([])
            self.robots[host] = rp
        return self.robots[host].can_fetch(UA, url)

    def get(self, url: str) -> str | None:
        if not self.allowed(url):
            print(f"  [robots] atlandı: {url}")
            return None
        host = url.split("/")[2]
        with self.lock:
            wait = self.last.get(host, 0) + HOST_DELAY - time.monotonic()
            self.last[host] = time.monotonic() + max(wait, 0)
        if wait > 0:
            time.sleep(wait)
        try:
            r = self.session.get(url, timeout=TIMEOUT)
            if not r.ok:
                print(f"  [{r.status_code}] {url}")
                return None
            r.encoding = r.encoding or "utf-8"
            return r.text
        except requests.RequestException as e:
            print(f"  [hata] {url}: {type(e).__name__}")
            return None


def _text(block: str, tag: str) -> str:
    m = re.search(rf"<{tag}[^>]*>\s*(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?\s*</{tag}>", block, re.S)
    return html.unescape(m.group(1).strip()) if m else ""


def _ts(value: str):
    if not value:
        return None
    try:
        ts = pd.Timestamp(value)
    except (ValueError, TypeError):
        try:
            ts = pd.Timestamp(parsedate_to_datetime(value))
        except (TypeError, ValueError):
            return None
    return ts.tz_localize("Europe/Istanbul") if ts.tzinfo is None else ts


def parse_feed(text: str) -> list:
    """RSS/Atom öğeleri ve site haritası <url> kayıtları → (adres, başlık, zaman, zaman türü)."""
    rows = []
    for item in re.findall(r"<item\b.*?</item>|<entry\b.*?</entry>", text, re.S):
        link = _text(item, "link") or (re.search(r'<link[^>]+href="([^"]+)"', item) or [None, ""])[1]
        when = _text(item, "pubDate") or _text(item, "published") or _text(item, "updated")
        rows.append((link, _text(item, "title"), _ts(when), "yayın"))
    for item in re.findall(r"<url>.*?</url>", text, re.S):
        news_date = _text(item, "news:publication_date")
        rows.append((_text(item, "loc"), _text(item, "news:title"),
                     _ts(news_date or _text(item, "lastmod")), "yayın" if news_date else "lastmod"))
    return [r for r in rows if r[0].startswith("http")]


def sub_sitemaps(text: str) -> list:
    return [html.unescape(u) for u in re.findall(r"<loc>\s*(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?\s*</loc>", text)]


def records(outlet: dict, rows: list, method: str, month: tuple | None = None) -> list:
    """Ham satırları kayda çevirir; tarih hassasiyetini belirler."""
    now = pd.Timestamp.now(tz="UTC")
    url_date = re.compile(outlet["url_tarih"]) if outlet.get("url_tarih") else None
    out = []
    for url, title, ts, kind in rows:
        precision = "saat" if (ts is not None and kind == "yayın") else "gün"
        if url_date and (m := url_date.search(url)):
            ts, precision = pd.Timestamp(f"{m[1]}-{m[2]}-{m[3]}", tz="Europe/Istanbul"), "gün"
        if month is not None:
            y, mo = month
            if ts is None or (ts.year, ts.month) != (y, mo):
                ts, precision = pd.Timestamp(year=y, month=mo, day=1, tz="Europe/Istanbul"), "ay"
        if ts is None:
            continue
        out.append({"site": outlet["site"], "url": url.strip(), "title": title, "pub_timestamp": ts.tz_convert("UTC"),
                    "precision": precision, "method": method, "collected_at": now})
    return out


def _rank(df: pd.DataFrame) -> pd.Series:
    return df["precision"].map(PRECISION_RANK) * 10 + (df["title"].fillna("") != "")


def save(recs: list) -> int:
    """Kayıtları yayın/ay dosyalarına birleştirir; yeni veya iyileşen kayıt sayısını döndürür."""
    if not recs:
        return 0
    df = pd.DataFrame(recs, columns=COLUMNS)
    df["month"] = df["pub_timestamp"].dt.strftime("%Y-%m")
    changed = 0
    for (site, month), part in df.groupby(["site", "month"]):
        path = EXTRA / site / f"{month}.parquet"
        old = pd.read_parquet(path) if path.exists() else pd.DataFrame(columns=COLUMNS)
        old_rank = dict(zip(old["url"], _rank(old))) if len(old) else {}
        both = pd.concat([old, part[COLUMNS]], ignore_index=True) if len(old) else part[COLUMNS].copy()
        both["_rank"] = _rank(both)
        best = both.sort_values(["_rank", "collected_at"], ascending=[False, True]).drop_duplicates("url")
        changed += int(sum(r > old_rank.get(u, -1) for u, r in zip(best["url"], best["_rank"])))
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        best.drop(columns="_rank").sort_values("pub_timestamp").to_parquet(tmp, index=False)
        tmp.replace(path)
    return changed


def collect_current(outlet: dict, f: Fetcher) -> int:
    recs = []
    for url in outlet.get("guncel", []):
        text = f.get(url)
        if text:
            recs += records(outlet, parse_feed(text), "rss" if "<item" in text[:5000] else "haber-haritası")
    return save(recs)


def collect_archive(outlet: dict, f: Fetcher, since: str, until: str) -> int:
    if not outlet.get("arsiv_dizini"):
        return 0
    index = f.get(outlet["arsiv_dizini"])
    if not index:
        return 0
    pat = re.compile(outlet["arsiv_kalip"])
    n = 0
    for sub in sub_sitemaps(index):
        m = pat.search(sub)
        if not m:
            continue
        month = None
        if m.groups():
            month = (int(m[1]), int(m[2]))
            if not (since <= f"{month[0]:04d}-{month[1]:02d}" <= until):
                continue
        text = f.get(sub)
        if not text:
            continue
        recs = records(outlet, parse_feed(text), "arşiv-haritası", month)
        if month is None:  # WordPress türü: aralık dışındaki kayıtlar atılır
            recs = [r for r in recs if since <= r["pub_timestamp"].strftime("%Y-%m") <= until]
        n += save(recs)
    return n


def status() -> None:
    import duckdb

    if not EXTRA.exists():
        print("Ek toplama henüz çalışmadı.")
        return
    print(duckdb.sql(f"""
        SELECT site, COUNT(*) haber, MIN(pub_timestamp)::DATE ilk, MAX(pub_timestamp)::DATE son,
               ROUND(AVG((title <> '')::INT) * 100) AS "başlıklı %",
               ROUND(AVG((precision = 'ay')::INT) * 100) AS "ay hassasiyetli %"
        FROM read_parquet('{EXTRA}/*/*.parquet') GROUP BY 1 ORDER BY 2 DESC""").df().to_string(index=False))


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("komut", choices=["guncel", "gecmis", "durum"])
    p.add_argument("--since", default="2000-01", help="geçmiş için ilk ay (YYYY-MM)")
    p.add_argument("--until", default=datetime.now(timezone.utc).strftime("%Y-%m"), help="son ay (YYYY-MM)")
    p.add_argument("--yayin", help="yalnızca bu yayın (ad)")
    a = p.parse_args()
    if a.komut == "durum":
        return status()
    outlets = tomllib.loads(CONFIG.read_text())["yayin"]
    if a.yayin:
        outlets = [o for o in outlets if o["ad"] == a.yayin]
        if not outlets:
            sys.exit(f"Yayın bulunamadı: {a.yayin}")
    # Aynı anda tek toplama: zamanlanmış iş (launchd) ile paneldeki güncelleme düğmesi aynı dosyalara yazmasın
    EXTRA.mkdir(parents=True, exist_ok=True)
    lock = open(EXTRA / ".lock", "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print("[ATLANDI] başka bir ek toplama sürüyor")
        return
    print(f"[{datetime.now():%Y-%m-%d %H:%M}] ek toplama ({a.komut}) başladı")
    f = Fetcher()
    job = (lambda o: collect_current(o, f)) if a.komut == "guncel" else (lambda o: collect_archive(o, f, a.since, a.until))
    totals = defaultdict(int)
    with ThreadPoolExecutor(min(8, len(outlets))) as ex:
        for o, n in zip(outlets, ex.map(job, outlets)):
            totals[o["ad"]] = n
            print(f"  {o['ad']:<14} {n:>8,} yeni/iyileşen kayıt")
    import source_index

    source_index.build()
    print(f"[OK] ek toplama ({a.komut}): {sum(totals.values()):,} kayıt; kaynak dizini güncellendi")
    # Başlıklardan kişi ve kurum adları (pipeline/entities.py); hata toplamayı bozmasın
    try:
        import entities

        entities.refresh()
    except Exception as e:  # noqa: BLE001
        print(f"[UYARI] ad çıkarma başarısız: {type(e).__name__}: {e}")
    # Yayındaki panel için BigQuery kopyası (pipeline/extra_upload.py; sorgu çalıştırmaz, yalnızca değişen aylar).
    # Kimlik (ADC) süresi dolmuşsa toplama yine tamamlanır, yükleme bir sonraki çalışmada yapılır
    if a.komut == "guncel" and not os.environ.get("EXTRA_NO_UPLOAD"):
        try:
            import extra_upload

            extra_upload.upload()
        except Exception as e:  # noqa: BLE001
            print(f"[UYARI] BigQuery'ye yükleme başarısız: {type(e).__name__}: {str(e)[:200]}")


if __name__ == "__main__":
    main()
