"""MCP araçlarının gövdesi (M1 fizibilitesi): yapay zekâ ajanlarının arşivi sorgulamasını sağlayan,
salt okunur, protokolden bağımsız fonksiyonlar.

Neden protokolden bağımsız: MCP protokol katmanı ince ve değişkendir; asıl iş veriyi doğru ve
dürüst döndürmektir. Buradaki fonksiyonlar `mcp` paketi kurulu olmadan da çalışır ve test edilir.
Protokol sarmalayıcısı ayrıdır (`ajan/sunucu.py`).

**Her yanıt üç şey taşır** ve bu zorunludur:
- `as_of`: verinin hangi güne kadar olduğu
- `yontem`: rakamın nasıl üretildiği, tek cümle
- `sinir`: o rakamı okurken bilinmesi gereken sınır

Çünkü bir ajan bu rakamları bağlamından kopararak kullanır; uyarı veriyle aynı pakette gitmezse
hiç gitmemiş olur.

Salt okunurdur: hiçbir araç yazma yapmaz, hiçbir araç ham haber metni döndürmez (zaten yoktur).
"""
import contextlib
import functools
import io
import os
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import re

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "pipeline"))

PROJE = os.environ.get("GDELT_BQ_PROJECT", "banded-scion-476220-r0")
ARSIV = Path(os.environ.get("GDELT_ARCHIVE", ROOT / "data" / "archive"))

# Ajan çağrıları sık ve ardışıktır; BigQuery okumaları süreç içinde önbelleğe alınır.
# Hazır kopyayı günde iki kez yenilemek yerine daha basiti: süreli bellek önbelleği.
ONBELLEK_SURE = 6 * 3600
# konu_ara varsayılan aralığı: ajan tarih vermezse son bu kadar gün aranır
VARSAYILAN_GUN = 90
# Yanıta konan örnek başlık sayısı. Rakam tek başına "ne yazıldı" sorusunu cevaplamaz;
# ajanın metne bakabilmesi için birkaç başlık gerekir.
ORNEK_BASLIK = 12

KAPSAM_UYARISI = ("Kapsam GDELT'in taradığı sitelerle sınırlıdır; tam sayım değil, geniş bir örneklemdir. "
                  "Ölçülen şey olayın kendisi değil, habere konu olma yoğunluğudur.")


# --------------------------------------------------------------------------------
# OKUMA
# --------------------------------------------------------------------------------
def _sureli(sure: int = ONBELLEK_SURE):
    """Basit süreli önbellek: aynı argümanla yapılan okuma `sure` saniye boyunca tekrarlanmaz."""
    def sarmala(fn):
        kutu = {}

        @functools.wraps(fn)
        def ic(*a, **kw):
            anahtar = (a, tuple(sorted(kw.items())))
            kayit = kutu.get(anahtar)
            if kayit and time.time() - kayit[0] < sure:
                return kayit[1]
            sonuc = fn(*a, **kw)
            kutu[anahtar] = (time.time(), sonuc)
            return sonuc
        ic.temizle = kutu.clear
        return ic
    return sarmala


@_sureli()
def _bq_oku(tablo: str, alanlar: list, kisit: str = "") -> pd.DataFrame:
    """Storage Read API (sorgu kotasına sayılmaz, ayda 300 TiB ücretsiz)."""
    import pyarrow as pa
    from google.cloud.bigquery_storage import BigQueryReadClient, types

    alanlar = list(alanlar)
    yol = f"projects/{PROJE}/datasets/gdelt_tr/tables/{tablo}"
    r = BigQueryReadClient()
    oturum = r.create_read_session(
        parent=f"projects/{PROJE}",
        read_session=types.ReadSession(
            table=yol, data_format=types.DataFormat.ARROW,
            read_options=types.ReadSession.TableReadOptions(selected_fields=alanlar, row_restriction=kisit),
        ),
        max_stream_count=4,
    )
    parts = [p.to_arrow() for s in oturum.streams for p in r.read_rows(s.name).rows(oturum).pages]
    return pa.Table.from_batches(parts).to_pandas() if parts else pd.DataFrame(columns=alanlar)


