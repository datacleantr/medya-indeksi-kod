"""Vaka dosyası PDF'i: dosyalar/<vaka>/DOSYA_*.md bölümlerini birleştirir ve iki eki her zaman koyar:
"Veri kapsamı ve boşluklar" (kapsam.md, cases.corpus üretir) ve "Açık kaynak kaydı" (ortak depodan).

    venv/bin/python -m cases.report <vaka>            # nötr sürüm: platform ve veri kaynağı adı geçmez
    venv/bin/python -m cases.report <vaka> --marka    # markalı sürüm: ürün adı (APP_NAME) ve GDELT atfı

Kapsam eki olmadan rapor üretilmez: eksik veri hiçbir raporda sessiz kalmamalı. Nötr sürüm yalnızca iç kullanım
içindir; dışarıyla paylaşılacak her sürümde GDELT atfı zorunludur (--marka).
Markdown alt kümesi: #–####, listeler (girintili), numaralı listeler, > alıntı blokları, tablolar, **kalın**, *soluk*,
`kod`, [bağlantı](url), ---.
"""
import html
import re
import sys

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import HRFlowable, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from cases.common import ROOT, app, load_case, store
from cases.sources import to_md as sources_md

FONTS = ROOT / "assets" / "fonts"
MK = app("MK")
INK, RED, MUTED, LINE = (colors.HexColor(MK[k]) for k in ("ink", "red", "muted", "line_strong"))
LINK = MK["cyan"]


def register_fonts() -> None:
    for name, file in [("Plex", "IBMPlexSans-Regular.ttf"), ("PlexB", "IBMPlexSans-SemiBold.ttf"),
                       ("Serif", "SourceSerif4-Regular.ttf"), ("SerifB", "SourceSerif4-SemiBold.ttf"),
                       ("Mono", "IBMPlexMono-Regular.ttf")]:
        pdfmetrics.registerFont(TTFont(name, str(FONTS / file)))
    for fam, regular, bold in [("Plex", "Plex", "PlexB"), ("Serif", "Serif", "SerifB")]:
        pdfmetrics.registerFontFamily(fam, normal=regular, bold=bold, italic=regular, boldItalic=bold)


STYLES = {
    "h1": ParagraphStyle("h1", fontName="SerifB", fontSize=17, leading=21, textColor=INK, spaceBefore=4, spaceAfter=4),
    "h2": ParagraphStyle("h2", fontName="SerifB", fontSize=13.5, leading=17, textColor=RED, spaceBefore=4, spaceAfter=6),
    "h3": ParagraphStyle("h3", fontName="SerifB", fontSize=11.5, leading=14, textColor=INK, spaceBefore=9, spaceAfter=4),
    "p": ParagraphStyle("p", fontName="Plex", fontSize=9, leading=12.6, textColor=INK, spaceAfter=4),
    "li": ParagraphStyle("li", fontName="Plex", fontSize=9, leading=12.4, textColor=INK, leftIndent=12, bulletIndent=3,
                         spaceAfter=2),
    "q": ParagraphStyle("q", fontName="Plex", fontSize=8.4, leading=11.4, textColor=MUTED, leftIndent=8,
                        borderPadding=(3, 5, 3, 6), backColor=colors.HexColor("#f6f1e7"), spaceAfter=5),
    "td": ParagraphStyle("td", fontName="Plex", fontSize=7.1, leading=8.9, textColor=INK, alignment=TA_LEFT),
    "th": ParagraphStyle("th", fontName="PlexB", fontSize=7.1, leading=8.9, textColor=INK),
}


def inline(t: str) -> str:
    t = html.escape(t, quote=False)
    t = re.sub(r"\[([^\]]+)\]\((https?://[^)]+)\)", rf'<link href="\2" color="{LINK}">\1</link>', t)
    t = re.sub(r"(?<![\"'>])(https?://[^\s<)]+)", lambda m: f'<link href="{m.group(1)}" color="{LINK}">{m.group(1)}</link>', t)
    t = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", t)
    t = re.sub(r"(?<!\*)\*(?!\s)(.+?)(?<!\s)\*(?!\*)", rf'<font color="{MK["muted"]}">\1</font>', t)
    return re.sub(r"`([^`]+)`", r'<font name="Mono" size="7.5">\1</font>', t)


def table(rows: list) -> Table:
    rows = [[c.strip() for c in r.strip().strip("|").split("|")] for r in rows]
    rows = [r for r in rows if not all(re.fullmatch(r":?-{2,}:?", c) for c in r)]
    n = max(len(r) for r in rows)
    rows = [r + [""] * (n - len(r)) for r in rows]
    width = A4[0] - 30 * mm
    lens = [max(min(len(re.sub(r"\]\(.*?\)", "]", r[i])), 90) for r in rows) + 4 for i in range(n)]
    ws = [max(width * ln / sum(lens), 14 * mm) for ln in lens]
    ws = [w * width / sum(ws) for w in ws]
    data = [[Paragraph(inline(c), STYLES["th"] if j == 0 else STYLES["td"]) for c in r] for j, r in enumerate(rows)]
    t = Table(data, colWidths=ws, repeatRows=1)
    t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e5ebea")),
                           ("LINEBELOW", (0, 0), (-1, 0), 0.6, INK), ("LINEBELOW", (0, 1), (-1, -1), 0.25, LINE),
                           ("LEFTPADDING", (0, 0), (-1, -1), 3), ("RIGHTPADDING", (0, 0), (-1, -1), 3),
                           ("TOPPADDING", (0, 0), (-1, -1), 2), ("BOTTOMPADDING", (0, 0), (-1, -1), 2)]))
    return t


