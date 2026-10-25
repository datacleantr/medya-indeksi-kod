"""Vitrin sayfasını (T3) üretir: herkese açık tanıtım sayfası, GitHub Pages için statik HTML.

    venv/bin/python vitrin/olustur.py            # vitrin/site/ altına üretir
    venv/bin/python vitrin/olustur.py --ac       # üretir ve tarayıcıda açar

Çıktı `vitrin/site/` klasörüdür; **ayrı ve herkese açık** bir repoya kopyalanıp GitHub Pages ile yayımlanır
(ana repo gizli kalır). Yayımlama adımları `vitrin/OKUBENI.md`'dedir.

İlkeler:
- **Sahte "canlı" gösterge yok.** Sayfadaki her rakam gerçek veridendir ve hangi döneme ait olduğu yazar.
  Rastgele üretilen ızgara, elle yazılmış yüzde, "LIVE" etiketi yoktur.
- **Sınırlar gizlenmez, öne çıkarılır.** Kapsamın örneklem olduğu, çeviri hataları ve "Diğer" payı sayfada
  açıkça yazar. Ayırt edici özelliğimiz bu.
- **Yarım hukuki metin yayımlanmaz.** `yasal/` altındaki metinlerde yer tutucu kaldığı sürece bağlantılar
  üretilmez (panelde de aynı kural).
- **E-posta toplanmaz.** KVKK aydınlatma metni yayımlanana kadar bekleme listesi formu etkin değildir.
"""
import argparse
import glob
import html
import json
import os
import re
import shutil
import sys
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SITE = Path(__file__).resolve().parent / "site"
YASAL = ROOT / "yasal"
YASAL_TASLAK = "[["

MK = {"ink": "#172126", "muted": "#616e74", "line": "#d7dfe0", "line_strong": "#bdc9ca",
      "paper": "#eef2f1", "surface": "#f9fbfa", "surface_deep": "#e5ebea",
      "red": "#a9473c", "cyan": "#267684", "amber_text": "#94611d", "green": "#16715e"}

AYLAR = ["Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran", "Temmuz", "Ağustos", "Eylül", "Ekim",
         "Kasım", "Aralık"]
YASAL_SAYFALAR = [("Kullanım koşulları", "kullanim-kosullari"),
                  ("Gizlilik politikası", "gizlilik-politikasi"),
                  ("KVKK aydınlatma metni", "kvkk-aydinlatma")]


# --------------------------------------------------------------------------------
# VERİ (hepsi gerçek; hiçbiri elle yazılmaz)
# --------------------------------------------------------------------------------
def rakamlar() -> dict:
    """Arşivin gerçek kapsamı. Veri yoksa betik durur — uydurma sayı yazmaktansa üretmemek yeğdir."""
    import duckdb
    import pandas as pd

    gunler = sorted(os.path.basename(f)[:-8] for f in glob.glob(str(ROOT / "data/archive/articles/*.parquet")))
    tot = ROOT / "data/archive/daily_totals/daily_totals.parquet"
    if not gunler or not tot.exists():
        sys.exit("Yerel arşiv yok; vitrin gerçek rakamlar olmadan üretilmez.")
    dt = pd.read_parquet(tot)
    ek = duckdb.sql(f"SELECT count(*) c, count(DISTINCT site) s "
                    f"FROM read_parquet('{ROOT}/data/archive/extra/*/*.parquet')").df()
    kaynak = (ROOT / "app.py").read_text()
    import ast
    ko = ast.literal_eval(re.search(r"^KEY_OUTLETS = (\{.*?^\})", kaynak, re.S | re.M).group(1))
    return {
        "ilk_gun": gunler[0], "son_gun": gunler[-1], "gun_sayisi": len(gunler),
        "haber": int(dt["articles"].sum()),
        "ek_kunye": int(ek["c"][0]), "ek_yayin": int(ek["s"][0]),
        "kilit_yayin": sum(len(v) for v in ko.values()), "kilit_cizgi": len(ko),
    }


def son_bulten() -> tuple:
    """(bülten verisi, pdf yolu). Bülten üretilmemişse (None, None)."""
    js = sorted(glob.glob(str(ROOT / "data/bulten/*/medya-nabzi.json")))
    if not js:
        return None, None
    veri = json.loads(Path(js[-1]).read_text())
    pdf = Path(js[-1]).parent / "medya-nabzi.pdf"
    return veri, (pdf if pdf.exists() else None)


