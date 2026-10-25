# Kişisel Verilerin Korunması — Aydınlatma Metni

**Veri sorumlusu:** [[VERİ SORUMLUSU]]
**Adres:** [[ADRES]]
**E-posta:** [[İLETİŞİM E-POSTA]]
**KEP:** [[KEP ADRESİ]]
**Yürürlük tarihi:** [[YÜRÜRLÜK TARİHİ]]

Bu metin, **Medya İndeksi** (bundan sonra "Panel") kapsamında kişisel verilerin nasıl işlendiğini 6698 sayılı Kişisel Verilerin Korunması Kanunu'nun (KVKK) 10. maddesi uyarınca açıklar.

> *Taslak notu: Bu metindeki kanun maddesi atıfları bilgilendirme amaçlıdır ve hukuki görüş değildir; yayımlanmadan önce hukukçu kontrolünden geçirilmelidir. Bkz. `OKUBENI.md`.*

---

## 1. Panel ne yapar, neyi yapmaz

Panel, **GDELT Project** adlı açık veri kaynağından alınan Türkçe haber **künyelerini** (başlık, haber adresi, yayın tarihi, haber sitesi) ve bu künyelerden türetilmiş sayısal alanları (duygu tonu, konu kodları, haberde geçen ad listeleri) analiz eder.

Panelin amacı **medyanın kendisini ölçmektir**: bir konunun hangi yayınlarda, ne sıklıkta, hangi dille ele alındığını göstermek. Kişiler hakkında değerlendirme veya profil üretmek **amacı dışındadır**.

Panel:

- Haberlerin **tam metnini saklamaz**; GDELT zaten saklamaz, Panel de ayrıca toplamaz.
- Haber içeriğini **yeniden yayımlamaz**; yalnızca başlık ve kaynağa bağlantı gösterir.
- Kişiler hakkında **profil, puan veya sınıflandırma üretmez**.
- **Otomatik karar** vermez; kimse hakkında hukuki sonuç doğuran bir işlem yapmaz.

## 2. İşlenen kişisel veriler ve kaynakları

### 2.1. Haberlerde adı geçen kişilere ait veriler

| Veri | Kaynak |
|---|---|
| Ad ve soyad | Haber başlıkları ve GDELT'in haber metninden çıkardığı ad listeleri |
| Kurum/görev bilgisi | Aynı |
| Haberle ilişkilendirilen yer bilgisi | GDELT konum alanları |
| Haberin tarihi, adresi ve yayımlandığı site | GDELT künye alanları |

Bu veriler **doğrudan ilgili kişiden toplanmaz**; kamuya açık olarak yayımlanmış haber içeriklerinden otomatik olarak türetilir.

**Hukuki sebep:** Bu işleme, KVKK'nın kişisel verilerin **ifade özgürlüğü kapsamında ve bilimsel/araştırma amaçlarıyla** işlenmesine ilişkin istisnası ile **veri sorumlusunun meşru menfaati** hukuki sebebine dayandırılmaktadır. İşleme, ilgili kişilerin temel hak ve özgürlüklerine zarar vermemek kaydıyla, yalnızca medya davranışının toplu (agregat) düzeyde ölçülmesi amacıyla yapılır.

**Sınırlar — taahhüdümüz:**

- Analizler **kamusal aktörler ve medya kuruluşları** düzeyinde yapılır.
- **Özel kişiler hakkında profil çıkarılmaz.**
- **Mağdur ve çocuk adları raporlarda yer almaz.**
- Bir kişiye ait verilerin tek başına derlenip o kişi hakkında bir dosya oluşturulması Panelin amacı dışındadır.

> **Önemli uyarı:** GDELT, Türkçe haberleri İngilizceye makine çevirisiyle çevirerek işler. Bu nedenle ad, kurum ve yer bilgileri **hatalı olabilir**: bir kişinin adı başka bir kelimeye dönüşebilir, bir soyadı yer adı sanılabilir. Panel bu sınırı her ekranda belirtir. Verisinin hatalı işlendiğini düşünen herkes 6. bölümdeki haklarını kullanabilir.

### 2.2. Panel kullanıcılarına ait veriler

| Veri | Neden | Hukuki sebep |
|---|---|---|
| E-posta adresi | Panele erişim yetkisi vermek için | Sözleşmenin kurulması/ifası ve meşru menfaat |
| Teknik kayıtlar (IP, tarayıcı, erişim zamanı) | Güvenlik ve hata takibi; barındırma sağlayıcısı tarafından tutulur | Meşru menfaat ve hukuki yükümlülük |
| Arama ifadeleri | Panelin çalışması için anlık olarak işlenir | Hizmetin ifası |

Panel **hesap sistemi, çerez tabanlı takip veya analitik yazılım kullanmaz**. Erişim yetkisi, barındırma sağlayıcısının (Streamlit Community Cloud) paylaşım ayarı üzerinden verilir; bu sağlayıcı kendi gizlilik politikası kapsamında teknik kayıt tutar.

