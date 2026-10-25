#!/usr/bin/env bash
# Vitrini herkese açık repoya yayımlar (T3).
#
#   bash vitrin/yayinla.sh                      # varsayılan repo adıyla
#   REPO=baska-ad bash vitrin/yayinla.sh        # başka adla
#
# İlk çalıştırmada repoyu oluşturur ve GitHub Pages'i açar; sonraki çalıştırmalarda yalnızca günceller.
# Gereken: `gh auth login` ile giriş yapılmış olmalı (repo oluşturma ve Pages ayarı için).
set -euo pipefail

KOK="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SITE="$KOK/vitrin/site"
REPO="${REPO:-medya-indeksi}"
STAGE="${STAGE:-/tmp/medya-indeksi-vitrin}"

[ -f "$SITE/index.html" ] || { echo "Site üretilmemiş. Önce: venv/bin/python vitrin/olustur.py"; exit 1; }
command -v gh >/dev/null || { echo "gh kurulu değil: brew install gh"; exit 1; }
gh auth status >/dev/null 2>&1 || { echo "GitHub oturumu yok. Şunu çalıştırın: gh auth login"; exit 1; }

KULLANICI="$(gh api user --jq .login)"
echo "GitHub kullanıcısı: $KULLANICI · repo: $REPO"

# 1) Repo yoksa oluştur (herkese açık)
if gh repo view "$KULLANICI/$REPO" >/dev/null 2>&1; then
  echo "[=] repo zaten var"
else
  gh repo create "$KULLANICI/$REPO" --public \
    --description "Medya İndeksi — Türkçe medya arşivi tanıtım sayfası" >/dev/null
  echo "[+] repo oluşturuldu"
fi

# 2) Site içeriğini hazırla ve push et
rm -rf "$STAGE"
git clone -q "git@github.com:$KULLANICI/$REPO.git" "$STAGE" 2>/dev/null || {
  mkdir -p "$STAGE" && cd "$STAGE" && git init -q -b main \
    && git remote add origin "git@github.com:$KULLANICI/$REPO.git"
}
cd "$STAGE"
git checkout -q -B main
# Eski site dosyalarını temizle (.git dışında), yenilerini koy
find . -mindepth 1 -maxdepth 1 ! -name .git -exec rm -rf {} +
cp -R "$SITE"/. .
cat > README.md <<'MD'
# Medya İndeksi — tanıtım sayfası

2015'ten bugüne Türkçe haberlerde bir kişinin, kurumun ya da konunun nasıl ele alındığını ölçen bir
araştırma aracının tanıtım sayfası. Sayfa [GitHub Pages](https://pages.github.com/) ile yayımlanır.

Bu repo yalnızca **üretilmiş statik sayfayı** içerir; kaynak kod ayrı ve özel bir repodadır.
Sayfadaki her rakam arşivden okunarak üretilir, elle yazılmaz.

Veri kaynağı: [The GDELT Project](https://www.gdeltproject.org/) (GKG 2.1). GDELT verisi açık ve
ücretsizdir; bu sayfa GDELT tarafından hazırlanmamıştır.
MD

git add -A
if git diff --cached --quiet; then
  echo "[=] değişiklik yok"
else
  git commit -q -m "Vitrin güncellendi ($(date +%d.%m.%Y))"
  git push -q -u origin main
  echo "[+] push edildi"
fi

# 3) GitHub Pages'i aç (zaten açıksa dokunmaz)
if gh api "repos/$KULLANICI/$REPO/pages" >/dev/null 2>&1; then
  echo "[=] Pages zaten açık"
else
  gh api -X POST "repos/$KULLANICI/$REPO/pages" -f "source[branch]=main" -f "source[path]=/" >/dev/null \
    && echo "[+] Pages açıldı" || echo "[!] Pages açılamadı; Settings → Pages'ten elle açın"
fi

echo
echo "Adres: https://$KULLANICI.github.io/$REPO/"
echo "(İlk yayımlamada birkaç dakika sürebilir.)"
