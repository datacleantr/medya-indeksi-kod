"""Sapma hesabı (N4): bir çerçevenin payı, kendi yakın geçmişine göre olağandışı mı?

Bu modül **yaprak**tır: yalnızca pandas'a bağlıdır, app.py'yi veya cases/'i import etmez.
Hem panel (`app.py`) hem haftalık bülten (`pipeline/bulten.py`) buradan okur; hesap tek yerdedir.

Girdi her zaman `ozet_gunluk` tablosudur (`pipeline/ozet.py`): gün × boyut × yayın.

İki tasarım kararı, ikisi de ölçümle doğrulandı:

1. **Taban kayan penceredir (`TABAN_HAFTA` = 52), tüm geçmiş değil.** Tüm geçmişe göre yüzdelik, yıllar içinde
   yapısal olarak yükselen bir çerçeveyi her hafta "sapma" sayar. Ölçüm (23.10.2026): "Yargı / soruşturma"
   tüm geçmişe göre son 12 haftanın 8'inde 90+ yüzdelikteydi; 52 haftalık kayan tabanla 4'ünde.

2. **Yüzde değişim değil yüzdelik dilim, üstelik mutlak tabanla birlikte.** Küçük tabanlı seriler küçük
   değişimlerde dev yüzdeler üretir; küçük tabanlı oran gürültüdür. `MIN_HABER` eşiğini geçmeyen
   çerçeve sapma olarak raporlanmaz.
"""
import pandas as pd

# Karşılaştırma penceresi: bu haftadan önceki kaç hafta tabanı oluşturur
TABAN_HAFTA = 52
# Yüzdelik hesaplanabilmesi için tabanda en az bu kadar hafta olmalı
TABAN_MIN_HAFTA = 26
# Haftalık haber sayısı bunun altındaki çerçeve sapma listesine girmez
MIN_HABER = 150
# Bu yüzdeliklerin dışı "olağandışı" sayılır
PCT_YUKSEK, PCT_DUSUK = 90.0, 10.0
# Tanımlı bir kategori değil artık küme olduğu için sapma listesine girmez
HARIC = {"Diğer"}


def hafta_ekle(df: pd.DataFrame) -> pd.DataFrame:
    """`gun` kolonundan ISO haftası (Pazartesi–Pazar) türetir."""
    df = df.copy()
    df["gun"] = pd.to_datetime(df["gun"])
    df["hafta"] = df["gun"].dt.to_period("W-SUN")
    return df


def tam_haftalar(df: pd.DataFrame) -> pd.Index:
    """Arşivde 7 günü de bulunan haftalar. Eksik hafta yanıltıcı pay üretir."""
    n = df.groupby("hafta")["gun"].nunique()
    return n[n == 7].index.sort_values()


def haftalik_cerceveler(df: pd.DataFrame) -> pd.DataFrame:
    """Hafta × çerçeve (tüm yayınlar): haber, pay, ağırlıklı ortalama ton."""
    tum = df[df["yayin"] == ""]
    toplam = tum[tum["boyut_tur"] == "toplam"].groupby("hafta")["haber"].sum().rename("hafta_haber")
    cer = tum[tum["boyut_tur"] == "cerceve"]
    g = (cer.groupby(["hafta", "boyut"])
            .apply(lambda x: pd.Series({"haber": x["haber"].sum(),
                                        "ort_ton": (x["ort_ton"] * x["haber"]).sum() / max(x["haber"].sum(), 1)}),
                   include_groups=False)
            .reset_index()
            .join(toplam, on="hafta"))
    g["pay"] = g["haber"] / g["hafta_haber"]
    return g


def yuzdelik(seri: pd.Series, deger: float) -> float | None:
    """`deger`in `seri` içindeki yüzdelik dilimi. Taban kısaysa None."""
    if len(seri) < TABAN_MIN_HAFTA:
        return None
    return float((seri <= deger).mean() * 100)


def hafta_tablosu(hf: pd.DataFrame, hafta) -> list:
    """Bir haftanın bütün çerçeveleri: pay, geçen haftaya fark, kayan tabana göre yüzdelik, ton.
    `hf` haftalik_cerceveler çıktısı, yalnızca tam haftalarla süzülmüş olmalıdır."""
    bu = hf[hf["hafta"] == hafta]
    onceki = hf[hf["hafta"] == hafta - 1].set_index("boyut")
    satir = []
    for _, r in bu.iterrows():
        ad = r["boyut"]
        taban = (hf[(hf["boyut"] == ad) & (hf["hafta"] < hafta)]
                 .sort_values("hafta").tail(TABAN_HAFTA)["pay"])
        onc = float(onceki.loc[ad, "pay"]) if ad in onceki.index else None
        satir.append({
            "cerceve": ad,
            "haber": int(r["haber"]),
            "pay": float(r["pay"]),
            "onceki_pay": onc,
            "fark_puan": (float(r["pay"]) - onc) * 100 if onc is not None else None,
            "yuzdelik": yuzdelik(taban, float(r["pay"])),
            "ort_ton": float(r["ort_ton"]),
            "taban_hafta": int(len(taban)),
            "esik_gecti": int(r["haber"]) >= MIN_HABER,
        })
    satir.sort(key=lambda x: -x["pay"])
    return satir


def sapmalar(satirlar: list) -> list:
    """Tablodan olağandışı olanları seçer: eşiği geçmiş, "Diğer" olmayan, uç yüzdelikteki çerçeveler.
    Tabandan en uzak olan başa gelir."""
    s = [x for x in satirlar
         if x["esik_gecti"] and x["cerceve"] not in HARIC and x["yuzdelik"] is not None
         and (x["yuzdelik"] >= PCT_YUKSEK or x["yuzdelik"] <= PCT_DUSUK)]
    s.sort(key=lambda x: -abs(x["yuzdelik"] - 50))
    return s
