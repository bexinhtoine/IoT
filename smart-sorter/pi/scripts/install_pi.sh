#!/usr/bin/env bash
# P-15 / S-07 — Cài sorter-core lên Raspberry Pi OS 64-bit Bookworm. Chạy bằng sudo từ thư mục pi/:
#   sudo bash scripts/install_pi.sh
# Việc làm: user sorter (video, dialout, gpio); thư mục /etc/sorter/{secrets,certs} 700, /var/lib/sorter,
# /var/log/sorter, /opt/sorter; apt python3-picamera2 libzbar0 python3-opencv; venv --system-site-packages;
# pip -r requirements.txt; cài service. Cuối cùng in hướng dẫn bật UART (raspi-config + dtoverlay=disable-bt).
set -euo pipefail

if [[ $EUID -ne 0 ]]; then echo "Chạy bằng sudo"; exit 1; fi
SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP_DIR=/opt/sorter
CFG_DIR=/etc/sorter
DATA_DIR=/var/lib/sorter
LOG_DIR=/var/log/sorter
SVC_USER=sorter

echo "== 1. User $SVC_USER (nhóm video, dialout, gpio)"
if ! id -u "$SVC_USER" >/dev/null 2>&1; then
  useradd --system --home-dir "$DATA_DIR" --shell /usr/sbin/nologin "$SVC_USER"
fi
for g in video dialout gpio; do getent group "$g" >/dev/null && usermod -aG "$g" "$SVC_USER" || true; done

echo "== 2. Thư mục"
install -d -m 755 -o root -g root "$CFG_DIR"
install -d -m 700 -o "$SVC_USER" -g "$SVC_USER" "$CFG_DIR/secrets" "$CFG_DIR/certs"
install -d -m 750 -o "$SVC_USER" -g "$SVC_USER" "$DATA_DIR" "$DATA_DIR/defects" "$LOG_DIR"
install -d -m 755 -o root -g root "$APP_DIR" "$APP_DIR/models"

echo "== 3. Gói hệ thống (picamera2 từ apt, libzbar cho pyzbar, OpenCV, BLAS cho onnxruntime)"
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y --no-install-recommends \
  python3 python3-venv python3-pip python3-picamera2 python3-libcamera python3-opencv \
  libzbar0 libatlas-base-dev libopenblas0 sqlite3 git rsync
# libatlas-base-dev có thể không còn trên Bookworm; không coi là lỗi
true

echo "== 4. Sao chép mã nguồn vào $APP_DIR"
rsync -a --delete --exclude 'run/' --exclude '__pycache__' --exclude '.pytest_cache' \
  "$SRC_DIR/sorter" "$SRC_DIR/tools" "$SRC_DIR/tests" "$SRC_DIR/requirements.txt" "$SRC_DIR/pyproject.toml" \
  "$SRC_DIR/README.md" "$SRC_DIR/config.example.yaml" "$APP_DIR/"
chown -R root:root "$APP_DIR"

echo "== 4b. Model ONNX đã xuất sẵn (không export hoặc tải model trên Pi)"
MODEL_SRC="$SRC_DIR/../ai/models"
MODEL_FILES=(packaging_cls.onnx labels.json model_card.json box_detector.onnx detector_card.json)
models_available=true
models_installed=false
for model_file in "${MODEL_FILES[@]}"; do
  [[ -f "$MODEL_SRC/$model_file" ]] || models_available=false
  [[ ! -e "$APP_DIR/models/$model_file" ]] || models_installed=true
done
if [[ "$models_available" == true && "$models_installed" == false ]]; then
  for model_file in "${MODEL_FILES[@]}"; do
    install -m 644 -o root -g root "$MODEL_SRC/$model_file" "$APP_DIR/models/$model_file"
  done
  echo "   Đã cài classifier 3-best + detector YOLO-World; xem ai/README.md để căn ROI và kiểm tra ảnh."
else
  echo "   Giữ model đang có hoặc thiếu bộ model nguồn; copy đồng bộ ai/models/ theo ai/README.md."
fi

echo "== 5. venv (--system-site-packages để dùng picamera2/libcamera từ apt)"
if [[ ! -x "$APP_DIR/venv/bin/python" ]]; then
  python3 -m venv --system-site-packages "$APP_DIR/venv"
fi
"$APP_DIR/venv/bin/pip" install --upgrade pip wheel
"$APP_DIR/venv/bin/pip" install -r "$APP_DIR/requirements.txt"

echo "== 6. Cấu hình và khóa"
if [[ ! -f "$CFG_DIR/config.yaml" ]]; then
  install -m 640 -o root -g "$SVC_USER" "$SRC_DIR/config.example.yaml" "$CFG_DIR/config.yaml"
  echo "   Đã tạo $CFG_DIR/config.yaml từ config.example.yaml — sửa lại cho đúng (ai.backend, thingsboard...)"
fi
if [[ ! -f "$CFG_DIR/secrets/qr_hmac.key" ]]; then
  sudo -u "$SVC_USER" "$APP_DIR/venv/bin/python" "$APP_DIR/tools/make_hmac_key.py" --out "$CFG_DIR/secrets/qr_hmac.key"
  echo "   Đã sinh khóa HMAC mới. Dùng CÙNG khóa này khi in QR (tools/qr_generator.py --key ...)."
fi
chown -R "$SVC_USER:$SVC_USER" "$CFG_DIR/secrets" "$CFG_DIR/certs"
chmod 600 "$CFG_DIR/secrets/"* 2>/dev/null || true
chmod 600 "$CFG_DIR/certs/"* 2>/dev/null || true

echo "== 7. systemd"
install -m 644 "$SRC_DIR/systemd/sorter.service" /etc/systemd/system/sorter.service
systemctl daemon-reload
systemctl enable sorter.service

cat <<'EOF'

== 8. VIỆC CÒN LẠI (làm tay, rồi reboot) ==
 a) Bật UART phần cứng và tắt console nối tiếp:
      sudo raspi-config  ->  Interface Options -> Serial Port
         "Would you like a login shell accessible over serial?"  -> No
         "Would you like the serial port hardware enabled?"       -> Yes
    Hoặc sửa tay: xóa "console=serial0,115200" trong /boot/firmware/cmdline.txt
 b) Gán UART0 (PL011) cho /dev/serial0 thay vì Bluetooth — thêm vào cuối /boot/firmware/config.txt:
      enable_uart=1
      dtoverlay=disable-bt
    rồi: sudo systemctl disable hciuart
 c) Bật camera CSI (Bookworm bật sẵn libcamera; kiểm tra: rpicam-hello --list-cameras)
 d) Chép chứng chỉ ThingsBoard vào /etc/sorter/certs/ (ca.pem, device.pem, device.key; quyền 600, chủ sorter)
    Kiểm tra model AI trong /opt/sorter/models/ (packaging_cls.onnx, labels.json, model_card.json,
    box_detector.onnx, detector_card.json); bước 4b cài sẵn nếu chưa có model.
 e) Kiểm tra cấu hình:  sudo -u sorter /opt/sorter/venv/bin/python -c "from sorter.config import load_config; load_config('/etc/sorter/config.yaml'); print('OK')"
    (chạy trong /opt/sorter)
 f) sudo reboot; sau đó: sudo systemctl start sorter && journalctl -u sorter -f
    Log JSON: /var/log/sorter/sorter.jsonl ; DB: /var/lib/sorter/sorter.db
 g) Hardening thêm (S-07): sudo bash scripts/harden_pi.sh (P6)
EOF
echo "Xong."
