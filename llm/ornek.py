"""LLM pilotu için örneklem: vaka korpusundan (cases.corpus) dönem × yayın grubu katmanlı başlık örneklemi.

    venv/bin/python -m llm.ornek <vaka> altin [--n 200]      # insan kodlaması için altın set → altin_set.csv
    venv/bin/python -m llm.ornek <vaka> pilot [--n 5000]     # LLM etiketlemesi için örneklem → pilot_ornek.parquet

- Yalnızca vakanın ilk döneminden itibaren (vaka.toml `donemler`) kayıtlar alınır; "arşiv öncesi" dışarıda kalır.
- Aynı başlığın kopyaları (ajans metni) tek sayılır: aksansız, noktalamasız başlık üzerinden tekilleştirilir.
- Yayın grubu KEY_OUTLETS'ten (cases.analyze.outlet_group); listede olmayan siteler "Diğer".
- Her dönemde gruplara eşit pay verilir, eksik kalan pay en büyük gruptan tamamlanır.
- Vaka `odak` kalıbı tanımlıyorsa (vaka.toml) altın sete ve pilota `ODAK_DISI_PAYI` kadar "odak dışı" başlık
  (kalıbı taşımayan) bilerek katılır; "ilgi" sorusunun ayırt edilip edilmediği böyle ölçülür. Oran
  sınırlanmazsa ilk ölçümde pilotun yarısı odak dışı çıkıyordu. Kalıp yoksa kota uygulanmaz.
- Gruplara eşit pay, grupların karşılaştırılması içindir; genel oranlar raporlanırken korpustaki paylarla
  yeniden ağırlıklandırılır.
- Altın sette kodlayıcı yayını görmez (körleme); CSV'de `site` ve `grup` sonradan karşılaştırma için durur.
- Tohum sabittir (SEED): aynı korpusla aynı örneklem çıkar.
"""
import argparse
import re
import sys

import pandas as pd

from cases.analyze import outlet_group
from cases.common import app, load_case

SEED = 20260929
ODAK_DISI_PAYI = 0.15


def load(cfg: dict) -> pd.DataFrame:
    df = pd.read_parquet(cfg["_dir"] / "korpus.parquet")
    first = pd.Timestamp(cfg["donemler"][0][1])
    df = df[df["pub_timestamp"] >= first].copy()
    fold = app("fold_tr")
    df["site"] = df["SourceCommonName"].fillna(df.get("site", pd.Series(index=df.index, dtype=str)))
    df["metin"] = df["baslik"].fillna("").str.strip()
    df = df[df["metin"].str.len() >= 15]
    df["anahtar"] = df["metin"].map(lambda t: re.sub(r"[^a-z0-9 ]", "", fold(t)).strip()[:90])
    # Odak kalıbı vakaya özgüdür, vaka.toml'dan gelir (`odak`). Olay merkezli konularda olayın imzasıdır
    # ("6 subat", "depremzede", bölge illeri); kalıp yoksa korpusun tamamı odak içi sayılır ve odak dışı
    # kotası 0 olur — sürekli konularda (asgari ücret, göçmen) "odak dışı"nın doğal bir tanımı yoktur.
    kalip = (cfg.get("odak") or "").strip()
    if kalip:
        blob = (df["metin"] + " " + df["DocumentIdentifier"].fillna(df.get("url", ""))).map(fold) \
            .str.replace(r"[-_/.]", " ", regex=True)
        df["odak"] = blob.str.contains(re.compile(kalip))
    else:
        df["odak"] = True
    df["grup"] = df["site"].map(outlet_group)
    df = df.sort_values("pub_timestamp").drop_duplicates("anahtar")  # kopyalardan ilk yayımlanan kalır
    return df


def stratified(df: pd.DataFrame, n: int, seed: int) -> pd.DataFrame:
    """Dönemlere kabaca eşit, dönem içinde yayın gruplarına eşit pay; eksik pay en kalabalık gruptan."""
    if n <= 0 or df.empty:      # odak kalıbı olmayan vakada odak dışı payı 0'dır: boş dilim istenir
        return df.head(0)
    periods = df["donem"].unique().tolist()
    per_period = {p: n // len(periods) + (1 if i < n % len(periods) else 0) for i, p in enumerate(periods)}
    parts = []
    for p, quota in per_period.items():
        sub = df[df["donem"] == p]
        groups = sub["grup"].value_counts()
        share = max(1, quota // len(groups))
        take = [g.sample(min(len(g), share), random_state=seed) for _, g in sub.groupby("grup")]
        got = pd.concat(take)
        if len(got) < quota:
            rest = sub.drop(got.index)
            got = pd.concat([got, rest.sample(min(len(rest), quota - len(got)), random_state=seed)])
        parts.append(got.sample(min(len(got), quota), random_state=seed))
    return pd.concat(parts)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("vaka")
    p.add_argument("tur", choices=["altin", "pilot"])
    p.add_argument("--n", type=int)
    a = p.parse_args()
    cfg = load_case(a.vaka)
    df = load(cfg)
    odak_payi = ODAK_DISI_PAYI if (cfg.get("odak") or "").strip() else 0.0
    if not odak_payi:
        print("[not] vaka.toml'da `odak` kalıbı yok: odak dışı kotası uygulanmıyor, "
              "korpusun tamamı odak içi sayıldı.")
    if a.tur == "altin":
        n = a.n or 200
        off = int(round(n * odak_payi))
        s = pd.concat([stratified(df[df["odak"]], n - off, SEED), stratified(df[~df["odak"]], off, SEED + 1)])
        s = s.sample(frac=1, random_state=SEED).reset_index(drop=True)  # sıra karışık: dönem ve odak sırası belli olmasın
        s.insert(0, "kimlik", [f"A{i + 1:03d}" for i in range(len(s))])
        out = cfg["_dir"] / "altin_set.csv"
        s[["kimlik", "donem", "metin", "pub_timestamp", "site", "grup", "kaynak", "odak", "DocumentIdentifier"]] \
            .to_csv(out, index=False, encoding="utf-8-sig")
    else:
        n = a.n or 5000
        altin = cfg["_dir"] / "altin_set.csv"
        if altin.exists():  # altın setteki başlıklar pilotta da olsun (karşılaştırma için), gerisi yeni örneklem
            gold = pd.read_csv(altin)["metin"]
            df = df[~df["metin"].isin(gold)]
        off = int(round(n * odak_payi))
        s = pd.concat([stratified(df[df["odak"]], n - off, SEED + 2), stratified(df[~df["odak"]], off, SEED + 3)])
        s = s.sample(frac=1, random_state=SEED).reset_index(drop=True)
        s.insert(0, "kimlik", [f"P{i + 1:05d}" for i in range(len(s))])
        out = cfg["_dir"] / "pilot_ornek.parquet"
        s[["kimlik", "donem", "metin", "pub_timestamp", "site", "grup", "kaynak", "odak", "DocumentIdentifier"]] \
            .to_parquet(out, index=False)
    print(f"[OK] {len(s)} başlık → {out}")
    print(s.groupby(["donem", "grup"]).size().unstack(fill_value=0).to_string())
    print(f"odak dışı: {int((~s['odak']).sum())}")


if __name__ == "__main__":
    sys.exit(main())
