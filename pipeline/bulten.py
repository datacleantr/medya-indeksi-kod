"""Haftalık Medya Nabzı (B1): bir haftanın özetini PDF ve JSON olarak üretir.

Kaynak yalnızca `data/archive/ozet_gunluk.parquet`tır (N1, `pipeline/ozet.py`). Haber metni veya başlık okumaz;
bu yüzden saniyeler sürer ve hiçbir API veya BigQuery maliyeti yoktur.

    venv/bin/python pipeline/bulten.py                    # arşivdeki son tam haftayı üretir
    venv/bin/python pipeline/bulten.py --hafta 2026-W42   # belirli bir ISO haftası
    venv/bin/python pipeline/bulten.py --liste            # üretilebilecek son haftaları gösterir

Çıktı: `data/bulten/<YYYY-Www>/` altında `medya-nabzi.pdf` ve `medya-nabzi.json`.
JSON vitrin sayfası (T3) içindir; PDF'teki her rakam JSON'da da vardır.

Ne anlatır:
- Haftanın hacmi ve tonu, önceki haftaya göre değişim.
- **Sapmalar:** Bir çerçevenin bu haftaki payı, aynı çerçevenin kendi haftalık geçmişinde hangi yüzdelik
  dilimde? Yüzde değişim yerine yüzdelik dilim kullanılır; küçük tabanlı seriler dev yüzdeler ürettiği için
  (`MIN_HABER` eşiğini geçmeyen çerçeve sapma olarak raporlanmaz).
- **Yayın imzaları:** Bir yayın çizgisi hangi çerçeveyi ortalamanın kaç katı yazdı (`KEY_OUTLETS` grupları).

Sınırlar (bültenin kendi veri notunda da yazar):
- Haberlerin yarıdan fazlası hiçbir çerçeveye uymaz ("Diğer"); paylar sınıflanabilen kısmın içindedir.
- Ölçülen şey olayın kendisi değil, **habere konu olma yoğunluğudur**.
- GDELT'in taradığı sitelerle sınırlıdır; tam sayım değil, geniş bir örneklemdir.
"""
import argparse
import json
import logging
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from cases.common import ARCHIVE, app_ns  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sapma  # noqa: E402

import streamlit  # noqa: E402,F401
logging.getLogger("streamlit.runtime.caching.cache_data_api").setLevel(logging.ERROR)

OZET = ARCHIVE / "ozet_gunluk.parquet"
OUT_DIR = ROOT / "data" / "bulten"

# Sapma hesabı ve eşikleri pipeline/sapma.py'dedir (panel de aynı modülü kullanır; hesap tek yerde).
MIN_HABER, BASELINE_WEEKS = sapma.MIN_HABER, sapma.TABAN_HAFTA
PCT_HIGH, PCT_LOW, SKIP = sapma.PCT_YUKSEK, sapma.PCT_DUSUK, sapma.HARIC
# Yayın imzasında bir çerçevenin grup payı genel payın bu katını aşarsa öne çıkarılır
INDEX_MIN = 1.6
# İmza için grubun o hafta en az bu kadar haberi olmalı
GROUP_MIN_HABER = 200
# ... ve o grup × çerçeve hücresinde en az bu kadar haber olmalı. Payda değil PAY (hücre) eşiğidir: 259 haberlik
# bir grubun 2 haberlik bir çerçevesi "3,6 kat" diye raporlanmamalı: küçük tabanlı oran gürültüdür.
CELL_MIN_HABER = 25

# --------------------------------------------------------------------------------
# VERİ
# --------------------------------------------------------------------------------
def iso_label(hafta) -> str:
    """Pandas dönemini ISO hafta etiketine çevirir: 2026-W42 (dosya adı ve JSON için)."""
    y, w, _ = hafta.start_time.date().isocalendar()
    return f"{y}-W{w:02d}"


