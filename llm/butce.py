"""LLM harcama defteri ve tavanı: her Claude çağrısı önce tahmin edilir, sonra gerçeği deftere yazılır.

Neden kodda: Console'daki harcama tavanı kendi kodumuzdaki bir döngü hatasından korumaz; asıl risk odur.
Ayrıca projedeki anahtar kullanıcı anahtarı (`sk-ant-usr-`) olduğu için Console tavanının uygulanıp
uygulanmadığı belirsizdir. Bu defter anahtar tipinden bağımsız çalışır.

    venv/bin/python -m llm.butce durum        # bu ayın harcaması ve kalan tavan
    venv/bin/python -m llm.butce gecmis       # son kayıtlar
    venv/bin/python -m llm.butce tahmin claude-haiku-5-5 160000 120000 --batch

Kullanım (kod içinden):

    from llm import butce
    yanit = butce.cagir(model="claude-haiku-5-5", etiket="anlatici",
                        max_tokens=1200, messages=[{"role": "user", "content": istem}])

`cagir` sırasıyla: girdi token'larını sayar (ücretsiz) → en kötü durum maliyetini hesaplar (çıktı =
`max_tokens`) → aylık tavanı aşacaksa **çağrıyı yapmaz** → çağırır → `usage`'dan gerçek maliyeti deftere yazar.

Defter: `data/store/llm_harcama.jsonl` (git dışı), satır başına bir çağrı.
"""
import argparse
import json
import os
import sys
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFTER = ROOT / "data" / "store" / "llm_harcama.jsonl"

# Aylık tavan (USD). Kullanıcı 29.09.2026'da 20 $ onayladı; ortam değişkeniyle geçici olarak değiştirilebilir.
AYLIK_TAVAN = float(os.environ.get("LLM_AYLIK_TAVAN", "20"))
# Tek bir çalışmanın (süreç) harcayabileceği en fazla tutar: kaçak döngüye karşı ikinci bariyer.
CALISMA_TAVANI = float(os.environ.get("LLM_CALISMA_TAVANI", "5"))

# $ / 1M token. Kaynak: platform.claude.com/docs/en/about-claude/pricing, çekilme tarihi 23.10.2026.
# Fiyat değişirse burası güncellenir; defterdeki eski kayıtlar o günkü fiyatla hesaplanmış hâliyle kalır.
FIYAT = {
    "claude-haiku-5-5":  {"girdi": 0.10, "cikti": 0.50, "onbellek_okuma": 0.01,  "onbellek_yazma": 0.125},
    "claude-haiku-4-5":  {"girdi": 1.00, "cikti": 5.00, "onbellek_okuma": 0.10,  "onbellek_yazma": 1.25},
    "claude-sonnet-5-5": {"girdi": 2.00, "cikti": 10.0, "onbellek_okuma": 0.10,  "onbellek_yazma": 2.50},
    "claude-sonnet-5":   {"girdi": 2.00, "cikti": 10.0, "onbellek_okuma": 0.20,  "onbellek_yazma": 2.50},
    "claude-opus-5-5":   {"girdi": 4.00, "cikti": 20.0, "onbellek_okuma": 0.20,  "onbellek_yazma": 5.00},
    "claude-opus-5":     {"girdi": 5.00, "cikti": 25.0, "onbellek_okuma": 0.50,  "onbellek_yazma": 6.25},
}
# Haiku 5.5'te 100.000 token'ı aşan istemler 5 kat pahalıdır (fiyat listesinde ayrı satır).
# Kademe **tek bir isteğin istem boyutuna** bakar, toplam token'a değil: 4.000 ayrı küçük çağrının
# toplamı 160.000 token olsa da her biri kısa istemdir ve ucuz kademededir.
HAIKU_ESIK, HAIKU_KAT = 100_000, 5.0
# Batch API girdide ve çıktıda %50 indirimlidir
BATCH_INDIRIM = 0.5


class ButceAsimi(RuntimeError):
    """Tavan aşılacağı için çağrı yapılmadı."""


def _fiyat(model: str) -> dict:
    if model not in FIYAT:
        raise ValueError(f"Fiyatı bilinmeyen model: {model}. llm/butce.py içindeki FIYAT'a ekleyin "
                         f"(bilinenler: {', '.join(sorted(FIYAT))})")
    return FIYAT[model]


