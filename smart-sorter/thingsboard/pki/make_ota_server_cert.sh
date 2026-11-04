#!/usr/bin/env bash
# S-01 / O-xx — Cert server cho máy chủ Secure OTA (app P7, HTTPS cổng 8443), ký bởi CA nội bộ.
# ESP32 xác minh máy chủ OTA bằng ca.pem nhúng trong firmware (ota_ca.pem), nên cert này PHẢI do cùng CA ký
# và SAN phải chứa đúng hostname/IP mà ESP32 dùng trong URL manifest.
# Kết quả: out/ota_server.pem + out/ota_server.key (quyền 600). Không ghi đè nếu không có --force.
#
# Cách dùng:  ./make_ota_server_cert.sh <hostname|ip> [ip ...] [--force]
#   ví dụ:    ./make_ota_server_cert.sh 192.168.1.10
set -euo pipefail
export MSYS_NO_PATHCONV=1   # Git Bash trên Windows: không chuyển '/CN=...' thành đường dẫn

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUT="${PKI_OUT:-$HERE/out}"
DAYS=730
FORCE=0
ARGS=()
for a in "$@"; do
  if [[ "$a" == "--force" ]]; then FORCE=1; else ARGS+=("$a"); fi
done
[[ ${#ARGS[@]} -ge 1 ]] || { echo "Cách dùng: $0 <hostname|ip> [ip ...] [--force]" >&2; exit 2; }
HOST="${ARGS[0]}"
[[ -f "$OUT/ca.pem" && -f "$OUT/ca.key" ]] || { echo "Chưa có CA — chạy ./make_ca.sh trước" >&2; exit 1; }

if [[ -f "$OUT/ota_server.key" && $FORCE -eq 0 ]]; then
  echo "Đã có $OUT/ota_server.key — giữ nguyên (dùng --force để tạo lại)."; exit 0
fi

is_ip() { [[ "$1" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; }
SAN=""
for h in "${ARGS[@]}"; do
  if is_ip "$h"; then SAN="$SAN,IP:$h"; else SAN="$SAN,DNS:$h"; fi
done
SAN="${SAN#,},DNS:localhost,IP:127.0.0.1"

# Thư mục tạm trong out/ (chạy được trên Git Bash); chỉ chuyển khóa + cert vào out/ khi thành công.
TMP="$OUT/.tmp.$$"; mkdir -p "$TMP"; trap 'rm -rf "$TMP"' EXIT
sed "s|@SAN@|$SAN|" "$HERE/openssl-san.cnf.template" > "$TMP/ext.cnf"

umask 077
openssl ecparam -name prime256v1 -genkey -noout -out "$TMP/ota_server.key"
openssl req -new -sha256 -key "$TMP/ota_server.key" -subj "/CN=$HOST/O=Smart Sorter OTA" -out "$TMP/ota.csr"
openssl x509 -req -sha256 -days "$DAYS" -in "$TMP/ota.csr" \
  -CA "$OUT/ca.pem" -CAkey "$OUT/ca.key" -CAcreateserial -CAserial "$OUT/ca.srl" \
  -extfile "$TMP/ext.cnf" -extensions server_ext -out "$TMP/ota_server.pem"
mv -f "$TMP/ota_server.key" "$OUT/ota_server.key"; chmod 600 "$OUT/ota_server.key"
mv -f "$TMP/ota_server.pem" "$OUT/ota_server.pem"; chmod 644 "$OUT/ota_server.pem"

echo "Đã tạo $OUT/ota_server.pem (SAN: $SAN, hạn $DAYS ngày)"
openssl x509 -in "$OUT/ota_server.pem" -noout -subject -enddate -ext subjectAltName
openssl verify -CAfile "$OUT/ca.pem" "$OUT/ota_server.pem"

cat <<EOF

Copy file đi đâu:
  ota_server.pem, ota_server.key -> ota-app/certs/   (app Secure OTA P7 dùng làm cert HTTPS 8443; key chmod 600)
  ca.pem                         -> esp32/main/certs/ota_ca.pem (ESP32 chỉ tin CA này khi tải manifest/firmware)
  Lưu ý mbedTLS trên ESP32 kiểm tra SAN theo hostname trong URL: URL manifest phải dùng đúng "$HOST".
EOF
