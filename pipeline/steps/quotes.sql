-- GQG -> quotes, [s, e] aralığı için (GQG 2020'den itibaren vardır).
BEGIN TRANSACTION;

DELETE FROM gdelt_tr.quotes WHERE gqg_day BETWEEN s AND e;
INSERT INTO gdelt_tr.quotes
SELECT DATE(date), date, url, title, quotes, CURRENT_TIMESTAMP()
FROM `gdelt-bq.gdeltv2.gqg`
WHERE date >= TIMESTAMP(s) AND date < TIMESTAMP(DATE_ADD(e, INTERVAL 1 DAY))
  AND lang = 'TURKISH';

COMMIT TRANSACTION;