def iso_period(label: str):
    """2026-W42 -> pandas dönemi. Geçersiz etiket None döner."""
    import re
    from datetime import date as _date

    m = re.fullmatch(r"(\d{4})-?W(\d{1,2})", label.strip().upper())
    if not m:
        return None
    try:
        return pd.Period(_date.fromisocalendar(int(m.group(1)), int(m.group(2)), 1), freq="W-SUN")
    except ValueError:
        return None


def load() -> pd.DataFrame:
    if not OZET.exists():
        sys.exit(f"Özet katmanı yok: {OZET}\nÖnce: venv/bin/python pipeline/ozet.py kur")
    return sapma.hafta_ekle(pd.read_parquet(OZET))


full_weeks = sapma.tam_haftalar
weekly_frames = sapma.haftalik_cerceveler


def group_frames(df: pd.DataFrame, hafta) -> pd.DataFrame:
    """Yayın grubu × çerçeve payı ve genel paya göre kaç kat olduğu (imza)."""
    y = df[(df["hafta"] == hafta) & (df["yayin"] != "")]
    if y.empty:
        return pd.DataFrame()
    toplam = y[y["boyut_tur"] == "toplam"].groupby("yayin_grubu")["haber"].sum().rename("grup_haber")
    cer = y[y["boyut_tur"] == "cerceve"].groupby(["yayin_grubu", "boyut"])["haber"].sum().reset_index()
    cer = cer.join(toplam, on="yayin_grubu")
    cer["grup_pay"] = cer["haber"] / cer["grup_haber"]
    # Karşılaştırma tabanı: kilit yayınların tamamı (genel arşiv değil; elmayla elma)
    kilit_toplam = int(toplam.sum())
    genel = cer.groupby("boyut")["haber"].sum() / max(kilit_toplam, 1)
    cer["genel_pay"] = cer["boyut"].map(genel)
    cer["kat"] = cer["grup_pay"] / cer["genel_pay"].replace(0, pd.NA)
    return cer


# --------------------------------------------------------------------------------
# HESAP
# --------------------------------------------------------------------------------
def compute(df: pd.DataFrame, hafta) -> dict:
    hafta_df = weekly_frames(df)
    tam = set(full_weeks(df))
    hafta_df = hafta_df[hafta_df["hafta"].isin(tam)]

    if hafta_df[hafta_df["hafta"] == hafta].empty:
        sys.exit(f"{hafta} için veri yok veya hafta tam değil.")
    onceki_h = hafta - 1

    tum = df[(df["yayin"] == "") & (df["boyut_tur"] == "toplam")]
    hafta_toplam = tum[tum["hafta"] == hafta]
    gecen_toplam = tum[tum["hafta"] == onceki_h]["haber"].sum()
    toplam_haber = int(hafta_toplam["haber"].sum())

    satirlar = sapma.hafta_tablosu(hafta_df, hafta)
    sapma_listesi = sapma.sapmalar(satirlar)

    gf = group_frames(df, hafta)
    imza = []
    if len(gf):
        yeterli = gf[(gf["grup_haber"] >= GROUP_MIN_HABER) & (gf["haber"] >= CELL_MIN_HABER)
                     & (~gf["boyut"].isin(SKIP)) & (gf["kat"] >= INDEX_MIN)]
        for grup, sub in yeterli.groupby("yayin_grubu"):
            top = sub.sort_values("kat", ascending=False).iloc[0]
            imza.append({"grup": grup, "cerceve": top["boyut"], "kat": float(top["kat"]),
                         "grup_pay": float(top["grup_pay"]), "genel_pay": float(top["genel_pay"]),
                         "haber": int(top["haber"]), "grup_haber": int(top["grup_haber"])})
        imza.sort(key=lambda x: -x["kat"])

    gunluk = hafta_toplam.sort_values("gun")[["gun", "haber", "ort_ton"]]
    ton = float((hafta_toplam["ort_ton"] * hafta_toplam["haber"]).sum() / max(toplam_haber, 1))
    return {
        "hafta": iso_label(hafta),
        "donem": str(hafta),
        "baslangic": hafta.start_time.date().isoformat(),
        "bitis": hafta.end_time.date().isoformat(),
        "hazirlanma": datetime.now().isoformat(timespec="minutes"),
        "arsiv_son_gun": df["gun"].max().date().isoformat(),
        "cerceve_seti": str(df["cerceve_seti"].iloc[0]),
        "toplam_haber": toplam_haber,
        "gunluk_ortalama": round(toplam_haber / 7, 1),
        "gecen_hafta_haber": int(gecen_toplam) if gecen_toplam else None,
        "degisim_yuzde": round((toplam_haber / gecen_toplam - 1) * 100, 1) if gecen_toplam else None,
        "ortalama_ton": round(ton, 2),
        "kaynak_sayisi": int(hafta_toplam["kaynak_sayisi"].max()),
        "gunluk": [{"gun": g.date().isoformat(), "haber": int(h), "ort_ton": round(float(t), 2)}
                   for g, h, t in gunluk.itertuples(index=False)],
        "cerceveler": satirlar,
        "sapmalar": sapma_listesi,
        "imzalar": imza,
        "esikler": {"min_haber": MIN_HABER, "taban_hafta": BASELINE_WEEKS,
                    "yuzdelik_yuksek": PCT_HIGH, "yuzdelik_dusuk": PCT_LOW,
                    "imza_min_kat": INDEX_MIN, "grup_min_haber": GROUP_MIN_HABER,
                    "hucre_min_haber": CELL_MIN_HABER},
    }


