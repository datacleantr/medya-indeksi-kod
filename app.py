"""
GDELT GKG Analiz Dashboard'u (Kişi/Kurum ve Kopya Haber Analizli Sürüm)
--------------------------------------------------
Birden fazla GDELT GKG CSV dosyasını aynı anda yükleyip birleştirerek
interaktif olarak analiz eden Streamlit uygulaması.

Beklenen kolonlar (BigQuery gkg_partitioned sorgusundan):
    zorunlu : pub_timestamp, SourceCommonName, DocumentIdentifier, Themes, V2Tone
    opsiyonel: GKGRECORDID, V2Persons, Organizations, PageTitle, V2Locations, TranslationInfo
"""

import gc
import glob
import hashlib
import html
import io
import os
import re
import sys
import threading
import tomllib
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import plotly.io as pio
import streamlit as st

# --------------------------------------------------------------------------------
# SAYFA AYARLARI
# --------------------------------------------------------------------------------
# Ürün adı tek yerde: ekran, PDF ve dosya adları buradan okur. Büyük harfli biçim ayrıdır (Python'un upper()'ı
# Türkçe "i"yi "İ" yapmaz)
APP_NAME = "Medya İndeksi"
APP_NAME_UPPER = "MEDYA İNDEKSİ"
APP_SLUG = "medya_indeksi"

st.set_page_config(
    page_title=APP_NAME,
    page_icon=":material/search:",
    layout="wide",
    # "auto": masaüstünde açık, telefonda kapalı başlar (açıkken dar ekranın çoğunu kaplıyordu)
    initial_sidebar_state="auto",
)

# --------------------------------------------------------------------------------
# GÖRSEL DİL (Mercek "basılı dosya"): kâğıt zemin, mürekkep metin, serif başlık, kırmızı kalem vurgusu.
# Renkler .streamlit/config.toml'daki temayla aynıdır; burada yalnızca temanın kapsamadığı ayrıntılar var.
# --------------------------------------------------------------------------------
MK = {
    # muted, cyan ve amber_text kâğıt zeminde WCAG AA (4,5:1) metin kontrastını geçer; amber yalnızca kenarlık ve
    # grafiklerde (3:1 yeterli), metinde amber_text kullanılır
    "ink": "#172126", "muted": "#616e74", "line": "#d7dfe0", "line_strong": "#bdc9ca",
    "paper": "#eef2f1", "surface": "#f9fbfa", "surface_deep": "#e5ebea",
    "red": "#a9473c", "cyan": "#267684", "amber": "#bd7b25", "amber_text": "#94611d", "green": "#16715e",
}
MK_SEQUENCE = [MK["ink"], MK["red"], MK["cyan"], MK["amber"], MK["green"], MK["muted"], "#6b4c8a", "#c98a7f"]
# Ton ölçeği: olumsuz (kırmızı) -> nötr (açık gri) -> olumlu (yeşil)
MK_TONE_SCALE = [[0, MK["red"]], [0.5, "#e9e4dc"], [1, MK["green"]]]
# Bu genişliğin (piksel) altı telefon sayılır; mobil CSS kuralları burada devreye girer (Streamlit de sütunları
# ~640 piksel altında alt alta dizer)
MOBILE_MAX_PX = 640

st.markdown(
    f"""
    <style>
    .stApp {{
        background-color: {MK["paper"]};
        background-image: linear-gradient({MK["line"]}55 1px, transparent 1px),
                          linear-gradient(90deg, {MK["line"]}55 1px, transparent 1px);
        background-size: 32px 32px;
    }}
    /* Üst boşluk Streamlit'in üst çubuğundan (~60 px) büyük: marka satırı çubuğun altında kalmasın */
    .block-container {{ padding-top: 3.8rem; max-width: 1400px; }}
    div[data-testid="stMetric"] {{
        background: {MK["surface"]}; border: 1px solid {MK["line"]}; border-top: 3px solid {MK["ink"]};
        border-radius: 4px; padding: 12px 16px;
    }}
    div[data-testid="stMetricLabel"] p {{
        font-family: "IBM Plex Mono", monospace; text-transform: uppercase; letter-spacing: .06em;
        font-size: .72rem; color: {MK["muted"]};
    }}
    div[data-testid="stMetricValue"] {{ font-family: "Source Serif 4", Georgia, serif; font-weight: 600; }}
    .mk-kicker {{
        font-family: "IBM Plex Mono", monospace; text-transform: uppercase; letter-spacing: .14em;
        font-size: .74rem; color: {MK["red"]}; margin-bottom: .1rem;
    }}
    .mk-brand {{ font-family: "Source Serif 4", Georgia, serif; font-size: 2.3rem; font-weight: 600;
        color: {MK["ink"]}; line-height: 1.1; margin: 0; }}
    .mk-brand span {{ color: {MK["red"]}; }}
    .mk-tagline {{ color: {MK["muted"]}; font-size: 1.02rem; margin: .2rem 0 .6rem 0; }}
    .mk-card-title {{
        font-family: "IBM Plex Mono", monospace; text-transform: uppercase; letter-spacing: .08em;
        font-size: .74rem; color: {MK["muted"]}; border-bottom: 1px solid {MK["line_strong"]};
        padding-bottom: .25rem; margin-bottom: .5rem;
    }}
    .mk-card-title.mk-red {{ color: {MK["red"]}; border-bottom-color: {MK["red"]}; font-weight: 600; }}
    .mk-headline {{ font-family: "Source Serif 4", Georgia, serif; font-size: 1.9rem; font-weight: 600;
        color: {MK["ink"]}; margin: 0 0 .2rem 0; }}
    .mk-fresh {{ font-family: "IBM Plex Mono", monospace; font-size: .7rem; letter-spacing: .06em;
        text-transform: uppercase; color: {MK["muted"]}; border: 1px solid {MK["line_strong"]};
        border-radius: 3px; padding: .22rem .55rem; margin: .1rem 0 .5rem 0; display: inline-block;
        max-width: 100%; background: {MK["surface"]}88; }}
    .mk-fresh b {{ color: {MK["ink"]}; font-weight: 600; }}
    /* Parça bir bütündür: "21.10.2026" satır sonunda ikiye bölünmez, bölünme parçalar arasında olur */
    .mk-fresh .part {{ white-space: nowrap; }}
    .mk-fresh .sep {{ color: {MK["line_strong"]}; margin: 0 .45rem; }}
    .mk-peak-ai {{ font-size: .85rem; color: {MK["muted"]}; margin: -.1rem 0 .45rem 1.2rem;
        padding-left: .6rem; border-left: 2px solid {MK["amber"]}66; }}
    .mk-peak-ai .tag {{ font-family: "IBM Plex Mono", monospace; font-size: .6rem; letter-spacing: .08em;
        text-transform: uppercase; color: {MK["amber_text"]}; border: 1px solid {MK["amber"]}55;
        padding: .03rem .26rem; border-radius: 2px; margin-right: .35rem; }}
    .mk-ai {{ border-left: 3px solid {MK["cyan"]}; padding: .1rem .75rem; margin: .2rem 0 .6rem 0; }}
    .mk-ai p {{ margin: 0 0 .55rem 0; }}
    .mk-ai .tag {{ font-family: "IBM Plex Mono", monospace; font-size: .62rem; letter-spacing: .08em;
        text-transform: uppercase; padding: .05rem .3rem; border-radius: 2px; margin-right: .4rem;
        vertical-align: .08rem; }}
    .mk-ai .veri {{ color: {MK["cyan"]}; border: 1px solid {MK["cyan"]}55; }}
    .mk-ai .yorum {{ color: {MK["amber_text"]}; border: 1px solid {MK["amber"]}55; }}
    .mk-note {{ font-size: .78rem; color: {MK["muted"]}; border-left: 3px solid {MK["amber"]};
        padding: .15rem .6rem; margin: .3rem 0 .8rem 0; background: {MK["surface"]}88; }}
    .mk-quote {{ font-family: "Source Serif 4", Georgia, serif; font-size: 1.02rem; color: {MK["ink"]};
        border-left: 3px solid {MK["red"]}; padding: .2rem .8rem; margin: .5rem 0 .1rem 0; }}
    .mk-quote-src {{ font-size: .78rem; color: {MK["muted"]}; margin: 0 0 .6rem .9rem; }}
    .mk-rank {{ display: flex; justify-content: space-between; border-bottom: 1px dotted {MK["line_strong"]};
        padding: .18rem 0; font-size: .92rem; }}
    .mk-rank b {{ font-family: "IBM Plex Mono", monospace; font-weight: 500; color: {MK["muted"]}; }}
    .mk-rank span {{ min-width: 0; overflow-wrap: anywhere; padding-right: .5rem; }}
    /* Streamlit'in markdown paragraf kuralı (font-size) yukarıdaki sınıf kurallarını eziyordu; daha belirgin seçici */
    div[data-testid="stMarkdownContainer"] p.mk-brand {{ font-size: 2.3rem; line-height: 1.1; }}
    div[data-testid="stMarkdownContainer"] p.mk-headline {{ font-size: 1.9rem; line-height: 1.2; }}
    div[data-testid="stMarkdownContainer"] p.mk-tagline {{ font-size: 1.02rem; }}

    /* Dar ekran (telefon): Streamlit sütunları alt alta dizer; burada yalnızca taşan veya çok yer kaplayan
       ayrıntılar düzeltilir. Masaüstü görünümü etkilenmez. */
    @media (max-width: {MOBILE_MAX_PX}px) {{
        .block-container {{ padding: 3.8rem .9rem 2rem .9rem; }}
        div[data-testid="stMarkdownContainer"] p.mk-brand {{ font-size: 1.55rem; }}
        div[data-testid="stMarkdownContainer"] p.mk-headline {{ font-size: 1.35rem !important;
            line-height: 1.2 !important; }}
        div[data-testid="stMarkdownContainer"] p.mk-tagline {{ font-size: .95rem; }}
        .mk-kicker, .mk-card-title {{ letter-spacing: .06em; }}
        /* Tazelik şeridi telefonda tek satıra sığmıyor: parçalar alt alta, ayraçlar gizlenir */
        .mk-fresh {{ display: block; font-size: .64rem; letter-spacing: .02em; padding: .3rem .5rem;
            line-height: 1.55; }}
        .mk-fresh .part {{ display: block; }}
        .mk-fresh .sep {{ display: none; }}
        .mk-rank {{ font-size: .86rem; }}
        /* Yalnızca gösterge kartlarından oluşan satırlar: alt alta değil, satır başına iki kart */
        div[data-testid="stHorizontalBlock"]:has(> div[data-testid="stColumn"] div[data-testid="stMetric"]) {{
            flex-direction: row !important; flex-wrap: wrap !important; gap: .5rem !important;
        }}
        div[data-testid="stHorizontalBlock"]:has(> div[data-testid="stColumn"] div[data-testid="stMetric"])
            > div[data-testid="stColumn"] {{
            flex: 1 1 calc(50% - .5rem) !important; min-width: calc(50% - .5rem) !important;
            width: calc(50% - .5rem) !important;
        }}
        div[data-testid="stMetric"] {{ padding: 8px 10px; }}
        div[data-testid="stMetricValue"] {{ font-size: 1.45rem; }}
        div[data-testid="stMetricLabel"] p {{ font-size: .64rem; letter-spacing: .03em; }}
        /* Dokunmatikte grafik araç çubuğu kalabalık yapar ve yanlışlıkla dokunulur */
        .modebar-container {{ display: none !important; }}
    }}
    </style>
    """,
    unsafe_allow_html=True,
)

# Grafik şablonu: şeffaf zemin (kâğıt dokusu görünsün), mürekkep yazı, ince çizgiler, Mercek renkleri
pio.templates["mercek"] = go.layout.Template(
    layout=go.Layout(
        font=dict(family="IBM Plex Sans, Helvetica Neue, sans-serif", color=MK["ink"], size=12),
        title=dict(font=dict(family="Source Serif 4, Georgia, serif", size=16)),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(249,251,250,0.75)",
        colorway=MK_SEQUENCE,
        xaxis=dict(gridcolor=MK["line"], linecolor=MK["line_strong"], zerolinecolor=MK["line_strong"]),
        yaxis=dict(gridcolor=MK["line"], linecolor=MK["line_strong"], zerolinecolor=MK["line_strong"]),
        legend=dict(bgcolor="rgba(0,0,0,0)"),
        hoverlabel=dict(bgcolor=MK["surface"], font=dict(color=MK["ink"])),
        margin=dict(l=10, r=10, t=40, b=10),
    )
)
pio.templates.default = "plotly_white+mercek"
PLOTLY_TEMPLATE = "plotly_white+mercek"

# --------------------------------------------------------------------------------
# SABİTLER
# --------------------------------------------------------------------------------

# BigQuery veri kaynağı (pipeline/ ile doldurulan Türkçe haber tablosu)
BQ_PROJECT = os.environ.get("GDELT_BQ_PROJECT", "banded-scion-476220-r0")
BQ_ARTICLES = f"{BQ_PROJECT}.gdelt_tr.articles"
BQ_TOTALS = f"{BQ_PROJECT}.gdelt_tr.daily_totals"
# Gün × site haber sayısı: kapsam denetimi (yerelde karşılığı data/archive/source_daily.parquet)
BQ_SOURCE_DAILY = f"{BQ_PROJECT}.gdelt_tr.source_daily"
BQ_OZET = f"{BQ_PROJECT}.gdelt_tr.ozet_gunluk"
BQ_SOZLUK = f"{BQ_PROJECT}.gdelt_tr.ad_sozlugu"
BQ_EVENTS_TU = f"{BQ_PROJECT}.gdelt_tr.events_tu"
# Boş aktör alanı okuma yoluna göre "None", "nan" veya boş gelebilir; hepsi yok sayılır
BOS_AKTOR = {"", "none", "nan", "nat", "null"}
# E3: bir olayın "ciddi" sayılması için gereken en az kaynak sayısı. Eşiksiz bakıldığında olayların
# %75'i tek anılmalıdır ve oran gürültüye boğulur (ölçüm 23.10.2026: eşiksiz %64,4, eşikli %53,5).
EVENTS_TU_MIN_KAYNAK = 5
# Arşiv dışı künyelerin BigQuery kopyası (pipeline/extra_upload.py); yayındaki panel buradan okur
BQ_EXTRA = f"{BQ_PROJECT}.gdelt_tr.extra_articles"
# Panel aramaları Storage Read API ile yapılır: sorgu kotasına sayılmaz, ayda ilk 300 TiB okuma ücretsizdir.
# Aşağıdaki sınırlar ücretsiz payı ve paneli kötüye kullanıma karşı korur.
# Tek aramanın okuyabileceği en fazla GB; aşan arama çalıştırılmaz. Tarih sınırı yoktur: tüm arşiv (2015–) tek
# aramada ~62 GB okur ve ~1 dakika sürer (Eylül 2026); arşiv yılda ~5 GB büyür
BQ_MAX_GB = 80
# Panelin bir UTC gününde toplam okuyabileceği en fazla GB (süreç içi sayaç, tüm kullanıcılar birlikte): ~48 tam
# arşiv araması; ayda en fazla ~90 TB, Storage Read'in 300 TiB ücretsiz payının altında
BQ_DAILY_MAX_GB = 3000
# Arşiv dışı göz atmada (yayında) bu kadar günden uzun aralıklar "uzun sürebilir" notuyla okunur
BQ_LONG_RANGE_DAYS = 366
# Çok genel aramalarda paneli yormamak için en fazla haber; aşılırsa arama durdurulur
BQ_MAX_ROWS = 50_000
BQ_COLUMNS = (
    "GKGRECORDID, pub_timestamp, SourceCommonName, DocumentIdentifier, PageTitle, Themes, V2Tone, "
    "V2Persons, Organizations, V2Locations, TranslationInfo"
)
# Yerel Parquet arşivi (pipeline/export.py ile indirilir): data/archive/articles/<gün>.parquet
ARCHIVE_DIR = os.environ.get(
    "GDELT_ARCHIVE", os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "archive")
)
ARCHIVE_ARTICLES = os.path.join(ARCHIVE_DIR, "articles")
# Başlık aramasında her Türkçe harf ailesinin karşılıkları. GDELT başlıkları Türkçe harfleri HTML koduyla (&#x131;)
# veya cp1254 bozulmasıyla (ı -> &#xFD; "ý", ğ -> &#xF0; "ð", ş -> &#xFE; "þ") yazar; nadiren UTF-8 çift kodlama
# (&#xC4;&#xB1;) ve Python'un İ küçültmesinden kalan birleşik nokta (&#x307;) görülür. Kalıp (?i) ile çalışır.
TR_TITLE_VARIANTS = {
    "i": ["i", "ı", "İ", "ý", "Ý", "&#x131;", "&#x130;", "&#xfd;", "&#xdd;", "&#305;", "&#304;",
          "&#xc4;&#xb1;", "&#xc4;&#xb0;"],
    "g": ["g", "ğ", "Ğ", "ð", "Ð", "&#x11f;", "&#x11e;", "&#xf0;", "&#xd0;", "&#287;", "&#286;", "&#xc4;&#x9f;"],
    "s": ["s", "ş", "Ş", "þ", "Þ", "&#x15f;", "&#x15e;", "&#xfe;", "&#xde;", "&#351;", "&#350;", "&#xc5;&#x9f;"],
    "c": ["c", "ç", "Ç", "&#xe7;", "&#xc7;", "&#231;", "&#199;", "&#xc3;&#xa7;"],
    "o": ["o", "ö", "Ö", "&#xf6;", "&#xd6;", "&#246;", "&#214;", "&#xc3;&#xb6;"],
    "u": ["u", "ü", "Ü", "&#xfc;", "&#xdc;", "&#252;", "&#220;", "&#xc3;&#xbc;"],
}
# Başlıkta kelimeler arası: boşluk veya HTML bölünmez boşluk
TITLE_WORD_GAP = r"(?:\s|&#xa0;|&nbsp;)+"
# Kelime sonu sınırı (yalnızca özel isimlerde ve kısa kelimelerde uygulanır, bkz. topic_patterns): "Kars" ifadesinin
# "karşı", "Karşıyaka" içinde eşleşmesini önler. Başlıkta harfler HTML kodu olabildiği için noktalama ve boşluk
# kodları da sınır sayılır; harf kodları (&#x131; gibi) sayılmaz.
TITLE_APOSTROPHE = r"(?:'|’|&#x27;|&#39;|&#x2019;|&#8217;|&apos;)"
TITLE_LETTER = r"(?:\pL|&#x[0-9a-f]{2,4};|&#[0-9]{2,4};)"
TITLE_END = (
    r"(?:$|[^\pL\pN&]|&#x(?:2[0-9a-f]|3[a-f]|a0|ab|bb|20[0-9a-f]{2});|&(?:quot|amp|nbsp|lt|gt);"
    r"|&#(?:3[2-9]|4[0-7]|160|82[0-9]{2});)"
)
# URL'de kesme işareti olmadığından özel isimden sonra gelebilecek yaygın ekler (tek harfli ekler "karşı" gibi
# kelimelerle karıştığı için alınmadı)
URL_SUFFIXES = r"(?:da|de|ta|te|dan|den|tan|ten|daki|deki|taki|teki|nin|nun|ya|ye|li|lu|lar|ler)"

# GDELT bazı sitelerde (ör. birgun.net) bağlantıyla birlikte HTML niteliklerini de URL'ye ekler:
# ".../haber-736839%20target=_blank%20class=stretched-link". Aynı haber birkaç URL ile gelmesin diye bu kuyruk silinir.
URL_ATTR_JUNK = r"(?i)(?:%20|\s|\")+(?:target|class|rel|id|style|title|href|onclick|aria-[a-z-]+|data-[a-z-]+)=.*$"

REQUIRED_COLUMNS = ["pub_timestamp", "SourceCommonName", "DocumentIdentifier", "Themes", "V2Tone"]
OPTIONAL_COLUMNS = ["GKGRECORDID", "V2Persons", "Organizations", "PageTitle", "V2Locations", "TranslationInfo"]

# V2Locations alanları: tip#ad#ülke#adm1#adm2#enlem#boylam#featureid#karakter_konumu
LOCATION_FIELDS = ["LocType", "LocName", "Country", "ADM1", "ADM2", "Lat", "Lon", "FeatureID", "Offset"]
LOCATION_TYPES = {"1": "Ülke", "2": "ABD eyaleti", "3": "ABD şehri", "4": "Şehir / yer", "5": "İl / eyalet"}

# GDELT bazen kişi adlarını yer adı sanıp kodlar (örn. bir soyadı, aynı adlı bir köye düşer).
# Adı aynı haberdeki bir kişi adının parçası olan yer, geçtiği haberlerin en az bu oranında böyleyse
# haritada varsayılan olarak hariç tutulur.
GEO_NOISE_MIN_SHARE = 0.5

LANGUAGE_NAMES = {"tur": "Türkçe", "eng": "İngilizce (orijinal)", "aze": "Azerice", "ara": "Arapça", "deu": "Almanca", "rus": "Rusça"}

# Başlık çerçeveleri: anahtar kelimeler Türkçe karakterlerden arındırılmış küçük harfle yazılır,
# kelime başında eşleşir (örn. "dava" -> "davasi", "davada"). Başka kelimelerin başı olan kısa kökler
# ("sel" -> selam, "kaza" -> kazanç, "zam" -> zaman) kullanılmaz; bunlar için ifade yazılır ("sel felaket").
# Kenar çubuğundaki "Çerçeve seti" ile seçilir; "Özel" seçilirse kullanıcı kendi listesini yazar.
FRAME_SETS = {
    "Genel": {
        "Siyaset / seçim": ["secim", "parti", "milletvekil", "meclis", "tbmm", "cumhurbaskan", "bakan",
                            "muhalefet", "iktidar", "belediye baskan", "aday", "kongre", "genel baskan"],
        "Yargı / soruşturma": ["dava", "durusma", "mahkeme", "sorusturma", "gozalti", "tutuklan", "iddianame",
                               "savci", "beraat", "yargitay", "hapis", "avukat", "cezaevi"],
        "Güvenlik / terör": ["teror", "pkk", "feto", "deas", "isid", "patlama", "saldiri", "silahli", "catisma",
                             "operasyon", "asker", "sehit", "jandarma", "emniyet"],
        "Ekonomi": ["ekonomi", "enflasyon", "faiz", "dolar", "borsa", "ihracat", "ithalat", "asgari ucret",
                    "vergi", "butce", "yatirim", "fiyat", "piyasa", "merkez bankasi", "kredi", "zamm", "zam geldi",
                    "akaryakit"],
        "Afet / kaza": ["deprem", "yangin", "trafik kaza", "kazada", "kazasi", "kaza gecir", "sel felaket",
                        "sel baskin", "selde", "heyelan", "cig dus", "firtina", "afad", "yarali",
                        "hayatini kaybet"],
        "Sağlık": ["saglik", "hastane", "doktor", "hekim", "salgin", "virus", "kanser", "tedavi", "ameliyat",
                   "hasta"],
        "Eğitim": ["egitim", "okul", "ogrenci", "ogretmen", "universite", "sinav", "meb", "lgs", "yks"],
        "Dış ilişkiler": ["abd", "avrupa birligi", "nato", "rusya", "israil", "iran", "yunanistan", "disisleri",
                          "buyukelci", "diplomasi", "ukrayna", "gazze", "suriye"],
        "Göç": ["gocmen", "multeci", "siginmaci", "duzensiz goc", "goc idare", "goc politika"],
        "Kültür / sanat / spor": ["festival", "konser", "film", "sergi", "tiyatro", "kitap", "odul", "futbol",
                                  "basketbol", "voleybol", "super lig", "sampiyon", "turnuva", "gures",
                                  "transfer", "teknik direktor"],
        "Anma / tören": ["anma", "anild", "anisina", "yil donum", "vefat", "cenaze", "toren", "mevlit", "kabri",
                         "mezar", "aramizdan ayril"],
    },
}
DEFAULT_FRAME_SET = "Genel"
CUSTOM_FRAME_SET = "Özel (kendi listem)"
CUSTOM_FRAME_EXAMPLE = "Göç: göçmen, mülteci, sığınmacı\nEkonomi: enflasyon, faiz, asgari ücret"

TITLE_STOPWORDS = {
    "ve", "ile", "bir", "bu", "şu", "da", "de", "ki", "mi", "mı", "mu", "mü", "için", "gibi", "olan", "oldu",
    "ne", "nasıl", "neden", "kim", "kimdir", "nerede", "hangi", "son", "dakika", "haber", "haberleri",
    "haberi", "gündem", "yeni", "the", "and",
}

TR_FOLD = str.maketrans({
    "ç": "c", "ğ": "g", "ı": "i", "ö": "o", "ş": "s", "ü": "u", "â": "a", "î": "i", "û": "u",
    "Ç": "c", "Ğ": "g", "İ": "i", "I": "i", "Ö": "o", "Ş": "s", "Ü": "u", "Â": "a", "Î": "i", "Û": "u",
})
# Windows-1254 sayfaların Latin-1 sanılmasından doğan bozulma (örn. "Iðdýr" -> "Iğdır")
MOJIBAKE_FIX = str.maketrans({"ý": "ı", "Ý": "İ", "ð": "ğ", "Ð": "Ğ", "þ": "ş", "Þ": "Ş"})
FOLDED_STOPWORDS = frozenset(w.translate(TR_FOLD).lower() for w in TITLE_STOPWORDS)

# V2Tone alanındaki 7 değerin sırası (GDELT GKG 2.1 kod kitabı)
TONE_FIELDS = [
    "AvgTone", "PositiveScore", "NegativeScore", "Polarity",
    "ActivityRefDensity", "SelfGroupRefDensity", "WordCount",
]

# Kopya içerik tespiti için alt sınır: çok kısa metinlerde ton değerleri tesadüfen çakışabilir
MIN_WORDS_FOR_COPY = 50

# Benzer başlık eşleşmesinde iki haberin kelime sayıları en fazla bu oranda farklı olabilir.
# Aynı olayı anlatan ama farklı yazılmış haberleri (örn. 233 vs 441 kelime) kopya saymamak için.
COPY_WORDCOUNT_TOLERANCE = 0.35
# Kenar çubuğundaki kopya tespiti ayarlarının varsayılanları (ana ekrandaki gündem kartları da bunları kullanır)
COPY_TITLE_THRESHOLD = 0.6
COPY_WINDOW_HOURS = 48
# Bundan az anlamlı kelimesi kalan başlıklar (örn. "<konu adı> anıldı") karşılaştırılmaz
TITLE_MIN_TOKENS = 2

# Başlıkların en az bu oranında geçen kelimeler konunun kendisi sayılır (örn. aranan kişinin adı).
# Bu kelimeler her başlıkta geçtiği için benzerliği yapay olarak yükseltir; başlık analizinde yok sayılır.
TOPIC_WORD_MIN_SHARE = 0.6
# Daha az başlıkta oranlar güvenilir olmadığından konu kelimesi aranmaz
TOPIC_WORD_MIN_TITLES = 10

# GDELT zaman damgaları UTC'dir; analiz Türkiye saatiyle yapılır
LOCAL_TZ = "Europe/Istanbul"

# Tema kodlarının önek bazlı aileleri (uzun/özel önekler önce gelmeli)
THEME_FAMILIES = [
    ("TAX_FNCACT", "Kişi rolü / meslek (TAX_FNCACT)"),
    ("TAX_ETHNICITY", "Etnik grup"),
    ("TAX_RELIGION", "Din / inanç"),
    ("TAX_WORLDLANGUAGES", "Dil adı"),
    ("TAX_WORLDMAMMALS", "Hayvan adı (çoğunlukla çeviri gürültüsü)"),
    ("TAX_WORLDFISH", "Hayvan adı (çoğunlukla çeviri gürültüsü)"),
    ("TAX_WORLDINSECTS", "Hayvan adı (çoğunlukla çeviri gürültüsü)"),
    ("TAX_WORLDREPTILES", "Hayvan adı (çoğunlukla çeviri gürültüsü)"),
    ("TAX_WORLDBIRDS", "Hayvan adı (çoğunlukla çeviri gürültüsü)"),
    ("TAX_DISEASE", "Hastalık"),
    ("TAX_MILITARY_TITLE", "Askeri rütbe"),
    ("TAX_WEAPONS", "Silah"),
    ("TAX_POLITICAL_PARTY", "Siyasi parti"),
    ("TAX_FOODSTAPLES", "Gıda"),
    ("TAX_ECON", "Ekonomi (taksonomi)"),
    ("TAX_", "Diğer GDELT taksonomisi"),
    ("WB_", "Dünya Bankası kalkınma taksonomisi"),
    ("CRISISLEX_", "Kriz iletişimi sözlüğü (CrisisLex)"),
    ("EPU_", "Ekonomik politika belirsizliği (EPU)"),
    ("UNGP_", "BM rehber ilkeleri / insan hakları"),
    ("USPEC_", "Genel siyaset / belirsizlik"),
    ("SOC_POINTSOFINTEREST", "Mekân türü"),
    ("SOC_", "Toplumsal olay"),
    ("ECON_", "Ekonomi"),
    ("ENV_", "Çevre / doğal kaynak"),
    ("MEDIA_", "Medya"),
    ("CRIME_", "Suç"),
    ("MANMADE_DISASTER", "Afet / kaza"),
    ("NATURAL_DISASTER", "Afet / kaza"),
    ("HUMAN_RIGHTS", "İnsan hakları"),
    ("SELF_IDENTIFIED", "Kendini tanımlayan ifade"),
]


# --------------------------------------------------------------------------------
# YARDIMCI FONKSİYONLAR
# --------------------------------------------------------------------------------


# Arama başına değişen argümanlı önbelleklerin üst sınırı: yayında (Streamlit Cloud) bellek sınırlıdır; her giriş
# bir aramanın tablolarını tutar. Sınır dolunca en eski giriş atılır
CACHE_MAX_ENTRIES = 16
# Büyük tablolar (arama sonucu, işlenmiş haberler, satırlara açılmış tema/yer listeleri) için daha dar sınır ve süre.
# Hazırlanan arama ayrıca oturumda tutulur (search_bundle); bu önbellekler yalnızca ilk hazırlıkta ve aynı aramanın
# başka oturumlarda tekrarında kullanılır. Eylül 2026 yük testi (5 eşzamanlı tüm yıllar araması, 31–39 bin haber):
# sunucu belleği bu düzenlemeler olmadan ~4,4 GB'a çıkıyordu, sonra ~2,3 GB (Community Cloud sınırı ~2,7 GB)
CACHE_BIG_ENTRIES = 2
CACHE_BIG_TTL = 1800
# Aynı anda en fazla bu kadar arşiv araması okunup hazırlanır; diğerleri sıra bekler (bellek tepe noktasını sınırlar)
SEARCH_CONCURRENCY = 2


@st.cache_resource(show_spinner=False)
def search_gate() -> dict:
    """Tüm oturumlar için ortak arama sırası: semafor ve o an çalışan arama sayısı."""
    return {"sem": threading.BoundedSemaphore(SEARCH_CONCURRENCY), "running": 0, "lock": threading.Lock()}


def search_busy() -> bool:
    return search_gate()["running"] >= SEARCH_CONCURRENCY


@contextmanager
def search_slot():
    """Arşiv araması için sıra: aynı anda en fazla SEARCH_CONCURRENCY arama. Çıkışta boşalan bellek geri verilir."""
    gate = search_gate()
    with gate["sem"]:
        with gate["lock"]:
            gate["running"] += 1
        try:
            yield
        finally:
            with gate["lock"]:
                gate["running"] -= 1
            release_memory()


def run_parallel(tasks: dict) -> dict:
    """Birbirinden bağımsız okumaları (çoğu ağ beklemesi) aynı anda çalıştırır: {ad: fonksiyon} -> {ad: sonuç}.
    Hata veren görevin sonucu Exception nesnesidir. İş parçacıklarına oturum bağlamı eklenir; önbellekli fonksiyonlar
    böylece normal çalışır (st öğeleri çağrılmamalıdır)."""
    from streamlit.runtime.scriptrunner import add_script_run_ctx, get_script_run_ctx

    ctx, results = get_script_run_ctx(), {}

    def work(name, fn):
        try:
            results[name] = fn()
        except Exception as e:
            results[name] = e

    threads = [threading.Thread(target=work, args=item, daemon=True) for item in tasks.items()]
    for t in threads:
        add_script_run_ctx(t, ctx)
        t.start()
    for t in threads:
        t.join()
    return results


def release_memory() -> None:
    """Büyük tablolar bırakıldıktan sonra belleği işletim sistemine iade eder (Arrow havuzu; Linux'ta glibc)."""
    gc.collect()
    try:
        import pyarrow as pa

        pa.default_memory_pool().release_unused()
    except Exception:
        pass
    if sys.platform.startswith("linux"):
        try:
            import ctypes

            ctypes.CDLL("libc.so.6").malloc_trim(0)
        except Exception:
            pass


@st.cache_data(ttl=CACHE_BIG_TTL, max_entries=CACHE_BIG_ENTRIES, show_spinner=False)
def load_data(files: tuple) -> pd.DataFrame:
    """(dosya_adı, bayt) çiftlerini okuyup tek DataFrame'de birleştirir."""
    df_list = [pd.read_csv(io.BytesIO(content), dtype={"GKGRECORDID": str}) for _, content in files]
    return pd.concat(df_list, ignore_index=True)


def title_char_pattern(c: str) -> str:
    """Başlık kalıbında tek harf: Türkçe harf ailesindeyse tüm karşılıkları, değilse kendisi."""
    variants = TR_TITLE_VARIANTS.get(fold_tr(c).lower())
    if not variants:
        return re.escape(c)
    alt = "|".join(re.escape(v) for v in variants)
    # İ'nin Python küçültmesi "i̇" (i + birleşik nokta) olarak kalmış olabilir
    return f"(?:{alt})(?:&#x307;)?" if fold_tr(c).lower() == "i" else f"(?:{alt})"


def topic_patterns(term: str) -> dict:
    """Aranan ifadeden URL, başlık ve kişi alanı için Türkçe karakter farklarına dayanıklı kalıplar üretir.
    Kalıplar RE2 sözdizimindedir (BigQuery ve DuckDB); her kalıp kelime başında sınır ister.
    Son kelime büyük harfle başlıyorsa (özel isim) veya en fazla 4 harfliyse kelime sonu da sınırlanır: başlıkta
    yalnızca kesme işaretli ek ("Kars'ta") veya -lı/-li ("Karslı"), URL'de URL_SUFFIXES ekleri kabul edilir.
    Aksi halde (cins isim, "deprem") ekler serbesttir ("depremin", "depremzede").
    URL: Türkçe harfler ASCII'ye indirgenir, 'ğ' isteğe bağlıdır, kelimeler arası serbesttir.
    Başlık: her Türkçe harf yalnızca kendi karşılıklarıyla (TR_TITLE_VARIANTS) eşleşir.
    Kişi: GDELT kişi adlarını ASCII yazar."""
    words = term.split()
    url_words = ["".join("g?" if c in "ğĞ" else re.escape(fold_tr(c)) for c in w) for w in words]
    title_words = ["".join(title_char_pattern(c) for c in w) for w in words]
    strict_end = bool(words) and (words[-1][0].isupper() or len(words[-1]) <= 4)
    url_end = title_end = ""
    if strict_end:
        url_end = URL_SUFFIXES + "?(?:[^a-z0-9]|$)"
        derived = f"(?:l(?:{title_char_pattern('i')}|{title_char_pattern('u')})(?:l[ae]r)?)"
        title_end = f"(?:{TITLE_APOSTROPHE}{TITLE_LETTER}*|{derived})?{TITLE_END}"
    return {
        "url_re": "(?i)(?:^|[^a-z0-9])" + ".*".join(url_words) + url_end,
        "title_re": r"(?i)(?:^|[^\pL\pN])" + TITLE_WORD_GAP.join(title_words) + title_end,
        "person_re": r"(?i)\b" + " ".join(re.escape(fold_tr(w)) for w in words) + r"\b",
    }


@st.cache_resource(show_spinner=False)
def bq_credentials():
    """Streamlit secrets'ta [gcp_service_account] varsa (yayındaki panel) o servis hesabının kimliği,
    yoksa None (yerel kullanım: gcloud oturumu, ADC)."""
    try:
        info = dict(st.secrets["gcp_service_account"])
    except Exception:
        return None
    if not info:
        return None
    from google.oauth2 import service_account

    return service_account.Credentials.from_service_account_info(info)


@st.cache_resource(show_spinner=False)
def bq_client():
    from google.cloud import bigquery

    return bigquery.Client(project=BQ_PROJECT, credentials=bq_credentials())


@st.cache_resource(show_spinner=False)
def bq_reader():
    from google.cloud.bigquery_storage import BigQueryReadClient

    return BigQueryReadClient(credentials=bq_credentials())


@st.cache_resource(show_spinner=False)
def bq_usage() -> dict:
    """Panelin UTC gün başına okuduğu bayt sayacı; tüm oturumlar aynı sözlüğü paylaşır.
    Uygulama yeniden başlarsa sıfırlanır."""
    return {}


def bq_used_today() -> int:
    return bq_usage().get(datetime.now(timezone.utc).date(), 0)


def bq_literal(value: str) -> str:
    """Storage Read API filtresi için BigQuery metin sabiti (ters bölü ve tırnak kaçışlı)."""
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


