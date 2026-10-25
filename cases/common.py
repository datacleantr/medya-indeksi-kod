"""Vaka araçlarının ortak parçaları: yollar, vaka yapılandırması, ortak depo ve app.py yardımcıları.

app.py'deki sabitler ve fonksiyonlar (clean_title, fold_tr, KEY_OUTLETS, coverage_report ...) paneli
çalıştırmadan yüklenir: app.py'nin yalnızca tanım satırları (import, def, atama) arayüz bölümüne kadar çalıştırılır.
Böylece panel ile vaka raporları aynı kuralları ve aynı kapsam denetimini kullanır.
"""
import ast
import os
import tomllib
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP = ROOT / "app.py"
ARCHIVE = Path(os.environ.get("GDELT_ARCHIVE", ROOT / "data" / "archive"))
# Vaka klasörleri gerçek kişi adları içerir; repoya girmez (.gitignore: dosyalar/)
CASES_DIR = ROOT / "dosyalar"
# Vakalar arası ortak depo: haber metinleri, açık kaynak bulgu kaydı (repoya girmez: data/store/)
STORE = ROOT / "data" / "store" / "vaka.duckdb"
# app.py'de arayüzün başladığı atama; tanımlar bundan öncedir
APP_UI_MARKER = "SRC_CSV"

SCHEMA = """
CREATE TABLE IF NOT EXISTS article_texts (
    url VARCHAR PRIMARY KEY,      -- haberin adresi (arşivdeki DocumentIdentifier)
    site VARCHAR,
    published TIMESTAMP,          -- arşivdeki yayın zamanı (TSİ)
    fetched TIMESTAMP,            -- metnin çekildiği an
    origin VARCHAR,               -- 'canlı' | 'wayback <zaman damgası>' | 'yok'
    fetched_url VARCHAR,          -- metnin gerçekten alındığı adres (yönlendirme veya Wayback)
    title VARCHAR,
    text VARCHAR                  -- yalnızca iç araştırma içindir (telif); dışa aktarılmaz
);
CREATE TABLE IF NOT EXISTS case_sources (
    id BIGINT PRIMARY KEY,
    case_name VARCHAR,            -- vaka klasörünün adı
    url VARCHAR,
    outlet VARCHAR,               -- yayın / kurum
    published VARCHAR,            -- yayın tarihi (serbest biçim: 22.09.2026, 2020, ...)
    claim VARCHAR,                -- kaynağın desteklediği bilgi veya iddia
    source_grade VARCHAR,         -- kaynak güvenilirliği A–F
    info_grade VARCHAR,           -- bilginin doğruluğu 1–6
    verified_by VARCHAR,          -- teyit eden ikinci kaynak veya yöntem
    note VARCHAR,
    added TIMESTAMP DEFAULT current_timestamp
);
CREATE SEQUENCE IF NOT EXISTS case_sources_id START 1;
"""


def store():
    """Ortak deponun bağlantısı (DuckDB tek dosya); tablo yoksa oluşturur."""
    import duckdb

    STORE.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(STORE))
    con.execute(SCHEMA)
    return con


def case_dir(name: str) -> Path:
    path = CASES_DIR / name
    if not (path / "vaka.toml").exists():
        raise SystemExit(f"Vaka yapılandırması yok: {path / 'vaka.toml'} (örnek: cases/vaka.ornek.toml)")
    return path


def load_case(name: str) -> dict:
    """dosyalar/<vaka>/vaka.toml yapılandırmasını okur (biçim: cases/vaka.ornek.toml)."""
    cfg = tomllib.loads((case_dir(name) / "vaka.toml").read_text())
    cfg["_name"] = name
    cfg["_dir"] = case_dir(name)
    return cfg


@lru_cache(maxsize=1)
def app_ns() -> dict:
    """app.py'nin tanımlarını (sabitler, yardımcı fonksiyonlar) paneli çalıştırmadan yükler."""
    import logging

    logging.getLogger("streamlit").setLevel(logging.ERROR)
    tree = ast.parse(APP.read_text())
    ns = {"__name__": "app_helpers", "__file__": str(APP)}
    kinds = (ast.Import, ast.ImportFrom, ast.FunctionDef, ast.Assign, ast.AnnAssign, ast.ClassDef)
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(getattr(t, "id", None) == APP_UI_MARKER or APP_UI_MARKER in
                                                [getattr(e, "id", None) for e in getattr(t, "elts", [])]
                                                for t in node.targets):
            break
        if isinstance(node, kinds):
            exec(compile(ast.Module([node], []), str(APP), "exec"), ns)
    return ns


def app(name: str):
    """app.py'den tek bir tanım: app("fold_tr")("Iğdır")."""
    return app_ns()[name]
