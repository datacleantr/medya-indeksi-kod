"""Arşive sor (modül H): serbest bir soruyu, ajan araçlarını kullanarak kaynaklı biçimde cevaplar.

Mimari, N2 anlatıcısıyla aynı ilkededir — **model ölçmez, anlatır** — ama paket sabit değildir:
modelin hangi aracı hangi argümanla çağıracağına karar vermesine izin verilir. Vektör araması
(RAG) yoktur; model yalnızca `ajan/araclar.py`'deki altı aracı çağırabilir, başka hiçbir kaynağa
erişemez.

MCP'de kaybettiğimiz şey burada geri kazanılır: **hangi aracın ne döndürdüğü ekranda gösterilir.**
Model bir rakamı uydurursa kullanıcı araç çıktısına bakıp görebilir.

Maliyet `llm/butce.py` tavanından geçer; bir soru genelde 2–3 model çağrısı eder.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from llm import butce  # noqa: E402

MODEL = "claude-haiku-5-5"
MAX_TOKENS = 1500
ETIKET = "arsive-sor"
# Bir soruda en fazla kaç tur araç çağrısı yapılabilir (kaçak döngüye karşı)
MAX_TUR = 4

SISTEM = """Sen bir Türkçe medya arşivi panelinin araştırma asistanısın. Kullanıcının sorusunu
SANA VERİLEN ARAÇLARLA cevaplarsın.

MUTLAK KURALLAR:
1. Yalnızca araç çıktılarındaki bilgiyi kullan. Dünya bilgini kullanma. Bir şey araç çıktısında
   yoksa "arşivde bu bilgi yok" de.
2. NEDEN SÖYLEME. Bu veri haberlerin NEYİ ne kadar yazdığını gösterir, NEDEN yazdığını göstermez.
   "X yüzünden", "X nedeniyle", "X'in etkisiyle" YASAK. İzin verilen: "aynı dönemde", "bununla birlikte".
3. Ölçülen şey olayın kendisi değil, HABERE KONU OLMA YOĞUNLUĞUDUR. "Şu kadar olay oldu" deme;
   "şu kadar haber çıktı" de.
4. Araç çıktısındaki `sinir` ve `kapsam_duzeyi` alanlarını dikkate al. Kapsam "Sınırlı" veya
   "Yetersiz" ise bunu cevabının BAŞINDA söyle.
5. Rakamları araç çıktısındaki hâliyle ver; kendi hesabını yapma.
6. Bir çerçeveye "olağandışı" (yüksek veya düşük) YALNIZCA araç çıktısının `sapmalar` listesinde
   yer alıyorsa diyebilirsin. `cerceveler` listesindeki yüzdelik değerleri tek başına bu nitelemeyi
   hak etmez; eşiği araç belirler, sen belirlemezsin.

ARAÇ KULLANIMI: Önce hangi aracın gerektiğine karar ver. Soru bir kişi, kurum, yer veya konu
hakkındaysa `konu_ara` kullan. Haftalık gündem sorusuysa `gundem_ozeti`. Bir yayının eğilimi
soruluyorsa `yayin_profili`. Gerekirse birden fazla araç çağır. Emin değilsen önce `kapsam` ile
verinin olup olmadığına bak.

