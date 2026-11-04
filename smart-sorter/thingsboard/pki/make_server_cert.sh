#!/usr/bin/env bash
# S-01 / T-01 — Cert server cho ThingsBoard MQTT 8883, ký bởi CA nội bộ (out/ca.pem).
# SAN gồm DNS:<hostname> (+ DNS:tb.local luôn có) và mọi IP truyền thêm; hạn 2 năm; extendedKeyUsage=serverAuth.
# Kết quả: out/server.pem + out/server_key.pem (quyền 600). Không ghi đè nếu không có --force.
#
# Cách dùng:  ./make_server_cert.sh <hostname> [ip ...] [--force]
#   ví dụ:    ./make_server_cert.sh tb.local 192.168.1.10 10.0.0.5
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
[[ ${#ARGS[@]} -ge 1 ]] || { echo "Cách dùng: $0 <hostname> [ip ...] [--force]" >&2; exit 2; }
HOST="${ARGS[0]}"
[[ -f "$OUT/ca.pem" && -f "$OUT/ca.key" ]] || { echo "Chưa có CA — chạy ./make_ca.sh trước" >&2; exit 1; }

if [[ -f "$OUT/server_key.pem" && $FORCE -eq 0 ]]; then
  echo "Đã có $OUT/server_key.pem — giữ nguyên (dùng --force để tạo lại)."; exit 0
fi

# Dựng danh sách SAN: DNS:tb.local luôn có; hostname (nếu là IP thì đưa vào IP:)
SAN="DNS:tb.local"
is_ip() { [[ "$1" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; }
if is_ip "$HOST"; then SAN="$SAN,IP:$HOST"; elif [[ "$HOST" != "tb.local" ]]; then SAN="$SAN,DNS:$HOST"; fi
for ip in "${ARGS[@]:1}"; do
  if is_ip "$ip"; then SAN="$SAN,IP:$ip"; else SAN="$SAN,DNS:$ip"; fi
done
SAN="$SAN,DNS:localhost,IP:127.0.0.1"

# Thư mục tạm bên trong out/ (không dùng /tmp để chạy được cả trên Git Bash); chỉ chuyển khóa + cert
# vào out/ khi mọi bước thành công -> không để lại khóa "mồ côi" làm hỏng kiểm tra idempotent.
TMP="$OUT/.tmp.$$"; mkdir -p "$TMP"; trap 'rm -rf "$TMP"' EXIT
sed "s|@SAN@|$SAN|" "$HERE/openssl-san.cnf.template" > "$TMP/ext.cnf"

umask 077
openssl ecparam -name prime256v1 -genkey -noout -out "$TMP/server_key.pem"
openssl req -new -sha256 -key "$TMP/server_key.pem" -subj "/CN=$HOST/O=Smart Sorter" -out "$TMP/server.csr"
openssl x509 -req -sha256 -days "$DAYS" -in "$TMP/server.csr" \
  -CA "$OUT/ca.pem" -CAkey "$OUT/ca.key" -CAcreateserial -CAserial "$OUT/ca.srl" \
  -extfile "$TMP/ext.cnf" -extensions server_ext -out "$TMP/server.pem"
mv -f "$TMP/server_key.pem" "$OUT/server_key.pem"; chmod 600 "$OUT/server_key.pem"
mv -f "$TMP/server.pem" "$OUT/server.pem";         chmod 644 "$OUT/server.pem"

echo "Đã tạo $OUT/server.pem (SAN: $SAN, hạn $DAYS ngày)"
openssl x509 -in "$OUT/server.pem" -noout -subject -enddate -ext subjectAltName
openssl verify -CAfile "$OUT/ca.pem" "$OUT/server.pem"

cat <<EOF

Copy file đi đâu:
  server.pem, server_key.pem -> thingsboard/certs/   (mount read-only vào container tại /config, xem docker-compose.yml)
      mkdir -p "$HERE/../certs" && cp $OUT/server.pem $OUT/server_key.pem "$HERE/../certs/"
      chmod 644 "$HERE/../certs/server.pem"; chmod 600 "$HERE/../certs/server_key.pem"
  Lưu ý: tiến trình ThingsBoard trong container chạy user thingsboard (uid 799); nếu lỗi quyền đọc khóa,
      sudo chown 799:799 thingsboard/certs/server_key.pem (hoặc chmod 640 + chgrp 799).
  Nếu Pi báo "unable to get local issuer certificate", gửi cả chain: cat server.pem ca.pem > certs/server.pem
EOF
