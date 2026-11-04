"""P9 / A-05 — Mô phỏng tấn công để kiểm thử phát hiện bất thường.

CHỈ chạy trên hệ thống của chính nhóm. Bắt buộc cờ --i-own-this-system, thiếu thì
in cảnh báo pháp lý và thoát mã 2.

Kịch bản (--scenario rpc|ssh|qr|uart|all):
  rpc : gọi RPC phương thức lạ 'dumpSecrets' 5 lần + spam 'getStatus' 15 lần trong 10 s
        qua REST API ThingsBoard (requests, luôn verify chứng chỉ, không bao giờ verify=False).
  ssh : thử đăng nhập SSH sai mật khẩu 8 lần tới --host (paramiko import lười).
  qr  : sinh 8 QR giả (chữ ký sai) vào thư mục ảnh sim bằng tools/qr_generator.py --forge
        (gọi subprocess nếu tool tồn tại); nếu không có tool thì bơm bộ đếm trong tiến trình.
  uart: mở cổng --port và ghi 20 khung UART rác sai CRC (encode_frame rồi lật byte CRC).

Mỗi kịch bản ghi {scenario, start_ms, end_ms} vào attack_log.jsonl để evaluate đối chiếu.

Lưu ý bảo mật: không bao giờ tắt xác minh TLS; mật khẩu nhập qua getpass, không ghi log.
"""
from __future__ import annotations

import argparse
import getpass
import json
import logging
import os
import secrets
import string
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

log = logging.getLogger("attack_sim")

LEGAL_WARNING = (
    "TU CHOI: Cong cu mo phong tan cong chi duoc chay tren he thong CUA CHINH BAN.\n"
    "Chay lai voi co --i-own-this-system de xac nhan ban so huu/duoc phep kiem thu he thong nay.\n"
    "Dung cong cu nay tren he thong khac co the vi pham phap luat."
)


def _now_ms() -> int:
    return int(time.time() * 1000)


def _append_log(log_path: str, scenario: str, start_ms: int, end_ms: int) -> None:
    """Ghi một dòng JSON {scenario, start_ms, end_ms} vào attack_log.jsonl."""
    rec = {"scenario": scenario, "start_ms": start_ms, "end_ms": end_ms}
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    log.info("Ghi nhật ký: %s", rec)


# ---------------------------------------------------------------------------
# Kịch bản RPC (ThingsBoard REST)
# ---------------------------------------------------------------------------
def scenario_rpc(args: argparse.Namespace, log_path: str) -> None:
    import requests  # import lười; cài bằng: pip install requests

    tb_url = args.tb_url.rstrip("/")
    verify: Any = args.ca if args.ca else True  # không bao giờ False
    password = args.password or getpass.getpass("Mat khau ThingsBoard: ")

    # Đăng nhập lấy JWT
    r = requests.post(
        f"{tb_url}/api/auth/login",
        json={"username": args.user, "password": password},
        timeout=10,
        verify=verify,
    )
    r.raise_for_status()
    token = r.json()["token"]
    headers = {"X-Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    start_ms = _now_ms()
    # 5 lần gọi phương thức lạ (ngoài whitelist) -> RPC_REJECTED
    for i in range(5):
        try:
            requests.post(
                f"{tb_url}/api/plugins/rpc/oneway/{args.device}",
                headers=headers,
                json={"method": "dumpSecrets", "params": {"i": i}},
                timeout=10,
                verify=verify,
            )
        except requests.RequestException as e:
            log.warning("Loi RPC dumpSecrets #%d: %s", i, e)
        time.sleep(0.2)

    # Spam 15 lần phương thức hợp lệ getStatus trong ~10 s -> vượt rate limit
    for i in range(15):
        try:
            requests.post(
                f"{tb_url}/api/plugins/rpc/oneway/{args.device}",
                headers=headers,
                json={"method": "getStatus", "params": {}},
                timeout=10,
                verify=verify,
            )
        except requests.RequestException as e:
            log.warning("Loi RPC getStatus #%d: %s", i, e)
        time.sleep(10.0 / 15)

    _append_log(log_path, "rpc", start_ms, _now_ms())


# ---------------------------------------------------------------------------
# Kịch bản SSH brute-force
# ---------------------------------------------------------------------------
def _random_password(n: int = 12) -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(n))


def scenario_ssh(args: argparse.Namespace, log_path: str) -> None:
    try:
        import paramiko  # import lười; cài bằng: pip install paramiko
    except ImportError:
        log.error("Thieu paramiko. Cai bang: pip install paramiko")
        return

    start_ms = _now_ms()
    attempts = args.ssh_attempts
    for i in range(attempts):
        client = paramiko.SSHClient()
        # Chỉ môi trường kiểm thử nội bộ; chấp nhận host key của chính Pi nhóm
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        try:
            client.connect(
                hostname=args.host,
                port=args.ssh_port,
                username=args.user or "pi",
                password=_random_password(),
                timeout=5,
                allow_agent=False,
                look_for_keys=False,
            )
            log.warning("Dang nhap #%d thanh cong ngoai y muon — dung lai", i)
            client.close()
            break
        except paramiko.AuthenticationException:
            log.info("Thu SSH sai mat khau #%d (nhu mong doi)", i + 1)
        except Exception as e:  # noqa: BLE001 - mang/timeout
            log.warning("Loi ket noi SSH #%d: %s", i + 1, e)
        finally:
            client.close()
        time.sleep(0.5)

    _append_log(log_path, "ssh", start_ms, _now_ms())


