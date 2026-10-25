# Metodoloji

Bu sayfa, Medya İndeksi'nin neyi nasıl ölçtüğünü ve **neyi ölçemediğini** ayrıntılı olarak anlatır.
Bir ölçüm aracının sınırlarını bilmeden sonuçlarını okumak yanıltıcıdır; bu yüzden sınırlar sayfanın
sonuna değil, yöntemin yanına yazılmıştır.

Son güncelleme: {{TARIH}} · Veri kapsamı: {{ILK_GUN}} – {{SON_GUN}}

---

## 1. Ne ölçülüyor

**Medya İndeksi medyanın kendisini ölçer.** Bir konunun hangi yayınlarda, ne sıklıkta, hangi dille ele
alındığını gösterir.

Ölçülen birim **habere konu olma yoğunluğudur**, olayın kendisi değil. Bu ayrım her çıktıda geçerlidir:

- Bir konuda haber sayısının artması, o alanda daha çok şey yaşandığı anlamına **gelmez**; daha çok
  yazıldığı anlamına gelir.
- Duygu tonu, haberin **dilini** ölçer; olayın iyi ya da kötü olduğunu göstermez.
- Araç hiçbir çıktısında **neden-sonuç ilişkisi kurmaz**. İki şeyin aynı dönemde artması, birinin
  diğerine yol açtığı anlamına gelmez.

## 2. Veri kaynakları

### 2.1. GDELT GKG 2.1 — birincil kaynak