def maliyet(model: str, girdi: int = 0, cikti: int = 0, onbellek_okuma: int = 0, onbellek_yazma: int = 0,
            batch: bool = False, istem_token: int | None = None) -> float:
    """Bir iş yükünün USD maliyeti. `istem_token` **tek bir isteğin** istem boyutudur ve yalnızca
    Haiku 5.5'in fiyat kademesini seçer; verilmezse `girdi + onbellek_okuma` varsayılır (tek çağrı hâli).
    Çok sayıda küçük çağrının toplamını hesaplarken istem_token'ı çağrı başına değerle verin."""
    f = _fiyat(model)
    istem = istem_token if istem_token is not None else girdi + onbellek_okuma
    kat = HAIKU_KAT if (model == "claude-haiku-5-5" and istem > HAIKU_ESIK) else 1.0
    tutar = (girdi * f["girdi"] + cikti * f["cikti"]
             + onbellek_okuma * f["onbellek_okuma"] + onbellek_yazma * f["onbellek_yazma"]) / 1e6 * kat
    return tutar * (BATCH_INDIRIM if batch else 1.0)


# --------------------------------------------------------------------------------
# DEFTER
# --------------------------------------------------------------------------------
_calisma_toplam = 0.0


def kayitlar(ay: str | None = None) -> list:
    """Defterdeki kayıtlar; `ay` verilirse (YYYY-AA) yalnızca o ay."""
    if not DEFTER.exists():
        return []
    out = []
    for satir in DEFTER.read_text().splitlines():
        if not satir.strip():
            continue
        try:
            k = json.loads(satir)
        except json.JSONDecodeError:
            continue
        if ay is None or k.get("zaman", "").startswith(ay):
            out.append(k)
    return out


def harcanan(ay: str | None = None) -> float:
    ay = ay or f"{date.today():%Y-%m}"
    return sum(k.get("maliyet", 0.0) for k in kayitlar(ay))


def kalan(ay: str | None = None) -> float:
    return AYLIK_TAVAN - harcanan(ay)


def yaz(model: str, etiket: str, girdi: int, cikti: int, onbellek_okuma: int, onbellek_yazma: int,
        batch: bool, tutar: float) -> None:
    DEFTER.parent.mkdir(parents=True, exist_ok=True)
    kayit = {"zaman": datetime.now().isoformat(timespec="seconds"), "model": model, "etiket": etiket,
             "girdi": girdi, "cikti": cikti, "onbellek_okuma": onbellek_okuma,
             "onbellek_yazma": onbellek_yazma, "batch": batch, "maliyet": round(tutar, 6)}
    with DEFTER.open("a") as f:
        f.write(json.dumps(kayit, ensure_ascii=False) + "\n")


def onayla(model: str, girdi: int, cikti: int, batch: bool = False, etiket: str = "") -> float:
    """Çağrıdan önce: en kötü durum maliyetini hesaplar, tavanları aşacaksa ButceAsimi yükseltir.
    Çıktı tahmini `max_tokens`tir (en kötü durum); böylece tavan hiçbir zaman eksik hesaplanmaz."""
    tahmin_tutar = maliyet(model, girdi=girdi, cikti=cikti, batch=batch)
    k = kalan()
    if tahmin_tutar > k:
        raise ButceAsimi(
            f"Aylık tavan aşılacaktı, çağrı yapılmadı. Tahmin {tahmin_tutar:.4f} $, kalan {k:.4f} $ "
            f"(tavan {AYLIK_TAVAN:.2f} $, bu ay harcanan {harcanan():.4f} $). "
            f"Tavanı geçici olarak LLM_AYLIK_TAVAN ile değiştirebilirsiniz."
        )
    if _calisma_toplam + tahmin_tutar > CALISMA_TAVANI:
        raise ButceAsimi(
            f"Bu çalışmanın tavanı aşılacaktı ({CALISMA_TAVANI:.2f} $), çağrı yapılmadı. "
            f"Bu çalışmada şimdiye kadar {_calisma_toplam:.4f} $ harcandı. Kaçak döngüye karşı ikinci bariyer; "
            f"bilerek daha çok harcayacaksanız LLM_CALISMA_TAVANI ile yükseltin."
        )
    return tahmin_tutar


def anahtar() -> str:
    """ANTHROPIC_API_KEY: önce ortam değişkeni, sonra .streamlit/secrets.toml."""
    if os.environ.get("ANTHROPIC_API_KEY"):
        return os.environ["ANTHROPIC_API_KEY"]
    yol = ROOT / ".streamlit" / "secrets.toml"
    if yol.exists():
        import tomllib
        k = tomllib.loads(yol.read_text()).get("ANTHROPIC_API_KEY", "")
        if k:
            return k
    raise RuntimeError("ANTHROPIC_API_KEY bulunamadı (.streamlit/secrets.toml veya ortam değişkeni).")


def istemci():
    import anthropic

    return anthropic.Anthropic(api_key=anahtar())