def md_to_flow(text: str) -> list:
    out, lines, i = [], text.splitlines(), 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("|"):
            block = []
            while i < len(lines) and lines[i].startswith("|"):
                block.append(lines[i])
                i += 1
            out += [table(block), Spacer(1, 5)]
            continue
        if line.startswith(">"):
            block = []
            while i < len(lines) and lines[i].startswith(">"):
                block.append(re.sub(r"^\s*- ", "• ", lines[i].lstrip(">").strip()))
                i += 1
            out.append(Paragraph("<br/>".join(inline(b) for b in block if b), STYLES["q"]))
            continue
        m = re.match(r"^(#{1,4}) (.*)", line)
        indent = len(line) - len(line.lstrip())
        if m:
            out.append(Paragraph(inline(m.group(2)), STYLES["h" + str(min(len(m.group(1)), 3))]))
        elif line.strip() == "---":
            out.append(HRFlowable(width="100%", thickness=0.4, color=LINE, spaceBefore=4, spaceAfter=6))
        elif re.match(r"^\s*[-*] ", line):
            style = ParagraphStyle("lx", parent=STYLES["li"], leftIndent=12 + indent * 5, bulletIndent=3 + indent * 5)
            out.append(Paragraph(inline(re.sub(r"^\s*[-*] ", "", line)), style, bulletText="•"))
        elif re.match(r"^\s*\d+\. ", line):
            num = re.match(r"^\s*(\d+)\.", line).group(1)
            style = ParagraphStyle("nx", parent=STYLES["li"], leftIndent=14 + indent * 5, bulletIndent=2 + indent * 5)
            out.append(Paragraph(inline(re.sub(r"^\s*\d+\. ", "", line)), style, bulletText=num + "."))
        elif line.strip():
            out.append(Paragraph(inline(line), STYLES["p"]))
        i += 1
    return out


def build(cfg: dict, branded: bool) -> str:
    folder = cfg["_dir"]
    sections = sorted(folder.glob("DOSYA_*.md"))
    if not sections:
        raise SystemExit(f"Bölüm yok: {folder}/DOSYA_*.md")
    if not (folder / "kapsam.md").exists():
        raise SystemExit(f"Kapsam eki yok: önce `venv/bin/python -m cases.corpus {cfg['_name']}` çalıştırın.")
    register_fonts()
    con = store()
    (folder / "kaynak_kaydi.md").write_text(sources_md(con, cfg))
    unrated = con.execute("SELECT COUNT(*) FROM case_sources WHERE case_name = ? AND source_grade || info_grade = 'F6'",
                          [cfg["_name"]]).fetchone()[0]
    story = []
    for k, f in enumerate(sections):
        if k:
            story.append(PageBreak())
        story += md_to_flow(f.read_text())
    appendix = ("## Ekler\n\n" + (folder / "kapsam.md").read_text().replace("### ", "### Ek A · ", 1) + "\n"
                + (folder / "kaynak_kaydi.md").read_text().replace("### ", "### Ek B · ", 1))
    if not branded:  # nötr sürümde veri kaynağının adı geçmez (kapsam uyarıları paneldeki metinlerle aynıdır)
        appendix = appendix.replace("GDELT'in", "Kaynak veritabanının").replace("GDELT", "kaynak veritabanı")
    story += [PageBreak()] + md_to_flow(appendix)

    title = f"Vaka Dosyası: {cfg['ad']}"
    upper = cfg["ad"].replace("i", "İ").replace("ı", "I").upper()
    # Nötr sürüm yalnızca iç kullanım içindir ve altbilgide bunu söyler. Markalı sürüm dışarıyla
    # paylaşılmak için üretilir; "iç çalışma" ibaresi orada yanlış olur ve çıkarılır.
    footer = [f"VAKA DOSYASI: {upper} — İÇ ÇALIŞMA / ÖN DEĞERLENDİRME"]
    if branded:
        footer = [f"{app('APP_NAME_UPPER')} — VAKA DOSYASI: {upper}",
                  "Veri kaynağı: The GDELT Project (gdeltproject.org). Kişilerle ilgili ifadeler kaynağıyla birlikte "
                  "iddia olarak verilmiştir."]

    def page(c, d):
        c.saveState()
        c.setFont("Mono", 6.8)
        c.setFillColor(MUTED)
        c.drawString(15 * mm, 10 * mm, footer[0][:120])
        c.drawRightString(A4[0] - 15 * mm, 10 * mm, str(d.page))
        if len(footer) > 1:
            c.drawString(15 * mm, 6.5 * mm, footer[1])
        c.setStrokeColor(RED)
        c.setLineWidth(1.2)
        c.line(15 * mm, A4[1] - 11 * mm, A4[0] - 15 * mm, A4[1] - 11 * mm)
        c.restoreState()

    slug = re.sub(r"[^a-z0-9]+", "_", app("fold_tr")(cfg["_name"])).strip("_")
    out = folder / f"{slug}_vaka_dosyasi{'_marka' if branded else ''}.pdf"
    doc = SimpleDocTemplate(str(out), pagesize=A4, leftMargin=15 * mm, rightMargin=15 * mm, topMargin=15 * mm,
                            bottomMargin=16 * mm, title=title, author=app("APP_NAME") if branded else "",
                            creator="", producer="")
    doc.build(story, onFirstPage=page, onLaterPages=page)
    print(f"[OK] {out}")
    if unrated:
        print(f"[UYARI] {unrated} kaynak derecelendirilmemiş (F6); ek B'de yalnızca sayısı görünür.")
    if not branded:
        print("[NOT] Nötr sürüm iç kullanım içindir; dışarıyla paylaşılacaksa --marka ile GDELT atıflı sürümü üretin.")
    return str(out)


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if len(args) != 1:
        raise SystemExit(__doc__)
    build(load_case(args[0]), "--marka" in sys.argv)


if __name__ == "__main__":
    main()
