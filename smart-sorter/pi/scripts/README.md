# pi/scripts — script cài đặt và hardening Raspberry Pi (S-07)

| Script | Mã | Vai trò | Sửa hệ thống? |
|---|---|---|---|
| `install_pi.sh` | P3 | Tạo user `sorter`, thư mục, venv, cài gói, bật UART (module P3) | Có |
| `harden_pi.sh` | S-07 | Cứng hóa: mật khẩu, SSH chỉ key, ufw, fail2ban, quyền bí mật, tắt dịch vụ thừa, unattended-upgrades | Có (idempotent) |
| `security_audit.sh` | S-07 | Kiểm tra, in bảng PASS/FAIL/SKIP kèm lệnh kiểm chứng để chụp slide | **Không** |

Chạy trên Raspberry Pi OS Bookworm 64-bit. Cả hai script hardening/audit đều kiểm tra được cú pháp bằng `bash -n`, nhưng chỉ chạy thật được trên Pi (cần systemd, ufw, sshd).

## Thứ tự làm

```
1. install_pi.sh (P3)            → có user sorter, /etc/sorter, /var/lib/sorter, venv
2. Chép bí mật + chứng chỉ        → /etc/sorter/secrets/qr_hmac.key (tools/make_hmac_key.py)
                                    /etc/sorter/certs/{ca.pem,device.pem,device.key} (thingsboard/pki/)
3. ssh-copy-id <admin>@<IP-Pi>    → PHẢI làm trước bước 4 (script sẽ từ chối chạy nếu thiếu key)
4. sudo ./harden_pi.sh ...        → giữ phiên SSH hiện tại, mở phiên MỚI thử lại
5. sudo ./security_audit.sh ...   → chụp bảng cho slide; sửa cho đến khi không còn FAIL
```

## 1. harden_pi.sh

```bash
# Xem trước, không sửa gì
sudo ./harden_pi.sh --admin-user chinh --lan-subnet 192.168.1.0/24 --dry-run

# Chạy thật, hỏi xác nhận trước các bước có thể tự khóa mình ra ngoài
sudo ./harden_pi.sh --admin-user chinh --lan-subnet 192.168.1.0/24

# Chạy lại lần sau (không hỏi) — idempotent
sudo ./harden_pi.sh --admin-user chinh --lan-subnet 192.168.1.0/24 --yes
```

Tham số: `--admin-user` (user duy nhất được SSH, phải có `~/.ssh/authorized_keys`), `--lan-subnet` (CIDR IPv4 được phép SSH), `--yes`, `--dry-run`, `--ssh-port` (mặc định 22), `--sorter-user` (mặc định `sorter`).

Các bước và file sinh ra:

| Bước | Làm gì | File / lệnh |
|---|---|---|
| (a) | Phát hiện mật khẩu mặc định `raspberry` (so băm trong `/etc/shadow`) → `chage -d 0` bắt đổi; khóa user `pi` nếu không phải admin (hỏi); khóa mật khẩu rỗng | `passwd -S pi` |
| (b) | **Kiểm tra key trước**, rồi ghi `PasswordAuthentication no`, `KbdInteractiveAuthentication no`, `PermitRootLogin no`, `MaxAuthTries 3`, `AllowUsers <admin>`, `X11Forwarding no`; `sshd -t` rồi `systemctl reload ssh` | `/etc/ssh/sshd_config.d/99-hardening.conf` |
| (c) | ufw: deny incoming, allow outgoing, chỉ `22/tcp` từ subnet; xóa luật SSH "Anywhere" nếu có (hỏi) | `ufw status verbose` |
| (d) | fail2ban jail sshd: maxretry 5, findtime 10m, bantime 10m, `backend = systemd` (Bookworm không có `/var/log/auth.log`) | `/etc/fail2ban/jail.d/sshd.local` |
| (e) | `/etc/sorter/secrets`, `/etc/sorter/certs`: owner `sorter:sorter`, thư mục 700, file 600 | `stat -c '%a %U' ...` |
| (f) | Tắt bluetooth, avahi-daemon, cups nếu có (hỏi từng cái) | `systemctl is-enabled ...` |
| (g) | unattended-upgrades cho bản vá bảo mật, không tự reboot | `/etc/apt/apt.conf.d/20auto-upgrades`, `52sorter-no-reboot` |
| (h) | In tóm tắt và việc cần làm tiếp | — |

Mã thoát: 0 xong; 1 tham số/không phải root; 2 thiếu user admin hoặc thiếu SSH key; 3 không có tty để hỏi (dùng `--yes`); 4 `sshd -t` lỗi (đã tự khôi phục file cũ); 5 không ghi được file cấu hình.

Mọi file bị ghi đè đều được sao lưu thành `<file>.bak.<YYYYmmddHHMMSS>`.

### Lưu ý trước khi chạy

