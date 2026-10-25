# cases/ — vaka çalışması araçları

Kullanım, ilkeler ve derece sistemi: [YARDIM.md §15](../YARDIM.md#15-vaka-çalışmaları-cases). Kısaca:

```bash
cp cases/vaka.ornek.toml dosyalar/<vaka>/vaka.toml
venv/bin/python -m cases.corpus  <vaka>          # korpus + kapsam.md
venv/bin/python -m cases.analyze <vaka>          # analiz.md
venv/bin/python -m cases.texts   <vaka> sec|cek|ozet
venv/bin/python -m cases.sources <vaka> ekle|ice-aktar|tara|liste|md
venv/bin/python -m cases.report  <vaka> [--marka]
```

Vaka klasörleri (`dosyalar/`) ve ortak depo (`data/store/`) gerçek kişi adları ve telifli metinler içerir; repoya girmez.
