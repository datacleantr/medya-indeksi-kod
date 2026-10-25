"""gdelt_tr BigQuery veri hattını kurar ve çalıştırır.

Kullanım (proje kökünden):
  venv/bin/python pipeline/deploy.py schema                          # veri seti ve tabloları oluşturur
  venv/bin/python pipeline/deploy.py estimate 2026-09-01 2026-09-27  # aralığın tarama maliyeti (ücretsiz dry run)
  venv/bin/python pipeline/deploy.py load 2026-09-27 2026-09-27      # aralığı elle (yeniden) yükler
  venv/bin/python pipeline/deploy.py backfill-step                   # geçmişten her tablo için bir parça yükler
  venv/bin/python pipeline/deploy.py status                          # tabloların doluluk durumu
  venv/bin/python pipeline/deploy.py source-daily                    # kaynak dizinini (source_daily) baştan kurar
  venv/bin/python pipeline/deploy.py schedule                        # servis hesabı + zamanlanmış sorgular
  venv/bin/python pipeline/deploy.py viewer                          # panelin salt okunur servis hesabı

Proje GDELT_BQ_PROJECT ortam değişkeniyle değiştirilebilir. Kimlik: gcloud ADC.
"""
import argparse
import os
import sys
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

from google.cloud import bigquery

PROJECT = os.environ.get("GDELT_BQ_PROJECT", "banded-scion-476220-r0")
DATASET = "gdelt_tr"
HERE = Path(__file__).parent
GB = 1024 ** 3

# GCAM (duygu boyutları) yalnızca bu günden itibaren taranır; öncesinde kolon NULL kalır
GCAM_SINCE = "2025-09-28"
# E3 (events_tu) kapsamı: kullanıcı 23.10.2026'da "B yöntemi, son 12 ay" dedi. Geriye genişletilirse
# bu tarih öne çekilir ve o ayın ücretsiz kotası yeniden değerlendirilir (EVENTS_DEGERLENDIRME.md §7).
EVENTS_TU_SINCE = (date.today() - timedelta(days=365)).isoformat()
# Hızlı geçmiş yükleme: yalnızca günlük sorgu kotası geçici olarak yükseltildiğinde (≥ 2 TiB) açılmalı.
# Kapalıyken bir çalışma en fazla ~400 GiB tarar (günlük kotaya sığar; kota 30.09.2026'dan beri 1 TiB).
FAST_BACKFILL = False
# Elle çalıştırılan tek bir yükleme işinin faturalayabileceği en fazla GB
DEFAULT_MAX_GB = 1500 if FAST_BACKFILL else 450


@dataclass
class Step:
    name: str            # steps/<name>.sql
    table: str           # geçmişte ne kadar geriye gidildiğini gösteren tablo
    day_col: str
    floor: str           # bu tablo için inilecek en eski gün
    months_recent: int   # GCAM_SINCE sonrası parça boyu (ay)
    months_old: int      # GCAM_SINCE öncesi parça boyu (ay)


# Her tablo kendi hızında geriye gider. Haberler 2015'e kadar, diğer tablolar son 12 ay.
STEPS = [
    Step("articles", "articles", "gkg_day", "2015-02-18", 12 if FAST_BACKFILL else 2, 48 if FAST_BACKFILL else 6),
    Step("quotes", "quotes", "gqg_day", GCAM_SINCE, 12, 12),
    Step("page_changes", "page_changes", "check_day", GCAM_SINCE, 12, 12),
    Step("events", "event_mentions", "mention_day", GCAM_SINCE, 12, 12),
    # E3: Türkiye olayları + dünya/Türkçe anılma özeti. Kapsam kararı 23.10.2026'da verildi: son 12 ay.
    Step("events_tu", "events_tu", "event_day", EVENTS_TU_SINCE, 12, 12),
]

client = bigquery.Client(project=PROJECT)


def sql(name: str) -> str:
    return (HERE / name).read_text()


def steps_body() -> str:
    return "\n".join(f"-- ===== {st.name} =====\n" + sql(f"steps/{st.name}.sql") for st in STEPS)


def declare(s_expr: str, e_expr: str) -> str:
    return (f"DECLARE gcam_since DATE DEFAULT DATE '{GCAM_SINCE}';\n"
            f"DECLARE s DATE DEFAULT {s_expr};\nDECLARE e DATE DEFAULT {e_expr};\n\n")


