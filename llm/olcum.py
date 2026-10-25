"""Altın sete karşı model ölçümü: yerel (Ollama) ve bulut (Claude) aynı istemle.

Kod kitabı tek kaynaktır (`llm/kodkitabi/<vaka>.toml`): insan kodlayıcı (`llm/kodlama.py`), LLM istemi
ve bu ölçüm aynı tanımları kullanır. Betik altın seti **yalnızca okur**; etiket dosyasına asla yazmaz
(altın set insan eliyle kodlanır, yoksa doğruluk ölçülmemiş olur).

Amaç yalnızca "hangi model daha iyi" değil, **kod kitabının neresi bozuk** sorusudur: karışıklık matrisi
ve seçenek bazlı fazla/az seçim tablosu bunun içindir. 24.10 ölçümünde iki bağımsız model de `cerceve`
alanında `barinma`'ya saplandı — bu modelin değil tanımın sorunuydu.

Kullanım:
    venv/bin/python -m llm.olcum deprem qwen3.8                    # yerel, K=25
    venv/bin/python -m llm.olcum deprem qwen3.8 --k 1              # tek tek sor (biçim etkisi)
    venv/bin/python -m llm.olcum deprem claude-haiku-5-5           # bulut (bütçe defterinden geçer)
    venv/bin/python -m llm.olcum deprem qwen3.8 --alan cerceve --kaydet
    venv/bin/python -m llm.olcum --modeller                        # yereldeki modelleri listele

Hız notu (M3 Max 64 GB, 24.10): qwen3.8 ~3,2 bin başlık/saat. Eşzamanlılık kazandırmaz (1 -> 4 paralel
istek 0,79 -> 0,88 başlık/sn); GPU tek istekle doyuyor. `--paralel` başka donanım için bırakıldı.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import tomllib
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
KODKITABI_DIZIN = ROOT / "llm" / "kodkitabi"
OLLAMA = "http://localhost:11434"

# Varsayılan: kod kitabının İLK İKİ alanı (kod kitabı alanları önem sırasına göre yazar). Alan adını
# sabitlemek konuya bağ yaratır — her vaka kendi alan adlarını kullanır. Hepsini birden ölçmek de
# istenmez: ölçümde bir çağrıda alan sayısı arttıkça
# önceki alanların uyumu düşüyordu. Fazlası --alan ile açıkça istenir.
VARSAYILAN_ALAN_SAYISI = 2
K_VARSAYILAN = 25          # çağrı başına başlık; 24.10'da toplu istem tek tek sormaktan iyi çıktı
ZAMAN_ASIMI = 900

# Ön denetim eşikleri: bir seçenek örneklemin bu kadarını alıyorsa tanımı fazla geniştir
# (deprem'de "barinma" ve "tutum"daki "konu_disi" böyle yakalanabilirdi); bu kadarının altında
# kalan seçenek ise pratikte ölüdür ve alanın ölçülebilirliğini düşürür.
SAPLANMA_ESIGI = 0.40
OLU_ESIGI = 0.02
# Yapısal olarak koşullu seçenekler: başka bir alanın değerine bağlıdırlar (ör. "uygulanamaz" yalnızca
# ilgi alanı konu dışıysa seçilir). Alanlar ayrı ayrı ölçüldüğünde model o bağlamı görmez ve seçenek
# haklı olarak boş kalır; ölü seçenek saymak yanlış alarm olur.
KOSULLU_SECENEKLER = {"uygulanamaz"}


# ------------------------------------------------------------------ kod kitabı ve istem

def kodkitabi(vaka: str) -> dict:
    yol = KODKITABI_DIZIN / f"{vaka}.toml"
    if not yol.exists():
        mevcut = ", ".join(sorted(p.stem for p in KODKITABI_DIZIN.glob("*.toml"))) or "yok"
        raise SystemExit(f"Kod kitabı bulunamadı: {yol}\nMevcut vakalar: {mevcut}")
    return tomllib.loads(yol.read_text())


def _alan_sozlugu(kk: dict) -> dict:
    return {a["ad"]: a for a in kk["alan"]}


def _secenek_metni(alan: dict) -> str:
    return "\n".join(f"- {anahtar}: {baslik} — {acik}" for anahtar, baslik, acik in alan["secenekler"])


def istem(kk: dict, alanlar: list[str]) -> str:
    """Hem yerel hem bulut modele giden ortak sistem istemi. Kod kitabı tanımları aynen kullanılır;
    burada yeniden yazılmaz — yoksa insan kodlayıcıyla ölçüm arasında sapma olur."""
    al = _alan_sozlugu(kk)
    kalip = ", ".join(f'"{a}": "<anahtar>"' for a in alanlar)
    bloklar = "\n\n".join(
        f"ALAN {i} — {ad} ({al[ad]['soru']})\n{_secenek_metni(al[ad])}"
        for i, ad in enumerate(alanlar, 1)
    )
    kurallar = [
        "Yalnızca başlığa bak. Haberin içeriğini tahmin etme.",
        "Her alan için TEK seçenek seç ve anahtarı aynen yaz.",
        "Verilen her numara için tam bir kayıt döndür, hiçbirini atlama.",
    ]
    if "ilgi" in alanlar:
        for ad in alanlar:
            if ad == "ilgi":
                continue
            if "uygulanamaz" in {s[0] for s in al[ad]["secenekler"]}:
                kurallar.append(
                    f'"ilgi" = "baska" veya "ilgisiz" ise "{ad}" mutlaka "uygulanamaz" olur.')
    return (
        f"Sen bir içerik analizi kodlayıcısısın. Konu: {kk['konu']}\n\n"
        "Sana numaralı Türkçe haber BAŞLIKLARI verilecek. Her biri için aşağıdaki alanları kodla.\n\n"
        "KURALLAR:\n" + "\n".join(f"- {k}" for k in kurallar) + "\n\n"
        + bloklar + "\n\n"
        + f'Yanıt yalnızca şu JSON olsun: {{"kayitlar": [{{"no": 1, {kalip}}}, ...]}}'
    )


# ------------------------------------------------------------------ altın set

def ornek_oku(vaka: str, alanlar: list[str]) -> pd.DataFrame:
    """Ön denetim için: altın set ÖRNEKLEMİ (altin_set.csv), insan etiketi olmadan.
    İnsan kodlamasına başlamadan önce kod kitabının ölçülebilir olup olmadığını sınamak içindir."""
    s_yol = ROOT / "dosyalar" / vaka / "altin_set.csv"
    if not s_yol.exists():
        raise SystemExit(f"Örneklem yok: {s_yol}\n"
                         f"Önce: venv/bin/python -m llm.ornek {vaka} altin")
    df = pd.read_csv(s_yol, encoding="utf-8-sig")
    for a in alanlar:
        df[a] = ""                      # insan etiketi yok; rapor yalnızca dağılıma bakar
    print(f"Ön denetim: {len(df)} başlık (insan etiketi kullanılmıyor)")
    return df.reset_index(drop=True)


def kodlayicilar(vaka: str) -> dict[str, Path]:
    """Vakadaki insan kodlayıcılar: `altin_etiketler_<ad>.csv` dosyalarından bulunur."""
    dizin = ROOT / "dosyalar" / vaka
    return {y.stem.replace("altin_etiketler_", ""): y
            for y in sorted(dizin.glob("altin_etiketler_*.csv"))}


def altin(vaka: str, alanlar: list[str], kodlayici: str | None = None) -> pd.DataFrame:
    """İnsan etiketleri + başlık metni. `karar_verilemedi` işaretli satırlar düşülür: insan kodlayıcı
    emin olamadığı yerde modelden uyum beklemek ölçümü bozar."""
    dizin = ROOT / "dosyalar" / vaka
    s_yol = dizin / "altin_set.csv"
    if not s_yol.exists():
        raise SystemExit(f"Altın set örneklemi yok: {s_yol}\n"
                         f"Önce: venv/bin/python -m llm.ornek {vaka} altin")

    kisiler = kodlayicilar(vaka)
    if not kisiler:
        raise SystemExit(f"{dizin} altında insan etiketi yok (altin_etiketler_<ad>.csv).\n"
                         f"Altın set insan eliyle kodlanır: "
                         f"venv/bin/streamlit run llm/kodlama.py --server.port 8503")
    if kodlayici:
        if kodlayici not in kisiler:
            raise SystemExit(f"Kodlayıcı yok: {kodlayici} (mevcut: {', '.join(kisiler)})")
        e_yol = kisiler[kodlayici]
    elif len(kisiler) == 1:
        e_yol = next(iter(kisiler.values()))
    else:
        raise SystemExit(f"Birden fazla kodlayıcı var: {', '.join(kisiler)}. "
                         f"--kodlayici ile birini seçin.")
    print(f"Kodlayıcı: {e_yol.stem.replace('altin_etiketler_', '')}")

    etiket = pd.read_csv(e_yol, encoding="utf-8-sig")
    ornek = pd.read_csv(s_yol, encoding="utf-8-sig")
    df = etiket.merge(ornek[["kimlik", "metin", "donem"]], on="kimlik", how="inner")

    eksik = [a for a in alanlar if a not in df.columns]
    if eksik:
        raise SystemExit(f"Altın sette bulunmayan alan: {', '.join(eksik)}")

    toplam = len(df)
    df = df[df["karar_verilemedi"] != 1]
    df = df[df["metin"].notna() & (df["metin"].astype(str).str.strip() != "")]
    for a in alanlar:
        df = df[df[a].notna()]
    print(f"Altın set: {toplam} kodlanmış kayıt -> {len(df)} kullanılabilir "
          f"(karar_verilemedi ve boş alanlar düşüldü)")
    return df.reset_index(drop=True)


# ------------------------------------------------------------------ model çağrısı

def yerel_modeller() -> list[str]:
    try:
        with urllib.request.urlopen(f"{OLLAMA}/api/tags", timeout=5) as f:
            return [m["name"] for m in json.load(f).get("models", [])]
    except Exception:
        return []


def _json_ayikla(ham: str) -> list:
    """Modelin metninden JSON kayıt listesini çıkarır. Yerelde `format=json` ile temiz gelir; bulutta
    model açıklama cümlesi ekleyebildiği için ilk `{` ile son `}` arası alınır."""
    try:
        d = json.loads(ham)
    except json.JSONDecodeError:
        try:
            d = json.loads(ham[ham.index("{"):ham.rindex("}") + 1])
        except (ValueError, json.JSONDecodeError):
            return []
    kayit = d.get("kayitlar", d if isinstance(d, list) else [])
    return [k for k in kayit if isinstance(k, dict)]


def _cagir_yerel(model: str, sistem: str, kullanici: str) -> tuple[list, int]:
    """Ollama /api/chat. `think=False`: qwen3 ailesinde düşünme varsayılan açıktır, bu iş için gereksiz
    ve token tavanını yiyor (anlatıcıda da aynı karar alındı)."""
    govde = json.dumps({
        "model": model, "stream": False, "think": False, "format": "json",
        "options": {"temperature": 0, "num_ctx": 16384},
        "messages": [{"role": "system", "content": sistem},
                     {"role": "user", "content": kullanici}],
    }).encode()
    istek = urllib.request.Request(f"{OLLAMA}/api/chat", govde,
                                   {"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(istek, timeout=ZAMAN_ASIMI) as f:
            d = json.load(f)
    except urllib.error.URLError as e:
        mevcut = ", ".join(yerel_modeller()) or "(Ollama yanıt vermiyor)"
        raise SystemExit(f"Ollama çağrısı başarısız: {e}\nYereldeki modeller: {mevcut}")
    return _json_ayikla(d.get("message", {}).get("content", "")), d.get("eval_count", 0)


def _cagir_bulut(model: str, sistem: str, kullanici: str, k: int, alan_sayisi: int) -> tuple[list, int]:
    """Claude. `butce.cagir`'dan geçer: tavan denetimi ve harcama defteri işler. Haiku 5.5 assistant
    prefill desteklemiyor, bu yüzden JSON'u istemle zorluyoruz."""
    from llm import butce

    # Kayıt başına ~25 token (no + alanlar) + pay
    max_tokens = max(500, k * (25 + 12 * alan_sayisi))
    yanit = butce.cagir(model=model, etiket="altin-olcum", max_tokens=max_tokens,
                        system=sistem, thinking={"type": "disabled"},
                        messages=[{"role": "user", "content": kullanici}])
    metin = "".join(p.text for p in yanit.content if getattr(p, "type", "") == "text")
    return _json_ayikla(metin), getattr(yanit.usage, "output_tokens", 0)


