-- GKG -> articles, daily_totals ve source_daily, [s, e] aralığı için.
-- GCAM yalnızca gcam_since ve sonrası için taranır; öncesinde kolon NULL kalır (GKG taraması ~5 kat azalır).
-- Aralık silinip yeniden yazılır; kendi transaction'ında çalıştığı için hata olursa tablo değişmez.
BEGIN TRANSACTION;

DELETE FROM gdelt_tr.articles WHERE gkg_day BETWEEN s AND e;

INSERT INTO gdelt_tr.articles
SELECT
  DATE(_PARTITIONTIME),
  GKGRECORDID,
  SAFE.PARSE_TIMESTAMP('%Y%m%d%H%M%S', CAST(DATE AS STRING)),
  SourceCommonName,
  DocumentIdentifier,
  REGEXP_EXTRACT(Extras, r'<PAGE_TITLE>(.*?)</PAGE_TITLE>'),
  SAFE_CAST(SPLIT(V2Tone, ',')[SAFE_OFFSET(0)] AS FLOAT64),
  SAFE_CAST(SPLIT(V2Tone, ',')[SAFE_OFFSET(6)] AS INT64),
  V2Tone, Themes, V2Persons, Organizations, V2Locations, TranslationInfo, Quotations, AllNames, GCAM,
  CURRENT_TIMESTAMP()
FROM `gdelt-bq.gdeltv2.gkg_partitioned`
WHERE _PARTITIONTIME BETWEEN TIMESTAMP(GREATEST(s, gcam_since)) AND TIMESTAMP(e)
  AND (TranslationInfo LIKE 'srclc:tur%' OR ENDS_WITH(SourceCommonName, '.tr'));

-- gcam_since öncesi: GCAM kolonuna hiç dokunulmaz, böylece taranmaz
INSERT INTO gdelt_tr.articles
SELECT
  DATE(_PARTITIONTIME),
  GKGRECORDID,
  SAFE.PARSE_TIMESTAMP('%Y%m%d%H%M%S', CAST(DATE AS STRING)),
  SourceCommonName,
  DocumentIdentifier,
  REGEXP_EXTRACT(Extras, r'<PAGE_TITLE>(.*?)</PAGE_TITLE>'),
  SAFE_CAST(SPLIT(V2Tone, ',')[SAFE_OFFSET(0)] AS FLOAT64),
  SAFE_CAST(SPLIT(V2Tone, ',')[SAFE_OFFSET(6)] AS INT64),
  V2Tone, Themes, V2Persons, Organizations, V2Locations, TranslationInfo, Quotations, AllNames, CAST(NULL AS STRING),
  CURRENT_TIMESTAMP()
FROM `gdelt-bq.gdeltv2.gkg_partitioned`
WHERE _PARTITIONTIME BETWEEN TIMESTAMP(s) AND TIMESTAMP(LEAST(e, DATE_SUB(gcam_since, INTERVAL 1 DAY)))
  AND (TranslationInfo LIKE 'srclc:tur%' OR ENDS_WITH(SourceCommonName, '.tr'));

-- Günlük toplamlar (yalnız Türkçe dilindeki haberler; kendi tablomuzdan, GDELT yeniden taranmaz)
DELETE FROM gdelt_tr.daily_totals WHERE day BETWEEN s AND e;
INSERT INTO gdelt_tr.daily_totals
SELECT gkg_day, COUNT(*), COUNT(DISTINCT DocumentIdentifier), COUNT(DISTINCT SourceCommonName),
       AVG(AvgTone), SUM(WordCount), CURRENT_TIMESTAMP()
FROM gdelt_tr.articles
WHERE gkg_day BETWEEN s AND e AND TranslationInfo LIKE 'srclc:tur%'
GROUP BY gkg_day;

-- Kaynak dizini (gün × site; tüm haberler, dil süzgeci yok: yerel dizinle aynı tanım)
DELETE FROM gdelt_tr.source_daily WHERE gkg_day BETWEEN s AND e;
INSERT INTO gdelt_tr.source_daily
SELECT gkg_day, SourceCommonName, COUNT(*), CURRENT_TIMESTAMP()
FROM gdelt_tr.articles
WHERE gkg_day BETWEEN s AND e
GROUP BY gkg_day, SourceCommonName;

COMMIT TRANSACTION;
