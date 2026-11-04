#!/usr/bin/env python3
"""Kiểm tra phần cứng ESP32 theo đúng bảng sơ đồ chân SPEC 2.3.

Bảng chân cắm trên ESP32:
  - UART2 RX/TX     : GPIO 16 (RX2) & GPIO 17 (TX2) nối sang Pi chân 8 (TX) & chân 10 (RX)
  - Động cơ L298N   : ENA (GPIO 25), IN1 (GPIO 26), IN2 (GPIO 27)
  - Servo A         : GPIO 18
  - Servo B         : GPIO 19
  - LED xanh        : GPIO 32
  - LED đỏ          : GPIO 33
  - Còi Buzzer      : GPIO 23
  - HC-SR04         : TRIG (GPIO 13), ECHO (GPIO 35 qua cầu chia áp)
  - Nút E-stop      : GPIO 39

Cách chạy trên Raspberry Pi:
  python3 smart-sorter/pi/tools/test_esp32_hardware.py
  hoặc chỉ định cổng:
  python3 smart-sorter/pi/tools/test_esp32_hardware.py --port /dev/serial0
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

# Đảm bảo UTF-8
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

PI_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PI_ROOT))

from sorter.config import UartCfg
from sorter.protocol_gen import (
    MsgType,
    SortPayload,
    SortTarget,
    SortReason,
    StartPayload,
    State,
    StatusPayload,
    EvtPkgDetectedPayload
)
from sorter.uart_link import UartLink


def print_pinout_table():
    print("=" * 70)
    print(" KIỂM TRA PHẦN CỨNG THEO ĐÚNG BẢNG SƠ ĐỒ CHÂN SPEC (ESP32 <-> PI)")
    print("=" * 70)
    print("""
  +----------------------+-------------------+------------------------------+
  | Chức năng            | Chân trên ESP32   | Nối tới thiết bị             |
  +----------------------+-------------------+------------------------------+
  | UART2 RX (Nhận từ Pi)| GPIO 16 (D16/RX2) | Pi TX (GPIO 14 - Chân 8)     |
  | UART2 TX (Gửi lên Pi)| GPIO 17 (D17/TX2) | Pi RX (GPIO 15 - Chân 10)    |
  | GND chung            | GND               | Pi GND (Chân 6), L298N, Servo|
  | PWM Động cơ (ENA)    | GPIO 25 (D25)     | Driver L298N ENA             |
  | Chiều động cơ        | GPIO 26 & GPIO 27 | Driver L298N IN1, IN2        |
  | Servo A (Kho A)      | GPIO 18 (D18)     | Dây tín hiệu Servo A         |
  | Servo B (Kho B)      | GPIO 19 (D19)     | Dây tín hiệu Servo B         |
  | LED xanh             | GPIO 32 (D32)     | Chân dương LED xanh (+220R)  |
  | LED đỏ               | GPIO 33 (D33)     | Chân dương LED đỏ (+220R)    |
  | Còi Buzzer           | GPIO 23 (D23)     | Còi qua transistor NPN       |
  | HC-SR04 TRIG / ECHO  | GPIO 13 / GPIO 35 | Cảm biến siêu âm (ECHO chia) |
  | Nút dừng E-Stop      | GPIO 39 (VN/D39)  | Tiếp điểm NC xuống GND       |
  +----------------------+-------------------+------------------------------+
