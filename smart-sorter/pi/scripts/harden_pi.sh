#!/usr/bin/env bash
# =============================================================================
# S-07 — harden_pi.sh: cứng hóa Raspberry Pi chạy sorter-core
#
# Chạy bằng sudo trên Raspberry Pi OS Bookworm (64-bit). Idempotent: chạy lại
# nhiều lần không làm hỏng gì (file đã đúng thì bỏ qua, lệnh đã áp dụng thì
# ufw/systemd tự báo "đã có").
#
# Cách dùng:
#   sudo ./harden_pi.sh --admin-user <tên> --lan-subnet 192.168.1.0/24 [--yes] [--dry-run]
#
#   --admin-user   user quản trị sẽ là người DUY NHẤT được SSH (phải có authorized_keys)
#   --lan-subnet   dải LAN được phép SSH vào (CIDR IPv4), ví dụ 192.168.1.0/24
#   --yes          không hỏi xác nhận (dùng khi đã chắc chắn, ví dụ chạy lại lần 2)
#   --dry-run      chỉ in các lệnh sẽ chạy, không sửa gì
#   --ssh-port     cổng SSH (mặc định 22)
#   --sorter-user  user chạy dịch vụ sorter (mặc định sorter)
#
# NGUYÊN TẮC AN TOÀN (không tự khóa mình ra ngoài):
#   1. Chỉ tắt đăng nhập mật khẩu sau khi ĐÃ THẤY key trong ~<admin>/.ssh/authorized_keys.
#   2. Luôn chạy `sshd -t` trước khi reload; lỗi cú pháp thì khôi phục file cũ.
#   3. Chỉ `reload` ssh (phiên đang mở không bị cắt) — hãy mở phiên SSH MỚI để thử
#      TRƯỚC KHI đóng phiên hiện tại.
#   4. Mỗi bước có thể khóa mình ra ngoài đều hỏi xác nhận (đọc từ /dev/tty).
#
# Các bước: (a) tài khoản/mật khẩu  (b) SSH chỉ key  (c) ufw  (d) fail2ban
#           (e) quyền /etc/sorter/{secrets,certs}  (f) tắt dịch vụ thừa
#           (g) unattended-upgrades  (h) tóm tắt
# =============================================================================
set -euo pipefail

SCRIPT_NAME=$(basename "$0")
ADMIN_USER=""
LAN_SUBNET=""
ASSUME_YES=0
DRY_RUN=0
SSH_PORT=22
SORTER_USER="sorter"

SECRETS_DIR=/etc/sorter/secrets
CERTS_DIR=/etc/sorter/certs
SORTER_CONFIG=/etc/sorter/config.yaml
SSHD_MAIN=/etc/ssh/sshd_config
SSHD_CONF=/etc/ssh/sshd_config.d/99-hardening.conf
F2B_JAIL=/etc/fail2ban/jail.d/sshd.local
APT_AUTO=/etc/apt/apt.conf.d/20auto-upgrades
APT_NOREBOOT=/etc/apt/apt.conf.d/52sorter-no-reboot

APT_UPDATED=0          # đã chạy apt-get update trong phiên này chưa
SUMMARY=()             # các dòng tóm tắt in ở bước (h)

# ----------------------------------------------------------------------------
# Log màu (tắt màu khi không phải terminal)
# ----------------------------------------------------------------------------
if [[ -t 1 ]]; then
  C_RED=$'\e[31m'; C_GREEN=$'\e[32m'; C_YELLOW=$'\e[33m'; C_BLUE=$'\e[34m'
  C_BOLD=$'\e[1m'; C_RESET=$'\e[0m'
else
  C_RED=""; C_GREEN=""; C_YELLOW=""; C_BLUE=""; C_BOLD=""; C_RESET=""
fi

log()  { printf '%s[*]%s %s\n'  "$C_BLUE"   "$C_RESET" "$*"; }
ok()   { printf '%s[OK]%s %s\n' "$C_GREEN"  "$C_RESET" "$*"; }
warn() { printf '%s[!]%s %s\n'  "$C_YELLOW" "$C_RESET" "$*" >&2; }
err()  { printf '%s[X]%s %s\n'  "$C_RED"    "$C_RESET" "$*" >&2; }
step() { printf '\n%s== %s ==%s\n' "$C_BOLD" "$*" "$C_RESET"; }
note() { SUMMARY+=("$*"); }

