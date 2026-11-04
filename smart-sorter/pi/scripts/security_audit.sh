#!/usr/bin/env bash
# =============================================================================
# S-07 — security_audit.sh: kiểm tra trạng thái hardening của Raspberry Pi
#
# KHÔNG SỬA GÌ. Chỉ đọc cấu hình và in bảng PASS/FAIL/SKIP từng mục kèm cột
# "lệnh kiểm chứng" để chụp vào slide (S-07). Exit 1 nếu có mục FAIL.
#
# Cách dùng:
#   sudo ./security_audit.sh [--admin-user <tên>] [--lan-subnet 192.168.1.0/24] [--json]
#
#   --admin-user   user được phép SSH (mặc định: $SUDO_USER hoặc $USER)
#   --lan-subnet   dải LAN mong đợi trong luật ufw (nếu bỏ trống: chỉ cần không phải Anywhere)
#   --sorter-user  user chạy dịch vụ (mặc định sorter)
#   --json         xuất JSON (mảng {id,name,status,detail,command}) thay vì bảng
#
# Nên chạy bằng sudo: một số mục (sshd -T, /etc/shadow, ufw, fail2ban) cần root,
# không có root thì các mục đó báo SKIP.
# =============================================================================
set -euo pipefail

ADMIN_USER="${SUDO_USER:-${USER:-}}"
LAN_SUBNET=""
SORTER_USER="sorter"
SERVICE="sorter.service"
JSON=0

SECRETS_DIR=/etc/sorter/secrets
CERTS_DIR=/etc/sorter/certs
SORTER_CONFIG=/etc/sorter/config.yaml
HMAC_KEY_DEFAULT=$SECRETS_DIR/qr_hmac.key

usage() { sed -n '2,19p' "$0" | sed 's/^# \{0,1\}//'; exit "${1:-0}"; }

while [[ $# -gt 0 ]]; do
  case "$1" in
    --admin-user)  ADMIN_USER=${2:-}; shift 2 ;;
    --lan-subnet)  LAN_SUBNET=${2:-}; shift 2 ;;
    --sorter-user) SORTER_USER=${2:-}; shift 2 ;;
    --json)        JSON=1; shift ;;
    --help|-h)     usage 0 ;;
    *) printf 'Tham số lạ: %s\n' "$1" >&2; usage 1 ;;
  esac
done

IS_ROOT=0; [[ $EUID -eq 0 ]] && IS_ROOT=1

# ----------------------------------------------------------------------------
# Ghi kết quả. Mỗi phần tử RESULTS: status<TAB>name<TAB>detail<TAB>command
# status ∈ PASS | FAIL | SKIP | INFO (INFO không tính vào exit code)
# ----------------------------------------------------------------------------
RESULTS=()
N_PASS=0; N_FAIL=0; N_SKIP=0

record() {
  local status=$1 name=$2 detail=$3 cmd=$4
  # bỏ tab/xuống dòng trong detail để không vỡ bảng
  detail=$(printf '%s' "$detail" | tr '\t\n' '  ')
  RESULTS+=("$status"$'\t'"$name"$'\t'"$detail"$'\t'"$cmd")
  case $status in
    PASS) N_PASS=$((N_PASS + 1)) ;;
    FAIL) N_FAIL=$((N_FAIL + 1)) ;;
    SKIP) N_SKIP=$((N_SKIP + 1)) ;;
  esac
}

# pf <điều kiện 0/1> <tên> <detail> <lệnh>: PASS nếu $1 == 0
pf() { if [[ $1 -eq 0 ]]; then record PASS "$2" "$3" "$4"; else record FAIL "$2" "$3" "$4"; fi; }

need_root() { # SKIP mục khi không có root
  if (( ! IS_ROOT )); then record SKIP "$1" "cần sudo" "$2"; return 1; fi
  return 0
}

# Giá trị hiệu lực của sshd (sau khi gộp mọi file Include)
SSHD_T=""
sshd_opt() { printf '%s\n' "$SSHD_T" | awk -v k="$1" 'tolower($1)==k {sub(/^[^ ]+ /,""); print; exit}'; }

