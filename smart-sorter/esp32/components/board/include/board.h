/* Sơ đồ chân ESP32 DevKit theo SPEC 2.3 — dùng chung cho mọi component (E-02 → E-07, E-10).
 *
 * Lưu ý phần cứng:
 * - GPIO34, GPIO35, GPIO39 là chân CHỈ ĐỌC, không có pull-up/pull-down nội → cần điện trở kéo ngoài.
 * - E-stop (GPIO39): tiếp điểm NC xuống GND + pull-up 10 kΩ lên 3,3 V. Bình thường LOW; bấm nút
 *   hoặc đứt dây → HIGH → dừng (fail-safe).
 * - HC-SR04 ECHO 5 V phải qua cầu chia áp 1 kΩ – 2 kΩ trước khi vào GPIO35.
 * - Servo dùng nguồn 5 V riêng, GND chung với ESP32 và L298N.
 */
#ifndef BOARD_H
#define BOARD_H

/* UART2 ↔ Raspberry Pi (115200 8N1) */
#define BOARD_UART_PORT_NUM     2
#define BOARD_UART_RX_GPIO      16   /* ← Pi TX (GPIO14, chân 8) */
#define BOARD_UART_TX_GPIO      17   /* → Pi RX (GPIO15, chân 10) */

/* Động cơ băng chuyền qua L298N (E-02) */
#define BOARD_MOTOR_PWM_GPIO    25   /* ENA — LEDC 1 kHz, 10 bit */
#define BOARD_MOTOR_IN1_GPIO    26   /* IN1 = 1 khi chạy */
#define BOARD_MOTOR_IN2_GPIO    27   /* IN2 = 0 (chỉ một chiều) */

/* Servo gạt hàng (E-04) — LEDC 50 Hz, 14 bit */
#define BOARD_SERVO_A_GPIO      18
#define BOARD_SERVO_B_GPIO      19

/* Cảm biến phát hiện kiện (E-03) */
#define BOARD_TCRT_DO_GPIO      34   /* chỉ đọc, cần pull-up ngoài nếu module không có */
#define BOARD_HCSR04_TRIG_GPIO  13
#define BOARD_HCSR04_ECHO_GPIO  35   /* chỉ đọc, qua cầu chia áp */

/* Đèn và còi (E-06) */
#define BOARD_LED_GREEN_GPIO    32
#define BOARD_LED_RED_GPIO      33
#define BOARD_BUZZER_GPIO       23   /* qua transistor NPN, mức HIGH = kêu */

/* Nút dừng khẩn cấp (E-07) */
#define BOARD_ESTOP_GPIO        39   /* chỉ đọc; HIGH = dừng */

#endif /* BOARD_H */