""")


def main():
    parser = argparse.ArgumentParser(description="Test toàn bộ phần cứng đấu vào ESP32 qua lệnh UART từ Pi")
    parser.add_argument("--port", default="/dev/serial0", help="Cổng UART nối với ESP32 (mặc định: /dev/serial0)")
    parser.add_argument("--baud", type=int, default=115200, help="Tốc độ Baud (mặc định: 115200)")
    args = parser.parse_args()

    print_pinout_table()

    print(f"[*] Đang mở cổng UART: {args.port} (Baud: {args.baud})...")
    uart_cfg = UartCfg(port=args.port, baud=args.baud, ack_timeout_ms=300, retries=2, ping_interval_ms=500)
    
    try:
        link = UartLink(uart_cfg)
        link.start()
    except Exception as e:
        print(f"[-] LỖI: Không thể mở cổng UART {args.port}: {e}")
        print("    Mẹo trên Pi: Hãy chắc chắn đã bật Serial Port trong 'sudo raspi-config' và tắt Serial Console.")
        sys.exit(1)

    print("[*] Đang gửi lệnh PING để kiểm tra kết nối với ESP32...")
    connected = False
    for attempt in range(1, 6):
        res = link.ping()
        if res.ok and link.state is not None:
            connected = True
            print(f"[+] KẾT NỐI THÀNH CÔNG VỚI ESP32! Trạng thái hiện tại: {link.state.name}")
            break
        print(f"    Thử lần {attempt}/5... (chưa thấy ESP32 phản hồi)")
        time.sleep(0.5)

    if not connected:
        print("\n[-] KHÔNG NHẬN ĐƯỢC TÍN HIỆU TỪ ESP32!")
        print("    Vui lòng kiểm tra 3 điểm sau:")
        print("    1. Đã cắm Pi TX (chân vật lý 8) sang ESP32 RX2 (chân D16) chưa?")
        print("    2. Đã cắm Pi RX (chân vật lý 10) sang ESP32 TX2 (chân D17) chưa?")
        print("    3. ĐÃ CẮM DÂY GND CHUNG GIỮA PI VÀ ESP32 CHƯA? (Rất quan trọng)")
        print("    4. Board ESP32 đã được cấp nguồn và nạp firmware chưa?")
        link.stop()
        sys.exit(1)

    # Đưa ESP32 về trạng thái IDLE
    st = link.get_status()
    if link.state in (State.ESTOP, State.FAULT):
        print(f"[*] ESP32 đang ở trạng thái {link.state.name}, đang gửi ESTOP_RESET...")
        link.send_command(MsgType.ESTOP_RESET)
        time.sleep(0.5)
    elif link.state == State.RUNNING:
        link.send_command(MsgType.STOP)
        time.sleep(0.5)

    print("\n" + "=" * 50)
    print(" BẮT ĐẦU TEST TỪNG LINH KIỆN CỦA BẠN TRÊN ESP32")
    print("=" * 50)

    # 1. TEST ĐỘNG CƠ BĂNG CHUYỀN (L298N: ENA=GPIO25, IN1=GPIO26, IN2=GPIO27)
    print("\n[1/5] TEST ĐỘNG CƠ BĂNG CHUYỀN (L298N: GPIO 25 PWM, GPIO 26 & 27)...")
    print("      -> Gửi lệnh START (tốc độ 60%). Động cơ sẽ quay trong 2.5 giây...")
    r = link.send_command(MsgType.START, StartPayload(60))
    if r.ok:
        print("      [+] ESP32 đã nhận lệnh START: ĐỘNG CƠ ĐANG CHẠY...")
        time.sleep(2.5)
        print("      -> Gửi lệnh STOP dừng động cơ...")
        link.send_command(MsgType.STOP)
        print("      [+] Đã dừng động cơ.")
    else:
        print(f"      [-] Lỗi gửi lệnh START: {r.error}")

    time.sleep(1)

    # 2. TEST SERVO A (GPIO 18) + ĐÈN XANH (GPIO 32) + CÒI (GPIO 23)
    print("\n[2/5] TEST SERVO A (GPIO 18) + ĐÈN XANH (GPIO 32) + CÒI (GPIO 23)...")
    print("      -> Giả lập phân loại HÀNG OK VÀO KHO A (SORT_A)...")
    print("      -> Mong đợi: LED XANH sáng 300ms, CÒI kêu 1 tiếng ngắn, SERVO A gạt...")
    # Cần sang RUNNING hoặc DETECTED để nhận SORT
    link.send_command(MsgType.START, StartPayload(60))
    time.sleep(0.3)
    r = link.send_command(MsgType.SORT, SortPayload(int(SortTarget.A), int(SortReason.OK), 1))
    if r.ok:
        print("      [+] ESP32 ĐÃ KÍCH HOẠT: LED Xanh sáng, Còi bíp, Servo A gạt!")
        time.sleep(2.0)
    else:
        print(f"      [-] Không gửi được lệnh SORT A: {r.error}")

    time.sleep(1)

    # 3. TEST SERVO B (GPIO 19)
    print("\n[3/5] TEST SERVO B (GPIO 19)...")
    print("      -> Giả lập phân loại HÀNG OK VÀO KHO B (SORT_B)...")
    print("      -> Mong đợi: SERVO B gạt...")
    r = link.send_command(MsgType.SORT, SortPayload(int(SortTarget.B), int(SortReason.OK), 2))
    if r.ok:
        print("      [+] ESP32 ĐÃ KÍCH HOẠT: Servo B gạt!")
        time.sleep(2.0)
    else:
        print(f"      [-] Không gửi được lệnh SORT B: {r.error}")

    time.sleep(1)

    # 4. TEST ĐÈN ĐỎ (GPIO 33) KHI HÀNG LỖI MÓP VỠ
    print("\n[4/5] TEST ĐÈN ĐỎ (GPIO 33)...")
    print("      -> Giả lập phát hiện HỘP BỊ LỖI RÁCH/MÓP (REJECT_DEFECT)...")
    print("      -> Mong đợi: LED ĐỎ sáng 300ms, không gạt servo (hàng tự trôi vào khay loại)...")
    r = link.send_command(MsgType.SORT, SortPayload(int(SortTarget.REJECT), int(SortReason.DEFECT), 3))
    if r.ok:
        print("      [+] ESP32 ĐÃ KÍCH HOẠT: LED ĐỎ sáng!")
        time.sleep(1.5)
    else:
        print(f"      [-] Lỗi: {r.error}")

    link.send_command(MsgType.STOP)
    time.sleep(0.5)

    # 5. TEST CẢM BIẾN SIÊU ÂM (HC-SR04: TRIG GPIO 13, ECHO GPIO 35)
    print("\n[5/5] TEST CẢM BIẾN SIÊU ÂM HC-SR04 (GPIO 13 & 35)...")
    print("      -> Đang bật chế độ RUNNING để cảm biến hoạt động.")
    print("      -> HÃY ĐƯA TAY HOẶC HỘP ĐẾN TRƯỚC CẢM BIẾN (< 30 cm) trong vòng 15 giây...")
    link.send_command(MsgType.START, StartPayload(60))
    
    end_time = time.time() + 15.0
    detected = False
    while time.time() < end_time:
        ev = link.next_event(0.5)
        if ev and ev.type == MsgType.EVT_PKG_DETECTED:
            payload = EvtPkgDetectedPayload.unpack(ev.payload)
            print(f"\n      [+] THÀNH CÔNG: CẢM BIẾN ĐÃ PHÁT HIỆN VẬT! (pkg_seq={payload.pkg_seq})")
            detected = True
            break
        print(".", end="", flush=True)

    link.send_command(MsgType.STOP)
    if not detected:
        print("\n      [-] Hết thời gian 15s mà chưa thấy cảm biến báo có vật.")
        print("          Kiểm tra lại dây TRIG (D13), ECHO (D35 qua cầu trở chia áp).")

    link.stop()
    print("\n" + "=" * 70)
    print(" HOÀN THÀNH QUÁ TRÌNH KIỂM TRA PHẦN CỨNG ESP32!")
    print("=" * 70)


if __name__ == "__main__":
    main()
