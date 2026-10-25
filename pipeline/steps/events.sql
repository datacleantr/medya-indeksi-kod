-- EventMentions -> event_mentions ve aynı aralıkta Türkçe haberlerde geçen olaylar -> events, [s, e] aralığı için.
BEGIN TRANSACTION;

DELETE FROM gdelt_tr.event_mentions WHERE mention_day BETWEEN s AND e;
INSERT INTO gdelt_tr.event_mentions
SELECT DATE(_PARTITIONTIME), GLOBALEVENTID, EventTimeDate, MentionTimeDate, MentionType, MentionSourceName,
       MentionIdentifier, SentenceID, Actor1CharOffset, Actor2CharOffset, ActionCharOffset, InRawText,
       Confidence, MentionDocLen, MentionDocTone, CURRENT_TIMESTAMP()
FROM `gdelt-bq.gdeltv2.eventmentions_partitioned`
WHERE _PARTITIONTIME BETWEEN TIMESTAMP(s) AND TIMESTAMP(e)
  AND MentionDocTranslationInfo LIKE 'srclc:tur%';

DELETE FROM gdelt_tr.events WHERE event_day BETWEEN s AND e;
INSERT INTO gdelt_tr.events
SELECT DATE(_PARTITIONTIME), *, CURRENT_TIMESTAMP()
FROM `gdelt-bq.gdeltv2.events_partitioned`
WHERE _PARTITIONTIME BETWEEN TIMESTAMP(s) AND TIMESTAMP(e)
  AND GLOBALEVENTID IN (SELECT GLOBALEVENTID FROM gdelt_tr.event_mentions WHERE mention_day BETWEEN s AND e);

COMMIT TRANSACTION;