# --------------------------------------------------------------------------------
# PDF
# --------------------------------------------------------------------------------
def build_pdf(f: dict, ns: dict) -> bytes:
    import io
    from xml.sax.saxutils import escape

    from reportlab.graphics.shapes import Drawing, Line, Rect
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    MK, APP_NAME_UPPER = ns["MK"], ns["APP_NAME_UPPER"]
    ns["pdf_fonts"]()
    ink, muted, red, line = (colors.HexColor(MK[k]) for k in ("ink", "muted", "red", "line_strong"))
    surface = colors.HexColor(MK["surface_deep"])

    st_kicker = ParagraphStyle("k", fontName="MkMono", fontSize=8, textColor=red, leading=11, spaceAfter=2)
    st_title = ParagraphStyle("t", fontName="MkSerif", fontSize=26, textColor=ink, leading=30, spaceAfter=4)
    st_sub = ParagraphStyle("s", fontName="MkSans", fontSize=10, textColor=muted, leading=14, spaceAfter=10)
    st_h = ParagraphStyle("h", fontName="MkMono", fontSize=8.5, textColor=muted, leading=12, spaceBefore=12,
                          spaceAfter=5)
    st_body = ParagraphStyle("b", fontName="MkSans", fontSize=9.5, textColor=ink, leading=13.5)
    st_small = ParagraphStyle("sm", fontName="MkSans", fontSize=8, textColor=muted, leading=11)
    st_cell = ParagraphStyle("c", fontName="MkSans", fontSize=8.5, textColor=ink, leading=11)
    st_note = ParagraphStyle("n", parent=st_small, backColor=colors.HexColor("#f4efe6"), borderPadding=5,
                             spaceBefore=6, spaceAfter=4)
    width = A4[0] - 36 * mm

    def h(text):
        upper = text.replace("i", "İ").replace("ı", "I").upper()
        return [Paragraph(escape(upper), st_h),
                Table([[""]], colWidths=[width], rowHeights=[0.6],
                      style=[("LINEABOVE", (0, 0), (-1, -1), 0.6, line)])]

    def pc(v, digits=1):
        return "—" if v is None else f"%{v:.{digits}f}".replace(".", ",")

    bas = datetime.fromisoformat(f["baslangic"])
    bit = datetime.fromisoformat(f["bitis"])
    story = [
        Paragraph(f"{APP_NAME_UPPER} — HAFTALIK MEDYA NABZI", st_kicker),
        Paragraph(escape(f"{bas:%d.%m.%Y} – {bit:%d.%m.%Y}"), st_title),
        Paragraph(escape(f"{f['hafta']} · Türkçe haber arşivi · Hazırlanma: "
                         f"{datetime.fromisoformat(f['hazirlanma']):%d.%m.%Y %H:%M} · "
                         f"Arşivin son günü: {datetime.fromisoformat(f['arsiv_son_gun']):%d.%m.%Y}"), st_sub),
    ]

    # --- Göstergeler ---
    deg = f["degisim_yuzde"]
    kpis = [("HABER", f"{f['toplam_haber']:,}".replace(",", ".")),
            ("GÜNLÜK ORTALAMA", f"{f['gunluk_ortalama']:,.0f}".replace(",", ".")),
            ("GEÇEN HAFTAYA GÖRE", "—" if deg is None else f"{deg:+.1f}%".replace(".", ",")),
            ("ORTALAMA TON", f"{f['ortalama_ton']:.2f}".replace(".", ",")),
            ("SİTE", f"{f['kaynak_sayisi']:,}".replace(",", "."))]
    kpi = Table([[Paragraph(k, st_small) for k, _ in kpis],
                 [Paragraph(escape(v), ParagraphStyle("kv", fontName="MkSerif", fontSize=14, leading=17,
                                                      textColor=ink)) for _, v in kpis]],
                colWidths=[width / 5] * 5)
    kpi.setStyle(TableStyle([("LINEABOVE", (0, 0), (-1, 0), 2, ink), ("BACKGROUND", (0, 0), (-1, -1), surface),
                             ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 6)]))
    story += [kpi, Spacer(1, 4)]

    # --- Günlük hacim çubukları ---
    story += h("Haftanın günleri · haber sayısı")
    d = Drawing(width, 86)
    gunler = f["gunluk"]
    ymax = max(g["haber"] for g in gunler) * 1.2 or 1
    bw = width / len(gunler)
    for i, g in enumerate(gunler):
        hh = g["haber"] / ymax * 58
        d.add(Rect(i * bw + bw * 0.22, 20, bw * 0.56, hh, fillColor=ink, strokeColor=None))
        tar = datetime.fromisoformat(g["gun"])
        d.add(_label(bw * (i + 0.5), 8, f"{tar:%d.%m}", muted, 7))
        d.add(_label(bw * (i + 0.5), 24 + hh, f"{g['haber']:,}".replace(",", "."), ink, 7))
    d.add(Line(0, 20, width, 20, strokeColor=line, strokeWidth=0.6))
    story += [d]

    # --- Sapmalar ---
    story += h("Haftanın sapmaları")
    if f["sapmalar"]:
        story.append(Paragraph(
            f"Bir çerçevenin bu haftaki payının, <b>önceki {f['esikler']['taban_hafta']} haftaya</b> göre "
            "bulunduğu yüzdelik dilim. Taban tüm geçmiş değil kayan bir penceredir; yoksa yıllar içinde yapısal "
            "olarak yükselen bir çerçeve her hafta sapma gibi görünür. Haftalık haber sayısı "
            f"{f['esikler']['min_haber']}'in altındaki çerçeveler listelenmez. "
            "<b>Yön ayrı bilgidir:</b> bir çerçeve geçen haftaya göre düşerken de tabanına göre yüksek olabilir.",
            st_small))
        rows = [[Paragraph(x, st_small) for x in ["Çerçeve", "Pay", "Geçen hafta", "Fark", "Tabana göre", "Ton"]]]
        for s in f["sapmalar"][:8]:
            # Ok gliflerini (▲▼) marka fontları içermiyor, tofu kutusu çıkıyor: sözcükle yazılır
            yon = "olağandışı yüksek" if s["yuzdelik"] >= PCT_HIGH else "olağandışı düşük"
            rows.append([
                Paragraph(escape(s["cerceve"]), st_cell),
                Paragraph(pc(s["pay"] * 100, 2), st_cell),
                Paragraph(pc(s["onceki_pay"] * 100 if s["onceki_pay"] is not None else None, 2), st_cell),
                Paragraph("—" if s["fark_puan"] is None else f"{s['fark_puan']:+.2f} puan".replace(".", ","),
                          st_cell),
                Paragraph(f'<b>{s["yuzdelik"]:.0f}.</b> yüzdelik<br/><font size="7">{yon}</font>', st_cell),
                Paragraph(f"{s['ort_ton']:.1f}".replace(".", ","), st_cell),
            ])
        t = Table(rows, colWidths=[width * x for x in (.25, .12, .14, .16, .20, .13)])
        t.setStyle(TableStyle([("LINEBELOW", (0, 0), (-1, 0), 0.8, ink),
                               ("LINEBELOW", (0, 1), (-1, -2), 0.3, line),
                               ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                               ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3)]))
        story.append(t)
    else:
        story.append(Paragraph("Bu hafta eşiği geçen bir sapma yok: tüm çerçeveler kendi olağan aralıklarında.",
                               st_body))

    # --- Bütün çerçeveler ---
    story += h("Bütün çerçeveler")
    rows = [[Paragraph(x, st_small) for x in ["Çerçeve", "Haber", "Pay", "Geçen haftaya fark", "Tabana göre",
                                              "Ton"]]]
    for s in f["cerceveler"]:
        rows.append([
            Paragraph(escape(s["cerceve"]), st_cell),
            Paragraph(f"{s['haber']:,}".replace(",", "."), st_cell),
            Paragraph(pc(s["pay"] * 100, 2), st_cell),
            Paragraph("—" if s["fark_puan"] is None else f"{s['fark_puan']:+.2f}".replace(".", ","), st_cell),
            Paragraph("—" if s["yuzdelik"] is None else f"{s['yuzdelik']:.0f}.", st_cell),
            Paragraph(f"{s['ort_ton']:.1f}".replace(".", ","), st_cell),
        ])
    t = Table(rows, colWidths=[width * x for x in (.28, .14, .14, .21, .12, .11)])
    t.setStyle(TableStyle([("LINEBELOW", (0, 0), (-1, 0), 0.8, ink),
                           ("LINEBELOW", (0, 1), (-1, -2), 0.3, line),
                           ("TOPPADDING", (0, 0), (-1, -1), 2.5), ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5)]))
    story.append(t)

    # --- Yayın imzaları ---
    story += h("Yayın çizgilerinin imzası")
    if f["imzalar"]:
        story.append(Paragraph(
            "Bir yayın çizgisinin en çok öne çıkardığı çerçeve: o grubun payının, <b>kilit yayınların "
            "tamamındaki</b> paya oranı. 1,0 ortalama demektir. Oranın kaç habere dayandığı ayrıca verilir; "
            f"{f['esikler']['hucre_min_haber']} haberin altındaki hücreler listelenmez, çünkü küçük sayılar "
            "büyük oranlar üretir.", st_small))
        rows = [[Paragraph(x, st_small) for x in ["Yayın çizgisi", "Öne çıkan çerçeve", "Haber", "Grup payı",
                                                  "Genel pay", "Kat"]]]
        for s in f["imzalar"][:8]:
            rows.append([
                Paragraph(escape(s["grup"]), st_cell),
                Paragraph(escape(s["cerceve"]), st_cell),
                Paragraph(f"{s['haber']:,}".replace(",", "."), st_cell),
                Paragraph(pc(s["grup_pay"] * 100, 1), st_cell),
                Paragraph(pc(s["genel_pay"] * 100, 1), st_cell),
                Paragraph(f"<b>{s['kat']:.1f}×</b>".replace(".", ","), st_cell),
            ])
        t = Table(rows, colWidths=[width * x for x in (.26, .24, .11, .14, .13, .12)])
        t.setStyle(TableStyle([("LINEBELOW", (0, 0), (-1, 0), 0.8, ink),
                               ("LINEBELOW", (0, 1), (-1, -2), 0.3, line),
                               ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3)]))
        story.append(t)
    else:
        story.append(Paragraph("Bu hafta eşiği geçen bir yayın imzası yok.", st_body))

    # --- Veri notu ve atıf (her dışa çıkan belgede zorunlu) ---
    story += h("Veri notu")
    story.append(Paragraph(
        "<b>Ne ölçüldü?</b> Bu bülten olayların kendisini değil, <b>Türk medyasında habere konu olma "
        "yoğunluğunu</b> ölçer. Bir çerçevenin payının artması o alanda daha çok şey yaşandığı değil, daha çok "
        "yazıldığı anlamına gelir.", st_small))
    story.append(Paragraph(
        f"<b>Çerçeveler nasıl atanıyor?</b> Haberin başlığındaki anahtar kelimelerle (&#8220;{f['cerceve_seti']}&#8221; "
        "seti). Bir haber birden fazla çerçeveye girebilir, bu yüzden payların toplamı %100'ü aşar. Haberlerin "
        "yarıdan fazlası hiçbir çerçeveye uymaz ve &#8220;Diğer&#8221;e düşer; yukarıdaki paylar bu artık kümeyi de içeren "
        "toplamın içindedir.", st_small))
    story.append(Paragraph(
        "<b>Kapsam.</b> GDELT'in taradığı sitelerle sınırlıdır; tam sayım değil, geniş bir örneklemdir. Her site "
        "ve her haber GDELT'e girmez. 2019 ortasından önceki haberlerde başlık bulunmadığı için çerçeveler "
        "adresten atanır ve daha zayıftır; yüzdelik dilimler o dönemi de içerir.", st_small))
    story.append(Paragraph(
        "Veri kaynağı: The GDELT Project (GKG 2.1) — https://www.gdeltproject.org/ · GDELT verisi açık ve "
        "ücretsizdir; bu bülten GDELT tarafından hazırlanmamıştır.", st_note))

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm,
                            topMargin=16 * mm, bottomMargin=14 * mm,
                            title=f"Haftalık Medya Nabzı {f['hafta']}", author=ns["APP_NAME"])
    doc.build(story)
    return buf.getvalue()


