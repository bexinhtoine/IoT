#!/usr/bin/env bash
# O-08 — Sinh ảnh NVS từ CSV (namespace ota_cfg: ssid/pass/url/max_ver) và ghi vào phân vùng nvs @0x9000.
# Dùng: bash esp32/tools/provision_nvs.sh [esp32/nvs/wifi_ota.csv] [/dev/ttyUSB0]
# Yêu cầu: đã kích hoạt môi trường ESP-IDF (IDF_PATH, esptool.py trong PATH).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ESP32_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
CSV="${1:-$ESP32_DIR/nvs/wifi_ota.csv}"
PORT="${2:-/dev/ttyUSB0}"
NVS_OFFSET=0x9000      # theo esp32/partitions.csv
NVS_SIZE=0x6000
OUT="$ESP32_DIR/build/nvs_wifi_ota.bin"

if [[ -z "${IDF_PATH:-}" ]]; then
  echo "IDF_PATH chua dat — chay '. \$IDF_PATH/export.sh' truoc" >&2
  exit 1
fi
if [[ ! -f "$CSV" ]]; then
  echo "Khong thay $CSV — copy tu esp32/nvs/wifi_ota.csv.example va sua ssid/pass/url" >&2
  exit 1
fi
if ! grep -q '^url,data,string,https://' "$CSV"; then
  echo "url trong CSV phai bat dau bang https:// (O-08, khong cho http)" >&2
  exit 1
fi

mkdir -p "$(dirname "$OUT")"
python "$IDF_PATH/components/nvs_flash/nvs_partition_generator/nvs_partition_gen.py" \
       generate "$CSV" "$OUT" "$NVS_SIZE"
echo "Da sinh $OUT — ghi vao $PORT @ $NVS_OFFSET"
esptool.py --chip esp32 -p "$PORT" write_flash "$NVS_OFFSET" "$OUT"
echo "Xong. Khoi dong lai board; log 'ota' se doc duoc ssid/url khi nhan OTA_CHECK."