def daily_script() -> str:
    """Zamanlanmış sorgunun çalıştığı günün (UTC) bir önceki gününü yükler. @run_date servis tarafından verilir."""
    day = "DATE_SUB(@run_date, INTERVAL 1 DAY)"
    return "-- Günlük yükleme\n" + declare(day, day) + steps_body()


def range_script() -> str:
    return declare("@start_day", "@end_day") + steps_body()


def frontier(st: Step) -> tuple:
    """Tablonun yüklü en eski gününden (oldest) geriye doğru bir sonraki parçanın [s, e] ifadeleri.
    GCAM_SINCE sınırı tek parçada aşılmaz; floor'a ulaşınca boş aralık (s > e) döner ve hiçbir şey taranmaz."""
    floor = f"DATE '{st.floor}'"
    months = f"IF(oldest > gcam_since, {st.months_recent}, {st.months_old})"
    lower = f"IF(oldest > gcam_since, GREATEST({floor}, gcam_since), {floor})"
    s = f"IF(oldest > {floor}, GREATEST({lower}, DATE_SUB(oldest, INTERVAL {months} MONTH)), DATE '1900-01-02')"
    e = f"IF(oldest > {floor}, DATE_SUB(oldest, INTERVAL 1 DAY), DATE '1900-01-01')"
    return s, e


def backfill_script() -> str:
    parts = ["-- Geçmiş veri: her tablo için yüklü en eski günden geriye doğru bir parça yükler.\n",
             f"DECLARE gcam_since DATE DEFAULT DATE '{GCAM_SINCE}';\nDECLARE oldest DATE;\nDECLARE s DATE;\nDECLARE e DATE;\n"]
    for st in STEPS:
        s, e = frontier(st)
        parts.append(f"\n-- ===== {st.name} =====\n"
                     f"SET oldest = (SELECT MIN({st.day_col}) FROM {DATASET}.{st.table});\n"
                     f"SET e = {e};\nSET s = {s};\n" + sql(f"steps/{st.name}.sql"))
    return "".join(parts)


def next_ranges() -> dict:
    """backfill_script ile aynı ifadelerle her tablonun bir sonraki parçasını hesaplar."""
    out = {}
    for st in STEPS:
        s, e = frontier(st)
        q = (f"SELECT {s} s, {e} e FROM (SELECT (SELECT MIN({st.day_col}) FROM {DATASET}.{st.table}) oldest, "
             f"DATE '{GCAM_SINCE}' gcam_since)")
        r = next(iter(client.query(q).result()))
        out[st.name] = (r.s.isoformat(), r.e.isoformat())
    return out


def _dry(q: str) -> int:
    return client.query(q, job_config=bigquery.QueryJobConfig(dry_run=True, use_query_cache=False)).total_bytes_processed


def step_scan(name: str, s: str, e: str) -> int:
    """Adımın GDELT'ten okuyacağı veri (dry run, ücretsiz). Boş aralık 0 döner."""
    if s > e:
        return 0
    nxt = f"DATE_ADD(DATE '{e}', INTERVAL 1 DAY)"
    part = lambda a, b: f"_PARTITIONTIME BETWEEN TIMESTAMP('{a}') AND TIMESTAMP('{b}')"
    if name == "articles":
        cols = ("_PARTITIONTIME, GKGRECORDID, DATE, SourceCommonName, DocumentIdentifier, Extras, V2Tone, Themes, "
                "V2Persons, Organizations, V2Locations, TranslationInfo, Quotations, AllNames")
        total, gs = 0, max(s, GCAM_SINCE)
        if gs <= e:
            total += _dry(f"SELECT {cols}, GCAM FROM `gdelt-bq.gdeltv2.gkg_partitioned` WHERE {part(gs, e)}")
        pe = min(e, (date.fromisoformat(GCAM_SINCE) - timedelta(days=1)).isoformat())
        if s <= pe:
            total += _dry(f"SELECT {cols} FROM `gdelt-bq.gdeltv2.gkg_partitioned` WHERE {part(s, pe)}")
        return total
    if name == "quotes":
        return _dry(f"SELECT date, url, title, quotes FROM `gdelt-bq.gdeltv2.gqg` "
                    f"WHERE date >= TIMESTAMP('{s}') AND date < TIMESTAMP({nxt}) AND lang = 'TURKISH'")
    if name == "page_changes":
        return _dry(f"""SELECT fetchdate_orig, fetchdate_check, status, http_code, page_url, page_domain_root, page_title,
                        title_new, redirect_url, num_changes, perc_changedchars, changes, page_lang
                        FROM `gdelt-bq.gdeltv2.gdg_partitioned`
                        WHERE fetchdate_check >= TIMESTAMP('{s}') AND fetchdate_check < TIMESTAMP({nxt})""")
    if name == "events":
        return (_dry(f"SELECT * EXCEPT (Extras) FROM `gdelt-bq.gdeltv2.eventmentions_partitioned` WHERE {part(s, e)}")
                + _dry(f"SELECT * FROM `gdelt-bq.gdeltv2.events_partitioned` WHERE {part(s, e)}"))
    if name == "events_tu":
        # Anılmalar olaydan sonra da gelir; adım 7 gün ileriye bakar (steps/events_tu.sql ile aynı)
        ileri = (date.fromisoformat(e) + timedelta(days=7)).isoformat()
        return (_dry(f"""SELECT DATE(_PARTITIONTIME), GLOBALEVENTID, EventRootCode, EventCode, Actor1Name,
                         Actor2Name, AvgTone, GoldsteinScale, NumMentions, NumSources, ActionGeo_FullName,
                         ActionGeo_ADM1Code, ActionGeo_CountryCode
                         FROM `gdelt-bq.gdeltv2.events_partitioned` WHERE {part(s, e)}""")
                + _dry(f"""SELECT GLOBALEVENTID, MentionIdentifier, MentionSourceName
                           FROM `gdelt-bq.gdeltv2.eventmentions_partitioned` WHERE {part(s, ileri)}""")
                + _dry(f"SELECT DocumentIdentifier FROM {DATASET}.articles "
                       f"WHERE gkg_day BETWEEN '{s}' AND '{ileri}'"))
    raise ValueError(name)


