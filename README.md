# Medya İndeksi

GDELT GKG 2.1 verisinden Türkçe haber arşivi kuran ve analiz eden Streamlit uygulaması.
Belirli bir kişi ya da konuyla ilgili haberleri BigQuery'den (veya yerel Parquet arşivinden)
çeker; çerçeve, ton, yayın çizgisi ve zaman serisi analizleri üretir.

**Dil:** Arayüz metinleri, kod yorumları ve commit mesajları Türkçe.

## Çalıştırma

```bash
python3.12 -m venv venv
venv/bin/pip install -r requirements.txt
venv/bin/streamlit run app.py
```

Sanal ortam: Python 3.12, pandas 3.0, Streamlit 1.64, Plotly 7.1.

Ek paket dosyaları ayrıdır ve `requirements.txt`'e eklenmez (yayın ortamı hafif kalsın):
`requirements-cases.txt` (vaka araçları), `requirements-llm.txt`, `requirements-mcp.txt`.

## Kimlik doğrulama

BigQuery erişimi iki yoldan olur:

1. **Yerel:** gcloud ADC — `gcloud auth application-default login`
2. **Yayın:** `.streamlit/secrets.toml` içindeki `[gcp_service_account]`

`bq_credentials()` secrets bulamazsa `None` döner ve Google istemcisi ADC'ye / servise bağlı
hesaba düşer. **Servis hesabı anahtarı asla repoya girmez** — yalnızca `.streamlit/secrets.toml`
(gitignore'da) veya Cloud Secrets.

## Yapı

| Yol | İçerik |
|---|---|
| `app.py` | Panelin tamamı. Yukarıdan aşağıya: sabitler → yardımcı fonksiyonlar → arayüz |
| `pipeline/` | BigQuery veri hattı: şema, yükleme adımları (`steps/*.sql`), dışa aktarma, özet katmanı, bülten |
| `cases/` | Vaka çalışması araçları: korpus, analiz, metin, kaynak kaydı, PDF rapor |
| `llm/` | LLM destekli başlık etiketleme: kod kitapları, örneklem, insan kodlama ekranı, bütçe defteri, doğruluk ölçümü |
| `ajan/` | MCP araçları (salt okunur, protokolden bağımsız) + stdio sunucusu |
| `vitrin/` | Statik tanıtım sayfası üreticisi |
| `yasal/` | Kullanım koşulları, gizlilik politikası, KVKK metni (**hukukçu kontrolünden geçmemiş taslak**) |
| `Dockerfile`, `dagit.sh` | Cloud Run dağıtımı |

### `app.py` içinde gezinme

Tüm kod tek dosyadadır. Önemli değişkenler:

- `df` — tüm veri · `scope_df` — filtreli, tekilleştirme öncesi · `filtered_df` — sekmelerin kullandığı
- `*_exploded_full` — index'i `df` satırına işaret eden patlatılmış liste kolonları

"Derin analiz" bölümü `DEEP_GROUPS` ile üç gruba ve `V_*` görünümlerine ayrılır. Her görünümün
gövdesi `if open_view == V_X: with deep_tabs[V_X]:` içindedir; **yalnızca açık görünüm hesaplanır.**
Yeni görünüm eklerken aynı kalıbı kullanın ve başka bir görünümde tanımlanan değişkene dayanmayın.

Kenar çubuğu süzgeçlerinin anahtarları `FILTER_KEY_PREFIX` (`f_`) ile başlar; `reset_filters`
bunları siler. Yeni süzgeç eklerken bu öneki kullanın.

## Veri hattı

Türkçe GDELT verisi `gdelt_tr` veri setinde tutulur (US bölgesi; GDELT tabloları orada).
Tüm tablolar gün bölümlüdür.

- `articles` — GKG Türkçe haberler + GCAM
- `daily_totals` — normalizasyon paydası
- `source_daily` — gün × site haber sayısı (kapsam denetimi)
- `quotes` (GQG), `page_changes` (GDG), `event_mentions`, `events`, `events_tu`
- `ozet_gunluk` — günlük özet katmanı (gün × çerçeve/tema × yayın)
- `extra_articles` — GDELT'te olmayan yayınların künyeleri
- `ad_sozlugu` — başlıklardan çıkarılmış kişi/kurum sözlüğü

Yükleme adımları `pipeline/steps/<tablo>.sql`'dir; aralığı sil + yaz yapar, her adım kendi
transaction'ındadır, **tekrar çalıştırmak güvenlidir**. `pipeline/deploy.py` bu adımlardan
günlük / elle aralık / geçmiş veri betikleri üretir.

```bash
venv/bin/python -m pipeline.deploy schema      # tabloları kur
venv/bin/python -m pipeline.deploy estimate    # dry run maliyet tahmini
venv/bin/python -m pipeline.deploy load        # yükle
venv/bin/python -m pipeline.deploy status
```

### Maliyet kuralları — bunlara uyun

- **Yüklemeden önce mutlaka dry run ile tahmin alın**, `maximum_bytes_billed` ile tavan koyun.
- **GDELT tablolarında her zaman bölüm filtresi kullanın.** Filtresiz sorgu terabaytlar tarar.
- Projenin o ayki kullanımını `INFORMATION_SCHEMA.JOBS_BY_PROJECT` ile kontrol etmeden
  "ücretsiz kotada" varsaymayın.
- Aynı tabloya eşzamanlı transaction çakışır; yükleme işlerini aynı anda çalıştırmayın.
- Panel aramaları **Storage Read API** kullanır ve sorgu kotasına sayılmaz; `BQ_MAX_GB` ve
  `BQ_DAILY_MAX_GB` ile tavanlıdır.

## Kurallar

**Streamlit 1.64**
- `use_container_width` yerine `width="stretch"`.
- Ağır hesaplamalar `@st.cache_data`; hashlenebilir argüman verin.
- Arama başına değişen argümanlı önbelleklere `max_entries`, büyük tablolara `ttl` verin
  (yayında bellek sınırlı, tüm oturumlar paylaşır).
- Arama sonucu oturumda `search_bundle` olarak tutulur; yeni bir tablo gerekiyorsa **pakete
  ekleyin**, her çizimde yeniden hesaplatmayın.
- Bağımsız ağ okumalarını `run_parallel` ile yapın (içinde `st.*` çağırmayın).

**pandas 3**
- Frekans kodlarında `"M"` yerine `"ME"`. `is_datetime64tz_dtype` kaldırıldı.

**Türkçe metin**
- `fold_tr` (aksan kaldırma) ve `tr_lower` (İ/I doğru küçültme) kullanın. **Düz `.lower()`
  kullanmayın** — Türkçede yanlış sonuç verir.
- Zaman damgaları `preprocess` sonrası tz'siz Türkiye saatidir; yeniden UTC varsaymayın.

**Arama kalıpları** (`topic_patterns`) RE2 sözdizimindedir; hem BigQuery hem DuckDB'de aynı
kalıp çalışır.
- Her kalıp kelime başı sınırı ister. **`.{1,8}` gibi joker kullanmayın** — kısa kelimelerde
  yüz binlerce yanlış eşleşme verir.
- Başlıkta Türkçe harfler asla düz gelmez; `TR_TITLE_VARIANTS` karşılıklarıyla eşleşir.
- **Kalıp değişikliğini yerel arşivde eski ve yeni sonuçları karşılaştırarak doğrulayın.**

**Görsel dil** ("basılı dosya")
- Renkleri `MK` sözlüğünden alın, **sabit hex yazmayın**. Metin renkleri kâğıt zeminde
  WCAG AA'yı (4,5:1) geçer.
- Plotly'de `template=PLOTLY_TEMPLATE` kullanın (`plotly_dark` kullanmayın).
- Simgeler emoji değil Material Symbols (`:material/download:`).
- `MOBILE_MAX_PX` (640) altı telefon sayılır. Görsel değişiklikleri telefon (390) ve
  masaüstü (1400) genişliğinde doğrulayın.

**Her grafiğin altında** `data_note` ile veri notu, **her kartta** `evidence_table` ile
"Kanıtı göster" olmalı.

**Konu bağımsızlığı:** Kodda belirli kişi adı sabit olarak yazılmaz. Konu adı
`detect_topic_words` ile başlıklardan bulunur. Örneklerde gerçek kişi adı kullanmayın;
yer tutucu veya yer adı kullanın.

**İsteğe bağlı kolonlar** (`OPTIONAL_COLUMNS`) eksikse `preprocess` boş string ile doldurur.
Yeni özellikler eski CSV'lerde de çökmeden çalışmalı.

## Kaynak gösterimi — zorunlu

GDELT kullanım koşulları: veri ticari dahil serbesttir, **ancak sunulduğu her yerde GDELT'e
atıf ve https://www.gdeltproject.org/ bağlantısı zorunludur.** Panelde `GDELT_ATTRIBUTION`
başlığın altında ve sayfa sonunda gösterilir. **Yeni ekran, rapor veya dışa aktarma eklerseniz
atıf da ekleyin.**

## LLM katmanı

İlke: **LLM ölçmez, anlatır.** Her rakam modelden bağımsız hesaplanır; model yalnızca
hesaplanmış bir olgu paketini cümleye çevirir, paketin dışına çıkamaz, nedensellik kuramaz.

Her Claude çağrısı `llm/butce.py` üzerinden geçer: çağrıdan önce en kötü durum maliyeti
hesaplanır, aylık/çalışma tavanını aşacaksa **çağrı yapılmaz**; sonra gerçek maliyet deftere
yazılır. İki bariyer vardır (aylık tavan ve kaçak döngüye karşı çalışma tavanı) ve ikisi de
ortam değişkeniyle değiştirilebilir.

Başlık etiketleme akışı ve kod kitabı biçimi için `llm/kodkitabi/ornek.toml` iskeletine bakın.
Doğruluk ölçümü `llm/olcum.py` ile yapılır; yerel bir Ollama modeli veya Claude aynı istemle
çalıştırılabilir. **Altın set insan eliyle kodlanır** — LLM altın seti kodlamaz, yoksa doğruluk
ölçülmüş olmaz.

## Test

Otomatik test paketi yoktur. Değişiklikten sonra uygulamayı çalıştırın:

```python
from streamlit.testing.v1 import AppTest
at = AppTest.from_file("app.py", default_timeout=200).run()
assert not at.exception
```

- `st.file_uploader`'ı örnek bir CSV döndürecek şekilde monkeypatch edin.
- Widget'ları index ile değil **label ile** bulun. `st.pills` / `st.segmented_control`
  `at.get("button_group")` ile bulunur.
- Arşivsiz (yayın) davranışı `GDELT_ARCHIVE=/olmayan/yol` ile test edin.
- En azından eski şemalı (yalnızca zorunlu kolonlu) bir CSV ile de deneyin.

## Dağıtım

```bash
bash dagit.sh
```

Cloud Run'a dağıtır. İmaja yalnızca panelin ihtiyacı olanlar girer (`app.py`,
`.streamlit/config.toml`, `assets/`, `yasal/`, iki pipeline modülü); `llm/` ve `ajan/` girmez.

- **`--session-affinity` şarttır:** Streamlit oturumu sunucuda tutar; aynı kullanıcı farklı
  örneğe düşerse oturum kopar.
- **`--server.enableCORS=false` KULLANILMAZ.** Kapalıyken her kaynaktan WebSocket bağlantısı
  kabul edilir ve XSRF koruması bunu durdurmaz.
- Parolalı giriş `parola_kapisi()` ile yapılır. Parolanın kendisi hiçbir yerde saklanmaz;
  yalnızca SHA-256 özeti `PANEL_PAROLA_OZETI` ortam değişkenindedir. Karşılaştırma
  `hmac.compare_digest` ile sabit zamanlıdır. Kapı **veri okunmadan öncedir.**