trap 'err "Lỗi tại dòng $LINENO: $BASH_COMMAND (mã $?)"' ERR

usage() {
  sed -n '2,30p' "$0" | sed 's/^# \{0,1\}//'
  exit "${1:-0}"
}

# ----------------------------------------------------------------------------
# Tham số dòng lệnh
# ----------------------------------------------------------------------------
while [[ $# -gt 0 ]]; do
  case "$1" in
    --admin-user)  ADMIN_USER=${2:-}; shift 2 ;;
    --lan-subnet)  LAN_SUBNET=${2:-}; shift 2 ;;
    --ssh-port)    SSH_PORT=${2:-}; shift 2 ;;
    --sorter-user) SORTER_USER=${2:-}; shift 2 ;;
    --yes|-y)      ASSUME_YES=1; shift ;;
    --dry-run|-n)  DRY_RUN=1; shift ;;
    --help|-h)     usage 0 ;;
    *) err "Tham số lạ: $1"; usage 1 ;;
  esac
done

[[ -n $ADMIN_USER ]] || { err "Thiếu --admin-user"; usage 1; }
[[ -n $LAN_SUBNET ]] || { err "Thiếu --lan-subnet (ví dụ 192.168.1.0/24)"; usage 1; }
[[ $LAN_SUBNET =~ ^[0-9]{1,3}(\.[0-9]{1,3}){3}/[0-9]{1,2}$ ]] \
  || { err "--lan-subnet phải là CIDR IPv4, ví dụ 192.168.1.0/24"; exit 1; }
[[ $SSH_PORT =~ ^[0-9]+$ ]] || { err "--ssh-port phải là số"; exit 1; }

if [[ $EUID -ne 0 ]]; then
  err "Phải chạy bằng root: sudo $SCRIPT_NAME ..."
  exit 1
fi

# ----------------------------------------------------------------------------
# Tiện ích chung
# ----------------------------------------------------------------------------

# run <lệnh...>: thực thi, hoặc chỉ in khi --dry-run
run() {
  if (( DRY_RUN )); then
    printf '%s[dry-run]%s %s\n' "$C_YELLOW" "$C_RESET" "$*"
    return 0
  fi
  log "\$ $*"
  "$@"
}

# confirm <câu hỏi>: trả 0 nếu người dùng đồng ý. --yes → luôn đồng ý.
# Đọc từ /dev/tty để vẫn hỏi được khi stdin bị chuyển hướng.
confirm() {
  local prompt=$1 ans
  if (( ASSUME_YES )); then
    log "(--yes) $prompt → đồng ý"
    return 0
  fi
  if (( DRY_RUN )); then
    log "(dry-run) sẽ hỏi: $prompt"
    return 0
  fi
  if [[ ! -r /dev/tty || ! -w /dev/tty ]]; then
    err "Không có terminal để hỏi xác nhận. Dùng --yes nếu đã chắc chắn."
    exit 3
  fi
  printf '%s[?]%s %s [y/N] ' "$C_YELLOW" "$C_RESET" "$prompt" > /dev/tty
  read -r ans < /dev/tty || ans=""
  [[ $ans =~ ^[Yy]([Ee][Ss])?$ ]]
}