def metodoloji_degerleri(r: dict) -> dict:
    """Metodoloji metnindeki {{token}}'ları app.py ve pipeline sabitlerinden doldurur.
    Sabitler koddan okunur; sayfa ile panel ayrı ayrı güncellenmez."""
    kaynak = (ROOT / "app.py").read_text()

    def sabit(ad: str, varsayilan: str = "") -> str:
        m = re.search(rf"^{ad}\s*=\s*([^\n#]+)", kaynak, re.M)
        return m.group(1).strip() if m else varsayilan

    sapma_src = (ROOT / "pipeline" / "sapma.py").read_text()

    def sapma_sabit(ad: str) -> str:
        m = re.search(rf"^{ad}\s*=\s*([^\n#]+)", sapma_src, re.M)
        return m.group(1).split(",")[0].strip() if m else ""

    bosluklar = []
    m = re.findall(r"date\((\d+), (\d+), (\d+)\)", sabit("GDELT_GAPS"))
    for i in range(0, len(m) - 1, 2):
        bas = date(*map(int, m[i]))
        bit = date(*map(int, m[i + 1]))
        bosluklar.append(f"{bas:%d.%m.%Y}" + (f" – {bit:%d.%m.%Y}" if bit != bas else ""))

    ts = re.search(r"TITLES_SINCE = date\((\d+), (\d+), (\d+)\)", kaynak)
    qs = re.search(r"QUOTES_SINCE = date\((\d+), (\d+), (\d+)\)", kaynak)
    return {
        "TARIH": f"{date.today():%d.%m.%Y}",
        "ILK_GUN": tr_tarih(r["ilk_gun"]), "SON_GUN": tr_tarih(r["son_gun"]),
        "ILK_YIL": r["ilk_gun"][:4],
        "EK_YAYIN": str(r["ek_yayin"]), "EK_KUNYE": tr_sayi(r["ek_kunye"]),
        "BQ_MAX_ROWS": tr_sayi(sabit("BQ_MAX_ROWS", "50000").replace("_", "")),
        "COPY_T": sabit("COPY_TITLE_THRESHOLD", "0.6").replace(".", ","),
        "COPY_W": sabit("COPY_WINDOW_HOURS", "48"),
        "TITLES_SINCE": (f"{AYLAR[int(ts.group(2)) - 1]} {ts.group(1)}" if ts else "2019 ortası"),
        "QUOTES_SINCE": f"{date(*map(int, qs.groups())):%d.%m.%Y}" if qs else "son 12 ay",
        "GAPS": " ve ".join(bosluklar) if bosluklar else "yok",
        "TABAN_HAFTA": sapma_sabit("TABAN_HAFTA"),
        "MIN_HABER": sapma_sabit("MIN_HABER"),
    }


def vaka_raporu() -> tuple:
    """(kaynak yol, yayın adı) — vitrine konacak vaka raporu PDF'i. Yoksa (None, None).
    Markalı sürüm kullanılır: nötr sürüm yalnızca iç kullanım içindir ve GDELT atfı taşımaz."""
    yol = ROOT / "dosyalar" / "deprem" / "deprem_vaka_dosyasi_marka.pdf"
    return (yol, "vaka-dosyasi-6-subat-2023-depremleri.pdf") if yol.exists() else (None, None)


def yasal_hazir() -> bool:
    """Üç metin de doldurulmuşsa True. Biri taslaksa hiçbiri yayımlanmaz."""
    for _, ad in YASAL_SAYFALAR:
        yol = YASAL / f"{ad}.md"
        if not yol.exists() or YASAL_TASLAK in yol.read_text():
            return False
    return True