@st.cache_data(ttl=3600, show_spinner=False)
def bq_coverage() -> tuple:
    """gdelt_tr'de yüklü ilk ve son gün (UTC). Tablo satırlarını listeler (tabledata.list): ücretsizdir."""
    from google.cloud import bigquery

    rows = bq_client().list_rows(BQ_TOTALS, selected_fields=[bigquery.SchemaField("day", "DATE")])
    days = [r["day"] for r in rows]
    return min(days), max(days)


@st.cache_data(ttl=CACHE_BIG_TTL, max_entries=CACHE_BIG_ENTRIES, show_spinner=False)
def bq_fetch(term: str, start, end) -> tuple:
    """Konuyla eşleşen haberleri Storage Read API ile getirir; filtre BigQuery sunucusunda çalışır ve yalnızca
    eşleşen satırlar gelir. (DataFrame, okunan bayt) döner. Sonuç CACHE_BIG_TTL süre önbellekte kalır.
    Sınır hataları önbelleğe alınmaz."""
    import threading
    from concurrent.futures import ThreadPoolExecutor

    import pyarrow as pa
    from google.cloud.bigquery_storage import types

    pat = topic_patterns(term)
    restriction = (
        f"gkg_day BETWEEN DATE '{start:%Y-%m-%d}' AND DATE '{end:%Y-%m-%d}' AND ("
        f"REGEXP_CONTAINS(DocumentIdentifier, {bq_literal(pat['url_re'])}) OR "
        f"REGEXP_CONTAINS(PageTitle, {bq_literal(pat['title_re'])}) OR "
        f"REGEXP_CONTAINS(V2Persons, {bq_literal(pat['person_re'])}))"
    )
    table = BQ_ARTICLES.replace(".", "/datasets/", 1).replace(".", "/tables/", 1)
    reader = bq_reader()
    session = reader.create_read_session(
        parent=f"projects/{BQ_PROJECT}",
        read_session=types.ReadSession(
            table=f"projects/{table}",
            data_format=types.DataFormat.ARROW,
            read_options=types.ReadSession.TableReadOptions(
                selected_fields=[c.strip() for c in BQ_COLUMNS.split(",")], row_restriction=restriction
            ),
        ),
        max_stream_count=8,
    )
    # Oturum açmak ücretsizdir; okuma başlamadan sınırlar denetlenir
    scanned = session.estimated_total_bytes_scanned
    if scanned > BQ_MAX_GB * 1024 ** 3:
        raise RuntimeError(
            f"Bu arama yaklaşık {scanned / 1024 ** 3:.1f} GB okuyacak; panel sınırı {BQ_MAX_GB} GB. "
            "Tarih aralığını daraltın."
        )
    used = bq_used_today()
    if used + scanned > BQ_DAILY_MAX_GB * 1024 ** 3:
        raise RuntimeError(
            f"Panelin günlük okuma sınırı ({BQ_DAILY_MAX_GB} GB) doldu: bugün {used / 1024 ** 3:.1f} GB okundu, "
            f"bu arama {scanned / 1024 ** 3:.1f} GB okuyacak. Yarın (UTC gece yarısından sonra) tekrar deneyin."
        )
    day = datetime.now(timezone.utc).date()
    bq_usage()[day] = bq_usage().get(day, 0) + scanned

    lock, count, too_many = threading.Lock(), [0], threading.Event()

    def read_stream(name):
        pages = []
        for page in reader.read_rows(name).rows(session).pages:
            if too_many.is_set():
                break
            batch = page.to_arrow()
            pages.append(batch)
            with lock:
                count[0] += batch.num_rows
                if count[0] > BQ_MAX_ROWS:
                    too_many.set()
        return pages

    with ThreadPoolExecutor(max(1, len(session.streams))) as pool:
        parts = [t for pages in pool.map(read_stream, [x.name for x in session.streams]) for t in pages]
    if too_many.is_set():
        raise RuntimeError(
            f"Arama çok genel: {fmt_int(BQ_MAX_ROWS)}'den fazla haber eşleşti. "
            "Daha belirgin bir ifade veya daha kısa bir tarih aralığı girin."
        )
    columns = [c.strip() for c in BQ_COLUMNS.split(",")]
    if parts:
        # Arrow tamponları dönüştürülürken bırakılır; tepe bellek yaklaşık yarıya iner
        table = pa.Table.from_batches(parts)
        del parts
        df = table.to_pandas(self_destruct=True, split_blocks=True)
        del table
    else:
        df = pd.DataFrame(columns=columns)
    if not df.empty:
        df = df.sort_values("pub_timestamp", ascending=False).reset_index(drop=True)
    return df, scanned


def archive_days() -> list:
    """Yerel arşivdeki günler (dosya adlarından; dosya okumaz). Arşiv yoksa boş liste."""
    if not os.path.isdir(ARCHIVE_ARTICLES):
        return []
    days = []
    for name in os.listdir(ARCHIVE_ARTICLES):
        if name.endswith(".parquet"):
            try:
                days.append(datetime.strptime(name[:-8], "%Y-%m-%d").date())
            except ValueError:
                pass
    return sorted(days)


# Veri tazeliği şeridi (N3). Günlük yükleme 08:15 UTC'de dünü yükler; bu yüzden 1 gün gecikme normaldir.
# Yerel arşiv elle güncellendiği için tolerans birkaç gündür.
FRESH_OK_DAYS, FRESH_WARN_DAYS = 2, 6


def fresh_state(behind: int | None) -> str:
    """Gecikmeye göre durum: guncel / gecikme / eski / bilinmiyor."""
    if behind is None:
        return "bilinmiyor"
    if behind <= FRESH_OK_DAYS:
        return "guncel"
    return "gecikme" if behind <= FRESH_WARN_DAYS else "eski"


def extra_last_run() -> datetime | None:
    """Arşiv dışı toplamanın son çalışma zamanı: içinde bulunulan (yoksa önceki) ayın dosyalarının en yeni
    değişme zamanı. Yalnızca birkaç dosyanın zaman damgasına bakar, dosya okumaz."""
    if not os.path.isdir(EXTRA_DIR):
        return None
    now = datetime.now()
    for month in [f"{now:%Y-%m}", f"{now.replace(day=1) - timedelta(days=1):%Y-%m}"]:
        times = [os.path.getmtime(p) for p in glob.glob(os.path.join(EXTRA_DIR, "*", f"{month}.parquet"))]
        if times:
            return datetime.fromtimestamp(max(times))
    return None


def render_freshness(slot, is_local: bool, local_days: list) -> None:
    """Başlığın altındaki tek satırlık veri tazeliği şeridi: hangi veri ne kadar eski.
    Bulut arşivinde `bq_coverage` (ücretsiz, 1 saat önbellekli) okunur; yerelde yalnızca dosya adları."""
    today = date.today()
    parts, states = [], []

    last = local_days[-1] if (is_local and local_days) else None
    if not is_local:
        try:
            last = bq_coverage()[1]
        except Exception:
            last = None
    behind = (today - last).days if last else None
    states.append(fresh_state(behind))
    gecikme = "bugün" if behind == 0 else ("dün" if behind == 1 else f"{behind} gün önce")
    parts.append(("Haberler", f"{last:%d.%m.%Y} · {gecikme}" if last else "bilinmiyor"))

    run = extra_last_run()
    if run:
        saat = (datetime.now() - run).total_seconds() / 3600
        states.append("guncel" if saat <= 12 else ("gecikme" if saat <= 48 else "eski"))
        parts.append(("Arşiv dışı", f"{saat:.0f} sa önce" if saat < 48 else f"{saat / 24:.0f} gün önce"))

    durum = {"eski": ("Veri eski", MK["red"]), "bilinmiyor": ("Durum bilinmiyor", MK["red"]),
             "gecikme": ("Gecikme var", MK["amber_text"])}.get(
        next((x for x in ["bilinmiyor", "eski", "gecikme"] if x in states), ""), ("Güncel", MK["green"]))
    parts.append((None, f"● {durum[0]}"))
    body = '<span class="sep">|</span>'.join(
        '<span class="part">'
        + (f'{html.escape(label)} ' if label else "")
        + (f'<b{"" if label else f" style=color:{durum[1]}"}>{html.escape(value)}</b>')
        + '</span>'
        for label, value in parts
    )
    slot.markdown(f'<div class="mk-fresh">{body}</div>', unsafe_allow_html=True)


@st.cache_data(ttl=6 * 3600, show_spinner=False)
def ozet_oku() -> pd.DataFrame:
    """Günlük özet katmanının son OZET_GUN günü, yalnızca "tüm yayınlar" satırları.
    Yerelde Parquet'ten, yayında Storage Read API ile (sorgu kotasına sayılmaz). Yoksa boş tablo."""
    bas = date.today() - timedelta(days=OZET_GUN)
    kolonlar = ["gun", "boyut_tur", "boyut", "yayin", "haber", "pay", "ort_ton"]
    if os.path.exists(OZET_INDEX):
        df = pd.read_parquet(OZET_INDEX, columns=kolonlar,
                             filters=[("gun", ">=", bas), ("yayin", "==", "")])
        return df[df["boyut_tur"].isin(["toplam", "cerceve"])]
    try:
        import pyarrow as pa
        from google.cloud.bigquery_storage import types

        table = BQ_OZET.replace(".", "/datasets/", 1).replace(".", "/tables/", 1)
        reader = bq_reader()
        session = reader.create_read_session(
            parent=f"projects/{BQ_PROJECT}",
            read_session=types.ReadSession(
                table=f"projects/{table}", data_format=types.DataFormat.ARROW,
                read_options=types.ReadSession.TableReadOptions(
                    selected_fields=kolonlar,
                    row_restriction=(f"gun >= DATE '{bas:%Y-%m-%d}' AND yayin = '' "
                                     "AND (boyut_tur = 'toplam' OR boyut_tur = 'cerceve')"),
                ),
            ),
            max_stream_count=4,
        )
        parts = [p.to_arrow() for st_ in session.streams for p in reader.read_rows(st_.name).rows(session).pages]
        return pa.Table.from_batches(parts).to_pandas() if parts else pd.DataFrame(columns=kolonlar)
    except Exception:
        return pd.DataFrame(columns=kolonlar)


@st.cache_data(ttl=6 * 3600, show_spinner=False)
def sapma_tablosu() -> tuple:
    """(hafta etiketi, başlangıç, bitiş, bütün çerçeveler, sapmalar). Özet katmanı yoksa None."""
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "pipeline"))
    import sapma as sapma_mod

    ham = ozet_oku()
    if ham.empty:
        return None
    df = sapma_mod.hafta_ekle(ham)
    tam = sapma_mod.tam_haftalar(df)
    if not len(tam):
        return None
    hf = sapma_mod.haftalik_cerceveler(df)
    hf = hf[hf["hafta"].isin(set(tam))]
    hafta = tam[-1]
    tablo = sapma_mod.hafta_tablosu(hf, hafta)
    return hafta, hafta.start_time.date(), hafta.end_time.date(), tablo, sapma_mod.sapmalar(tablo)


def render_sapmalar() -> None:
    """"Haftanın gündem sapmaları": bir çerçevenin payı kendi son 52 haftasına göre olağandışı mı?
    Özet katmanından (pipeline/ozet.py) okunur; arama gerektirmez."""
    try:
        veri = sapma_tablosu()
    except Exception:
        return
    if not veri:
        return
    _, bas, bit, tablo, sapmalar = veri
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "pipeline"))
    import sapma as sapma_mod

    st.markdown('<div class="mk-kicker" style="margin-top:1.4rem">Haftanın gündem sapmaları</div>',
                unsafe_allow_html=True)
    st.caption(f"{bas:%d.%m.%Y} – {bit:%d.%m.%Y} · Bir çerçevenin bu haftaki payı, **aynı çerçevenin önceki "
               f"{sapma_mod.TABAN_HAFTA} haftasına** göre hangi yüzdelik dilimde? Taban tüm geçmiş değil kayan "
               "bir penceredir; yoksa yıllar içinde yükselen bir çerçeve her hafta sapma gibi görünür.")
    if sapmalar:
        for x in sapmalar[:4]:
            yon = "olağandışı yüksek" if x["yuzdelik"] >= sapma_mod.PCT_YUKSEK else "olağandışı düşük"
            renk = MK["amber_text"] if x["yuzdelik"] >= sapma_mod.PCT_YUKSEK else MK["cyan"]
            fark = "" if x["fark_puan"] is None else f' · geçen haftaya göre {x["fark_puan"]:+.2f} puan'
            st.markdown(
                f'<div class="mk-rank"><span>{html.escape(x["cerceve"])} — '
                f'<b>%{x["pay"] * 100:.2f}</b>{fark}</span>'
                f'<b style="color:{renk}">{x["yuzdelik"]:.0f}. yüzdelik · {yon}</b></div>',
                unsafe_allow_html=True)
    else:
        st.markdown('<div class="mk-rank"><span>Bu hafta eşiği geçen bir sapma yok: '
                    'tüm çerçeveler kendi olağan aralıklarında.</span></div>', unsafe_allow_html=True)
    with st.expander("Bütün çerçeveler", expanded=False, icon=":material/table_rows:"):
        gorunum = pd.DataFrame([{
            "Çerçeve": x["cerceve"], "Haber": x["haber"], "Pay %": round(x["pay"] * 100, 2),
            "Geçen haftaya fark (puan)": None if x["fark_puan"] is None else round(x["fark_puan"], 2),
            "Tabana göre yüzdelik": None if x["yuzdelik"] is None else round(x["yuzdelik"]),
            "Ortalama ton": round(x["ort_ton"], 1),
        } for x in tablo])
        st.dataframe(gorunum, width="stretch", hide_index=True)
        st.markdown(
            f'<div class="mk-note"><b>Veri notu.</b> Bir haber birden fazla çerçeveye girebilir; paylar '
            f'toplamı %100\'ü aşar. Haberlerin yarıdan fazlası hiçbir çerçeveye uymaz ve "Diğer"e düşer. '
            f'Haftalık haber sayısı {sapma_mod.MIN_HABER}\'in altındaki çerçeveler sapma sayılmaz: küçük '
            'tabanda küçük değişimler dev oranlar üretir. Ölçülen şey olayın kendisi değil, habere konu '
            'olma yoğunluğudur.</div>', unsafe_allow_html=True)


@st.cache_resource(ttl=24 * 3600, show_spinner=False)
def ad_matcher():
    """Ad sözlüğü eşleştiricisi (yerelde Parquet, yayında Storage Read). Sözlük yoksa None.
    Eşleştirme kuralı `pipeline/adlar.py`'dedir; sözlüğü kuran betik de aynı modülü kullanır."""
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "pipeline"))
    kolonlar = ["ad", "anahtar", "tur"]
    try:
        from adlar import Matcher

        if os.path.exists(SOZLUK_INDEX):
            return Matcher(pd.read_parquet(SOZLUK_INDEX, columns=kolonlar))
        import pyarrow as pa
        from google.cloud.bigquery_storage import types

        table = BQ_SOZLUK.replace(".", "/datasets/", 1).replace(".", "/tables/", 1)
        reader = bq_reader()
        session = reader.create_read_session(
            parent=f"projects/{BQ_PROJECT}",
            read_session=types.ReadSession(
                table=f"projects/{table}", data_format=types.DataFormat.ARROW,
                read_options=types.ReadSession.TableReadOptions(selected_fields=kolonlar),
            ),
            max_stream_count=1,
        )
        parts = [p.to_arrow() for st_ in session.streams for p in reader.read_rows(st_.name).rows(session).pages]
        if not parts:
            return None
        return Matcher(pa.Table.from_batches(parts).to_pandas())
    except Exception:
        return None


@st.cache_data(max_entries=CACHE_MAX_ENTRIES, show_spinner=False)
def baslik_adlari(titles_key: str, _titles: tuple) -> pd.DataFrame:
    """Arama sonucunun başlıklarında sözlükteki adları sayar. (ad, tur, haber) tablosu döner.
    GDELT'in kişi/kurum alanlarından bağımsızdır: kaynak doğrudan Türkçe başlıktır."""
    m = ad_matcher()
    if m is None or not _titles:
        return pd.DataFrame(columns=["ad", "tur", "haber"])
    sayac = {}
    for t in _titles:
        for ad, tur in m.find(fold_tr(t)):
            sayac[(ad, tur)] = sayac.get((ad, tur), 0) + 1
    if not sayac:
        return pd.DataFrame(columns=["ad", "tur", "haber"])
    out = pd.DataFrame([(a, t, n) for (a, t), n in sayac.items()], columns=["ad", "tur", "haber"])
    return out.sort_values("haber", ascending=False).reset_index(drop=True)


@st.cache_data(ttl=6 * 3600, show_spinner=False)
def dunya_turk_oku() -> pd.DataFrame:
    """E3: Türkiye'de geçen olaylar ve dünya/Türkçe anılma özeti (gdelt_tr.events_tu).
    Yalnızca eşiği geçen olaylar okunur. Storage Read API; sorgu kotasına sayılmaz."""
    alanlar = ["event_day", "EventRootCode", "Actor1Name", "Actor2Name", "ActionGeo_FullName",
               "toplam_anilma", "toplam_kaynak", "turkce_anilma", "turkce_kaynak"]
    try:
        return bq_table_read("events_tu", alanlar, f"toplam_kaynak >= {EVENTS_TU_MIN_KAYNAK}")
    except Exception:
        return pd.DataFrame(columns=alanlar)


def render_dunya_turk() -> None:
    """"Dünya ve Türk medyası" (E3): Türkiye'de geçen bir olayı dünya medyası yazmış, Türk medyası
    yazmamış olabilir. Arama gerektirmez; tablo yoksa bölüm hiç çıkmaz."""
    try:
        df = dunya_turk_oku()
    except Exception:
        return
    if df.empty:
        return
    yok = df[df["turkce_anilma"] == 0]
    pay = len(yok) / len(df) * 100
    ilk, son = df["event_day"].min(), df["event_day"].max()

    st.markdown('<div class="mk-kicker" style="margin-top:1.4rem">Dünya ve Türk medyası</div>',
                unsafe_allow_html=True)
    st.caption(
        f"{as_date(ilk):%d.%m.%Y} – {as_date(son):%d.%m.%Y} · **Türkiye'de geçen** olaylar (GDELT'in yer "
        f"kodlaması) ve bunları kimin yazdığı. Yalnızca en az {EVENTS_TU_MIN_KAYNAK} farklı kaynağın "
        "yazdığı olaylar sayılır: eşiksiz bakıldığında olayların dörtte üçü tek anılmalıdır ve oran "
        "gürültüye boğulur."
    )
    k1, k2, k3 = st.columns(3)
    k1.metric("Olay", fmt_int(len(df)))
    # delta_color="off": bu bir değişim değil, pay; ok ve renk yanıltıcı olurdu
    k2.metric("Türkçe haberi olmayan", fmt_int(len(yok)), f"%{pay:.1f}", delta_color="off")
    k3.metric("Dünya anılması", fmt_int(int(df["toplam_anilma"].sum())))

    st.markdown('<div class="mk-card-title">Dünyanın en çok yazdığı, arşivimizde Türkçe haberi '
                'bulunmayan olaylar</div>', unsafe_allow_html=True)
    # GDELT karşılıklı aktör çiftlerini iki ayrı olay olarak kaydeder (A→B ve B→A); listede tekrar
    # etmesinler diye aktör çifti sıralanıp tekilleştirilir
    ust = yok.assign(_cift=[
        "|".join(sorted(str(x) for x in (a, b) if str(x).strip().lower() not in BOS_AKTOR))
        for a, b in zip(yok["Actor1Name"], yok["Actor2Name"])
    ])
    ust = (ust.sort_values("toplam_kaynak", ascending=False)
              .drop_duplicates(["event_day", "EventRootCode", "_cift"]).head(10))
    for _, r in ust.iterrows():
        kod = CAMEO_ROOTS.get(str(r["EventRootCode"]), str(r["EventRootCode"]))
        aktor = " → ".join(str(x) for x in (r["Actor1Name"], r["Actor2Name"])
                           if str(x).strip().lower() not in BOS_AKTOR)
        yer = "" if str(r["ActionGeo_FullName"]).strip().lower() in BOS_AKTOR else str(r["ActionGeo_FullName"])
        st.markdown(
            f'<div class="mk-rank"><span>{as_date(r["event_day"]):%d.%m.%Y} · '
            f'<b>{html.escape(kod)}</b>{" · " + html.escape(aktor) if aktor else ""}'
            f'{" · " + html.escape(yer) if yer else ""}</span>'
            f'<b>{fmt_int(int(r["toplam_kaynak"]))} kaynak</b></div>', unsafe_allow_html=True)

    with st.expander("Olay türüne göre", expanded=False, icon=":material/category:"):
        g = df.assign(tur=df["EventRootCode"].astype(str).map(lambda k: CAMEO_ROOTS.get(k, k)))
        ozet = (g.groupby("tur").agg(olay=("tur", "size"),
                                     turkce_yok=("turkce_anilma", lambda x: int((x == 0).sum())))
                .reset_index())
        ozet["Türkçe haberi yok %"] = (ozet["turkce_yok"] / ozet["olay"] * 100).round(1)
        ozet = ozet[ozet["olay"] >= 50].sort_values("Türkçe haberi yok %", ascending=False)
        st.dataframe(ozet.rename(columns={"tur": "Olay türü", "olay": "Olay", "turkce_yok": "Türkçe haberi yok"}),
                     width="stretch", hide_index=True)
        st.markdown(
            '<div class="mk-note"><b>Veri notu.</b> "Türkçe haberi yok", olayın anılmaları arasında '
            '<b>bizim arşivimizde bulunan</b> bir haber olmadığı anlamına gelir — Türk medyasının hiç '
            'yazmadığı anlamına gelmez. Arşiv, GDELT\'in taradığı Türkçe kaynaklarla sınırlı bir '
            'örneklemdir. Ayrıca olayın "Türkiye\'de geçmiş" sayılması GDELT\'in yer kodlamasına dayanır '
            've bu kodlama hata yapabilir. Rakamlar gösterge olarak okunmalıdır.</div>',
            unsafe_allow_html=True)


def render_arsive_sor() -> None:
    """"Arşive sor" (modül H): serbest soru → ajan araçları → kaynaklı cevap.
    MCP'de kaybedilen kanıt zinciri burada korunur: hangi aracın ne döndürdüğü ekranda gösterilir."""
    if not llm_hazir():
        return
    st.markdown('<div class="mk-kicker" style="margin-top:1.4rem">Arşive sor</div>', unsafe_allow_html=True)
    st.caption(
        "Bir soru yazın; yapay zekâ **yalnızca arşiv araçlarını** kullanarak cevaplar. Başka bir kaynağa "
        "bakmaz, neden-sonuç kurmaz. Hangi aracı hangi argümanla çağırdığını ve ne döndürdüğünü "
        "cevabın altında görebilirsiniz."
    )
    st.markdown(
        '<div class="mk-note"><b>Örnek sorular.</b> "Geçen hafta hangi çerçeve olağandışıydı?" · '
        '"Sözcü hangi konuları ortalamanın üstünde yazıyor?" · "Türk medyası göçmenleri nasıl işliyor?" · '
        '"Iğdır hakkında ne yazıldı?"</div>', unsafe_allow_html=True)
    soru = st.text_input("Soru", key="sor_soru", label_visibility="collapsed",
                         placeholder="Sorunuzu yazın, sonra Sor'a basın")
    # Tek tetikleyici düğmedir. Hazır soru çipleri kaldırıldı: çip, widget oluşturulduktan sonra
    # st.session_state["sor_soru"]'yu değiştirmeye çalışıyordu ve Streamlit buna izin vermiyor
    # (StreamlitWidgetAlreadyInstantiatedError).
    if st.button("Sor", key="sor_git", type="primary", icon=":material/search:"):
        if not soru or not soru.strip():
            st.info("Önce bir soru yazın.")
        else:
            from llm import butce, sorgu

            with st.spinner("Arşiv sorgulanıyor..."):
                try:
                    st.session_state["sor_cevap"] = (soru, sorgu.sor(soru))
                except butce.ButceAsimi as exc:
                    st.session_state.pop("sor_cevap", None)
                    st.error(f"Bütçe tavanı: {exc}")
                except Exception as exc:
                    st.session_state.pop("sor_cevap", None)
                    st.error(f"Cevap üretilemedi: {type(exc).__name__}: {exc}")
    kayit = st.session_state.get("sor_cevap")
    if kayit:
        sorulan, r = kayit
        st.markdown(f'<div class="mk-ai"><p><span class="tag veri">soru</span>{html.escape(sorulan)}</p>'
                    + "".join(f"<p>{html.escape(p_, quote=False)}</p>"
                              for p_ in r["cevap"].split("\n") if p_.strip())
                    + "</div>", unsafe_allow_html=True)
        with st.expander(f"Hangi araçlar kullanıldı ({len(r['adimlar'])} çağrı)",
                         icon=":material/fact_check:"):
            for a in r["adimlar"]:
                st.markdown(f"**`{a['arac']}`** · argüman: `{a['girdi']}`")
                st.json(a["cikti"], expanded=False)
        st.caption(f"{r['model']} · {r['tur']} tur · maliyet {r['maliyet']:.4f} $. "
                   "Cevaptaki her rakam yukarıdaki araç çıktılarındadır; model başka bir kaynağa bakmaz.")


# Parolalı giriş (yayındaki panel). Parolanın KENDİSİ hiçbir yerde saklanmaz; yalnızca SHA-256 özeti
# ortam değişkeninde durur ve Streamlit sunucu tarafında çalıştığı için özet tarayıcıya hiç gitmez.
# Değişken tanımlı değilse kapı hiç çıkmaz (yerel kullanım).
PAROLA_OZETI = os.environ.get("PANEL_PAROLA_OZETI", "").strip().lower()
# Bir oturumda bu kadar yanlış denemeden sonra her deneme yavaşlatılır (kaba kuvvete karşı)
PAROLA_YAVASLATMA = 3


def parola_kapisi() -> bool:
    """Parola tanımlıysa giriş ekranını gösterir ve doğru parola girilene kadar False döner.
    Tanımlı değilse (yerel kullanım) her zaman True."""
    if not PAROLA_OZETI:
        return True
    if st.session_state.get("girildi"):
        return True

    # Marka başlığı bu fonksiyondan ÖNCE çiziliyor; burada tekrarlanmaz (iki kez görünürdü)
    st.markdown('<div class="mk-note">Panel şu anda yalnızca davetli kullanıcılara açıktır. '
                'Erişim parolanızı girin.</div>', unsafe_allow_html=True)
    girdi = st.text_input("Erişim parolası", type="password", key="parola_girdi")
    if st.button("Giriş", type="primary", key="parola_git"):
        import hashlib
        import hmac
        import time as _t

        deneme = int(st.session_state.get("parola_deneme", 0))
        if deneme >= PAROLA_YAVASLATMA:
            _t.sleep(min(2 ** (deneme - PAROLA_YAVASLATMA + 1), 8))
        # compare_digest: sabit zamanlı karşılaştırma, zamanlama saldırısına kapalı
        if hmac.compare_digest(hashlib.sha256((girdi or "").encode()).hexdigest(), PAROLA_OZETI):
            st.session_state["girildi"] = True
            st.session_state.pop("parola_deneme", None)
            st.rerun()
        else:
            st.session_state["parola_deneme"] = deneme + 1
            st.error("Parola yanlış.")
    st.caption(GDELT_ATTRIBUTION)
    return False


# Yasal metinler (yasal/). Taslakta kalan doldurulacak alanlar bu işaretle gösterilir; işaret duran bir
# metin panelde GÖSTERİLMEZ (yarım bir hukuki metni yayımlamak yayımlamamaktan kötüdür).
YASAL_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "yasal")
YASAL_TASLAK_ISARETI = "[["
YASAL_METINLER = [("Kullanım koşulları", "kullanim-kosullari.md"),
                  ("Gizlilik politikası", "gizlilik-politikasi.md"),
                  ("KVKK aydınlatma metni", "kvkk-aydinlatma.md")]


@st.cache_data(ttl=3600, show_spinner=False)
def yasal_hazir() -> list:
    """Yayımlanmaya hazır yasal metinler: [(başlık, içerik)]. Doldurulmamış alan kalmışsa boş liste."""
    out = []
    for baslik, dosya in YASAL_METINLER:
        yol = os.path.join(YASAL_DIR, dosya)
        if not os.path.exists(yol):
            return []
        icerik = open(yol, encoding="utf-8").read()
        if YASAL_TASLAK_ISARETI in icerik:
            return []
        out.append((baslik, icerik))
    return out


def render_yasal() -> None:
    """Hazırsa üç yasal metni sekmeli bir açılır kutuda gösterir."""
    metinler = yasal_hazir()
    if not metinler:
        return
    with st.expander("Kullanım koşulları, gizlilik ve KVKK", expanded=False, icon=":material/gavel:"):
        for sekme, (_, icerik) in zip(st.tabs([b for b, _ in metinler]), metinler):
            with sekme:
                st.markdown(icerik)


@st.cache_resource(show_spinner=False)
def llm_hazir() -> bool:
    """Yapay zekâ özeti yalnızca anahtar VE anthropic paketi varsa sunulur. Yayındaki panelde anahtar
    bilinçli olarak yoktur (herkese açık panelde her ziyaretçi harcama yapabilirdi); düğme orada çıkmaz."""
    try:
        import anthropic  # noqa: F401

        from llm import butce
        return bool(butce.anahtar())
    except Exception:
        return False


def render_anlati(sonuc: dict) -> None:
    """Anlatı paragraflarını kanıt etiketleriyle çizer (L3 şeması: veri / yorum)."""
    parcalar = []
    for par in sonuc.get("paragraflar", []):
        tur = par.get("tur", "yorum")
        ad = "veri" if tur == "veri" else "yorum"
        # quote=False: metin öğe içeriğine giriyor, öznitelik değil; kesme işaretleri &#x27; olmasın
        parcalar.append(f'<p><span class="tag {ad}">{ad}</span>'
                        f'{html.escape(par["metin"], quote=False)}</p>')
    if not parcalar:
        st.warning("Özet üretilemedi; model beklenen biçimde yanıt vermedi. Tekrar deneyebilirsiniz.")
        return
    st.markdown(f'<div class="mk-ai">{"".join(parcalar)}</div>', unsafe_allow_html=True)
    if sonuc.get("kesildi"):
        st.warning("Özet uzunluk sınırına takıldı ve son paragraf eksik olabilir.")
    st.caption(
        f"Bu özeti {sonuc['model']} yazdı; **yalnızca yukarıdaki kartın rakamlarını** kullanır, başka bir "
        "kaynağa bakmaz ve neden-sonuç kurmaz. **veri** etiketli paragraflar karttaki rakamların aktarımıdır; "
        "**yorum** etiketli paragraflar bu rakamlar arasında bağ kurar. "
        f"Maliyet: {sonuc['maliyet']:.4f} $ · {sonuc['girdi_token']:,}→{sonuc['cikti_token']:,} token."
    )


def archive_update_section(local_hi) -> None:
    """Yerel arşiv modunda: arşivin BigQuery'nin kaç gün gerisinde olduğunu gösterir ve
    pipeline/export.py'yi düğmeyle çalıştırır (yalnızca eksik veya değişmiş günleri indirir)."""
    try:
        bq_hi = bq_coverage()[1]
    except Exception:
        bq_hi = None
    behind = (bq_hi - local_hi).days if bq_hi else None
    title = "Yerel arşivi güncelle" + (f" — {behind} gün geride" if behind else "")
    with st.expander(title, expanded=bool(behind), icon=":material/sync:"):
        st.markdown(
            "Her gün 11:15'te çalışan zamanlanmış iş yalnızca **bulut arşivini** (BigQuery; yayındaki panel buradan okur) günceller. "
            "Bu bilgisayardaki arşiv onun bir kopyasıdır; Google Cloud bilgisayarınıza kendiliğinden dosya "
            "gönderemediği için yeni günleri buradan indirmeniz gerekir.\n\n"
            "- İndirme yalnızca eksik ve bulut arşivinde yeniden yüklenmiş günleri alır. Bir günlük güncelleme "
            "~50–80 MB ve birkaç saniye sürer, maliyeti ayda birkaç senttir.\n"
            "- En iyi zaman her gün 12:00'den sonradır; dünün verisi o saatte bulut arşivinde olur.\n"
            "- Aynı işlem terminalden de yapılabilir: `venv/bin/python pipeline/export.py`"
        )
        if bq_hi is None:
            st.warning(
                "Bulut arşivine (BigQuery) bağlanılamadı; arşivin güncel olup olmadığı bilinmiyor. Google oturumu dolmuş olabilir: "
                f"`gcloud auth application-default login --project {BQ_PROJECT}`"
            )
        elif behind <= 0:
            st.success(f"Arşiv güncel: bulut arşivindeki son gün ({bq_hi:%d.%m.%Y}) bu bilgisayarda da var.")
        else:
            st.info(
                f"Bulut arşivindeki son gün: {bq_hi:%d.%m.%Y}. Bu bilgisayardaki arşivin son günü: {local_hi:%d.%m.%Y} "
                f"({behind} gün geride)."
            )
        if st.button("Yerel arşivi güncelle", key="archive_update"):
            run_archive_update()