[The GDELT Project](https://www.gdeltproject.org/) dünya genelinde haber sitelerini sürekli tarar ve
her haber için yapısal bir kayıt üretir. Türkçe haberler iki ölçütten biriyle seçilir: GDELT'in dil
alanı `srclc:tur` ise, veya haberin alan adı `.tr` ile bitiyorsa.

Her kayıtta bulunanlar: yayın tarihi ve saati, haber sitesi, haber adresi, başlık, duygu tonu
(`AvgTone`), tema kodları, haberde geçen kişi/kurum/yer listeleri, kelime sayısı.

**Haberin tam metni yoktur.** GDELT metni saklamaz; yalnızca metinden çıkardığı bilgileri verir.
Medya İndeksi de ayrıca tam metin toplamaz.

### 2.2. Arşiv dışı toplama — kapsam boşluğunu kapatmak

GDELT'in taramadığı yayınlar vardır ve bu boşluk konuya göre sonuçları saptırabilir. Bu yüzden
{{EK_YAYIN}} yayının künyeleri kendi **site haritalarından ve RSS akışlarından** ayrıca toplanır:
şu an {{EK_KUNYE}} künye.

Kurallar: tam metin toplanmaz, yalnızca künye (başlık, adres, tarih). `robots.txt`'ye uyulur. Site
başına saniyede en fazla bir istek gönderilir.

**Bu kayıtlar ton, tema ve kişi bilgisi içermediği için göstergelere, tona ve sekmelere karışmaz.**
Ayrı bir bölümde gösterilir ve kapsam denetiminde "yalnızca ek toplamada" olarak sayılır.

### 2.3. Yardımcı kaynaklar

| Kaynak | İçerik | Dönem |
|---|---|---|
| GQG (alıntılar) | Haberlerdeki alıntılar ve öncesi/sonrası bağlam | {{QUOTES_SINCE}}'den itibaren |
| GDG (sayfa değişiklikleri) | Yayından sonra değiştirilen veya silinen haber sayfaları | Son 12 ay |
| Events 2.0 (CAMEO) | Haberlerin anlattığı kodlanmış olaylar | Son 12 ay |

## 3. Veri hattı

Veri Google BigQuery'de gün bölümlü tablolarda tutulur. Günlük yükleme her gün 08:15 UTC'de
(Türkiye saatiyle 11:15) bir önceki günün verisini ekler. Her yükleme adımı kendi işlemindedir;
tekrar çalıştırmak güvenlidir.

Panelin üstündeki **veri tazeliği şeridi**, verinin son gününü ve kaç gün geride olduğunu her
ziyarette gösterir. Günlük yükleme bir önceki günü eklediği için 1 gün gecikme normaldir; daha
eskiyse şerit uyarı rengine döner.

## 4. Ölçüm yöntemleri

### 4.1. Arama

Arama, ifadenin haberin **adresinde, başlığında veya kişi listesinde** geçtiği haberleri bulur.
Yalnızca haber metninin içinde geçen bir ad bulunamaz — çünkü metin yoktur.

Kalıplar RE2 sözdizimindedir ve her kalıp **kelime başı sınırı** ister. Son kelime büyük harfliyse
veya dört harf ya da kısaysa kelime sonu da sınırlanır; böylece "Kars" araması "karşı" kelimesini
getirmez. Türkçe karakterler başlıkta hiçbir zaman düz gelmediği için her kalıp Türkçe varyantlarla
birlikte aranır.

{{BQ_MAX_ROWS}} haberden fazlasıyla eşleşen aramalar durdurulur.

### 4.2. Çerçeveler

Her habere, **başlığındaki** anahtar kelimelere göre bir veya birden fazla çerçeve atanır
(Siyaset, Yargı, Güvenlik, Ekonomi, Afet, Sağlık, Eğitim, Dış ilişkiler, Göç, Kültür, Anma).
Başlık yoksa haberin adresindeki kelimeler kullanılır.

Bilinmesi gerekenler:

- Bir haber **birden fazla çerçeveye** girebilir; bu yüzden çerçeve paylarının toplamı %100'ü aşar.
- Hiçbirine uymayan haber **"Diğer"** olur ve bu pay yüksektir — ölçümde haberlerin yarıdan fazlası.
  Çerçeve seti bilinçli olarak dar tutulmuş bir anahtar kelime listesidir; haberlerin tamamını
  sınıflandırmaz.
- Kullanıcı kendi çerçeve listesini yazabilir; çerçeveler koda gömülü değildir.

### 4.3. Yayın grupları

Kilit yayınlar, karşılaştırma yapılabilmesi için **yayın çizgisine göre** gruplanır: haber ajansları,
ana akım, hükümete yakın, muhalif/bağımsız, ulusalcı, Kürt siyasetine yakın, Gülen hareketine yakın,
yabancı yayınların Türkçe servisleri.

Bu gruplama hakkında bilinmesi gerekenler:

- **Gruplama bize aittir, GDELT'e değil.** Veri kaynağı yayınları sınıflandırmaz; bu ayrım aracın
  kendi editoryal kararıdır.
- Ölçüt, yayınların **kamuya açık yayın çizgisi ve sahiplik yapısıdır**; içerik analiziyle veya
  aracın kendi ölçümleriyle belirlenmemiştir. Bir yayının bir gruba konması, o yayın hakkında bir
  değer yargısı değil, karşılaştırma için kullanılan bir etikettir.
- Gruplar **sabit değildir**: yayın çizgisi değişen bir yayının grubu güncellenir.
- Çıktılarda **grup düzeyinde** rakam verilir; haftalık bültende hangi yayının hangi grupta olduğu
  gösterilmez.
- Araç, hiçbir grubu diğerine göre doğru, güvenilir veya nitelikli saymaz. Ölçülen tek şey, o
  grubun hangi konuyu ne kadar yazdığıdır.

Bu etiketlerin hassas olduğunun farkındayız. Yayımlanmalarının nedeni, bir yayın grubunun gündemini
diğerleriyle karşılaştırmanın başka bir yolu olmaması ve ölçütün gizli tutulmasının şeffaflığı daha
çok zedeleyecek olmasıdır.

### 4.4. Duygu tonu

Ton, GDELT'in `AvgTone` değeridir: haber metninin olumlu ve olumsuz kelime yoğunluğundan hesaplanan
bir sayı. Negatif değerler olumsuz dili gösterir.

**Ton, haberin İngilizce makine çevirisi üzerinden hesaplanır.** Tek tek haberlerin tonuna değil,
büyük kümelerin ortalamasına güvenilmelidir.

### 4.5. Kopya haber tespiti

İki haber şu durumlarda aynı kopya grubuna girer: (1) ton değerlerinin yedisi birebir aynıysa — bu
pratikte aynı metin demektir; veya (2) başlıkları yeterince benzer (Jaccard ≥ {{COPY_T}}), aralarında
en fazla {{COPY_W}} saat varsa ve kelime sayıları yakınsa. Bağlantılar geçişlidir; her grubun en erken
haberi "orijinal", diğerleri kopya sayılır.

### 4.6. Veri kapsamı denetimi

Her arama sonucu bir kapsam değerlendirmesi alır: **Yeterli**, **Sınırlı** veya **Yetersiz**.
Üç denetim düzeyi belirler:

1. **Arşiv öncesine atıf.** Başlıklar {{ILK_YIL}} öncesi yıllara sık atıf yapıyorsa, olayın kendisi
   arşivde yok demektir.
2. **Başlık boşluğu.** Başlıksız haber oranı yüksekse (GDELT {{TITLES_SINCE}} öncesinde başlık tutmaz)
   başlığa dayalı analizler zayıflar.
3. **GDELT boşlukları.** Aralıkta GDELT'in hiç veri toplamadığı günler varsa uyarılır:
   {{GAPS}}.

Aynı değerlendirme PDF raporun başında da yer alır.

### 4.7. Sapma ölçüsü

Bir çerçevenin payının olağandışı olup olmadığı, **aynı çerçevenin kendi önceki
{{TABAN_HAFTA}} haftasına** göre belirlenir ve yüzdelik dilim olarak verilir.

İki tasarım kararı ve gerekçeleri:

- **Taban kayan penceredir, tüm geçmiş değil.** Tüm geçmişe göre yüzdelik hesaplamak, yıllar içinde
  yapısal olarak yükselen bir çerçeveyi her hafta "sapma" diye işaretler. Ölçümde bir çerçeve tüm
  geçmişe göre son 12 haftanın sekizinde 90. yüzdeliğin üstündeydi; kayan tabanla dördünde.
- **Yüzde değişim değil yüzdelik dilim, üstelik mutlak bir tabanla.** Küçük tabanlı seriler küçük
  değişimlerde dev oranlar üretir. Haftalık haber sayısı {{MIN_HABER}}'in altındaki çerçeveler sapma
  olarak raporlanmaz.

### 4.8. Yapay zekâ özeti

Panelde istenirse, bir arama sonucunun rakamları bir dil modeliyle düzyazıya çevrilir.
**Model ölçmez, anlatır:** bütün sayılar modelden önce ve ondan bağımsız üretilir; model yalnızca
ekranda zaten görünen rakamları cümleye döker.

Üç kural isteme yazılıdır:

1. Ekrandaki olgu paketinin dışında hiçbir bilgi kullanılamaz; model başka bir kaynağa bakmaz.
2. **Neden-sonuç kurulamaz.**
3. Kapsam "Sınırlı" veya "Yetersiz" ise ilk paragrafta söylenir.

Her paragraf **veri** (rakamların aktarımı) veya **yorum** (rakamlar arasında bağ kuran cümle)
olarak etiketlenir.

## 5. Bilinen sınırlar

Bu bölüm aracın zayıf noktalarının eksiksiz listesidir.

| Sınır | Ne anlama gelir |
|---|---|
| **Tam sayım değil, örneklem** | Kapsam GDELT'in taradığı sitelerle sınırlıdır. Her haber ve her site veriye girmez. |
| **Makine çevirisi hataları** | Kişi, kurum ve yer adları bozulabilir: bir ad başka kelimeye dönüşebilir, bir soyadı yer adı sanılabilir. Tek tek kayıtlara değil, genel eğilimlere bakılmalıdır. |
| **Tam metin yok** | Yalnızca künye ve türetilmiş alanlar vardır. Metin içi analiz yapılamaz. |
| **"Diğer" payı yüksek** | Haberlerin yarıdan fazlası hiçbir çerçeveye uymaz. Çerçeve payları bu artık kümeyi de içeren toplamın içindedir. |
| **{{TITLES_SINCE}} öncesi başlık yok** | O dönemde çerçeveler adresten atanır ve daha zayıftır. Uzun dönem karşılaştırmalarında bu kırılma vardır. |
| **Yıllar arası kapsam farkı** | GDELT'in taradığı site sayısı yıllara göre değişir. Ham haber sayılarını yıllar arasında karşılaştırırken dikkat gerekir; bu yüzden paylar kullanılır. |
| **Olay kodlaması seyrek** | Türkçe haberlerin yalnızca yaklaşık altıda biri kodlanmış bir olaya bağlanır; ekonomi haberlerinde bu oran sıfıra yakındır. Olay görünümü eşik aşılmadan gösterilmez. |
| **Veri bir gün geriden** | Anlık değildir. |
| **GDELT boşlukları** | {{GAPS}} tarihlerinde veri yoktur. |

## 6. Tekrarlanabilirlik

- Her grafiğin altında, o grafiğin dayandığı veriyi açıklayan bir **veri notu** vardır.
- Her kartta **"Kanıtı göster"** ile kartın dayandığı haberlerin listesi açılır.
- Sonuçlar CSV olarak indirilebilir; PDF raporda ekrandakiyle **aynı rakamlar** kullanılır (ikisi
  aynı hesabı çağırır).
- Çerçeve seti, kopya tespiti eşikleri ve tarih aralığı kullanıcı tarafından değiştirilebilir ve
  raporda hangi ayarların kullanıldığı yazar.

## 7. Etik çerçeve

- Analizler **kamusal aktörler ve medya kuruluşları** düzeyinde yapılır.
- **Özel kişiler hakkında profil çıkarılmaz**; mağdur ve çocuk adları raporlanmaz.
- Haberlerin tam metni saklanmaz ve yeniden yayımlanmaz.
- "Koordinasyon", "sansür" gibi sonuçlar **gösterge** olarak sunulur, kanıt olarak değil. Her
  göstergenin arkasında ham kayıtlar gösterilir.

## 8. Kaynak gösterimi

Veri kaynağı [The GDELT Project](https://www.gdeltproject.org/)'tir (GKG 2.1, GQG, GDG, Events 2.0).
GDELT verisi açık ve ücretsizdir; Medya İndeksi GDELT tarafından geliştirilmemiştir. Çıktılar
paylaşılırken hem Medya İndeksi'ne hem GDELT'e atıf yapılmalıdır.