# --------------------------------------------------------------------------------
# MARKDOWN -> HTML (küçük ve bağımlılıksız; yalnızca yasal metinlerin kullandığı öğeler)
# --------------------------------------------------------------------------------
def md2html(md: str) -> str:
    out, tablo, liste = [], [], False

    def tabloyu_bitir():
        nonlocal tablo
        if not tablo:
            return
        satirlar = [[h.strip() for h in s.strip().strip("|").split("|")] for s in tablo]
        govde = [s for s in satirlar[1:] if not set("".join(s)) <= set("-: ")]
        out.append("<table><thead><tr>" + "".join(f"<th>{satir_ici(c)}</th>" for c in satirlar[0])
                   + "</tr></thead><tbody>"
                   + "".join("<tr>" + "".join(f"<td>{satir_ici(c)}</td>" for c in s) + "</tr>" for s in govde)
                   + "</tbody></table>")
        tablo = []

    def liste_bitir():
        nonlocal liste
        if liste:
            out.append("</ul>")
            liste = False

    for ham in md.split("\n"):
        s = ham.rstrip()
        if s.startswith("|"):
            tablo.append(s)
            continue
        tabloyu_bitir()
        if not s.strip():
            liste_bitir()
            continue
        if s.startswith("#"):
            liste_bitir()
            d = len(s) - len(s.lstrip("#"))
            out.append(f"<h{min(d, 4)}>{satir_ici(s.lstrip('#').strip())}</h{min(d, 4)}>")
        elif s.lstrip().startswith(("- ", "* ")):
            if not liste:
                out.append("<ul>")
                liste = True
            out.append(f"<li>{satir_ici(s.lstrip()[2:])}</li>")
        elif s.startswith(">"):
            liste_bitir()
            out.append(f'<blockquote>{satir_ici(s.lstrip("> "))}</blockquote>')
        elif s.startswith("---"):
            liste_bitir()
            out.append("<hr>")
        else:
            liste_bitir()
            out.append(f"<p>{satir_ici(s)}</p>")
    tabloyu_bitir()
    liste_bitir()
    return "\n".join(out)


def satir_ici(t: str) -> str:
    t = html.escape(t)
    t = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', t)
    t = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", t)
    t = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"<em>\1</em>", t)
    t = re.sub(r"`([^`]+)`", r"<code>\1</code>", t)
    return t


# --------------------------------------------------------------------------------
# SAYFA
# --------------------------------------------------------------------------------
# CSS'in kendi yüzde işaretleri (100%) ile çakışmasın diye renkler @@token@@ ile yazılır
CSS_SABLON = """
*{box-sizing:border-box}
html{-webkit-text-size-adjust:100%}
body{margin:0;background:@@paper@@;color:@@ink@@;
  background-image:linear-gradient(@@line@@55 1px,transparent 1px),linear-gradient(90deg,@@line@@55 1px,transparent 1px);
  background-size:32px 32px;
  font-family:"IBM Plex Sans",-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
  font-size:16px;line-height:1.6}
.wrap{max-width:860px;margin:0 auto;padding:2.5rem 1.2rem 4rem}
a{color:@@cyan@@}
h1,h2,h3{font-family:"Source Serif 4",Georgia,serif;line-height:1.15;margin:0}
h1{font-size:2.6rem;font-weight:600}
h1 span{color:@@red@@}
h2{font-size:1.5rem;margin:2.6rem 0 .3rem}
h3{font-size:1.1rem;margin:1.4rem 0 .3rem}
.kicker{font-family:"IBM Plex Mono",ui-monospace,monospace;text-transform:uppercase;letter-spacing:.14em;
  font-size:.72rem;color:@@red@@;margin-bottom:.3rem}
.tagline{font-size:1.1rem;color:@@muted@@;margin:.5rem 0 0;max-width:46rem}
.rule{border:0;border-top:1px solid @@line_strong@@;margin:.5rem 0 0}
.lead{font-size:1.02rem}
.muted{color:@@muted@@}
.small{font-size:.86rem;color:@@muted@@}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:.6rem;margin:1.2rem 0}
.stat{background:@@surface_deep@@;border-top:2px solid @@ink@@;padding:.7rem .8rem}
/* nowrap: tarih (18.02.2015) iki satıra bölünmesin. En uzun rakamla (60.776.693) aynı karakter
   sayısında olduğu için ek bir küçültme gerekmiyor. */
.stat .n{font-family:"Source Serif 4",Georgia,serif;font-size:1.6rem;line-height:1.1;white-space:nowrap}
.stat .l{font-family:"IBM Plex Mono",ui-monospace,monospace;font-size:.64rem;letter-spacing:.07em;
  text-transform:uppercase;color:@@muted@@;margin-top:.2rem}
.card{background:@@surface@@;border:1px solid @@line_strong@@;padding:1rem 1.1rem;margin:1rem 0}
.note{font-size:.84rem;color:@@muted@@;border-left:3px solid @@amber_text@@;background:@@surface@@;
  padding:.5rem .8rem;margin:1rem 0}
.row{display:flex;justify-content:space-between;gap:1rem;border-bottom:1px dotted @@line_strong@@;
  padding:.4rem 0;font-size:.95rem;flex-wrap:wrap}
.row b{white-space:nowrap}
.hi{color:@@amber_text@@}
.lo{color:@@cyan@@}
.btn{display:inline-block;background:@@ink@@;color:@@paper@@;text-decoration:none;padding:.55rem 1rem;
  font-size:.92rem;margin:.3rem .4rem .3rem 0}
.btn.ghost{background:transparent;color:@@ink@@;border:1px solid @@ink@@}
.off{opacity:.55;border:1px dashed @@line_strong@@;padding:1rem;background:@@surface@@}
table{border-collapse:collapse;width:100%;font-size:.9rem;margin:.8rem 0}
th,td{text-align:left;padding:.35rem .5rem;border-bottom:1px solid @@line@@}
th{font-family:"IBM Plex Mono",ui-monospace,monospace;font-size:.68rem;letter-spacing:.06em;
  text-transform:uppercase;color:@@muted@@;border-bottom:1.5px solid @@ink@@}
code{font-family:"IBM Plex Mono",ui-monospace,monospace;font-size:.86em;background:@@surface_deep@@;padding:.05em .3em}
blockquote{border-left:3px solid @@line_strong@@;margin:.8rem 0;padding:.2rem .9rem;color:@@muted@@}
footer{margin-top:3rem;border-top:1px solid @@line_strong@@;padding-top:1rem;font-size:.84rem;color:@@muted@@}
footer a{margin-right:1rem}
@media(max-width:640px){
  .wrap{padding:1.5rem .9rem 3rem}
  h1{font-size:1.9rem}h2{font-size:1.25rem}
  .grid{grid-template-columns:repeat(2,1fr)}
  table{display:block;overflow-x:auto}
}
"""
CSS = CSS_SABLON
for _k, _v in MK.items():
    CSS = CSS.replace(f"@@{_k}@@", _v)

