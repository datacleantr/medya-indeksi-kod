"""Medya İndeksi MCP sunucusu (M1): yapay zekâ ajanlarının Türkçe medya arşivini sorgulamasını sağlar.

Salt okunurdur. Araç gövdeleri `ajan/araclar.py`'dedir ve protokolden bağımsızdır; bu dosya yalnızca
MCP sarmalayıcısıdır.

    venv/bin/python -m ajan.sunucu           # stdio (Claude Desktop / Claude Code bunu çağırır)
    venv/bin/python -m ajan.sunucu --dene    # protokolsüz deneme: her aracı bir kez çağırır

Claude Code'a eklemek için (proje kökünden):

    claude mcp add medya-indeksi -- /Users/<kullanıcı>/dataclean_proje/gdelt/venv/bin/python -m ajan.sunucu

Claude Desktop için `claude_desktop_config.json`:

    {"mcpServers": {"medya-indeksi": {
        "command": "/Users/<kullanıcı>/dataclean_proje/gdelt/venv/bin/python",
        "args": ["-m", "ajan.sunucu"],
        "cwd": "/Users/<kullanıcı>/dataclean_proje/gdelt"}}}

Kimlik: BigQuery'ye gcloud ADC ile bağlanır (yerel arşiv varsa ondan okur, BigQuery'ye hiç gitmez).
Barındırma, API anahtarı veya hız sınırı YOKTUR: sunucu yalnızca bu bilgisayarda çalışır.

**Her araç yanıtı `as_of`, `yontem` ve `sinir` alanlarını taşır.** `sinir`, o rakamı okurken bilinmesi
gereken kapsam uyarısıdır ve cevaba taşınması beklenir — araç açıklamalarında da ayrıca yazılıdır,
çünkü modelin uyarıyı taşıyacağı teknik olarak garanti edilemez.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ajan import araclar  # noqa: E402

UYARI = ("Yanıttaki `sinir` alanını cevabına taşı: bu veri tam sayım değil, GDELT'in taradığı sitelerle "
         "sınırlı bir örneklemdir ve olayın kendisini değil habere konu olma yoğunluğunu ölçer.")

TANIM = """Medya İndeksi: 2015'ten bugüne Türkçe haberlerin arşivi (60+ milyon haber, 4.246 gün).

Bu sunucu MEDYANIN KENDİSİNİ ölçer: bir konunun hangi yayınlarda, ne sıklıkta, hangi çerçeveyle ele
alındığını. Olayların kendisini ölçmez. Bir konuda haber sayısının artması, o alanda daha çok şey
yaşandığı anlamına gelmez; daha çok yazıldığı anlamına gelir.

Hiçbir araç neden-sonuç ilişkisi kurmaz ve ham haber metni döndürmez (arşivde yoktur).
Kişiler hakkında profil çıkarmak için tasarlanmamıştır; araçlar çerçeve, yayın, olay ve kapsam
düzeyindedir.

Veri kaynağı: The GDELT Project (gdeltproject.org). Çıktıları paylaşırken GDELT'e atıf zorunludur."""


def _cikti(d: dict) -> str:
    return json.dumps(d, ensure_ascii=False, indent=1, default=str)


