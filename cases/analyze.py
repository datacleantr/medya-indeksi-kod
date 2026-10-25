"""Vaka analizi: başlık kümeleri (iddia kaydı), dönem ve yayın grubuna göre çerçeveler, başlıklardan ad ağı.

    venv/bin/python -m cases.analyze <vaka>

Önce cases.corpus çalıştırılmalıdır. Çıktılar (dosyalar/<vaka>/): kumeler.csv, kume_uyeleri.csv,
cerceve_donem.csv, cerceve_grup.csv, adlar.csv, ad_agi.csv, analiz.md.

Yayın grupları panelin kapsam denetimindeki KEY_OUTLETS'ten gelir; oradaki yayınlar dışındaki siteler "Diğer"dir.
Kişi ağı başlıklardaki özel ad dizilerinden kurulur: GDELT'in kişi alanı Türkçe haberlerde makine çevirisinden
geldiği için bozuktur. Ad listesi gürültü içerir; rapora aktarmadan önce elle gözden geçirin.
"""
import collections
import itertools
import re
import sys

import pandas as pd

from cases.common import app, load_case

CLUSTER_MIN_JACCARD = 0.5     # iki başlık aynı iddia sayılır
CLUSTER_WINDOW_DAYS = 10      # bir küme en son üyesinden bu kadar gün sonra kapanır
CLUSTER_MIN_SITES = 3         # rapora giren küme en az bu kadar sitede görülmeli
NAME_MIN_TITLES = 4
NAME_WORD = r"[A-ZÇĞİÖŞÜÂ][a-zçğıöşüâ]+"
# Başlıklarda ad sanılan kalıplar (site adları, haber kalıpları)
NAME_NOISE = re.compile(r"^(Son |Eski |Yeni |Flaş|Güncel |Haber|Gündem|Yerel |Sıcak |Kritik |Köşe |Dönemin |İşte )|"
                        r"(Haber|Haberleri|Gazetesi|Portalı|Kıbrıs)$")


def stems(text: str, stop: set) -> frozenset:
    return frozenset(w[:5] for w in re.findall(r"[a-z]{3,}", app("fold_tr")(text)) if w not in stop)


def clusters(df: pd.DataFrame, stop: set) -> tuple:
    df = df.sort_values("pub_timestamp").copy()
    df["st"] = df["baslik"].map(lambda t: stems(t, stop))
    reps, cid = [], []  # reps: (kökler, küme no, son zaman)
    for st, ts in zip(df["st"], df["pub_timestamp"]):
        best, bj = None, 0.0
        if len(st) >= 2:
            for i in range(len(reps) - 1, max(-1, len(reps) - 400), -1):
                s2, c, last = reps[i]
                if (ts - last).days > CLUSTER_WINDOW_DAYS:
                    continue
                j = len(st & s2) / len(st | s2)
                if j > bj:
                    best, bj = c, j
        if best is None or bj < CLUSTER_MIN_JACCARD:
            best = len(reps)
            reps.append((st, best, ts))
        cid.append(best)
        s, c, _ = reps[best]
        reps[best] = (s, c, ts)
    df["kume"] = cid
    g = df.groupby("kume").agg(ilk=("pub_timestamp", "min"), son=("pub_timestamp", "max"),
                               haber=("DocumentIdentifier", "size"), site=("SourceCommonName", "nunique"),
                               ilk_site=("SourceCommonName", "first"), baslik=("baslik", "first"),
                               url=("DocumentIdentifier", "first"))
    return g[g["site"] >= CLUSTER_MIN_SITES].sort_values("ilk"), df[["kume", "pub_timestamp", "SourceCommonName",
                                                                        "baslik", "DocumentIdentifier"]]


def outlet_group(site: str) -> str:
    for group, outlets in app("KEY_OUTLETS").items():
        if any(site in domains for domains in outlets.values()):
            return group
    return "Diğer"


def names(df: pd.DataFrame, subject: frozenset) -> tuple:
    def extract(t):
        t = re.sub(r"['’][a-zçğıöşü]+", "", t)
        found = set()
        for m in re.finditer(rf"{NAME_WORD}(?:\s+{NAME_WORD}){{1,2}}", t):
            n = m.group(0)
            if not NAME_NOISE.search(n) and not (set(app("fold_tr")(n).split()) & subject):
                found.add(n)
        return found
    titled = df[df["PageTitle"] != ""]
    sets = titled["PageTitle"].map(extract)
    cnt = collections.Counter(n for s in sets for n in s)
    top = {n for n, c in cnt.items() if c >= NAME_MIN_TITLES}
    pairs = collections.Counter()
    for s in sets:
        pairs.update(itertools.combinations(sorted(s & top), 2))
    first = (titled.assign(ad=sets).explode("ad").dropna(subset=["ad"]).sort_values("pub_timestamp")
             .groupby("ad").agg(adet=("DocumentIdentifier", "size"), ilk=("pub_timestamp", "min"),
                                ilk_site=("SourceCommonName", "first"), ilk_baslik=("PageTitle", "first")))
    first = first[first["adet"] >= NAME_MIN_TITLES].sort_values("adet", ascending=False)
    net = pd.DataFrame([(a, b, c) for (a, b), c in pairs.most_common()], columns=["ad1", "ad2", "baslik"])
    return first, net


