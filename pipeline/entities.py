"""Başlıklardan kişi ve kurum adları: sözlük (GDELT başlıklarından) ve eşleştirme (arşiv dışı künyelere).

GDELT'in kişi/kurum alanları Türkçe haberlerde makine çevirisinden geçtiği için bozuktur ve arşiv dışı künyelerde
(adres, başlık, tarih) hiç yoktur. Bu modül iki adımda çalışır:

1. `sozluk`: 2019 ortasından beri GDELT başlıklarında (panelin clean_title'ıyla) büyük harfli ad dizileri aranır.
   - Kişi: pipeline/ad_kurallari.toml'daki bir ön adla başlayan 2–3 kelime.
   - Kurum: bir kurum ekiyle ("Bakanlığı", "Partisi"...) biten 2–5 kelime veya 3–6 harfli kısaltma.
   - Yalnızca en az SOZLUK_MIN_TITLES başlıkta ve SOZLUK_MIN_SITES sitede geçenler sözlüğe girer.
   - Başlığın çoğu büyük harfle başlayan kelimelerden oluşuyorsa ("Her Kelimesi Büyük") bu başlık atlanır.
   Çıktı: data/archive/ad_sozlugu.parquet (ad, anahtar, tur, baslik, site).
2. `isle`: arşiv dışı künyelerin başlığında (yoksa adresindeki metinde) sözlükteki adlar aranır. Adres metni küçük
   harfli ve Türkçe karaktersizdir ("ozgur-ozelden"); eşleştirme bu yüzden aksansız anahtarla ve son kelimede
   Türkçe ekleri tolere ederek yapılır. Çıktı: data/archive/extra_entities.parquet (url, site, pub_timestamp, ad, tur).
   Artımlıdır; `--full` baştan kurar. extra_collect.py her toplamadan sonra `refresh()`'i çağırır: sözlük 30 günden
   eskiyse veya ad_kurallari.toml değiştiyse sözlüğü yeniden kurar ve tüm künyeleri baştan işler.

    venv/bin/python pipeline/entities.py sozluk            # sözlüğü kur (~1,5 dk; ayda bir kendiliğinden)
    venv/bin/python pipeline/entities.py isle [--full]     # arşiv dışı künyelere uygula
    venv/bin/python pipeline/entities.py dene "başlık veya adres"
"""
import argparse
import collections
import re
import sys
import tomllib
from concurrent.futures import ProcessPoolExecutor
from datetime import date
from pathlib import Path

import duckdb
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))
from adlar import SUFFIXES, Matcher  # noqa: E402,F401
from source_index import ARCHIVE  # noqa: E402

RULES = HERE / "ad_kurallari.toml"
GAZETTEER = ARCHIVE / "ad_sozlugu.parquet"
EXTRA_ENTITIES = ARCHIVE / "extra_entities.parquet"
EXTRA = ARCHIVE / "extra"
TITLES_SINCE = date(2019, 7, 1)        # GDELT bu tarihten önce başlık tutmuyor
GAZETTEER_MAX_AGE_DAYS = 30            # refresh(): sözlük bundan eskiyse yeniden kurulur ve tüm künyeler yeniden işlenir
SOZLUK_MIN_TITLES, SOZLUK_MIN_SITES = 5, 3
ACRONYM_MIN_TITLES, ACRONYM_MIN_SITES = 20, 5
ACRONYM_MAX_LOWER_RATIO = 0.2          # aksansız küçük harfli (ekli) kullanımı bu katı aşan kısaltma atılır
TITLECASE_SHARE = 0.6                  # başlık kelimelerinin bu kadarı büyük harfle başlıyorsa başlık atlanır
# Ön ad aynı zamanda sıradan bir kelimeyse (umut, barış, deniz) cümle başında yanlış eşleşir: küçük harfle en az
# AMBIGUOUS_FIRST_LOWER kez geçen ön adlarda, ilk iki kelimenin küçük harfli ikili olarak geçişi ("barış
# görüşmeleri") büyük harfli geçişinin AMBIGUOUS_LAST_RATIO katını aşıyorsa ad atılır
AMBIGUOUS_FIRST_LOWER, AMBIGUOUS_LAST_RATIO = 50, 0.5
# Kişi adının son kelimesi başlıklarda küçük harfle, adın kendisinden bu kattan fazla geçiyorsa sıradan kelimedir
# ("bayramı", "sağlık"). Bedeli: sıradan kelime olan bazı soyadları ("kurt", "demir") seyrek kişilerde kaçar
LAST_LOWER_MAX_RATIO = 3
# SUFFIXES ve Matcher pipeline/adlar.py'dedir (panel de aynı modülü kullanır; kural tek yerde)