def cagir(model: str, etiket: str, max_tokens: int, messages: list, system=None, client=None, **kwargs):
    """Tavan denetimli Claude çağrısı. Gerçek kullanım `usage`'dan deftere yazılır; yanıt döner."""
    global _calisma_toplam
    client = client or istemci()
    istek = {"model": model, "max_tokens": max_tokens, "messages": messages, **kwargs}
    if system is not None:
        istek["system"] = system

    # Girdi token'ları ücretsiz sayılır; sayılamazsa kabaca 4 karakter = 1 token varsayılır
    try:
        sayim = client.messages.count_tokens(**{k: v for k, v in istek.items()
                                                if k in ("model", "messages", "system", "tools")})
        girdi = sayim.input_tokens
    except Exception:
        girdi = sum(len(str(m.get("content", ""))) for m in messages) // 4 + 1

    onayla(model, girdi=girdi, cikti=max_tokens, etiket=etiket)
    yanit = client.messages.create(**istek)

    u = yanit.usage
    tutar = maliyet(model, girdi=u.input_tokens, cikti=u.output_tokens,
                    onbellek_okuma=getattr(u, "cache_read_input_tokens", 0) or 0,
                    onbellek_yazma=getattr(u, "cache_creation_input_tokens", 0) or 0)
    _calisma_toplam += tutar
    yaz(model, etiket, u.input_tokens, u.output_tokens,
        getattr(u, "cache_read_input_tokens", 0) or 0, getattr(u, "cache_creation_input_tokens", 0) or 0,
        False, tutar)
    return yanit


# --------------------------------------------------------------------------------
def _usd(x: float) -> str:
    """Küçük tutarlar 4 hanede 0,0000 göründüğü için gereken kadar hane kullanılır."""
    return f"{x:.6f}" if 0 < x < 0.01 else f"{x:.4f}"


def _durum(a):
    ay = a.ay or f"{date.today():%Y-%m}"
    ks = kayitlar(ay)
    h = sum(k["maliyet"] for k in ks)
    print(f"Ay {ay}: {len(ks)} çağrı, harcanan {_usd(h)} $, tavan {AYLIK_TAVAN:.2f} $, "
          f"kalan {_usd(AYLIK_TAVAN - h)} $  (%{h / AYLIK_TAVAN * 100:.2f} kullanıldı)")
    if not ks:
        print("  (bu ay kayıt yok)")
        return
    gruplar = {}
    for k in ks:
        g = gruplar.setdefault((k["etiket"], k["model"]), {"n": 0, "tutar": 0.0, "girdi": 0, "cikti": 0})
        g["n"] += 1
        g["tutar"] += k["maliyet"]
        g["girdi"] += k["girdi"]
        g["cikti"] += k["cikti"]
    print(f"  {'etiket':<18} {'model':<20} {'çağrı':>6} {'girdi tk':>10} {'çıktı tk':>10} {'tutar $':>10}")
    for (et, mo), g in sorted(gruplar.items(), key=lambda x: -x[1]["tutar"]):
        print(f"  {et:<18} {mo:<20} {g['n']:>6,} {g['girdi']:>10,} {g['cikti']:>10,} {_usd(g['tutar']):>10}")


def _gecmis(a):
    ks = kayitlar()[-a.n:]
    if not ks:
        print("Defter boş.")
        return
    for k in ks:
        print(f"  {k['zaman']}  {k['etiket']:<16} {k['model']:<20} "
              f"{k['girdi']:>7,}→{k['cikti']:>6,} tk  {k['maliyet']:.6f} $")


def _tahmin(a):
    istem = a.girdi // max(a.cagri, 1)
    t = maliyet(a.model, girdi=a.girdi, cikti=a.cikti, batch=a.batch, istem_token=istem)
    # (gösterim _usd ile; küçük tutarlarda 4 hane yetmiyor)
    print(f"{a.model}: {a.girdi:,} girdi + {a.cikti:,} çıktı token, {a.cagri:,} çağrı"
          + (f" (çağrı başına ~{istem:,} token istem)" if a.cagri > 1 else "")
          + (" · batch %50 indirimli" if a.batch else "") + f"  →  {_usd(t)} $")
    print(f"Bu ay kalan tavan: {_usd(kalan())} $")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    sp = sub.add_parser("durum", help="bu ayın harcaması")
    sp.add_argument("--ay", help="YYYY-AA (varsayılan: bu ay)")
    sp.set_defaults(fn=_durum)
    sp = sub.add_parser("gecmis", help="son kayıtlar")
    sp.add_argument("-n", type=int, default=20)
    sp.set_defaults(fn=_gecmis)
    sp = sub.add_parser("tahmin", help="bir iş yükünün maliyetini hesapla")
    sp.add_argument("model")
    sp.add_argument("girdi", type=int, help="TOPLAM girdi token")
    sp.add_argument("cikti", type=int, help="TOPLAM çıktı token")
    sp.add_argument("--batch", action="store_true")
    sp.add_argument("--cagri", type=int, default=1,
                    help="iş yükündeki çağrı sayısı (Haiku 5.5 fiyat kademesi çağrı başına istem boyutuna bakar)")
    sp.set_defaults(fn=_tahmin)
    a = p.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