ÜSLUP: Türkçe, kısa cümleler, 2–4 paragraf. Abartı sıfatı kullanma ("çarpıcı", "dikkat çekici").
Rakamları cümle içinde ver. Sonunda hangi araçları kullandığını yazma — kullanıcı zaten görüyor."""


def _semalar() -> list:
    """Araç tanımları. Açıklamalar MCP sunucusundakiyle aynı ilkededir: her aracın sınırı yazılı."""
    tarih = {"type": "string", "description": "YYYY-AA-GG; verilmezse son 90 gün"}
    return [
        {"name": "konu_ara",
         "description": ("Bir kişi, kurum, yer veya konunun Türkçe haberlerde nasıl ele alındığı: haber "
                         "sayısı, aylık dağılım, en çok yazan siteler, çerçeve payları, ton, kapsam "
                         "değerlendirmesi ve örnek başlıklar. Arama ifadenin haberin ADRESİNDE, BAŞLIĞINDA "
                         "veya kişi listesinde geçtiği haberleri bulur; yalnızca gövdede geçen bir ad "
                         "BULUNAMAZ."),
         "input_schema": {"type": "object", "properties": {
             "ifade": {"type": "string", "description": "Aranacak kişi, kurum, yer veya konu"},
             "baslangic": tarih, "bitis": tarih}, "required": ["ifade"]}},
        {"name": "gundem_ozeti",
         "description": ("Bir haftanın medya gündemi: hacim, ton, çerçeve payları ve her çerçevenin kendi "
                         "önceki 52 haftasına göre olağandışı olup olmadığı. Sapan çerçeveler için örnek "
                         "başlıklar da döner."),
         "input_schema": {"type": "object", "properties": {
             "hafta": {"type": "string", "description": "ISO etiketi (2026-W42); boşsa son tam hafta"}}}},
        {"name": "cerceve_serisi",
         "description": ("Bir çerçevenin günlük payının zaman serisi. Geçerli çerçeveler: Ekonomi, "
                         "Siyaset / seçim, Yargı / soruşturma, Afet / kaza, Güvenlik / terör, Dış ilişkiler, "
                         "Sağlık, Eğitim, Göç, Kültür / sanat / spor, Anma / tören, Diğer."),
         "input_schema": {"type": "object", "properties": {
             "cerceve": {"type": "string"}, "baslangic": tarih, "bitis": tarih}, "required": ["cerceve"]}},
        {"name": "yayin_profili",
         "description": ("Bir yayının çerçeve dağılımı ve her çerçeveyi kilit yayınların geneline göre kaç "
                         "kat öne çıkardığı (1,0 ortalama). Yayınların HABER SAYILARI birbiriyle "
                         "karşılaştırılamaz, yalnızca paylar karşılaştırılabilir."),
         "input_schema": {"type": "object", "properties": {
             "yayin": {"type": "string", "description": "Alan adı, ör. sozcu.com.tr"},
             "baslangic": tarih, "bitis": tarih}, "required": ["yayin"]}},
        {"name": "dunya_turk",
         "description": ("Türkiye'de geçen olayları dünya medyası ne kadar yazdı, arşivde Türkçe haberi var "
                         "mı? 'Türkçe haberi yok' Türk medyasının hiç yazmadığı anlamına GELMEZ."),
         "input_schema": {"type": "object", "properties": {
             "min_kaynak": {"type": "integer", "description": "En az kaç farklı kaynak (varsayılan 5)"},
             "limit": {"type": "integer"}}}},
        {"name": "kapsam",
         "description": "Bir dönemde arşivde hangi yayınlar var ve hacimleri ne?",
         "input_schema": {"type": "object", "properties": {
             "baslangic": tarih, "bitis": tarih}, "required": ["baslangic", "bitis"]}},
    ]


def sor(soru: str, model: str = MODEL) -> dict:
    """Soruyu araç döngüsüyle cevaplar.
    {"cevap", "adimlar": [{"arac","girdi","cikti"}], "maliyet", "tur", "model"}"""
    from ajan import araclar

    mesajlar = [{"role": "user", "content": soru}]
    adimlar, toplam = [], 0.0
    cevap = ""
    for tur in range(1, MAX_TUR + 1):
        yanit = butce.cagir(model=model, etiket=ETIKET, max_tokens=MAX_TOKENS, system=SISTEM,
                            messages=mesajlar, tools=_semalar(), thinking={"type": "disabled"})
        u = yanit.usage
        toplam += butce.maliyet(model, girdi=u.input_tokens, cikti=u.output_tokens)
        cevap = "".join(b.text for b in yanit.content if b.type == "text").strip() or cevap
        istekler = [b for b in yanit.content if b.type == "tool_use"]
        if not istekler:
            break
        mesajlar.append({"role": "assistant", "content": yanit.content})
        sonuclar = []
        for b in istekler:
            fn = araclar.ARACLAR.get(b.name)
            try:
                cikti = fn(**b.input) if fn else {"hata": f"Bilinmeyen araç: {b.name}"}
            except Exception as e:
                cikti = {"hata": f"{type(e).__name__}: {e}"}
            adimlar.append({"arac": b.name, "girdi": b.input, "cikti": cikti})
            sonuclar.append({"type": "tool_result", "tool_use_id": b.id,
                             "content": json.dumps(cikti, ensure_ascii=False, default=str)})
        mesajlar.append({"role": "user", "content": sonuclar})
    return {"cevap": cevap, "adimlar": adimlar, "maliyet": toplam, "tur": tur, "model": model}