def print_estimate(ranges: dict) -> int:
    total = 0
    for name, (s, e) in ranges.items():
        b = step_scan(name, s, e)
        total += b
        print(f"  {name:<14} {s} .. {e}  {b / GB:8.2f} GB" if s <= e else f"  {name:<14} tamamlandı")
    print(f"  {'TOPLAM':<14} {'':<24}{total / GB:8.2f} GB")
    return total


def run_script(script: str, params: list, max_gb: float, label: str):
    cfg = bigquery.QueryJobConfig(query_parameters=params, maximum_bytes_billed=int(max_gb * GB))
    job = client.query(script, job_config=cfg)
    job.result()
    children = list(client.list_jobs(parent_job=job.job_id))
    billed = sum((c.total_bytes_billed or 0) for c in children)
    print(f"[OK] {label}: iş {job.job_id}, faturalanan {billed / GB:.2f} GB ({len(children)} adım)")
    return job


def check_cap(total: int, max_gb: float):
    if total > max_gb * GB:
        sys.exit(f"Tahmini tarama {total / GB:.1f} GB > tavan {max_gb} GB; yükleme yapılmadı.")


def cmd_schema(_):
    client.query(sql("schema.sql")).result()
    print(f"[OK] {PROJECT}.{DATASET} veri seti ve tabloları hazır")


def cmd_estimate(a):
    print_estimate({st.name: (a.start, a.end) for st in STEPS})


def cmd_load(a):
    check_cap(print_estimate({st.name: (a.start, a.end) for st in STEPS}), a.max_gb)
    params = [bigquery.ScalarQueryParameter("start_day", "DATE", a.start),
              bigquery.ScalarQueryParameter("end_day", "DATE", a.end)]
    run_script(range_script(), params, a.max_gb, f"yükleme {a.start}..{a.end}")


def cmd_backfill_step(a):
    ranges = next_ranges()
    if all(s > e for s, e in ranges.values()):
        print("Geçmiş veri tamamlandı.")
        return
    print("Bir sonraki parça:")
    check_cap(print_estimate(ranges), a.max_gb)
    run_script(backfill_script(), [], a.max_gb, "backfill")


SOURCE_DAILY_REBUILD = f"""
BEGIN TRANSACTION;
DELETE FROM {DATASET}.source_daily WHERE TRUE;
INSERT INTO {DATASET}.source_daily
SELECT gkg_day, SourceCommonName, COUNT(*), CURRENT_TIMESTAMP()
FROM {DATASET}.articles
GROUP BY gkg_day, SourceCommonName;
COMMIT TRANSACTION;
"""