W = r"[A-ZÇĞİÖŞÜ][a-zçğıöşüâîû]+"
ACR = r"[A-ZÇĞİÖŞÜ]{2,6}"


def rules() -> dict:
    r = tomllib.loads(RULES.read_text())
    return {
        "first": set(r["on_adlar"]),
        "not_last": set(r["soyad_olmayan"]),
        "org_suffix": set(r["kurum_ekleri"]),
        "acr_stop": set(r["kisaltma_disi"]),
        "acr_two": set(r["kisaltma_iki_harf"]),
        "first_only": set(r["yalniz_ilk_ad"]),
    }


def _helpers():
    from cases.common import app_ns

    ns = app_ns()
    return ns["clean_title"], ns["fold_tr"]


def low(word: str) -> str:
    return word.replace("I", "ı").replace("İ", "i").lower()


def key_of(text: str, fold) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", fold(text)))


def is_titlecase(title: str) -> bool:
    words = re.findall(r"[A-Za-zÇĞİÖŞÜçğıöşüâîû]{3,}", title)
    return len(words) >= 4 and sum(w[0].isupper() for w in words) / len(words) > TITLECASE_SHARE


def is_allcaps(title: str) -> bool:
    words = re.findall(r"[A-Za-zÇĞİÖŞÜçğıöşüâîû]{3,}", title)
    return len(words) >= 3 and sum(w.isupper() for w in words) / len(words) > 0.5


def extract(title: str, R: dict) -> set:
    """Bir başlıktaki aday adlar: {(yüzey biçimi, tür)}. Kesme işaretinden sonraki ekler atılır."""
    t = re.sub(r"['’][a-zçğıöşü]+", "", title)
    found = set()
    for m in re.finditer(rf"(?<![\w])(?:{W}|{ACR})(?:\s+(?:{W}|{ACR})){{0,5}}", t):
        words = m.group(0).split()
        # kişi: ön ad(lar) + soyad
        for i, w in enumerate(words):
            if w in R["first"]:
                j = i + 1
                while j < len(words) and j - i < 2 and words[j] in R["first"] and words[j] not in R["first_only"]:
                    j += 1
                if j < len(words) and j - i < 3 and not words[j].isupper() and words[j] not in R["not_last"] \
                        and words[j] not in R["org_suffix"]:
                    found.add((" ".join(words[i:j + 1]), "kişi"))
                break
        # kurum: kurum ekiyle biten dizi (en fazla 4 kelime öncesiyle)
        for j, w in enumerate(words):
            if w in R["org_suffix"] and j > 0:
                found.add((" ".join(words[max(0, j - 4):j + 1]), "kurum"))
    if not is_allcaps(title):
        for a in re.findall(rf"(?<![\w]){ACR}(?![\w])", t):
            if (len(a) >= 3 or a in R["acr_two"]) and a not in R["acr_stop"]:
                found.add((a, "kısaltma"))
    return found


def _scan_year(year: int) -> tuple:
    """Bir yılın başlıklarından aday sayıları: (Counter[(ad, tür)] başlık, dict site kümesi, küçük harf sayıları)."""
    clean, _ = _helpers()
    R = rules()
    start = max(date(year, 1, 1), TITLES_SINCE)
    files = [str(p) for p in sorted((ARCHIVE / "articles").glob(f"{year}-*.parquet"))
             if p.stem >= start.isoformat()]
    if not files:
        return collections.Counter(), {}, collections.Counter()
    df = duckdb.sql(f"SELECT PageTitle t, SourceCommonName s FROM read_parquet({files}) WHERE PageTitle <> ''").df()
    cnt, sites, lower = collections.Counter(), collections.defaultdict(set), collections.Counter()
    low_first = {low(f) for f in R["first"]}
    for raw, site in df.itertuples(index=False):
        title = clean(raw)
        words = re.findall(r"(?<![\w'’])[a-zçğıöşü]{3,}\b", title)
        lower.update(words)
        # küçük harfli ikililer ("barış görüşmeleri"): yalnızca ilk kelimesi bir ön ad olanlar
        lower.update(f"{a} {b}" for a, b in re.findall(r"(?<![\w'’])([a-zçğıöşü]+)\s+(?=([a-zçğıöşü]+))", title)
                     if a in low_first)
        if is_titlecase(title):
            continue
        for item in extract(title, R):
            cnt[item] += 1
            sites[item].add(site)
    return cnt, {k: v for k, v in sites.items() if cnt[k] >= 2}, lower