# write_file <đường dẫn> <mode> <nội dung>
# Trả 0 nếu đã ghi (hoặc dry-run), 1 nếu nội dung đã đúng (không ghi).
# Sao lưu file cũ thành <path>.bak.<thời gian> trước khi ghi đè.
# Lưu ý: hàm này thường được gọi trong `if`, nơi bash TẮT set -e, nên mọi lỗi
# ghi file phải được bắt tường minh và thoát (exit 5) — không được im lặng bỏ qua.
write_file() {
  local path=$1 mode=$2 content=$3
  if [[ -f $path ]] && [[ "$(cat "$path")" == "$content" ]]; then
    ok "$path đã đúng nội dung, bỏ qua"
    return 1
  fi
  if (( DRY_RUN )); then
    printf '%s[dry-run]%s ghi %s (mode %s):\n' "$C_YELLOW" "$C_RESET" "$path" "$mode"
    printf '%s\n' "$content" | sed 's/^/    | /'
    return 0
  fi
  mkdir -p "$(dirname "$path")" || { err "Không tạo được thư mục cho $path"; exit 5; }
  if [[ -f $path ]]; then
    cp -a "$path" "$path.bak.$(date +%Y%m%d%H%M%S)" || { err "Không sao lưu được $path"; exit 5; }
  fi
  local tmp
  tmp=$(mktemp) || { err "mktemp lỗi"; exit 5; }
  printf '%s\n' "$content" > "$tmp" || { rm -f "$tmp"; err "Không ghi được file tạm"; exit 5; }
  if ! install -m "$mode" -o root -g root "$tmp" "$path"; then
    rm -f "$tmp"
    err "Không ghi được $path"
    exit 5
  fi
  rm -f "$tmp"
  ok "Đã ghi $path"
  return 0
}