def yerel_mi(model: str) -> bool:
    return not model.startswith("claude-")


def cagir(model: str, sistem: str, kullanici: str, k: int, alan_sayisi: int) -> tuple[list, int]:
    ad = model.split("ollama:", 1)[-1]
    if yerel_mi(model):
        return _cagir_yerel(ad, sistem, kullanici)
    return _cagir_bulut(model, sistem, kullanici, k, alan_sayisi)


# ------------------------------------------------------------------ ölçüm

def olc(vaka: str, model: str, alanlar: list[str], k: int = K_VARSAYILAN,
        n: int | None = None, paralel: int = 1,
        kodlayici: str | None = None, ondenetim: bool = False) -> tuple[pd.DataFrame, dict]:
    kk = kodkitabi(vaka)
    sistem = istem(kk, alanlar)
    df = ornek_oku(vaka, alanlar) if ondenetim else altin(vaka, alanlar, kodlayici)
    if n:
        df = df.head(n)

    gruplar = [df.iloc[i:i + k] for i in range(0, len(df), k)]
    print(f"\nmodel={model}  {'yerel' if yerel_mi(model) else 'bulut'}  n={len(df)}  "
          f"K={k}  alanlar={alanlar}  çağrı={len(gruplar)}"
          + (f"  paralel={paralel}" if paralel > 1 else ""), flush=True)

    bitti = [0]
    t0 = time.time()

    def bir_grup(grup: pd.DataFrame) -> list[dict]:
        kullanici = "\n".join(
            f"{i + 1}. [{r.donem}] {r.metin}" for i, r in enumerate(grup.itertuples()))
        try:
            kayit, _ = cagir(model, sistem, kullanici, len(grup), len(alanlar))
        except SystemExit:
            raise
        except Exception as e:                                  # tek grup patlasa ölçüm sürsün
            print(f"  ! grup hatası: {type(e).__name__}: {e}", flush=True)
            kayit = []
        eslem = {kyt.get("no"): kyt for kyt in kayit}
        cikti = []
        for i, r in enumerate(grup.itertuples()):
            y = eslem.get(i + 1, {})
            satir = {"kimlik": r.kimlik, "metin": r.metin, "donem": r.donem}
            for a in alanlar:
                satir[f"insan_{a}"] = str(getattr(r, a)).strip()
                satir[f"model_{a}"] = str(y.get(a, "")).strip()
            cikti.append(satir)
        bitti[0] += len(grup)
        print(f"  {bitti[0]}/{len(df)}  {time.time() - t0:.0f} sn  "
              f"(dönen {len(kayit)}/{len(grup)})", flush=True)
        return cikti

    if paralel > 1:
        with ThreadPoolExecutor(max_workers=paralel) as ex:
            satirlar = [s for parca in ex.map(bir_grup, gruplar) for s in parca]
    else:
        satirlar = [s for grup in gruplar for s in bir_grup(grup)]

    sure = time.time() - t0
    sonuc = pd.DataFrame(satirlar)
    bilgi = {"model": model, "vaka": vaka, "n": len(sonuc), "k": k, "sure": sure,
             "ondenetim": ondenetim,
             "surum": kk.get("surum", ""),
             "sn_basina": sure / max(len(sonuc), 1),
             "saatte": 3600 / (sure / max(len(sonuc), 1)) if len(sonuc) else 0}
    return sonuc, bilgi