def build_gazetteer() -> pd.DataFrame:
    _, fold = _helpers()
    R = rules()
    years = range(TITLES_SINCE.year, date.today().year + 1)
    cnt, sites, lower = collections.Counter(), collections.defaultdict(set), collections.Counter()
    with ProcessPoolExecutor(min(6, len(years))) as ex:
        for year, (c, s, lw) in zip(years, ex.map(_scan_year, years)):
            cnt.update(c)
            lower.update(lw)
            for k, v in s.items():
                sites[k] |= v
            print(f"  {year}: {sum(c.values()):,} aday")
    # Aksansız küçük harfli kelime sayıları: adres metninde kısaltma sıradan kelimeyle çakışıyor mu ("bes" = "beş")
    folded_lower = collections.Counter()
    for w, c in lower.items():
        if " " not in w:
            folded_lower[fold(w)] += c

    def collisions(acr: str) -> int:
        a = fold(acr)
        return folded_lower[a] + sum(folded_lower[a + suf] for suf in SUFFIXES)

    rows = []
    for (name, kind), n in cnt.items():
        n_sites = len(sites.get((name, kind), ()))
        min_t, min_s = (ACRONYM_MIN_TITLES, ACRONYM_MIN_SITES) if kind == "kısaltma" else \
            (SOZLUK_MIN_TITLES, SOZLUK_MIN_SITES)
        if n < min_t or n_sites < min_s:
            continue
        # büyük harfle yazılmış sıradan kelime ("KAZA", "İZLE") veya aksansız biçimi sıradan bir kelimeyle çakışan
        # kısaltma ("BES" / "beş", "AŞTİ" / "astılar") adreste ayırt edilemez
        if kind == "kısaltma" and collisions(name) >= ACRONYM_MAX_LOWER_RATIO * n:
            continue
        if kind == "kişi":
            words = name.split()
            if lower[low(words[0])] >= AMBIGUOUS_FIRST_LOWER and \
                    lower[f"{low(words[0])} {low(words[1])}"] > AMBIGUOUS_LAST_RATIO * n:
                continue
            # son kelime sıradan bir kelimeyse ("Zafer Bayramı", "Barış Ödülü") ad değildir
            if lower[low(words[-1])] > LAST_LOWER_MAX_RATIO * n:
                continue
        rows.append({"ad": name, "anahtar": key_of(name, fold), "tur": "kurum" if kind == "kısaltma" else kind,
                     "baslik": n, "site": n_sites})
    gz = pd.DataFrame(rows)
    # Aynı anahtarın birden fazla yüzey biçimi varsa en sık olanı kalır ("Ak Parti" / "AK Parti")
    gz = gz.sort_values("baslik", ascending=False).drop_duplicates("anahtar")
    # Kurum dizileri bazen başında fazladan kelime taşır ("Yeni Ticaret Bakanlığı"); daha kısa bir kurum adıyla
    # biten ve ondan çok daha seyrek olan diziler atılır
    org = gz[gz["tur"] == "kurum"].set_index("anahtar")["baslik"]
    def redundant(r):
        if r["tur"] != "kurum":
            return False
        parts = r["anahtar"].split()
        return any(" ".join(parts[i:]) in org.index and org[" ".join(parts[i:])] >= 3 * r["baslik"]
                   for i in range(1, len(parts)))
    gz = gz[~gz.apply(redundant, axis=1)]
    return gz.sort_values(["tur", "baslik"], ascending=[True, False]).reset_index(drop=True)


def extra_text(df: pd.DataFrame, fold) -> pd.Series:
    """Künyenin eşleştirme metni: başlık varsa başlık, yoksa adresin yol kısmı (aksansız)."""
    from cases.common import app

    slug = df["url"].map(app("url_slug"))
    return df["title"].fillna("").where(df["title"].fillna("") != "", slug).map(fold)