### 2.3. Bülten aboneleri (planlanan)

Haftalık bülten e-posta ile gönderilmeye başlandığında, yalnızca **e-posta adresi** ve **abonelik tarihi** işlenir.

- Hukuki sebep: **açık rıza**.
- Rıza, her bültendeki "abonelikten çık" bağlantısıyla her zaman geri alınabilir.
- E-posta adresi üçüncü kişilerle paylaşılmaz ve reklam amacıyla kullanılmaz.
- Rıza geri alındığında adres kayıtlardan silinir.

## 3. İşleme amaçları

- Türkçe medyada bir konunun nasıl ele alındığını ölçmek ve görselleştirmek.
- Araştırma ve gazetecilik amaçlı raporlar üretmek.
- Panelin güvenliğini ve çalışırlığını sağlamak.
- (Rıza verilmişse) haftalık bülten göndermek.

## 4. Verilerin aktarılması

| Kime | Neden |
|---|---|
| Barındırma ve altyapı sağlayıcıları (Streamlit Community Cloud, Google Cloud / BigQuery) | Panelin çalışması ve verinin saklanması |
| Yapay zekâ sağlayıcısı (Anthropic) | Yalnızca kullanıcı bir özet talep ettiğinde ve yalnızca ekrandaki toplu rakamlar ile haber başlıkları gönderilir |
| Yetkili kamu kurum ve kuruluşları | Hukuki yükümlülük hâlinde |

Bu sağlayıcıların sunucuları **yurt dışında** bulunabilir. Veriler reklam, pazarlama veya üçüncü kişilere satış amacıyla **aktarılmaz**.

> *Hukukçuya not: Yurt dışına aktarım rejiminin (KVKK m.9) bu yapıya nasıl uygulanacağı teyit edilmelidir.*

## 5. Saklama süreleri

| Veri | Süre |
|---|---|
| Haber künyeleri ve türetilmiş alanlar | Arşiv niteliğinde olduğundan süresiz; kaynak veri (GDELT) kamuya açık kaldığı sürece |
| Bülten abone e-postası | Abonelik sürdüğü sürece; rıza geri alındığında silinir |
| Teknik kayıtlar | Barındırma sağlayıcısının politikası uyarınca |

## 6. İlgili kişinin hakları

KVKK'nın 11. maddesi uyarınca herkes, kendisiyle ilgili olarak:

- Kişisel verisinin işlenip işlenmediğini öğrenme, işlenmişse buna ilişkin bilgi talep etme,
- İşlenme amacını ve amacına uygun kullanılıp kullanılmadığını öğrenme,
- Yurt içinde veya yurt dışında aktarıldığı üçüncü kişileri bilme,
- **Eksik veya yanlış işlenmişse düzeltilmesini isteme**,
- Şartları oluştuğunda **silinmesini veya yok edilmesini isteme**,
- Düzeltme ve silme işlemlerinin verinin aktarıldığı üçüncü kişilere bildirilmesini isteme,
- İşlenen verilerin münhasıran otomatik sistemlerle analiz edilmesi suretiyle aleyhine bir sonuç çıkmasına itiraz etme,
- Kanuna aykırı işleme nedeniyle zarara uğraması hâlinde zararın giderilmesini talep etme

haklarına sahiptir.

**Başvuru:** Taleplerinizi [[İLETİŞİM E-POSTA]] adresine iletebilirsiniz. Başvurular en geç **30 gün** içinde sonuçlandırılır.

Başvurunuzda, hakkında işlem yapılmasını istediğiniz kaydın bulunabilmesi için **adınızı ve mümkünse ilgili haberin adresini** belirtmeniz süreci hızlandırır.

> **Düzeltme ve silme talepleri hakkında:** Panel, haberlerin kendisini barındırmaz; yalnızca kamuya açık haberlerin künyelerini indeksler. Bir haberin içeriğinin düzeltilmesi veya kaldırılması talebi, **haberi yayımlayan yayın kuruluşuna** yöneltilmelidir. Panel, kendi arşivindeki kaydı silebilir veya düzeltebilir; ancak kaynak haber yerinde kaldığı ve GDELT tarafından yeniden taranabildiği sürece kaydın tekrar oluşabileceğini belirtmek isteriz. Bu durumda kalıcı engelleme için kaydı dışlama listesine alırız.

## 7. Veri güvenliği

- Erişim yalnızca adı belirlenmiş kullanıcılara verilir.
- Altyapı erişim anahtarları şifreli ortamda tutulur, kaynak kodda yer almaz.
- Veri tabanı erişimi **salt okunur** bir hizmet hesabıyla yapılır.

## 8. Değişiklikler

Bu metin güncellendiğinde yeni sürüm bu adreste yayımlanır ve yürürlük tarihi değiştirilir. Önemli değişikliklerde kayıtlı kullanıcılar bilgilendirilir.