def cmd_source_daily(a):
    """Kaynak dizinini tüm geçmiş için yeniden kurar. Yalnızca gdelt_tr.articles'ın gün ve site kolonlarını
    okur (tüm geçmiş ~1,3 GB); sonrasında articles adımı her gün kendi aralığını günceller."""
    client.query(sql("schema.sql")).result()
    scan = _dry(f"SELECT gkg_day, SourceCommonName FROM {DATASET}.articles")
    print(f"  source_daily   tüm geçmiş  {scan / GB:8.2f} GB")
    check_cap(scan, a.max_gb)
    run_script(SOURCE_DAILY_REBUILD, [], a.max_gb, "kaynak dizini")


def cmd_status(_):
    q = f"""
    SELECT 'articles' t, MIN(gkg_day) ilk, MAX(gkg_day) son, COUNT(*) satir FROM {DATASET}.articles UNION ALL
    SELECT 'daily_totals', MIN(day), MAX(day), COUNT(*) FROM {DATASET}.daily_totals UNION ALL
    SELECT 'quotes', MIN(gqg_day), MAX(gqg_day), COUNT(*) FROM {DATASET}.quotes UNION ALL
    SELECT 'page_changes', MIN(check_day), MAX(check_day), COUNT(*) FROM {DATASET}.page_changes UNION ALL
    SELECT 'event_mentions', MIN(mention_day), MAX(mention_day), COUNT(*) FROM {DATASET}.event_mentions UNION ALL
    SELECT 'events', MIN(event_day), MAX(event_day), COUNT(*) FROM {DATASET}.events UNION ALL
    SELECT 'source_daily', MIN(gkg_day), MAX(gkg_day), COUNT(*) FROM {DATASET}.source_daily
    """
    for r in client.query(q).result():
        print(f"  {r.t:<15} {str(r.ilk):<11} {str(r.son):<11} {r.satir:>12,} satır")
    for t in client.list_tables(DATASET):
        tbl = client.get_table(t)
        print(f"  {t.table_id:<15} {tbl.num_bytes / GB:8.3f} GB")


SA_ID = "gdelt-loader"
SA_EMAIL = f"{SA_ID}@{PROJECT}.iam.gserviceaccount.com"
# Yayındaki panelin (Streamlit Cloud) kullandığı hesap: yalnızca gdelt_tr'yi okur, sorgu çalıştırır
VIEWER_ID = "gdelt-viewer"
# Zamanlar UTC (Türkiye = UTC+3). BigQuery günlük kotası Pasifik gece yarısında sıfırlanır: yaz saatinde 07:00,
# kış saatinde 08:00 UTC. İşler her iki durumda da sıfırlamadan sonra çalışsın diye 08:15 / 09:15 UTC'dedir;
# böylece o gün başka sorgular kotayı doldurmuş olsa da etkilenmezler.
# (zaman, betik, açık mı). Geçmiş veri 28.09.2026'da tamamlandığı için duraklatıldı: açık kalsaydı hiçbir şey
# yüklemeden her gün tabloların en eski gününü bulmak için ~0,6 GB tarardı. Yeniden açmak için True yapıp
# `schedule` çalıştırın (örn. bir tablonun floor tarihi geri çekildiğinde).
SCHEDULES = {
    "gdelt_tr günlük yükleme": ("every day 08:15", daily_script, True),
    "gdelt_tr geçmiş veri": ("every day 09:15", backfill_script, False),
}