# ------------------------------------------------------------------ rapor

def rapor_ondenetim(sonuc: pd.DataFrame, alanlar: list[str], bilgi: dict, kk: dict) -> None:
    """Kod kitabı ön denetimi: doğruluk ÖLÇMEZ (insan etiketi yok), ölçülebilirliği denetler.
    Bir seçenek örneklemi yutuyorsa tanımı geniştir; hiç seçilmeyen seçenek alanı zayıflatır."""
    al = _alan_sozlugu(kk)
    print("\n" + "=" * 66)
    print(f"ÖN DENETİM · {bilgi['model']} · kod kitabı {bilgi['surum']} · n={bilgi['n']}")
    print(f"SÜRE {bilgi['sure']:.0f} sn ({bilgi['sn_basina']:.2f} sn/başlık)")
    print("\nBu rapor DOĞRULUK ÖLÇMEZ — insan etiketi yok. Yalnızca kod kitabının ölçülebilir olup\n"
          "olmadığını denetler. Doğruluk için altın setin insan eliyle kodlanması şarttır.")

    sorun = []
    for ad in alanlar:
        model = sonuc[f"model_{ad}"]
        gecerli = [s[0] for s in al[ad]["secenekler"]]
        dolu = model[model != ""]
        print(f"\n--- {ad.upper()} ({len(gecerli)} seçenek) ---")
        if dolu.empty:
            print("  model hiç yanıt vermedi"); sorun.append((ad, "yanıt yok", 0.0)); continue
        pay = dolu.value_counts(normalize=True)
        # Süzgeç alanında (kod kitabında `tur = "suzgec"`) baskın sınıf beklenir: korpus kalıbı
        # temizse haberlerin çoğu zaten konu içidir. Orada saplanma uyarısı yanlış alarmdır;
        # alanın işlevi gürültüyü elemektir, bulgu üretmek değil.
        suzgec = al[ad].get("tur") == "suzgec"
        if suzgec:
            print("  (süzgeç alanı: baskın sınıf beklenir, saplanma denetlenmez; bulgu alanı değil)")
        for s in gecerli:
            p_ = float(pay.get(s, 0.0))
            isaret = ""
            if suzgec:
                pass
            elif p_ >= SAPLANMA_ESIGI:
                isaret = "  <== SAPLANMA: tanım fazla geniş, bölün"; sorun.append((ad, f"'{s}' saplanması", p_))
            elif s in KOSULLU_SECENEKLER:
                isaret = "  (koşullu seçenek; ölü sayılmaz)"
            elif p_ < OLU_ESIGI:
                isaret = "  <== ölü seçenek"; sorun.append((ad, f"'{s}' ölü", p_))
            if suzgec and p_ < OLU_ESIGI:
                isaret = "  (süzgeçte seyrek seçenek; sorun sayılmaz)"
            print(f"  %{100*p_:5.1f}  {s:<16}{isaret}")
        kacak = sorted(set(dolu) - set(gecerli))
        if kacak:
            print(f"  kod kitabında olmayan anahtar: {', '.join(kacak)}")
            sorun.append((ad, f"kaçak anahtar: {', '.join(kacak)}", 0.0))
        bos = int((model == "").sum())
        if bos:
            print(f"  yanıtsız: {bos}/{len(model)}")

    print("\n" + "-" * 66)
    if sorun:
        print(f"{len(sorun)} sorun bulundu — insan kodlamasına başlamadan kod kitabını düzeltin:")
        for ad, ne, p_ in sorun:
            print(f"  · {ad}: {ne}" + (f" (%{100*p_:.1f})" if p_ else ""))
        print("\nDüzeltip bu komutu yeniden çalıştırın; yerelde maliyet 0.")
    else:
        print(f"Saplanma (>=%{100*SAPLANMA_ESIGI:.0f}) veya ölü seçenek (<%{100*OLU_ESIGI:.0f}) yok.")
        print("Kod kitabı ölçülebilir görünüyor; insan kodlamasına geçebilirsiniz "
              "(venv/bin/streamlit run llm/kodlama.py --server.port 8503).")