ISKELET = """<!doctype html>
<html lang="tr"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>%(title)s</title>
<meta name="description" content="%(desc)s">
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400&family=IBM+Plex+Sans:wght@400;600&family=Source+Serif+4:wght@400;600&display=swap" rel="stylesheet">
<link rel="stylesheet" href="%(kok)sstyle.css">
</head><body><div class="wrap">
%(body)s
<footer>
%(footer)s
</footer>
</div></body></html>"""


def tr_sayi(n) -> str:
    return f"{int(n):,}".replace(",", ".")


def tr_tarih(s: str) -> str:
    return datetime.fromisoformat(str(s)).strftime("%d.%m.%Y")


def footer_html(kok: str, yasal_var: bool) -> str:
    bag = f'<a href="{kok}metodoloji.html">Metodoloji</a>'
    if yasal_var:
        bag += " " + " ".join(f'<a href="{kok}{ad}.html">{html.escape(b)}</a>' for b, ad in YASAL_SAYFALAR)
    return (f'<p>Veri kaynağı: <a href="https://www.gdeltproject.org/">The GDELT Project</a> (GKG 2.1). '
            'GDELT verisi açık ve ücretsizdir; bu sayfa GDELT tarafından hazırlanmamıştır.</p>'
            f'<p>{bag}</p>'
            f'<p class="small">Medya İndeksi · sayfa {date.today():%d.%m.%Y} tarihinde üretildi.</p>')