def _ozet(gun_sayisi: int = 400) -> pd.DataFrame:
    """Özet katmanının son N günü, yalnızca tüm yayınlar satırları."""
    bas = date.today() - timedelta(days=gun_sayisi)
    kolon = ["gun", "boyut_tur", "boyut", "yayin", "haber", "pay", "ort_ton"]
    yerel = ARSIV / "ozet_gunluk.parquet"
    if yerel.exists():
        df = pd.read_parquet(yerel, columns=kolon, filters=[("gun", ">=", bas)])
        return df[df["boyut_tur"].isin(["toplam", "cerceve"])]
    return _bq_oku("ozet_gunluk", tuple(kolon),
                   f"gun >= DATE '{bas:%Y-%m-%d}' AND (boyut_tur = 'toplam' OR boyut_tur = 'cerceve')")


@contextlib.contextmanager
def _stdout_koru():
    """MCP stdio'da protokol stdout'u kullanır; buraya yazılan her şey akışı bozar.
    app.py'nin tanımları yüklenirken veya arama sırasında bir print() olursa stderr'e yönlendirilir."""
    tampon = io.StringIO()
    with contextlib.redirect_stdout(tampon):
        yield
    if tampon.getvalue().strip():
        sys.stderr.write("[ajan] stdout'a yazıldı, stderr'e alındı: " + tampon.getvalue()[:500] + "\n")


@functools.lru_cache(maxsize=1)
def _app():
    """app.py'nin tanımlarını paneli çalıştırmadan yükler (cases/common.py ile aynı yol).
    Yalnızca konu_ara çağrılınca yüklenir; diğer araçlar app.py'ye bağlı değildir."""
    import logging
    sys.path.insert(0, str(ROOT))
    from cases.common import app_ns
    logging.getLogger("streamlit.runtime.caching.cache_data_api").setLevel(logging.ERROR)
    with _stdout_koru():
        return app_ns()


def _aralik(baslangic: str | None, bitis: str | None) -> tuple:
    b = date.fromisoformat(bitis) if bitis else date.today()
    a = date.fromisoformat(baslangic) if baslangic else b - timedelta(days=VARSAYILAN_GUN)
    return a, b


def _damga(df: pd.DataFrame, gun_kolonu: str = "gun") -> str:
    return str(pd.to_datetime(df[gun_kolonu]).max().date()) if len(df) else "—"