def run_archive_update() -> None:
    """export.py'yi (GDELT arşivi), ardından extra_collect.py guncel'i (arşiv dışı yayınların son künyeleri) ayrı
    süreçlerde çalıştırır ve çıktıyı canlı gösterir; bitince önbelleği temizleyip sayfayı yeniler. Ek toplamanın
    hatası güncellemeyi durdurmaz (uyarı verilir)."""
    import subprocess
    import sys

    root = os.path.dirname(os.path.abspath(__file__))
    commands = [("GDELT arşivi", [os.path.join(root, "pipeline", "export.py")], True),
                ("Arşiv dışı kaynaklar", [os.path.join(root, "pipeline", "extra_collect.py"), "guncel"], False)]
    lines = []
    with st.status("Yerel arşiv güncelleniyor...", expanded=True) as status:
        box = st.empty()
        for label, args, required in commands:
            lines.append(f"── {label}")
            proc = subprocess.Popen([sys.executable, "-u", *args], cwd=root,
                                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            for line in proc.stdout:
                if "warn" in line.lower():
                    continue
                lines.append(line.rstrip())
                box.code("\n".join(lines[-15:]) or "...")
            proc.wait()
            if proc.returncode != 0 and required:
                status.update(label="Güncelleme başarısız oldu", state="error")
                st.error(
                    "Çıktıdaki hatayı kontrol edin. 'invalid_grant' veya 'credentials' geçiyorsa Google oturumunu "
                    f"yenileyin: `gcloud auth application-default login --project {BQ_PROJECT}`"
                )
                return
            if proc.returncode != 0:
                st.warning(f"{label} güncellenemedi; GDELT arşivi güncel. Çıktıyı kontrol edin.")
        status.update(label="Yerel arşiv güncellendi", state="complete")
    local_fetch.clear()
    source_presence.clear()
    extra_fetch.clear()
    st.rerun()


@st.cache_data(ttl=600, max_entries=CACHE_BIG_ENTRIES, show_spinner=False)
def local_fetch(term: str, start, end) -> pd.DataFrame:
    """BigQuery aramasının aynısını yerel Parquet arşivinde DuckDB ile yapar (ücretsiz).
    Yalnızca aralıktaki günlerin dosyaları okunur."""
    import duckdb

    files = [
        os.path.join(ARCHIVE_ARTICLES, f"{d:%Y-%m-%d}.parquet") for d in archive_days() if start <= d <= end
    ]
    if not files:
        return pd.DataFrame(columns=[c.strip() for c in BQ_COLUMNS.split(",")])
    pat = topic_patterns(term)
    sql = f"""
        SELECT {BQ_COLUMNS}
        FROM read_parquet(?)
        WHERE regexp_matches(DocumentIdentifier, ?)
           OR regexp_matches(PageTitle, ?)
           OR regexp_matches(V2Persons, ?)
        ORDER BY pub_timestamp DESC
        LIMIT {BQ_MAX_ROWS}"""
    with duckdb.connect() as con:
        return con.execute(sql, [files, pat["url_re"], pat["title_re"], pat["person_re"]]).df()


@st.cache_data(ttl=CACHE_BIG_TTL, max_entries=CACHE_BIG_ENTRIES, show_spinner=False)
def preprocess(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()

    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        hint = ""
        if "Theme" in df.columns and "Count" in df.columns:
            hint = (
                " Bu dosya bir tema özeti; haber satırları içermediği için yeniden analiz edilemez. "
                "Panelin altındaki 'Filtrelenmiş Veriyi CSV Olarak İndir' ile alınan dosyayı yükleyin."
            )
        raise ValueError(f"Eksik kolon(lar): {', '.join(missing)}.{hint}")
    for col in OPTIONAL_COLUMNS:
        if col not in df.columns:
            df[col] = ""

    # BigQuery zaman damgası UTC gelir ("2024-12-29 20:30:00.000000 UTC"); Türkiye saatine çevrilir
    ts = pd.to_datetime(df["pub_timestamp"], errors="coerce", utc=True)
    df["pub_timestamp"] = ts.dt.tz_convert(LOCAL_TZ).dt.tz_localize(None)
    df = df.dropna(subset=["pub_timestamp"])

    # Birden fazla dosya (veya URL'sine HTML kuyruğu eklenmiş kayıt) aynı haberi içerebilir:
    # URL temizlenir, URL başına en erken kayıt tutulur
    df["DocumentIdentifier"] = (
        df["DocumentIdentifier"].fillna("").astype(str).str.replace(URL_ATTR_JUNK, "", regex=True).str.strip()
    )
    df = df.sort_values("pub_timestamp").drop_duplicates("DocumentIdentifier", keep="first")

    df["SourceCommonName"] = df["SourceCommonName"].fillna("Bilinmiyor")
    for col in ["Themes", "V2Persons", "Organizations", "V2Tone", "PageTitle", "V2Locations", "TranslationInfo"]:
        df[col] = df[col].fillna("").astype(str).str.strip()

    # Başlık: HTML varlıklarını çöz ("I&#x11F;d&#x131;r" -> "Iğdır"), site adını ayıkla
    df["PageTitle"] = df["PageTitle"].map(clean_title)
    df["TitleCore"] = df["PageTitle"].map(title_core)


    # Kaynak dili: "srclc:tur;eng:GT-TUR 1.0" -> tur; boşsa haber zaten İngilizcedir
    df["Language"] = df["TranslationInfo"].str.extract(r"srclc:(\w+)", expand=False).fillna("eng")

    # V2Tone -> 7 sayısal alan
    tone = df["V2Tone"].str.split(",", expand=True).reindex(columns=range(len(TONE_FIELDS)))
    tone.columns = TONE_FIELDS
    df[TONE_FIELDS] = tone.apply(pd.to_numeric, errors="coerce")

    df["date_only"] = df["pub_timestamp"].dt.date
    df["year_month"] = df["pub_timestamp"].dt.to_period("M").astype(str)

    return df.reset_index(drop=True)


def build_search_bundle(key: tuple, raw: pd.DataFrame, estimate) -> dict:
    """Bir aramanın (veya CSV yüklemesinin) analiz tabloları; oturumda search_bundle olarak tutulur.
    raw_len: ham satır sayısı (tekilleştirme öncesi), estimate: bulutta okunan bayt (yerel ve CSV'de None)."""
    bundle = {"key": key, "raw_len": len(raw), "estimate": estimate}
    if raw.empty and key[0] != "csv":
        return bundle  # arama sonuç vermedi; çağıran uyarı gösterip durur
    df = preprocess(raw)
    del raw
    themes = explode_list_column(df[["Themes"]], "Themes")
    family_map = {t: get_theme_family(t) for t in themes["Themes"].unique()}
    themes["Family"] = themes["Themes"].map(family_map)
    bundle.update(
        df=df, themes=themes,
        persons=explode_list_column(df[["V2Persons"]], "V2Persons"),
        orgs=explode_list_column(df[["Organizations"]], "Organizations"),
        locations=explode_locations(df[["V2Locations"]]),
    )
    return bundle


def classify_sentiment(tone: pd.Series, threshold: float) -> pd.Series:
    """AvgTone skorunu ±eşik değere göre Pozitif / Nötr / Negatif kategorisine ayırır."""
    labels = np.select(
        [tone.isna(), tone > threshold, tone < -threshold],
        ["Bilinmiyor", "Pozitif", "Negatif"],
        default="Nötr",
    )
    return pd.Series(labels, index=tone.index)


# Arama başına dört kolon (tema, kişi, kurum, çerçeve) açılır; günün panosu da kullanır
@st.cache_data(ttl=CACHE_BIG_TTL, max_entries=CACHE_BIG_ENTRIES * 5, show_spinner=False)
def explode_list_column(df: pd.DataFrame, col: str) -> pd.DataFrame:
    """';' ile ayrılmış liste kolonunu haber başına tekil değerlerle satırlara açar.
    Dönen tablonun index'i orijinal haber satırının index'idir."""
    # V2 formatındaki ",karakter_konumu" eklerini at (örn. "Ad Soyad,15")
    values = df[col].str.replace(r",\d+(?=;|$)", "", regex=True)
    exploded = values.str.split(";").explode().str.strip()
    exploded = exploded[exploded.notna() & (exploded != "")]
    out = exploded.to_frame(col)
    return out[~out.reset_index().duplicated().to_numpy()]


def clean_title(title: str) -> str:
    if not title:
        return ""
    title = html.unescape(title).replace("\xa0", " ")
    # UTF-8 metnin cp1254/cp1252 sanılıp iki kez kodlanması ("rÃ¶vanÅŸÄ±" -> "rövanşı", "ğŸš¨" -> emoji)
    if re.search(r"[ÃÄÅÂğ][\x80-\xbf€‚ƒ„…†‡ˆ‰Š‹ŒŽ‘’“”•–—˜™š›œžŸ]", title):
        for codec in ("cp1254", "cp1252"):
            try:
                title = title.encode(codec).decode("utf-8")
                break
            except (UnicodeEncodeError, UnicodeDecodeError):
                continue
    if any(c in title for c in "ýÝðÐþÞ"):
        title = title.translate(MOJIBAKE_FIX)
    return re.sub(r"\s+", " ", title).strip()


def title_core(title: str) -> str:
    """'Başlık - Site Adı' / 'Site | Başlık' kalıplarında en uzun parçayı (asıl başlığı) döner."""
    if not title:
        return ""
    return max(re.split(r"\s+[|–-]\s+", title), key=len).strip()


def url_slug(url: str) -> str:
    path = re.sub(r"^https?://[^/]+", "", str(url))
    return re.sub(r"[-_/.,?=]+|\d+", " ", path).strip()


def fold_tr(text: str) -> str:
    """Türkçe karakterleri ASCII'ye indirip küçük harfe çevirir (eşleştirme için)."""
    return text.translate(TR_FOLD).lower()


def tr_lower(text: str) -> str:
    return text.replace("I", "ı").replace("İ", "i").lower()


def title_words(title: str) -> list:
    """Başlığın 3+ harfli kelimeleri; küçük harf, kesme işaretinden sonraki ekler atılmış
    ("İstanbul'un" -> "istanbul")."""
    text = re.sub(r"(?<=\w)['’]\w+", "", tr_lower(title))
    return [w for w in re.findall(r"[a-z0-9çğıöşüâîû]+", text) if len(w) > 2]


def word_stem(word: str) -> str:
    """Kaba kök: Türkçe karakterler sadeleşir, ilk 5 harf alınır ("soruşturmasında" -> "sorus")."""
    return fold_tr(word)[:5]


def title_signature(title: str, topic_stems: tuple = ()) -> frozenset:
    """Başlığı karşılaştırma için dolgu ve konu kelimeleri atılmış kök kümesine indirger."""
    return frozenset(
        word_stem(w) for w in title_words(title)
        if fold_tr(w) not in FOLDED_STOPWORDS and word_stem(w) not in topic_stems
    )


@st.cache_data(max_entries=CACHE_MAX_ENTRIES, show_spinner=False)
def detect_topic_words(titles: pd.Series) -> list:
    """Başlıkların en az TOPIC_WORD_MIN_SHARE oranında geçen kelimeleri (sıklığa göre) döner."""
    words = titles[titles != ""].map(title_words)
    if len(words) < TOPIC_WORD_MIN_TITLES:
        return []
    stems = words.map(lambda ws: {word_stem(w) for w in ws}).explode().dropna()
    share = stems.value_counts() / len(words)
    topic = share[share >= TOPIC_WORD_MIN_SHARE].index
    # Her kök için başlıklarda en sık görülen yazılışı göster
    surface = words.explode().dropna()
    surface = surface[surface.map(word_stem).isin(topic)]
    most_common = surface.groupby(surface.map(word_stem)).agg(lambda s: s.value_counts().index[0])
    return most_common.reindex(topic).tolist()


def parse_topic_stems(text: str) -> tuple:
    return tuple(sorted({word_stem(w) for w in title_words(text)}))


def wordcounts_close(a: float, b: float) -> bool:
    if not (np.isfinite(a) and np.isfinite(b)) or max(a, b) <= 0:
        return False
    return abs(a - b) / max(a, b) <= COPY_WORDCOUNT_TOLERANCE


@st.cache_data(ttl=CACHE_BIG_TTL, max_entries=CACHE_MAX_ENTRIES, show_spinner=False)
def detect_copy_groups(
    docs: pd.DataFrame, use_titles: bool, threshold: float, window_hours: int, topic_stems: tuple = ()
) -> pd.DataFrame:
    """Kopya haber gruplarını bulur. İki haber şu durumlarda aynı gruba girer:
      1) V2Tone'un 7 değeri birebir aynı (aynı metin), veya
      2) başlıkları benzer (Jaccard >= eşik), aralarında en fazla `window_hours` saat var
         ve kelime sayıları yakın.
    Bağlantılar geçişlidir (A~B, B~C ise A, B, C aynı grup). `docs` zamana göre sıralı olmalıdır;
    her grubun en erken haberi orijinal, diğerleri kopya sayılır."""
    n = len(docs)
    parent = np.arange(n)
    exact_edge = np.zeros(n, dtype=bool)
    title_edge = np.zeros(n, dtype=bool)

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(a: int, b: int, edge_flags: np.ndarray) -> None:
        edge_flags[a] = edge_flags[b] = True
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)

    wordcounts = docs["WordCount"].to_numpy(dtype=float)
    positions = pd.Series(np.arange(n), index=docs.index)

    eligible = docs[docs["WordCount"] >= MIN_WORDS_FOR_COPY]
    for group_index in eligible.groupby("V2Tone").groups.values():
        members = positions[group_index].to_numpy()
        for other in members[1:]:
            union(members[0], other, exact_edge)

    if use_titles:
        signatures = docs["TitleCore"].map(lambda t: title_signature(t, topic_stems)).to_numpy()
        hours = ((docs["pub_timestamp"] - docs["pub_timestamp"].min()).dt.total_seconds() / 3600).to_numpy()
        order = np.argsort(hours, kind="stable")
        for i, a in enumerate(order):
            if len(signatures[a]) < TITLE_MIN_TOKENS:
                continue
            for b in order[i + 1:]:
                if hours[b] - hours[a] > window_hours:
                    break
                if len(signatures[b]) < TITLE_MIN_TOKENS or not wordcounts_close(wordcounts[a], wordcounts[b]):
                    continue
                shared = len(signatures[a] & signatures[b])
                if shared and shared / len(signatures[a] | signatures[b]) >= threshold:
                    union(a, b, title_edge)

    roots = np.array([find(i) for i in range(n)])
    out = pd.DataFrame({"CopyGroup": roots}, index=docs.index)
    out["CopyGroupSize"] = out.groupby("CopyGroup")["CopyGroup"].transform("size")
    out["IsCopy"] = out.duplicated("CopyGroup", keep="first")
    group_exact = pd.Series(exact_edge).groupby(roots).transform("any").to_numpy()
    group_title = pd.Series(title_edge).groupby(roots).transform("any").to_numpy()
    out["CopyMethod"] = np.select(
        [group_exact & group_title, group_exact, group_title],
        ["Birebir metin + benzer başlık", "Birebir metin", "Benzer başlık"],
        default="",
    )
    return out


def frame_set_key(frames: dict) -> tuple:
    """Çerçeve sözlüğünün önbellek anahtarı olarak kullanılabilen (hashlenebilir) biçimi."""
    return tuple((name, tuple(keywords)) for name, keywords in frames.items())


@st.cache_data(max_entries=CACHE_MAX_ENTRIES, show_spinner=False)
def assign_frames(titles: pd.DataFrame, frame_set: tuple) -> pd.Series:
    """Her habere başlığındaki (başlık yoksa URL'deki) anahtar kelimelere göre çerçeve atar.
    Birden fazla çerçeve ';' ile ayrılır; hiçbirine uymayan haber "Diğer" olur."""
    patterns = [
        (name, re.compile(r"\b(?:" + "|".join(re.escape(k) for k in keywords) + ")"))
        for name, keywords in frame_set if keywords
    ]
    text = titles["TitleCore"].where(titles["TitleCore"] != "", titles["DocumentIdentifier"].map(url_slug))

    def detect(t: str) -> str:
        folded = fold_tr(t)
        found = [name for name, pattern in patterns if pattern.search(folded)]
        return ";".join(found) if found else "Diğer"

    return text.map(detect)


def parse_custom_frames(text: str) -> dict:
    """"Kategori: kelime, kelime" satırlarından çerçeve sözlüğü üretir. Kelimeler Türkçe karakterden
    arındırılıp küçültülür; iki nokta içermeyen veya kelimesi olmayan satırlar yok sayılır."""
    frames = {}
    for line in text.splitlines():
        name, sep, words = line.partition(":")
        name = name.strip()
        keywords = [fold_tr(w).strip() for w in words.split(",") if fold_tr(w).strip()]
        if sep and name and keywords and name != "Diğer":
            frames.setdefault(name, []).extend(keywords)
    return frames


@st.cache_data(ttl=CACHE_BIG_TTL, max_entries=CACHE_BIG_ENTRIES, show_spinner=False)
def explode_locations(df: pd.DataFrame) -> pd.DataFrame:
    """V2Locations'ı haber başına tekil yer satırlarına açar (index = haber satırı)."""
    exploded = df["V2Locations"].str.split(";").explode()
    exploded = exploded[exploded.notna() & (exploded != "")]
    if exploded.empty:
        return pd.DataFrame(columns=LOCATION_FIELDS[:-1])
    parts = exploded.str.split("#", expand=True).reindex(columns=range(len(LOCATION_FIELDS)))
    parts.columns = LOCATION_FIELDS
    parts["Lat"] = pd.to_numeric(parts["Lat"], errors="coerce")
    parts["Lon"] = pd.to_numeric(parts["Lon"], errors="coerce")
    parts = parts.drop(columns=["Offset"])
    return parts[~parts.reset_index().duplicated(["index", "FeatureID", "LocName"]).to_numpy()]


@st.cache_data(max_entries=CACHE_MAX_ENTRIES, show_spinner=False)
def detect_person_named_places(locations: pd.DataFrame, persons: pd.DataFrame, topic_stems: tuple) -> list:
    """Muhtemelen kişi adından yanlış kodlanmış yerleri bulur. Yerin adı (virgülden önceki kısım):
      - bir konu kelimesiyse (aranan kişinin soyadı gibi; ad il adıyla aynıysa hariç), veya
      - aynı haberdeki bir kişi adının parçasıysa (geçtiği haberlerin en az GEO_NOISE_MIN_SHARE oranında)."""
    places = locations[locations["LocType"] != "1"]  # ülke adları kişi adı değildir
    if places.empty:
        return []
    parts = places["LocName"].str.split(",")
    name = parts.str[0].str.strip().map(fold_tr)
    adm1 = parts.str[1].fillna("").str.strip().map(fold_tr)

    topic_hit = name.map(word_stem).isin(topic_stems) & (name != adm1) & (name.str.len() > 2)

    tokens = persons["V2Persons"].map(fold_tr).str.split().explode().dropna()
    doc_tokens = set(zip(tokens.index, tokens))
    person_hit = pd.Series([(i, n) in doc_tokens for i, n in zip(places.index, name)], index=places.index, dtype=bool)
    person_share = person_hit.groupby(places["LocName"]).mean()

    flagged = set(places.loc[topic_hit, "LocName"]) | set(person_share[person_share >= GEO_NOISE_MIN_SHARE].index)
    return sorted(flagged)


def get_theme_family(theme_code: str) -> str:
    code_upper = theme_code.upper()
    for prefix, label in THEME_FAMILIES:
        if code_upper.startswith(prefix):
            return label
    return "GDELT çekirdek teması"


def with_doc_fields(exploded: pd.DataFrame, docs: pd.DataFrame) -> pd.DataFrame:
    """Açılmış tabloyu filtrelenmiş haberlerle sınırlar ve tarih/ton bilgisini ekler."""
    subset = exploded[exploded.index.isin(docs.index)]
    return subset.join(docs[["pub_timestamp", "AvgTone"]])


def get_top_themes(theme_df: pd.DataFrame, n: int = 500) -> pd.DataFrame:
    counts = theme_df["Themes"].value_counts().head(n).reset_index()
    counts.columns = ["Theme", "Count"]
    return counts


def entity_summary(entity_df: pd.DataFrame, col: str, n: int) -> pd.DataFrame:
    return (
        entity_df.groupby(col)
        .agg(HaberSayisi=("AvgTone", "size"), OrtalamaTon=("AvgTone", "mean"))
        .sort_values("HaberSayisi", ascending=False)
        .head(n)
        .reset_index()
    )


def to_csv_bytes(df: pd.DataFrame) -> bytes:
    return df.to_csv(index=False).encode("utf-8-sig")


def articles_csv_bytes(df: pd.DataFrame) -> bytes:
    """Haber tablosunu panele yeniden yüklenebilir CSV'ye çevirir. Zaman damgası Türkiye saati olarak saat dilimiyle
    yazılır (+03:00); preprocess onu doğru UTC'ye çevirir, saatler kaymaz."""
    out = df.copy()
    out["pub_timestamp"] = out["pub_timestamp"].dt.tz_localize(LOCAL_TZ)
    return to_csv_bytes(out)


def fmt_int(value: int) -> str:
    return f"{value:,}".replace(",", ".")


# --------------------------------------------------------------------------------
# KONU KARTI VE KARŞILAŞTIRMA YARDIMCILARI
# --------------------------------------------------------------------------------
# GDELT'in veri toplamadığı günler (kaynak tabloda bölüm yok); veri notlarında gösterilir
GDELT_GAPS = [(date(2017, 8, 29), date(2017, 8, 29)), (date(2025, 6, 15), date(2025, 7, 1))]
# Alıntı (GQG) verisinin başladığı gün; öncesinde "öne çıkan alıntılar" boş kalır
QUOTES_SINCE = date(2025, 9, 28)
TR_MONTHS = ["Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran", "Temmuz", "Ağustos", "Eylül", "Ekim",
             "Kasım", "Aralık"]
# Alıntının bir konuşmacıya ait olduğunu gösteren bağlam kelimeleri ("... dedi")
QUOTE_SPEAKER_HINTS = re.compile(r"(?i)\b(?:dedi|söyledi|açıkladı|ifade etti|belirtti|kaydetti|vurguladı|dile getirdi)")

# --- Veri kapsamı denetimi ("Veri kapsamı ve boşluklar") ---
# GDELT GKG 2.1'in ilk günü; öncesinde Türkçe haber verisi yok
ARCHIVE_START = date(2015, 2, 18)
# Yerel kaynak dizini (pipeline/source_index.py): gün × site haber sayısı; kaynak = gdelt | ek
SOURCE_INDEX = os.path.join(ARCHIVE_DIR, "source_daily.parquet")
# Günlük özet katmanı (pipeline/ozet.py). Sapma bölümü yalnızca tüm yayınlar ("") satırlarını okur.
OZET_INDEX = os.path.join(ARCHIVE_DIR, "ozet_gunluk.parquet")
# Sapma hesabı 52 haftalık taban + o hafta ister; bir yıldan biraz fazlası okunur
OZET_GUN = 400
# Türkçe ad sözlüğü (pipeline/entities.py). GDELT'in makine çevirisinden gelen bozuk adlarının yerine
# başlıklardan çıkarılmış temiz adları göstermek için kullanılır.
SOZLUK_INDEX = os.path.join(ARCHIVE_DIR, "ad_sozlugu.parquet")
# Arşiv dışı (ek) toplama: GDELT'te olmayan yayınların künyeleri (pipeline/extra_collect.py)
EXTRA_DIR = os.path.join(ARCHIVE_DIR, "extra")
# Kapsamı denetlenen yayınlar: farklı yayın çizgilerinden temsilciler. Grup adları yalnızca kapsam dengesini
# göstermek içindir; yayın başına birden fazla alan adı olabilir (alan adı değişiklikleri).
KEY_OUTLETS = {
    "Haber ajansları": {"AA": ["aa.com.tr"], "DHA": ["dha.com.tr"], "İHA": ["iha.com.tr"]},
    "Ana akım": {"Hürriyet": ["hurriyet.com.tr"], "Milliyet": ["milliyet.com.tr"], "Habertürk": ["haberturk.com"],
                 "NTV": ["ntv.com.tr"], "CNN Türk": ["cnnturk.com"], "TRT Haber": ["trthaber.com"],
                 "Sözcü": ["sozcu.com.tr"]},
    "Hükümete yakın": {"Sabah": ["sabah.com.tr"], "Yeni Şafak": ["yenisafak.com", "yenisafak.com.tr"],
                       "Star": ["star.com.tr"], "Akşam": ["aksam.com.tr"], "A Haber": ["ahaber.com.tr"],
                       "Yeni Akit": ["yeniakit.com.tr"]},
    "Muhalif / bağımsız": {"Cumhuriyet": ["cumhuriyet.com.tr"], "BirGün": ["birgun.net"], "Evrensel": ["evrensel.net"],
                           "T24": ["t24.com.tr"], "Halk TV": ["halktv.com.tr"], "Tele1": ["tele1.com.tr"],
                           "Diken": ["diken.com.tr"], "Gazete Duvar": ["gazeteduvar.com.tr"], "Bianet": ["bianet.org"],
                           "Karar": ["karar.com"], "Medyascope": ["medyascope.tv"], "Kısa Dalga": ["kisadalga.net"],
                           "Serbestiyet": ["serbestiyet.com"]},
    "Ulusalcı": {"Aydınlık": ["aydinlik.com.tr", "aydinlikgazete.com"],
                 "Ulusal Kanal": ["ulusalkanal.com.tr", "ulusal.com.tr"], "Odatv": ["odatv.com", "odatv4.com"],
                 "Nefes": ["nefes.com.tr"], "Yeniçağ": ["yenicaggazetesi.com.tr", "yenicaggazetesi.com"]},
    "Kürt siyasetine yakın": {"Özgür Gündem": ["ozgur-gundem.com", "ozgurgundem.com"],
                              "Mezopotamya Ajansı": ["mezopotamyaajansi.com"]},
    "Gülen hareketine yakın": {"Samanyoluhaber": ["samanyoluhaber.com", "shaber3.com"], "TR724": ["tr724.com"],
                               "Bold Medya": ["boldmedya.com"]},
    "Yabancı yayınların Türkçe servisleri": {"BBC Türkçe": ["bbc.com"], "DW Türkçe": ["dw.com"],
                                             "Euronews": ["euronews.com"],
                                             "Sputnik": ["sputniknews.com.tr", "sputniknews.com", "anlatilaninotesi.com.tr"],
                                             "Amerika'nın Sesi": ["amerikaninsesi.com"]},
}
# Bir yayın aralıktaki arşiv günlerinin bu oranından azında varsa "aralığın bir kısmında" sayılır
OUTLET_MIN_DAY_SHARE = 0.5
# Başlıksız haber payı bu oranı aşarsa uyarı (GDELT 2019 ortasından önce başlık tutmuyor)
TITLE_GAP_SHARE = 0.5
TITLES_SINCE = date(2019, 7, 1)
# Başlıkların en az bu kadarı arşiv öncesi bir yıla atıf yapıyorsa "olayın kendisi arşivde yok" uyarısı
OLD_YEAR_MIN_SHARE, OLD_YEAR_MIN_TITLES = 0.02, 10


def as_date(value) -> date:
    return value.date() if isinstance(value, datetime) else value


def data_note(start, end, extra: str = "") -> None:
    """Grafiklerin altındaki "Veri notu": aralıktaki GDELT boşlukları, kapsam uyarısı ve varsa ek not."""
    start, end = as_date(start), as_date(end)
    gaps = [
        f"{a:%d.%m.%Y}" + (f"–{b:%d.%m.%Y}" if b != a else "")
        for a, b in GDELT_GAPS if a <= end and b >= start
    ]
    parts = []
    if gaps:
        parts.append("Bu aralıkta GDELT'in veri toplamadığı günler var: " + ", ".join(gaps) + ".")
    parts.append("Kapsam GDELT'in taradığı sitelerle sınırlıdır; tam sayım değil, geniş bir örneklemdir.")
    if extra:
        parts.append(extra)
    st.markdown(f'<div class="mk-note"><b>Veri notu.</b> {html.escape(" ".join(parts))}</div>',
                unsafe_allow_html=True)


@st.cache_data(ttl=3600, max_entries=CACHE_MAX_ENTRIES, show_spinner=False)
def source_presence(start, end) -> tuple | None:
    """(site bazında GDELT haber sayısı `n`, ek toplamadaki haber sayısı `n_ek`, GDELT veya ek toplamada haberi olan
    gün sayısı `days`;
    aralıktaki arşiv günü sayısı). Önce yerel kaynak dizini, yoksa (yayında) BigQuery'deki gdelt_tr.source_daily
    okunur (Storage Read API; 1 yıl ~2 MB; ek toplama orada yoktur). İkisi de yoksa None."""
    start, end = as_date(start), as_date(end)
    if os.path.exists(SOURCE_INDEX):
        idx = pd.read_parquet(SOURCE_INDEX, filters=[("gkg_day", ">=", pd.Timestamp(start)),
                                                     ("gkg_day", "<=", pd.Timestamp(end))])
    else:
        try:
            full = bq_source_daily_all()
        except Exception:
            return None
        days = pd.to_datetime(full["gkg_day"])
        idx = full[(days >= pd.Timestamp(start)) & (days <= pd.Timestamp(end))]
    if idx.empty:
        return None
    if "kaynak" not in idx:
        idx["kaynak"] = "gdelt"
    gd, ek = idx[idx["kaynak"] == "gdelt"], idx[idx["kaynak"] == "ek"]
    out = gd.groupby("site", observed=True).agg(n=("n", "sum"))
    out = out.join(ek.groupby("site", observed=True)["n"].sum().rename("n_ek"), how="outer")
    # gün kapsamı: GDELT veya ek toplamada haberi olan günler (ikisi birbirini tamamlar)
    out = out.join(idx.groupby("site", observed=True)["gkg_day"].nunique().rename("days"), how="outer").fillna(0)
    out.index = out.index.astype(str)
    return out, int(gd["gkg_day"].nunique())


@st.cache_data(ttl=600, max_entries=CACHE_MAX_ENTRIES, show_spinner=False)
def extra_fetch(term: str) -> pd.DataFrame:
    """Arşiv dışı (ek) toplamada konuyla eşleşen künyeler: adres veya başlıkta, panelin kalıplarıyla.
    Tarih süzgeci yoktur (2015 öncesi kayıtlar da gelir); ayırmayı çağıran yapar. Yerel ek toplama yoksa (yayında)
    BigQuery kopyasından okunur; o da yoksa boş."""
    import duckdb

    cols = ["site", "url", "title", "pub_timestamp", "precision"]
    pat = topic_patterns(term)
    if extra_local():
        with duckdb.connect() as con:
            df = con.execute(
                f"""SELECT {", ".join(cols)} FROM read_parquet('{EXTRA_DIR}/*/*.parquet')
                    WHERE regexp_matches(url, ?) OR regexp_matches(COALESCE(title, ''), ?)""",
                [pat["url_re"], pat["title_re"]]).df()
    elif extra_bq_available():
        df = bq_extra_read(cols, f"REGEXP_CONTAINS(url, {bq_literal(pat['url_re'])}) OR "
                                 f"REGEXP_CONTAINS(COALESCE(title, ''), {bq_literal(pat['title_re'])})")
    else:
        return pd.DataFrame(columns=cols + ["baslik"])
    df["pub_timestamp"] = pd.to_datetime(df["pub_timestamp"], utc=True).dt.tz_convert(LOCAL_TZ).dt.tz_localize(None)
    df["baslik"] = df["title"].fillna("").where(df["title"].fillna("") != "",
                                               df["url"].map(url_slug).str.replace("-", " "))
    return df.sort_values("pub_timestamp").reset_index(drop=True)


def extra_local() -> bool:
    """Ek toplama bu bilgisayarda var mı (data/archive/extra)."""
    return os.path.isdir(EXTRA_DIR) and any(os.scandir(EXTRA_DIR))


@st.cache_data(ttl=3600, show_spinner=False)
def extra_bq_available() -> bool:
    """Ek toplamanın BigQuery kopyası okunabiliyor mu (tablo bilgisi okunur; sorgu çalışmaz)."""
    try:
        return bool(bq_client().get_table(BQ_EXTRA).num_rows)
    except Exception:
        return False


def bq_extra_read(fields: list, restriction: str) -> pd.DataFrame:
    """Ek toplamanın BigQuery kopyasından satırlar (Storage Read API; süzme sunucuda, yalnızca eşleşenler gelir).
    Panelin okuma sınırları (BQ_MAX_GB, günlük sınır) ve EXTRA_BQ_MAX_ROWS uygulanır."""
    import pyarrow as pa
    from google.cloud.bigquery_storage import types

    table = BQ_EXTRA.replace(".", "/datasets/", 1).replace(".", "/tables/", 1)
    reader = bq_reader()
    session = reader.create_read_session(
        parent=f"projects/{BQ_PROJECT}",
        read_session=types.ReadSession(
            table=f"projects/{table}", data_format=types.DataFormat.ARROW,
            read_options=types.ReadSession.TableReadOptions(selected_fields=fields, row_restriction=restriction),
        ),
        max_stream_count=4,
    )
    scanned = session.estimated_total_bytes_scanned
    used = bq_used_today()
    if used + scanned > BQ_DAILY_MAX_GB * 1024 ** 3:
        raise RuntimeError(f"Panelin günlük okuma sınırı ({BQ_DAILY_MAX_GB} GB) doldu. Yarın tekrar deneyin.")
    day = datetime.now(timezone.utc).date()
    bq_usage()[day] = bq_usage().get(day, 0) + scanned
    parts, n = [], 0
    for stream in session.streams:
        for page in reader.read_rows(stream.name).rows(session).pages:
            batch = page.to_arrow()
            parts.append(batch)
            n += batch.num_rows
            if n > EXTRA_BQ_MAX_ROWS:
                raise RuntimeError(f"{fmt_int(EXTRA_BQ_MAX_ROWS)}'den fazla künye eşleşti. Tarih aralığını daraltın, "
                                   "yayın seçin veya bir kelimeyle süzün.")
    return pa.Table.from_batches(parts).to_pandas() if parts else pd.DataFrame(columns=fields)


@st.cache_resource(ttl=6 * 3600, show_spinner=False)
def bq_source_daily_all() -> pd.DataFrame:
    """BigQuery'deki kaynak dizininin tamamı (~0,9 milyon satır, bellekte ~40 MB). Tüm oturumlar paylaşır, 6 saatte
    bir yenilenir; her tarih aralığı için ayrıca okumak (tüm yıllarda ~25 sn) yerine bellekte süzülür. Değiştirilmez."""
    df = bq_source_daily(date(2015, 1, 1), date.today() + timedelta(days=1))
    df["site"] = df["site"].astype("category")
    return df


def bq_source_daily(start, end) -> pd.DataFrame:
    """BigQuery'deki kaynak dizininden aralığın satırları (Storage Read API; sorgu kotasına sayılmaz)."""
    import pyarrow as pa
    from google.cloud.bigquery_storage import types

    table = BQ_SOURCE_DAILY.replace(".", "/datasets/", 1).replace(".", "/tables/", 1)
    reader = bq_reader()
    session = reader.create_read_session(
        parent=f"projects/{BQ_PROJECT}",
        read_session=types.ReadSession(
            table=f"projects/{table}", data_format=types.DataFormat.ARROW,
            read_options=types.ReadSession.TableReadOptions(
                selected_fields=["gkg_day", "site", "n"],
                row_restriction=f"gkg_day BETWEEN DATE '{start:%Y-%m-%d}' AND DATE '{end:%Y-%m-%d}'",
            ),
        ),
        max_stream_count=4,
    )
    parts = [p.to_arrow() for s in session.streams for p in reader.read_rows(s.name).rows(session).pages]
    return pa.Table.from_batches(parts).to_pandas() if parts else pd.DataFrame(columns=["gkg_day", "site", "n"])


def coverage_report(fdf: pd.DataFrame, start, end, extra: pd.DataFrame | None = None) -> dict:
    """Aramanın veri kapsamı: arşiv öncesi döneme atıflar, başlık boşluğu ve GDELT boşlukları (uyarılar; düzeyi
    belirler; konu kartı ve PDF bunları gösterir) ile kilit yayınların arşivde olup olmadığı (`outlets` tablosu;
    uyarı üretmez, panelde ve PDF'te gösterilmez, yalnızca vaka araçlarının kapsam.md'sinde kullanılır). Uyarılar (önem, metin) listesidir.
    `extra`: aralıktaki ek toplama eşleşmeleri; GDELT'te olmayan yayınlar "yalnızca ek toplamada" sayılır."""
    start, end = as_date(start), as_date(end)
    warnings = []
    # Olayın kendisi arşiv öncesinde mi? Başlıklarda arşiv başlangıcından eski yıllara atıflar: doğrudan ("2011'de")
    # veya göreli ("15 yıl sonra", "15 yıllık"; haberin yılından çıkarılır)
    titled = fdf.loc[fdf["PageTitle"].fillna("") != "", ["PageTitle", "pub_timestamp"]]
    if len(titled):
        direct = titled["PageTitle"].str.findall(r"\b(19[5-9]\d|20[01]\d)\b").map(lambda ys: {int(y) for y in ys})
        ago = titled["PageTitle"].str.extract(r"\b(\d{1,2}) y[ıi]l(?:l[ıi]k| sonra| [öo]nce|d[ıi]r| evvel)", expand=False)
        relative = (titled["pub_timestamp"].dt.year - pd.to_numeric(ago, errors="coerce")).dropna().astype(int)
        refs = direct.copy()
        for i, y in relative.items():
            refs[i] = refs[i] | {y}
        years = refs.map(lambda ys: {y for y in ys if y < ARCHIVE_START.year}).explode().dropna().astype(int)
        for year, n in years.value_counts().items():
            if n >= OLD_YEAR_MIN_TITLES and n / len(titled) >= OLD_YEAR_MIN_SHARE:
                warnings.append(("yüksek", f"Başlıklarda {year} yılına atıf oranı %{n / len(titled) * 100:.0f} "
                                           f"(doğrudan veya \"… yıl sonra\" biçiminde). Arşiv {ARCHIVE_START:%d.%m.%Y}'te "
                                           "başlar; o dönemin haberleri yoktur, olayın kendisi başka kaynaklardan "
                                           "incelenmelidir."))
    # Başlıksız haberler: eşleşme yalnızca URL ve kişi alanına dayanır, bir kısmı kaçar
    untitled = float((fdf["PageTitle"].fillna("") == "").mean()) if len(fdf) else 0.0
    if untitled == 1.0 and len(fdf) and as_date(fdf["pub_timestamp"].max()) >= TITLES_SINCE:
        warnings.append(("orta", "Verideki hiçbir haberde başlık yok; yüklenen dosyada başlık (PageTitle) kolonu "
                                 "olmayabilir. Başlığa dayalı analizler (kopya tespiti, çerçeveler) çalışmaz."))
    elif untitled >= TITLE_GAP_SHARE:
        warnings.append(("orta", f"Başlıksız haber oranı %{untitled * 100:.0f} (GDELT 2019 ortasından önce başlık "
                                  "tutmuyor). Bu haberler yalnızca adres (URL) ve kişi alanıyla bulundu; Türkçe "
                                  "karakterli ve adreste geçmeyen haberler kaçmış olabilir."))
    gaps = [f"{a:%d.%m.%Y}" + (f"–{b:%d.%m.%Y}" if b != a else "") for a, b in GDELT_GAPS if a <= end and b >= start]
    if gaps:
        warnings.append(("düşük", "GDELT'in veri toplamadığı günler: " + ", ".join(gaps) + "."))
    # Kilit yayınlar: arşivde var mı, varsa bu konuda haber yapmış mı?
    presence, range_days = source_presence(start, end) or (None, 0)
    in_result = fdf["SourceCommonName"].value_counts()
    in_extra = extra["site"].value_counts() if extra is not None and len(extra) else pd.Series(dtype=int)
    rows = []
    for group, outlets in KEY_OUTLETS.items():
        for name, domains in outlets.items():
            n_res = int(sum(in_result.get(d, 0) for d in domains))
            n_ek_res = int(sum(in_extra.get(d, 0) for d in domains))
            n_arch, n_ek, share = None, 0, None
            if presence is not None:
                hit = presence[presence.index.isin(domains)]
                n_arch, n_ek = int(hit["n"].sum()), int(hit["n_ek"].sum())
                share = float(hit["days"].max()) / range_days if len(hit) and range_days else 0.0
            if n_arch == 0 and n_ek:
                status = "Yalnızca ek toplamada"
            elif n_arch == 0:
                status = "Arşivde yok"
            elif share is not None and share < OUTLET_MIN_DAY_SHARE:
                status = f"Aralığın bir kısmında arşivde (%{share * 100:.0f} gün)"
            elif n_res:
                status = "Konuyu işlemiş"
            else:
                status = "Arşivde var, konuda haber yok" if n_arch is not None else "Sonuçlarda yok (arşiv bilinmiyor)"
            rows.append({"Grup": group, "Yayın": name, "Arşivdeki haber": n_arch, "Konudaki haber": n_res,
                         "Ek toplama": n_ek, "Ek toplamada konudaki": n_ek_res, "Durum": status})
    # Yayın tablosu uyarı üretmez ve panelde gösterilmez: hangi yayının arşivde olmadığı konuya değil yalnızca
    # tarih aralığına bağlıdır; her aramada aynı bilgiyi göstermek kullanıcıyı yanıltıyordu (vaka araçları kullanır)
    outlets = pd.DataFrame(rows)
    warnings.sort(key=lambda w: ["yüksek", "orta", "düşük"].index(w[0]))
    levels = {w[0] for w in warnings}
    level = "Yetersiz" if "yüksek" in levels else "Sınırlı" if "orta" in levels else "Yeterli"
    return {"level": level, "warnings": warnings, "outlets": outlets, "indexed": presence is not None,
            "extra": extra if extra is not None else pd.DataFrame()}


def render_coverage(cov: dict) -> None:
    """Konu kartının üstündeki "Veri kapsamı ve boşluklar" şeridi; ayrıntılar açılır kutuda."""
    color = {"Yetersiz": MK["red"], "Sınırlı": MK["amber_text"], "Yeterli": MK["green"]}[cov["level"]]
    n = len(cov["warnings"])
    summary = cov["warnings"][0][1] if n else "Bu aramada belirgin bir kapsam boşluğu görünmüyor."
    st.markdown(
        f'<div class="mk-note" style="border-left:4px solid {color}"><b>Veri kapsamı: '
        f'<span style="color:{color}">{cov["level"]}</span></b>'
        f'{f" · {n} uyarı" if n else ""}. {html.escape(summary)}</div>',
        unsafe_allow_html=True,
    )
    if n > 1:
        with st.expander("Veri kapsamı ve boşluklar · ayrıntı"):
            for severity, text in cov["warnings"]:
                st.markdown(f"- **{severity.capitalize()}:** {text}")


def render_extra(extra_all: pd.DataFrame, start, end) -> None:
    """"Arşiv dışı kaynaklar": GDELT'te olmayan yayınlardan (ek toplama) konuyla eşleşen künyeler. Aralık içi ve
    arşiv başlangıcından (2015) önceki kayıtlar ayrı gösterilir. Konu kartının rakamlarına katılmaz."""
    if extra_all.empty:
        return
    start_ts, end_ts = pd.Timestamp(as_date(start)), pd.Timestamp(as_date(end)) + pd.Timedelta(days=1)
    in_range = extra_all[(extra_all["pub_timestamp"] >= start_ts) & (extra_all["pub_timestamp"] < end_ts)]
    before = extra_all[extra_all["pub_timestamp"] < pd.Timestamp(ARCHIVE_START)]
    if in_range.empty and before.empty:
        return
    card_title("Arşiv dışı kaynaklar · ek toplama")
    parts = []
    if len(in_range):
        parts.append(f"bu aralıkta **{fmt_int(len(in_range))}** haber "
                     f"({', '.join(f'{s} {n}' for s, n in in_range['site'].value_counts().head(6).items())})")
    if len(before):
        parts.append(f"arşiv başlangıcından ({ARCHIVE_START:%d.%m.%Y}) önce **{fmt_int(len(before))}** haber")
    st.markdown("GDELT'in taramadığı yayınlarda " + "; ".join(parts) + ".")
    view_cols = {"pub_timestamp": st.column_config.DatetimeColumn("Tarih", format="DD.MM.YYYY"),
                 "site": st.column_config.TextColumn("Yayın"),
                 "baslik": st.column_config.TextColumn("Başlık (yoksa adresten)", width="large"),
                 "precision": st.column_config.TextColumn("Tarih hassasiyeti"),
                 "url": st.column_config.LinkColumn("Bağlantı", display_text="Haberi aç")}
    with st.expander("Kanıtı göster · arşiv dışı kaynaklar"):
        options = (["Bu aralık"] if len(in_range) else []) + (["Arşiv öncesi (2015'ten önce)"] if len(before) else [])
        pick = st.radio("Dönem", options, horizontal=True, key="ev_extra")
        rows = in_range if pick == "Bu aralık" else before
        st.dataframe(rows.sort_values("pub_timestamp", ascending=False)[list(view_cols)], hide_index=True,
                     width="stretch", column_config=view_cols, key="ev_extra_table")
    st.markdown(
        '<div class="mk-note"><b>Veri notu.</b> Bu kayıtlar GDELT\'ten değil, yayınların kendi site haritaları ve '
        'RSS akışlarından toplanan künyelerdir: yalnızca adres, başlık ve tarih vardır; ton, tema ve kişi bilgisi '
        'yoktur ve konu kartının rakamlarına katılmaz. Eşleşme adres ve başlıkta yapılır. Eski haritalarda tarih '
        'yalnızca ay düzeyindedir ("ay"); başlığı olmayan kayıtlarda başlık adresten okunur.</div>',
        unsafe_allow_html=True,
    )


# --- "Arşiv dışı" ekranı: ek toplamadaki künyelere arama yapmadan göz atma ---
# Başlıklardan çıkarılan kişi ve kurum adları (pipeline/entities.py; extra_collect.py her toplamadan sonra günceller)
EXTRA_ENTITIES = os.path.join(ARCHIVE_DIR, "extra_entities.parquet")
# Çerçeve, ad ve tablo hesabı bu kadar künyeye kadar yapılır; üstünde yalnızca sayım grafiği gösterilir
EXTRA_DETAIL_MAX_ROWS = 150_000
EXTRA_TOP_SITES = 8
# Yayında (BigQuery) bir göz atmada okunabilecek en fazla künye (tarih sınırı yok)
EXTRA_BQ_MAX_ROWS = 300_000


def extra_available() -> bool:
    """Ek toplama yerelde veya (yayında) BigQuery kopyasında var mı."""
    return extra_local() or extra_bq_available()


@st.cache_data(ttl=600, show_spinner=False)
def extra_sites() -> list:
    """Ek toplamadaki yayınlar (alan adı): yerelde künye sayısına göre, yayında pipeline/extra_sources.toml sırasıyla
    (BigQuery'de tüm tabloyu okumamak için)."""
    import duckdb

    if not extra_local():
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "pipeline", "extra_sources.toml"),
                  "rb") as f:
            return [o["site"] for o in tomllib.load(f)["yayin"]]
    with duckdb.connect() as con:
        return con.execute(f"SELECT site FROM read_parquet('{EXTRA_DIR}/*/*.parquet') GROUP BY site "
                           "ORDER BY count(*) DESC").df()["site"].tolist()