# apt_install <gói...>: cài gói còn thiếu (apt-get update một lần)
apt_install() {
  local pkg missing=()
  for pkg in "$@"; do
    dpkg -s "$pkg" >/dev/null 2>&1 || missing+=("$pkg")
  done
  (( ${#missing[@]} )) || { ok "Đã cài: $*"; return 0; }
  if (( ! APT_UPDATED )); then
    run env DEBIAN_FRONTEND=noninteractive apt-get update -qq
    APT_UPDATED=1
  fi
  run env DEBIAN_FRONTEND=noninteractive apt-get install -y -qq "${missing[@]}"
}

# unit_exists <unit>: đơn vị systemd có được cài không
unit_exists() {
  systemctl list-unit-files --no-legend "$1" 2>/dev/null | grep -q .
}

# is_default_password <user>: 0 nếu mật khẩu của user là "raspberry" (mặc định Pi OS cũ)
# So sánh băm trong /etc/shadow bằng crypt của libxcrypt (hỗ trợ yescrypt/sha512).
is_default_password() {
  local user=$1 hash
  hash=$(getent shadow "$user" 2>/dev/null | cut -d: -f2) || return 1
  # rỗng, bị khóa (!), hoặc không có mật khẩu (*) → không phải mặc định
  [[ -n $hash && $hash != '!'* && $hash != '*'* ]] || return 1
  command -v python3 >/dev/null 2>&1 || return 1
  python3 -W ignore - "$hash" <<'PY'
import sys, warnings
warnings.filterwarnings("ignore")
import crypt
h = sys.argv[1]
sys.exit(0 if crypt.crypt("raspberry", h) == h else 1)
PY
}

# has_ssh_key <file>: file authorized_keys có ít nhất một khóa công khai hợp lệ
has_ssh_key() {
  local f=$1
  [[ -s $f ]] || return 1
  grep -qE '^[[:space:]]*([a-z0-9=,"-]+[[:space:]]+)?(ssh-(rsa|ed25519|dss)|ecdsa-sha2-nistp[0-9]+|sk-(ssh-ed25519|ecdsa-sha2-nistp256)@openssh\.com)[[:space:]]+[A-Za-z0-9+/]+=*' "$f"
}

# ip_in_subnet <ip> <cidr>: 0 nếu ip nằm trong dải (dùng python ipaddress)
ip_in_subnet() {
  command -v python3 >/dev/null 2>&1 || return 2
  python3 - "$1" "$2" <<'PY'
import sys, ipaddress
try:
    ip = ipaddress.ip_address(sys.argv[1])
    net = ipaddress.ip_network(sys.argv[2], strict=False)
    sys.exit(0 if ip in net else 1)
except ValueError:
    sys.exit(2)
PY
}

# ----------------------------------------------------------------------------
# (a) Tài khoản và mật khẩu
# ----------------------------------------------------------------------------
step_accounts() {
  step "(a) Tài khoản và mật khẩu mặc định"

  if ! getent passwd "$ADMIN_USER" >/dev/null; then
    err "User quản trị '$ADMIN_USER' không tồn tại. Tạo trước: sudo adduser $ADMIN_USER && sudo usermod -aG sudo $ADMIN_USER"
    exit 2
  fi

  local u
  for u in "$ADMIN_USER" pi; do
    getent passwd "$u" >/dev/null || continue
    if is_default_password "$u"; then
      warn "User '$u' đang dùng MẬT KHẨU MẶC ĐỊNH 'raspberry'."
      if [[ $u == "$ADMIN_USER" ]]; then
        # Bắt đổi mật khẩu ở lần đăng nhập tới. Đăng nhập bằng SSH key vẫn vào được
        # (PAM sẽ yêu cầu đổi ngay khi vào), nên không gây mất SSH.
        run chage -d 0 "$u"
        note "Mật khẩu '$u' là mặc định → đã bắt đổi ở lần đăng nhập tới (chage -d 0). Đổi ngay: passwd"
      fi
    else
      ok "User '$u' không dùng mật khẩu mặc định"
    fi
  done

  # Khóa tài khoản 'pi' nếu không phải user quản trị (tài khoản không dùng)
  if [[ $ADMIN_USER != pi ]] && getent passwd pi >/dev/null; then
    local st
    st=$(passwd -S pi 2>/dev/null | awk '{print $2}')
    if [[ $st == L* ]]; then
      ok "Tài khoản 'pi' đã bị khóa"
    elif confirm "Khóa tài khoản 'pi' (không dùng nữa; bạn sẽ KHÔNG đăng nhập được bằng 'pi', kể cả bằng key)?"; then
      # -L khóa mật khẩu; -e 1 (hết hạn 1970-01-02) chặn luôn đăng nhập bằng key
      run usermod -L -e 1 pi
      note "Đã khóa tài khoản 'pi' (usermod -L -e 1). Mở lại: usermod -U -e '' pi"
    else
      warn "Giữ nguyên tài khoản 'pi' theo yêu cầu"
    fi
  fi

  # Tài khoản có mật khẩu rỗng → khóa (không bao giờ có lý do để giữ)
  local empty
  empty=$(awk -F: '($2 == "") {print $1}' /etc/shadow 2>/dev/null || true)
  if [[ -n $empty ]]; then
    for u in $empty; do
      warn "User '$u' có mật khẩu RỖNG → khóa mật khẩu"
      run passwd -l "$u"
      note "Đã khóa mật khẩu rỗng của '$u'"
    done
  else
    ok "Không có tài khoản nào mật khẩu rỗng"
  fi
}

# ----------------------------------------------------------------------------
# (b) SSH: chỉ dùng key, chỉ user quản trị
# ----------------------------------------------------------------------------
step_ssh() {
  step "(b) SSH chỉ dùng key (PasswordAuthentication no, AllowUsers $ADMIN_USER)"

  local home ak
  home=$(getent passwd "$ADMIN_USER" | cut -d: -f6)
  ak="$home/.ssh/authorized_keys"

  # --- KIỂM TRA KEY TRƯỚC KHI TẮT MẬT KHẨU (bắt buộc) ---
  if ! has_ssh_key "$ak"; then
    err "KHÔNG tìm thấy SSH key trong $ak → DỪNG, không tắt đăng nhập mật khẩu."
    cat >&2 <<EOF

  Thêm key từ máy tính của bạn rồi chạy lại script:
    # trên laptop (nếu chưa có key):  ssh-keygen -t ed25519 -C "laptop-$ADMIN_USER"
    ssh-copy-id $ADMIN_USER@<IP-cua-Pi>
    # thử đăng nhập bằng key (không hỏi mật khẩu là đúng):
    ssh -o PasswordAuthentication=no $ADMIN_USER@<IP-cua-Pi> 'echo OK'

EOF
    exit 2
  fi
  local nkeys
  nkeys=$(grep -cE '^[[:space:]]*([a-z0-9=,"-]+[[:space:]]+)?(ssh-|ecdsa-|sk-)' "$ak" || true)
  ok "Tìm thấy $nkeys key trong $ak"

  # sshd (StrictModes yes) từ chối key nếu quyền thư mục quá rộng → chỉnh lại cho chắc
  run chown "$ADMIN_USER:" "$home/.ssh" "$ak"
  run chmod 700 "$home/.ssh"
  run chmod 600 "$ak"

  # Người đang chạy script (qua sudo) khác admin → sẽ tự khóa mình sau khi AllowUsers có hiệu lực
  if [[ -n ${SUDO_USER:-} && $SUDO_USER != "$ADMIN_USER" && $SUDO_USER != root ]]; then
    warn "Bạn đang đăng nhập bằng '$SUDO_USER' nhưng AllowUsers chỉ cho '$ADMIN_USER'."
    warn "Phiên hiện tại vẫn sống sau reload, nhưng lần SSH tới bằng '$SUDO_USER' sẽ BỊ TỪ CHỐI."
  fi

  # Bookworm có sẵn dòng Include ở đầu sshd_config; nếu không có thì thêm vào đầu
  # (sshd lấy giá trị ĐẦU TIÊN gặp, nên file drop-in phải được include trước mọi lệnh khác)
  if ! grep -qE '^[[:space:]]*Include[[:space:]]+/etc/ssh/sshd_config\.d/\*\.conf' "$SSHD_MAIN"; then
    warn "$SSHD_MAIN thiếu 'Include /etc/ssh/sshd_config.d/*.conf' → thêm vào đầu file"
    if (( ! DRY_RUN )); then
      cp -a "$SSHD_MAIN" "$SSHD_MAIN.bak.$(date +%Y%m%d%H%M%S)"
      sed -i '1i Include /etc/ssh/sshd_config.d/*.conf' "$SSHD_MAIN"
    else
      printf '%s[dry-run]%s sed -i "1i Include /etc/ssh/sshd_config.d/*.conf" %s\n' "$C_YELLOW" "$C_RESET" "$SSHD_MAIN"
    fi
  fi

  local content
  content=$(cat <<EOF
# S-07 — sinh bởi pi/scripts/harden_pi.sh. Khôi phục khi mất SSH: xem pi/scripts/README.md
Port $SSH_PORT
PasswordAuthentication no
KbdInteractiveAuthentication no
ChallengeResponseAuthentication no
PubkeyAuthentication yes
PermitRootLogin no
PermitEmptyPasswords no
MaxAuthTries 3
LoginGraceTime 30
AllowUsers $ADMIN_USER
X11Forwarding no
AllowAgentForwarding no
AllowTcpForwarding no
ClientAliveInterval 300
ClientAliveCountMax 2
EOF
)

  if ! confirm "Tắt đăng nhập SSH bằng mật khẩu và chỉ cho phép '$ADMIN_USER' (đã có $nkeys key)?"; then
    warn "Bỏ qua bước SSH theo yêu cầu"
    note "SSH: BỎ QUA (chưa tắt mật khẩu)"
    return 0
  fi

  local changed=0
  if write_file "$SSHD_CONF" 644 "$content"; then changed=1; fi

  (( DRY_RUN )) && { note "SSH: (dry-run) sẽ ghi $SSHD_CONF, sshd -t, systemctl reload ssh"; return 0; }

  # Kiểm tra cú pháp TRƯỚC khi reload; lỗi → khôi phục bản cũ (hoặc xóa) rồi dừng
  if ! sshd -t; then
    err "sshd -t báo lỗi cấu hình → khôi phục và KHÔNG reload"
    local last
    last=$(ls -1t "$SSHD_CONF".bak.* 2>/dev/null | head -n1 || true)
    if [[ -n $last ]]; then mv -f "$last" "$SSHD_CONF"; else rm -f "$SSHD_CONF"; fi
    sshd -t || true
    exit 4
  fi
  ok "sshd -t: cấu hình hợp lệ"

  if (( changed )); then
    run systemctl reload ssh
    note "SSH: đã áp dụng $SSHD_CONF (reload, phiên hiện tại vẫn mở)"
    warn "GIỮ phiên này mở. Mở terminal MỚI và thử: ssh $ADMIN_USER@<IP-Pi>  — vào được rồi mới đóng phiên cũ."
  else
    note "SSH: $SSHD_CONF đã đúng từ trước"
  fi
}

# ----------------------------------------------------------------------------
# (c) ufw: chặn mọi kết nối vào, chỉ SSH từ LAN
# ----------------------------------------------------------------------------
step_ufw() {
  step "(c) ufw: deny incoming, allow outgoing, SSH $SSH_PORT/tcp chỉ từ $LAN_SUBNET"

  # Người đang SSH từ ngoài dải LAN sẽ mất kết nối ở lần SSH tới (phiên hiện tại giữ được
  # nhờ conntrack, nhưng đừng trông cậy vào đó)
  if [[ -n ${SSH_CONNECTION:-} ]]; then
    local client_ip
    client_ip=${SSH_CONNECTION%% *}
    if ip_in_subnet "$client_ip" "$LAN_SUBNET"; then
      ok "Bạn đang SSH từ $client_ip, nằm trong $LAN_SUBNET"
    else
      warn "Bạn đang SSH từ $client_ip, KHÔNG nằm trong $LAN_SUBNET → sau khi bật ufw bạn sẽ không SSH lại được từ địa chỉ này!"
    fi
  fi

  apt_install ufw

  if ! confirm "Bật ufw (chặn mọi cổng vào, chỉ cho SSH $SSH_PORT/tcp từ $LAN_SUBNET)?"; then
    warn "Bỏ qua bước ufw theo yêu cầu"
    note "ufw: BỎ QUA"
    return 0
  fi

  run ufw default deny incoming
  run ufw default allow outgoing
  run ufw allow from "$LAN_SUBNET" to any port "$SSH_PORT" proto tcp comment "SSH tu LAN (S-07)"

  # Xóa luật SSH mở cho mọi nơi nếu còn sót (ví dụ từ lần cài tay trước)
  local broad
  broad=$(ufw status 2>/dev/null | grep -E "^(${SSH_PORT}(/tcp)?|OpenSSH)([[:space:]]+\(v6\))?[[:space:]]+ALLOW([[:space:]]+IN)?[[:space:]]+Anywhere" || true)
  if [[ -n $broad ]]; then
    warn "Còn luật SSH mở cho MỌI địa chỉ:"
    printf '%s\n' "$broad" | sed 's/^/    /'
    if confirm "Xóa các luật SSH 'Anywhere' này (chỉ giữ luật từ $LAN_SUBNET)?"; then
      run ufw --force delete allow "$SSH_PORT/tcp" || true
      run ufw --force delete allow "$SSH_PORT" || true
      run ufw --force delete allow OpenSSH || true
      note "ufw: đã xóa luật SSH mở cho mọi nơi"
    fi
  fi

  run ufw --force enable
  note "ufw: active, default deny incoming, chỉ $SSH_PORT/tcp từ $LAN_SUBNET (Pi chỉ kết nối RA ThingsBoard 8883 nên không cần cổng vào khác)"
}

# ----------------------------------------------------------------------------
# (d) fail2ban cho sshd
# ----------------------------------------------------------------------------
step_fail2ban() {
  step "(d) fail2ban: jail sshd (maxretry 5, bantime 10m, findtime 10m)"
  apt_install fail2ban

  # Bookworm không có /var/log/auth.log (không cài rsyslog) → bắt buộc backend=systemd
  local content
  content=$(cat <<EOF
# S-07 — sinh bởi pi/scripts/harden_pi.sh
[sshd]
enabled  = true
port     = $SSH_PORT
backend  = systemd
maxretry = 5
findtime = 10m
bantime  = 10m
# Không bao giờ khóa chính dải LAN quản trị? Bỏ comment nếu muốn (giảm giá trị demo A-04):
# ignoreip = 127.0.0.1/8 ::1 $LAN_SUBNET
EOF
)
  local changed=0
  if write_file "$F2B_JAIL" 644 "$content"; then changed=1; fi
  run systemctl enable --now fail2ban
  if (( changed )); then
    run systemctl restart fail2ban
  fi
  if (( ! DRY_RUN )); then
    sleep 1
    if fail2ban-client status sshd >/dev/null 2>&1; then
      ok "fail2ban jail sshd đang chạy"
    else
      warn "fail2ban-client status sshd chưa trả lời; kiểm tra: journalctl -u fail2ban -n 30"
    fi
  fi
  note "fail2ban: jail sshd (backend systemd, maxretry 5, bantime 10m)"
}

# ----------------------------------------------------------------------------
# (e) Quyền thư mục bí mật và chứng chỉ
# ----------------------------------------------------------------------------
step_secrets() {
  step "(e) Quyền $SECRETS_DIR và $CERTS_DIR: $SORTER_USER:$SORTER_USER, 700, file 600"

  local owner_ok=1
  if ! getent passwd "$SORTER_USER" >/dev/null; then
    warn "User '$SORTER_USER' chưa tồn tại (chạy pi/scripts/install_pi.sh trước). Chỉ chỉnh mode, chưa chown."
    owner_ok=0
  fi

  local d
  for d in "$SECRETS_DIR" "$CERTS_DIR"; do
    run mkdir -p "$d"
    run chmod 700 "$d"
    # mọi file bên trong 600 (chỉ file thường; không đụng thư mục con)
    if (( DRY_RUN )); then
      printf '%s[dry-run]%s find %s -type f -exec chmod 600 {} +\n' "$C_YELLOW" "$C_RESET" "$d"
    else
      find "$d" -type f -exec chmod 600 {} +
    fi
    if (( owner_ok )); then
      run chown -R "$SORTER_USER:$SORTER_USER" "$d"
    fi
  done
  # Thư mục cha /etc/sorter: 755 để user sorter đi vào được; config.yaml không phải bí mật
  run chmod 755 "$(dirname "$SECRETS_DIR")"

  # Nhắc file bắt buộc theo 4.6
  local f
  for f in "$SECRETS_DIR/qr_hmac.key" "$CERTS_DIR/ca.pem" "$CERTS_DIR/device.pem" "$CERTS_DIR/device.key"; do
    [[ -f $f ]] && ok "Có $f" || warn "Chưa có $f (tạo bằng tools/make_hmac_key.py / thingsboard/pki/make_device_cert.sh)"
  done
  note "Bí mật: $SECRETS_DIR, $CERTS_DIR → 700/600, owner $SORTER_USER$( (( owner_ok )) || printf ' (CHƯA chown: thiếu user)')"
}

# ----------------------------------------------------------------------------
# (f) Tắt dịch vụ không dùng
# ----------------------------------------------------------------------------
step_services() {
  step "(f) Tắt dịch vụ không dùng: bluetooth, avahi-daemon, cups"

  # Cảnh báo riêng cho avahi: tắt → Pi không phân giải được tên *.local (vd tb.local)
  local tb_host=""
  if [[ -f $SORTER_CONFIG ]]; then
    tb_host=$(awk '/^thingsboard:/{f=1} f && /^[[:space:]]+host:/{print $2; exit}' "$SORTER_CONFIG" 2>/dev/null || true)
  fi

  local svc units u enabled active
  for svc in bluetooth avahi-daemon cups; do
    if ! unit_exists "$svc.service"; then
      ok "$svc không được cài, bỏ qua"
      continue
    fi
    enabled=$(systemctl is-enabled "$svc.service" 2>/dev/null || true)
    active=$(systemctl is-active "$svc.service" 2>/dev/null || true)
    if [[ $enabled =~ ^(disabled|masked)$ && $active != active ]]; then
      ok "$svc đã tắt ($enabled, $active)"
      continue
    fi

    local q="Tắt $svc ($enabled, $active)?"
    if [[ $svc == avahi-daemon ]]; then
      q="Tắt avahi-daemon? LƯU Ý: Pi sẽ không phân giải được tên *.local."
      if [[ $tb_host == *.local ]]; then
        warn "config.yaml đang dùng thingsboard.host=$tb_host → sau khi tắt avahi phải đổi sang IP tĩnh hoặc thêm vào /etc/hosts!"
      fi
    fi
    if ! confirm "$q"; then
      warn "Giữ $svc theo yêu cầu"
      note "Dịch vụ $svc: GIỮ (theo yêu cầu)"
      continue
    fi

    units=("$svc.service")
    case $svc in
      avahi-daemon) units+=(avahi-daemon.socket) ;;
      cups)         units+=(cups.socket cups.path cups-browsed.service) ;;
    esac
    for u in "${units[@]}"; do
      unit_exists "$u" || continue
      run systemctl disable --now "$u" || warn "Không tắt được $u (bỏ qua)"
    done
    note "Dịch vụ $svc: đã disable --now"
  done
}

# ----------------------------------------------------------------------------
# (g) unattended-upgrades cho bản vá bảo mật
# ----------------------------------------------------------------------------
step_upgrades() {
  step "(g) unattended-upgrades (tự cài bản vá bảo mật, không tự khởi động lại)"
  apt_install unattended-upgrades

  # Tương đương `dpkg-reconfigure -plow unattended-upgrades` chọn Yes
  write_file "$APT_AUTO" 644 "$(cat <<'EOF'
// S-07 — sinh bởi pi/scripts/harden_pi.sh (tương đương dpkg-reconfigure -plow unattended-upgrades)
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
APT::Periodic::AutocleanInterval "7";
EOF
)" || true

  # Không tự reboot giữa buổi demo; mặc định của Debian đã giới hạn ở Debian-Security
  # (xem /etc/apt/apt.conf.d/50unattended-upgrades, mục Origins-Pattern)
  write_file "$APT_NOREBOOT" 644 "$(cat <<'EOF'
// S-07 — không tự khởi động lại Pi sau khi vá (băng chuyền đang chạy)
Unattended-Upgrade::Automatic-Reboot "false";
Unattended-Upgrade::Remove-Unused-Dependencies "true";
EOF
)" || true

  run systemctl enable --now apt-daily.timer apt-daily-upgrade.timer
  if (( ! DRY_RUN )); then
    # Kiểm tra cấu hình được apt đọc đúng
    if apt-config dump 2>/dev/null | grep -qE '^APT::Periodic::Unattended-Upgrade "1"'; then
      ok "APT::Periodic::Unattended-Upgrade = 1"
    else
      warn "apt-config không thấy Unattended-Upgrade=1; kiểm tra $APT_AUTO"
    fi
  fi
  note "unattended-upgrades: bật (bản vá bảo mật, Automatic-Reboot false). Thử khô: unattended-upgrade --dry-run -d"
}