# --------------------------------------------------------------------------------
# ARAÇLAR
# --------------------------------------------------------------------------------
def gundem_ozeti(hafta: str | None = None) -> dict:
    """Bir haftanın medya gündemi: hacim, ton, çerçeve payları ve kendi geçmişine göre sapmalar.
    `hafta`: ISO etiketi (2026-W42); boşsa arşivdeki son tam hafta."""
    import sapma

    ham = _ozet()
    if ham.empty:
        return {"hata": "Özet katmanı okunamadı."}
    df = sapma.hafta_ekle(ham)
    tam = sapma.tam_haftalar(df)
    if not len(tam):
        return {"hata": "Tam hafta yok."}
    hf = sapma.haftalik_cerceveler(df)
    hf = hf[hf["hafta"].isin(set(tam))]
    if hafta:
        import re
        m = re.fullmatch(r"(\d{4})-?W(\d{1,2})", hafta.strip().upper())
        if not m:
            return {"hata": f"Hafta etiketi anlaşılmadı: {hafta} (beklenen: 2026-W42)"}
        h = pd.Period(date.fromisocalendar(int(m.group(1)), int(m.group(2)), 1), freq="W-SUN")
        if h not in set(tam):
            return {"hata": f"{hafta} arşivde tam bir hafta değil."}
    else:
        h = tam[-1]
    tablo = sapma.hafta_tablosu(hf, h)
    sapan = sapma.sapmalar(tablo)
    ornek = _hafta_basliklari(h.start_time.date(), h.end_time.date(), [x["cerceve"] for x in sapan])
    y, w, _ = h.start_time.date().isocalendar()
    toplam = next((x for x in tablo if x["cerceve"] == "Diğer"), None)
    return {
        "hafta": f"{y}-W{w:02d}",
        "baslangic": str(h.start_time.date()), "bitis": str(h.end_time.date()),
        "cerceveler": [{"cerceve": x["cerceve"], "haber": x["haber"], "pay": round(x["pay"], 5),
                        "fark_puan": None if x["fark_puan"] is None else round(x["fark_puan"], 2),
                        "yuzdelik": None if x["yuzdelik"] is None else round(x["yuzdelik"], 1),
                        "ort_ton": round(x["ort_ton"], 2)} for x in tablo],
        "sapmalar": [{"cerceve": x["cerceve"], "pay": round(x["pay"], 5),
                      "yuzdelik": round(x["yuzdelik"], 1),
                      "yon": "olağandışı yüksek" if x["yuzdelik"] >= sapma.PCT_YUKSEK else "olağandışı düşük",
                      "ornek_basliklar": ornek.get(x["cerceve"], [])}
                     for x in sapan],
        "as_of": _damga(df),
        "yontem": (f"Çerçeveler haber başlığındaki anahtar kelimelerle atanır. Sapma, çerçevenin payının "
                   f"önceki {sapma.TABAN_HAFTA} haftaya göre yüzdelik dilimidir; haftalık haber sayısı "
                   f"{sapma.MIN_HABER}'in altındaki çerçeveler sapma sayılmaz."),
        "sinir": (KAPSAM_UYARISI + " Bir haber birden fazla çerçeveye girebilir, paylar toplamı %100'ü aşar; "
                  "haberlerin yarıdan fazlası hiçbir çerçeveye uymaz ve 'Diğer'e düşer"
                  + (f" (bu hafta %{toplam['pay']*100:.0f})." if toplam else ".")),
    }


def _hafta_basliklari(bas: date, bit: date, cerceveler: list) -> dict:
    """Bir haftada verilen çerçevelere düşen örnek başlıklar. Yerel arşiv yoksa boş döner:
    rakam tek başına 'ne yazıldı' sorusunu cevaplamaz, ama uydurma da yapılmaz."""
    if not cerceveler or not (ARSIV / "articles").is_dir():
        return {}
    try:
        ns = _app()
        gunler = [(ARSIV / "articles" / f"{bas + timedelta(days=i):%Y-%m-%d}.parquet") for i in range(7)]
        var = [str(g) for g in gunler if g.exists()]
        if not var:
            return {}
        with _stdout_koru():
            df = pd.concat([pd.read_parquet(g, columns=["PageTitle", "DocumentIdentifier", "V2Tone",
                                                        "pub_timestamp", "SourceCommonName", "Themes"])
                            for g in var], ignore_index=True)
            df = ns["preprocess"](df)
            df["Frames"] = ns["assign_frames"](df[["TitleCore", "DocumentIdentifier"]],
                                               ns["frame_set_key"](ns["FRAME_SETS"]["Genel"]))
        out = {}
        for c in cerceveler:
            alt = df[df["Frames"].str.contains(re.escape(c), regex=True, na=False)]
            alt = alt[alt["PageTitle"] != ""]
            out[c] = [str(t)[:130] for t in alt["PageTitle"].head(5)]
        return out
    except Exception:
        return {}