def ensure_service_account(sa_id=SA_ID, display="GDELT zamanlanmış yükleme", dataset_role="WRITER",
                           roles=("roles/bigquery.jobUser",)):
    """Servis hesabını ve en dar yetkileri hazırlar: projede yalnızca `roles` (varsayılan: sorgu çalıştırma),
    veri olarak yalnızca gdelt_tr üzerinde dataset_role. Tekrar çalıştırmak güvenlidir."""
    import google.auth
    from google.auth.transport.requests import AuthorizedSession

    email = f"{sa_id}@{PROJECT}.iam.gserviceaccount.com"
    creds, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
    s = AuthorizedSession(creds)
    su = f"https://serviceusage.googleapis.com/v1/projects/{PROJECT}/services/iam.googleapis.com"
    if s.get(su).json().get("state") != "ENABLED":
        s.post(su + ":enable").raise_for_status()
        print("[OK] IAM API açıldı")
    r = s.post(f"https://iam.googleapis.com/v1/projects/{PROJECT}/serviceAccounts",
               json={"accountId": sa_id, "serviceAccount": {"displayName": display}})
    if r.status_code not in (200, 409):
        r.raise_for_status()
    print(f"[OK] servis hesabı: {email}")

    # Proje düzeyinde yalnızca verilen roller
    crm = f"https://cloudresourcemanager.googleapis.com/v1/projects/{PROJECT}"
    policy = s.post(crm + ":getIamPolicy", json={}).json()
    member = f"serviceAccount:{email}"
    changed = False
    for role in roles:
        binding = next((b for b in policy.setdefault("bindings", []) if b["role"] == role), None)
        if binding is None:
            policy["bindings"].append({"role": role, "members": [member]})
            changed = True
        elif member not in binding["members"]:
            binding["members"].append(member)
            changed = True
    # Yetkiler zaten varsa IAM politikasına yazılmaz
    if changed:
        s.post(crm + ":setIamPolicy", json={"policy": policy}).raise_for_status()
    print(f"[OK] {', '.join(roles)} {'verildi' if changed else 'zaten var'}")

    ds = client.get_dataset(DATASET)
    if not any(e.entity_id == email for e in ds.access_entries):
        ds.access_entries = list(ds.access_entries) + [bigquery.AccessEntry(dataset_role, "userByEmail", email)]
        client.update_dataset(ds, ["access_entries"])
        print(f"[OK] {DATASET} üzerinde {dataset_role} yetkisi verildi")
    else:
        print(f"[OK] {DATASET} üzerinde yetki zaten var")
    return email


def cmd_viewer(_):
    # readSessionUser: panel aramaları Storage Read API ile yapılır (sorgu kotasına sayılmaz, ayda 300 TiB ücretsiz)
    email = ensure_service_account(VIEWER_ID, "GDELT panel (salt okunur)", "READER",
                                   ("roles/bigquery.jobUser", "roles/bigquery.readSessionUser"))
    print(f"""
Anahtarı Console'dan oluşturun (dosya repoya girmemeli):
  IAM ve Yönetici > Servis Hesapları > {email} > Anahtarlar > Anahtar ekle > JSON
İndirilen JSON'un alanlarını Streamlit Cloud'da uygulamanın Settings > Secrets bölümüne
[gcp_service_account] başlığı altında TOML olarak yapıştırın (YARDIM.md, "Paneli yayınlamak").""")


def cmd_schedule(_):
    from google.cloud import bigquery_datatransfer as dt

    ensure_service_account()
    dts = dt.DataTransferServiceClient()
    parent = f"projects/{PROJECT}/locations/us"
    existing = {c.display_name: c for c in dts.list_transfer_configs(parent=parent)}
    for name, (schedule, script, enabled) in SCHEDULES.items():
        cfg = dt.TransferConfig(
            display_name=name,
            data_source_id="scheduled_query",
            params={"query": script()},
            schedule=schedule,
            email_preferences=dt.EmailPreferences(enable_failure_email=True),
            disabled=not enabled,
        )
        if name in existing:
            cfg.name = existing[name].name
            dts.update_transfer_config(request=dt.UpdateTransferConfigRequest(
                transfer_config=cfg, service_account_name=SA_EMAIL,
                update_mask={"paths": ["params", "schedule", "email_preferences", "service_account_name", "disabled"]},
            ))
            print(f"[OK] güncellendi: {name} ({schedule} UTC, {'açık' if enabled else 'duraklatıldı'})")
        else:
            created = dts.create_transfer_config(request=dt.CreateTransferConfigRequest(
                parent=parent, transfer_config=cfg, service_account_name=SA_EMAIL,
            ))
            print(f"[OK] oluşturuldu: {name} ({schedule} UTC), sonraki çalışma: {created.next_run_time}")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("schema").set_defaults(fn=cmd_schema)
    for name, fn in [("estimate", cmd_estimate), ("load", cmd_load)]:
        sp = sub.add_parser(name)
        sp.add_argument("start")
        sp.add_argument("end")
        sp.add_argument("--max-gb", type=float, default=DEFAULT_MAX_GB)
        sp.set_defaults(fn=fn)
    sp = sub.add_parser("backfill-step")
    sp.add_argument("--max-gb", type=float, default=DEFAULT_MAX_GB)
    sp.set_defaults(fn=cmd_backfill_step)
    sub.add_parser("status").set_defaults(fn=cmd_status)
    sp = sub.add_parser("source-daily")
    sp.add_argument("--max-gb", type=float, default=5)
    sp.set_defaults(fn=cmd_source_daily)
    sub.add_parser("schedule").set_defaults(fn=cmd_schedule)
    sub.add_parser("viewer").set_defaults(fn=cmd_viewer)
    a = p.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
