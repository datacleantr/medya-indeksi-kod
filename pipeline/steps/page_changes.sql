-- GDG -> page_changes, [s, e] aralığı için (GDG 2018'den itibaren vardır).
BEGIN TRANSACTION;

DELETE FROM gdelt_tr.page_changes WHERE check_day BETWEEN s AND e;
INSERT INTO gdelt_tr.page_changes
SELECT DATE(fetchdate_check), fetchdate_orig, fetchdate_check, status, http_code, page_url, page_domain_root,
       page_title, title_new, redirect_url, num_changes, perc_changedchars, changes, CURRENT_TIMESTAMP()
FROM `gdelt-bq.gdeltv2.gdg_partitioned`
WHERE fetchdate_check >= TIMESTAMP(s) AND fetchdate_check < TIMESTAMP(DATE_ADD(e, INTERVAL 1 DAY))
  AND page_lang = 'tr';

COMMIT TRANSACTION;
