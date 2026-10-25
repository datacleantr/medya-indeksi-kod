# Vitrin (T3) — tanıtım sayfası

Herkese açık tanıtım sayfası. `vitrin/olustur.py` üretir, çıktı `vitrin/site/` klasörüdür.

```bash
venv/bin/python vitrin/olustur.py        # üret
venv/bin/python vitrin/olustur.py --ac   # üret ve tarayıcıda aç
```

## Neden üretilen, elle yazılan değil

Sayfadaki **her rakam arşivden okunur**: haber sayısı, gün sayısı, arşiv dışı künye sayısı, kilit yayın sayısı
ve haftalık bülten rakamları. Hiçbiri HTML'e elle yazılmaz. Arşiv güncellenince sayfa yeniden üretilir ve
rakamlar kendiliğinden güncellenir.

Bu, bir rakip incelemesinden çıkan dersin uygulamasıdır: oradaki "canlı"
ısı haritası `Math.random()` ile çiziliyordu ve ülke yüzdeleri HTML'e elle yazılmıştı.

## Sayfalar

| Sayfa | İçerik |
|---|---|
| `index.html` | Tanıtım: rakamlar, ne ölçer/ölçmez, nasıl çalışır, sınırlar, haftalık bülten, bekleme listesi |
| `metodoloji.html` | **P2** — ayrıntılı yöntem: kaynaklar, veri hattı, ölçüm yöntemleri, bilinen sınırlar, tekrarlanabilirlik, etik |
| `kullanim-kosullari.html`, `gizlilik-politikasi.html`, `kvkk-aydinlatma.html` | Yalnızca yasal metinler doldurulmuşsa üretilir |

Metodoloji metninin kaynağı `vitrin/metodoloji.md`'dir. İçindeki `{{token}}`'lar `app.py` ve
`pipeline/sapma.py` sabitlerinden **koddan okunarak** doldurulur; eşik değiştiğinde sayfa kendiliğinden
güncellenir. Doldurulmamış token kalırsa üretici durur.

## Sayfanın kuralları

| Kural | Nasıl uygulanır |
|---|---|
| Sahte "canlı" gösterge yok | Her rakam veriden gelir, dönemi yazar |
| Sınırlar gizlenmez | "Sınırlar" bölümü sayfanın en uzun bölümüdür ve öne çıkar |
| Yarım hukuki metin yayımlanmaz | `yasal/` metinlerinde yer tutucu (`[[`) kaldıkça sayfaları ve alt bilgi bağlantıları üretilmez |
| E-posta toplanmaz | Bekleme listesi bölümü etkin değildir; nedeni sayfada yazılıdır (KVKK) |
| GDELT atfı | Alt bilgide, her sayfada |

## Yayımlama (GitHub Pages)

Ana repo **gizli** kalır; vitrin **ayrı ve herkese açık** bir repoya konur. Tek komutla:

```bash
gh auth login                      # bir kez, etkileşimli
venv/bin/python vitrin/olustur.py  # sayfayı üret
bash vitrin/yayinla.sh             # repoyu oluştur + push + Pages'i aç
```

`yayinla.sh` tekrar çalıştırılabilir: repo varsa yeniden oluşturmaz, Pages açıksa dokunmaz,
değişiklik yoksa boş commit atmaz. Repo adı varsayılan `medya-indeksi`; değiştirmek için
`REPO=baska-ad bash vitrin/yayinla.sh`.

Adres: `https://<kullanıcı>.github.io/medya-indeksi/`

`.nojekyll` dosyası üretilir; GitHub Pages'in dosyaları olduğu gibi sunması için gereklidir.

## Yayımlamadan önce kontrol listesi

- [ ] `pipeline/ozet.py kur` ve `pipeline/bulten.py` çalıştırıldı mı (rakamlar güncel mi)?
- [ ] Bültende kişi adı veya hassas içerik var mı? (Bülten yalnızca çerçeve ve yayın düzeyindedir; başlık içermez.)
- [ ] Yasal metinler doldurulup hukukçu kontrolünden geçti mi? Geçmediyse sayfa yine yayımlanabilir, yalnızca
      yasal bağlantılar çıkmaz — ama **bekleme listesi de açılmaz**.
- [ ] Panelin adresi sayfaya eklenecek mi? (Şu an eklenmedi: panel test kullanıcılarıyla sınırlı.)
- [ ] **Bültendeki yayın çizgisi adları herkese açık olacak.** `KEY_OUTLETS` grupları siyasi nitelemelerdir
      ("Hükümete yakın", "Gülen hareketine yakın" gibi). Bülten PDF'i grup adlarını gösterir (hangi yayının
      hangi grupta olduğunu göstermez). Türkiye bağlamında bu adlar hassastır; yayımlamadan önce karar
      verilmelidir. Seçenekler: olduğu gibi bırakmak · imza bölümünü herkese açık bültenden çıkarmak ·
      herkese açık çıktıda nötr adlar kullanmak.

## Bilinçli olarak eklenmeyenler

- **Panele bağlantı:** Panel şu an yalnızca adı belirlenmiş kullanıcılara açık. Vitrinden herkese açık bir
  bağlantı vermek erken; karar verilince `olustur.py` içindeki başlık bölümüne eklenir.
- **E-posta formu:** `P3` bitmeden açılmaz.
- **Fiyat bilgisi:** Ticari doğrulama görüşmeleri (`T2`) sonuçlanmadan fiyat yazılmaz.