# ----------------------------------------------------------------------------
# (h) Tóm tắt
# ----------------------------------------------------------------------------
step_summary() {
  step "(h) Tóm tắt"
  local line
  for line in "${SUMMARY[@]}"; do
    printf '  %s-%s %s\n' "$C_GREEN" "$C_RESET" "$line"
  done
  cat <<EOF

Tiếp theo:
  1. Mở terminal MỚI, thử:  ssh $ADMIN_USER@<IP-Pi>   (phải vào được bằng key, không hỏi mật khẩu)
  2. Kiểm tra:              sudo $(dirname "$0")/security_audit.sh --admin-user $ADMIN_USER --lan-subnet $LAN_SUBNET
  3. Chụp bảng PASS/FAIL cho slide S-07.
Nếu lỡ mất SSH: cắm màn hình + bàn phím, xem "Khôi phục" trong pi/scripts/README.md.
EOF
}

# ----------------------------------------------------------------------------
main() {
  step "harden_pi.sh — S-07 — admin=$ADMIN_USER subnet=$LAN_SUBNET port=$SSH_PORT $( (( DRY_RUN )) && printf '[DRY-RUN]' )$( (( ASSUME_YES )) && printf '[--yes]' )"
  step_accounts
  step_ssh
  step_ufw
  step_fail2ban
  step_secrets
  step_services
  step_upgrades
  step_summary
}

main "$@"
