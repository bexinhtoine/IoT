#!/usr/bin/env python3
"""Công cụ kiểm tra phần cứng GPIO trực tiếp trên Raspberry Pi.

Kiểm tra từng linh kiện lần lượt (bước qua từng bước):
  1. Đèn LED (GPIO 17 - Chân vật lý 11)
  2. Còi Buzzer (GPIO 27 - Chân vật lý 13)
  3. Động cơ Servo (GPIO 18 - Chân vật lý 12)
  4. Động cơ DC L298N (GPIO 23/24 - Chân vật lý 16 & 18)

Cách chạy trên Raspberry Pi:
  python3 smart-sorter/pi/tools/test_gpio_hardware.py
"""
import sys
import time

print("=" * 65)
print(" KIỂM TRA PHẦN CỨNG GPIO RASPBERRY PI (LED - CÒI - SERVO - ĐỘNG CƠ)")
print("=" * 65)

# 1. Kiểm tra thư viện gpiozero
try:
    import gpiozero
    from gpiozero import LED, OutputDevice, AngularServo, Motor
    print(f"[+] Đã nạp thành công gpiozero (phiên bản: {gpiozero.__version__})")
except ImportError as e:
    print(f"[-] LỖI: Chưa cài gpiozero: {e}")
    print("    Chạy: sudo apt install python3-gpiozero python3-lgpio")
    sys.exit(1)

# Định nghĩa chân BCM mặc định
LED_PIN = 17       # Chân vật lý 11
BUZZER_PIN = 27    # Chân vật lý 13
SERVO_PIN = 18     # Chân vật lý 12
MOTOR_IN1 = 23     # Chân vật lý 16
MOTOR_IN2 = 24     # Chân vật lý 18

print("\n--- BẢNG ĐỐI CHIẾU CHÂN CẮM DÂY TRÊN THANH 40-PIN PI ---")
print(f"  * ĐÈN LED   : GPIO {LED_PIN}  -> CHÂN VẬT LÝ SỐ 11 (nối qua trở 220-330 ohm)")
print(f"  * CÒI BUZZER: GPIO {BUZZER_PIN}  -> CHÂN VẬT LÝ SỐ 13")
print(f"  * SERVO GẠT : GPIO {SERVO_PIN}  -> CHÂN VẬT LÝ SỐ 12 (Dây cam/vàng tín hiệu)")
print(f"  * ĐỘNG CƠ   : GPIO {MOTOR_IN1}  -> CHÂN VẬT LÝ SỐ 16 (L298N IN1)")
print(f"                GPIO {MOTOR_IN2}  -> CHÂN VẬT LÝ SỐ 18 (L298N IN2)")
print("  * DÂY GND   : CHÂN VẬT LÝ SỐ 6, 9, 14, 20, 25, 30, 34 hoặc 39")
print("  [LƯU Ý QUAN TRỌNG]: TẤT CẢ DÂY GND (MASS) PHẢI ĐƯỢC NỐI CHUNG VỚI PI!")
print("-" * 65)

# BƯỚC 1: TEST ĐÈN LED
print("\n[1/4] ĐANG TEST ĐÈN LED (GPIO 17 - Chân 11)...")
try:
    led = LED(LED_PIN)
    print("      -> BẬT ĐÈN LED (Sáng trong 2 giây)...")
    led.on()
    time.sleep(2)
    print("      -> TẮT ĐÈN LED")
    led.off()
    led.close()
    print("      => Xong bài test Đèn LED.")
except Exception as e:
    print(f"      [-] Lỗi điều khiển LED: {e}")

time.sleep(1)

# BƯỚC 2: TEST CÒI BUZZER
print("\n[2/4] ĐANG TEST CÒI BUZZER (GPIO 27 - Chân 13)...")
try:
    buzzer = OutputDevice(BUZZER_PIN, active_high=True, initial_value=False)
    print("      -> BÍP 1 TIẾNG (0.3 giây)...")
    buzzer.on()
    time.sleep(0.3)
    buzzer.off()
    time.sleep(0.3)
    print("      -> BÍP 2 TIẾNG...")
    buzzer.on()
    time.sleep(0.15)
    buzzer.off()
    time.sleep(0.15)
    buzzer.on()
    time.sleep(0.15)
    buzzer.off()
    buzzer.close()
    print("      => Xong bài test Còi Buzzer.")
except Exception as e:
    print(f"      [-] Lỗi điều khiển Còi: {e}")

time.sleep(1)

# BƯỚC 3: TEST SERVO
print("\n[3/4] ĐANG TEST SERVO (GPIO 18 - Chân 12)...")
try:
    servo = AngularServo(
        SERVO_PIN,
        min_angle=0,
        max_angle=180,
        initial_angle=0,
        min_pulse_width=0.0005,
        max_pulse_width=0.0025,
    )
    print("      -> Đưa servo về góc 0 độ...")
    servo.angle = 0
    time.sleep(1)
    print("      -> Xoay servo sang góc 45 độ (Vị trí gạt hộp)...")
    servo.angle = 45
    time.sleep(1.5)
    print("      -> Xoay servo sang góc 90 độ...")
    servo.angle = 90
    time.sleep(1)
    print("      -> Trả servo về vị trí ban đầu (0 độ)...")
    servo.angle = 0
    time.sleep(1)
    servo.close()
    print("      => Xong bài test Servo.")
except Exception as e:
    print(f"      [-] Lỗi điều khiển Servo: {e}")

time.sleep(1)

# BƯỚC 4: TEST ĐỘNG CƠ DC (L298N)
print("\n[4/4] ĐANG TEST ĐỘNG CƠ DC BĂNG CHUYỀN (GPIO 23 & 24 - Chân 16 & 18)...")
try:
    motor = Motor(forward=MOTOR_IN1, backward=MOTOR_IN2)
    print("      -> ĐỘNG CƠ QUAY THUẬN (Chạy trong 2 giây)...")
    motor.forward()
    time.sleep(2)
    print("      -> DỪNG ĐỘNG CƠ...")
    motor.stop()
    motor.close()
    print("      => Xong bài test Động cơ.")
except Exception as e:
    print(f"      [-] Lỗi điều khiển Động cơ: {e}")

print("\n" + "=" * 65)
print(" HOÀN THÀNH QUÁ TRÌNH KIỂM TRA PHẦN CỨNG!")
print("=" * 65)
print("Kiểm tra kết quả thực tế trên mô hình:")
print(" 1. Nếu Đèn / Còi / Servo / Động cơ KHÔNG phản hồi:")
print("    -> Kiểm tra xem đã cắm đúng SỐ CHÂN VẬT LÝ chưa (xem bảng ở trên).")
print("    -> Kiểm tra đã nối DÂY GND (MASS) chung giữa Pi và thiết bị chưa.")
print("    -> Động cơ và Servo phải có NGUỒN NGOÀI CẤP RIÊNG (Pi không đủ dòng nuôi motor).")