# ----------------------------------------------------------------------------
# Các mục kiểm tra
# ----------------------------------------------------------------------------
chk_os() {
  local pretty="?"
  # shellcheck disable=SC1091
  [[ -r /etc/os-release ]] && pretty=$(. /etc/os-release && printf '%s' "${PRETTY_NAME:-?}")
  record INFO "Phiên bản OS / kernel" "$pretty, $(uname -m), $(uname -r)" "cat /etc/os-release; uname -mr"
}

chk_default_password() {
  local name="Không còn mật khẩu mặc định (pi/raspberry)"
  local cmd="sudo getent shadow pi | cut -d: -f2 | cut -c1-4 ; passwd -S pi"
  need_root "$name" "$cmd" || return 0
  command -v python3 >/dev/null 2>&1 || { record SKIP "$name" "thiếu python3" "$cmd"; return 0; }
  local u bad=() locked=()
  for u in pi "$ADMIN_USER"; do
    [[ -n $u ]] && getent passwd "$u" >/dev/null || continue
    local hash
    hash=$(getent shadow "$u" | cut -d: -f2)
    if [[ -z $hash || $hash == '!'* || $hash == '*'* ]]; then locked+=("$u"); continue; fi
    if python3 -W ignore - "$hash" <<'PY'
import sys, warnings
warnings.filterwarnings("ignore")
import crypt
h = sys.argv[1]
sys.exit(0 if crypt.crypt("raspberry", h) == h else 1)
PY
    then bad+=("$u"); fi
  done
  if (( ${#bad[@]} )); then
    record FAIL "$name" "vẫn là 'raspberry': ${bad[*]}" "$cmd"
  else
    record PASS "$name" "không user nào dùng 'raspberry'${locked:+; bị khóa: ${locked[*]}}" "$cmd"
  fi
}

chk_pi_locked() {
  local name="Tài khoản 'pi' bị khóa (nếu không phải admin)"
  local cmd="passwd -S pi"
  if ! getent passwd pi >/dev/null; then record PASS "$name" "không có user pi" "$cmd"; return 0; fi
  if [[ $ADMIN_USER == pi ]]; then record INFO "$name" "pi chính là admin" "$cmd"; return 0; fi
  need_root "$name" "$cmd" || return 0
  local st; st=$(passwd -S pi 2>/dev/null | awk '{print $2}')
  pf "$([[ $st == L* ]] && echo 0 || echo 1)" "$name" "passwd -S: $st" "$cmd"
}

chk_sshd() {
  local cmd="sudo sshd -T | grep -iE 'passwordauth|kbdinteractive|permitroot|allowusers|maxauthtries'"
  if ! need_root "SSH PasswordAuthentication no" "$cmd"; then
    record SKIP "SSH KbdInteractiveAuthentication no" "cần sudo" "$cmd"
    record SKIP "SSH PermitRootLogin no" "cần sudo" "$cmd"
    record SKIP "SSH AllowUsers chỉ admin" "cần sudo" "$cmd"
    record SKIP "SSH MaxAuthTries ≤ 3" "cần sudo" "$cmd"
    return 0
  fi
  if ! SSHD_T=$(sshd -T 2>/dev/null); then
    record FAIL "SSH sshd -T chạy được" "sshd -T lỗi (cấu hình hỏng?)" "sudo sshd -t"
    return 0
  fi
  local v
  v=$(sshd_opt passwordauthentication);      pf "$([[ $v == no ]] && echo 0 || echo 1)" "SSH PasswordAuthentication no" "= $v" "$cmd"
  v=$(sshd_opt kbdinteractiveauthentication); pf "$([[ $v == no ]] && echo 0 || echo 1)" "SSH KbdInteractiveAuthentication no" "= $v" "$cmd"
  v=$(sshd_opt permitrootlogin);             pf "$([[ $v == no ]] && echo 0 || echo 1)" "SSH PermitRootLogin no" "= $v" "$cmd"
  v=$(sshd_opt allowusers)
  if [[ -z $v ]]; then
    record FAIL "SSH AllowUsers chỉ admin" "AllowUsers chưa đặt" "$cmd"
  elif [[ " $v " == *" $ADMIN_USER "* && $(wc -w <<<"$v") -eq 1 ]]; then
    record PASS "SSH AllowUsers chỉ admin" "= $v" "$cmd"
  else
    record FAIL "SSH AllowUsers chỉ admin" "= $v (mong đợi chỉ '$ADMIN_USER')" "$cmd"
  fi
  v=$(sshd_opt maxauthtries); pf "$([[ ${v:-99} -le 3 ]] && echo 0 || echo 1)" "SSH MaxAuthTries ≤ 3" "= $v" "$cmd"
  v=$(sshd_opt x11forwarding); pf "$([[ $v == no ]] && echo 0 || echo 1)" "SSH X11Forwarding no" "= $v" "sudo sshd -T | grep -i x11forwarding"
  record INFO "SSH drop-in 99-hardening.conf" "$([[ -f /etc/ssh/sshd_config.d/99-hardening.conf ]] && echo 'có' || echo 'KHÔNG có')" "ls -l /etc/ssh/sshd_config.d/"
}

chk_admin_key() {
  local name="Admin '$ADMIN_USER' có SSH key"
  local home ak cmd
  home=$(getent passwd "$ADMIN_USER" 2>/dev/null | cut -d: -f6 || true)
  ak="$home/.ssh/authorized_keys"
  cmd="wc -l $ak"
  if [[ -z $home ]]; then record FAIL "$name" "user không tồn tại" "$cmd"; return 0; fi
  if [[ ! -r $ak ]]; then record "$( (( IS_ROOT )) && echo FAIL || echo SKIP)" "$name" "không đọc được $ak" "$cmd"; return 0; fi
  local n; n=$(grep -cE '^[[:space:]]*([a-z0-9=,"-]+[[:space:]]+)?(ssh-|ecdsa-|sk-)' "$ak" || true)
  pf "$([[ $n -ge 1 ]] && echo 0 || echo 1)" "$name" "$n key trong $ak" "$cmd"
}

chk_ufw() {
  local cmd="sudo ufw status verbose"
  if ! command -v ufw >/dev/null 2>&1; then
    record FAIL "ufw active" "ufw chưa cài" "$cmd"
    record FAIL "ufw default deny incoming" "ufw chưa cài" "$cmd"
    record FAIL "ufw chỉ mở 22/tcp từ LAN" "ufw chưa cài" "$cmd"
    return 0
  fi
  if ! need_root "ufw active" "$cmd"; then
    record SKIP "ufw default deny incoming" "cần sudo" "$cmd"
    record SKIP "ufw chỉ mở 22/tcp từ LAN" "cần sudo" "$cmd"
    return 0
  fi
  local st; st=$(ufw status verbose 2>/dev/null || true)
  pf "$(grep -q '^Status: active' <<<"$st" && echo 0 || echo 1)" "ufw active" "$(grep -m1 '^Status:' <<<"$st")" "$cmd"
  pf "$(grep -q '^Default: deny (incoming)' <<<"$st" && echo 0 || echo 1)" "ufw default deny incoming" "$(grep -m1 '^Default:' <<<"$st")" "$cmd"

  # Mọi luật ALLOW phải là 22/tcp và nguồn không phải Anywhere (đúng subnet nếu có --lan-subnet)
  local rules bad="" line to action from
  rules=$(ufw status 2>/dev/null | grep -E 'ALLOW' || true)
  while IFS= read -r line; do
    [[ -z $line ]] && continue
    # cột: To | Action [IN/OUT] | From [# comment]
    to=$(awk '{print $1}' <<<"$line")
    if grep -qE 'ALLOW[[:space:]]+(IN|OUT)' <<<"$line"; then
      action=$(awk '{print $2" "$3}' <<<"$line"); from=$(awk '{print $4}' <<<"$line")
    else
      action=$(awk '{print $2}' <<<"$line"); from=$(awk '{print $3}' <<<"$line")
    fi
    [[ $action == *OUT* ]] && continue
    if [[ $to != 22/tcp && $to != 22 ]]; then bad+="[$to←$from] "; continue; fi
    if [[ $from == Anywhere* ]]; then bad+="[$to←Anywhere] "; continue; fi
    if [[ -n $LAN_SUBNET && $from != "$LAN_SUBNET" ]]; then bad+="[$to←$from≠$LAN_SUBNET] "; fi
  done <<<"$rules"
  if [[ -z $rules ]]; then
    record FAIL "ufw chỉ mở 22/tcp từ LAN" "không có luật ALLOW nào (SSH bị chặn?)" "$cmd"
  elif [[ -z $bad ]]; then
    record PASS "ufw chỉ mở 22/tcp từ LAN" "$(tr -s ' ' <<<"$rules" | tr '\n' ';')" "$cmd"
  else
    record FAIL "ufw chỉ mở 22/tcp từ LAN" "luật sai: $bad" "$cmd"
  fi
}

chk_fail2ban() {
  local cmd="systemctl is-active fail2ban; sudo fail2ban-client status sshd"
  local act; act=$(systemctl is-active fail2ban 2>/dev/null || true)
  pf "$([[ $act == active ]] && echo 0 || echo 1)" "fail2ban active" "= $act" "$cmd"
  need_root "fail2ban jail sshd" "$cmd" || return 0
  local out
  if out=$(fail2ban-client status sshd 2>/dev/null); then
    local banned; banned=$(grep -m1 'Currently banned' <<<"$out" | awk -F: '{gsub(/ /,"",$2); print $2}')
    record PASS "fail2ban jail sshd" "jail sshd chạy, đang ban: ${banned:-0}" "$cmd"
  else
    record FAIL "fail2ban jail sshd" "fail2ban-client status sshd lỗi" "$cmd"
  fi
}

chk_dir_perm() { # <tên> <thư mục>
  local name=$1 d=$2 cmd
  cmd="stat -c '%a %U:%G %n' $d; sudo find $d -type f -exec stat -c '%a %U %n' {} +"
  if [[ ! -d $d ]]; then record FAIL "$name" "thiếu thư mục $d" "$cmd"; return 0; fi
  local mo; mo=$(stat -c '%a %U' "$d" 2>/dev/null || echo "? ?")
  if [[ $mo != "700 $SORTER_USER" ]]; then record FAIL "$name" "thư mục: $mo (mong đợi 700 $SORTER_USER)" "$cmd"; return 0; fi
  need_root "$name" "$cmd" || return 0
  local badf nf
  nf=$(find "$d" -type f 2>/dev/null | wc -l)
  badf=$(find "$d" -type f \( ! -perm 600 -o ! -user "$SORTER_USER" \) -exec stat -c '%a %U %n' {} + 2>/dev/null || true)
  if [[ -n $badf ]]; then
    record FAIL "$name" "file sai quyền: $(tr '\n' ';' <<<"$badf")" "$cmd"
  else
    record PASS "$name" "thư mục 700 $SORTER_USER, $nf file đều 600 $SORTER_USER" "$cmd"
  fi
}

chk_hmac_key() {
  local name="Khóa HMAC QR quyền 600 (S-03)"
  local key=$HMAC_KEY_DEFAULT
  if [[ -r $SORTER_CONFIG ]]; then
    local k; k=$(awk '/^qr:/{f=1} f && /^[[:space:]]+hmac_key_file:/{print $2; exit}' "$SORTER_CONFIG" 2>/dev/null || true)
    [[ -n $k ]] && key=$k
  fi
  local cmd="stat -c '%a %U %n' $key"
  if [[ ! -e $key ]]; then
    if (( IS_ROOT )); then record FAIL "$name" "thiếu $key (tools/make_hmac_key.py)" "$cmd"; else record SKIP "$name" "không thấy/không đọc được $key" "$cmd"; fi
    return 0
  fi
  local mo; mo=$(stat -c '%a %U' "$key" 2>/dev/null || echo "? ?")
  pf "$([[ $mo == "600 $SORTER_USER" ]] && echo 0 || echo 1)" "$name" "$key: $mo" "$cmd"
}

chk_service() {
  local cmd="systemctl show -p User -p NoNewPrivileges -p ProtectSystem -p ActiveState $SERVICE"
  local load; load=$(systemctl show -p LoadState --value "$SERVICE" 2>/dev/null || true)
  if [[ $load != loaded ]]; then
    record SKIP "Dịch vụ $SERVICE chạy bằng user $SORTER_USER" "unit chưa cài (LoadState=$load)" "$cmd"
    record SKIP "Dịch vụ $SERVICE NoNewPrivileges=yes" "unit chưa cài" "$cmd"
    record SKIP "Dịch vụ $SERVICE đang chạy" "unit chưa cài" "$cmd"
    return 0
  fi
  local u nnp act ps
  u=$(systemctl show -p User --value "$SERVICE" 2>/dev/null || true)
  nnp=$(systemctl show -p NoNewPrivileges --value "$SERVICE" 2>/dev/null || true)
  ps=$(systemctl show -p ProtectSystem --value "$SERVICE" 2>/dev/null || true)
  act=$(systemctl is-active "$SERVICE" 2>/dev/null || true)
  pf "$([[ $u == "$SORTER_USER" ]] && echo 0 || echo 1)" "Dịch vụ $SERVICE chạy bằng user $SORTER_USER" "User=$u" "$cmd"
  pf "$([[ $nnp == yes ]] && echo 0 || echo 1)" "Dịch vụ $SERVICE NoNewPrivileges=yes" "NoNewPrivileges=$nnp, ProtectSystem=$ps" "$cmd"
  record INFO "Dịch vụ $SERVICE đang chạy" "ActiveState=$act" "systemctl status $SERVICE"
}

chk_svc_off() { # <unit>
  local svc=$1 name cmd
  name="Dịch vụ $svc tắt"
  cmd="systemctl is-enabled $svc; systemctl is-active $svc"
  if ! systemctl list-unit-files --no-legend "$svc.service" 2>/dev/null | grep -q .; then
    record PASS "$name" "không cài" "$cmd"; return 0
  fi
  local en act
  en=$(systemctl is-enabled "$svc.service" 2>/dev/null || true)
  act=$(systemctl is-active "$svc.service" 2>/dev/null || true)
  pf "$([[ $en =~ ^(disabled|masked)$ && $act != active ]] && echo 0 || echo 1)" "$name" "$en, $act" "$cmd"
}

chk_unattended() {
  local name="unattended-upgrades bật" cmd="apt-config dump | grep Periodic; systemctl is-active apt-daily-upgrade.timer"
  if ! dpkg -s unattended-upgrades >/dev/null 2>&1; then record FAIL "$name" "gói chưa cài" "$cmd"; return 0; fi
  local on timer
  on=$(apt-config dump 2>/dev/null | grep -E '^APT::Periodic::Unattended-Upgrade ' | awk '{print $2}' | tr -d '";' || true)
  timer=$(systemctl is-active apt-daily-upgrade.timer 2>/dev/null || true)
  pf "$([[ $on == 1 && $timer == active ]] && echo 0 || echo 1)" "$name" "Unattended-Upgrade=${on:-?}, apt-daily-upgrade.timer=$timer" "$cmd"
}

chk_listen() {
  local name="Cổng TCP lắng nghe ngoài loopback chỉ có 22" cmd="ss -tlnp"
  command -v ss >/dev/null 2>&1 || { record SKIP "$name" "thiếu ss" "$cmd"; return 0; }
  local ports
  ports=$(ss -H -tln 2>/dev/null | awk '{print $4}' \
    | grep -vE '^(127\.|\[::1\]|::1|\[::ffff:127\.)' \
    | sed -E 's/.*:([0-9]+)$/\1/' | sort -un | tr '\n' ' ' || true)
  ports=${ports% }
  local extra
  extra=$(tr ' ' '\n' <<<"$ports" | grep -vxE '22' | tr '\n' ' ' || true)
  pf "$([[ -z ${extra// /} ]] && echo 0 || echo 1)" "$name" "đang lắng nghe: ${ports:-(không có)}" "$cmd"
}

chk_ntp() {
  local name="Thời gian đồng bộ NTP (QR timestamp, TLS)" cmd="timedatectl show -p NTPSynchronized -p NTP"
  command -v timedatectl >/dev/null 2>&1 || { record SKIP "$name" "thiếu timedatectl" "$cmd"; return 0; }
  local s; s=$(timedatectl show -p NTPSynchronized --value 2>/dev/null || true)
  pf "$([[ $s == yes ]] && echo 0 || echo 1)" "$name" "NTPSynchronized=$s, $(date -u +'%Y-%m-%dT%H:%M:%SZ')" "$cmd"
}

# ----------------------------------------------------------------------------
# In kết quả
# ----------------------------------------------------------------------------
json_escape() {
  local s=$1
  s=${s//\\/\\\\}; s=${s//\"/\\\"}; s=${s//$'\n'/\\n}; s=${s//$'\t'/\\t}
  printf '%s' "$s"
}

print_json() {
  local i=0 first=1 r status name detail cmd
  printf '{"host":"%s","time":"%s","admin_user":"%s","summary":{"pass":%d,"fail":%d,"skip":%d},"checks":[' \
    "$(json_escape "$(hostname)")" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$(json_escape "$ADMIN_USER")" "$N_PASS" "$N_FAIL" "$N_SKIP"
  for r in "${RESULTS[@]}"; do
    i=$((i + 1))
    IFS=$'\t' read -r status name detail cmd <<<"$r"
    (( first )) || printf ','
    first=0
    printf '{"id":%d,"name":"%s","status":"%s","detail":"%s","command":"%s"}' \
      "$i" "$(json_escape "$name")" "$status" "$(json_escape "$detail")" "$(json_escape "$cmd")"
  done
  printf ']}\n'
}

print_table() {
  local C_RED="" C_GREEN="" C_YELLOW="" C_BOLD="" C_RESET=""
  if [[ -t 1 ]]; then
    C_RED=$'\e[31m'; C_GREEN=$'\e[32m'; C_YELLOW=$'\e[33m'; C_BOLD=$'\e[1m'; C_RESET=$'\e[0m'
  fi
  printf '%sSmart Sorter — security audit (S-07)%s  host=%s  admin=%s  %s\n' \
    "$C_BOLD" "$C_RESET" "$(hostname)" "$ADMIN_USER" "$(date +'%Y-%m-%d %H:%M')"
  (( IS_ROOT )) || printf '%s(không chạy bằng sudo: một số mục SKIP)%s\n' "$C_YELLOW" "$C_RESET"
  printf '\n'
  local body i=0 r status name detail cmd color
  body=$(printf '#\tKẾT QUẢ\tMỤC\tCHI TIẾT\tLỆNH KIỂM CHỨNG\n'
    for r in "${RESULTS[@]}"; do
      i=$((i + 1))
      IFS=$'\t' read -r status name detail cmd <<<"$r"
      printf '%d\t%s\t%s\t%s\t%s\n' "$i" "$status" "$name" "${detail:0:60}" "$cmd"
    done)
  if command -v column >/dev/null 2>&1 && column -t -s $'\t' -o ' | ' <<<"x" >/dev/null 2>&1; then
    body=$(column -t -s $'\t' -o ' | ' <<<"$body")
  fi
  while IFS= read -r r; do
    color=""
    case $r in
      *" | PASS | "*) color=$C_GREEN ;;
      *" | FAIL | "*) color=$C_RED ;;
      *" | SKIP | "*) color=$C_YELLOW ;;
    esac
    printf '%s%s%s\n' "$color" "$r" "$C_RESET"
  done <<<"$body"
  printf '\n%sTổng: PASS %d, FAIL %d, SKIP %d%s\n' "$C_BOLD" "$N_PASS" "$N_FAIL" "$N_SKIP" "$C_RESET"
  if (( N_FAIL )); then
    printf '%sCó mục FAIL → chạy: sudo %s/harden_pi.sh --admin-user %s --lan-subnet <CIDR>%s\n' \
      "$C_RED" "$(dirname "$0")" "$ADMIN_USER" "$C_RESET"
  fi
}

# ----------------------------------------------------------------------------
main() {
  chk_os
  chk_default_password
  chk_pi_locked
  chk_admin_key
  chk_sshd
  chk_ufw
  chk_fail2ban
  chk_dir_perm "Thư mục $SECRETS_DIR 700/600 owner $SORTER_USER" "$SECRETS_DIR"
  chk_dir_perm "Thư mục $CERTS_DIR 700/600 owner $SORTER_USER" "$CERTS_DIR"
  chk_hmac_key
  chk_service
  chk_svc_off bluetooth
  chk_svc_off avahi-daemon
  chk_svc_off cups
  chk_unattended
  chk_listen
  chk_ntp

  if (( JSON )); then print_json; else print_table; fi
  (( N_FAIL == 0 ))
}

main "$@"