def _label(x, y, text, color, size):
    from reportlab.graphics.shapes import String

    s = String(x, y, text, fillColor=color, fontName="MkMono", fontSize=size, textAnchor="middle")
    return s


# --------------------------------------------------------------------------------
def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--hafta", help="ISO haftası, ör. 2026-W42 (varsayılan: arşivdeki son tam hafta)")
    p.add_argument("--liste", action="store_true", help="üretilebilecek son haftaları göster")
    a = p.parse_args()

    df = load()
    tam = full_weeks(df)
    if a.liste:
        print("Üretilebilecek son haftalar (arşivde 7 günü de olanlar):")
        for w in tam[-10:]:
            print(f"  {iso_label(w)}   {w.start_time:%d.%m.%Y} – {w.end_time:%d.%m.%Y}")
        return
    if a.hafta:
        hafta = iso_period(a.hafta)
        if hafta is None:
            sys.exit(f"Hafta etiketi anlaşılmadı: {a.hafta} (beklenen biçim: 2026-W42)")
        if hafta not in set(tam):
            sys.exit(f"{a.hafta} arşivde tam bir hafta değil. Seçenekler için --liste.")
    else:
        hafta = tam[-1]

    f = compute(df, hafta)
    out = OUT_DIR / iso_label(hafta)
    out.mkdir(parents=True, exist_ok=True)
    (out / "medya-nabzi.json").write_text(json.dumps(f, ensure_ascii=False, indent=2))
    (out / "medya-nabzi.pdf").write_bytes(build_pdf(f, app_ns()))
    print(f"[OK] {f['hafta']} ({f['baslangic']} – {f['bitis']}): {f['toplam_haber']:,} haber, "
          f"{len(f['sapmalar'])} sapma, {len(f['imzalar'])} imza")
    print(f"     {out / 'medya-nabzi.pdf'}")
    print(f"     {out / 'medya-nabzi.json'}")


if __name__ == "__main__":
    main()