@st.cache_data(ttl=600, max_entries=CACHE_MAX_ENTRIES, show_spinner=False)
def extra_browse(start, end, sites: tuple, term: str) -> pd.DataFrame:
    """Aralıktaki künyeler (Türkiye saatiyle), başlık yoksa adresten okunan başlık ve başlıktan çıkarılan adlar
    ("Ad|tür;..."). `term` varsa panelin arama kalıplarıyla adres veya başlıkta eşleşenler."""
    import duckdb

    start, end = as_date(start), as_date(end)
    if not extra_local():
        lo, hi = start - timedelta(days=1), end + timedelta(days=2)
        where = [f"pub_timestamp >= TIMESTAMP '{lo:%Y-%m-%d}' AND pub_timestamp < TIMESTAMP '{hi:%Y-%m-%d}'"]
        if sites:
            where.append("site IN (" + ", ".join(bq_literal(x) for x in sites) + ")")
        if term:
            pat = topic_patterns(term)
            where.append(f"(REGEXP_CONTAINS(url, {bq_literal(pat['url_re'])}) OR "
                         f"REGEXP_CONTAINS(COALESCE(title, ''), {bq_literal(pat['title_re'])}))")
        df = bq_extra_read(["site", "url", "title", "pub_timestamp", "precision", "adlar"], " AND ".join(where))
        return extra_browse_finish(df, start, end)
    # UTC saklanır; Türkiye günü sınırları için bir gün pay bırakılır, kesin süzme aşağıda yapılır
    where = ["pub_timestamp >= ?", "pub_timestamp < ?"]
    params = [pd.Timestamp(start - timedelta(days=1), tz="UTC"), pd.Timestamp(end + timedelta(days=2), tz="UTC")]
    if sites:
        where.append(f"site IN ({', '.join('?' * len(sites))})")
        params += list(sites)
    if term:
        pat = topic_patterns(term)
        where.append("(regexp_matches(url, ?) OR regexp_matches(COALESCE(title, ''), ?))")
        params += [pat["url_re"], pat["title_re"]]
    names = (f"LEFT JOIN (SELECT url, string_agg(ad || '|' || tur, ';') AS adlar FROM "
             f"read_parquet('{EXTRA_ENTITIES}') WHERE ad IS NOT NULL GROUP BY url) e USING (url)"
             if os.path.exists(EXTRA_ENTITIES) else "")
    with duckdb.connect() as con:
        df = con.execute(
            f"""SELECT k.site, k.url, k.title, k.pub_timestamp, k.precision{", e.adlar" if names else ""}
                FROM read_parquet('{EXTRA_DIR}/*/*.parquet') k {names}
                WHERE {" AND ".join("k." + w if w.startswith(("pub_", "site")) else w for w in where)}""",
            params).df()
    return extra_browse_finish(df, start, end)


def extra_browse_finish(df: pd.DataFrame, start, end) -> pd.DataFrame:
    """extra_browse'un ortak son adımı: Türkiye saati, kesin gün süzmesi, okunabilir başlık, adlar."""
    if "adlar" not in df:
        df["adlar"] = None
    df["pub_timestamp"] = pd.to_datetime(df["pub_timestamp"], utc=True).dt.tz_convert(LOCAL_TZ).dt.tz_localize(None)
    df = df[(df["pub_timestamp"] >= pd.Timestamp(start)) &
            (df["pub_timestamp"] < pd.Timestamp(end) + pd.Timedelta(days=1))].copy()
    df["has_title"] = df["title"].fillna("") != ""
    df["baslik"] = df["title"].fillna("").map(clean_title).where(
        df["has_title"], df["url"].map(url_slug).str.replace("-", " "))
    df["adlar"] = df["adlar"].fillna("")
    return df.sort_values("pub_timestamp", ascending=False).reset_index(drop=True)


def render_extra_browser() -> None:
    """"Arşiv dışı" ekranı: GDELT'te olmayan yayınların künyelerine (ek toplama) arama yapmadan göz atma. Yayın bazında
    hacim, başlıktan çerçeveler (seçilen çerçeve setiyle) ve başlıktan çıkarılan kişi/kurum adları. Ton ve tema yoktur."""
    st.markdown(
        '<div class="mk-kicker">Arşiv dışı kaynaklar</div><p class="mk-headline">GDELT\'in taramadığı yayınlar</p>'
        '<p class="mk-tagline">Yayınların kendi site haritaları ve RSS akışlarından toplanan künyeler: adres, başlık, '
        'tarih. Konu aramadan göz atın; isterseniz bir kelimeyle süzün.</p>',
        unsafe_allow_html=True,
    )
    today = date.today()
    c1, c2 = st.columns([2, 3])
    rng = c1.date_input("Tarih aralığı", value=(today - timedelta(days=DEFAULT_RANGE_DAYS), today),
                        min_value=date(2000, 1, 1), max_value=today, key="ex_range")
    all_sites = extra_sites()
    picked = c2.multiselect("Yayınlar", all_sites, placeholder="Tümü", key="ex_sites")
    c3, c4 = st.columns([2, 3])
    term = c3.text_input("Başlık veya adreste ara (isteğe bağlı)", key="ex_term",
                         placeholder="örn. deprem").strip()
    frame_name = c4.selectbox("Çerçeve seti", list(FRAME_SETS), key="ex_frames",
                              help="Başlıklar (başlık yoksa adres metni) bu setin anahtar kelimeleriyle etiketlenir.")
    if not (isinstance(rng, tuple) and len(rng) == 2):
        st.info("Tarih aralığının başlangıç ve bitişini seçin.")
        return
    if term and len(term) < 3:
        st.warning("Arama ifadesi en az 3 karakter olmalı.")
        return
    start, end = rng
    long_range = not extra_local() and (end - start).days + 1 > BQ_LONG_RANGE_DAYS
    try:
        with st.spinner("Künyeler okunuyor" + (" (uzun aralık, biraz sürebilir)..." if long_range else "...")):
            df = extra_browse(start, end, tuple(picked), term)
    except Exception as e:
        st.warning(f"Künyeler okunamadı: {e}")
        return
    if df.empty:
        st.info("Bu seçimde künye yok.")
        return

    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Künye", fmt_int(len(df)))
    k2.metric("Yayın", fmt_int(df["site"].nunique()))
    k3.metric("Gerçek başlığı olan", f"%{df['has_title'].mean() * 100:.0f}",
              help="Geri kalanlarda başlık adresteki metinden okunur (küçük harfli, Türkçe karaktersiz).")
    k4.metric("Kişi/kurum adı bulunan", f"%{(df['adlar'] != '').mean() * 100:.0f}")

    freq, unit = auto_freq(start, end)
    top = df["site"].value_counts().head(EXTRA_TOP_SITES).index
    vol = (df[df["site"].isin(top)].assign(b=lambda d: bucket_of(d["pub_timestamp"], freq))
           .groupby(["b", "site"]).size().unstack(fill_value=0))
    vol = vol.reindex(pd.date_range(vol.index.min(), vol.index.max(), freq=freq), fill_value=0)
    card_title(f"Künye sayısı · {unit} bazında" + (f" (en çok künyesi olan {EXTRA_TOP_SITES} yayın)"
                                                     if df["site"].nunique() > EXTRA_TOP_SITES else ""))
    fig = go.Figure([go.Scatter(x=vol.index, y=vol[s], mode="lines", name=s, line=dict(width=1.6)) for s in vol])
    fig.update_layout(template=PLOTLY_TEMPLATE, height=300, xaxis_title="", yaxis_title="",
                      legend=dict(orientation="h", y=1.1))
    st.plotly_chart(fig, width="stretch")
    note = ("Sayılar toplanan künyelerdir, yayının tüm haberleri değildir: eski arşiv haritalarında tarih bazen yalnızca "
            "ay düzeyindedir (ayın ilk gününde yığılma görülebilir); yalnızca güncel akış veren yayınlar toplamanın "
            "başladığı günden (29.09.2026) itibaren birikir.")
    st.markdown(f'<div class="mk-note"><b>Veri notu.</b> {html.escape(note)}</div>', unsafe_allow_html=True)

    if len(df) > EXTRA_DETAIL_MAX_ROWS:
        st.info(f"{fmt_int(len(df))} künye var. Çerçeve, ad ve tablo için tarih aralığını daraltın, yayın seçin veya "
                f"bir kelimeyle süzün (en fazla {fmt_int(EXTRA_DETAIL_MAX_ROWS)} künye).")
        return

    df["Cerceve"] = assign_frames(
        pd.DataFrame({"TitleCore": df["baslik"].where(df["has_title"], ""), "DocumentIdentifier": df["url"]}),
        frame_set_key(FRAME_SETS[frame_name]))
    names = (df.loc[df["adlar"] != "", ["url", "adlar"]].assign(adlar=lambda d: d["adlar"].str.split(";"))
             .explode("adlar"))
    names[["ad", "tur"]] = names["adlar"].str.split("|", n=1, expand=True)
    names = names.drop_duplicates(["url", "ad"])

    left, right = st.columns([3, 2])
    with left:
        card_title(f"Çerçeveler · yayın başına künyelerin yüzdesi ({frame_name})")
        fr = df[["site", "Cerceve"]].assign(Cerceve=df["Cerceve"].str.split(";")).explode("Cerceve")
        sites_top = df["site"].value_counts().head(12).index
        pct = (fr[fr["site"].isin(sites_top)].groupby(["site", "Cerceve"]).size().unstack(fill_value=0)
               .div(df["site"].value_counts().reindex(sites_top), axis=0).mul(100))
        pct = pct[[c for c in pct.columns if c != "Diğer"] + (["Diğer"] if "Diğer" in pct else [])]
        fig_h = px.imshow(pct.round(0), text_auto=".0f", aspect="auto",
                          color_continuous_scale=[[0, MK["surface"]], [1, MK["cyan"]]], labels={"color": "% künye"})
        fig_h.update_layout(template=PLOTLY_TEMPLATE, xaxis_title="", yaxis_title="", height=max(280, len(pct) * 32))
        st.plotly_chart(fig_h, width="stretch")
    with right:
        tab_p, tab_o = st.tabs(["Kişiler", "Kurumlar"])
        for tab, kind in ((tab_p, "kişi"), (tab_o, "kurum")):
            with tab:
                counts = names[names["tur"] == kind]["ad"].value_counts().head(12)
                rank_list(list(counts.items()))
    st.markdown(
        '<div class="mk-note"><b>Veri notu.</b> Bu künyelerde ton, tema ve GDELT\'in kişi/yer alanları yoktur. '
        'Çerçeveler başlıktaki (yoksa adresteki) anahtar kelimelerle, adlar GDELT başlıklarından kurulan bir ad '
        'sözlüğüyle kural tabanlı bulunur. Sözlük 2019 ortasından sonraki başlıklardan kurulduğu için daha eski '
        'dönemin adlarını eksik tanır; adreste Türkçe karakter olmadığından bazı kısaltmalar (ör. "yok" ile çakışan '
        'YÖK) bilerek dışarıda bırakılmıştır.</div>',
        unsafe_allow_html=True,
    )

    options = ["(tümü)"] + names["ad"].value_counts().head(200).index.tolist()
    pick = st.selectbox("Bir ada göre süz", options, key="ex_name")
    rows = df if pick == "(tümü)" else df[df["url"].isin(names.loc[names["ad"] == pick, "url"])]
    view = rows.assign(Adlar=rows["adlar"].str.replace(r"\|[^;]*", "", regex=True).str.replace(";", " · "))
    cols = {"pub_timestamp": st.column_config.DatetimeColumn("Tarih", format="DD.MM.YYYY HH:mm"),
            "site": st.column_config.TextColumn("Yayın"),
            "baslik": st.column_config.TextColumn("Başlık (yoksa adresten)", width="large"),
            "Cerceve": st.column_config.TextColumn("Çerçeve"),
            "Adlar": st.column_config.TextColumn("Kişi / kurum"),
            "precision": st.column_config.TextColumn("Tarih hassasiyeti"),
            "url": st.column_config.LinkColumn("Bağlantı", display_text="Haberi aç")}
    card_title(f"Künyeler · {fmt_int(len(rows))}")
    st.dataframe(view[list(cols)].head(1000), hide_index=True, width="stretch", column_config=cols, key="ex_table")
    if len(rows) > 1000:
        st.caption("İlk 1.000 künye gösteriliyor (en yeniler). Tamamı için CSV'yi indirin.")
    st.download_button(
        "Künyeleri CSV olarak indir",
        view[list(cols)].rename(columns={"pub_timestamp": "tarih", "site": "yayin", "baslik": "baslik",
                                         "Cerceve": "cerceve", "Adlar": "adlar", "precision": "tarih_hassasiyeti"})
        .to_csv(index=False).encode("utf-8-sig"),
        file_name=f"arsiv_disi_{start:%Y%m%d}_{end:%Y%m%d}.csv", mime="text/csv", key="ex_csv",
        icon=":material/download:", type="primary")


def card_title(text: str) -> None:
    st.markdown(f'<div class="mk-card-title">{html.escape(text)}</div>', unsafe_allow_html=True)


def rank_list(items) -> None:
    """(etiket, sayı veya metin) çiftlerini noktalı çizgili sıralı liste olarak gösterir."""
    rows = "".join(
        f'<div class="mk-rank"><span>{html.escape(str(label))}</span>'
        f'<b>{html.escape(count) if isinstance(count, str) else fmt_int(count)}</b></div>'
        for label, count in items
    )
    st.markdown(rows or '<div class="mk-rank"><span>—</span></div>', unsafe_allow_html=True)


def auto_freq(start, end) -> tuple:
    """Tarih aralığına göre zaman şeridi aralığı: (pandas frekansı, açıklama)."""
    days = (as_date(end) - as_date(start)).days
    if days <= 120:
        return "D", "gün"
    if days <= 730:
        return "W-MON", "hafta"
    return "MS", "ay"


def period_label(ts, freq: str) -> str:
    if freq == "D":
        return f"{ts:%d.%m.%Y}"
    if freq.startswith("W"):
        return f"{ts:%d.%m.%Y} haftası"
    return f"{TR_MONTHS[ts.month - 1]} {ts.year}"


def bucket_of(timestamps: pd.Series, freq: str) -> pd.Series:
    """Her haberin ait olduğu zaman kovası (auto_freq aralığının başlangıcı)."""
    if freq == "D":
        return timestamps.dt.normalize()
    if freq.startswith("W"):
        return timestamps.dt.to_period("W-SUN").dt.start_time
    return timestamps.dt.to_period("M").dt.start_time


def find_peaks(counts: pd.Series, k: int = 3) -> list:
    """En yüksek k kova; birbirine 2 kovadan yakın zirveler tek sayılır. Ortancanın 1,5 katını geçmeyenler atlanır."""
    if counts.empty:
        return []
    positions = {ts: i for i, ts in enumerate(counts.index)}
    floor = counts.median() * 1.5
    peaks = []
    for ts, value in counts.sort_values(ascending=False).items():
        if value <= floor or len(peaks) >= k:
            break
        if all(abs(positions[ts] - positions[p]) > 2 for p in peaks):
            peaks.append(ts)
    return sorted(peaks)


def tone_word(value: float) -> str:
    if value <= -1.5:
        return "olumsuz"
    if value >= 1.5:
        return "olumlu"
    return "nötr"


def evidence_table(rows: pd.DataFrame, key: str, limit: int = 200) -> None:
    """"Kanıtı göster": bir kartın arkasındaki haberler (en yeniler üstte), bağlantılı."""
    view = rows.sort_values("pub_timestamp", ascending=False).head(limit)[
        ["pub_timestamp", "SourceCommonName", "PageTitle", "AvgTone", "DocumentIdentifier"]
    ]
    st.dataframe(
        view, hide_index=True, width="stretch", key=key,
        column_config={
            "pub_timestamp": st.column_config.DatetimeColumn("Tarih", format="DD.MM.YYYY HH:mm"),
            "SourceCommonName": st.column_config.TextColumn("Site"),
            "PageTitle": st.column_config.TextColumn("Başlık", width="large"),
            "AvgTone": st.column_config.NumberColumn("Ton", format="%.1f"),
            "DocumentIdentifier": st.column_config.LinkColumn("Bağlantı", display_text="Haberi aç"),
        },
    )
    if len(rows) > limit:
        st.caption(f"İlk {fmt_int(limit)} haber gösteriliyor ({fmt_int(len(rows))} haberin en yenileri).")


@st.cache_data(ttl=3600, max_entries=CACHE_MAX_ENTRIES, show_spinner=False)
def fetch_quotes(urls_key: str, _urls: tuple, start, end, is_local: bool) -> pd.DataFrame:
    """Verilen haberlerin GQG alıntıları (url, date, pre, quote, post). Yerelde arşivden, yayında Storage Read API ile
    okunur; ikisi de ücretsizdir. Alıntılar yalnızca QUOTES_SINCE sonrası için vardır."""
    urls = _urls  # önbellek anahtarı urls_key'dir; uzun URL listesi her çizimde hash'lenmez
    columns = ["url", "date", "pre", "quote", "post"]
    start = max(as_date(start), QUOTES_SINCE)
    end = as_date(end)
    if not urls or start > end:
        return pd.DataFrame(columns=columns)
    if is_local:
        import duckdb

        folder = os.path.join(ARCHIVE_DIR, "quotes")
        files = [
            os.path.join(folder, f"{start + timedelta(days=i):%Y-%m-%d}.parquet")
            for i in range((end - start).days + 1)
        ]
        files = [f for f in files if os.path.exists(f)]
        if not files:
            return pd.DataFrame(columns=columns)
        with duckdb.connect() as con:
            con.register("wanted", pd.DataFrame({"u": list(urls)}))
            return con.execute(
                "SELECT url, date, q.pre, q.quote, q.post FROM read_parquet(?) , UNNEST(quotes) AS t(q) "
                "WHERE url IN (SELECT u FROM wanted)",
                [files],
            ).df()
    from google.cloud.bigquery_storage import types

    table = f"{BQ_PROJECT}.gdelt_tr.quotes".replace(".", "/datasets/", 1).replace(".", "/tables/", 1)
    # Filtre metni sınırlı olduğundan en fazla 800 haberin alıntısı okunur
    url_list = ", ".join(bq_literal(u) for u in list(urls)[:800])
    reader = bq_reader()
    session = reader.create_read_session(
        parent=f"projects/{BQ_PROJECT}",
        read_session=types.ReadSession(
            table=f"projects/{table}",
            data_format=types.DataFormat.ARROW,
            read_options=types.ReadSession.TableReadOptions(
                selected_fields=["url", "date", "quotes"],
                row_restriction=f"gqg_day BETWEEN DATE '{start:%Y-%m-%d}' AND DATE '{end:%Y-%m-%d}' "
                                f"AND url IN ({url_list})",
            ),
        ),
        max_stream_count=4,
    )
    frames = [reader.read_rows(x.name).to_dataframe(session) for x in session.streams]
    raw = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=["url", "date", "quotes"])
    rows = [
        {"url": r.url, "date": r.date, "pre": q.get("pre"), "quote": q.get("quote"), "post": q.get("post")}
        for r in raw.itertuples() for q in (r.quotes if r.quotes is not None else [])
    ]
    return pd.DataFrame(rows, columns=columns)


def top_quotes(quotes: pd.DataFrame, k: int = 5) -> pd.DataFrame:
    """Konuşmacısı belli, anlamlı uzunluktaki alıntılardan en çok haberde tekrarlananlar."""
    if quotes.empty:
        return quotes
    q = quotes.dropna(subset=["quote"]).copy()
    q["quote"] = q["quote"].str.strip()
    q = q[q["quote"].str.len().between(50, 400)]
    context = q["pre"].fillna("") + " " + q["post"].fillna("")
    q = q[context.str.contains(QUOTE_SPEAKER_HINTS)]
    if q.empty:
        return q
    q["key"] = q["quote"].map(lambda t: fold_tr(t)[:120])
    grouped = (
        q.sort_values("date")
        .groupby("key")
        .agg(quote=("quote", "first"), pre=("pre", "first"), url=("url", "first"), date=("date", "first"),
             haber=("url", "nunique"))
        .sort_values(["haber", "date"], ascending=[False, False])
    )
    return grouped.head(k)


@st.cache_data(ttl=3600, show_spinner=False)
def daily_totals(is_local: bool) -> pd.Series:
    """Günlük toplam Türkçe haber sayısı (normalizasyon paydası). Yerelde arşivden, yayında tablo listelemesiyle
    (ücretsiz) okunur."""
    if is_local:
        import duckdb

        path = os.path.join(ARCHIVE_DIR, "daily_totals", "*.parquet")
        frame = duckdb.sql(f"SELECT day, articles FROM '{path}'").df()
    else:
        from google.cloud import bigquery

        rows = bq_client().list_rows(
            BQ_TOTALS, selected_fields=[bigquery.SchemaField("day", "DATE"), bigquery.SchemaField("articles", "INT64")]
        )
        frame = pd.DataFrame([{"day": r["day"], "articles": r["articles"]} for r in rows])
    frame["day"] = pd.to_datetime(frame["day"])
    return frame.set_index("day")["articles"].sort_index()


def render_card_header(title: str, start, end, fdf: pd.DataFrame) -> None:
    st.markdown(
        f'<div class="mk-kicker">Konu kartı</div><p class="mk-headline">{html.escape(title)}</p>'
        f'<p class="mk-tagline">{as_date(start):%d.%m.%Y} – {as_date(end):%d.%m.%Y} · '
        f'{fmt_int(len(fdf))} haber · {fmt_int(fdf["SourceCommonName"].nunique())} site</p>',
        unsafe_allow_html=True,
    )


def card_facts(fdf: pd.DataFrame, start, end, persons: pd.DataFrame, orgs: pd.DataFrame,
               topic_stems: tuple) -> dict:
    """Konu kartının ve PDF raporun ortak hesapları (ikisi aynı rakamları gösterir)."""
    freq, unit = auto_freq(start, end)
    buckets = bucket_of(fdf["pub_timestamp"], freq)
    counts = buckets.value_counts().sort_index()
    if len(counts) > 1:
        counts = counts.reindex(pd.date_range(counts.index.min(), counts.index.max(), freq=freq), fill_value=0)
    peaks = []
    for i, ts in enumerate(find_peaks(counts), start=1):
        in_bucket = fdf[buckets == ts]
        top = in_bucket.sort_values(["CopyGroupSize", "pub_timestamp"], ascending=[False, True]).iloc[0]
        peaks.append((i, ts, len(in_bucket), top["PageTitle"] or url_slug(top["DocumentIdentifier"]), in_bucket))
    p = persons[persons.index.isin(fdf.index)]
    if topic_stems:
        p = p[~p["V2Persons"].map(fold_tr).str.contains("|".join(re.escape(t) for t in topic_stems), regex=True)]
    o = orgs[orgs.index.isin(fdf.index)]
    stories = (
        fdf.sort_values("pub_timestamp")
        .groupby("CopyGroup")
        .agg(baslik=("PageTitle", "first"), ilk=("SourceCommonName", "first"), zaman=("pub_timestamp", "first"),
             site=("SourceCommonName", "nunique"))
        .query("site > 1")
        .sort_values("site", ascending=False)
        .head(5)
    )
    avg = float(fdf["AvgTone"].mean())
    return {
        "freq": freq, "unit": unit, "counts": counts, "peaks": peaks,
        "top_sites": fdf["SourceCommonName"].value_counts().head(8),
        "avg_tone": avg, "neg": float((fdf["AvgTone"] <= -1.5).mean()), "pos": float((fdf["AvgTone"] >= 1.5).mean()),
        "persons": p, "orgs": o,
        "top_people": p["V2Persons"].value_counts().head(6), "top_orgs": o["Organizations"].value_counts().head(4),
        "stories": stories,
    }


def render_topic_card(fdf: pd.DataFrame, start, end, persons: pd.DataFrame, orgs: pd.DataFrame,
                      topic_stems: tuple, quotes: pd.DataFrame | None, zirve_aciklama: dict | None = None) -> None:
    """"Önce cevap": zaman şeridi ve zirveler, siteler, ton, aktörler, en çok yayılan haberler, alıntılar.
    Her kartın altında "Kanıtı göster" ile kartın dayandığı haberler listelenir."""
    facts = card_facts(fdf, start, end, persons, orgs, topic_stems)
    freq, unit, counts, peak_rows = facts["freq"], facts["unit"], facts["counts"], facts["peaks"]

    # --- Zaman şeridi ---
    card_title(f"Zaman şeridi · {unit} bazında haber sayısı")
    fig = go.Figure(go.Scatter(
        x=counts.index, y=counts.values, mode="lines", line=dict(color=MK["ink"], width=1.6),
        fill="tozeroy", fillcolor="rgba(23,33,38,0.08)", hovertemplate="%{x|%d.%m.%Y}: %{y} haber<extra></extra>",
    ))
    for i, ts, *_ in peak_rows:
        fig.add_annotation(x=ts, y=counts[ts], text=f"<b>{i}</b>", showarrow=True, arrowhead=0, ay=-26,
                           font=dict(color=MK["surface"], size=11), bgcolor=MK["red"], bordercolor=MK["red"])
    fig.update_layout(template=PLOTLY_TEMPLATE, height=260, showlegend=False, xaxis_title="", yaxis_title="")
    st.plotly_chart(fig, width="stretch")
    for i, ts, n, headline, _ in peak_rows:
        st.markdown(
            f'<div class="mk-rank"><span><b style="color:{MK["red"]}">{i}</b>&nbsp; {period_label(ts, freq)} — '
            f'{html.escape(headline[:140])}</span><b>{fmt_int(n)} haber</b></div>',
            unsafe_allow_html=True,
        )
        # L3: yapay zekâ özeti istenmişse o zirvenin açıklaması başlığın altına gelir
        aciklama = (zirve_aciklama or {}).get(i)
        if aciklama:
            st.markdown(
                f'<div class="mk-peak-ai"><span class="tag yorum">yorum</span>'
                f'{html.escape(aciklama, quote=False)}</div>',
                unsafe_allow_html=True,
            )
    if peak_rows:
        st.caption("Zirvenin başlığı, o dönemde en çok siteye yayılan haberdir."
                   + (" Altındaki **yorum** satırı, o dönemin başlıklarından üretilmiş tek cümlelik "
                      "özettir; tek bir habere değil zirvenin tamamına bakar." if zirve_aciklama else ""))
    data_note(start, end, "Yıllar arasında GDELT'in taradığı site sayısı değiştiği için ham sayılar doğrudan "
                          "karşılaştırılamaz; karşılaştırmada 'Karşılaştır' ekranındaki oranlı görünümü kullanın.")
    with st.expander("Kanıtı göster · zaman şeridi"):
        options = [f"{i}. zirve — {period_label(ts, freq)}" for i, ts, *_ in peak_rows] + ["Tüm haberler"]
        pick = st.selectbox("Hangi dönemin haberleri?", options, key="ev_peak")
        rows = fdf if pick == "Tüm haberler" else peak_rows[options.index(pick)][4]
        evidence_table(rows, "ev_peak_table")

    c1, c2, c3 = st.columns(3)
    # --- Siteler ---
    with c1:
        card_title("En çok yazan siteler")
        top_sites = facts["top_sites"]
        rank_list(top_sites.items())
        with st.expander("Kanıtı göster · siteler"):
            site = st.selectbox("Site", top_sites.index.tolist(), key="ev_site")
            evidence_table(fdf[fdf["SourceCommonName"] == site], "ev_site_table")
    # --- Ton ---
    with c2:
        card_title("Ton")
        avg = facts["avg_tone"]
        gauge = go.Figure(go.Indicator(
            mode="gauge+number", value=avg, number=dict(valueformat=".1f", font=dict(size=34)),
            gauge=dict(
                axis=dict(range=[-10, 10], tickwidth=1, tickcolor=MK["line_strong"]),
                bar=dict(color=MK["ink"], thickness=0.25),
                steps=[dict(range=[-10, -1.5], color="#e8cfc9"), dict(range=[-1.5, 1.5], color=MK["surface_deep"]),
                       dict(range=[1.5, 10], color="#cfe3dc")],
                borderwidth=0,
            ),
        ))
        gauge.update_layout(template=PLOTLY_TEMPLATE, height=170, margin=dict(l=20, r=20, t=10, b=0))
        st.plotly_chart(gauge, width="stretch")
        neg, pos = facts["neg"], facts["pos"]
        st.markdown(f"Genel dil **{tone_word(avg)}**. Haberlerin **%{neg * 100:.0f}**'i olumsuz, "
                    f"**%{pos * 100:.0f}**'i olumlu, kalanı nötr.")
        with st.expander("Kanıtı göster · ton"):
            pick = st.radio("Haberler", ["En olumsuz 50", "En olumlu 50"], horizontal=True, key="ev_tone")
            rows = fdf.nsmallest(50, "AvgTone") if pick.startswith("En olumsuz") else fdf.nlargest(50, "AvgTone")
            evidence_table(rows, "ev_tone_table")
    # --- Aktörler ---
    with c3:
        card_title("Birlikte anılan aktörler")
        p, o = facts["persons"], facts["orgs"]
        top_people, top_orgs = facts["top_people"], facts["top_orgs"]
        rank_list(list(top_people.items()) + [(f"{name} (kurum)", n) for name, n in top_orgs.items()])
        with st.expander("Kanıtı göster · aktörler"):
            actors = top_people.index.tolist() + top_orgs.index.tolist()
            if actors:
                actor = st.selectbox("Aktör", actors, key="ev_actor")
                src = p if actor in top_people.index else o
                col = "V2Persons" if actor in top_people.index else "Organizations"
                evidence_table(fdf.loc[fdf.index.isin(src.index[src[col] == actor])], "ev_actor_table")
    st.markdown(
        '<div class="mk-note"><b>Veri notu.</b> Kişi ve kurum adlarını GDELT, haberin İngilizce makine çevirisinden '
        'çıkarır; adlar zaman zaman bozuk veya eksik olabilir. Aranan konunun kendi adı listeden çıkarılmıştır.</div>',
        unsafe_allow_html=True,
    )

    c4, c5 = st.columns([1, 1])
    # --- En çok yayılan haberler ---
    with c4:
        card_title("En çok yayılan haberler")
        groups = facts["stories"]
        if groups.empty:
            st.caption("Birden fazla sitede yayımlanan (kopya) haber yok.")
        for gid, g in groups.iterrows():
            st.markdown(
                f'<div class="mk-rank"><span>{html.escape((g["baslik"] or "")[:110])}<br>'
                f'<small style="color:{MK["muted"]}">İlk: {html.escape(g["ilk"])} · {g["zaman"]:%d.%m.%Y %H:%M}'
                f'</small></span><b>{g["site"]} site</b></div>',
                unsafe_allow_html=True,
            )
        if not groups.empty:
            with st.expander("Kanıtı göster · yayılan haberler"):
                labels = [(g["baslik"] or "")[:80] for _, g in groups.iterrows()]
                pick = st.selectbox("Haber", labels, key="ev_story")
                gid = groups.index[labels.index(pick)]
                evidence_table(fdf[fdf["CopyGroup"] == gid], "ev_story_table")
    # --- Alıntılar ---
    with c5:
        card_title("Öne çıkan alıntılar")
        if quotes is None:
            st.caption("Alıntılar yalnızca bulut ve yerel arşiv aramalarında gösterilir.")
        else:
            best = top_quotes(quotes)
            if best.empty:
                st.caption(f"Bu haberlerde konuşmacısı belli bir alıntı bulunamadı. Alıntı verisi "
                           f"{QUOTES_SINCE:%d.%m.%Y} sonrasını kapsar.")
            for _, q in best.iterrows():
                speaker = (q["pre"] or "").strip()[-70:]
                st.markdown(
                    f'<div class="mk-quote">“{html.escape(q["quote"])}”</div>'
                    f'<div class="mk-quote-src">…{html.escape(speaker)} · '
                    f'<a href="{html.escape(q["url"])}" target="_blank">kaynak</a> · {fmt_int(q["haber"])} haberde</div>',
                    unsafe_allow_html=True,
                )
            st.markdown(
                f'<div class="mk-note"><b>Veri notu.</b> Alıntılar GDELT Quotation Graph\'tan gelir ve '
                f'{QUOTES_SINCE:%d.%m.%Y} sonrasını kapsar; konuşmacı, alıntının öncesindeki metinden okunur.</div>',
                unsafe_allow_html=True,
            )