- Đang SSH bằng user nào thì đặt `--admin-user` đúng user đó (script cảnh báo nếu `$SUDO_USER` khác admin).
- Đang SSH từ IP nào thì `--lan-subnet` phải chứa IP đó (script tự kiểm tra `$SSH_CONNECTION` và cảnh báo).
- Tắt `avahi-daemon` thì Pi không phân giải được `tb.local`. Nếu `thingsboard.host` trong `/etc/sorter/config.yaml` là tên `.local`, hãy đổi sang IP tĩnh hoặc thêm dòng vào `/etc/hosts` trước, hoặc trả lời "N" khi được hỏi tắt avahi.
- Mật khẩu admin còn mặc định: script chỉ **bắt đổi ở lần đăng nhập tới** (`chage -d 0`), không tự đổi hộ; đổi ngay bằng `passwd`.
- Luật ufw chỉ áp dụng IPv4; SSH qua IPv6 bị chặn (chủ đích).

## 2. security_audit.sh

```bash
sudo ./security_audit.sh --admin-user chinh --lan-subnet 192.168.1.0/24          # bảng để chụp slide
sudo ./security_audit.sh --admin-user chinh --json > audit_$(date +%F).json       # JSON cho báo cáo
echo $?                                                                           # 1 nếu có FAIL
```

Không sửa gì. Mỗi dòng có 5 cột: số thứ tự, kết quả (PASS/FAIL/SKIP/INFO), mục, chi tiết, **lệnh kiểm chứng** (để giám khảo tự gõ lại). Chạy không `sudo` thì các mục cần root (sshd -T, /etc/shadow, ufw, fail2ban, quyền file) báo SKIP.

Các mục: mật khẩu mặc định; `pi` bị khóa; admin có key; `PasswordAuthentication`, `KbdInteractiveAuthentication`, `PermitRootLogin`, `AllowUsers`, `MaxAuthTries`, `X11Forwarding` (đọc giá trị hiệu lực bằng `sshd -T`); ufw active, default deny, chỉ 22/tcp từ subnet; fail2ban active + jail sshd; quyền `secrets`/`certs` 700/600 owner sorter; khóa HMAC 600; `sorter.service` chạy `User=sorter`, `NoNewPrivileges=yes`; bluetooth/avahi/cups tắt; unattended-upgrades; cổng TCP lắng nghe ngoài loopback chỉ 22 (`ss -tlnp`); NTP đồng bộ (`timedatectl`); phiên bản OS.

Chụp slide: chạy trong terminal rộng ≥ 160 cột (hoặc `| less -S`), hoặc dùng `--json` rồi đổ vào bảng.

## 3. Khôi phục khi lỡ mất SSH

Trước hết thử từ máy khác trong LAN đúng subnet, đúng user, bằng key: `ssh -v -i ~/.ssh/id_ed25519 <admin>@<IP-Pi>`. Dòng `-v` cho biết bị từ chối ở bước nào (no route = ufw, "Permission denied (publickey)" = key/AllowUsers, timeout = fail2ban đang ban IP).

Nếu vẫn không vào được, cắm **màn hình HDMI + bàn phím** vào Pi, đăng nhập bằng user admin (mật khẩu vẫn dùng được ở console vì chỉ SSH bị tắt mật khẩu), rồi tùy nguyên nhân:

```bash
# 1. Bị ufw chặn (IP máy bạn ngoài subnet)?
sudo ufw status numbered
sudo ufw allow from <IP-may-ban>/32 to any port 22 proto tcp     # mở tạm
# hoặc tắt hẳn tường lửa để sửa:  sudo ufw disable

# 2. Bị fail2ban ban IP (gõ sai nhiều lần)?
sudo fail2ban-client status sshd
sudo fail2ban-client set sshd unbanip <IP-may-ban>

# 3. Sai AllowUsers / chưa có key / cần bật lại mật khẩu TẠM THỜI?
sudo nano /etc/ssh/sshd_config.d/99-hardening.conf
#   - sửa dòng  AllowUsers <admin>   cho đúng user
#   - hoặc đổi  PasswordAuthentication no  →  yes  (tạm thời!)
#   - hoặc xóa hẳn file / khôi phục bản sao lưu:
#       sudo mv /etc/ssh/sshd_config.d/99-hardening.conf.bak.<thời gian> /etc/ssh/sshd_config.d/99-hardening.conf
sudo sshd -t && sudo systemctl reload ssh

# 4. Tài khoản bị khóa / hết hạn?
sudo usermod -U -e '' <user>
sudo passwd <user>
```

Sau khi vào lại được bằng key: chép key vào đúng user (`ssh-copy-id`), đặt lại `PasswordAuthentication no` và chạy `sudo ./harden_pi.sh ... --yes` rồi `security_audit.sh` để xác nhận.

Nếu không có màn hình: rút thẻ SD, cắm vào laptop, sửa file `99-hardening.conf` trong phân vùng rootfs (cần Linux đọc được ext4), hoặc đặt `PasswordAuthentication yes`; cắm lại thẻ và khởi động.