def index_html(r: dict, bulten, pdf_adi, yasal_var: bool, vaka_adi=None) -> str:
    g = []
    g.append('<p class="kicker">Türkçe medya arşivi</p>')
    g.append('<h1>Medya <span>İndeksi</span></h1>')
    g.append('<p class="tagline">2015\'ten bugüne Türkçe haberlerde bir kişinin, kurumun ya da konunun '
             'nasıl ele alındığını ölçen bir araştırma aracı.</p>')

    g.append('<div class="grid">')
    for n, l in [(tr_sayi(r["haber"]), "haber kaydı"),
                 (tr_sayi(r["gun_sayisi"]), "gün kesintisiz"),
                 (tr_tarih(r["ilk_gun"]), "arşivin başlangıcı"),
                 (tr_sayi(r["ek_kunye"]), "arşiv dışı künye")]:
        g.append(f'<div class="stat"><div class="n">{n}</div><div class="l">{l}</div></div>')
    g.append('</div>')
    g.append(f'<p class="small">Kapsam {tr_tarih(r["ilk_gun"])} – {tr_tarih(r["son_gun"])}. '
             f'Bu sayfadaki her rakam arşivden okunmuştur; hiçbiri elle yazılmamıştır.</p>')

    # --- Ne ölçer ---
    g.append('<h2>Ne ölçer — ve ne ölçmez</h2><hr class="rule">')
    g.append('<p class="lead">Medya İndeksi <strong>medyanın kendisini</strong> ölçer: bir konunun hangi '
             'yayınlarda, ne sıklıkta, hangi dille ele alındığını.</p>')
    g.append('<div class="card"><p><strong>Bir konuda haber sayısının artması, o alanda daha çok şey '
             'yaşandığı anlamına gelmez; daha çok yazıldığı anlamına gelir.</strong></p>'
             '<p class="small">Araç hiçbir çıktısında neden-sonuç ilişkisi kurmaz. İki şeyin aynı dönemde '
             'artması, birinin diğerine yol açtığı anlamına gelmez.</p></div>')

    # --- Nasıl çalışır ---
    g.append('<h2>Nasıl çalışır</h2><hr class="rule">')
    for i, (bas, met) in enumerate([
        ("Kaynak", 'Açık veri projesi <a href="https://www.gdeltproject.org/">GDELT</a>, dünya genelinde haber '
                   'sitelerini tarar. Türkçe haberlerin künyeleri (başlık, adres, tarih, yayın) ve GDELT\'in '
                   'türettiği alanlar (duygu tonu, konu kodları, adlar) günlük olarak toplanır.'),
        ("Arşiv dışı tamamlama", f'GDELT\'in taramadığı {r["ek_yayin"]} yayının künyeleri, kendi site haritaları '
                                 've RSS akışlarından ayrıca toplanır. Tam metin toplanmaz; robots.txt\'ye uyulur.'),
        ("Ölçüm", f'{r["kilit_yayin"]} kilit yayın {r["kilit_cizgi"]} yayın çizgisine göre gruplanır. Haberler '
                  'başlıklarındaki anahtar kelimelerle çerçevelere ayrılır; her çerçevenin günlük payı ve tonu '
                  'hesaplanır.'),
        ("Sapma", 'Bir çerçevenin payı, <strong>kendi önceki 52 haftasına</strong> göre değerlendirilir. '
                  'Yüzde değişim değil yüzdelik dilim kullanılır: küçük tabanlı seriler küçük değişimlerde '
                  'dev oranlar üretir.'),
    ], start=1):
        g.append(f'<h3>{i}. {bas}</h3><p>{met}</p>')

    # --- Sınırlar ---
    g.append('<h2>Sınırlar</h2><hr class="rule">')
    g.append('<p class="lead">Bu bölüm sayfanın en önemli kısmıdır. Bir ölçüm aracının neyi ölçemediğini '
             'bilmeden sonuçları okumak yanıltıcıdır.</p><ul>')
    for s in [
        '<strong>Tam sayım değil, örneklemdir.</strong> Kapsam GDELT\'in taradığı sitelerle sınırlıdır; '
        'her haber ve her site veriye girmez.',
        '<strong>Çeviri hataları vardır.</strong> GDELT Türkçe haberleri İngilizceye makine çevirisiyle '
        'çevirerek işler; kişi, kurum ve yer adları bu yüzden bozulabilir. Tek tek haberlere değil, genel '
        'eğilimlere bakılmalıdır.',
        '<strong>Haberlerin tam metni yoktur.</strong> Yalnızca künye ve türetilmiş alanlar tutulur.',
        '<strong>Haberlerin yarıdan fazlası hiçbir çerçeveye uymaz.</strong> Çerçeveler dar bir anahtar '
        'kelime listesidir; sınıflanamayan haberler "Diğer"e düşer.',
        '<strong>2019 ortasından önce başlık yoktur.</strong> O dönemde çerçeveler haber adresinden atanır '
        've daha zayıftır.',
        '<strong>Veri bir gün geriden gelir.</strong> Anlık değildir.',
    ]:
        g.append(f"<li>{s}</li>")
    g.append('</ul>')
    g.append('<div class="note">Araç, her aramada bir <strong>veri kapsamı değerlendirmesi</strong> '
             '(Yeterli / Sınırlı / Yetersiz) gösterir ve o aramaya özgü boşlukları uyarır.</div>')
    g.append('<p><a class="btn ghost" href="metodoloji.html">Ayrıntılı metodoloji →</a></p>')

    # --- Haftalık bülten ---
    if bulten:
        g.append('<h2>Haftalık Medya Nabzı</h2><hr class="rule">')
        g.append(f'<p>Her hafta, o haftanın medya gündemi: hacim, ton, çerçeve payları ve kendi geçmişine '
                 f'göre olağandışı olanlar. Aşağıdaki rakamlar <strong>{html.escape(bulten["hafta"])}</strong> '
                 f'({tr_tarih(bulten["baslangic"])} – {tr_tarih(bulten["bitis"])}) haftasına aittir.</p>')
        deg = bulten.get("degisim_yuzde")
        g.append('<div class="grid">')
        for n, l in [(tr_sayi(bulten["toplam_haber"]), "haber"),
                     (tr_sayi(round(bulten["gunluk_ortalama"])), "günlük ortalama"),
                     ("—" if deg is None else f"{deg:+.1f}%".replace(".", ","), "geçen haftaya göre"),
                     (f"{bulten['ortalama_ton']:.2f}".replace(".", ","), "ortalama ton")]:
            g.append(f'<div class="stat"><div class="n">{n}</div><div class="l">{l}</div></div>')
        g.append('</div>')
        if bulten["sapmalar"]:
            g.append('<h3>O haftanın sapmaları</h3>')
            for s in bulten["sapmalar"][:4]:
                yuksek = s["yuzdelik"] >= 90
                g.append(f'<div class="row"><span>{html.escape(s["cerceve"])} — '
                         f'<b>%{s["pay"] * 100:.2f}</b></span>'
                         f'<b class="{"hi" if yuksek else "lo"}">{s["yuzdelik"]:.0f}. yüzdelik · '
                         f'{"olağandışı yüksek" if yuksek else "olağandışı düşük"}</b></div>')
            g.append('<p class="small">Taban, aynı çerçevenin önceki 52 haftasıdır. Haftalık haber sayısı '
                     f'{bulten["esikler"]["min_haber"]}\'in altındaki çerçeveler sapma sayılmaz.</p>')
        if pdf_adi:
            g.append(f'<p><a class="btn" href="{pdf_adi}">Örnek bülteni indir (PDF)</a></p>')

    # --- Vaka raporu ---
    if vaka_adi:
        g.append('<h2>Örnek vaka raporu</h2><hr class="rule">')
        g.append('<p class="lead">Aracın tek bir konuda neler ürettiğini gösteren tam bir dosya: '
                 '<strong>6 Şubat 2023 depremleri ve sonrasında Türk medyası.</strong></p>')
        g.append('<p>3 yıl 8 aylık dönemde <strong>278 haber sitesinde 263.194 haberin başlığına</strong> '
                 'dayanır. Rapor depremi değil, deprem haberciliğini inceler.</p>')
        g.append('<h3>Üç bulgu</h3><ul>')
        for b in [
            '<strong>Anlatı dört aşamada değişti — ve hesap sorma aşaması hiç olmadı.</strong> '
            'İlk 72 saatte yardım ve dayanışma %20,9; birinci yılda siyaset %9,3; üçüncü yıldan sonra '
            'bilim ve risk %9,6 ile zirvede. <strong>Sorumluluk ve hesap sorma çerçevesi hiçbir dönemde '
            '%3,1&#39;i aşmadı.</strong>',
            '<strong>Aynı olay, yayın çizgisine göre ölçülebilir biçimde farklı çerçevelendi.</strong> '
            'Sorumluluk ve hesap sorma muhalif/bağımsız yayınlarda %4,4, hükümete yakın yayınlarda %1,8. '
            'Siyaset çerçevesi ise hükümete yakın yayınlarda %14,1 ile en yüksek.',
            '<strong>Bu konuda en çok yayılan haberler 6 Şubat&#39;la ilgili değil.</strong> '
            'En çok siteye yayılan başlıkların neredeyse tamamı rutin küçük sarsıntılar ve yurt dışı '
            'afetleri. Haberciliğe dair kendi başına bir bulgu.',
        ]:
            g.append(f"<li>{b}</li>")
        g.append('</ul>')
        g.append('<div class="note">Raporun dörtte biri <strong>söyleyemediklerine</strong> ayrılmıştır: '
                 'kapsamın örneklem olması, bir yayın çizgisinin veride hiç temsil edilmemesi, çerçevelerin '
                 'kaba bir araç olması, ton değerinin neden kullanılamadığı.</div>')
        g.append(f'<p><a class="btn" href="{vaka_adi}">Vaka raporunu indir (PDF, 7 sayfa)</a></p>')

    # --- Bekleme listesi (etkin değil) ---
    g.append('<h2>Bekleme listesi</h2><hr class="rule">')
    g.append('<div class="off"><p><strong>Henüz e-posta toplamıyoruz.</strong></p>'
             '<p class="small">Bülteni e-posta ile göndermeye, KVKK aydınlatma metni yayımlandıktan sonra '
             'başlayacağız. Bir aydınlatma metni olmadan kişisel veri toplamak doğru olmaz; form o zamana '
             'kadar burada etkin değil.</p></div>')

    yil = date.today().year
    return ISKELET % {"title": "Medya İndeksi — Türkçe medya arşivi",
                      "desc": "2015'ten bugüne Türkçe haberlerde bir konunun nasıl ele alındığını ölçen "
                              "araştırma aracı. GDELT verisiyle.",
                      "kok": "", "body": "\n".join(g), "footer": footer_html("", yasal_var), "yil": yil}