# ---------------------------------------------------------------------------
# Kịch bản QR giả
# ---------------------------------------------------------------------------
def scenario_qr(args: argparse.Namespace, log_path: str) -> None:
    start_ms = _now_ms()
    qr_tool = Path(__file__).resolve().parent / "qr_generator.py"
    made = False
    if qr_tool.exists():
        out_dir = args.qr_dir or "tests/fixtures/images"
        Path(out_dir).mkdir(parents=True, exist_ok=True)
        for i in range(8):
            try:
                subprocess.run(
                    [
                        sys.executable,
                        str(qr_tool),
                        "--forge",
                        "--out",
                        str(Path(out_dir) / f"forged_{i}.png"),
                    ],
                    timeout=15,
                    check=False,
                )
                made = True
            except (OSError, subprocess.TimeoutExpired) as e:
                log.warning("Loi sinh QR gia #%d: %s", i, e)
    if not made:
        # Dự phòng khi chưa có qr_generator: bơm bộ đếm trong cùng tiến trình
        # (chỉ có ý nghĩa nếu attack_sim chạy chung tiến trình với detector).
        try:
            from sorter import interfaces

            for _ in range(8):
                interfaces.metrics.inc(interfaces.F_QR_BAD_SIGNATURE)
            log.info("Khong co qr_generator — da bom 8 QR_BAD_SIGNATURE vao bo dem")
        except Exception as e:  # noqa: BLE001
            log.warning("Khong bom duoc bo dem QR: %s", e)

    _append_log(log_path, "qr", start_ms, _now_ms())


# ---------------------------------------------------------------------------
# Kịch bản UART rác (sai CRC)
# ---------------------------------------------------------------------------
def _bad_crc_frame(seq: int) -> bytes:
    """Dựng một khung hợp lệ rồi lật byte CRC cuối để thành sai CRC."""
    from sorter.protocol_gen import MsgType
    from sorter.uart_codec import encode_frame

    frame = bytearray(encode_frame(seq & 0xFF, int(MsgType.PING)))
    frame[-1] ^= 0xFF  # lật byte CRC cao -> CRC sai
    return bytes(frame)


def scenario_uart(args: argparse.Namespace, log_path: str) -> None:
    start_ms = _now_ms()
    frames = [_bad_crc_frame(i) for i in range(20)]
    if args.port:
        try:
            import serial  # import lười; cài bằng: pip install pyserial

            with serial.Serial(args.port, args.baud, timeout=1) as ser:
                for fr in frames:
                    ser.write(fr)
                    time.sleep(0.05)
            log.info("Da ghi 20 khung sai CRC ra %s", args.port)
        except Exception as e:  # noqa: BLE001
            log.warning("Khong mo duoc cong %s: %s", args.port, e)
    else:
        log.info("Khong co --port: da tao 20 khung sai CRC (che do sim, khong ghi ra dau)")

    _append_log(log_path, "uart", start_ms, _now_ms())


SCENARIOS = {
    "rpc": scenario_rpc,
    "ssh": scenario_ssh,
    "qr": scenario_qr,
    "uart": scenario_uart,
}


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="Mo phong tan cong (A-05) — chi chay tren he thong cua ban")
    ap.add_argument("--i-own-this-system", dest="owned", action="store_true",
                    help="Xac nhan ban so huu/duoc phep kiem thu he thong nay")
    ap.add_argument("--scenario", choices=["rpc", "ssh", "qr", "uart", "all"], default="all")
    ap.add_argument("--log", default="attack_log.jsonl", help="File nhat ky doi chieu")
    # RPC
    ap.add_argument("--tb-url", default="https://localhost:8443")
    ap.add_argument("--user", default=None)
    ap.add_argument("--password", default=None, help="De trong se hoi qua getpass")
    ap.add_argument("--device", default=None, help="Device ID tren ThingsBoard")
    ap.add_argument("--ca", default=None, help="Duong dan CA de verify TLS (mac dinh verify=True)")
    # SSH
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--ssh-port", type=int, default=22)
    ap.add_argument("--ssh-attempts", type=int, default=8)
    # QR
    ap.add_argument("--qr-dir", default=None)
    # UART
    ap.add_argument("--port", default=None)
    ap.add_argument("--baud", type=int, default=115200)
    return ap


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    ap = build_parser()
    args = ap.parse_args(argv)

    if not args.owned:
        print(LEGAL_WARNING, file=sys.stderr)
        return 2

    log_path = args.log
    # Xóa log cũ cho một lượt chạy mới
    if os.path.exists(log_path):
        os.remove(log_path)

    chosen = list(SCENARIOS) if args.scenario == "all" else [args.scenario]
    for name in chosen:
        log.info("=== Chay kich ban: %s ===", name)
        try:
            SCENARIOS[name](args, log_path)
        except Exception as e:  # noqa: BLE001 - mot kich ban loi khong chan cac kich ban khac
            log.error("Kich ban %s loi: %s", name, e)
    log.info("Hoan tat. Nhat ky: %s", log_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
