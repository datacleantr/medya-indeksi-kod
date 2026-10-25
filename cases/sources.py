"""Açık kaynak bulgu kaydı: arşiv dışından doğrulanan her bilgi, kaynağı ve derecesiyle ortak depoya yazılır
(data/store/vaka.duckdb, case_sources). Rapor aracı bu kaydı PDF'e ek olarak koyar.

    venv/bin/python -m cases.sources <vaka> ekle --url U --yayin Y --tarih T --iddia "..." --kaynak B --bilgi 2
                                                 [--teyit "..."] [--not "..."]
    venv/bin/python -m cases.sources <vaka> ice-aktar kaynaklar.csv   # kolonlar: url,yayin,tarih,iddia,kaynak,bilgi,teyit,not
    venv/bin/python -m cases.sources <vaka> tara      # DOSYA_*.md'lerdeki kayıtsız URL'leri "değerlendirilmedi" (F6) ekler
    venv/bin/python -m cases.sources <vaka> liste
    venv/bin/python -m cases.sources <vaka> md        # kaynak_kaydi.md

Derece: kaynak güvenilirliği A (tam güvenilir) … E (güvenilmez), F (değerlendirilemez); bilginin doğruluğu
1 (başka kaynakla teyitli) … 5 (olası değil), 6 (değerlendirilemez). "tara" ile eklenenler F6'dır: rapora
girmeden önce derecelendirilmelidir.
"""
import argparse
import re
import sys

import pandas as pd

from cases.common import load_case, store

SOURCE_GRADES = "ABCDEF"
INFO_GRADES = "123456"
URL_RE = re.compile(r"https?://[^\s)>\]|]+")


def add(con, case: str, url: str, outlet: str, published: str, claim: str, source_grade: str, info_grade: str,
        verified_by: str = "", note: str = "") -> bool:
    """Kayda ekler; aynı vaka + URL + iddia zaten varsa eklemez."""
    source_grade, info_grade = source_grade.upper(), str(info_grade)
    if source_grade not in SOURCE_GRADES or info_grade not in INFO_GRADES:
        raise SystemExit(f"Geçersiz derece: {source_grade}{info_grade} (A–F ve 1–6)")
    exists = con.execute("SELECT 1 FROM case_sources WHERE case_name = ? AND url = ? AND claim = ?",
                         [case, url, claim]).fetchone()
    if exists:
        return False
    con.execute("INSERT INTO case_sources (id, case_name, url, outlet, published, claim, source_grade, info_grade, "
                "verified_by, note) VALUES (nextval('case_sources_id'), ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [case, url, outlet, published, claim, source_grade, info_grade, verified_by, note])
    return True


def scan(con, cfg: dict) -> int:
    """Vaka metinlerindeki kaynak URL'lerini (Kaynaklar listeleri, tablolar) kayda "değerlendirilmedi" olarak ekler."""
    known = set(con.execute("SELECT url FROM case_sources WHERE case_name = ?", [cfg["_name"]]).df()["url"])
    n = 0
    for f in sorted(cfg["_dir"].glob("DOSYA_*.md")):
        for line in f.read_text().splitlines():
            for url in URL_RE.findall(line):
                url = url.rstrip(".,;:'\"")
                if url in known:
                    continue
                context = re.sub(r"\s+", " ", URL_RE.sub("", line)).strip(" -*|:·")[:300]
                outlet = re.sub(r"^www\.", "", url.split("/")[2])
                add(con, cfg["_name"], url, outlet, "", context or f.stem, "F", "6", note=f"{f.name} içinden tarandı")
                known.add(url)
                n += 1
    return n


def to_md(con, cfg: dict) -> str:
    df = con.execute("SELECT * FROM case_sources WHERE case_name = ? ORDER BY source_grade, info_grade, published",
                     [cfg["_name"]]).df()
    graded = df[df["source_grade"] + df["info_grade"] != "F6"]
    lines = [f"### Açık kaynak kaydı ({len(graded)} derecelendirilmiş, {len(df) - len(graded)} değerlendirilmemiş)", "",
             "Derece: kaynak A (tam güvenilir) … E (güvenilmez), F (değerlendirilemez) · bilgi 1 (teyitli) … "
             "5 (olası değil), 6 (değerlendirilemez).", "",
             "| Derece | Yayın | Tarih | Bilgi / iddia | Teyit |", "|---|---|---|---|---|"]
    for r in graded.itertuples():
        lines.append(f"| {r.source_grade}{r.info_grade} | [{r.outlet}]({r.url}) | {r.published or '—'} | "
                     f"{r.claim.replace('|', '/')} | {(r.verified_by or '—').replace('|', '/')} |")
    if len(df) - len(graded):
        lines += ["", f"*{len(df) - len(graded)} kaynak metin içinde geçiyor ama henüz derecelendirilmedi "
                      f"(`python -m cases.sources {cfg['_name']} liste`).*"]
    return "\n".join(lines) + "\n"


def main():
    if len(sys.argv) < 3:
        raise SystemExit(__doc__)
    cfg = load_case(sys.argv[1])
    cmd = sys.argv[2]
    con = store()
    if cmd == "ekle":
        p = argparse.ArgumentParser()
        for a in ("--url", "--yayin", "--tarih", "--iddia", "--kaynak", "--bilgi"):
            p.add_argument(a, required=True)
        p.add_argument("--teyit", default="")
        p.add_argument("--not", dest="note", default="")
        a = p.parse_args(sys.argv[3:])
        ok = add(con, cfg["_name"], a.url, a.yayin, a.tarih, a.iddia, a.kaynak, a.bilgi, a.teyit, a.note)
        print("[OK] eklendi" if ok else "zaten kayıtlı")
    elif cmd == "ice-aktar":
        df = pd.read_csv(sys.argv[3], dtype=str).fillna("")
        n = sum(add(con, cfg["_name"], r["url"], r["yayin"], r["tarih"], r["iddia"], r["kaynak"], r["bilgi"],
                    r.get("teyit", ""), r.get("not", "")) for r in df.to_dict("records"))
        print(f"[OK] {n} kayıt eklendi ({len(df) - n} zaten vardı)")
    elif cmd == "tara":
        print(f"[OK] {scan(con, cfg)} kaynak eklendi (F6: derecelendirilmeli)")
    elif cmd == "liste":
        df = con.execute("SELECT id, source_grade || info_grade AS derece, outlet, published, claim FROM case_sources "
                         "WHERE case_name = ? ORDER BY id", [cfg["_name"]]).df()
        with pd.option_context("display.max_colwidth", 90, "display.width", 200, "display.max_rows", 500):
            print(df.to_string(index=False))
    elif cmd == "md":
        (cfg["_dir"] / "kaynak_kaydi.md").write_text(to_md(con, cfg))
        print(f"[OK] {cfg['_dir'] / 'kaynak_kaydi.md'}")
    else:
        raise SystemExit(__doc__)


if __name__ == "__main__":
    main()