def alt_sayfa(baslik: str, md: str, yasal_var: bool) -> str:
    govde = ('<p class="kicker"><a href="index.html">← Medya İndeksi</a></p>' + md2html(md))
    return ISKELET % {"title": f"{baslik} — Medya İndeksi", "desc": baslik, "kok": "",
                      "body": govde, "footer": footer_html("", yasal_var), "yil": date.today().year}


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--ac", action="store_true", help="üretince tarayıcıda aç")
    a = p.parse_args()

    r = rakamlar()
    bulten, pdf = son_bulten()
    hazir = yasal_hazir()

    if SITE.exists():
        shutil.rmtree(SITE)
    SITE.mkdir(parents=True)
    (SITE / "style.css").write_text(CSS)
    pdf_adi = None
    if pdf:
        pdf_adi = f"medya-nabzi-{bulten['hafta']}.pdf"
        shutil.copy(pdf, SITE / pdf_adi)
    vaka_kaynak, vaka_adi = vaka_raporu()
    if vaka_kaynak:
        shutil.copy(vaka_kaynak, SITE / vaka_adi)
    (SITE / "index.html").write_text(index_html(r, bulten, pdf_adi, hazir, vaka_adi))

    md = (Path(__file__).resolve().parent / "metodoloji.md").read_text()
    for k, v in metodoloji_degerleri(r).items():
        md = md.replace("{{" + k + "}}", str(v))
    kalan = re.findall(r"\{\{(\w+)\}\}", md)
    if kalan:
        sys.exit(f"Metodoloji metninde doldurulmamış token kaldı: {', '.join(sorted(set(kalan)))}")
    (SITE / "metodoloji.html").write_text(alt_sayfa("Metodoloji", md, hazir))

    if hazir:
        for baslik, ad in YASAL_SAYFALAR:
            (SITE / f"{ad}.html").write_text(alt_sayfa(baslik, (YASAL / f"{ad}.md").read_text(), True))
    (SITE / ".nojekyll").write_text("")

    print(f"[OK] vitrin üretildi: {SITE}")
    if not vaka_kaynak:
        print("     [!] Vaka raporu yok: venv/bin/python -m cases.report deprem --marka")
    print("     sayfa: index.html, metodoloji.html"
          + (f", {len(YASAL_SAYFALAR)} yasal sayfa" if hazir else ""))
    if not hazir:
        print("     [!] Yasal metinler taslak (yer tutucu var): sayfaları ve bağlantıları üretilmedi.")
    if not bulten:
        print("     [!] Bülten yok: venv/bin/python pipeline/bulten.py")
    if a.ac:
        os.system(f"open '{SITE / 'index.html'}'")


if __name__ == "__main__":
    main()
