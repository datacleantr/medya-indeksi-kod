"""Konu kartının yapay zekâ anlatısı (N2): ekrandaki rakamları düzyazıya çevirir.

Mimari karar: **LLM burada ölçmez, anlatır.** Bütün sayılar LLM'den önce ve ondan bağımsız üretilir
(`card_facts`, `coverage_report`, `summarize_changes`); Claude yalnızca hazır bir olgu paketini (fact-pack)
cümleye döker. Bu yüzden:
- Altın set gerekmez: LLM yeni bir ölçüm üretmiyor, ürettiği her rakam kullanıcının ekranda gördüğü karttan.
- Halüsinasyon yüzeyi dar: pakette olmayan olgu cümlesi yasak, paket istemin içinde.
- Maliyet kayıt sayısıyla değil soru sayısıyla orantılı (çağrı başına ~0,05 cent, Haiku 5.5).

Vektör araması (RAG) **yoktur**: paket, panelin zaten hesapladığı tablolardan deterministik olarak kurulur.

Üç kural isteme yazılıdır ve çıktıda denetlenir:
1. Pakette olmayan hiçbir olgu veya rakam kullanılamaz.
2. **Nedensellik yasağı:** "X yüzünden arttı" denemez. Veri ilgiyi gösterir, nedeni göstermez.
3. Kapsam düzeyi "Sınırlı" veya "Yetersiz" ise ilk paragrafta söylenir.

Her paragraf `veri` (paketteki rakamların aktarımı) veya `yorum` (bağ kuran cümle) olarak etiketlenir;
L3'teki kanıt etiketi şemasıyla aynıdır.
"""
import json
import re

from llm import butce

MODEL = "claude-haiku-5-5"
# Haiku 5.5'te düşünme varsayılan AÇIK ve bu iş için gereksiz: rakamları düzyazıya çevirmek muhakeme
# istemiyor. Açıkken 1100 token'ın tamamını düşünme yedi ve JSON yarıda kesildi (ölçüm 23.10.2026).
# Kapalıyken aynı çıktı ~1.290 token; tavan ona göre seçildi.
DUSUNME = {"type": "disabled"}
MAX_TOKENS = 2000   # zirve açıklamaları eklendi
ETIKET = "anlatici"
# Pakete giren örnek başlık sayısı: modele konunun dilini gösterir, maliyeti ~400 token artırır
ORNEK_BASLIK = 25
# Zirve başına pakete giren ek başlık sayısı: modelin o dönemde ne yazıldığını görmesi için
ZIRVE_BASLIK = 6

SISTEM = """Sen bir medya arşivi analiz panelinin yazarısın. Sana bir arama sonucunun ÖLÇÜLMÜŞ olguları
veriliyor. Görevin bu olguları Türkçe, yalın ve profesyonel bir dille 4-6 kısa paragrafa çevirmek.

MUTLAK KURALLAR:
1. Yalnızca OLGU PAKETİ'ndeki bilgileri kullan. Pakette olmayan hiçbir olay, kişi, tarih veya rakam yazma.
   Dünya bilgini kullanma. Bir şey pakette yoksa, yokmuş gibi davran.
2. NEDEN SÖYLEME. Bu veri haberlerin NEYİ ne kadar yazdığını gösterir, NEDEN yazdığını göstermez.
   "X yüzünden arttı", "X nedeniyle", "X'in etkisiyle" gibi ifadeler YASAK.
   İzin verilen en güçlü ifade: "aynı dönemde", "bununla birlikte", "şu başlıklar öne çıkıyor".
3. Ölçülen şey olayın kendisi değil, HABERE KONU OLMA YOĞUNLUĞUDUR. "Şu kadar olay oldu" deme;
   "şu kadar haber çıktı" de.
4. Kapsam düzeyi "Sınırlı" veya "Yetersiz" ise bunu İLK paragrafta açıkça söyle.
5. Rakamları paketteki hâliyle ver. Yuvarlama yapma, kendi hesabını yapma.

Yanıtında iç veya sistem XML etiketi kullanma.

ÜSLUP: Kısa cümleler. Abartı ve sıfat yığını yok. "Çarpıcı", "dikkat çekici", "ilginç" gibi değerlendirme
sıfatları kullanma. Okuyucu rakamları zaten görüyor; sen onları bağla ve sırala.

ZİRVE AÇIKLAMALARI: Pakette ZİRVELER varsa, her zirve için **tek kısa cümle** yaz: o dönemde ne
yazıldığı. Yalnızca o zirvenin başlıklarına dayan; başka bilgi ekleme. Olayın ne olduğunu değil,
HABERLERİN NEYİ ANLATTIĞINI söyle. Tarihi tekrar yazma, cümleye doğrudan başla.
İyi: "Adana ve Osmaniye'deki sarsıntılar ve AFAD açıklamaları öne çıkıyor."
Kötü: "6 Şubat'ta büyük bir deprem meydana geldi ve binlerce kişi öldü." (pakette yok, dünya bilgisi)

ÇIKTI: Yalnızca şu JSON, başka hiçbir şey yazma. **Önce "zirveler", sonra "paragraflar"** yaz:
{"zirveler": [{"sira": 1, "aciklama": "..."}],
 "paragraflar": [{"tur": "veri" | "yorum", "metin": "..."}]}

"zirveler" ZORUNLUDUR: pakette kaç zirve varsa o kadar girdi olmalı, "sira" paketteki numaradır.
Pakette hiç zirve yoksa boş liste yaz.
"paragraflar": **en fazla 6** paragraf. "tur" alanı: paragraf yalnızca paketteki rakamları aktarıyorsa
"veri"; rakamlar arasında bağ kuruyor veya bir örüntüye işaret ediyorsa "yorum". En az 3'ü "veri" olmalı."""