def kur():
    from mcp.server.mcpserver import MCPServer

    s = MCPServer(name="medya-indeksi", title="Medya İndeksi", instructions=TANIM)

    @s.tool(description="Bir kişi, kurum, yer veya konunun Türkçe haberlerde nasıl ele alındığı: "
                        "haber sayısı, aylık dağılım, en çok yazan siteler, çerçeve payları, ortalama ton, "
                        "kapsam değerlendirmesi ve ÖRNEK BAŞLIKLAR. Tarih verilmezse son 90 gün. "
                        "Arama ifadenin haberin ADRESİNDE, BAŞLIĞINDA veya kişi listesinde geçtiği haberleri "
                        "bulur; yalnızca haber gövdesinde geçen bir ad BULUNAMAZ (arşivde tam metin yoktur). "
                        "Sonucu yorumlarken `kapsam_duzeyi` alanına bak: 'Sınırlı' veya 'Yetersiz' ise "
                        "bunu cevabında söyle. " + UYARI)
    def konu_ara(ifade: str, baslangic: str | None = None, bitis: str | None = None) -> str:
        return _cikti(araclar.konu_ara(ifade, baslangic, bitis))

    @s.tool(description="Bir haftanın medya gündemi: haber hacmi, ortalama ton, çerçeve payları ve her "
                        "çerçevenin kendi önceki 52 haftasına göre olağandışı olup olmadığı. Sapan "
                        "çerçeveler için ÖRNEK BAŞLIKLAR da döner. "
                        "hafta: ISO etiketi (örn. 2026-W42); boş bırakılırsa arşivdeki son tam hafta. " + UYARI)
    def gundem_ozeti(hafta: str | None = None) -> str:
        return _cikti(araclar.gundem_ozeti(hafta))

    @s.tool(description="Bir çerçevenin günlük payının zaman serisi. Çerçeve adları gundem_ozeti "
                        "çıktısındadır (Ekonomi, Siyaset / seçim, Yargı / soruşturma, Afet / kaza, "
                        "Güvenlik / terör, Dış ilişkiler, Sağlık, Eğitim, Göç, Kültür / sanat / spor, "
                        "Anma / tören, Diğer). Tarihler YYYY-AA-GG. " + UYARI)
    def cerceve_serisi(cerceve: str, baslangic: str | None = None, bitis: str | None = None) -> str:
        return _cikti(araclar.cerceve_serisi(cerceve, baslangic, bitis))

    @s.tool(description="Bir yayının çerçeve dağılımı ve her çerçeveyi kilit yayınların geneline göre kaç "
                        "kat öne çıkardığı (1,0 ortalamadır). yayin: alan adı (örn. sozcu.com.tr); geçersiz "
                        "ad verilirse mevcut yayınlar listelenir. Yayınların HABER SAYILARI birbiriyle "
                        "karşılaştırılamaz, yalnızca grup içi paylar karşılaştırılabilir. Tarih verilmezse son 90 gün. " + UYARI)
    def yayin_profili(yayin: str, baslangic: str | None = None, bitis: str | None = None) -> str:
        return _cikti(araclar.yayin_profili(yayin, baslangic, bitis))

    @s.tool(description="Türkiye'de geçen olayları dünya medyası ne kadar yazdı, arşivde Türkçe haberi var "
                        "mı? 'Türkçe haberi yok' Türk medyasının hiç yazmadığı anlamına GELMEZ; arşiv bir "
                        "örneklemdir. min_kaynak: olayın sayılması için gereken en az farklı kaynak "
                        "(varsayılan 5; eşiksiz bakıldığında olayların dörtte üçü tek anılmalıdır). " + UYARI)
    def dunya_turk(min_kaynak: int = 5, limit: int = 20) -> str:
        return _cikti(araclar.dunya_turk(min_kaynak, limit))

    @s.tool(description="Bir dönemde arşivde hangi yayınlar var ve hacimleri ne? Bir sonuca varmadan önce "
                        "kapsamın yeterli olup olmadığını denetlemek için kullanılır. Tarihler YYYY-AA-GG. "
                        + UYARI)
    def kapsam(baslangic: str, bitis: str) -> str:
        return _cikti(araclar.kapsam(baslangic, bitis))

    return s


def dene() -> int:
    """Protokolsüz deneme: her aracı bir kez çağırır, zorunlu alanları denetler."""
    from datetime import date, timedelta
    bit = date.today().isoformat()
    bas = (date.today() - timedelta(days=30)).isoformat()
    cagrilar = [("konu_ara", {"ifade": "deprem", "baslangic": bas, "bitis": bit}),
                ("gundem_ozeti", {}),
                ("cerceve_serisi", {"cerceve": "Ekonomi"}),
                ("yayin_profili", {"yayin": "sozcu.com.tr"}),
                ("dunya_turk", {"limit": 3}),
                ("kapsam", {"baslangic": bas, "bitis": bit})]
    hata = 0
    for ad, kw in cagrilar:
        r = araclar.ARACLAR[ad](**kw)
        eksik = [k for k in ("as_of", "yontem", "sinir") if k not in r] if "hata" not in r else []
        durum = f"HATA: {r['hata']}" if "hata" in r else ("eksik alan: " + ", ".join(eksik) if eksik else "ok")
        hata += ("hata" in r) or bool(eksik)
        print(f"  {ad:<16} {len(json.dumps(r, ensure_ascii=False)) / 1024:>5.1f} KB  {durum}")
    print(f"\n{f'[OK] {len(cagrilar)} araç da çalışıyor' if not hata else f'[!] {hata} sorun'}")
    return hata


def main():
    if "--dene" in sys.argv:
        sys.exit(dene())
    kur().run(transport="stdio")


if __name__ == "__main__":
    main()
