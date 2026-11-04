#!/usr/bin/env bash
# S-01 / T-02 — Cert thiết bị (Raspberry Pi) cho mutual TLS: CN=<device_name>, hạn 1 năm, extendedKeyUsage=clientAuth.
# ThingsBoard nhận diện thiết bị bằng chính chứng chỉ này (credentials X.509), không dùng access token.
# Kết quả: out/<device_name>.pem + out/<device_name>.key (quyền 600). Không ghi đè nếu không có --force.
#
# Cách dùng:  ./make_device_cert.sh <device_name> [--force]
#   ví dụ:    ./make_device_cert.sh sorter-pi-01
set -euo pipefail
export MSYS_NO_PATHCONV=1   # Git Bash trên Windows: không chuyển '/CN=...' thành đường dẫn

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUT="${PKI_OUT:-$HERE/out}"
DAYS=365
FORCE=0
NAME=""
for a in "$@"; do
  if [[ "$a" == "--force" ]]; then FORCE=1; else NAME="$a"; fi
done
[[ -n "$NAME" ]] || { echo "Cách dùng: $0 <device_name> [--force]" >&2; exit 2; }
[[ "$NAME" =~ ^[A-Za-z0-9._-]{1,64}$ ]] || { echo "Tên thiết bị chỉ gồm chữ, số, . _ - (≤64 ký tự)" >&2; exit 2; }
[[ -f "$OUT/ca.pem" && -f "$OUT/ca.key" ]] || { echo "Chưa có CA — chạy ./make_ca.sh trước" >&2; exit 1; }

if [[ -f "$OUT/$NAME.key" && $FORCE -eq 0 ]]; then
  echo "Đã có $OUT/$NAME.key — giữ nguyên (dùng --force để tạo lại; phải dán lại cert mới vào ThingsBoard)."; exit 0
fi

# Thư mục tạm trong out/ (chạy được trên Git Bash); chỉ chuyển khóa + cert vào out/ khi thành công.
TMP="$OUT/.tmp.$$"; mkdir -p "$TMP"; trap 'rm -rf "$TMP"' EXIT
sed "s|@SAN@|DNS:$NAME|" "$HERE/openssl-san.cnf.template" > "$TMP/ext.cnf"

umask 077
openssl ecparam -name prime256v1 -genkey -noout -out "$TMP/$NAME.key"
openssl req -new -sha256 -key "$TMP/$NAME.key" -subj "/CN=$NAME/O=Smart Sorter" -out "$TMP/$NAME.csr"
openssl x509 -req -sha256 -days "$DAYS" -in "$TMP/$NAME.csr" \
  -CA "$OUT/ca.pem" -CAkey "$OUT/ca.key" -CAcreateserial -CAserial "$OUT/ca.srl" \
  -extfile "$TMP/ext.cnf" -extensions client_ext -out "$TMP/$NAME.pem"
mv -f "$TMP/$NAME.key" "$OUT/$NAME.key"; chmod 600 "$OUT/$NAME.key"
mv -f "$TMP/$NAME.pem" "$OUT/$NAME.pem"; chmod 644 "$OUT/$NAME.pem"

echo "Đã tạo $OUT/$NAME.pem (CN=$NAME, hạn $DAYS ngày)"
openssl x509 -in "$OUT/$NAME.pem" -noout -subject -enddate
openssl verify -CAfile "$OUT/ca.pem" "$OUT/$NAME.pem"

cat <<EOF

Copy file đi đâu:
  $NAME.pem -> Pi: /etc/sorter/certs/device.pem   (chmod 644, owner sorter)   [thingsboard.client_cert]
  $NAME.key -> Pi: /etc/sorter/certs/device.key   (chmod 600, owner sorter)   [thingsboard.client_key]
      scp $OUT/$NAME.pem $OUT/$NAME.key sorter@<pi>:/tmp/ && ssh sorter@<pi> \\
        'sudo install -o sorter -g sorter -m 644 /tmp/$NAME.pem /etc/sorter/certs/device.pem && \\
         sudo install -o sorter -g sorter -m 600 /tmp/$NAME.key /etc/sorter/certs/device.key && rm /tmp/$NAME.*'
  $NAME.pem -> ThingsBoard UI: Devices → $NAME → Manage credentials → Credentials type: X.509 → dán toàn bộ nội dung
      (cat $OUT/$NAME.pem). Xem SETUP.md mục 3.
  Khóa .key KHÔNG dán vào ThingsBoard, KHÔNG commit.
EOF