def rapor(sonuc: pd.DataFrame, alanlar: list[str], bilgi: dict, kk: dict) -> None:
    al = _alan_sozlugu(kk)
    print("\n" + "=" * 66)
    print(f"{bilgi['model']}  ·  kod kitabı sürümü {bilgi['surum']}  ·  n={bilgi['n']}")
    print(f"SÜRE {bilgi['sure']:.0f} sn  ({bilgi['sn_basina']:.2f} sn/başlık, "
          f"~{bilgi['saatte'] / 1000:.1f} bin başlık/saat)")

    for ad in alanlar:
        insan, model = sonuc[f"insan_{ad}"], sonuc[f"model_{ad}"]
        bos = (model == "").sum()
        esit = insan == model
        print(f"\n--- {ad.upper()} ({len(al[ad]['secenekler'])} seçenek) ---")
        print(f"uyum {esit.sum()}/{len(sonuc)} = %{100 * esit.mean():.1f}"
              + (f"   (yanıtsız {bos})" if bos else ""))

        gecerli = {s[0] for s in al[ad]["secenekler"]}
        kacak = sorted(set(model) - gecerli - {""})
        if kacak:
            print(f"kod kitabında olmayan anahtar üretti: {', '.join(kacak)}")

        # Seçenek bazlı fazla/az seçim: bir seçeneğin diğerlerini yutup yutmadığını gösterir
        tablo = pd.DataFrame({
            "insan": insan.value_counts(),
            "model": model.value_counts(),
        }).fillna(0).astype(int)
        tablo["fark"] = tablo["model"] - tablo["insan"]
        tablo["isabet"] = [int(((insan == s) & (model == s)).sum()) for s in tablo.index]
        tablo = tablo.sort_values("fark", key=abs, ascending=False)
        print("\nseçenek bazlı (fark = model - insan; büyük artı = bu seçeneğe saplanma):")
        print(tablo.to_string())

        yanlis = sonuc[~esit]
        if len(yanlis):
            print("\nen sık karışma (insan -> model):")
            print(yanlis.groupby([f"insan_{ad}", f"model_{ad}"]).size()
                  .sort_values(ascending=False).head(8).to_string())


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        prog="llm.olcum", description="Altın sete karşı model ölçümü (yerel Ollama veya Claude).")
    p.add_argument("vaka", nargs="?", help="kod kitabı adı, ör. deprem")
    p.add_argument("model", nargs="?", help="yerel: qwen3.8 · bulut: claude-haiku-5-5")
    p.add_argument("--alan", action="append", dest="alanlar",
                   help=f"ölçülecek alan (yinelenebilir); varsayılan kod kitabının ilk "
                        f"{VARSAYILAN_ALAN_SAYISI} alanı")
    p.add_argument("--k", type=int, default=K_VARSAYILAN, help="çağrı başına başlık (1 = tek tek sor)")
    p.add_argument("--n", type=int, help="yalnızca ilk N başlık (deneme için)")
    p.add_argument("--paralel", type=int, default=1, help="eşzamanlı istek (yerelde kazandırmıyor)")
    p.add_argument("--kaydet", action="store_true",
                   help="satır satır sonucu data/store/olcum_<vaka>_<model>.csv olarak yaz")
    p.add_argument("--kodlayici", help="hangi insan kodlayıcının etiketleri (birden fazlaysa zorunlu)")
    p.add_argument("--ondenetim", action="store_true",
                   help="insan etiketi beklemeden kod kitabını sına: doğruluk ölçmez, "
                        "saplanan ve ölü seçenekleri bulur")
    p.add_argument("--modeller", action="store_true", help="yereldeki Ollama modellerini listele")
    a = p.parse_args(argv)

    if a.modeller:
        m = yerel_modeller()
        print("\n".join(m) if m else "Ollama yanıt vermiyor (ollama serve çalışıyor mu?)")
        return 0
    if not a.vaka or not a.model:
        p.error("vaka ve model gerekli (veya --modeller)")

    kk = kodkitabi(a.vaka)
    alanlar = a.alanlar or [x["ad"] for x in kk["alan"]][:VARSAYILAN_ALAN_SAYISI]
    tanimli = set(_alan_sozlugu(kk))
    if bilinmeyen := [x for x in alanlar if x not in tanimli]:
        p.error(f"kod kitabında yok: {', '.join(bilinmeyen)} (tanımlı: {', '.join(sorted(tanimli))})")

    if a.ondenetim and a.kodlayici:
        p.error("--ondenetim insan etiketi kullanmaz; --kodlayici ile birlikte verilmez")

    oncesi = None
    if not yerel_mi(a.model):
        from llm import butce
        oncesi = butce.harcanan()

    sonuc, bilgi = olc(a.vaka, a.model, alanlar, k=a.k, n=a.n, paralel=a.paralel,
                       kodlayici=a.kodlayici, ondenetim=a.ondenetim)
    (rapor_ondenetim if a.ondenetim else rapor)(sonuc, alanlar, bilgi, kk)

    if oncesi is not None:
        from llm import butce
        print(f"\nbu ölçümün maliyeti: {butce.harcanan() - oncesi:.4f} $  "
              f"(bu ay toplam {butce.harcanan():.4f} $)")
    else:
        print("\nbu ölçümün maliyeti: 0 $ (yerel model)")

    if a.kaydet:
        yol = ROOT / "data" / "store" / f"olcum_{a.vaka}_{a.model.replace(':', '_')}.csv"
        yol.parent.mkdir(parents=True, exist_ok=True)
        sonuc.to_csv(yol, index=False)
        print(f"satır satır sonuç: {yol}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