def _sayi(x) -> str:
    return f"{int(x):,}".replace(",", ".")


def paket(konu: str, start, end, kaynak: str, fdf, facts: dict, coverage: dict,
          changes=None, ornekler=None) -> str:
    """Panelin hesapladığı tablolardan olgu paketini kurar. LLM yalnızca bunu görür."""
    sat = [f"KONU: {konu}",
           f"TARİH ARALIĞI: {start:%d.%m.%Y} – {end:%d.%m.%Y}",
           f"VERİ KAYNAĞI: {kaynak}",
           "",
           "KAPSAM DEĞERLENDİRMESİ:",
           f"  düzey: {coverage['level']}"]
    for onem, metin in coverage.get("warnings", []):
        sat.append(f"  uyarı ({onem}): {metin}")
    if not coverage.get("warnings"):
        sat.append("  uyarı: yok")

    tekil = int((~fdf["IsCopy"]).sum()) if "IsCopy" in fdf.columns else len(fdf)
    sat += ["", "HACİM:",
            f"  toplam haber: {_sayi(len(fdf))}",
            f"  tekil içerik (kopyalar çıkarılmış): {_sayi(tekil)}",
            f"  farklı haber sitesi: {_sayi(fdf['SourceCommonName'].nunique())}"]

    sat += ["", f"ZAMAN DAĞILIMI ({facts['unit']} bazında haber sayısı):"]
    seri = facts["counts"]
    adim = max(1, len(seri) // 24)   # uzun serilerde seyreltilir; istem şişmesin
    sat += ["  " + ", ".join(f"{t:%Y-%m-%d}: {int(v)}" for t, v in list(seri.items())[::adim])]
    if facts["peaks"]:
        sat.append("  ZİRVELER (en yoğun dönemler; her zirve için o dönemin öne çıkan başlıkları):")
        for i, ts, n, baslik, kova in facts["peaks"]:
            sat.append(f"    {i}. {ts:%d.%m.%Y} — {_sayi(n)} haber")
            sat.append(f"       en çok yayılan: \"{baslik[:120]}\"")
            # Zirve açıklaması için o dönemin başka başlıkları da gerekir; en çok kopyalanan birkaç tanesi
            try:
                ust = (kova.sort_values("CopyGroupSize", ascending=False)["PageTitle"]
                       if "CopyGroupSize" in kova.columns else kova["PageTitle"])
                ornek = [t for t in ust if t and t != baslik][:ZIRVE_BASLIK]
                for t in ornek:
                    sat.append(f"       - {str(t)[:110]}")
            except Exception:
                pass

    sat += ["", "DUYGU TONU (GDELT AvgTone; negatif = olumsuz dil):",
            f"  ortalama: {facts['avg_tone']:.2f}",
            f"  olumsuz haber payı: %{facts['neg'] * 100:.1f}",
            f"  olumlu haber payı: %{facts['pos'] * 100:.1f}"]

    sat += ["", "EN ÇOK YAZAN SİTELER:"]
    sat += [f"  {s}: {_sayi(n)} haber" for s, n in facts["top_sites"].items()]

    if len(facts["top_people"]):
        sat += ["", "BİRLİKTE EN ÇOK ANILAN KİŞİLER (GDELT makine çevirisinden çıkardığı adlar; bozuk olabilir):"]
        sat += [f"  {k}: {_sayi(n)}" for k, n in facts["top_people"].items()]
    if len(facts["top_orgs"]):
        sat += ["", "BİRLİKTE EN ÇOK ANILAN KURUMLAR:"]
        sat += [f"  {k}: {_sayi(n)}" for k, n in facts["top_orgs"].items()]

    if len(facts["stories"]):
        sat += ["", "EN ÇOK SİTEYE YAYILAN HABERLER:"]
        for _, r in facts["stories"].iterrows():
            sat.append(f"  \"{str(r['baslik'])[:110]}\" — ilk: {r['ilk']} — {int(r['site'])} sitede")

    if changes and changes[0]:
        sat += ["", f"YAYIN SONRASI DEĞİŞİKLİKLER: {changes[0]}"]

    if ornekler is not None and len(ornekler):
        sat += ["", f"ÖRNEK BAŞLIKLAR (rastgele {min(len(ornekler), ORNEK_BASLIK)} tanesi):"]
        sat += [f"  - {str(b)[:130]}" for b in list(ornekler)[:ORNEK_BASLIK]]

    return "\n".join(sat)


def anlat(paket_metni: str, model: str = MODEL) -> dict:
    """Paketi Claude'a verir, etiketli paragrafları döner.
    {"paragraflar": [{"tur","metin"}], "maliyet": float, "model": str}"""
    yanit = butce.cagir(
        model=model, etiket=ETIKET, max_tokens=MAX_TOKENS, system=SISTEM, thinking=DUSUNME,
        messages=[{"role": "user", "content": f"OLGU PAKETİ:\n\n{paket_metni}"}],
    )
    ham = "".join(b.text for b in yanit.content if b.type == "text").strip()
    paragraflar = _ayristir(ham)
    u = yanit.usage
    return {"paragraflar": paragraflar, "zirveler": _zirveler(ham), "model": model, "ham": ham,
            "kesildi": yanit.stop_reason == "max_tokens",
            "girdi_token": u.input_tokens, "cikti_token": u.output_tokens,
            "maliyet": butce.maliyet(model, girdi=u.input_tokens, cikti=u.output_tokens)}


def _zirveler(ham: str) -> dict:
    """{sıra: açıklama}. Model zirve üretmediyse veya JSON bozuksa boş sözlük."""
    veri = _json_blok(ham)
    out = {}
    for z in (veri or {}).get("zirveler", []):
        try:
            metin = str(z.get("aciklama", "")).strip()
            if metin:
                out[int(z["sira"])] = metin
        except (ValueError, TypeError, KeyError):
            continue
    return out


def _json_blok(ham: str):
    """Yanıttaki JSON nesnesini çözer; bulunamazsa None."""
    metin = ham
    blok = re.search(r"```(?:json)?\s*(.*?)```", ham, re.S)
    if blok:
        metin = blok.group(1)
    kose = re.search(r"\{.*\}", metin, re.S)
    if kose:
        metin = kose.group(0)
    try:
        return json.loads(metin)
    except json.JSONDecodeError:
        return None


def _ayristir(ham: str) -> list:
    """JSON'u çözer. Model JSON dışına çıkarsa ham metni tek 'yorum' paragrafı olarak döndürür
    (sessizce boş sonuç vermemek için)."""
    veri = _json_blok(ham)
    if veri:
        cikan = []
        for p in veri.get("paragraflar", []):
            t = str(p.get("metin", "")).strip()
            if t:
                cikan.append({"tur": "veri" if p.get("tur") == "veri" else "yorum", "metin": t})
        if cikan:
            return cikan
    return [{"tur": "yorum", "metin": ham}] if ham else []
