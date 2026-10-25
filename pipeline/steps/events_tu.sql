-- Türkiye'de geçen olaylar + dünya/Türkçe anılma özeti -> events_tu, [s, e] aralığı için.
--
-- E3 "Dünya ve Türk medyası": bir olayı dünya medyası ne kadar yazdı, Türk medyası ne kadar?
-- Olayın kendisi ActionGeo_CountryCode = 'TU' ile seçilir; Türkçe olup olmadığına değil, NEREDE
-- geçtiğine bakılır. Böylece "dünya yazdı, Türk medyası yazmadı" olayları da tabloya girer.
--
-- Türkçelik ölçütü: anılmanın URL'si kendi arşivimizde (gdelt_tr.articles) var mı?
-- MentionDocTranslationInfo KULLANILMAZ: 2015'ten beri 342 GB tarar ve bu eşleşmeye göre
-- fazladan bir şey kazandırmaz (ölçüm 23.10.2026, EVENTS_DEGERLENDIRME.md §7).
--
-- Ham anılmalar indirilmez; olay başına özet tutulur. Türkçe haberlerin kendisi zaten
-- gdelt_tr.event_mentions'tadır (kanıt listesi oradan gelir).
BEGIN TRANSACTION;

DELETE FROM gdelt_tr.events_tu WHERE event_day BETWEEN s AND e;

INSERT INTO gdelt_tr.events_tu
WITH olay AS (
  SELECT DATE(_PARTITIONTIME) AS event_day, GLOBALEVENTID, EventRootCode, EventCode,
         Actor1Name, Actor2Name, AvgTone, GoldsteinScale, NumMentions, NumSources,
         ActionGeo_FullName, ActionGeo_ADM1Code
  FROM `gdelt-bq.gdeltv2.events_partitioned`
  WHERE _PARTITIONTIME BETWEEN TIMESTAMP(s) AND TIMESTAMP(e)
    AND ActionGeo_CountryCode = 'TU'
),
-- Anılmalar olaydan günler sonra da gelebilir; bu yüzden aralık sonundan sonrası da taranır
anilma AS (
  SELECT GLOBALEVENTID, MentionIdentifier, MentionSourceName
  FROM `gdelt-bq.gdeltv2.eventmentions_partitioned`
  WHERE _PARTITIONTIME BETWEEN TIMESTAMP(s) AND TIMESTAMP(DATE_ADD(e, INTERVAL 7 DAY))
),
tr AS (
  SELECT DISTINCT DocumentIdentifier
  FROM gdelt_tr.articles
  WHERE gkg_day BETWEEN s AND DATE_ADD(e, INTERVAL 7 DAY)
)
SELECT o.event_day, o.GLOBALEVENTID, o.EventRootCode, o.EventCode, o.Actor1Name, o.Actor2Name,
       o.AvgTone, o.GoldsteinScale, o.NumMentions, o.NumSources,
       o.ActionGeo_FullName, o.ActionGeo_ADM1Code,
       COUNT(a.MentionIdentifier) AS toplam_anilma,
       COUNT(DISTINCT a.MentionSourceName) AS toplam_kaynak,
       COUNTIF(t.DocumentIdentifier IS NOT NULL) AS turkce_anilma,
       COUNT(DISTINCT IF(t.DocumentIdentifier IS NOT NULL, a.MentionSourceName, NULL)) AS turkce_kaynak,
       CURRENT_TIMESTAMP()
FROM olay o
LEFT JOIN anilma a USING (GLOBALEVENTID)
LEFT JOIN tr t ON t.DocumentIdentifier = a.MentionIdentifier
GROUP BY o.event_day, o.GLOBALEVENTID, o.EventRootCode, o.EventCode, o.Actor1Name, o.Actor2Name,
         o.AvgTone, o.GoldsteinScale, o.NumMentions, o.NumSources, o.ActionGeo_FullName,
         o.ActionGeo_ADM1Code;

COMMIT TRANSACTION;
