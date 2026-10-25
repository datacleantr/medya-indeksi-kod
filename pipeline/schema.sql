-- gdelt_tr veri seti ve tabloları. Tekrar çalıştırılabilir (IF NOT EXISTS).
-- GDELT tabloları US bölgesinde olduğundan veri seti de US'te olmalı.

CREATE SCHEMA IF NOT EXISTS gdelt_tr
OPTIONS (location = 'US', description = 'GDELT Türkçe haber alt kümesi ve yardımcı tablolar');

-- GKG 2.1'den Türkçe haberler (srclc:tur) ve .tr alan adlı kaynaklar
CREATE TABLE IF NOT EXISTS gdelt_tr.articles (
  gkg_day DATE NOT NULL OPTIONS (description = 'GKG bölüm günü (UTC); yükleme ve silme bu güne göre yapılır'),
  GKGRECORDID STRING,
  pub_timestamp TIMESTAMP,
  SourceCommonName STRING,
  DocumentIdentifier STRING,
  PageTitle STRING,
  AvgTone FLOAT64,
  WordCount INT64,
  V2Tone STRING,
  Themes STRING,
  V2Persons STRING,
  Organizations STRING,
  V2Locations STRING,
  TranslationInfo STRING,
  Quotations STRING,
  AllNames STRING,
  GCAM STRING,
  loaded_at TIMESTAMP
)
PARTITION BY gkg_day
CLUSTER BY SourceCommonName
OPTIONS (description = 'GDELT GKG 2.1 Türkçe haberler');

-- Günlük toplam Türkçe haber sayısı: konuları "Türk medyasının binde kaçı" olarak normalize etmek için
CREATE TABLE IF NOT EXISTS gdelt_tr.daily_totals (
  day DATE NOT NULL,
  articles INT64,
  unique_urls INT64,
  sources INT64,
  avg_tone FLOAT64,
  words INT64,
  loaded_at TIMESTAMP
)
CLUSTER BY day;

-- Kaynak dizini: gün × site haber sayısı. Panelin "Veri kapsamı ve boşluklar" denetimi bir yayının seçilen
-- aralıkta arşivde olup olmadığını buradan okur (yerelde karşılığı data/archive/source_daily.parquet).
-- articles adımı her yüklemede aynı aralık için yeniden yazar; tamamı `deploy.py source-daily` ile yeniden kurulur.
CREATE TABLE IF NOT EXISTS gdelt_tr.source_daily (
  gkg_day DATE NOT NULL,
  site STRING,
  n INT64,
  loaded_at TIMESTAMP
)
PARTITION BY DATE_TRUNC(gkg_day, MONTH)
CLUSTER BY site
OPTIONS (description = 'Gün × site haber sayısı (gdelt_tr.articles özeti)');

-- Takip edilen kişi / konu tanımları
CREATE TABLE IF NOT EXISTS gdelt_tr.topics (
  topic_id STRING NOT NULL,
  name STRING,
  url_re STRING,
  title_re STRING,
  text_terms ARRAY<STRING> OPTIONS (description = 'Web NGrams ile haber gövdesinde aranacak kelimeler'),
  active BOOL,
  created_at TIMESTAMP
);

-- GQG: Türkçe haberlerdeki alıntılar (2020'den itibaren)
CREATE TABLE IF NOT EXISTS gdelt_tr.quotes (
  gqg_day DATE NOT NULL,
  date TIMESTAMP,
  url STRING,
  title STRING,
  quotes ARRAY<STRUCT<pre STRING, quote STRING, post STRING>>,
  loaded_at TIMESTAMP
)
PARTITION BY gqg_day;

-- GDG: Türkçe haber sayfalarının sonradan değişmesi / silinmesi
CREATE TABLE IF NOT EXISTS gdelt_tr.page_changes (
  check_day DATE NOT NULL,
  fetchdate_orig TIMESTAMP,
  fetchdate_check TIMESTAMP,
  status STRING,
  http_code INT64,
  page_url STRING,
  page_domain_root STRING,
  page_title STRING,
  title_new STRING,
  redirect_url STRING,
  num_changes INT64,
  perc_changedchars FLOAT64,
  changes ARRAY<STRUCT<`from` STRING, `to` STRING, from_range STRING, to_range STRING>>,
  loaded_at TIMESTAMP
)
PARTITION BY check_day
CLUSTER BY page_domain_root;

-- EventMentions: olayların Türkçe haberlerde geçişleri
CREATE TABLE IF NOT EXISTS gdelt_tr.event_mentions (
  mention_day DATE NOT NULL,
  GLOBALEVENTID INT64,
  EventTimeDate INT64,
  MentionTimeDate INT64,
  MentionType INT64,
  MentionSourceName STRING,
  MentionIdentifier STRING,
  SentenceID INT64,
  Actor1CharOffset INT64,
  Actor2CharOffset INT64,
  ActionCharOffset INT64,
  InRawText INT64,
  Confidence INT64,
  MentionDocLen INT64,
  MentionDocTone FLOAT64,
  loaded_at TIMESTAMP
)
PARTITION BY mention_day
CLUSTER BY MentionSourceName;

-- Events: aynı gün Türkçe haberlerde geçen olaylar (şema GDELT'ten kopyalanır, veri taranmaz)
CREATE TABLE IF NOT EXISTS gdelt_tr.events
PARTITION BY event_day
CLUSTER BY EventRootCode
AS
SELECT DATE(_PARTITIONTIME) AS event_day, *, CURRENT_TIMESTAMP() AS loaded_at
FROM `gdelt-bq.gdeltv2.events_partitioned`
WHERE FALSE;

-- E3: Türkiye'de geçen olaylar + dünya/Türkçe anılma özeti. "Bir olayı dünya ne kadar yazdı, Türk medyası
-- ne kadar?" Olay NEREDE geçtiğine göre seçilir (ActionGeo_CountryCode='TU'), hangi dilde yazıldığına göre
-- değil; böylece "dünya yazdı, Türk medyası yazmadı" olayları da görünür. Türkçelik ölçütü anılma URL'sinin
-- gdelt_tr.articles'ta bulunmasıdır. Ham anılmalar saklanmaz; Türkçe haberler event_mentions'tadır.
CREATE TABLE IF NOT EXISTS gdelt_tr.events_tu (
  event_day DATE NOT NULL,
  GLOBALEVENTID INT64,
  EventRootCode STRING,
  EventCode STRING,
  Actor1Name STRING,
  Actor2Name STRING,
  AvgTone FLOAT64,
  GoldsteinScale FLOAT64,
  NumMentions INT64 OPTIONS (description = "GDELT'in kendi anılma sayısı"),
  NumSources INT64,
  ActionGeo_FullName STRING,
  ActionGeo_ADM1Code STRING,
  toplam_anilma INT64 OPTIONS (description = 'Tüm dillerdeki anılma sayısı'),
  toplam_kaynak INT64 OPTIONS (description = 'Tüm dillerdeki tekil kaynak sayısı'),
  turkce_anilma INT64 OPTIONS (description = "URL'si gdelt_tr.articles'ta bulunan anılma sayısı"),
  turkce_kaynak INT64,
  loaded_at TIMESTAMP
)
PARTITION BY event_day
CLUSTER BY EventRootCode
OPTIONS (description = 'Türkiye olayları: dünya ve Türk medyası anılma karşılaştırması (E3)');