def cerceve_serisi(cerceve: str, baslangic: str | None = None, bitis: str | None = None) -> dict:
    """Bir çerçevenin günlük payı. Çerçeve adları `gundem_ozeti` çıktısındadır.
    Tarih verilmezse son 90 gün."""
    ham = _ozet(gun_sayisi=4000)
    if ham.empty:
        return {"hata": "Özet katmanı okunamadı."}
    ham["gun"] = pd.to_datetime(ham["gun"])
    _a, _b = _aralik(baslangic, bitis)
    baslangic, bitis = str(_a), str(_b)
    a, b = pd.Timestamp(_a), pd.Timestamp(_b)
    alt = ham[(ham["gun"] >= a) & (ham["gun"] <= b) & (ham["yayin"] == "")]
    cer = alt[(alt["boyut_tur"] == "cerceve") & (alt["boyut"] == cerceve)]
    if cer.empty:
        mevcut = sorted(alt[alt["boyut_tur"] == "cerceve"]["boyut"].unique())
        return {"hata": f"Çerçeve bulunamadı: {cerceve}", "mevcut_cerceveler": mevcut}
    return {
        "cerceve": cerceve, "baslangic": baslangic, "bitis": bitis,
        "seri": [{"gun": str(g.date()), "haber": int(h), "pay": round(float(p), 5)}
                 for g, h, p in cer.sort_values("gun")[["gun", "haber", "pay"]].itertuples(index=False)],
        "ortalama_pay": round(float(cer["pay"].mean()), 5),
        "as_of": _damga(ham),
        "yontem": "Pay = o çerçevedeki haber sayısı / o günün toplam haber sayısı.",
        "sinir": KAPSAM_UYARISI,
    }


def yayin_profili(yayin: str, baslangic: str | None = None, bitis: str | None = None) -> dict:
    """Bir yayının çerçeve dağılımı ve kilit yayınların geneline göre kaç kat öne çıkardığı.
    Tarih verilmezse son 90 gün."""
    kolon = ["gun", "boyut_tur", "boyut", "yayin", "yayin_grubu", "haber"]
    yerel = ARSIV / "ozet_gunluk.parquet"
    _a, _b = _aralik(baslangic, bitis)
    baslangic, bitis = str(_a), str(_b)
    a, b = pd.Timestamp(_a), pd.Timestamp(_b)
    if yerel.exists():
        df = pd.read_parquet(yerel, columns=kolon,
                             filters=[("gun", ">=", a.date()), ("gun", "<=", b.date())])
    else:
        df = _bq_oku("ozet_gunluk", tuple(kolon),
                     f"gun BETWEEN DATE '{a:%Y-%m-%d}' AND DATE '{b:%Y-%m-%d}'")
    if df.empty:
        return {"hata": "Veri yok."}
    yayinlar = sorted(x for x in df["yayin"].unique() if x)
    if yayin not in yayinlar:
        return {"hata": f"Yayın bulunamadı: {yayin}", "mevcut_yayinlar": yayinlar}
    kilit = df[df["yayin"] != ""]
    genel_top = kilit[kilit["boyut_tur"] == "toplam"]["haber"].sum()
    genel = kilit[kilit["boyut_tur"] == "cerceve"].groupby("boyut")["haber"].sum() / max(genel_top, 1)
    v = df[df["yayin"] == yayin]
    v_top = int(v[v["boyut_tur"] == "toplam"]["haber"].sum())
    v_cer = v[v["boyut_tur"] == "cerceve"].groupby("boyut")["haber"].sum()
    satir = []
    for ad, n in v_cer.sort_values(ascending=False).items():
        gp = float(genel.get(ad, 0))
        satir.append({"cerceve": ad, "haber": int(n), "pay": round(n / max(v_top, 1), 5),
                      "genel_pay": round(gp, 5),
                      "kat": round((n / max(v_top, 1)) / gp, 2) if gp else None})
    return {
        "yayin": yayin, "yayin_grubu": str(v["yayin_grubu"].iloc[0]),
        "baslangic": baslangic, "bitis": bitis, "haber": v_top, "cerceveler": satir,
        "as_of": _damga(df),
        "yontem": "Kat = yayının o çerçevedeki payı / kilit yayınların tamamındaki pay. 1,0 ortalamadır.",
        "sinir": (KAPSAM_UYARISI + " Yayınların haber sayıları birbiriyle karşılaştırılamaz: rakam o yayının "
                  "ne kadar yazdığını değil, arşivin onu ne kadar gördüğünü yansıtır."),
    }


