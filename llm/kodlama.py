"""Altın set kodlama ekranı (yalnızca yerel; yayındaki panele girmez).

    venv/bin/streamlit run llm/kodlama.py --server.port 8503

Başlıklar dosyalar/<vaka>/altin_set.csv'den (llm/ornek.py) tek tek gösterilir; kod kitabı llm/kodkitabi/<vaka>.toml.
Etiketler her kayıtta dosyalar/<vaka>/altin_etiketler_<kodlayıcı>.csv'ye yazılır; kaldığınız yerden devam
edilir. Birden fazla kodlayıcı ayrı dosyalara yazar (kodlayıcılar arası uyum sonra ölçülür).

Körleme: yayın, site ve tam tarih gösterilmez; yalnızca başlık ve dönem. Seçenekler boş başlar (önerilen
bir cevap yoktur). "İlgi" alanında 6 Şubat dışı bir seçim yapılırsa diğer alanlar "Uygulanamaz" olur.
"""
import re
import tomllib
from datetime import datetime
from pathlib import Path

import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parent.parent
CASES = ROOT / "dosyalar"
BOOKS = Path(__file__).resolve().parent / "kodkitabi"
NOT_APPLICABLE = "uygulanamaz"
RELEVANT = {"6subat", "risk"}  # bu ilgi seçimlerinde diğer alanlar kodlanır

st.set_page_config(page_title="Altın set kodlama", page_icon=":material/label:", layout="centered")


def cases_with_sets() -> list:
    return sorted(p.parent.name for p in CASES.glob("*/altin_set.csv") if (BOOKS / f"{p.parent.name}.toml").exists())


@st.cache_data
def load_book(case: str) -> dict:
    return tomllib.loads((BOOKS / f"{case}.toml").read_text())


@st.cache_data
def load_set(case: str) -> pd.DataFrame:
    return pd.read_csv(CASES / case / "altin_set.csv")


def labels_path(case: str, coder: str) -> Path:
    slug = re.sub(r"[^a-z0-9]+", "_", coder.lower()).strip("_") or "kodlayici"
    return CASES / case / f"altin_etiketler_{slug}.csv"


def load_labels(path: Path) -> pd.DataFrame:
    return pd.read_csv(path, dtype=str).fillna("") if path.exists() else pd.DataFrame(columns=["kimlik"])


def save_label(path: Path, row: dict) -> None:
    df = load_labels(path)
    df = pd.concat([df[df["kimlik"] != row["kimlik"]], pd.DataFrame([row])], ignore_index=True)
    df.sort_values("kimlik").to_csv(path, index=False, encoding="utf-8-sig")


cases = cases_with_sets()
if not cases:
    st.error("Kodlanacak altın set yok. Önce: venv/bin/python -m llm.ornek <vaka> altin")
    st.stop()

with st.sidebar:
    st.header("Kodlama")
    case = st.selectbox("Vaka", cases)
    coder = st.text_input("Kodlayıcı adı", key="coder", help="Etiketler bu adla ayrı bir dosyaya yazılır.")
book, items = load_book(case), load_set(case)
fields = book["alan"]
if not coder.strip():
    st.info("Başlamak için kenar çubuğuna kodlayıcı adınızı yazın.")
    st.stop()

path = labels_path(case, coder)
done = load_labels(path)
done_ids = set(done["kimlik"])
with st.sidebar:
    st.progress(len(done_ids) / len(items), text=f"{len(done_ids)} / {len(items)} kodlandı")
    if st.button("İlk kodlanmamışa git", icon=":material/skip_next:", width="stretch"):
        todo = [i for i, k in enumerate(items["kimlik"]) if k not in done_ids]
        st.session_state["pos"] = todo[0] if todo else len(items) - 1
    st.caption(f"Kod kitabı: {book['surum']} · Etiketler: `{path.relative_to(ROOT)}`")
    with st.expander("Kod kitabı", icon=":material/menu_book:"):
        for f in fields:
            st.markdown(f"**{f['soru']}**  \n{f['aciklama']}")
            for _, label, desc in f["secenekler"]:
                st.markdown(f"- *{label}:* {desc}")

if "pos" not in st.session_state:
    todo = [i for i, k in enumerate(items["kimlik"]) if k not in done_ids]
    st.session_state["pos"] = todo[0] if todo else 0
pos = min(max(st.session_state["pos"], 0), len(items) - 1)
item = items.iloc[pos]
prev = done[done["kimlik"] == item["kimlik"]].iloc[0].to_dict() if item["kimlik"] in done_ids else {}

st.caption(f"{book['konu']} · {item['kimlik']} · {pos + 1} / {len(items)}"
           + (" · :green[kodlandı]" if prev else ""))
st.markdown(f"**Dönem:** {item['donem']}")
st.subheader(item["metin"])

values = {}
for f in fields:
    opts = f["secenekler"]
    codes = [c for c, _, _ in opts]
    labels = {c: lbl for c, lbl, _ in opts}
    helps = "\n".join(f"- **{lbl}:** {d}" for _, lbl, d in opts)
    if f["ad"] != "ilgi" and values.get("ilgi") and values["ilgi"] not in RELEVANT:
        values[f["ad"]] = NOT_APPLICABLE
        st.caption(f"{f['soru']} → Uygulanamaz")
        continue
    shown = [c for c in codes if c != NOT_APPLICABLE]
    default = prev.get(f["ad"])
    values[f["ad"]] = st.radio(
        f["soru"], shown, index=shown.index(default) if default in shown else None,
        format_func=lambda c, labels=labels: labels[c], help=helps, key=f"{item['kimlik']}_{f['ad']}",
    )
unsure = st.checkbox("Karar verilemedi (bu başlık belirsiz)", value=prev.get("karar_verilemedi") == "1",
                     key=f"{item['kimlik']}_unsure")
note = st.text_input("Not (isteğe bağlı)", value=prev.get("not", ""), key=f"{item['kimlik']}_note")

missing = [f["soru"] for f in fields if not values.get(f["ad"])]
c1, c2, c3 = st.columns([2, 1, 1])
if c1.button("Kaydet ve sonraki", type="primary", icon=":material/check:", width="stretch",
             disabled=bool(missing) and not unsure):
    save_label(path, {"kimlik": item["kimlik"], "kodlayici": coder.strip(), "surum": book["surum"],
                      **{f["ad"]: values.get(f["ad"]) or "" for f in fields},
                      "karar_verilemedi": "1" if unsure else "", "not": note,
                      "zaman": datetime.now().isoformat(timespec="seconds")})
    st.session_state["pos"] = pos + 1 if pos + 1 < len(items) else pos
    st.rerun()
if c2.button("Önceki", icon=":material/arrow_back:", width="stretch", disabled=pos == 0):
    st.session_state["pos"] = pos - 1
    st.rerun()
if c3.button("Atla", icon=":material/arrow_forward:", width="stretch", disabled=pos + 1 >= len(items)):
    st.session_state["pos"] = pos + 1
    st.rerun()
if missing and not unsure:
    st.caption("Kaydetmek için tüm soruları yanıtlayın ya da 'Karar verilemedi'yi işaretleyin.")
if len(done_ids) == len(items):
    st.success("Altın setin tamamı kodlandı. Teşekkürler!")