def apply_extra(full: bool = False) -> int:
    if not GAZETTEER.exists():
        print("[ATLANDI] ad sözlüğü yok; önce: venv/bin/python pipeline/entities.py sozluk")
        return 0
    if not EXTRA.is_dir():
        return 0
    _, fold = _helpers()
    matcher = Matcher(pd.read_parquet(GAZETTEER))
    old = pd.read_parquet(EXTRA_ENTITIES) if EXTRA_ENTITIES.exists() and not full else None
    q = f"SELECT site, url, title, pub_timestamp FROM read_parquet('{EXTRA}/*/*.parquet')"
    if old is not None:
        done = old[["url"]].drop_duplicates()
        df = duckdb.sql(f"{q} WHERE url NOT IN (SELECT url FROM done)").df()
    else:
        df = duckdb.sql(q).df()
    # İşlenen her künye kaydedilir; ad bulunamayanlar ad = NULL ile (artımlı çalışmada yeniden taranmasın)
    found = extra_text(df, fold).map(matcher.find)
    rows = df[["url", "site", "pub_timestamp"]].assign(ad=found).explode("ad")
    rows["tur"] = rows["ad"].map(lambda x: x[1] if isinstance(x, tuple) else None)
    rows["ad"] = rows["ad"].map(lambda x: x[0] if isinstance(x, tuple) else None)
    out = rows if old is None else pd.concat([old, rows], ignore_index=True)
    out.to_parquet(EXTRA_ENTITIES, index=False)
    n = int(rows["ad"].notna().sum())
    print(f"[OK] adlar: {len(df):,} künye işlendi, {n:,} ad bulundu → {EXTRA_ENTITIES.name}")
    return n


def yukle_bq() -> int:
    """Ad sözlüğünü BigQuery'ye yazar (gdelt_tr.ad_sozlugu), yayındaki panel okuyabilsin diye.
    Sorgu çalıştırmaz; yükleme işi ücretsizdir, tablo ~0,4 MB."""
    import os

    from google.cloud import bigquery

    if not GAZETTEER.exists():
        raise SystemExit("Ad sözlüğü yok; önce: venv/bin/python pipeline/entities.py sozluk")
    proje = os.environ.get("GDELT_BQ_PROJECT", "banded-scion-476220-r0")
    ref = f"{proje}.gdelt_tr.ad_sozlugu"
    gz = pd.read_parquet(GAZETTEER)
    S = bigquery.SchemaField
    sema = [S("ad", "STRING", description="Türkçe yazımıyla ad"),
            S("anahtar", "STRING", description="Aksansız, küçük harfli eşleştirme anahtarı"),
            S("tur", "STRING", description="kişi | kurum"),
            S("baslik", "INT64", description="Sözlük kurulurken kaç başlıkta görüldü"),
            S("site", "INT64", description="Kaç farklı sitede görüldü")]
    client = bigquery.Client(project=proje)
    tablo = bigquery.Table(ref, schema=sema)
    tablo.description = ("Türkçe başlıklardan kurulmuş kişi ve kurum adı sözlüğü (pipeline/entities.py). "
                         "GDELT'in makine çevirisinden gelen bozuk adlarının yerine kullanılır.")
    client.create_table(tablo, exists_ok=True)
    cfg = bigquery.LoadJobConfig(schema=sema, write_disposition="WRITE_TRUNCATE")
    client.load_table_from_dataframe(gz[[f.name for f in sema]], ref, job_config=cfg).result()
    print(f"[OK] {len(gz):,} ad → {ref}")
    return len(gz)


def refresh() -> None:
    """Toplamadan sonra çağrılır: sözlük yoksa veya eskidiyse (ya da kurallar değiştiyse) yeniden kurar ve tüm
    künyeleri baştan işler; yoksa yalnızca yeni künyeleri işler."""
    import time

    stale = not GAZETTEER.exists() or time.time() - GAZETTEER.stat().st_mtime > GAZETTEER_MAX_AGE_DAYS * 86400 \
        or RULES.stat().st_mtime > GAZETTEER.stat().st_mtime
    if stale:
        gz = build_gazetteer()
        gz.to_parquet(GAZETTEER, index=False)
        print(f"[OK] ad sözlüğü yenilendi: {len(gz):,} ad")
    apply_extra(full=stale)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("komut", choices=["sozluk", "isle", "dene", "yukle"])
    p.add_argument("metin", nargs="?")
    p.add_argument("--full", action="store_true", help="isle: baştan kur")
    a = p.parse_args()
    if a.komut == "sozluk":
        gz = build_gazetteer()
        gz.to_parquet(GAZETTEER, index=False)
        print(f"[OK] ad sözlüğü: {len(gz):,} ad ({gz['tur'].value_counts().to_dict()}) → {GAZETTEER.name}")
    elif a.komut == "isle":
        apply_extra(a.full)
    elif a.komut == "yukle":
        yukle_bq()
    else:
        _, fold = _helpers()
        print(Matcher(pd.read_parquet(GAZETTEER)).find(fold(a.metin or "")))


if __name__ == "__main__":
    main()