def dunya_turk(min_kaynak: int = 5, limit: int = 20) -> dict:
    """Türkiye'de geçen olayları dünya medyası ne kadar yazdı, arşivde Türkçe haberi var mı?"""
    kolon = ["event_day", "EventRootCode", "Actor1Name", "Actor2Name", "ActionGeo_FullName",
             "toplam_anilma", "toplam_kaynak", "turkce_anilma"]
    df = _bq_oku("events_tu", tuple(kolon), f"toplam_kaynak >= {int(min_kaynak)}")
    if df.empty:
        return {"hata": "events_tu okunamadı."}
    yok = df[df["turkce_anilma"] == 0]
    bos = {"", "none", "nan", "null"}
    ust = yok.sort_values("toplam_kaynak", ascending=False).head(int(limit))
    return {
        "olay": len(df), "turkce_haberi_olmayan": len(yok),
        "turkce_haberi_olmayan_pay": round(len(yok) / len(df) * 100, 1),
        "min_kaynak": int(min_kaynak),
        "olaylar": [{"gun": str(pd.Timestamp(r["event_day"]).date()), "cameo": str(r["EventRootCode"]),
                     "aktorler": [str(x) for x in (r["Actor1Name"], r["Actor2Name"])
                                  if str(x).strip().lower() not in bos],
                     "yer": str(r["ActionGeo_FullName"]), "kaynak": int(r["toplam_kaynak"])}
                    for _, r in ust.iterrows()],
        "as_of": _damga(df, "event_day"),
        "yontem": ("Olay, GDELT'in yer kodlamasına göre Türkiye'de geçenlerden seçilir. 'Türkçe haberi var' "
                   "demek, olayın anılmalarından birinin URL'sinin arşivimizde bulunması demektir."),
        "sinir": ("'Türkçe haberi yok', Türk medyasının hiç yazmadığı anlamına GELMEZ — arşiv bir örneklemdir. "
                  "Ayrıca olayın Türkiye'de geçmiş sayılması GDELT'in yer kodlamasına dayanır ve hata yapar. "
                  f"Eşiksiz bakıldığında olayların dörtte üçü tek anılmalıdır; bu yüzden en az {min_kaynak} "
                  "kaynak şartı vardır."),
    }


def kapsam(baslangic: str, bitis: str) -> dict:
    """Bir dönemde arşivde hangi yayınlar var, hacimleri ne?"""
    a, b = pd.Timestamp(baslangic), pd.Timestamp(bitis)
    yerel = ARSIV / "source_daily.parquet"
    if yerel.exists():
        df = pd.read_parquet(yerel, filters=[("gkg_day", ">=", a), ("gkg_day", "<=", b)])
    else:
        df = _bq_oku("source_daily", ("gkg_day", "site", "n"),
                     f"gkg_day BETWEEN DATE '{a:%Y-%m-%d}' AND DATE '{b:%Y-%m-%d}'")
    if df.empty:
        return {"hata": "Bu aralıkta arşiv kaydı yok."}
    g = df.groupby("site")["n"].sum().sort_values(ascending=False)
    gun = df["gkg_day"].nunique()
    return {
        "baslangic": baslangic, "bitis": bitis, "gun": int(gun),
        "site": int(df["site"].nunique()), "haber": int(df["n"].sum()),
        "en_cok_yazanlar": [{"site": s, "haber": int(n)} for s, n in g.head(25).items()],
        "as_of": str(pd.to_datetime(df["gkg_day"]).max().date()),
        "yontem": "Gün × site haber sayılarının toplamı.",
        "sinir": KAPSAM_UYARISI + " Bir yayının listede olmaması, o dönemde yayın yapmadığı anlamına gelmez.",
    }