def pct_table(df: pd.DataFrame, by: str, cols: list) -> pd.DataFrame:
    t = df.groupby(by, sort=False).agg(haber=("DocumentIdentifier", "size"), **{c: (c, "mean") for c in cols})
    t[cols] = (t[cols] * 100).round(1)
    return t


def to_md(t: pd.DataFrame) -> str:
    t = t.reset_index()
    head = "| " + " | ".join(map(str, t.columns)) + " |\n|" + "---|" * len(t.columns) + "\n"
    return head + "\n".join("| " + " | ".join(str(v) for v in r) + " |" for r in t.itertuples(index=False)) + "\n"


def main():
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    cfg = load_case(sys.argv[1])
    out = cfg["_dir"]
    df = pd.read_parquet(out / "korpus.parquet")
    fold_tr = app("fold_tr")
    text = (df["baslik"] + " " + df["DocumentIdentifier"].str.replace(r"[-_/.]", " ", regex=True)).map(fold_tr)
    frames = cfg.get("cerceveler", {})
    for name, pat in frames.items():
        df[name] = text.str.contains(pat, regex=True)
    df["grup"] = df["SourceCommonName"].map(outlet_group)
    # konunun kendi adı kümelemede ve ad listesinde sayılmaz
    subject = frozenset(re.findall(r"[a-z]{3,}", fold_tr(cfg["dahil"])))
    stop = set(app("FOLDED_STOPWORDS")) | subject | {w + s for w in subject for s in ("nin", "nun", "in", "un", "a", "e")}

    g, members = clusters(df, stop)
    g.to_csv(out / "kumeler.csv")
    members.to_csv(out / "kume_uyeleri.csv", index=False)
    parts = [f"# Analiz özeti · {cfg['ad']}", "",
             f"{len(df)} haber, {df['SourceCommonName'].nunique()} site; en az {CLUSTER_MIN_SITES} sitede görülen "
             f"{len(g)} başlık kümesi.", ""]
    if frames:
        cols = list(frames)
        if "donem" in df:
            by_period = pct_table(df, "donem", cols)
            by_period.to_csv(out / "cerceve_donem.csv")
            parts += ["## Dönemlere göre çerçeve payları (%)", "", to_md(by_period)]
        # Ton yalnızca GDELT kayıtlarında vardır; ek toplamadan gelen künyelerde yoktur. Bazı yayın
        # gruplarında (ör. arşivde olmayan yayınların çok olduğu gruplarda) ton, grubun küçük ve
        # temsili olmayan bir alt kümesinden hesaplanır. "ton_kapsami" bunu görünür kılar: değeri
        # %100'ün belirgin altındaki gruplarda ton başka gruplarla KARŞILAŞTIRILAMAZ.
        ton = df.groupby("grup")["AvgTone"].agg(ton="mean", _n="count")
        ton["ton_kapsami"] = (ton["_n"] / df.groupby("grup").size() * 100).round(1)
        by_group = pct_table(df, "grup", cols).join(ton[["ton", "ton_kapsami"]].round(2))
        by_group.to_csv(out / "cerceve_grup.csv")
        parts += ["## Yayın gruplarına göre (%)", "", to_md(by_group.sort_values("haber", ascending=False))]
    first, net = names(df, subject)
    first.to_csv(out / "adlar.csv")
    net.to_csv(out / "ad_agi.csv", index=False)
    parts += ["## Başlıklarda en sık geçen adlar", "",
              to_md(first.head(25)[["adet", "ilk", "ilk_site"]]),
              "## En güçlü birlikte geçmeler", "", to_md(net.head(15).set_index("ad1"))]
    top = g.sort_values("site", ascending=False).head(25)
    parts += ["## En çok siteye yayılan başlık kümeleri", "",
              to_md(top.assign(ilk=top["ilk"].dt.strftime("%d.%m.%Y %H:%M"))[["ilk", "site", "ilk_site", "baslik"]])]
    (out / "analiz.md").write_text("\n".join(parts))
    print(f"[OK] {len(g)} küme, {len(first)} ad → {out / 'analiz.md'}")


if __name__ == "__main__":
    main()