def render_compare(terms: tuple, start, end, is_local: bool) -> None:
    """2–4 ifadeyi yan yana karşılaştırır: hacim (ham veya tüm Türkçe haberlere oranla), ton, siteler, ortaklık."""
    frames, capped = {}, []
    with st.spinner("İfadeler aranıyor..."):
        for term in terms:
            with search_slot():
                raw = local_fetch(term, start, end) if is_local else bq_fetch(term, start, end)[0]
                if len(raw) >= BQ_MAX_ROWS:
                    capped.append(term)
                frames[term] = preprocess(raw) if not raw.empty else pd.DataFrame()
                del raw
    if capped:
        st.warning(
            f"{', '.join(capped)}: sonuç {fmt_int(BQ_MAX_ROWS)} haberle sınırlandı (yalnızca en yeniler). Bu ifadeler "
            "için eski dönemler eksik kalır ve karşılaştırma yanıltıcı olur; tarih aralığını daraltın."
        )
    empty = [t for t, f in frames.items() if f.empty]
    if empty:
        st.warning("Haber bulunamayan ifadeler: " + ", ".join(empty))
    frames = {t: f for t, f in frames.items() if not f.empty}
    if not frames:
        st.stop()

    st.markdown(
        f'<div class="mk-kicker">Karşılaştırma</div><p class="mk-headline">{html.escape(" · ".join(frames))}</p>'
        f'<p class="mk-tagline">{as_date(start):%d.%m.%Y} – {as_date(end):%d.%m.%Y}</p>',
        unsafe_allow_html=True,
    )
    cols = st.columns(len(frames))
    for col, (term, f) in zip(cols, frames.items()):
        col.metric(term, fmt_int(len(f)), help="Tekilleştirilmiş haber sayısı")
        col.caption(f"{fmt_int(f['SourceCommonName'].nunique())} site · ortalama ton {f['AvgTone'].mean():.1f} "
                    f"({tone_word(f['AvgTone'].mean())})")

    freq, unit = auto_freq(start, end)
    normalize = st.toggle("Tüm Türkçe haberlere oranla (10 bin haberde)", value=True,
                          help="Yıllar arasında GDELT'in taradığı site sayısı değiştiği için oranlı görünüm "
                               "dönemleri daha adil karşılaştırır.")
    series = []
    for term, f in frames.items():
        c = bucket_of(f["pub_timestamp"], freq).value_counts().sort_index()
        series.append(c.rename(term))
    vol = pd.concat(series, axis=1, sort=True).astype(float).fillna(0.0)
    full = pd.date_range(vol.index.min(), vol.index.max(), freq=freq)
    vol = vol.reindex(full, fill_value=0)
    if normalize:
        totals = daily_totals(is_local)
        totals = totals.groupby(bucket_of(totals.index.to_series(), freq).to_numpy()).sum()
        vol = vol.div(totals.reindex(vol.index), axis=0) * 10_000
    card_title(f"Görünürlük · {unit} bazında" + (" (10 bin Türkçe haberde)" if normalize else " (haber sayısı)"))
    fig = go.Figure()
    for term in vol.columns:
        fig.add_trace(go.Scatter(x=vol.index, y=vol[term], mode="lines", name=term, line=dict(width=1.8)))
    fig.update_layout(template=PLOTLY_TEMPLATE, height=320, xaxis_title="", yaxis_title="",
                      legend=dict(orientation="h", y=1.08))
    st.plotly_chart(fig, width="stretch")

    tone_freq = "MS" if freq != "D" else "W-MON"
    card_title("Ton · " + ("ay" if tone_freq == "MS" else "hafta") + " bazında ortalama")
    fig_t = go.Figure()
    for term, f in frames.items():
        t = f.groupby(bucket_of(f["pub_timestamp"], tone_freq))["AvgTone"].mean()
        fig_t.add_trace(go.Scatter(x=t.index, y=t.values, mode="lines+markers", name=term, marker=dict(size=4)))
    fig_t.add_hline(y=0, line=dict(color=MK["line_strong"], width=1))
    fig_t.update_layout(template=PLOTLY_TEMPLATE, height=280, xaxis_title="", yaxis_title="Ortalama ton",
                        legend=dict(orientation="h", y=1.1))
    st.plotly_chart(fig_t, width="stretch")

    c1, c2 = st.columns([3, 2])
    with c1:
        card_title("Siteler · her ifadenin haberlerindeki payı (%)")
        combined = pd.concat([f.assign(Ifade=t) for t, f in frames.items()])
        top_sites = combined["SourceCommonName"].value_counts().head(10).index
        share = (
            combined[combined["SourceCommonName"].isin(top_sites)]
            .groupby(["SourceCommonName", "Ifade"]).size()
            .div(combined.groupby("Ifade").size(), level="Ifade").mul(100).rename("Pay").reset_index()
        )
        fig_s = px.bar(share, x="Pay", y="SourceCommonName", color="Ifade", barmode="group", orientation="h")
        fig_s.update_layout(template=PLOTLY_TEMPLATE, height=420, yaxis_title="", xaxis_title="%",
                            legend_title="", yaxis=dict(categoryorder="total ascending"))
        st.plotly_chart(fig_s, width="stretch")
    with c2:
        card_title("Ortak haberler")
        urls = {t: set(f["DocumentIdentifier"]) for t, f in frames.items()}
        names = list(urls)
        pairs = [(a, b, len(urls[a] & urls[b])) for i, a in enumerate(names) for b in names[i + 1:]]
        rank_list([(f"{a} + {b}", n) for a, b, n in pairs])
        st.caption("İki ifadenin aynı haberde birlikte geçtiği haber sayısı.")
    data_note(start, end)
    with st.expander("Kanıtı göster · karşılaştırma"):
        term = st.selectbox("İfade", list(frames), key="ev_cmp")
        evidence_table(frames[term], "ev_cmp_table")


# --------------------------------------------------------------------------------
# DEĞİŞİKLİK DEDEKTİFİ (GDELT GDG: yayından sonra değişen sayfalar)
# --------------------------------------------------------------------------------
# Sayfa değişikliği (GDG) verisinin başladığı gün
PAGE_CHANGES_SINCE = date(2025, 9, 28)
# GDG sayfaları yayından günler sonra yeniden kontrol eder; aralığın bitişinden sonraki bu kadar gün de okunur
PAGE_CHANGES_LOOKAHEAD_DAYS = 31
# Metin değişikliğinde en az bu oranda (%) karakter değişmiş olmalı; daha azı çoğunlukla sayaç/tarih güncellemesidir
TEXT_CHANGE_MIN_PERC = 5.0
GDG_COLUMNS = ["page_url", "status", "page_title", "title_new", "perc_changedchars", "changes",
               "fetchdate_orig", "fetchdate_check"]


@st.cache_data(ttl=3600, max_entries=CACHE_MAX_ENTRIES, show_spinner=False)
def fetch_page_changes(urls_key: str, _urls: tuple, start, end, is_local: bool) -> pd.DataFrame:
    """Verilen haberlerin GDG kontrolleri (değişmeyenler dahil; kontrol edilen haber sayısı için gerekir).
    Yerelde arşivden, yayında Storage Read API ile okunur. En fazla 800 haber (yayında)."""
    urls = _urls  # önbellek anahtarı urls_key'dir; uzun URL listesi her çizimde hash'lenmez
    start = max(as_date(start), PAGE_CHANGES_SINCE)
    stop = min(as_date(end) + timedelta(days=PAGE_CHANGES_LOOKAHEAD_DAYS), date.today())
    if not urls or start > stop:
        return pd.DataFrame(columns=GDG_COLUMNS)
    if is_local:
        import duckdb

        folder = os.path.join(ARCHIVE_DIR, "page_changes")
        files = [os.path.join(folder, f"{start + timedelta(days=i):%Y-%m-%d}.parquet")
                 for i in range((stop - start).days + 1)]
        files = [f for f in files if os.path.exists(f)]
        if not files:
            return pd.DataFrame(columns=GDG_COLUMNS)
        with duckdb.connect() as con:
            con.register("wanted", pd.DataFrame({"u": list(urls)}))
            return con.execute(
                f"SELECT {', '.join(GDG_COLUMNS)} FROM read_parquet(?) WHERE page_url IN (SELECT u FROM wanted)",
                [files],
            ).df()
    from google.cloud.bigquery_storage import types

    table = f"{BQ_PROJECT}.gdelt_tr.page_changes".replace(".", "/datasets/", 1).replace(".", "/tables/", 1)
    url_list = ", ".join(bq_literal(u) for u in list(urls)[:800])
    reader = bq_reader()
    session = reader.create_read_session(
        parent=f"projects/{BQ_PROJECT}",
        read_session=types.ReadSession(
            table=f"projects/{table}",
            data_format=types.DataFormat.ARROW,
            read_options=types.ReadSession.TableReadOptions(
                selected_fields=GDG_COLUMNS,
                row_restriction=f"check_day BETWEEN DATE '{start:%Y-%m-%d}' AND DATE '{stop:%Y-%m-%d}' "
                                f"AND page_url IN ({url_list})",
            ),
        ),
        max_stream_count=4,
    )
    frames = [reader.read_rows(x.name).to_dataframe(session) for x in session.streams]
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=GDG_COLUMNS)


# GDELT Events (CAMEO): Türkçe haberlerde anılan olaylar (event_mentions -> events). Veri bu tarihten sonrasını kapsar
EVENTS_SINCE = date(2025, 9, 28)
# Olay, anıldığı haberden önceki günlerde eklenmiş olabilir; olay dosyaları aralığın bu kadar gün öncesinden okunur
EVENTS_LOOKBACK_DAYS = 7
# Dağılım ancak yeterli haber olaya bağlıysa gösterilir. GDELT olayları çoğunlukla siyaset, diplomasi, güvenlik ve
# protesto haberlerinden çıkarır (Eylül 2026 ölçümü: tüm haberlerin %16'sı, askeri/diplomasi temalı haberlerin ~%40'ı,
# enflasyon temalı haberlerin %0'ı olaya bağlı)
EVENTS_MIN_ARTICLES = 50
EVENTS_MIN_SHARE = 10.0
# Yayında olay tablosu okunurken filtreye en fazla bu kadar olay kimliği yazılır
EVENTS_BQ_MAX_IDS = 5000
EVENT_COLUMNS = ["url", "GLOBALEVENTID", "EventRootCode", "QuadClass", "Actor1Name", "Actor2Name"]
# CAMEO ana kodları (EventRootCode). QuadClass: 01–05 sözlü işbirliği, 06–08 fiilî işbirliği, 09–13 sözlü çatışma,
# 14–20 fiilî çatışma
CAMEO_ROOTS = {
    "01": "Açıklama yapma", "02": "Çağrı", "03": "İşbirliği niyeti", "04": "Görüşme, istişare",
    "05": "Diplomatik işbirliği", "06": "Fiilî işbirliği", "07": "Yardım", "08": "Geri adım, taviz",
    "09": "Soruşturma", "10": "Talep", "11": "Eleştiri, suçlama", "12": "Reddetme", "13": "Tehdit",
    "14": "Protesto", "15": "Güç gösterisi", "16": "İlişkileri azaltma", "17": "Baskı, zor kullanma",
    "18": "Saldırı", "19": "Çatışma", "20": "Kitlesel şiddet",
}
QUAD_CLASSES = {1: "Sözlü işbirliği", 2: "Fiilî işbirliği", 3: "Sözlü çatışma", 4: "Fiilî çatışma"}
QUAD_COLORS = {"Sözlü işbirliği": MK["cyan"], "Fiilî işbirliği": MK["green"], "Sözlü çatışma": MK["amber"],
               "Fiilî çatışma": MK["red"]}


def bq_table_read(table_name: str, fields: list, restriction: str) -> pd.DataFrame:
    """gdelt_tr'deki bir tablodan Storage Read API ile okuma (sorgu kotasına sayılmaz)."""
    from google.cloud.bigquery_storage import types

    table = f"{BQ_PROJECT}.gdelt_tr.{table_name}".replace(".", "/datasets/", 1).replace(".", "/tables/", 1)
    reader = bq_reader()
    session = reader.create_read_session(
        parent=f"projects/{BQ_PROJECT}",
        read_session=types.ReadSession(
            table=f"projects/{table}",
            data_format=types.DataFormat.ARROW,
            read_options=types.ReadSession.TableReadOptions(selected_fields=fields, row_restriction=restriction),
        ),
        max_stream_count=4,
    )
    frames = [reader.read_rows(x.name).to_dataframe(session) for x in session.streams]
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=fields)


@st.cache_data(ttl=3600, max_entries=CACHE_MAX_ENTRIES, show_spinner=False)
def fetch_events(urls_key: str, _urls: tuple, start, end, is_local: bool) -> pd.DataFrame:
    """Verilen haberlerde anılan GDELT olayları: haber başına olay (url, olay kimliği, CAMEO ana kodu, QuadClass,
    aktörler). Haber ile olay, event_mentions'taki haber adresiyle (MentionIdentifier) eşleşir. Yerelde arşivden,
    yayında Storage Read API ile okunur (en fazla 800 haber). Olaylar yalnızca EVENTS_SINCE sonrası için vardır."""
    urls = _urls  # önbellek anahtarı urls_key'dir; uzun URL listesi her çizimde hash'lenmez
    start = max(as_date(start), EVENTS_SINCE)
    stop = min(as_date(end) + timedelta(days=1), date.today())
    if not urls or start > stop:
        return pd.DataFrame(columns=EVENT_COLUMNS)
    ev_start = max(start - timedelta(days=EVENTS_LOOKBACK_DAYS), EVENTS_SINCE)
    if is_local:
        import duckdb

        def files(folder, a, b):
            paths = [os.path.join(ARCHIVE_DIR, folder, f"{a + timedelta(days=i):%Y-%m-%d}.parquet")
                     for i in range((b - a).days + 1)]
            return [p for p in paths if os.path.exists(p)]

        m_files, e_files = files("event_mentions", start, stop), files("events", ev_start, stop)
        if not m_files or not e_files:
            return pd.DataFrame(columns=EVENT_COLUMNS)
        with duckdb.connect() as con:
            con.register("wanted", pd.DataFrame({"u": list(urls)}))
            return con.execute(
                "SELECT DISTINCT m.MentionIdentifier AS url, e.GLOBALEVENTID, e.EventRootCode, e.QuadClass, "
                "e.Actor1Name, e.Actor2Name FROM read_parquet(?) m JOIN read_parquet(?) e USING (GLOBALEVENTID) "
                "WHERE m.MentionIdentifier IN (SELECT u FROM wanted)",
                [m_files, e_files],
            ).df()
    url_list = ", ".join(bq_literal(u) for u in list(urls)[:800])
    mentions = bq_table_read(
        "event_mentions", ["MentionIdentifier", "GLOBALEVENTID"],
        f"mention_day BETWEEN DATE '{start:%Y-%m-%d}' AND DATE '{stop:%Y-%m-%d}' "
        f"AND MentionIdentifier IN ({url_list})",
    ).drop_duplicates()
    if mentions.empty:
        return pd.DataFrame(columns=EVENT_COLUMNS)
    ids = mentions["GLOBALEVENTID"].drop_duplicates().head(EVENTS_BQ_MAX_IDS)
    events = bq_table_read(
        "events", EVENT_COLUMNS[1:],
        f"event_day BETWEEN DATE '{ev_start:%Y-%m-%d}' AND DATE '{stop:%Y-%m-%d}' "
        f"AND GLOBALEVENTID IN ({', '.join(str(int(i)) for i in ids)})",
    )
    return (mentions.rename(columns={"MentionIdentifier": "url"})
            .merge(events, on="GLOBALEVENTID")[EVENT_COLUMNS].drop_duplicates())


def summarize_changes(gdg: pd.DataFrame) -> tuple:
    """GDG kayıtlarından (kontrol edilen URL sayısı, başlık değişiklikleri, metin değişiklikleri).
    Aynı değişiklik farklı kontrollerde tekrar kaydedildiği için ilk kaydı tutulur."""
    if gdg.empty:
        return 0, pd.DataFrame(), pd.DataFrame()
    checked = gdg["page_url"].nunique()
    titles = gdg[(gdg["status"] == "PAGE_TITLECHANGE") & gdg["title_new"].notna()].copy()
    titles["old"] = titles["page_title"].fillna("").map(clean_title)
    titles["new"] = titles["title_new"].fillna("").map(clean_title)
    titles = titles[(titles["new"].str.strip() != "") & (titles["old"].str.strip() != "")]
    # Yalnızca site adı eki değişenleri ("... | NTV Haber", "...Güncel Haberler") ele: asıl başlıklar aynıysa ya da
    # biri diğerinin başıysa ve fark kısa ise değişiklik sayılmaz
    old_key = titles["old"].map(lambda t: re.sub(r"\W+", " ", fold_tr(title_core(t))).strip())
    new_key = titles["new"].map(lambda t: re.sub(r"\W+", " ", fold_tr(title_core(t))).strip())
    suffix_only = [
        a == b or ((a.startswith(b) or b.startswith(a)) and abs(len(a) - len(b)) <= 40)
        for a, b in zip(old_key, new_key)
    ]
    titles = titles[~pd.Series(suffix_only, index=titles.index)].assign(old_key=old_key, new_key=new_key)
    titles = (titles.sort_values("fetchdate_check").drop_duplicates(["page_url", "new_key"])
              .drop_duplicates(["old_key", "new_key"]))

    texts = gdg[(gdg["status"] == "PAGE_TEXTCHANGE") & (gdg["perc_changedchars"] >= TEXT_CHANGE_MIN_PERC)].copy()
    has_words = texts["changes"].map(
        lambda ch: any(re.search(r"[^\W\d_]{3}", (c.get("from") or "") + (c.get("to") or "")) for c in list(ch))
        if ch is not None else False
    )
    # Boş tabloda map() object tipli boş seri döner; loc ile bool'a çevrilmeden süzülürse kolonlar kaybolur
    texts = texts.loc[has_words.astype(bool)].sort_values("perc_changedchars", ascending=False)
    texts = texts.drop_duplicates("page_url")
    return checked, titles, texts


def diff_html(old: str, new: str) -> tuple:
    """Kelime düzeyinde fark: (eski metin, silinenler kırmızı çizili), (yeni metin, eklenenler yeşil)."""
    import difflib

    a, b = old.split(), new.split()
    left, right = [], []
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(a=a, b=b, autojunk=False).get_opcodes():
        old_part = html.escape(" ".join(a[i1:i2]))
        new_part = html.escape(" ".join(b[j1:j2]))
        if op == "equal":
            left.append(old_part)
            right.append(new_part)
        else:
            if old_part:
                left.append(f'<del style="background:#f3dcd8;color:{MK["red"]}">{old_part}</del>')
            if new_part:
                right.append(f'<ins style="background:#d6ebe3;color:{MK["green"]};text-decoration:none">'
                             f'{new_part}</ins>')
    return " ".join(left), " ".join(right)


# --------------------------------------------------------------------------------
# YANKI HARİTASI (bir haberin siteler arasında yayılımı)
# --------------------------------------------------------------------------------
def echo_summary(group: pd.DataFrame) -> dict:
    """Bir kopya grubunun yayılım özeti: ilk site/zaman, 1 saat ve 24 saat içindeki site sayısı."""
    g = group.sort_values("pub_timestamp")
    first = g.iloc[0]
    delay = (g["pub_timestamp"] - first["pub_timestamp"]).dt.total_seconds() / 3600
    by_site = g.assign(delay=delay).groupby("SourceCommonName")["delay"].min().sort_values()
    return {
        "first_site": first["SourceCommonName"], "first_time": first["pub_timestamp"],
        "title": first["PageTitle"] or url_slug(first["DocumentIdentifier"]),
        "sites": len(by_site), "within_1h": int((by_site <= 1).sum()), "within_24h": int((by_site <= 24).sum()),
        "by_site": by_site,
    }


# --------------------------------------------------------------------------------
# BUGÜN GÜNDEMDE (arama yapılmadan önce ana ekranda: son günün öne çıkanları)
# --------------------------------------------------------------------------------
TRENDING_BASELINE_DAYS = 28
TRENDING_MIN_COUNT = 15
TRENDING_MIN_RATIO = 3.0


# Günün panosundaki kategori sütunları: "Genel" çerçeve setinden (kısa ad, çerçeve adı)
BOARD_CATEGORIES = [("Politika", "Siyaset / seçim"), ("Ekonomi", "Ekonomi"), ("Dünya", "Dış ilişkiler"),
                    ("Güvenlik", "Güvenlik / terör"), ("Yargı", "Yargı / soruşturma")]


# Süresi dolunca eski pano hemen gösterilir, yenisi arka planda hazırlanır (ilk ziyaretçi ~8 sn beklemez)
@st.cache_data(ttl=6 * 3600, refresh_mode="background", show_spinner=False)
def trending_data(day, is_local: bool) -> dict:
    """"Günün medya panosu" verisi (son gün = day):
    - özet: haber sayısı, ortalama ton
    - isimler: önceki 28 güne göre sıçrayan kişiler ve 29 günlük seyirleri
    - hikâyeler: başlığının kelime kökleri aynı olan haber grupları (en çok siteye yayılanlar), çerçeveleri ve
      manşet için yayılım gecikmeleri
    - değişen başlıklar: o gün GDELT'in yakaladığı, yalnızca site eki değişmeyen başlık değişiklikleri
    Yerelde arşivden, yayında Storage Read API ile okunur (okuma ücretsiz; birkaç MB internet çıkışı)."""
    day = as_date(day)
    base_start = day - timedelta(days=TRENDING_BASELINE_DAYS)
    cols = ["pub_timestamp", "SourceCommonName", "DocumentIdentifier", "PageTitle", "Themes", "V2Tone",
            "V2Persons", "TranslationInfo"]
    gdg_cols = GDG_COLUMNS
    if is_local:
        import duckdb

        def read(folder, days, fields, where=""):
            files = [os.path.join(ARCHIVE_DIR, folder, f"{d:%Y-%m-%d}.parquet") for d in days]
            files = [f for f in files if os.path.exists(f)]
            if not files:
                return pd.DataFrame(columns=fields)
            with duckdb.connect() as con:
                return con.execute(f"SELECT {', '.join(fields)} FROM read_parquet(?) {where}", [files]).df()

        today = read("articles", [day], cols)
        base = read("articles", [base_start + timedelta(days=i) for i in range(TRENDING_BASELINE_DAYS)],
                    ["gkg_day", "V2Persons"])
        gdg = read("page_changes", [day], gdg_cols, "WHERE status = 'PAGE_TITLECHANGE'")
    else:
        from google.cloud.bigquery_storage import types

        reader = bq_reader()

        def read_bq(table_name, restriction, fields):
            table = f"{BQ_PROJECT}.gdelt_tr.{table_name}".replace(".", "/datasets/", 1).replace(".", "/tables/", 1)
            session = reader.create_read_session(
                parent=f"projects/{BQ_PROJECT}",
                read_session=types.ReadSession(
                    table=f"projects/{table}", data_format=types.DataFormat.ARROW,
                    read_options=types.ReadSession.TableReadOptions(selected_fields=fields,
                                                                    row_restriction=restriction),
                ),
                max_stream_count=4,
            )
            parts = [reader.read_rows(x.name).to_dataframe(session) for x in session.streams]
            return pd.concat(parts, ignore_index=True) if parts else pd.DataFrame(columns=fields)

        today = read_bq("articles", f"gkg_day = DATE '{day:%Y-%m-%d}'", cols)
        base = read_bq("articles", f"gkg_day BETWEEN DATE '{base_start:%Y-%m-%d}' AND "
                                   f"DATE '{day - timedelta(days=1):%Y-%m-%d}'", ["gkg_day", "V2Persons"])
        gdg = read_bq("page_changes", f"check_day = DATE '{day:%Y-%m-%d}' AND status = 'PAGE_TITLECHANGE'",
                      gdg_cols)
    empty = {"people": pd.DataFrame(), "stories": pd.DataFrame(), "hero": None, "changes": pd.DataFrame(),
             "count": 0, "tone": None}
    if today.empty:
        return empty
    df = preprocess(today)
    df["Frames"] = assign_frames(df[["TitleCore", "DocumentIdentifier"]], frame_set_key(FRAME_SETS["Genel"]))

    # --- Sıçrayan isimler ---
    people_today = explode_list_column(df[["V2Persons"]], "V2Persons")["V2Persons"].value_counts()
    base_long = explode_list_column(base.fillna({"V2Persons": ""}), "V2Persons")
    base_long["day"] = base.loc[base_long.index, "gkg_day"].to_numpy()
    base_counts = base_long["V2Persons"].value_counts()
    base_daily = base_counts.reindex(people_today.index, fill_value=0) / TRENDING_BASELINE_DAYS
    ratio = people_today / base_daily.clip(lower=0.5)
    candidates = (
        pd.DataFrame({"n": people_today, "ratio": ratio})
        .query("n >= @TRENDING_MIN_COUNT and ratio >= @TRENDING_MIN_RATIO")
        .sort_values("n", ascending=False).head(30)
    )
    # Makine çevirisinden bozuk çıkan adları (Türkçe harfleri düşmüş soyadları) ve kişi sanılan yer adlarını elemek için ad,
    # o günün başlıklarında da aynen (Türkçe karakterler indirgenmiş) en az 3 kez geçmeli
    folded_titles = df["TitleCore"].map(fold_tr)
    in_titles = [
        int(folded_titles.str.contains(r"\b" + re.escape(fold_tr(name)) + r"\b", regex=True).sum())
        for name in candidates.index
    ]
    people = candidates.assign(titles=in_titles).query("titles >= 3").head(8)
    days = [base_start + timedelta(days=i) for i in range(TRENDING_BASELINE_DAYS)]
    daily = (base_long[base_long["V2Persons"].isin(people.index)]
             .assign(day=lambda x: pd.to_datetime(x["day"]).dt.date)
             .groupby(["V2Persons", "day"]).size())
    people["seyir"] = [
        [int(daily.get((name, d), 0)) for d in days] + [int(people.loc[name, "n"])] for name in people.index
    ]

    # --- Hikâyeler: başlığının kelime kökleri aynı olan haberler (noktalama, büyük/küçük harf, site eki farkları
    # tolere edilir). Konu kartındaki benzer başlık araması bir günlük veride ana ekran için fazla yavaş.
    key = df["TitleCore"].map(lambda t: tuple(sorted(title_signature(t))))
    grouped = df[key.map(len) >= 3].assign(key=key).sort_values("pub_timestamp")
    stories = (
        grouped.groupby("key")
        .agg(baslik=("PageTitle", "first"), ilk=("SourceCommonName", "first"), url=("DocumentIdentifier", "first"),
             zaman=("pub_timestamp", "first"), frames=("Frames", "first"), site=("SourceCommonName", "nunique"))
        .query("site > 2").sort_values(["site", "zaman"], ascending=[False, True])
    )
    hero = None
    if not stories.empty:
        top_key = stories.index[0]
        hero = {**stories.iloc[0].to_dict(), **echo_summary(grouped[grouped["key"] == top_key])}

    # --- Değişen başlıklar: silinen büyük harfli kelime (çoğunlukla isim) en güçlü sinyal; rutin maç
    # güncellemeleri ("canlı izle" -> "maç sonucu") geri itilir. Başlığı sitenin genel başlığına dönen sayfalar
    # (haber kaldırılıp ana sayfaya yönlenmiş olabilir) ayrıca işaretlenir.
    _, titles, _ = summarize_changes(gdg)
    if not titles.empty:
        def score(row):
            old_w, new_w = set(row["old"].split()), set(row["new"].split())
            removed, added = old_w - new_w, new_w - old_w
            names_removed = sum(1 for w in removed if w[:1].isupper())
            routine = re.search(r"\b(mac|canli|izle|gol|skor|kac kac|hangi kanalda)", fold_tr(row["old"]))
            return 3 * names_removed + len(removed) + len(added) - (40 if routine else 0)

        def looks_removed(row):
            common = set(fold_tr(row["old"]).split()) & set(fold_tr(row["new"]).split())
            generic = re.search(r"(son dakika haber|guncel haber|haberleri$|haber sitesi|anasayfa|ana sayfa)",
                                fold_tr(row["new"]))
            routine = re.search(r"\b(mac|canli|izle|gol|skor)", fold_tr(row["old"]))
            return bool(generic) and len(common) <= 1 and not routine

        titles = titles[(titles["old"].str.len() >= 20) & (titles["new"].str.len() >= 20)].copy()
        titles["site"] = titles["page_url"].map(lambda u: re.sub(r"^https?://(www\.)?", "", u).split("/")[0])
        titles["score"] = titles.apply(score, axis=1)
        titles["removed"] = titles.apply(looks_removed, axis=1)
        titles = titles.sort_values(["removed", "score"], ascending=[False, False]).head(6)
    return {"people": people, "stories": stories, "hero": hero, "changes": titles, "count": len(df),
            "tone": float(df["AvgTone"].mean())}


# --------------------------------------------------------------------------------
# PDF RAPOR (reportlab; fontlar assets/fonts, OFL lisanslı)
# --------------------------------------------------------------------------------
FONT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "fonts")


@st.cache_resource(show_spinner=False)
def pdf_fonts() -> bool:
    """Marka fontlarını reportlab'e bir kez kaydeder (Türkçe karakterler için TTF gerekir)."""
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    for name, file in [("MkSerif", "SourceSerif4-SemiBold.ttf"), ("MkSerifReg", "SourceSerif4-Regular.ttf"),
                       ("MkSans", "IBMPlexSans-Regular.ttf"), ("MkSansBold", "IBMPlexSans-SemiBold.ttf"),
                       ("MkMono", "IBMPlexMono-Regular.ttf")]:
        pdfmetrics.registerFont(TTFont(name, os.path.join(FONT_DIR, file)))
    pdfmetrics.registerFontFamily("MkSans", normal="MkSans", bold="MkSansBold", italic="MkSans",
                                  boldItalic="MkSansBold")
    return True