def konu_ara(ifade: str, baslangic: str | None = None, bitis: str | None = None) -> dict:
    """Bir kişi, kurum veya konunun Türkçe haberlerde nasıl ele alındığı.
    Arama ifadenin haberin ADRESİNDE, BAŞLIĞINDA veya kişi listesinde geçtiği haberleri bulur;
    yalnızca haber gövdesinde geçen bir ad bulunamaz (arşivde tam metin yoktur)."""
    ns = _app()
    a, b = _aralik(baslangic, bitis)
    _ = _stdout_koru
    if a > b:
        return {"hata": "Başlangıç bitişten sonra olamaz."}
    try:
        with _stdout_koru():
            ham = ns["local_fetch"](ifade, a, b) if (ARSIV / "articles").is_dir() \
                else ns["bq_fetch"](ifade, a, b)[0]
    except Exception as e:
        return {"hata": f"Arama yapılamadı: {type(e).__name__}: {e}"}
    if ham.empty:
        return {"ifade": ifade, "baslangic": str(a), "bitis": str(b), "haber": 0,
                "not": "Bu aralıkta eşleşen haber yok. Daha uzun bir aralık veya başka bir ifade deneyin.",
                "as_of": str(b), "yontem": "Arama adres, başlık ve kişi alanında yapılır.",
                "sinir": KAPSAM_UYARISI}
    df = ns["preprocess"](ham)
    df["Frames"] = ns["assign_frames"](df[["TitleCore", "DocumentIdentifier"]],
                                       ns["frame_set_key"](ns["FRAME_SETS"]["Genel"]))
    kapsam_r = ns["coverage_report"](df, a, b)

    aylik = (df.groupby(df["pub_timestamp"].dt.to_period("M").astype(str)).size()
               .rename("haber").reset_index().rename(columns={"index": "ay"}))
    cer = (df["Frames"].str.split(";").explode().value_counts() / len(df)).head(8)
    basliklar = [t for t in df.sort_values("pub_timestamp", ascending=False)["PageTitle"] if t][:ORNEK_BASLIK]
    return {
        "ifade": ifade, "baslangic": str(a), "bitis": str(b),
        "haber": len(df), "site": int(df["SourceCommonName"].nunique()),
        "ortalama_ton": round(float(df["AvgTone"].mean()), 2),
        "kapsam_duzeyi": kapsam_r["level"],
        "kapsam_uyarilari": [m for _, m in kapsam_r.get("warnings", [])],
        "aylik": [{"ay": r[0], "haber": int(r[1])} for r in aylik.itertuples(index=False)],
        "en_cok_yazanlar": [{"site": s_, "haber": int(n)}
                            for s_, n in df["SourceCommonName"].value_counts().head(10).items()],
        "cerceveler": [{"cerceve": k, "pay": round(float(v), 4)} for k, v in cer.items()],
        "ornek_basliklar": [str(t)[:140] for t in basliklar],
        "as_of": str(df["pub_timestamp"].max().date()),
        "yontem": ("Arama, ifadenin haberin adresinde, başlığında veya kişi listesinde geçtiği haberleri "
                   "bulur. Çerçeveler başlıktaki anahtar kelimelerle atanır; bir haber birden fazla "
                   "çerçeveye girebilir."),
        "sinir": (KAPSAM_UYARISI + " Yalnızca haber gövdesinde geçen bir ad BULUNAMAZ: arşivde tam metin "
                  "yoktur. Kapsam düzeyi 'Sınırlı' veya 'Yetersiz' ise sonucu buna göre yorumla."),
    }


ARACLAR = {
    "konu_ara": konu_ara,
    "gundem_ozeti": gundem_ozeti,
    "cerceve_serisi": cerceve_serisi,
    "yayin_profili": yayin_profili,
    "dunya_turk": dunya_turk,
    "kapsam": kapsam,
}
