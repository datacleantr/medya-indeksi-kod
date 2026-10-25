#!/usr/bin/env bash
# Paneli Cloud Run'a dağıtır (P11). Tekrar çalıştırılabilir.
#
#   bash dagit.sh              # dağıtır. .streamlit/panel_parolasi.txt varsa parolalı giriş kurar
#                              # ve servisi herkese açar; yoksa Cloud Run kimlik doğrulaması kalır.
#
# Gereken: `gcloud auth login` ile giriş yapılmış olmalı.
#
# Neden bu ayarlar:
#   --min-instances=0   Trafik yokken örnek çalışmaz → ücretsiz katmanda kalır. Bedeli soğuk başlangıç
#                       (bu uygulamanın import yükü ağır, ilk istek 10–30 sn sürebilir).
#   --session-affinity  Streamlit oturumu sunucuda tutar; aynı kullanıcı aynı örneğe gitmezse oturumu
#                       kopar. Cloud Run'da bu ŞART.
#   --timeout=3600      Streamlit websocket kullanır; kısa zaman aşımı bağlantıyı düşürür.
#   --service-account   gdelt-viewer: projede yalnızca jobUser + readSessionUser, gdelt_tr'de READER.
#                       Panel bu kimlikle okur; imaja anahtar konmaz.
set -euo pipefail

PROJE="${PROJE:-banded-scion-476220-r0}"
BOLGE="${BOLGE:-us-central1}"        # BigQuery veri seti US'te; aynı bölge = düşük gecikme, çıkış ücreti yok
SERVIS="${SERVIS:-medya-indeksi}"
SA="gdelt-viewer@${PROJE}.iam.gserviceaccount.com"

command -v gcloud >/dev/null || { echo "gcloud kurulu değil"; exit 1; }
gcloud auth list --filter=status:ACTIVE --format="value(account)" 2>/dev/null | grep -q . || {
  echo "GitHub değil, GOOGLE oturumu yok. Şunu çalıştırın:  gcloud auth login"; exit 1; }

echo "proje: $PROJE · bölge: $BOLGE · servis: $SERVIS"

echo "[1/3] Gerekli API'ler açılıyor (açıksa dokunmaz)…"
gcloud services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com \
  --project "$PROJE" --quiet

# Erişim: parola özeti varsa uygulama kendi kapısını kurar ve servis herkese açılır;
# yoksa Cloud Run kimlik doğrulaması devrede kalır.
PAROLA_DOSYA=".streamlit/panel_parolasi.txt"
OZET=""
[ -f "$PAROLA_DOSYA" ] && OZET=$(grep -E "^özet" "$PAROLA_DOSYA" | awk '{print $3}')

if [ -n "$OZET" ]; then
  ERISIM="--allow-unauthenticated"
  ORTAM="--set-env-vars=PANEL_PAROLA_OZETI=$OZET"
  NOT="Herkese açık URL + uygulama içi PAROLA. Parola: $PAROLA_DOSYA (repoya girmez)"
  echo "[i] Parolalı giriş etkin (özet ortam değişkeninde; parolanın kendisi sunucuda yok)"
else
  ERISIM="--no-allow-unauthenticated"
  ORTAM="--clear-env-vars"
  NOT="Kimlik doğrulamalı. Görüntülemek için:  gcloud run services proxy $SERVIS --region $BOLGE --project $PROJE"
fi

echo "[2/3] Kaynak yükleniyor ve imaj kuruluyor (Cloud Build)…"
echo "[3/3] Dağıtılıyor…"
gcloud run deploy "$SERVIS" \
  --source . \
  --project "$PROJE" \
  --region "$BOLGE" \
  --service-account "$SA" \
  --memory 4Gi \
  --cpu 2 \
  --min-instances 0 \
  --max-instances 3 \
  --concurrency 20 \
  --timeout 3600 \
  --session-affinity \
  $ERISIM \
  $ORTAM \
  --quiet

echo
URL=$(gcloud run services describe "$SERVIS" --project "$PROJE" --region "$BOLGE" \
      --format="value(status.url)" 2>/dev/null)
echo "Adres : $URL"
echo "Erişim: $NOT"
echo
echo "Soğuk başlangıç ölçümü için:  bash dagit.sh --olc"
echo
echo "Maliyet: min-instances=0 olduğu için trafik yokken ücret işlemez. Cloud Run'ın aylık ücretsiz"
echo "katmanı (2M istek, 360 bin GiB-sn bellek, 180 bin vCPU-sn) düşük trafikte bunu karşılar."
