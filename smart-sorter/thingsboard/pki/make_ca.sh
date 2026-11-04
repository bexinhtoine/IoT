#!/usr/bin/env bash
# S-01 — Tạo CA nội bộ "Smart Sorter Root CA" (EC P-256, hạn 5 năm).
# Kết quả: out/ca.pem (chứng chỉ, public) + out/ca.key (khóa riêng, quyền 600, KHÔNG commit).
# Idempotent: không ghi đè khóa đã có nếu không có --force.
#
# Cách dùng:  ./make_ca.sh [--force]
set -euo pipefail
export MSYS_NO_PATHCONV=1   # Git Bash trên Windows: không chuyển '/CN=...' thành đường dẫn

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUT="${PKI_OUT:-$HERE/out}"
CA_CN="Smart Sorter Root CA"
CA_DAYS=1825          # 5 năm
FORCE=0
[[ "${1:-}" == "--force" ]] && FORCE=1

command -v openssl >/dev/null || { echo "Thiếu openssl" >&2; exit 1; }
mkdir -p "$OUT"
chmod 700 "$OUT" 2>/dev/null || true

if [[ -f "$OUT/ca.key" && $FORCE -eq 0 ]]; then
  echo "Đã có $OUT/ca.key — giữ nguyên (dùng --force để tạo lại; mọi cert đã ký sẽ VÔ HIỆU)."
else
  if [[ $FORCE -eq 1 && -f "$OUT/ca.key" ]]; then
    echo "CẢNH BÁO: --force sẽ tạo CA mới; mọi cert server/device đã ký bằng CA cũ sẽ không còn tin cậy."
  fi
  umask 077
  openssl ecparam -name prime256v1 -genkey -noout -out "$OUT/ca.key"
  chmod 600 "$OUT/ca.key"
  # Tự ký: CA:TRUE, chỉ dùng để ký cert (keyCertSign, cRLSign)
  openssl req -x509 -new -sha256 -key "$OUT/ca.key" -days "$CA_DAYS" \
    -subj "/CN=$CA_CN/O=Smart Sorter" \
    -addext "basicConstraints=critical,CA:TRUE,pathlen:0" \
    -addext "keyUsage=critical,keyCertSign,cRLSign" \
    -addext "subjectKeyIdentifier=hash" \
    -out "$OUT/ca.pem"
  chmod 644 "$OUT/ca.pem"
  echo "Đã tạo CA: $OUT/ca.pem (hạn $CA_DAYS ngày)"
fi

openssl x509 -in "$OUT/ca.pem" -noout -subject -enddate -fingerprint -sha256

cat <<EOF

Copy file đi đâu:
  ca.pem  -> Raspberry Pi: /etc/sorter/certs/ca.pem        (owner sorter, chmod 644)  [thingsboard.ca_cert]
  ca.pem  -> ESP32 firmware: esp32/main/certs/ota_ca.pem     (nhúng vào firmware để xác minh máy chủ OTA, P8)
  ca.pem  -> ota-app/certs/ca.pem                            (app OTA xác minh ThingsBoard/OTA server nếu cần)
  ca.key  -> GIỮ TRÊN MÁY QUẢN TRỊ, không copy đi đâu, không commit (đã có trong .gitignore).

Tiếp theo: ./make_server_cert.sh tb.local 192.168.1.10
          ./make_device_cert.sh sorter-pi-01
          ./make_ota_server_cert.sh 192.168.1.10
EOF