def build_pdf_report(title: str, start, end, fdf: pd.DataFrame, facts: dict, quotes: pd.DataFrame | None,
                     changes: tuple | None, source_note: str, coverage: dict | None = None,
                     zirve_aciklama: dict | None = None) -> bytes:
    """Konu kartının markalı PDF raporu. `facts` card_facts'ten gelir; ekrandakiyle aynı rakamlar."""
    from xml.sax.saxutils import escape

    from reportlab.graphics.shapes import Circle, Drawing, Line, Rect, String
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    pdf_fonts()
    ink, muted, red, line = (colors.HexColor(MK[k]) for k in ("ink", "muted", "red", "line_strong"))
    green, surface = colors.HexColor(MK["green"]), colors.HexColor(MK["surface_deep"])
    st_kicker = ParagraphStyle("k", fontName="MkMono", fontSize=8, textColor=red, leading=11, spaceAfter=2)
    st_title = ParagraphStyle("t", fontName="MkSerif", fontSize=26, textColor=ink, leading=30, spaceAfter=4)
    st_sub = ParagraphStyle("s", fontName="MkSans", fontSize=10, textColor=muted, leading=14, spaceAfter=10)
    st_h = ParagraphStyle("h", fontName="MkMono", fontSize=8.5, textColor=muted, leading=12, spaceBefore=12,
                          spaceAfter=5, borderPadding=(0, 0, 3, 0))
    st_body = ParagraphStyle("b", fontName="MkSans", fontSize=9.5, textColor=ink, leading=13.5)
    st_small = ParagraphStyle("sm", fontName="MkSans", fontSize=8, textColor=muted, leading=11)
    st_quote = ParagraphStyle("q", fontName="MkSerifReg", fontSize=10.5, textColor=ink, leading=14.5,
                              leftIndent=8, borderPadding=(0, 0, 0, 6), spaceBefore=6)
    st_note = ParagraphStyle("n", parent=st_small, backColor=colors.HexColor("#f4efe6"), borderPadding=5,
                             spaceBefore=6, spaceAfter=6)
    width = A4[0] - 36 * mm

    def h(text):
        upper = text.replace("i", "İ").replace("ı", "I").upper()
        return [Paragraph(escape(upper), st_h), Table([[""]], colWidths=[width], rowHeights=[0.6],
                                                            style=[("LINEABOVE", (0, 0), (-1, -1), 0.6, line)])]

    def p(text, style=st_body):
        return Paragraph(escape(str(text)), style)

    story = [
        Paragraph(f"{APP_NAME_UPPER} — KONU RAPORU", st_kicker),
        Paragraph(escape(title), st_title),
        Paragraph(escape(f"{as_date(start):%d.%m.%Y} – {as_date(end):%d.%m.%Y} · {source_note} · "
                         f"Hazırlanma: {datetime.now():%d.%m.%Y %H:%M}"), st_sub),
    ]
    kpis = [("HABER", fmt_int(len(fdf))), ("TEKİL İÇERİK", fmt_int(int((~fdf["IsCopy"]).sum()))),
            ("SİTE", fmt_int(fdf["SourceCommonName"].nunique())),
            ("ORTALAMA TON", f"{facts['avg_tone']:.1f} ({tone_word(facts['avg_tone'])})")]
    kpi_table = Table(
        [[Paragraph(k, st_small) for k, _ in kpis],
         [Paragraph(escape(v), ParagraphStyle("kv", fontName="MkSerif", fontSize=15, leading=18, textColor=ink))
          for _, v in kpis]],
        colWidths=[width / 4] * 4,
    )
    kpi_table.setStyle(TableStyle([("LINEABOVE", (0, 0), (-1, 0), 2, ink), ("BACKGROUND", (0, 0), (-1, -1), surface),
                                   ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 6)]))
    story += [kpi_table, Spacer(1, 6)]

    # --- Veri kapsamı ve boşluklar (rapor eksik veriyi saklamasın diye en üstte) ---
    if coverage is not None:
        level_color = {"Yetersiz": MK["red"], "Sınırlı": MK["amber_text"], "Yeterli": MK["green"]}[coverage["level"]]
        story += h("Veri kapsamı ve boşluklar")
        story.append(Paragraph(f'Kapsam değerlendirmesi: <font color="{level_color}"><b>{coverage["level"]}</b></font>',
                               st_body))
        for severity, text in coverage["warnings"]:
            story.append(Paragraph(f"• <b>{escape(severity.capitalize())}:</b> {escape(text)}", st_small))
        if not coverage["warnings"]:
            story.append(p("Bu aramada belirgin bir kapsam boşluğu görünmüyor.", st_small))
        extra = coverage.get("extra", pd.DataFrame())
        if len(extra):
            counts = ", ".join(f"{s} {n}" for s, n in extra["site"].value_counts().head(8).items())
            story.append(p(f"Arşiv dışı kaynaklarda (yayınların site haritaları ve RSS akışları) bu aralıkta "
                           f"{fmt_int(len(extra))} haber daha var: {counts}. Bu kayıtlar rapordaki rakamlara "
                           "katılmamıştır.", st_small))

    # --- Zaman şeridi ---
    counts = facts["counts"]
    story += h(f"Zaman şeridi · {facts['unit']} bazında haber sayısı")
    d = Drawing(width, 150)
    x0, y0, w, hgt = 28, 18, width - 36, 118
    ymax = max(float(counts.max()), 1.0) * 1.15
    n = max(len(counts) - 1, 1)
    xs = [x0 + i / n * w for i in range(len(counts))]
    ys = [y0 + float(v) / ymax * hgt for v in counts.values]
    d.add(Line(x0, y0, x0 + w, y0, strokeColor=line, strokeWidth=0.6))
    for frac in (0.5, 1.0):
        yy = y0 + frac * hgt / 1.15
        d.add(Line(x0, yy, x0 + w, yy, strokeColor=colors.HexColor(MK["line"]), strokeWidth=0.4))
        d.add(String(x0 - 4, yy - 3, fmt_int(round(ymax / 1.15 * frac)), fontName="MkSans", fontSize=6.5,
                     fillColor=muted, textAnchor="end"))
    for i in range(1, len(xs)):
        d.add(Line(xs[i - 1], ys[i - 1], xs[i], ys[i], strokeColor=ink, strokeWidth=1))
    for i in range(0, len(counts), max(1, len(counts) // 6)):
        d.add(String(xs[i], y0 - 11, period_label(counts.index[i], facts["freq"]).replace(" haftası", ""),
                     fontName="MkSans", fontSize=6.5, fillColor=muted, textAnchor="middle"))
    positions = {ts: k for k, ts in enumerate(counts.index)}
    for i, ts, *_ in facts["peaks"]:
        k = positions[ts]
        d.add(Circle(xs[k], ys[k] + 9, 5.5, fillColor=red, strokeColor=red))
        d.add(String(xs[k], ys[k] + 6.6, str(i), fontName="MkSansBold", fontSize=7, fillColor=colors.white,
                     textAnchor="middle"))
    story.append(d)
    for i, ts, cnt, headline, _ in facts["peaks"]:
        story.append(Paragraph(
            f'<font color="{MK["red"]}"><b>{i}</b></font>&nbsp; {escape(period_label(ts, facts["freq"]))} — '
            f'{escape(headline[:150])} <font color="{MK["muted"]}">({fmt_int(cnt)} haber)</font>', st_body))
        # L3: zirve açıklaması (yapay zekâ özeti istenmişse); kanıt etiketiyle
        aciklama = (zirve_aciklama or {}).get(i)
        if aciklama:
            story.append(Paragraph(
                f'<font color="{MK["amber_text"]}">YORUM</font>&nbsp; {escape(aciklama)}',
                ParagraphStyle("zirve", parent=st_small, leftIndent=14, spaceAfter=3)))
    if facts["peaks"]:
        story.append(p("Zirvenin başlığı, o dönemde en çok siteye yayılan haberdir.", st_small))

    # --- Siteler ve ton ---
    sites = facts["top_sites"]
    ds = Drawing(width * 0.55, 14 * len(sites) + 4)
    top_n = max(int(sites.max()), 1) if len(sites) else 1
    for k, (name, cnt) in enumerate(sites.items()):
        yy = 14 * (len(sites) - 1 - k) + 2
        ds.add(String(0, yy + 2, name[:28], fontName="MkSans", fontSize=7.5, fillColor=ink))
        ds.add(Rect(110, yy, (width * 0.55 - 150) * cnt / top_n, 9, fillColor=ink, strokeColor=None))
        ds.add(String(114 + (width * 0.55 - 150) * cnt / top_n, yy + 2, fmt_int(cnt), fontName="MkMono",
                      fontSize=7, fillColor=muted))
    neg, pos = facts["neg"], facts["pos"]
    dt = Drawing(width * 0.4, 40)
    bar_w = width * 0.4 - 4
    dt.add(Rect(0, 18, bar_w * neg, 12, fillColor=red, strokeColor=None))
    dt.add(Rect(bar_w * neg, 18, bar_w * (1 - neg - pos), 12, fillColor=surface, strokeColor=None))
    dt.add(Rect(bar_w * (1 - pos), 18, bar_w * pos, 12, fillColor=green, strokeColor=None))
    dt.add(String(0, 4, f"%{neg * 100:.0f} olumsuz", fontName="MkSans", fontSize=7.5, fillColor=red))
    dt.add(String(bar_w, 4, f"%{pos * 100:.0f} olumlu", fontName="MkSans", fontSize=7.5, fillColor=green,
                  textAnchor="end"))
    tone_block = [p(f"Ortalama ton {facts['avg_tone']:.1f}: genel dil {tone_word(facts['avg_tone'])}."), dt,
                  p("Ton −10 (çok olumsuz) ile +10 (çok olumlu) arasındadır; ±1,5 arası nötr sayılır.", st_small)]
    story += h("En çok yazan siteler · Ton")
    story.append(Table([[ds, tone_block]], colWidths=[width * 0.57, width * 0.43],
                       style=[("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0)]))

    # --- Aktörler ---
    story += h("Birlikte anılan aktörler")
    people = [f"{name} ({fmt_int(c)})" for name, c in facts["top_people"].items()]
    orgs = [f"{name} ({fmt_int(c)})" for name, c in facts["top_orgs"].items()]
    story.append(Table([[p("Kişiler", st_small), p("Kurumlar", st_small)],
                        [p(" · ".join(people) or "—"), p(" · ".join(orgs) or "—")]],
                       colWidths=[width * 0.55, width * 0.45], style=[("VALIGN", (0, 0), (-1, -1), "TOP"),
                                                                      ("LEFTPADDING", (0, 0), (-1, -1), 0)]))
    story.append(p("Adları GDELT, haberin İngilizce makine çevirisinden çıkarır; bozuk veya eksik olabilir. "
                   "Aranan konunun kendi adı listeden çıkarılmıştır.", st_small))

    # --- Yayılan haberler ---
    stories = facts["stories"]
    if not stories.empty:
        rows = [[p("Haber", st_small), p("İlk yayımlayan", st_small), p("Site", st_small)]]
        for _, g in stories.iterrows():
            rows.append([p((g["baslik"] or "")[:120]), p(f"{g['ilk']}\n{g['zaman']:%d.%m.%Y %H:%M}", st_small),
                         p(fmt_int(g["site"]))])
        table = Table(rows, colWidths=[width * 0.62, width * 0.28, width * 0.10])
        table.setStyle(TableStyle([("LINEBELOW", (0, 0), (-1, -1), 0.3, line), ("VALIGN", (0, 0), (-1, -1), "TOP"),
                                   ("LEFTPADDING", (0, 0), (-1, -1), 0)]))
        story += h("En çok yayılan haberler") + [table]

    # --- Alıntılar ---
    if quotes is not None and not quotes.empty:
        story += h("Öne çıkan alıntılar")
        for _, q in quotes.iterrows():
            story.append(KeepTogether([
                Paragraph(f'<font color="{MK["red"]}">“</font>{escape(q["quote"])}”', st_quote),
                p(f"…{(q['pre'] or '').strip()[-80:]} · {fmt_int(q['haber'])} haberde", st_small),
            ]))

    # --- Yayın sonrası değişiklikler ---
    if changes is not None:
        checked, titles, texts = changes
        story += h("Yayın sonrası değişiklikler")
        story.append(p(f"GDELT'in yeniden kontrol ettiği {fmt_int(checked)} haberin {fmt_int(len(titles))} tanesinin "
                       f"başlığı, {fmt_int(len(texts))} tanesinin metni yayımlandıktan sonra değişmiş."))
        for _, r in titles.head(8).iterrows():
            story.append(Paragraph(
                f'<font color="{MK["red"]}"><strike>{escape(r["old"][:130])}</strike></font><br/>'
                f'<font color="{MK["green"]}">→ {escape(r["new"][:130])}</font>', st_body))
            story.append(Spacer(1, 3))

    # --- Yöntem ---
    gaps = [f"{a:%d.%m.%Y}" + (f"–{b:%d.%m.%Y}" if b != a else "") for a, b in GDELT_GAPS
            if a <= as_date(end) and b >= as_date(start)]
    story += h("Yöntem ve veri notu")
    story.append(Paragraph(escape(
        "Kaynak: The GDELT Project (GKG 2.1; alıntılar GQG, sayfa değişiklikleri GDG), https://www.gdeltproject.org/. "
        "Haberler, aranan ifadenin haberin adresinde (URL), başlığında veya GDELT'in kişi listesinde geçmesiyle "
        "bulunur; haberin tam metni aranmaz. Aynı adresin tekrarları elenir; ajans kopyaları ayrıca işaretlenir. "
        "Kapsam GDELT'in taradığı sitelerle sınırlıdır; tam sayım değil, geniş bir örneklemdir. Ton ve adlar "
        "makine çevirisi üzerinden hesaplanır; tek tek haberlere değil genel eğilimlere güvenin."
        + (" Bu aralıkta GDELT'in veri toplamadığı günler: " + ", ".join(gaps) + "." if gaps else "")
    ), st_note))

    def decorate(canvas, doc):
        canvas.saveState()
        canvas.setStrokeColor(ink)
        canvas.setLineWidth(1.2)
        canvas.line(18 * mm, A4[1] - 12 * mm, A4[0] - 18 * mm, A4[1] - 12 * mm)
        canvas.setFont("MkMono", 7)
        canvas.setFillColor(muted)
        canvas.drawString(18 * mm, 10 * mm, f"{APP_NAME} — Veri: The GDELT Project (gdeltproject.org)")
        canvas.drawRightString(A4[0] - 18 * mm, 10 * mm, f"Sayfa {doc.page}")
        canvas.restoreState()

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm, topMargin=18 * mm,
                            bottomMargin=18 * mm, title=f"{APP_NAME} · {title}", author=APP_NAME)
    doc.build(story, onFirstPage=decorate, onLaterPages=decorate)
    return buffer.getvalue()


# --------------------------------------------------------------------------------
# BAŞLIK
# --------------------------------------------------------------------------------
st.markdown(
    f'<div class="mk-kicker">{APP_NAME}</div>'
    '<p class="mk-brand">Türk medyasının <span>hafızası</span></p>'
    '<p class="mk-tagline">2015\'ten bugüne Türkçe haberlerde bir kişinin, kurumun ya da konunun nasıl ele alındığını '
    'birkaç saniyede görün.</p>',
    unsafe_allow_html=True,
)
# GDELT kullanım koşulları: veriyi sunan her yerde GDELT'e atıf ve gdeltproject.org bağlantısı zorunludur
GDELT_ATTRIBUTION = (
    "Veri kaynağı: [The GDELT Project](https://www.gdeltproject.org/) (GKG 2.1, GQG, GDG, Events 2.0). "
    "GDELT verisi açık ve ücretsizdir; bu panel GDELT tarafından geliştirilmemiştir."
)
ABOUT_MD = f"""
**{APP_NAME} nedir?** 2015'ten bugüne Türkçe yayımlanan haberlerde bir kişinin, kurumun, yerin veya konunun nasıl
ele alındığını incelemenizi sağlayan bir araştırma aracı. Veri kaynağı, dünya genelinde haber sitelerini sürekli
tarayan açık veri projesi **[GDELT](https://www.gdeltproject.org/)**'tir (Global Knowledge Graph 2.1).

**Hangi veri var?**
- **Kapsam:** 18 Şubat 2015'ten dünkü güne kadar yaklaşık 61 milyon Türkçe haber kaydı. Her gün 11:15 civarında
  bir önceki günün haberleri eklenir. GDELT'in veri toplamadığı günler: 29.08.2017 ve 15.06–01.07.2025.
- **Verinin güncelliği:** Başlığın altındaki şerit, haber verisinin son gününü ve kaç gün önce olduğunu gösterir.
  Günlük yükleme bir önceki günü eklediği için 1 gün gecikme normaldir; daha eski ise şerit uyarı rengine döner.
- **Her haber için:** yayın tarihi ve saati, haber sitesi, bağlantı (URL), başlık, duygu tonu, GDELT tema kodları,
  habere adı geçen kişiler, kurumlar ve yerler.
- **Haberin tam metni yoktur.** GDELT metni saklamaz, yalnızca metinden çıkardığı bilgileri verir. Her haberin
  bağlantısı Detay tablosundadır (Derin analiz → Veri).

**Nasıl kullanılır?**
1. **Konu ara** ile bir kişi adı, kurum veya ifade yazın ("Ad Soyad", "Iğdır" gibi) ya da bir örnek aramaya
   tıklayın. Türkçe karakter ve büyük/küçük harf farkı önemsizdir. **Karşılaştır** ile 2–4 ifadeyi virgülle ayırarak
   yan yana koyabilirsiniz.
2. Tarih aralığını seçip **Ara**'ya basın. 2015'ten bugüne **tüm yıllar** tek aramada aranabilir. Bulut arşivinde
   sonuç 1 aylık aralıkta birkaç saniyede, tüm yıllarda ~1 dakikada gelir. Çok genel ifadeler (50 binden fazla
   haber) durdurulur; daha belirgin bir ifade seçin.
3. Önce **konu kartı** gelir. En üstteki **Veri kapsamı** şeridi sonucun ne kadar güvenilir olduğunu söyler
   (Yeterli / Sınırlı / Yetersiz). Ardından zaman şeridi ve zirveler, en çok yazan siteler, ton, birlikte anılan
   aktörler, en çok yayılan haberler ve alıntılar gelir. Her kartın altındaki **Kanıtı göster** kartın dayandığı haberleri listeler.
   Ayrıntılı analiz için aşağıdaki **Derin analiz** bölümünde bir grup (Genel bakış, Araştırma araçları, Veri) ve
   görünüm seçin. Sonuçları kenar çubuğundaki süzgeçlerle daraltabilirsiniz.

Arama, ifadenin haberin **adresinde, başlığında veya kişi listesinde** geçtiği haberleri bulur. Yalnızca haber
metninin içinde geçen bir ad bulunamaz. Daha önce indirdiğiniz sonuç dosyalarını **Dosya yükle (CSV)** seçeneğiyle
yeniden yükleyebilirsiniz.

**Neler analiz edilebilir?**

| Grup · görünüm | Cevapladığı sorular |
|---|---|
| **Genel bakış** · Zaman serisi | Konu ne zaman gündeme geldi? Yıl dönümü, dava tarihi gibi dalgalar |
| **Genel bakış** · Duygu tonu | Haberler ne kadar olumlu/olumsuz? Hangi site daha sert bir dil kullanıyor? |
| **Genel bakış** · Tema analizi | Konu hangi başlıklar altında ele alınıyor (hukuk, güvenlik, siyaset…)? |
| **Araştırma araçları** · Yankı haritası | Bir haber ilk nerede çıktı, hangi sitelere ne hızla yayıldı? |
| **Araştırma araçları** · Değişiklik dedektifi | Hangi haberlerin başlığı veya metni yayımlandıktan sonra değiştirildi? |
| **Araştırma araçları** · Başlık ve çerçeve | Başlıklar hangi konu çerçevelerine ayrılıyor, hangi kelimeler öne çıkıyor? |
| **Araştırma araçları** · Kişi ve kurum | Konuyla birlikte en çok hangi kişi ve kurumlar anılıyor? |
| **Araştırma araçları** · Olay türleri | Haberler hangi olayları anlatıyor: görüşme, eleştiri, protesto, saldırı? (Eylül 2025 sonrası; siyaset ve güvenlik konularında) |
| **Araştırma araçları** · Harita | Haberlerde hangi yerlerden söz ediliyor? |
| **Araştırma araçları** · Kopya haberler | Aynı ajans metni kaç sitede, ne hızla yayıldı? |
| **Veri** · Detay tablosu | Hangi haberler çıktı? Başlık, site, tarih, ton, bağlantı |
| **Veri** · Tema rehberi | GDELT tema kodları ne anlama geliyor? |

Soldaki kenar çubuğundan tarih, site, tema, kişi, ton ve kelimeye göre filtreleyebilirsiniz. **Çerçeve seti** ile
başlıkların hangi kategorilere ayrılacağını seçebilir, **Özel** seçeneğiyle kendi kategorilerinizi yazabilirsiniz.
Konu kartının altındaki **PDF rapor hazırla** ile tek tıkla markalı bir rapor alabilir, sonuçları sayfanın
altındaki **Filtrelenmiş Veriyi CSV Olarak İndir** ile indirip Excel'de açabilirsiniz. Arama yapmadan önce ana
ekrandaki **Bugün gündemde** kartları arşivdeki son günün öne çıkan isimlerini ve en çok yayılan haberlerini gösterir.

**Örnek araştırma soruları:** Bir kişinin haberlerdeki görünürlüğü yıllar içinde nasıl değişti? Aynı olayı farklı
siteler hangi tonla verdi? Bir konunun yıl dönümlerinde haber sayısı artıyor mu? Bir kişiyle en çok birlikte anılan
kurumlar hangileri? Bir haber ajans kaynaklı mı, kaç siteye kopyalandı?

**Bilinmesi gereken sınırlamalar**
- **Çeviri hataları:** GDELT Türkçe haberleri İngilizceye makine çevirisiyle çevirip öyle işler. Kişi, kurum ve yer
  adları bu yüzden zaman zaman bozulur; ton da çeviri üzerinden hesaplanır. Tek tek haberlere değil, genel
  eğilimlere güvenin.
- **Yıllar arasında kapsam farkı:** GDELT'in taradığı site sayısı yıllara göre değişir (2015–16'da günde ~25 bin,
  bugün ~10 bin haber). Yıllar arasında ham haber sayısını karşılaştırırken dikkatli olun.
- **Eksik haberler:** Her site ve her haber GDELT'e girmez; veri kapsamlı bir örneklemdir, tam sayım değildir.
  Konu kartındaki **Veri kapsamı** şeridi aramaya özgü boşlukları uyarır: başlıklar arşiv öncesi bir yıla (2015'ten
  önce) sık atıf yapıyorsa olayın kendisi arşivde yoktur; başlıksız haber oranı yüksekse veya aralıkta GDELT'in veri
  toplamadığı günler varsa da uyarılır. Aynı değerlendirme PDF raporun başında yer alır. Bazı yayınlar belirli
  yıllarda GDELT'te yoktur (ör. Cumhuriyet, Halk TV, Diken ve Aydınlık 2026'da); bunların künyeleri aşağıdaki arşiv
  dışı kaynaklarla tamamlanır.
- **Arşiv dışı kaynaklar:** GDELT'in taramadığı bazı yayınların (Cumhuriyet, Aydınlık, Ulusal Kanal, Diken, Tele1,
  Halk TV, Nefes, Yeniçağ, Karar, Medyascope, Kısa Dalga, Serbestiyet, TR724) haber künyeleri kendi site haritaları
  ve RSS akışlarından ayrıca toplanır. Konu kartının altında **Arşiv dışı kaynaklar** bölümünde, 2015 öncesi
  kayıtlarla birlikte gösterilir. Bu kayıtlarda yalnızca başlık ve tarih vardır; ton ve tema yoktur, kartın
  rakamlarına katılmaz. Arama türlerinin yanındaki **Arşiv dışı** ekranı bu künyelere konu aramadan göz atmayı
  sağlar: yayın bazında sayılar, başlıktan çerçeveler ve başlıktan bulunan kişi/kurum adları. Künyeler 6 saatte bir
  güncellenir. Uzun aralıklarda yayın seçerek veya bir kelimeyle süzerek bakın (en fazla 300 bin künye).
- **Genel ifadeler:** 50 binden fazla haberle eşleşen bir arama durdurulur; daha belirgin bir ifade veya daha kısa bir
  aralık seçin.
"""
# Parolalı giriş: doğru parola girilene kadar panelin geri kalanı hiç çalışmaz (veri de okunmaz)
if not parola_kapisi():
    st.stop()

st.caption(GDELT_ATTRIBUTION)
# Veri tazeliği şeridi: kaynak seçildikten sonra doldurulur (render_freshness), ama başlığın altında durur
freshness_slot = st.empty()
with st.expander("Bu panel hakkında", expanded=False, icon=":material/info:"):
    st.markdown(ABOUT_MD)
render_yasal()


# Veri kaynağı etiketleri (ekranda ve PDF kapağında görünür; iç adlar SRC_* / bq_* olarak kalır)
SRC_CSV, SRC_BQ, SRC_LOCAL = "Dosya yükle (CSV)", "Bulut arşivi", "Yerel arşiv"
MODE_TOPIC, MODE_COMPARE, MODE_EXTRA = "Konu ara", "Karşılaştır", "Arşiv dışı"
# Örnek aramalar: gerçek kişi adı içermez (bkz. CLAUDE.md "Konu bağımsızlığı")
EXAMPLES = {
    MODE_TOPIC: ["Iğdır", "deprem", "asgari ücret", "göçmen", "orman yangını"],
    MODE_COMPARE: ["Ankara, İzmir", "enflasyon, faiz", "deprem, sel felaketi"],
}
# Arama kutusundaki tarih aralığının, örnek aramaların ve gündem panosundaki isim tıklamasının varsayılan uzunluğu (gün)
DEFAULT_RANGE_DAYS = 30
local_days = archive_days()
# Yerel arşiv yalnızca indirilmişse (bilgisayardaki panelde) seçenek olarak görünür ve varsayılandır
sources = ([SRC_LOCAL] if local_days else []) + [SRC_BQ, SRC_CSV]

top_left, top_right = st.columns([3, 2])
with top_left:
    # Başlangıç değeri session_state'ten verilir: gündem kartındaki isim tıklaması arama türünü "Konu ara"ya çevirir
    st.session_state.setdefault("mode", MODE_TOPIC)
    # "Arşiv dışı": ek toplama (pipeline/extra_collect.py) yerelde veya BigQuery kopyasında varsa görünür
    modes = [MODE_TOPIC, MODE_COMPARE] + ([MODE_EXTRA] if extra_available() else [])
    if st.session_state.get("mode") not in modes:
        st.session_state["mode"] = MODE_TOPIC
    mode = st.segmented_control(
        "Arama türü", modes, key="mode", label_visibility="collapsed"
    ) or MODE_TOPIC
with top_right:
    data_source = st.radio("Veri kaynağı", sources, horizontal=True, label_visibility="collapsed")
# Şerit başlığın altındaki yer tutucuya yazılır; CSV modunda veri kullanıcıdan geldiği için gösterilmez
if data_source != SRC_CSV:
    render_freshness(freshness_slot, data_source == SRC_LOCAL, local_days)



def render_footer() -> None:
    """Sayfa sonu GDELT atfı ("Bu panel hakkında" başlıktaki atfın altındadır). Erken biten ekranlarda da
    st.stop()'tan önce çağrılır."""
    st.markdown("---")
    st.caption(f"{GDELT_ATTRIBUTION} • {APP_NAME}")


def render_trending(day, is_local: bool, source: str, search_start) -> None:
    """Ana ekranda "Günün medya panosu" (arama yapılmadan önce): günün manşeti ve yankısı, özet, sıçrayan isimler
    (tıklayınca aranır), değişen başlıklar, kategori sütunları. Fotoğraf kullanılmaz; görseller veriden üretilir."""
    try:
        with st.spinner("Günün panosu hazırlanıyor..."):
            data = trending_data(day, is_local)
            totals = daily_totals(is_local)
    except Exception as e:
        st.caption(f"Gündem panosu yüklenemedi: {e}")
        return
    if not data["count"]:
        return
    st.markdown(
        f'<div class="mk-kicker" style="margin-top:1rem">Bugün gündemde · günün medya panosu</div>'
        f'<p class="mk-tagline">{as_date(day):%d.%m.%Y} · arşivdeki en son gün</p>',
        unsafe_allow_html=True,
    )

    # --- 1. satır: günün manşeti + özet ---
    c_hero, c_sum = st.columns([2.4, 1])
    hero = data["hero"]
    with c_hero:
        if hero:
            category = (hero["frames"] or "").split(";")[0]
            category = "" if category == "Diğer" else f" · {category}"
            st.markdown(
                f'<div class="mk-card-title">Günün manşeti{html.escape(category)}</div>'
                f'<p class="mk-headline" style="font-size:2rem;line-height:1.15">'
                f'<a href="{html.escape(hero["url"])}" target="_blank" style="color:{MK["ink"]};text-decoration:none">'
                f'{html.escape(hero["baslik"] or hero["title"])}</a></p>'
                f'<p class="mk-tagline">İlk: <b>{html.escape(hero["first_site"])}</b> · '
                f'{hero["first_time"]:%H:%M} · <b>{hero["sites"]} siteye</b> yayıldı '
                f'(ilk 1 saatte {hero["within_1h"]}, 24 saatte {hero["within_24h"]})</p>',
                unsafe_allow_html=True,
            )
            by_site = hero["by_site"]
            curve = pd.Series(range(1, len(by_site) + 1), index=by_site.values)
            fig = go.Figure(go.Scatter(x=curve.index, y=curve.values, mode="lines+markers", line_shape="hv",
                                       line=dict(color=MK["red"], width=2), marker=dict(size=5, color=MK["red"]),
                                       fill="tozeroy", fillcolor="rgba(169,71,60,0.08)",
                                       hovertemplate="%{x:.1f} saat sonra: %{y} site<extra></extra>"))
            fig.update_layout(template=PLOTLY_TEMPLATE, height=150, margin=dict(l=10, r=10, t=10, b=30),
                              xaxis_title="İlk yayından sonra (saat)", yaxis_title="Site")
            st.plotly_chart(fig, width="stretch", key="hero_curve")
            with st.expander("Yankı haritası · hangi site ne zaman yayımladı"):
                rank_list([(site, f"+{hrs:.1f} sa") for site, hrs in by_site.items()])
        else:
            st.caption("Bugün birden fazla siteye yayılan bir haber yok.")
    with c_sum:
        card_title("Günün özeti")
        prev = totals[(totals.index < pd.Timestamp(day)) &
                      (totals.index >= pd.Timestamp(day) - pd.Timedelta(days=TRENDING_BASELINE_DAYS))]
        change = (data["count"] / prev.mean() - 1) * 100 if len(prev) else 0
        st.metric("Türkçe haber", fmt_int(data["count"]), f"%{change:+.0f} (28 gün ortalamasına göre)",
                  delta_color="off")
        st.metric("Ortalama ton", f"{data['tone']:.1f}", tone_word(data["tone"]), delta_color="off")
        st.metric("Birden fazla sitede çıkan haber", fmt_int(len(data["stories"])))

    # --- 2. satır: öne çıkan isimler + değişen başlıklar ---
    def search_person() -> None:
        name = st.session_state.get("trend_pick")
        if name:
            st.session_state["mode"] = MODE_TOPIC
            st.session_state["topic_query"] = (source, MODE_TOPIC, (name,), search_start, day)
            st.session_state["trend_pick"] = None

    c_people, c_changes = st.columns([1, 1.25])
    with c_people:
        card_title("Öne çıkan isimler · son 29 gün")
        people = data["people"]
        if people.empty:
            st.caption("Bugün belirgin bir sıçrama yok.")
        else:
            table = pd.DataFrame({
                "İsim": people.index, "Bugün": people["n"].to_numpy(),
                "Artış": [f"{r:.0f} kat" for r in people["ratio"]], "Seyir": people["seyir"].to_numpy(),
            })
            st.dataframe(table, hide_index=True, width="stretch", key="trend_table", column_config={
                "Seyir": st.column_config.LineChartColumn("Son 29 gün", y_min=0),
            })
            st.pills("Aramak için bir isme tıklayın", people.index.tolist(), key="trend_pick",
                     on_change=search_person)
            st.caption("Önceki 28 güne göre en az 3 kat daha çok anılan kişiler; adı o günün başlıklarında da geçmeli. "
                       "Adlar GDELT'in makine çevirisinden gelir.")
    with c_changes:
        card_title("Değişen başlıklar · bugün yakalananlar")
        changes = data["changes"]
        if changes.empty:
            st.caption("Bugün yakalanan anlamlı bir başlık değişikliği yok.")
        for _, r in changes.iterrows():
            old_html, new_html = diff_html(r["old"], r["new"])
            badge = (f' <b style="color:{MK["red"]}">· haber kaldırılmış olabilir</b>' if r["removed"] else "")
            st.markdown(
                f'<div class="mk-rank" style="display:block"><small style="color:{MK["muted"]}">'
                f'{html.escape(r["site"])} · <a href="{html.escape(r["page_url"])}" target="_blank">sayfayı aç</a>'
                f'{badge}</small><br>{old_html}<br>→ {new_html}</div>',
                unsafe_allow_html=True,
            )
        st.caption("GDELT'in bugün yeniden kontrol ettiği sayfalarda başlığı değişenler; yalnızca site adı değişenler "
                   "elenir. Silinen isimler öne alınır. Ayrıntı için bir konu arayıp Değişiklik Dedektifi'ni açın.")

    # --- 3. satır: kategori sütunları ---
    stories = data["stories"]
    if hero is not None:
        stories = stories.iloc[1:]
    cols = st.columns(len(BOARD_CATEGORIES))
    for col, (short, frame) in zip(cols, BOARD_CATEGORIES):
        with col:
            st.markdown(f'<div class="mk-card-title mk-red">{html.escape(short)}</div>', unsafe_allow_html=True)
            picked = stories[stories["frames"].fillna("").str.contains(frame, regex=False)].head(3)
            if picked.empty:
                st.caption("Bugün yayılan haber yok.")
            for _, g in picked.iterrows():
                st.markdown(
                    f'<div class="mk-rank" style="display:block"><a href="{html.escape(g["url"])}" target="_blank" '
                    f'style="color:{MK["ink"]};text-decoration:none;font-family:\'Source Serif 4\',Georgia,serif;'
                    f'font-size:1.02rem">{html.escape((g["baslik"] or "")[:110])}</a><br>'
                    f'<small style="color:{MK["muted"]}">{g["site"]} site · ilk: {html.escape(g["ilk"])}</small></div>',
                    unsafe_allow_html=True,
                )
    st.caption("Kategoriler başlıklardan 'Genel' çerçeve setiyle otomatik belirlenir; bir haber birden fazla sütunda "
               "görünebilir. Haberler, başlığının kelime kökleri aynı olan ve en çok siteye yayılanlardır.")


def parse_terms(text: str, mode: str) -> tuple:
    """Arama kutusundaki metinden ifade(ler): Karşılaştır'da virgülle ayrılmış 2–4 ifade, Konu ara'da tek ifade."""
    if mode == MODE_COMPARE:
        return tuple(dict.fromkeys(t.strip() for t in text.split(",") if t.strip()))
    return (text.strip(),) if text.strip() else ()


def validate_terms(terms: tuple, mode: str) -> str:
    if mode == MODE_COMPARE and not 2 <= len(terms) <= 4:
        return "Karşılaştırmak için virgülle ayırarak 2–4 ifade yazın (örn. 'Ankara, İzmir')."
    if not terms or any(len(t) < 3 for t in terms):
        return "Her ifade en az 3 karakter olmalı."
    return ""


# "Arşiv dışı": ek toplamadaki künyelere göz atma; veri kaynağı seçiminden bağımsızdır (yerelde dosyalardan,
# yayında BigQuery kopyasından)
if mode == MODE_EXTRA:
    render_extra_browser()
    render_footer()
    st.stop()

# --------------------------------------------------------------------------------
# VERİ KAYNAĞI: CSV
# --------------------------------------------------------------------------------
if data_source == SRC_CSV:
    if mode == MODE_COMPARE:
        st.info("Karşılaştırma yalnızca bulut ve yerel arşivde çalışır. Yüklenen dosyalar konu olarak analiz edilir.")
    uploaded_files = st.file_uploader(
        "GDELT GKG CSV dosyalarınızı seçin veya sürükleyip bırakın (Birden fazla seçebilirsiniz)",
        type=["csv"],
        accept_multiple_files=True,
    )

    if not uploaded_files:
        st.info(
            "Başlamak için **bir veya birden fazla** CSV dosyası yükleyin. Beklenen kolonlar: "
            "`pub_timestamp`, `SourceCommonName`, `DocumentIdentifier`, `Themes`, `V2Tone` "
            "(opsiyonel: `PageTitle`, `V2Persons`, `Organizations`, `V2Locations`, `TranslationInfo`, `GKGRECORDID`). "
            "Ayrıntılı kullanım için proje klasöründeki `YARDIM.md` dosyasına bakın."
        )
        render_footer()
        st.stop()
    source_label = f"{len(uploaded_files)} dosya birleştirildi"
    card_title_text = f"{len(uploaded_files)} CSV dosyası"
    quotes_df = None

# --------------------------------------------------------------------------------
# VERİ KAYNAĞI: BIGQUERY VEYA YEREL ARŞİV (konu araması veya karşılaştırma)
# --------------------------------------------------------------------------------
else:
    is_local = data_source == SRC_LOCAL
    if is_local:
        cov_lo, cov_hi = local_days[0], local_days[-1]
    else:
        try:
            cov_lo, cov_hi = bq_coverage()
        except Exception as e:
            st.error(
                f"Bulut arşivine (BigQuery) bağlanılamadı: {e}\n\nYerelde oturum süresi dolmuş olabilir; terminalde şunu çalıştırın: "
                f"`gcloud auth application-default login --project {BQ_PROJECT}`. Yayındaki panelde "
                "Settings > Secrets içindeki `[gcp_service_account]` anahtarını kontrol edin."
            )
            st.stop()
    default_start = max(cov_lo, cov_hi - timedelta(days=DEFAULT_RANGE_DAYS - 1))

    def use_example(source: str, mode: str, start, end) -> None:
        """Örnek arama çipine tıklanınca aramayı doğrudan başlatır."""
        picked = st.session_state.get("example_pick")
        if picked:
            st.session_state["topic_query"] = (source, mode, parse_terms(picked, mode), start, end)
            st.session_state["example_pick"] = None

    with st.form("topic_form"):
        if mode == MODE_TOPIC:
            term_input = st.text_input(
                "Aranacak kişi, kurum ya da konu",
                placeholder="Bir kişi, kurum ya da konu yazın",
                help="URL'de, başlıkta veya GDELT'in kişi alanında geçen haberler getirilir. "
                     "Türkçe karakter ve büyük/küçük harf farkları otomatik tolere edilir.",
            )
        else:
            term_input = st.text_input(
                "Karşılaştırılacak ifadeler",
                placeholder="Virgülle ayırın: Ankara, İzmir",
                help="2–4 ifade. Her biri ayrı aranır; sonuçlar aynı grafiklerde yan yana gösterilir.",
            )
        col_dates, col_btn = st.columns([3, 1], vertical_alignment="bottom")
        date_input = col_dates.date_input(
            "Tarih aralığı (UTC gün)",
            help=None if is_local else "Tüm yıllar aranabilir; 1 yıldan uzun aralıklar bulut arşivinde ~1 dakika sürer.",
            value=(default_start, cov_hi),
            min_value=cov_lo,
            max_value=cov_hi,
        )
        submitted = col_btn.form_submit_button("Ara", type="primary", width="stretch")
    st.pills(
        "Örnek aramalar", EXAMPLES[mode], key="example_pick",
        on_change=use_example, args=(data_source, mode, default_start, cov_hi),
    )
    # Arama sonucu gösterilmeyecekse gündem kartları arama kutusunun hemen altında yer alır
    active = st.session_state.get("topic_query")
    if not (submitted or (active and active[0] == data_source and active[1] == mode)):
        render_trending(cov_hi, is_local, data_source, default_start)
        render_sapmalar()
        render_dunya_turk()
        render_arsive_sor()
    if is_local:
        st.caption(
            f"Bilgisayardaki arşivde {cov_lo:%d.%m.%Y} – {cov_hi:%d.%m.%Y} arasındaki {fmt_int(len(local_days))} "
            "günün Türkçe haberleri var. Arama ücretsizdir."
        )
        archive_update_section(cov_hi)
    else:
        st.caption(f"Bulut arşivinde {cov_lo:%d.%m.%Y} – {cov_hi:%d.%m.%Y} arasındaki Türkçe haberler var.")

    if submitted:
        terms = parse_terms(term_input, mode)
        problem = validate_terms(terms, mode)
        if problem:
            st.warning(problem)
            st.stop()
        if not (isinstance(date_input, tuple) and len(date_input) == 2):
            st.warning("Tarih aralığının başlangıç ve bitişini seçin.")
            st.stop()
        st.session_state["topic_query"] = (data_source, mode, terms, date_input[0], date_input[1])

    query = st.session_state.get("topic_query")
    # Kaynak veya arama türü değiştiyse önceki arama gösterilmez
    if not query or query[0] != data_source or query[1] != mode:
        render_footer()
        st.stop()

    _, _, q_terms, q_start, q_end = query
    if mode == MODE_COMPARE:
        try:
            render_compare(q_terms, q_start, q_end, is_local)
        except Exception as e:
            st.error(f"{'Yerel arşiv' if is_local else 'Bulut arşivi'} araması başarısız oldu: {e}")
        render_footer()
        st.stop()

    q_term = q_terms[0]
    # Hazırlanan arama oturumda tutulur; tıklamalar arşivi ve önbelleği yeniden okumaz
    bundle = st.session_state.get("search_bundle")
    if not (bundle and bundle["key"] == query):
        st.session_state.pop("search_bundle", None)
        bundle = None
        queue_note = " Şu anda başka aramalar çalışıyor; sıranız gelince başlayacak." if search_busy() else ""
        try:
            if is_local:
                with st.spinner(f"'{q_term}' haberleri yerel arşivde aranıyor...{queue_note}"):
                    with search_slot():
                        bundle = build_search_bundle(query, local_fetch(q_term, q_start, q_end), None)
            else:
                long_range = (q_end - q_start).days + 1 > BQ_LONG_RANGE_DAYS
                with st.spinner(f"'{q_term}' haberleri bulut arşivinden getiriliyor"
                                + (" (uzun aralık, ~1 dakika sürebilir)..." if long_range else "...") + queue_note):
                    with search_slot():
                        bundle = build_search_bundle(query, *bq_fetch(q_term, q_start, q_end))
        except Exception as e:
            st.error(f"{'Yerel arşiv' if is_local else 'Bulut arşivi'} araması başarısız oldu: {e}")
            st.stop()
        st.session_state["search_bundle"] = bundle
    estimate = bundle["estimate"]

    if bundle["raw_len"] == 0:
        st.warning(f"'{q_term}' için {q_start:%d.%m.%Y} – {q_end:%d.%m.%Y} arasında haber bulunamadı.")
        st.stop()
    if is_local and bundle["raw_len"] >= BQ_MAX_ROWS:
        st.warning(f"Sonuç {fmt_int(BQ_MAX_ROWS)} haberle sınırlandı (en yeniler). Daha dar bir arama yapın.")
    with st.expander("Sorgu ayrıntıları"):
        if estimate is None:
            st.write("Yerel arşivde arandı (ücretsiz). Kullanılan kalıplar:")
        else:
            st.write(
                f"Bulut arşivinde (BigQuery) okunan veri: yaklaşık {estimate / 1024 ** 3:.2f} GB (Storage Read API; sorgu kotasına "
                "sayılmaz, ayda 300 TiB'a kadar ücretsiz). Kullanılan kalıplar:"
            )
        st.json(topic_patterns(q_term))
    where = "yerel arşivden" if is_local else "bulut arşivinden"
    source_label = f"'{q_term}' için {where} {fmt_int(bundle['raw_len'])} haber getirildi"
    card_title_text = q_term
    quotes_df = "pending"

# --------------------------------------------------------------------------------
# ÖN İŞLEME
# --------------------------------------------------------------------------------
if data_source == SRC_CSV:
    csv_key = ("csv",) + tuple((getattr(f, "file_id", f.name), getattr(f, "size", None)) for f in uploaded_files)
    bundle = st.session_state.get("search_bundle")
    if not (bundle and bundle["key"] == csv_key):
        st.session_state.pop("search_bundle", None)
        try:
            with st.spinner("Veri hazırlanıyor..."):
                files_payload = tuple((f.name, f.getvalue()) for f in uploaded_files)
                bundle = build_search_bundle(csv_key, load_data(files_payload), None)
        except ValueError as e:
            st.error(f"Veri hatası: {e}")
            st.stop()
        except Exception as e:
            st.error(f"Dosyalar okunurken beklenmeyen bir hata oluştu: {e}")
            st.stop()
        st.session_state["search_bundle"] = bundle
df = bundle["df"]
theme_exploded_full, persons_exploded_full = bundle["themes"], bundle["persons"]
orgs_exploded_full, locations_exploded_full = bundle["orgs"], bundle["locations"]

if df.empty:
    st.warning("Yüklenen dosyalarda geçerli (tarih parse edilebilen) satır bulunamadı.")
    st.stop()

dropped = bundle["raw_len"] - len(df)
st.success(
    f"Başarıyla {source_label}! Toplam {fmt_int(len(df))} tekil haber analiz için hazır"
    + (f" ({fmt_int(dropped)} tekrar eden / tarihsiz satır çıkarıldı)." if dropped else ".")
)

# --------------------------------------------------------------------------------
# SIDEBAR - FİLTRELER
# --------------------------------------------------------------------------------
FILTER_KEY_PREFIX = "f_"


def reset_filters() -> None:
    """Kenar çubuğu süzgeçlerini varsayılana döndürür (anahtarı FILTER_KEY_PREFIX ile başlayan araçlar)."""
    for key in [k for k in st.session_state if str(k).startswith(FILTER_KEY_PREFIX)]:
        del st.session_state[key]


min_date = df["pub_timestamp"].min().date()
max_date = df["pub_timestamp"].max().date()
all_sources = sorted(df["SourceCommonName"].unique().tolist())
all_themes = sorted(theme_exploded_full["Themes"].unique().tolist())
all_persons = persons_exploded_full["V2Persons"].value_counts().index.tolist()
sentiment_options = ["Pozitif", "Nötr", "Negatif", "Bilinmiyor"]

with st.sidebar:
    st.header("Süzgeçler")
    st.button("Süzgeçleri sıfırla", icon=":material/restart_alt:", on_click=reset_filters, key="reset_sidebar",
              width="stretch")

    with st.expander("Zaman ve kapsam", expanded=True, icon=":material/date_range:"):
        # Anahtar aralığa bağlı: yeni bir arama gelince tarih süzgeci yeni aralıkla başlar
        date_range = st.date_input(
            "Tarih aralığı (Türkiye saati)",
            value=(min_date, max_date),
            min_value=min_date,
            max_value=max_date,
            key=f"{FILTER_KEY_PREFIX}date_{min_date}_{max_date}",
        )
        selected_sources = st.multiselect(
            "Kaynak (site)",
            options=all_sources,
            default=[],
            placeholder="Tümü",
            help="Boş bırakılırsa tüm kaynaklar dahil edilir.",
            key=f"{FILTER_KEY_PREFIX}sources",
        )
        search_keyword = st.text_input(
            "Haber içinde / URL'de ara",
            value="",
            help="Başlık, URL, tema, kaynak veya kişi alanlarında geçmesini istediğiniz metni yazın.",
            key=f"{FILTER_KEY_PREFIX}keyword",
        )
        dedupe_copies = st.checkbox(
            "Kopya (ajans) haberlerini tekilleştir",
            value=False,
            help="Kopya gruplarından yalnızca ilk yayımlanan haber kalır.",
            key=f"{FILTER_KEY_PREFIX}dedupe",
        )

    with st.expander("İçerik", expanded=True, icon=":material/topic:"):
        selected_themes = st.multiselect(
            "Tema (Themes)",
            options=all_themes,
            default=[],
            placeholder="Tümü",
            help="Seçilen temalardan en az birini içeren haberler gösterilir (tam eşleşme).",
            key=f"{FILTER_KEY_PREFIX}themes",
        )
        selected_persons = st.multiselect(
            "Kişi (V2Persons)",
            options=all_persons,
            default=[],
            placeholder="Tümü",
            help="Seçilen kişilerden en az birinin geçtiği haberler gösterilir. Liste sıklığa göre sıralıdır.",
            key=f"{FILTER_KEY_PREFIX}persons",
        )
        selected_sentiments = st.multiselect(
            "Duygu tonu kategorisi",
            options=sentiment_options,
            default=[],
            placeholder="Tümü",
            key=f"{FILTER_KEY_PREFIX}sentiments",
        )
        frame_set_name = st.selectbox(
            "Çerçeve seti",
            list(FRAME_SETS) + [CUSTOM_FRAME_SET],
            help="Başlıkların hangi konu çerçevelerine ayrılacağını belirler. Aramayı ve veriyi değiştirmez; "
                 "yalnızca 'Başlık ve çerçeve' görünümünü, 'Başlık çerçevesi' süzgecini ve tablodaki 'Çerçeve' "
                 "kolonunu etkiler.",
            key=f"{FILTER_KEY_PREFIX}frame_set",
        )
        if frame_set_name == CUSTOM_FRAME_SET:
            custom_frames_text = st.text_area(
                "Kendi çerçeveleriniz",
                value=CUSTOM_FRAME_EXAMPLE,
                help="Her satıra bir çerçeve: 'Çerçeve adı: kelime, kelime, ...'. Kelimeler başlıktaki kelimelerin "
                     "başıyla eşleşir (örn. 'mülteci' -> 'mültecilere'). 'sel' gibi başka kelimelerin başı olan kısa "
                     "kökler yerine ifade yazın ('sel felaketi').",
                key=f"{FILTER_KEY_PREFIX}custom_frames",
            )
            active_frames = parse_custom_frames(custom_frames_text)
            if not active_frames:
                st.warning("Geçerli çerçeve bulunamadı. Örnek: 'Göç: göçmen, mülteci'")
        else:
            active_frames = FRAME_SETS[frame_set_name]
        df["Frames"] = assign_frames(df[["TitleCore", "DocumentIdentifier"]], frame_set_key(active_frames))
        frames_exploded_full = explode_list_column(df[["Frames"]], "Frames")
        all_frames = list(active_frames) + ["Diğer"]
        selected_frames = st.multiselect(
            "Başlık çerçevesi",
            options=all_frames,
            default=[],
            placeholder="Tümü",
            help="Başlıktaki anahtar kelimelere göre atanan konu çerçevesi. Bir haber birden fazla çerçevede "
                 "olabilir.",
            # Anahtar sete bağlı: çerçeve seti değişince eski setin seçimleri kalmasın
            key=f"{FILTER_KEY_PREFIX}frames_{frame_set_name}",
        )

    with st.expander("Gelişmiş", expanded=False, icon=":material/tune:"):
        tone_threshold = st.slider(
            "Nötr bant genişliği (±AvgTone)",
            min_value=0.0, max_value=5.0, value=1.0, step=0.5,
            help="AvgTone bu değerin üstündeyse Pozitif, eksisinin altındaysa Negatif sayılır.",
            key=f"{FILTER_KEY_PREFIX}tone_band",
        )
        topic_words_text = st.text_input(
            "Konu kelimeleri",
            value=", ".join(detect_topic_words(df["TitleCore"])),
            help="Başlıkların çoğunda geçen, aranan konuyu tanımlayan kelimeler (örn. kişinin adı ve soyadı). "
                 "Otomatik bulunur; virgülle ayırarak düzenleyebilirsiniz. Başlık benzerliği ve kelime frekansında "
                 "yok sayılır, harita hatalarını bulmakta kullanılır.",
            # Otomatik değer aramaya göre değişir; anahtar aralığa bağlı olduğu için yeni aramada yenilenir
            key=f"{FILTER_KEY_PREFIX}topic_words_{min_date}_{max_date}_{len(df)}",
        )
        topic_stems = parse_topic_stems(topic_words_text)
        top_n = st.slider("Gösterilecek tema sayısı", min_value=5, max_value=40, value=20, step=5,
                          key=f"{FILTER_KEY_PREFIX}top_n")
        all_families = sorted(theme_exploded_full["Family"].unique())
        excluded_families = st.multiselect(
            "Grafiklerde gizlenecek tema aileleri",
            options=all_families,
            default=[],
            placeholder="Seçin",
            help="Örn. 'Kişi rolü / meslek' veya 'Hayvan adı' gibi genel/gürültülü aileleri tema grafiklerinden "
                 "çıkarır.",
            key=f"{FILTER_KEY_PREFIX}families",
        )
        st.markdown("**Kopya tespiti**")
        use_title_copies = st.checkbox(
            "Benzer başlıkları da kopya say",
            value=True,
            help="Kapalıyken yalnızca V2Tone değerleri birebir aynı olan haberler kopya sayılır.",
            key=f"{FILTER_KEY_PREFIX}title_copies",
        )
        title_threshold = st.slider(
            "Başlık benzerlik eşiği",
            min_value=0.4, max_value=1.0, value=COPY_TITLE_THRESHOLD, step=0.05,
            disabled=not use_title_copies,
            help="Kelime köklerinin örtüşme oranı (Jaccard). 1.0 = aynı kelimeler; düşürdükçe daha çok haber "
                 "birleşir.",
            key=f"{FILTER_KEY_PREFIX}title_threshold",
        )
        copy_window_hours = st.slider(
            "Zaman penceresi (saat)",
            min_value=6, max_value=168, value=COPY_WINDOW_HOURS, step=6,
            disabled=not use_title_copies,
            help="Yalnızca bu süre içinde yayımlanan benzer başlıklar karşılaştırılır.",
            key=f"{FILTER_KEY_PREFIX}copy_window",
        )

if isinstance(date_range, tuple) and len(date_range) == 2:
    start_date, end_date = date_range
else:
    start_date, end_date = min_date, max_date

# --------------------------------------------------------------------------------
# FİLTRELEME MANTIĞI
# --------------------------------------------------------------------------------
df["Sentiment"] = classify_sentiment(df["AvgTone"], tone_threshold)
copy_info = detect_copy_groups(
    df[["pub_timestamp", "V2Tone", "WordCount", "TitleCore"]], use_title_copies, title_threshold, copy_window_hours,
    topic_stems,
)
df[copy_info.columns] = copy_info

mask = (
    (df["pub_timestamp"].dt.date >= start_date)
    & (df["pub_timestamp"].dt.date <= end_date)
)

if selected_sources:
    mask &= df["SourceCommonName"].isin(selected_sources)

if selected_sentiments:
    mask &= df["Sentiment"].isin(selected_sentiments)

if selected_themes:
    theme_docs = theme_exploded_full.index[theme_exploded_full["Themes"].isin(selected_themes)]
    mask &= df.index.isin(theme_docs)

if selected_persons:
    person_docs = persons_exploded_full.index[persons_exploded_full["V2Persons"].isin(selected_persons)]
    mask &= df.index.isin(person_docs)

if selected_frames:
    frame_docs = frames_exploded_full.index[frames_exploded_full["Frames"].isin(selected_frames)]
    mask &= df.index.isin(frame_docs)

if search_keyword.strip():
    kw = search_keyword.strip()
    mask &= (
        df["PageTitle"].str.contains(kw, case=False, na=False, regex=False)
        | df["DocumentIdentifier"].str.contains(kw, case=False, na=False, regex=False)
        | df["Themes"].str.contains(kw, case=False, na=False, regex=False)
        | df["SourceCommonName"].str.contains(kw, case=False, na=False, regex=False)
        | df["V2Persons"].str.contains(kw, case=False, na=False, regex=False)
    )

# Kopya analizi sekmesi, tekilleştirme seçiminden bağımsız olarak tüm kopyaları görmeli
scope_df = df[mask]

if dedupe_copies:
    mask &= ~df["IsCopy"]

filtered_df = df[mask].copy()

if filtered_df.empty:
    st.warning("Seçilen süzgeçlere uyan haber bulunamadı. Süzgeçleri gevşetin veya sıfırlayın.")
    st.button("Süzgeçleri sıfırla", icon=":material/restart_alt:", on_click=reset_filters, type="primary",
              key="reset_empty")
    render_footer()
    st.stop()

# --------------------------------------------------------------------------------
# KPI METRİK KARTLARI
# --------------------------------------------------------------------------------
theme_exploded_filtered = with_doc_fields(theme_exploded_full, filtered_df)
theme_view = theme_exploded_filtered[~theme_exploded_filtered["Family"].isin(excluded_families)]

if data_source == SRC_CSV:
    card_start, card_end = filtered_df["pub_timestamp"].min(), filtered_df["pub_timestamp"].max()
else:
    card_start, card_end = q_start, q_end
render_card_header(card_title_text, card_start, card_end, filtered_df)

col1, col2, col3, col4, col5 = st.columns(5)
col1.metric("Toplam Haber Sayısı", fmt_int(len(filtered_df)))
col2.metric(
    "Tekil İçerik",
    fmt_int(int((~filtered_df["IsCopy"]).sum())),
    help="Aynı veya benzer başlıklı ajans metninin farklı sitelerdeki kopyaları tek sayılır (bkz. Kopya Haberler).",
)
col3.metric("Ortalama Duygu Tonu", f"{filtered_df['AvgTone'].mean():.2f}")
col4.metric("Benzersiz Tema Sayısı", fmt_int(theme_exploded_filtered["Themes"].nunique()))
col5.metric("Benzersiz Kaynak Sayısı", fmt_int(filtered_df["SourceCommonName"].nunique()))
# Alıntılar ve sayfa değişiklikleri yalnızca konu aramalarında (BigQuery/yerel arşiv) okunur; kenar çubuğu
# filtreleri uygulanmış haberler için, en yeniler önce (yayında en fazla 800 haber)
recent = filtered_df.sort_values("pub_timestamp", ascending=False)
if data_source == SRC_BQ:
    # Yayında filtre metni sınırlı: en yeni 800 haber. Okuma aralığı da bu haberlerin tarih aralığına daraltılır;
    # aksi halde alıntı ve değişiklik tabloları tüm aralık boyunca taranır (1 yılda ~50 sn yerine ~10 sn)
    recent = recent.head(800)
recent_urls = tuple(recent["DocumentIdentifier"])
recent_key = hashlib.md5("\n".join(recent_urls).encode()).hexdigest()
extra_start = max(as_date(card_start), as_date(recent["pub_timestamp"].min()))
extra_end = min(as_date(card_end), as_date(recent["pub_timestamp"].max()))
is_local_source = data_source == SRC_LOCAL

# Birbirinden bağımsız dört okuma (arşiv dışı künyeler, kaynak dizini, alıntılar, sayfa değişiklikleri) aynı anda
# yapılır; çoğu ağ beklemesidir ve sırayla yapıldığında ilk çizimde ~25 sn ekliyordu
card_reads = {"presence": lambda: source_presence(card_start, card_end)}
if data_source != SRC_CSV:
    card_reads["extra"] = lambda: extra_fetch(q_term)
if quotes_df is not None:
    card_reads["quotes"] = lambda: fetch_quotes(recent_key, recent_urls, extra_start, extra_end, is_local_source)
    card_reads["gdg"] = lambda: fetch_page_changes(recent_key, recent_urls, extra_start, extra_end, is_local_source)
card_data = run_parallel(card_reads)

# Arşiv dışı (ek toplama) eşleşmeleri: yalnızca konu aramalarında ve ek toplama varsa. Okunamazsa kart yine gösterilir
extra_all = card_data.get("extra")
if not isinstance(extra_all, pd.DataFrame):
    extra_all = pd.DataFrame()
extra_in_range = extra_all[(extra_all["pub_timestamp"] >= pd.Timestamp(as_date(card_start))) &
                           (extra_all["pub_timestamp"] < pd.Timestamp(as_date(card_end)) + pd.Timedelta(days=1))] \
    if len(extra_all) else None
coverage = coverage_report(filtered_df, card_start, card_end, extra_in_range)
render_coverage(coverage)

gdg_df, changes, changes_error = None, None, ""
if quotes_df is not None:
    quotes_df = card_data["quotes"]
    if not isinstance(quotes_df, pd.DataFrame):
        quotes_df = pd.DataFrame(columns=["url", "date", "pre", "quote", "post"])
    if isinstance(card_data["gdg"], Exception):
        changes_error = str(card_data["gdg"])
    else:
        gdg_df = card_data["gdg"]
        changes = summarize_changes(gdg_df)
_ai_kayit = st.session_state.get("ai_summary")
_ai_key = (card_title_text, str(card_start), str(card_end), len(filtered_df))
render_topic_card(filtered_df, card_start, card_end, persons_exploded_full, orgs_exploded_full, topic_stems,
                  quotes_df, (_ai_kayit[1].get("zirveler") if _ai_kayit and _ai_kayit[0] == _ai_key else None))
render_extra(extra_all, card_start, card_end)

# --- Yapay zekâ özeti (N2): karttaki rakamları düzyazıya çevirir; ölçüm yapmaz ---
ai_key = _ai_key
if llm_hazir():
    ai_col1, ai_col2 = st.columns([1, 3])
    if ai_col1.button("Yapay zekâ özeti", key="ai_make", width="stretch", icon=":material/auto_awesome:",
                      help="Konu kartının rakamlarını 4-6 paragraflık bir değerlendirmeye çevirir. "
                           "Yalnızca karttaki veriyi kullanır; başka kaynağa bakmaz, neden-sonuç kurmaz."):
        from llm import anlatici, butce

        with st.spinner("Özet yazılıyor..."):
            try:
                ai_facts = card_facts(filtered_df, card_start, card_end, persons_exploded_full,
                                      orgs_exploded_full, topic_stems)
                titled = filtered_df["PageTitle"][filtered_df["PageTitle"] != ""]
                ornekler = titled.sample(min(len(titled), anlatici.ORNEK_BASLIK), random_state=7) \
                    if len(titled) else []
                paket = anlatici.paket(card_title_text, card_start, card_end, data_source, filtered_df,
                                       ai_facts, coverage, changes, ornekler)
                st.session_state["ai_summary"] = (ai_key, anlatici.anlat(paket))
                st.rerun()   # zirve açıklamaları kartın üstünde çizilir; sayfa yeniden kurulmalı
            except butce.ButceAsimi as exc:
                st.session_state.pop("ai_summary", None)
                st.error(f"Bütçe tavanı: {exc}")
            except Exception as exc:
                st.session_state.pop("ai_summary", None)
                st.error(f"Özet üretilemedi: {type(exc).__name__}: {exc}")
    ozet = st.session_state.get("ai_summary")
    if ozet and ozet[0] == ai_key:
        render_anlati(ozet[1])

# --- PDF rapor ---
pdf_key = (card_title_text, str(card_start), str(card_end), len(filtered_df), frame_set_name)
pdf_col1, pdf_col2, _ = st.columns([1, 1, 3])
if pdf_col1.button("PDF rapor hazırla", key="pdf_make", width="stretch", type="primary",
                   icon=":material/picture_as_pdf:",
                   help="Konu kartının markalı PDF raporu: zaman şeridi, siteler, ton, aktörler, yayılan haberler, "
                        "alıntılar, yayın sonrası değişiklikler ve yöntem notu. Kenar çubuğu filtreleri uygulanır."):
    with st.spinner("Rapor hazırlanıyor..."):
        facts = card_facts(filtered_df, card_start, card_end, persons_exploded_full, orgs_exploded_full, topic_stems)
        best_quotes = top_quotes(quotes_df) if isinstance(quotes_df, pd.DataFrame) else None
        st.session_state["pdf_report"] = (pdf_key, build_pdf_report(
            card_title_text, card_start, card_end, filtered_df, facts, best_quotes,
            changes if changes and changes[0] else None, data_source, coverage,
            (_ai_kayit[1].get("zirveler") if _ai_kayit and _ai_kayit[0] == _ai_key else None),
        ))
report = st.session_state.get("pdf_report")
if report and report[0] == pdf_key:
    slug = re.sub(r"[^a-z0-9]+", "-", fold_tr(card_title_text)).strip("-")[:40] or "rapor"
    pdf_col2.download_button("PDF'i indir", report[1], icon=":material/download:", type="primary", file_name=f"{APP_SLUG}_{slug}_{date.today():%Y%m%d}.pdf",
                             mime="application/pdf", width="stretch", key="pdf_download")

st.markdown('<div class="mk-kicker" style="margin-top:1.5rem">Derin analiz</div>', unsafe_allow_html=True)

# --------------------------------------------------------------------------------
# SEKMELER
# --------------------------------------------------------------------------------
# Analizler üç gruptadır; grubun sekmelerinden yalnızca açık olanın gövdesi çalışır (on_change="rerun" ile .open).
# Gizli sekmelerdeki grafikler kurulmadığı için büyük aramalarda her tıklama belirgin şekilde hızlanır.
V_TIME, V_TONE, V_THEME = ":material/show_chart: Zaman serisi", ":material/contrast: Duygu tonu", \
    ":material/category: Tema analizi"
V_ECHO, V_CHANGE, V_FRAME = ":material/cell_tower: Yankı haritası", ":material/difference: Değişiklik dedektifi", \
    ":material/title: Başlık ve çerçeve"
V_ACTORS, V_MAP, V_COPY = ":material/groups: Kişi ve kurum", ":material/map: Harita", \
    ":material/content_copy: Kopya haberler"
V_TABLE, V_GUIDE = ":material/table_rows: Detay tablosu", ":material/menu_book: Tema rehberi"
V_EVENTS = ":material/event_note: Olay türleri"
DEEP_GROUPS = {
    ":material/insights: Genel bakış": [V_TIME, V_TONE, V_THEME],
    ":material/manage_search: Araştırma araçları": [V_ECHO, V_CHANGE, V_FRAME, V_ACTORS, V_EVENTS, V_MAP, V_COPY],
    ":material/dataset: Veri": [V_TABLE, V_GUIDE],
}
deep_group = st.segmented_control(
    "Analiz grubu", list(DEEP_GROUPS), default=list(DEEP_GROUPS)[0], key="deep_group", label_visibility="collapsed",
) or list(DEEP_GROUPS)[0]
deep_views = DEEP_GROUPS[deep_group]
deep_tabs = dict(zip(deep_views, st.tabs(deep_views, key=f"deep_tabs_{deep_views[0]}", on_change="rerun")))
open_view = next((v for v, t in deep_tabs.items() if t.open), deep_views[0])

# --- YANKI HARİTASI ---
if open_view == V_ECHO:
    with deep_tabs[V_ECHO]:
        st.subheader("Yankı Haritası")
        st.caption("Bir haberin ilk yayımlandığı siteden diğer sitelere zaman içinde yayılımı. Kopyalar, kenar çubuğundaki "
                   "'Kopya tespiti ayarları'na göre bulunur; tekilleştirme seçiminden bağımsızdır.")
        echo_sites = scope_df.groupby("CopyGroup")["SourceCommonName"].nunique()
        echo_sites = echo_sites[echo_sites >= 3].sort_values(ascending=False).head(40)
        if echo_sites.empty:
            st.info("Bu aramada en az 3 siteye yayılan haber yok.")
        else:
            firsts = scope_df.sort_values("pub_timestamp").groupby("CopyGroup").first()
            echo_labels = {
                gid: f"{n} site · {firsts.loc[gid, 'pub_timestamp']:%d.%m.%Y} · {(firsts.loc[gid, 'PageTitle'] or '')[:90]}"
                for gid, n in echo_sites.items()
            }
            echo_gid = st.selectbox("Haber (en çok siteye yayılanlar)", list(echo_labels), format_func=echo_labels.get,
                                    key="echo_pick")
            echo_group = scope_df[scope_df["CopyGroup"] == echo_gid]
            es = echo_summary(echo_group)
            m1, m2, m3, m4 = st.columns(4)
            m1.metric("İlk yayımlayan", es["first_site"])
            m2.metric("İlk yayın", f"{es['first_time']:%d.%m.%Y %H:%M}")
            m3.metric("1 saat içinde", f"{es['within_1h']} site")
            m4.metric("24 saat içinde", f"{es['within_24h']} / {es['sites']} site")
            by_site = es["by_site"]
            fig_echo = go.Figure()
            for site, hrs in by_site.items():
                fig_echo.add_trace(go.Scatter(x=[0, hrs], y=[site, site], mode="lines", showlegend=False,
                                              line=dict(color=MK["line_strong"], width=1), hoverinfo="skip"))
            fig_echo.add_trace(go.Scatter(
                x=by_site.values, y=by_site.index, mode="markers", showlegend=False,
                marker=dict(size=10, color=[MK["red"]] + [MK["ink"]] * (len(by_site) - 1)),
                hovertemplate="%{y}: ilk yayından %{x:.1f} saat sonra<extra></extra>",
            ))
            fig_echo.update_layout(template=PLOTLY_TEMPLATE, height=max(300, 22 * len(by_site) + 80),
                                   xaxis_title="İlk yayından sonra geçen süre (saat)", yaxis_title="",
                                   yaxis=dict(autorange="reversed"))
            st.plotly_chart(fig_echo, width="stretch")
            cumulative = pd.Series(range(1, len(by_site) + 1), index=by_site.values)
            fig_cum = go.Figure(go.Scatter(x=cumulative.index, y=cumulative.values, mode="lines", line_shape="hv",
                                           line=dict(color=MK["red"], width=2), fill="tozeroy",
                                           fillcolor="rgba(169,71,60,0.08)"))
            fig_cum.update_layout(template=PLOTLY_TEMPLATE, height=220, xaxis_title="Saat", yaxis_title="Site sayısı",
                                  title="Yayılım eğrisi")
            st.plotly_chart(fig_cum, width="stretch")
            data_note(es["first_time"], es["first_time"],
                      "GDELT haberleri 15 dakikalık taramalarla topladığı için aynı 15 dakikada yayımlananlar aynı anda "
                      "görünür; sıralama bu çözünürlükle sınırlıdır. İlk görünen site, metnin asıl kaynağı olmayabilir "
                      "(örn. ajans).")
            with st.expander("Kanıtı göster · yayılan haberin tüm kopyaları"):
                evidence_table(echo_group, "ev_echo_table")

# --- DEĞİŞİKLİK DEDEKTİFİ ---
if open_view == V_CHANGE:
    with deep_tabs[V_CHANGE]:
        st.subheader("Değişiklik Dedektifi")
        st.caption("Yayımlandıktan sonra başlığı veya metni değiştirilen haberler. GDELT sayfaları yayından sonra "
                   "yeniden ziyaret edip farkları kaydeder (GDG).")
        if data_source == SRC_CSV:
            st.info("Değişiklik verisi yalnızca bulut ve yerel arşiv aramalarında gösterilir.")
        elif changes_error:
            st.error(f"Sayfa değişikliği verisi okunamadı: {changes_error}")
        elif not changes or changes[0] == 0:
            st.info(f"Bu aramadaki haberler için sayfa kontrol verisi yok. Veri {PAGE_CHANGES_SINCE:%d.%m.%Y} "
                    "sonrasını kapsar.")
        else:
            checked, title_changes, text_changes = changes
            article_info = (filtered_df.drop_duplicates("DocumentIdentifier")
                            .set_index("DocumentIdentifier")[["SourceCommonName", "pub_timestamp"]])
            d1, d2, d3 = st.columns(3)
            d1.metric("Kontrol edilen haber", fmt_int(checked))
            d2.metric("Başlığı değişen", fmt_int(len(title_changes)), f"%{100 * len(title_changes) / checked:.1f}",
                      delta_color="off")
            d3.metric("Metni değişen", fmt_int(len(text_changes)), f"%{100 * len(text_changes) / checked:.1f}",
                      delta_color="off", help=f"En az %{TEXT_CHANGE_MIN_PERC:.0f} karakteri değişen, sayı ve tarih "
                                              "güncellemesinden fazlası olan haberler.")
            site_of = article_info["SourceCommonName"]
            checked_by_site = gdg_df.drop_duplicates("page_url")["page_url"].map(site_of).value_counts()
            changed_by_site = title_changes["page_url"].map(site_of).value_counts()
            site_rates = (pd.DataFrame({"Kontrol": checked_by_site, "Başlığı değişen": changed_by_site})
                          .fillna(0).astype(int).query("Kontrol >= 10"))
            if not site_rates.empty:
                site_rates["Oran (%)"] = (100 * site_rates["Başlığı değişen"] / site_rates["Kontrol"]).round(1)
                card_title("Siteler · başlık değiştirme oranı (en az 10 kontrol edilen haber)")
                st.dataframe(site_rates.sort_values("Oran (%)", ascending=False).head(15), width="stretch")
            card_title("Başlığı değişen haberler (en yeniler üstte)")
            sites_changed = ["Tümü"] + sorted(title_changes["page_url"].map(site_of).dropna().unique())
            pick_site = st.selectbox("Site", sites_changed, key="chg_site")
            shown = title_changes.sort_values("fetchdate_check", ascending=False)
            if pick_site != "Tümü":
                shown = shown[shown["page_url"].map(site_of) == pick_site]
            for _, r in shown.head(40).iterrows():
                old_html, new_html = diff_html(r["old"], r["new"])
                pub = article_info["pub_timestamp"].get(r["page_url"])
                st.markdown(
                    f'<div class="mk-rank" style="display:block"><small style="color:{MK["muted"]}">'
                    f'{html.escape(str(site_of.get(r["page_url"], "")))} · yayın {pub:%d.%m.%Y %H:%M} · '
                    f'fark edildi {r["fetchdate_check"]:%d.%m.%Y} · '
                    f'<a href="{html.escape(r["page_url"])}" target="_blank">haberi aç</a></small><br>'
                    f'<span style="color:{MK["muted"]}">Önce:</span> {old_html}<br>'
                    f'<span style="color:{MK["muted"]}">Sonra:</span> {new_html}</div>'
                    if pub is not None else "",
                    unsafe_allow_html=True,
                )
            if len(shown) > 40:
                st.caption(f"İlk 40 değişiklik gösteriliyor ({fmt_int(len(shown))} değişikliğin en yenileri).")
            with st.expander(f"Metni değişen haberler ({fmt_int(len(text_changes))})"):
                for _, r in text_changes.head(20).iterrows():
                    st.markdown(f'**{html.escape(str(site_of.get(r["page_url"], "")))}** · karakterlerin '
                                f'%{r["perc_changedchars"]:.0f}\'i değişti · '
                                f'<a href="{html.escape(r["page_url"])}" target="_blank">haberi aç</a>',
                                unsafe_allow_html=True)
                    for c in list(r["changes"])[:3]:
                        old_html, new_html = diff_html(c.get("from") or "", c.get("to") or "")
                        st.markdown(f'<div class="mk-note">Önce: {old_html or "—"}<br>Sonra: {new_html or "—"}</div>',
                                    unsafe_allow_html=True)
            data_note(max(as_date(card_start), PAGE_CHANGES_SINCE), card_end,
                      f"Sayfa değişikliği verisi {PAGE_CHANGES_SINCE:%d.%m.%Y} sonrasını kapsar. GDELT sayfaları "
                      "yayından birkaç saat, bir gün ve bir hafta sonra yeniden ziyaret eder; değişiklik o aralıkta "
                      "yakalanır. Dinamik sayfalar ve site şablonu değişiklikleri yanlış alarm üretebilir; bir "
                      "değişikliğin nedenini veri söylemez."
                      + (" Yayındaki panelde en yeni 800 haber kontrol edilir." if data_source == SRC_BQ else ""))

# --- TAB 1: TEMA ANALİZİ ---
if open_view == V_THEME:
    with deep_tabs[V_THEME]:
        st.subheader(f"En Çok Geçen İlk {top_n} Tema")
        top_themes_df = get_top_themes(theme_view, n=top_n)

        if top_themes_df.empty:
            st.info("Bu filtre kombinasyonu için tema verisi bulunamadı.")
        else:
            fig_themes = px.bar(
                top_themes_df.sort_values("Count"),
                x="Count",
                y="Theme",
                orientation="h",
                text="Count",
                color="Count",
                color_continuous_scale=[[0, MK["surface_deep"]], [1, MK["ink"]]],
                title=f"En Popüler {top_n} Tema (Haber Sayısına Göre)",
            )
            fig_themes.update_layout(
                height=max(400, top_n * 25),
                template=PLOTLY_TEMPLATE,
                showlegend=False,
                yaxis_title="",
                xaxis_title="Haber Sayısı",
            )
            st.plotly_chart(fig_themes, width="stretch")

            st.subheader("Tema Ailelerine Göre Dağılım")
            family_counts = (
                theme_view.reset_index()
                .drop_duplicates(["index", "Family"])
                .groupby("Family")
                .size()
                .reset_index(name="HaberSayisi")
                .sort_values("HaberSayisi")
            )
            fig_family = px.bar(
                family_counts, x="HaberSayisi", y="Family", orientation="h", text="HaberSayisi",
                title="Her tema ailesinin geçtiği haber sayısı",
            )
            fig_family.update_layout(
                height=max(400, len(family_counts) * 25), template=PLOTLY_TEMPLATE,
                yaxis_title="", xaxis_title="Haber Sayısı",
            )
            st.plotly_chart(fig_family, width="stretch")

# --- TAB 2: DUYGU TONU ANALİZİ ---
if open_view == V_TONE:
    with deep_tabs[V_TONE]:
        tone_valid = filtered_df.dropna(subset=["AvgTone", "WordCount"])
        m1, m2, m3 = st.columns(3)
        m1.metric("Medyan Ton", f"{filtered_df['AvgTone'].median():.2f}")
        if not tone_valid.empty and tone_valid["WordCount"].sum() > 0:
            weighted_tone = np.average(tone_valid["AvgTone"], weights=tone_valid["WordCount"])
            m2.metric("Kelime Ağırlıklı Ort. Ton", f"{weighted_tone:.2f}", help="Uzun haberlerin tona etkisi daha fazladır.")
        m3.metric("Ortalama Polarite", f"{filtered_df['Polarity'].mean():.2f}", help="Pozitif + negatif kelime yoğunluğu; duygusal yüklülük.")

        col_a, col_b = st.columns([1, 1.3])

        with col_a:
            st.subheader("Duygu Dağılımı")
            sentiment_counts = filtered_df["Sentiment"].value_counts().reset_index()
            sentiment_counts.columns = ["Sentiment", "Count"]

            color_map = {"Pozitif": MK["green"], "Nötr": MK["line_strong"], "Negatif": MK["red"], "Bilinmiyor": MK["muted"]}
            fig_pie = px.pie(
                sentiment_counts,
                names="Sentiment",
                values="Count",
                color="Sentiment",
                color_discrete_map=color_map,
                hole=0.45,
                title=f"Haberlerin Duygu Tonu Dağılımı (nötr bant ±{tone_threshold:g})",
            )
            fig_pie.update_traces(textinfo="percent+label")
            fig_pie.update_layout(template=PLOTLY_TEMPLATE)
            st.plotly_chart(fig_pie, width="stretch")

        with col_b:
            st.subheader("AvgTone Skor Dağılımı")
            fig_hist = px.histogram(
                filtered_df,
                x="AvgTone",
                nbins=50,
                color_discrete_sequence=["#3498db"],
                title="Ortalama Ton Skorlarının Histogramı",
            )
            fig_hist.add_vline(x=0, line_dash="dash", line_color="white", annotation_text="0")
            for x in (-tone_threshold, tone_threshold):
                fig_hist.add_vline(x=x, line_dash="dot", line_color="#95a5a6")
            fig_hist.update_layout(template=PLOTLY_TEMPLATE, xaxis_title="AvgTone", yaxis_title="Haber Sayısı")
            st.plotly_chart(fig_hist, width="stretch")

        st.subheader("Ton ve Polarite")
        st.caption(
            "Aynı ortalama tona sahip haberler çok farklı olabilir: düşük polarite sakin bir dil, "
            "yüksek polarite hem olumlu hem olumsuz kelimelerin yoğun olduğu, duygusal yüklü bir dil demektir."
        )
        fig_scatter = px.scatter(
            tone_valid,
            x="AvgTone",
            y="Polarity",
            size="WordCount",
            color="Sentiment",
            color_discrete_map=color_map,
            hover_data={"SourceCommonName": True, "DocumentIdentifier": True, "WordCount": True},
            size_max=30,
            opacity=0.7,
        )
        fig_scatter.update_layout(template=PLOTLY_TEMPLATE, xaxis_title="AvgTone", yaxis_title="Polarite")
        st.plotly_chart(fig_scatter, width="stretch")

        st.subheader("Kaynaklara Göre Ton Karşılaştırması")
        min_articles = st.slider("Kaynak başına en az haber sayısı", 2, 20, 3, key="min_articles_tone")
        source_counts = filtered_df["SourceCommonName"].value_counts()
        eligible_sources = source_counts[source_counts >= min_articles].head(20).index
        if len(eligible_sources) == 0:
            st.info("Bu eşikte yeterli haberi olan kaynak yok; eşiği düşürün.")
        else:
            source_tone = filtered_df[filtered_df["SourceCommonName"].isin(eligible_sources)]
            order = source_tone.groupby("SourceCommonName")["AvgTone"].median().sort_values().index.tolist()
            fig_box = px.box(
                source_tone, x="AvgTone", y="SourceCommonName", points="all",
                category_orders={"SourceCommonName": order},
                hover_data={"DocumentIdentifier": True},
                title="Kaynak bazında AvgTone dağılımı (medyana göre sıralı, en fazla 20 kaynak)",
            )
            fig_box.add_vline(x=0, line_dash="dash", line_color="white")
            fig_box.update_layout(template=PLOTLY_TEMPLATE, height=max(400, len(order) * 30), yaxis_title="")
            st.plotly_chart(fig_box, width="stretch")

# --- TAB 3: ZAMAN SERİSİ ---
if open_view == V_TIME:
    with deep_tabs[V_TIME]:
        st.subheader("Zaman İçinde Haber Hacmi ve Duygu Tonu")

        granularity = st.radio("Zaman Aralığı", ["Günlük", "Haftalık", "Aylık", "Yıllık"], horizontal=True, index=2)

        if granularity == "Yıllık":
            temp_df = filtered_df.copy()
            temp_df["Yil"] = temp_df["pub_timestamp"].dt.year
            ts = (
                temp_df.groupby("Yil")
                .agg(HaberSayisi=("DocumentIdentifier", "count"), OrtalamaTon=("AvgTone", "mean"))
                .reset_index()
            )
            ts["pub_timestamp"] = pd.to_datetime(ts["Yil"].astype(str) + "-01-01")
        else:
            freq_map = {"Günlük": "D", "Haftalık": "W", "Aylık": "ME"}
            freq = freq_map[granularity]

            ts = (
                filtered_df.set_index("pub_timestamp")
                .resample(freq)
                .agg(HaberSayisi=("DocumentIdentifier", "count"), OrtalamaTon=("AvgTone", "mean"))
                .reset_index()
            )

        fig_ts = go.Figure()
        fig_ts.add_trace(
            go.Bar(x=ts["pub_timestamp"], y=ts["HaberSayisi"], name="Haber Sayısı", marker_color="#3498db", opacity=0.6, yaxis="y1")
        )
        fig_ts.add_trace(
            go.Scatter(
                x=ts["pub_timestamp"], y=ts["OrtalamaTon"], name="Ortalama Ton",
                mode="lines+markers", line=dict(color="#f1c40f", width=3), yaxis="y2",
            )
        )

        xaxis_config = dict(title="Tarih")
        if granularity == "Yıllık":
            xaxis_config = dict(title="Yıl", tickformat="%Y", dtick="M12")

        fig_ts.update_layout(
            template=PLOTLY_TEMPLATE,
            title=f"{granularity} Haber Hacmi ve Ortalama Duygu Tonu",
            yaxis=dict(title="Haber Sayısı"),
            yaxis2=dict(title="Ortalama Ton", overlaying="y", side="right"),
            xaxis=xaxis_config,
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
            hovermode="x unified",
        )
        st.plotly_chart(fig_ts, width="stretch")

        st.subheader("En Popüler Temaların Zaman İçindeki Seyri")
        top5_names = get_top_themes(theme_view, n=5)["Theme"].tolist()
        if top5_names:
            theme_ts_source = theme_view[theme_view["Themes"].isin(top5_names)].copy()

            if granularity == "Yıllık":
                theme_ts_source["Yil"] = theme_ts_source["pub_timestamp"].dt.year
                theme_ts = (
                    theme_ts_source.groupby(["Themes", "Yil"])
                    .size()
                    .reset_index(name="Count")
                )
                theme_ts["pub_timestamp"] = pd.to_datetime(theme_ts["Yil"].astype(str) + "-01-01")
            else:
                freq_map = {"Günlük": "D", "Haftalık": "W", "Aylık": "ME"}
                freq = freq_map[granularity]
                theme_ts = (
                    theme_ts_source.set_index("pub_timestamp")
                    .groupby("Themes")
                    .resample(freq)
                    .size()
                    .reset_index(name="Count")
                )

            fig_theme_ts = px.line(
                theme_ts, x="pub_timestamp", y="Count", color="Themes",
                title=f"İlk 5 Temanın {granularity} Frekansı", markers=True,
            )
            if granularity == "Yıllık":
                fig_theme_ts.update_layout(template=PLOTLY_TEMPLATE, xaxis=dict(title="Yıl", tickformat="%Y", dtick="M12"), yaxis_title="Haber Sayısı")
            else:
                fig_theme_ts.update_layout(template=PLOTLY_TEMPLATE, xaxis_title="Tarih", yaxis_title="Haber Sayısı")

            st.plotly_chart(fig_theme_ts, width="stretch")
        else:
            st.info("Zaman serisi için yeterli tema verisi yok.")

# --- BAŞLIK & ÇERÇEVE ---
if open_view == V_FRAME:
    with deep_tabs[V_FRAME]:
        st.caption(
            "Her haber, başlığındaki anahtar kelimelere göre bir veya daha fazla çerçeveye atanır "
            "(başlık yoksa URL metni kullanılır). Çerçeve setini kenar çubuğundaki 'Çerçeve seti' ile değiştirebilir "
            "veya 'Özel' seçip kendi listenizi yazabilirsiniz."
        )
        frames_filtered = with_doc_fields(frames_exploded_full, filtered_df)

        col_fa, col_fb = st.columns([1, 1.3])
        with col_fa:
            st.subheader("Çerçeve Dağılımı")
            frame_summary = entity_summary(frames_filtered, "Frames", len(all_frames))
            fig_frames = px.bar(
                frame_summary.sort_values("HaberSayisi"),
                x="HaberSayisi", y="Frames", orientation="h", text="HaberSayisi",
                color="OrtalamaTon", color_continuous_scale=MK_TONE_SCALE, color_continuous_midpoint=0,
                hover_data={"OrtalamaTon": ":.2f"},
            )
            fig_frames.update_layout(
                template=PLOTLY_TEMPLATE, yaxis_title="", xaxis_title="Haber Sayısı", coloraxis_colorbar_title="Ort. Ton",
            )
            st.plotly_chart(fig_frames, width="stretch")

        with col_fb:
            st.subheader("Çerçevelerin Zaman İçindeki Seyri")
            frame_granularity = st.radio("Zaman Aralığı", ["Günlük", "Haftalık", "Aylık"], horizontal=True, index=1, key="frame_gran")
            frame_freq = {"Günlük": "D", "Haftalık": "W", "Aylık": "ME"}[frame_granularity]
            frame_ts = (
                frames_filtered.set_index("pub_timestamp")
                .groupby("Frames")
                .resample(frame_freq)
                .size()
                .reset_index(name="Count")
            )
            fig_frame_ts = px.line(frame_ts, x="pub_timestamp", y="Count", color="Frames", markers=True)
            fig_frame_ts.update_layout(template=PLOTLY_TEMPLATE, xaxis_title="Tarih", yaxis_title="Haber Sayısı", legend_title="")
            st.plotly_chart(fig_frame_ts, width="stretch")

        st.subheader("Kaynak × Çerçeve")
        st.caption("En çok haber yapan 15 kaynağın haberlerinin çerçevelere göre oranı (satır toplamı %100 olmayabilir; çoklu çerçeve).")
        top_sources = filtered_df["SourceCommonName"].value_counts().head(15).index
        source_frame = (
            frames_filtered.join(filtered_df[["SourceCommonName"]])
            .query("SourceCommonName in @top_sources")
            .groupby(["SourceCommonName", "Frames"]).size().unstack(fill_value=0)
        )
        source_totals = filtered_df["SourceCommonName"].value_counts().reindex(source_frame.index)
        source_frame_pct = source_frame.div(source_totals, axis=0) * 100
        fig_heat = px.imshow(
            source_frame_pct.round(0), text_auto=".0f", aspect="auto", color_continuous_scale=[[0, MK["surface"]], [1, MK["cyan"]]],
            labels={"color": "% haber"},
        )
        fig_heat.update_layout(template=PLOTLY_TEMPLATE, xaxis_title="", yaxis_title="", height=max(350, len(source_frame) * 30))
        st.plotly_chart(fig_heat, width="stretch")

        st.subheader("Başlıklarda En Sık Geçen Kelimeler")
        titles = filtered_df.loc[filtered_df["TitleCore"] != "", "TitleCore"]
        if titles.empty:
            st.info("Başlık verisi yok: yüklenen dosyada PageTitle kolonu bulunmuyor.")
        else:
            words = titles.map(lambda t: set(title_words(t))).explode().dropna()  # haber başına tekil
            words = words[~words.map(fold_tr).isin(FOLDED_STOPWORDS) & ~words.map(word_stem).isin(topic_stems)]
            word_counts = words.value_counts().head(25).reset_index()
            word_counts.columns = ["Kelime", "HaberSayisi"]
            fig_words = px.bar(word_counts.sort_values("HaberSayisi"), x="HaberSayisi", y="Kelime", orientation="h")
            fig_words.update_layout(template=PLOTLY_TEMPLATE, height=650, yaxis_title="", xaxis_title="Başlığında geçtiği haber sayısı")
            st.plotly_chart(fig_words, width="stretch")

        language_counts = filtered_df["Language"].map(lambda c: LANGUAGE_NAMES.get(c, c)).value_counts()
        st.caption("Kaynak dili dağılımı: " + ", ".join(f"{lang} {fmt_int(n)}" for lang, n in language_counts.items()))

# --- HARİTA ---
if open_view == V_MAP:
    with deep_tabs[V_MAP]:
        locations_filtered = locations_exploded_full[locations_exploded_full.index.isin(filtered_df.index)]
        if locations_filtered.empty:
            st.info("Konum verisi yok: yüklenen dosyada V2Locations kolonu bulunmuyor.")
        else:
            st.caption(
                ":material/warning: GDELT bazı kişi adlarını yer adı sanıp kodlar (örn. bir soyadı aynı adlı bir köye düşer). "
                "Adı bir konu kelimesiyle veya haberdeki bir kişi adıyla aynı olan yerler varsayılan olarak "
                "hariç tutulur; listeyi aşağıdan düzenleyebilirsiniz."
            )
            location_options = locations_filtered["LocName"].value_counts().index.tolist()
            person_named_places = detect_person_named_places(
                locations_filtered[["LocType", "LocName"]], persons_exploded_full, topic_stems
            )
            excluded_locations = st.multiselect(
                "Hariç tutulacak (hatalı kodlanmış) yerler",
                options=location_options,
                default=[loc for loc in person_named_places if loc in location_options],
                placeholder="Seçin",
            )
            type_options = sorted(locations_filtered["LocType"].dropna().unique())
            selected_types = st.multiselect(
                "Yer türü",
                options=type_options,
                default=[t for t in type_options if t != "1"],
                format_func=lambda t: LOCATION_TYPES.get(t, t),
                placeholder="Seçin",
                help="Ülke düzeyindeki kayıtlar (ör. 'Turkey') ülke merkezine düşer ve haritada tek bir büyük nokta oluşturur.",
            )

            loc_view = locations_filtered[
                ~locations_filtered["LocName"].isin(excluded_locations)
                & locations_filtered["LocType"].isin(selected_types)
            ].join(filtered_df[["AvgTone"]])

            loc_summary = (
                loc_view.dropna(subset=["Lat", "Lon"])
                .groupby("LocName")
                .agg(Lat=("Lat", "first"), Lon=("Lon", "first"), HaberSayisi=("AvgTone", "size"), OrtalamaTon=("AvgTone", "mean"))
                .reset_index()
                .sort_values("HaberSayisi", ascending=False)
            )

            if loc_summary.empty:
                st.info("Seçilen filtrelerle haritada gösterilecek yer kalmadı.")
            else:
                fig_map = px.scatter_map(
                    loc_summary, lat="Lat", lon="Lon", size="HaberSayisi", color="OrtalamaTon",
                    color_continuous_scale=MK_TONE_SCALE, color_continuous_midpoint=0, size_max=40,
                    hover_name="LocName", hover_data={"HaberSayisi": True, "OrtalamaTon": ":.2f", "Lat": False, "Lon": False},
                    zoom=4.5, center={"lat": 39.0, "lon": 35.0}, map_style="carto-positron", height=600,
                )
                fig_map.update_layout(margin=dict(l=0, r=0, t=0, b=0), coloraxis_colorbar_title="Ort. Ton")
                st.plotly_chart(fig_map, width="stretch")

                col_ma, col_mb = st.columns(2)
                with col_ma:
                    st.subheader("En Çok Geçen Yerler")
                    top_locs = loc_summary.head(15).sort_values("HaberSayisi")
                    fig_locs = px.bar(top_locs, x="HaberSayisi", y="LocName", orientation="h", text="HaberSayisi")
                    fig_locs.update_layout(template=PLOTLY_TEMPLATE, yaxis_title="", xaxis_title="Haber Sayısı")
                    st.plotly_chart(fig_locs, width="stretch")
                with col_mb:
                    st.subheader("Haberlerde Geçen Ülkeler")
                    countries = locations_filtered[~locations_filtered["LocName"].isin(excluded_locations)]
                    country_labels = (
                        countries[countries["LocType"] == "1"].groupby("Country")["LocName"]
                        .agg(lambda s: s.value_counts().index[0])
                    )
                    country_counts = (
                        countries.reset_index().drop_duplicates(["index", "Country"])["Country"]
                        .value_counts().head(15).rename_axis("Country").reset_index(name="HaberSayisi")
                    )
                    country_counts["Ülke"] = country_counts["Country"].map(country_labels).fillna(country_counts["Country"])
                    fig_countries = px.bar(country_counts.sort_values("HaberSayisi"), x="HaberSayisi", y="Ülke", orientation="h", text="HaberSayisi")
                    fig_countries.update_layout(template=PLOTLY_TEMPLATE, yaxis_title="", xaxis_title="Haber Sayısı")
                    st.plotly_chart(fig_countries, width="stretch")

# --- TAB 4: KİŞİ & KURUM ---
if open_view == V_ACTORS:
    with deep_tabs[V_ACTORS]:
        entity_n = st.slider("Gösterilecek kişi/kurum sayısı", 5, 40, 20, step=5, key="entity_n")

        # --- Başlıklardan adlar: GDELT'in bozuk adlarının Türkçe karşılığı (pipeline/entities.py sözlüğü) ---
        titled = filtered_df["PageTitle"][filtered_df["PageTitle"] != ""]
        ad_tablo = baslik_adlari(f"{len(filtered_df)}|{card_title_text}", tuple(titled)) if len(titled) \
            else pd.DataFrame(columns=["ad", "tur", "haber"])
        if len(ad_tablo):
            st.subheader("Başlıklardan adlar")
            st.caption(
                "Doğrudan **Türkçe başlıklardan** çıkarılmıştır; GDELT'in makine çevirisinden gelen bozuk "
                "adlarının temiz karşılığıdır. Sözlükte olmayan adlar bulunamaz (sözlük 2019 ortasından "
                "sonraki başlıklardan kurulmuştur), bu yüzden liste GDELT'inkinden kısadır. "
                "Yalnızca başlıkta geçen adları bulur; haber gövdesinde geçenleri bulmaz."
            )
            ad_p, ad_o = st.columns(2)
            for sutun, tur, baslik in [(ad_p, "kişi", "Kişiler"), (ad_o, "kurum", "Kurumlar")]:
                with sutun:
                    alt = ad_tablo[ad_tablo["tur"] == tur].head(entity_n)
                    if alt.empty:
                        st.info(f"Başlıklarda sözlükten {tur} bulunamadı.")
                        continue
                    fig_ad = px.bar(alt.sort_values("haber"), x="haber", y="ad", orientation="h", text="haber")
                    fig_ad.update_traces(marker_color=MK["cyan"])
                    fig_ad.update_layout(template=PLOTLY_TEMPLATE, height=max(300, len(alt) * 25),
                                         yaxis_title="", xaxis_title="Haber sayısı", title=baslik)
                    st.plotly_chart(fig_ad, width="stretch")
            # Kanıt: en çok geçen adın başlıkta göründüğü haberler (tek ad; tüm başlıkları yeniden
            # eşleştirmek pahalı olurdu)
            ilk_ad = ad_tablo["ad"].iloc[0]
            with st.expander(f'Kanıtı göster: başlığında "{ilk_ad}" geçen haberler',
                             icon=":material/fact_check:"):
                desen = r"\b" + re.escape(fold_tr(ilk_ad))
                kanit = filtered_df[filtered_df["PageTitle"].map(fold_tr).str.contains(desen, regex=True)]
                evidence_table(kanit, "ad_kanit")
            st.markdown("---")

        st.subheader("GDELT'in kişi ve kurum alanları")
        st.caption(
            ":material/warning: GDELT Türkçe haberleri makine çevirisiyle İngilizceye aktarıp adları öyle çıkarır. "
            "Bu yüzden bazı kişi/kurum adları bozuk görünebilir (örn. Türkçe harfleri düşmüş soyadları, 'Republican People's Party' = CHP). "
            "Buna karşılık bu alanlar **haber gövdesini** de tarar; yukarıdaki başlık listesinin yakalamadığı "
            "adları içerebilir. Sayılar haber başına tekildir."
        )

        persons_filtered = with_doc_fields(persons_exploded_full, filtered_df)
        orgs_filtered = with_doc_fields(orgs_exploded_full, filtered_df)

        col_p, col_o = st.columns(2)
        for column, data, col_name, title in [
            (col_p, persons_filtered, "V2Persons", "En Çok Geçen Kişiler"),
            (col_o, orgs_filtered, "Organizations", "En Çok Geçen Kurumlar"),
        ]:
            with column:
                st.subheader(title)
                if data.empty:
                    st.info("Bu filtre kombinasyonu için veri yok.")
                    continue
                summary = entity_summary(data, col_name, entity_n)
                fig_entity = px.bar(
                    summary.sort_values("HaberSayisi"),
                    x="HaberSayisi", y=col_name, orientation="h", text="HaberSayisi",
                    color="OrtalamaTon", color_continuous_scale=MK_TONE_SCALE, color_continuous_midpoint=0,
                    hover_data={"OrtalamaTon": ":.2f"},
                )
                fig_entity.update_layout(
                    template=PLOTLY_TEMPLATE, height=max(400, entity_n * 25),
                    yaxis_title="", xaxis_title="Haber Sayısı", coloraxis_colorbar_title="Ort. Ton",
                )
                st.plotly_chart(fig_entity, width="stretch")

# --- OLAY TÜRLERİ ---
if open_view == V_EVENTS:
    with deep_tabs[V_EVENTS]:
        st.subheader("Olay Türleri")
        st.caption(
            "GDELT haber metinlerinden \"kim, kime, ne yaptı\" biçiminde olaylar çıkarır ve CAMEO koduyla sınıflar "
            "(açıklama, görüşme, protesto, saldırı…). Bu görünüm, aramadaki haberlerin anlattığı olayların türlerini "
            "gösterir. Kodlama makineyle yapılır ve gürültülüdür; tek tek olaylara değil, genel dağılıma güvenin."
        )
        if data_source == SRC_CSV:
            st.info("Olay verisi yalnızca bulut ve yerel arşiv aramalarında gösterilir.")
        else:
            try:
                ev = fetch_events(recent_key, recent_urls, extra_start, extra_end, data_source == SRC_LOCAL)
                ev_error = ""
            except Exception as e:
                ev, ev_error = pd.DataFrame(columns=EVENT_COLUMNS), str(e)
            ev_base = recent.loc[recent["pub_timestamp"] >= pd.Timestamp(EVENTS_SINCE), "DocumentIdentifier"].nunique()
            ev_linked = ev["url"].nunique()
            ev_share = 100 * ev_linked / ev_base if ev_base else 0.0
            if ev_error:
                st.error(f"Olay verisi okunamadı: {ev_error}")
            elif ev_base == 0:
                st.info(f"Olay verisi {EVENTS_SINCE:%d.%m.%Y} sonrasını kapsar; bu aralıkta haber yok.")
            elif ev_linked < EVENTS_MIN_ARTICLES or ev_share < EVENTS_MIN_SHARE:
                st.info(
                    f"Bu aramadaki {fmt_int(ev_base)} haberden olaya bağlı olan: {fmt_int(ev_linked)} "
                    f"(%{ev_share:.1f}). Dağılım göstermek için yetersiz (en az {EVENTS_MIN_ARTICLES} haber ve "
                    f"%{EVENTS_MIN_SHARE:.0f} gerekir). GDELT olayları çoğunlukla siyaset, diplomasi, güvenlik ve "
                    "protesto haberlerinden çıkarır; ekonomi haberlerinde neredeyse hiç olay yoktur."
                )
            else:
                ev = ev.copy()
                ev["Tür"] = ev["EventRootCode"].map(CAMEO_ROOTS).fillna("Diğer")
                ev["Sınıf"] = ev["QuadClass"].map(QUAD_CLASSES).fillna("Diğer")
                quad_counts = ev.groupby("Sınıf")["url"].nunique().reindex(list(QUAD_CLASSES.values()), fill_value=0)
                conflict = ev.loc[ev["QuadClass"].isin([3, 4]), "url"].nunique()
                material_conflict = ev.loc[ev["QuadClass"] == 4, "url"].nunique()
                e1, e2, e3, e4 = st.columns(4)
                e1.metric("Olaya bağlı haber", fmt_int(ev_linked), f"%{ev_share:.1f}", delta_color="off", delta_arrow="off",
                          help=f"{EVENTS_SINCE:%d.%m.%Y} sonrasındaki {fmt_int(ev_base)} haberin olay kodlanmış olanları.")
                e2.metric("Farklı olay", fmt_int(ev["GLOBALEVENTID"].nunique()))
                e3.metric("Çatışma içeren", f"%{100 * conflict / ev_linked:.0f}",
                          help="Sözlü veya fiilî çatışma türünde en az bir olay anlatan haberlerin payı.")
                e4.metric("Fiilî çatışma", f"%{100 * material_conflict / ev_linked:.0f}",
                          help="Protesto, güç gösterisi, baskı, saldırı, çatışma veya kitlesel şiddet anlatan haberler.")

                col_qa, col_qb = st.columns([1, 1.4])
                with col_qa:
                    card_title("Olay sınıfları · haber sayısı")
                    quad_df = quad_counts.rename("Haber").reset_index()
                    fig_quad = px.bar(quad_df, x="Haber", y="Sınıf", orientation="h", text="Haber", color="Sınıf",
                                      color_discrete_map=QUAD_COLORS)
                    fig_quad.update_traces(textangle=0)
                    fig_quad.update_layout(template=PLOTLY_TEMPLATE, showlegend=False, height=300,
                                           yaxis_title="", xaxis_title="Haber sayısı",
                                           yaxis=dict(categoryorder="array", categoryarray=list(quad_df["Sınıf"])[::-1]))
                    st.plotly_chart(fig_quad, width="stretch")
                with col_qb:
                    card_title("Olay türleri · haber sayısı (Açıklama yapma hariç)")
                    root_df = (ev[ev["EventRootCode"] != "01"].groupby(["Tür", "Sınıf"])["url"].nunique()
                               .rename("Haber").reset_index().sort_values("Haber").tail(15))
                    fig_root = px.bar(root_df, x="Haber", y="Tür", orientation="h", text="Haber", color="Sınıf",
                                      color_discrete_map=QUAD_COLORS,
                                      category_orders={"Sınıf": list(QUAD_CLASSES.values())})
                    fig_root.update_traces(textangle=0, textposition="outside", cliponaxis=False)
                    fig_root.update_layout(template=PLOTLY_TEMPLATE, height=max(300, 26 * len(root_df) + 110),
                                           yaxis_title="", xaxis_title="Haber sayısı", showlegend=False,
                                           yaxis=dict(categoryorder="total ascending"),
                                           xaxis=dict(range=[0, root_df["Haber"].max() * 1.18]))
                    st.plotly_chart(fig_root, width="stretch")
                    st.caption("Renkler olay sınıflarıdır (soldaki grafik).")
                statements = ev.loc[ev["EventRootCode"] == "01", "url"].nunique()
                st.caption(f"Açıklama yapma: {fmt_int(statements)} haber (%{100 * statements / ev_linked:.0f}). "
                           "Çoğunlukla bir demecin aktarılmasıdır; tek başına bilgi vermediği için grafikte yoktur.")

                card_title("En sık olay ilişkileri · kim, ne, kime")
                pairs = ev[(ev["Actor1Name"].fillna("") != "") & (ev["Actor2Name"].fillna("") != "")
                           & (ev["EventRootCode"] != "01")]
                if pairs.empty:
                    st.info("Her iki tarafı belli olay yok.")
                else:
                    pair_table = (pairs.groupby(["Actor1Name", "Tür", "Actor2Name"])["url"].nunique()
                                  .rename("Haber").reset_index().sort_values("Haber", ascending=False).head(15))
                    st.dataframe(pair_table, hide_index=True, width="stretch", column_config={
                        "Actor1Name": st.column_config.TextColumn("Yapan"),
                        "Tür": st.column_config.TextColumn("Olay"),
                        "Actor2Name": st.column_config.TextColumn("Hedef"),
                    })
                    st.caption("Aktör adları GDELT'in İngilizce kodlamasıdır (TURKEY, POLICE, GOVERNMENT gibi).")

                data_note(max(as_date(card_start), EVENTS_SINCE), card_end,
                          f"Olay verisi {EVENTS_SINCE:%d.%m.%Y} sonrasını kapsar ve GDELT'in makine kodlamasıdır; yer "
                          "ve aktör tespitinde yanılabilir. Bir haber birden fazla olay türü anlatabilir; bu yüzden "
                          "türlerin toplamı haber sayısını aşabilir."
                          + (" Yayında en yeni 800 haber incelenir." if data_source == SRC_BQ else ""))
                with st.expander("Kanıtı göster · olay türleri"):
                    kinds = ev.groupby("Tür")["url"].nunique().sort_values(ascending=False)
                    pick_kind = st.selectbox("Olay türü", list(kinds.index), key="ev_kind",
                                             format_func=lambda k: f"{k} ({fmt_int(kinds[k])} haber)")
                    kind_urls = set(ev.loc[ev["Tür"] == pick_kind, "url"])
                    evidence_table(filtered_df[filtered_df["DocumentIdentifier"].isin(kind_urls)]
                                   .drop_duplicates("DocumentIdentifier"), key="ev_evidence")

# --- TAB 5: KOPYA HABERLER ---
if open_view == V_COPY:
    with deep_tabs[V_COPY]:
        st.caption(
            "İki haber şu durumlarda aynı kopya grubuna girer: (1) V2Tone'un 7 değeri birebir aynıysa "
            f"(aynı metin, {MIN_WORDS_FOR_COPY}+ kelime), veya (2) başlıkları benzer, aralarında en fazla "
            f"{copy_window_hours} saat var ve kelime sayıları en fazla %{COPY_WORDCOUNT_TOLERANCE * 100:.0f} farklıysa. "
            "Grubun en erken yayımlanan haberi 'orijinal' sayılır. Ayarlar kenar çubuğundaki "
            "'Kopya tespiti ayarları' bölümündedir. Bu analiz tekilleştirme seçiminden bağımsızdır."
        )
        copy_rows = scope_df[scope_df.duplicated("CopyGroup", keep=False)]

        k1, k2, k3 = st.columns(3)
        k1.metric("Kopya Gruplarındaki Haber", fmt_int(len(copy_rows)))
        k2.metric("Kopya Grubu Sayısı", fmt_int(copy_rows["CopyGroup"].nunique()))
        k3.metric("Kopya Oranı", f"{(copy_rows.duplicated('CopyGroup').sum() / max(len(scope_df), 1)):.0%}",
                  help="Orijinal dışındaki kopyaların tüm haberlere oranı.")

        if copy_rows.empty:
            st.info("Seçilen filtrelerde kopya haber grubu bulunamadı.")
        else:
            groups = (
                copy_rows.sort_values("pub_timestamp")
                .groupby("CopyGroup")
                .agg(
                    KopyaSayisi=("DocumentIdentifier", "size"),
                    Yontem=("CopyMethod", "first"),
                    IlkYayin=("pub_timestamp", "first"),
                    SonYayin=("pub_timestamp", "last"),
                    IlkKaynak=("SourceCommonName", "first"),
                    Baslik=("PageTitle", "first"),
                    Kaynaklar=("SourceCommonName", lambda s: ", ".join(dict.fromkeys(s))),
                    OrnekURL=("DocumentIdentifier", "first"),
                    Ton=("AvgTone", "first"),
                )
                .sort_values("KopyaSayisi", ascending=False)
                .reset_index()
            )
            groups["Yayılma Süresi (saat)"] = (
                (groups["SonYayin"] - groups["IlkYayin"]).dt.total_seconds() / 3600
            ).round(1)

            col_first, col_copier = st.columns(2)
            with col_first:
                first_counts = groups["IlkKaynak"].value_counts().head(15).reset_index()
                first_counts.columns = ["Kaynak", "GrupSayisi"]
                fig_first = px.bar(
                    first_counts.sort_values("GrupSayisi"), x="GrupSayisi", y="Kaynak", orientation="h",
                    title="İçeriği ilk yayımlayan kaynaklar",
                )
                fig_first.update_layout(template=PLOTLY_TEMPLATE, yaxis_title="", xaxis_title="Kopya Grubu Sayısı")
                st.plotly_chart(fig_first, width="stretch")
            with col_copier:
                copier_counts = (
                    copy_rows[copy_rows.duplicated("CopyGroup", keep="first")]["SourceCommonName"]
                    .value_counts().head(15).reset_index()
                )
                copier_counts.columns = ["Kaynak", "KopyaSayisi"]
                fig_copier = px.bar(
                    copier_counts.sort_values("KopyaSayisi"), x="KopyaSayisi", y="Kaynak", orientation="h",
                    title="Hazır içeriği en çok yeniden yayımlayan kaynaklar",
                )
                fig_copier.update_layout(template=PLOTLY_TEMPLATE, yaxis_title="", xaxis_title="Kopya Haber Sayısı")
                st.plotly_chart(fig_copier, width="stretch")

            st.subheader("Kopya Grupları")
            groups_display = groups.drop(columns=["CopyGroup"])
            groups_display["IlkYayin"] = groups_display["IlkYayin"].dt.strftime("%Y-%m-%d %H:%M")
            groups_display["SonYayin"] = groups_display["SonYayin"].dt.strftime("%Y-%m-%d %H:%M")
            st.dataframe(
                groups_display,
                width="stretch",
                height=400,
                hide_index=True,
                column_config={
                    "OrnekURL": st.column_config.LinkColumn("İlk Haber", display_text="Haberi aç"),
                    "Ton": st.column_config.NumberColumn("Ton", format="%.2f"),
                    "Yontem": st.column_config.TextColumn("Yöntem"),
                },
            )

            st.subheader("Grup İçeriği")
            st.caption("Bir grubun hangi haberlerden oluştuğunu görmek ve yanlış birleşmeleri kontrol etmek için seçin.")
            group_labels = {
                row.CopyGroup: f"{row.KopyaSayisi} haber · {row.IlkYayin:%Y-%m-%d} · {row.Baslik[:80] or row.IlkKaynak}"
                for row in groups.itertuples()
            }
            selected_group = st.selectbox(
                "Kopya grubu", options=list(group_labels), format_func=group_labels.get, key="copy_group_select",
            )
            members = copy_rows[copy_rows["CopyGroup"] == selected_group].sort_values("pub_timestamp")
            st.dataframe(
                members.assign(Tarih=members["pub_timestamp"].dt.strftime("%Y-%m-%d %H:%M"))[
                    ["Tarih", "SourceCommonName", "PageTitle", "WordCount", "AvgTone", "IsCopy", "DocumentIdentifier"]
                ].rename(columns={
                    "SourceCommonName": "Kaynak", "PageTitle": "Başlık", "WordCount": "Kelime",
                    "AvgTone": "Ton", "IsCopy": "Kopya mı", "DocumentIdentifier": "Habere Git",
                }),
                width="stretch",
                hide_index=True,
                column_config={
                    "Habere Git": st.column_config.LinkColumn("Habere Git", display_text="Haberi aç"),
                    "Ton": st.column_config.NumberColumn("Ton", format="%.2f"),
                },
            )

# --- TAB 6: DETAY TABLOSU ---
if open_view == V_TABLE:
    with deep_tabs[V_TABLE]:
        st.subheader("Filtrelenmiş Haberler")
        st.caption(f"{fmt_int(len(filtered_df))} haber gösteriliyor. Saatler Türkiye saatidir.")

        persons_joined = persons_exploded_full.groupby(level=0)["V2Persons"].agg("; ".join)

        display_df = filtered_df[[
            "pub_timestamp", "SourceCommonName", "PageTitle", "DocumentIdentifier", "AvgTone", "Sentiment", "Themes",
            "Organizations", "WordCount", "IsCopy", "Frames", "Language",
        ]].copy()
        display_df["Frames"] = display_df["Frames"].str.replace(";", "; ", regex=False)
        display_df["Language"] = display_df["Language"].map(lambda c: LANGUAGE_NAMES.get(c, c))
        display_df["Ham URL"] = display_df["DocumentIdentifier"]
        display_df["Kişiler"] = persons_joined.reindex(display_df.index).fillna("")
        display_df["Kurumlar"] = display_df["Organizations"].str.replace(";", "; ", regex=False)

        display_df = display_df.sort_values("pub_timestamp", ascending=False)
        display_df["pub_timestamp"] = display_df["pub_timestamp"].dt.strftime("%Y-%m-%d %H:%M")

        display_df = display_df[[
            "pub_timestamp",
            "SourceCommonName",
            "PageTitle",
            "Ham URL",
            "DocumentIdentifier",
            "Sentiment",
            "AvgTone",
            "Themes",
            "Kişiler",
            "Kurumlar",
            "Frames",
            "WordCount",
            "IsCopy",
            "Language",
        ]]

        display_df = display_df.rename(columns={
            "pub_timestamp": "Tarih",
            "SourceCommonName": "Kaynak",
            "DocumentIdentifier": "Habere Git",
            "Sentiment": "Duygu",
            "AvgTone": "Ort. Ton",
            "Themes": "Temalar",
            "WordCount": "Kelime",
            "IsCopy": "Kopya mı",
            "PageTitle": "Başlık",
            "Frames": "Çerçeve",
            "Language": "Dil",
        })

        st.dataframe(
            display_df,
            width="stretch",
            height=500,
            column_config={
                "Ham URL": st.column_config.LinkColumn("Ham URL"),
                "Habere Git": st.column_config.LinkColumn("Habere Git", display_text="Haberi aç"),
                "Ort. Ton": st.column_config.NumberColumn("Ort. Ton", format="%.2f"),
                "Kelime": st.column_config.NumberColumn("Kelime", format="%d"),
            },
            hide_index=True,
        )

# --- TAB 7: TEMA REHBERİ ---
if open_view == V_GUIDE:
    with deep_tabs[V_GUIDE]:
        st.subheader("GDELT tema kodları sözlüğü ve rehberi")
        st.caption(
            "Filtrelenmiş haberlerde geçen tüm tema kodları. Aile, kodun önekine göre belirlenir "
            "(WB_ = Dünya Bankası, CRISISLEX_ = kriz sözlüğü, TAX_FNCACT = kişi rolü vb.)."
        )

        theme_guide_df = (
            theme_exploded_filtered.groupby(["Themes", "Family"])
            .agg(HaberSayisi=("AvgTone", "size"), OrtalamaTon=("AvgTone", "mean"))
            .reset_index()
            .sort_values("HaberSayisi", ascending=False)
        )
        theme_guide_df["Haber Oranı"] = theme_guide_df["HaberSayisi"] / len(filtered_df) * 100
        theme_guide_df.columns = ["Tema Kodu", "Tema Ailesi", "Haber Sayısı", "Ort. Ton", "Haber Oranı"]

        theme_search = st.text_input("Tema kodlarında veya ailelerde ara", icon=":material/search:", value="", help="Örn: corruption, kriz, wb vb.")

        if theme_search.strip():
            mask_theme = (
                theme_guide_df["Tema Kodu"].str.contains(theme_search, case=False, na=False, regex=False)
                | theme_guide_df["Tema Ailesi"].str.contains(theme_search, case=False, na=False, regex=False)
            )
            theme_guide_df = theme_guide_df[mask_theme]

        st.dataframe(
            theme_guide_df,
            width="stretch",
            height=500,
            hide_index=True,
            column_config={
                "Haber Sayısı": st.column_config.NumberColumn("Haber Sayısı", format="%d"),
                "Ort. Ton": st.column_config.NumberColumn("Ort. Ton", format="%.2f"),
                "Haber Oranı": st.column_config.ProgressColumn("Haber Oranı", format="%.0f%%", min_value=0, max_value=100),
            },
        )

# --------------------------------------------------------------------------------
# EXPORT
# --------------------------------------------------------------------------------
st.markdown("---")
st.subheader("Sonuçları dışa aktar")

export_col1, export_col2 = st.columns(2)

with export_col1:
    st.download_button(
        "Süzülmüş haberleri CSV olarak indir", icon=":material/download:", type="primary",
        data=lambda fdf=filtered_df: articles_csv_bytes(fdf),
        help="Haber satırlarını indirir. Bu dosya panele yeniden yüklenip analiz edilebilir.",
        file_name=f"gdelt_filtrelenmis_{datetime.now().strftime('%Y%m%d_%H%M')}.csv",
        mime="text/csv",
    )

with export_col2:
    st.download_button(
        "Tema özetini CSV olarak indir", icon=":material/table_view:",
        data=lambda tv=theme_view, n=top_n: to_csv_bytes(get_top_themes(tv, n=n)),
        help="Yalnızca tema sayıları (rapor içindir). Haber satırı içermediği için panele yeniden yüklenemez.",
        file_name=f"gdelt_tema_ozeti_{datetime.now().strftime('%Y%m%d_%H%M')}.csv",
        mime="text/csv",
    )

render_footer()
