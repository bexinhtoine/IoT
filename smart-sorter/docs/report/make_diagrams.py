"""Sinh 12 sơ đồ PNG cho báo cáo Word vào docs/report/assets/. Chạy: python make_diagrams.py"""
from __future__ import annotations

from pathlib import Path

from diagram_lib import (BLUE, BLUE_FILL, GREEN, GREEN_FILL, GREY, GREY_FILL, ORANGE, ORANGE_FILL, PURPLE, PURPLE_FILL, RED, RED_FILL,
                         TEAL, TEAL_FILL, arrow, bottom, box, center, label, left, lifeline, msg, new_fig, region, right, save, selfmsg, top)

OUT = Path(__file__).with_name("assets")
OUT.mkdir(exist_ok=True)


# ----------------------------------------------------------------------------- 1. Luồng dữ liệu tổng thể
def fig_dataflow():
    fig, ax = new_fig(13, 8.0, (0, 26), (0, 15.6))
    region(ax, 0.2, 6.9, 20.6, 8.4, "Raspberry Pi 4 — sorter-core (pi/sorter)", fc="#F7FAFF", ec=BLUE)
    cam = box(ax, 0.5, 11.6, 2.6, 1.6, "Pi Camera CSI", "Picamera2\nkhóa AE/AWB", fc=GREY_FILL, ec=GREY)
    s1 = box(ax, 3.7, 11.6, 2.9, 1.6, "1. Chụp ảnh", "settle 150 ms\n≤ 3 khung RGB")
    s2 = box(ax, 7.1, 11.6, 2.9, 1.6, "2. Đọc QR", "pyzbar / OpenCV\nCLAHE khi mờ")
    s3 = box(ax, 10.5, 11.6, 3.3, 1.6, "3. Xác thực QR", "regex · kho · HMAC\nthời gian · trùng")
    s4 = box(ax, 14.3, 11.6, 3.0, 1.6, "4. AI bao bì", "ROI → resize\nONNX probs/type_probs")
    s5 = box(ax, 17.8, 11.6, 2.7, 1.6, "5. Quyết định", "decide(qr, ai)\nbảng 1.2")
    for a, b in [(cam, s1), (s1, s2), (s2, s3), (s3, s4), (s4, s5)]:
        arrow(ax, right(a), left(b), color=BLUE)
    for x, t in [(5.15, "ảnh"), (8.55, "khung"), (12.15, "chuỗi QR"), (15.8, "QrResult"), (19.15, "ClassResult")]:
        label(ax, x, 13.55, t, fs=7, color=BLUE)

    defect = box(ax, 0.5, 7.9, 2.8, 1.9, "Ảnh lỗi", "defects/*.jpg\n320×240 ≤ 40 KB", fc=GREY_FILL, ec=GREY)
    db = box(ax, 3.7, 7.9, 5.6, 1.9, "SQLite sorter.db", "packages · used_codes · rpc_audit\nsecurity_events · system_events · counters", fc=GREY_FILL, ec=GREY, bfs=7.4)
    key = box(ax, 9.7, 7.3, 3.4, 1.3, "Khóa HMAC 32 byte", "secrets/, quyền 600", fc=ORANGE_FILL, ec=ORANGE, bfs=7.4)
    arrow(ax, (11.4, 8.6), (12.3, 11.6), color=ORANGE, label="đọc 1 lần", lpos=0.3, loff=(1.0, -0.1))
    arrow(ax, (11.0, 11.6), (8.2, 9.8), color=GREY, label="used_codes\n(chống trùng)", lpos=0.55, loff=(-1.3, 0.1))
    orch = box(ax, 14.3, 8.3, 6.2, 1.9, "Orchestrator (P-08)", "ghi packages, bộ đếm, ảnh lỗi, telemetry\nmọi ngoại lệ → REJECT_FAILSAFE", fc=BLUE_FILL, ec=BLUE, bfs=7.4)
    arrow(ax, bottom(s5), top(orch), color=BLUE, label="Decision", loff=(0.9, 0))
    arrow(ax, (14.3, 9.6), (9.3, 9.6), color=GREY, label="INSERT", lpos=0.3, loff=(0, 0.25))
    arrow(ax, (15.0, 8.3), (1.9, 7.9), color=GREY, dashed=True, rad=-0.18, )
    label(ax, 7.6, 7.25, "khi REJECT_DEFECT: lưu JPEG gốc + bản 320×240", fs=7, color=GREY, bg="white")

    uart = box(ax, 21.6, 11.6, 4.0, 1.6, "6. UART link (P-07)", "SORT + ACK, gửi lại ×3\nPING 500 ms, CRC16", fc=TEAL_FILL, ec=TEAL, bfs=7.4)
    arrow(ax, right(s5), left(uart), color=TEAL, label="SORT(target,\nreason, pkg_seq)", loff=(0, 0.6))
    esp = box(ax, 21.6, 7.9, 4.0, 2.4, "ESP32 (firmware)", "FSM · băng · servo A/B\nLED/còi · E-stop · OTA", fc=TEAL_FILL, ec=TEAL, bfs=7.4)
    arrow(ax, (24.9, 11.6), (24.9, 10.3), color=TEAL)
    arrow(ax, (22.3, 10.3), (22.3, 11.6), color=TEAL)
    label(ax, 23.6, 10.95, "EVT_PKG_DETECTED\nEVT_SORT_DONE · STATUS", fs=6.8, color=TEAL)
    label(ax, 23.6, 7.5, "băng chuyền · khay A / B / Loại", fs=7.5, color=TEAL, italic=True)

    region(ax, 0.2, 0.3, 25.4, 6.2, "Mạng LAN — ThingsBoard & giám sát", fc="#FBFBFB", ec="#AAAAAA")
    tele = box(ax, 3.7, 3.6, 5.2, 1.9, "ThingsBoardClient (P-10)", "paho-mqtt · TLS ≥ 1.2 · X.509\nhàng đợi 1000 khi mất mạng", fc=PURPLE_FILL, ec=PURPLE, bfs=7.4)
    arrow(ax, (15.5, 8.3), (8.9, 5.0), color=PURPLE, rad=-0.1, label="telemetry: pkg_*, cnt_*, defect_image,\nsecurity_*, cpu_temp…, ota_*", lpos=0.5, loff=(1.9, -0.35))
    tb = box(ax, 10.5, 3.3, 5.3, 2.4, "ThingsBoard CE (Docker)", "MQTT 8883 · alarm · rule chain\nAudit Logs · 2FA · phân quyền", fc=PURPLE_FILL, ec=PURPLE, bfs=7.4)
    arrow(ax, right(tele), left(tb), color=PURPLE, label="MQTT/TLS 8883\nv1/devices/me/…", loff=(0, 0.45))
    dash = box(ax, 17.4, 3.3, 4.2, 2.4, "Dashboard", "Admin: điều khiển + 2FA\nViewer: chỉ xem (sorter-view)", fc=PURPLE_FILL, ec=PURPLE, bfs=7.4)
    arrow(ax, right(tb), left(dash), color=PURPLE, label="WebSocket")
    rpc = box(ax, 10.5, 0.7, 5.3, 1.8, "RpcHandler (S-05)", "whitelist 8 method · 5 lệnh/10 s\nrpc_audit + rpc_log", fc=RED_FILL, ec=RED, bfs=7.4)
    arrow(ax, (12.0, 3.3), (12.0, 2.5), color=RED, label="RPC request", lpos=0.5, loff=(-1.3, 0))
    arrow(ax, (18.6, 3.3), (15.8, 1.9), color=RED, rad=-0.15, label="nút điều khiển", lpos=0.55, loff=(0.9, -0.3))
    arrow(ax, (15.8, 1.4), (23.6, 7.9), color=RED, rad=0.3)
    label(ax, 21.4, 1.5, "UART START/STOP/ESTOP/\nESTOP_RESET/OTA_CHECK", fs=7.2, color=RED, bg="white")
    anom = box(ax, 3.7, 0.7, 5.2, 1.9, "Isolation Forest (A-01…A-04)", "bộ đếm 10 s (metrics.inc từ orchestrator,\nrpc, uart, mqtt) → score → flag · luật RULE_*", fc=ORANGE_FILL, ec=ORANGE, bfs=7.0)
    arrow(ax, top(anom), bottom(tele), color=ORANGE, label="anomaly_score/flag/top_features", fs=7)
    save(fig, OUT / "d01_dataflow.png")


# ----------------------------------------------------------------------------- 2. Tuần tự xử lý một kiện
def fig_sequence():
    fig, ax = new_fig(12, 9.2, (0, 16), (0, 20))
    xe, xp, xt = 2.5, 8.0, 13.5
    lifeline(ax, xe, 18.8, 0.6, "ESP32 (FSM)", fc=TEAL_FILL, ec=TEAL, w=2.6)
    lifeline(ax, xp, 18.8, 0.6, "Pi sorter-core", w=2.6)
    lifeline(ax, xt, 18.8, 0.6, "ThingsBoard", fc=PURPLE_FILL, ec=PURPLE, w=2.6)
    # nền: ping/status
    region(ax, 0.6, 16.9, 10.8, 1.5, "", fc="#F6F6F6", ec="#CCCCCC")
    label(ax, 6.0, 18.1, "Nền liên tục: Pi → PING mỗi 500 ms / ESP32 → PONG · ESP32 → STATUS mỗi 1 s · Pi → ThingsBoard sức khỏe mỗi 10 s", fs=7.5, color="#444")
    label(ax, 6.0, 17.4, "Mất khung hợp lệ > 2000 ms → ESP32 FAULT(LINK_LOST), băng dừng (E-09)", fs=7.5, color=RED)
    y = 16.2
    selfmsg(ax, xe, y, "Cảm biến báo hàng → dừng băng ngay (< 20 ms)\nRUNNING → DETECTED, bắt đầu t_decision 3 s", side=1); y -= 1.1
    msg(ax, xe, xp, y, "EVT_PKG_DETECTED(pkg_seq)", color=TEAL); y -= 0.55
    msg(ax, xp, xe, y, "ACK(seq)", color=GREY, dashed=True, above=False); y -= 0.9
    selfmsg(ax, xp, y, "Chờ settle 150 ms · chụp ≤ 3 khung (10–25 ms/khung)", side=1); y -= 0.8
    selfmsg(ax, xp, y, "Đọc QR 60–100 ms · xác thực HMAC + used_codes < 1 ms", side=1); y -= 0.8
    selfmsg(ax, xp, y, "QR hợp lệ → AI ONNX trên ROI (3 ms dummy / ≤ 150 ms thật)", side=1); y -= 0.8
    selfmsg(ax, xp, y, "decide() → SORT_A / SORT_B / REJECT_*", side=1); y -= 0.9
    msg(ax, xp, xe, y, "SORT(target, reason, pkg_seq) — chờ ACK 200 ms, gửi lại tối đa 3 lần", color=BLUE); y -= 0.55
    msg(ax, xe, xp, y, "ACK (hoặc NACK BAD_STATE/BAD_PARAM)", color=GREY, dashed=True, above=False); y -= 0.95
    label(ax, 5.2, y + 0.05, "Ngân sách ≤ 1 s từ lúc dừng băng đến SORT (đo được 80–130 ms trong sim)", fs=7.5, color=BLUE, italic=True, bg="#EEF3FB"); y -= 0.85
    selfmsg(ax, xe, y, "DETECTED → SORTING: servo A/B sang góc gạt (REJECT: không gạt)\nchạy băng t_clear_x · servo về · LED/còi theo reason", side=1); y -= 1.2
    msg(ax, xe, xp, y, "EVT_SORT_DONE(pkg_seq, target)", color=TEAL); y -= 0.55
    msg(ax, xp, xe, y, "ACK", color=GREY, dashed=True, above=False); y -= 0.9
    selfmsg(ax, xp, y, "Ghi packages, counters, ảnh lỗi (nếu REJECT_DEFECT)", side=-1); y -= 0.9
    msg(ax, xp, xt, y, "telemetry: pkg_id, warehouse, qr_status, ai_label, ai_conf, decision, reason, latency_ms, cnt_*", color=PURPLE); y -= 0.75
    msg(ax, xp, xt, y, "defect_image (base64 ≤ 40 KB) + defect_label  — chỉ khi REJECT_DEFECT", color=PURPLE, dashed=True); y -= 0.75
    msg(ax, xp, xt, y, "security_alert, security_event_type, security_level, security_detail — khi REJECT_SECURITY", color=RED, dashed=True); y -= 0.9
    label(ax, 2.5, y - 0.2, "Quá 3 s không nhận SORT → ESP32 tự REJECT(FAILSAFE)\n+ EVT_FAULT(DECISION_TIMEOUT) (E-05)", fs=7.5, color=RED, bg=RED_FILL)
    label(ax, 13.5, y - 0.2, "Rule chain sao chép telemetry\nsang sorter-view cho Viewer", fs=7.5, color=PURPLE, bg=PURPLE_FILL)
    save(fig, OUT / "d02_sequence.png")


# ----------------------------------------------------------------------------- 3. Máy trạng thái ESP32
def fig_fsm():
    fig, ax = new_fig(12, 7.0, (0, 24), (0, 14.2))

    def state(x, y, name, body, fc=TEAL_FILL, ec=TEAL):
        return box(ax, x - 1.7, y - 0.95, 3.4, 1.9, name, body, fc=fc, ec=ec, fs=10)

    boot = state(2.6, 11.0, "BOOT", "init driver\nservo home")
    idle = state(8.0, 11.0, "IDLE", "băng dừng\nnhận SET_PARAM")
    run = state(14.0, 11.0, "RUNNING", "băng chạy\nsensor bật, xanh")
    det = state(20.5, 11.0, "DETECTED", "băng dừng\nchờ SORT ≤ 3 s")
    ota = state(2.6, 5.0, "OTA", "Wi-Fi bật\nchỉ ESTOP/STATUS/PING", fc=PURPLE_FILL, ec=PURPLE)
    estop = state(8.0, 5.0, "ESTOP", "PWM tắt từ ISR\nđỏ nháy + còi", fc=RED_FILL, ec=RED)
    fault = state(14.0, 5.0, "FAULT", "băng dừng\nxanh–đỏ luân phiên", fc=ORANGE_FILL, ec=ORANGE)
    sort = state(20.5, 5.0, "SORTING", "servo gạt\nchạy t_clear")

    arrow(ax, right(boot), left(idle), color=TEAL, label="init xong\n(GPIO39 HIGH → ESTOP)", loff=(0, 0.45))
    arrow(ax, right(idle), left(run), color=TEAL, label="START(speed)\nramp 200 ms", loff=(0, 0.45))
    arrow(ax, (13.0, 11.95), (9.0, 11.95), color=TEAL, rad=0.45); label(ax, 11.0, 13.6, "STOP", fs=7.5, color=TEAL, bg="white")
    arrow(ax, right(run), left(det), color=TEAL, label="cảm biến → dừng ngay\nEVT_PKG_DETECTED", loff=(0, 0.45))
    arrow(ax, (21.0, 10.05), (21.0, 5.95), color=TEAL, label="SORT hợp lệ → ACK\nhết 3 s → REJECT FAILSAFE\n+ EVT_FAULT(DECISION_TIMEOUT)", lpos=0.5, loff=(1.9, 0), fs=7.2)
    arrow(ax, (19.6, 5.95), (15.2, 10.05), color=TEAL, label="hết t_clear → servo về,\nEVT_SORT_DONE,\nkhóa sensor t_rearm", lpos=0.25, loff=(-1.5, -0.1), fs=7.2)
    arrow(ax, bottom(run), top(fault), color=ORANGE, label="LINK_LOST: không khung\nhợp lệ > 2000 ms", lpos=0.5, loff=(1.5, 0.3), fs=7.2)
    arrow(ax, left(sort), right(fault), color=ORANGE, dashed=True, label="LINK_LOST (cũng từ\nDETECTED/SORTING)", loff=(0, 0.45), fs=7.2)
    arrow(ax, (12.3, 5.95), (7.3, 7.6), color=RED, dashed=True, label="ESTOP_RESET", lpos=0.5, loff=(0, -0.35), fs=7.2)
    arrow(ax, (7.3, 5.95), (7.3, 10.05), color=RED, dashed=True, label="ESTOP_RESET\n(nút đã nhả)\nEVT_ESTOP_CLEARED", lpos=0.5, loff=(-1.6, 0), fs=7.2)
    arrow(ax, (8.7, 10.05), (8.7, 5.95), color=RED, label="", lpos=0.5)
    label(ax, 10.8, 8.6, "ESTOP (RPC) hoặc nút\nE-stop (ISR) — từ MỌI\ntrạng thái, kể cả OTA", fs=7.5, color=RED, bg=RED_FILL, bold=True)
    arrow(ax, (6.6, 10.05), (3.4, 5.95), color=PURPLE, rad=-0.15); label(ax, 1.9, 8.9, "OTA_CHECK\n(chỉ ở IDLE)", fs=7.2, color=PURPLE, bg="white")
    arrow(ax, (3.9, 5.95), (7.0, 10.05), color=PURPLE, rad=-0.15, dashed=True); label(ax, 1.9, 7.3, "REJECTED / UP_TO_DATE\n→ IDLE; REBOOTING → reset", fs=7.0, color=PURPLE, bg="white")
    label(ax, 12.0, 2.9, "Lệnh ngoài trạng thái cho phép → NACK BAD_STATE · PING/GET_STATUS được trả lời ở mọi trạng thái · ESTOP_RESET khi nút còn bấm → NACK BAD_STATE",
          fs=7.6, color="#444", italic=True)
    label(ax, 12.0, 2.2, "Một task fsm xử lý tuần tự mọi sự kiện (lệnh UART, cảm biến, E-stop, timer, link) qua một queue; timer chỉ đẩy sự kiện", fs=7.6, color="#444", italic=True)
    save(fig, OUT / "d03_fsm.png")


# ----------------------------------------------------------------------------- 4. Lưu đồ quyết định
def fig_decision():
    fig, ax = new_fig(11, 9.4, (0, 22), (0, 19))

    def dec(x, y, text, w=4.6, h=1.5):
        # hình thoi (vẽ bằng polygon)
        import matplotlib.patches as mp
        ax.add_patch(mp.Polygon([(x - w / 2, y), (x, y + h / 2), (x + w / 2, y), (x, y - h / 2)], closed=True, fc="#FFFDE7", ec=ORANGE, lw=1.3))
        label(ax, x, y, text, fs=8.5)
        return (x - w / 2, y - h / 2, w, h)

    def act(x, y, text, w=6.2, h=1.0, fc=BLUE_FILL, ec=BLUE):
        return box(ax, x - w / 2, y - h / 2, w, h, text, fc=fc, ec=ec, fs=8.5)

    def out(x, y, text, fc, ec):
        return box(ax, x - 2.3, y - 0.5, 4.6, 1.0, text, fc=fc, ec=ec, fs=9)

    cx = 7
    start = act(cx, 18.0, "EVT_PKG_DETECTED → chụp ≤ 3 khung")
    d1 = dec(cx, 16.0, "Đọc được đúng\n1 mã QR?")
    d2 = dec(cx, 13.6, "Định dạng, kho,\nchữ ký HMAC đúng?")
    d3 = dec(cx, 11.2, "Thời gian hợp lệ và\nchưa dùng (used_codes)?")
    ai = act(cx, 9.0, "AI: cắt ROI → ONNX → p_ok, p_defect")
    d4 = dec(cx, 7.0, "p_defect ≥\ndefect_threshold?")
    d5 = dec(cx, 4.6, "p_ok ≥ min_confidence?\n(không thì UNCERTAIN)")
    d6 = dec(cx, 2.2, "Kho trong QR?")
    for a, b in [(start, d1), (d1, d2), (d2, d3), (d3, ai), (ai, d4), (d4, d5), (d5, d6)]:
        arrow(ax, bottom(a), top(b), color=GREY)
    for yy, t in [(14.9, "có"), (12.5, "đúng"), (10.1, "hợp lệ"), (5.9, "không"), (3.5, "có")]:
        label(ax, cx + 0.5, yy, t, fs=7.5, color=GREEN)

    sec = out(16.0, 13.0, "REJECT_SECURITY → khay Loại", RED_FILL, RED)
    arrow(ax, right(d1), (13.7, 16.0), color=RED, label="NO_QR / MULTI_QR", loff=(0, 0.2))
    arrow(ax, (13.7, 16.0), (16.0, 13.5), color=RED)
    arrow(ax, right(d2), left(sec), color=RED, label="BAD_FORMAT · BAD_WAREHOUSE\nBAD_SIGNATURE", loff=(0, 0.45))
    arrow(ax, right(d3), (13.7, 11.2), color=RED, label="FUTURE_TIMESTAMP\nEXPIRED · DUPLICATE", loff=(0, -0.5))
    arrow(ax, (13.7, 11.2), (16.0, 12.5), color=RED)
    label(ax, 16.0, 11.6, "LED đỏ nháy + còi dài\nalarm Security alert (S-09)\nkhông chạy AI", fs=7.5, color=RED, italic=True)

    dft = out(16.0, 6.0, "REJECT_DEFECT → khay Loại", ORANGE_FILL, ORANGE)
    arrow(ax, right(d4), (13.7, 7.0), color=ORANGE, label="có (lỗi)", loff=(0, 0.2))
    arrow(ax, (13.7, 7.0), (16.0, 6.5), color=ORANGE)
    arrow(ax, right(d5), (13.7, 4.6), color=ORANGE, label="không → UNCERTAIN", loff=(0, -0.3))
    arrow(ax, (13.7, 4.6), (16.0, 5.5), color=ORANGE)
    label(ax, 16.0, 4.6, "LED đỏ 1 s · ảnh lỗi + nhãn\n(tear/dent/crush/wet) lên dashboard", fs=7.5, color=ORANGE, italic=True)

    sa = out(3.0, 0.5, "SORT_A → khay A", GREEN_FILL, GREEN)
    sb = out(11.0, 0.5, "SORT_B → khay B", GREEN_FILL, GREEN)
    arrow(ax, left(d6), (3.0, 2.2), color=GREEN, label="A", loff=(0, 0.2))
    arrow(ax, (3.0, 2.2), top(sa), color=GREEN)
    arrow(ax, right(d6), (11.0, 2.2), color=GREEN, label="B", loff=(0, 0.2))
    arrow(ax, (11.0, 2.2), top(sb), color=GREEN)
    fs_ = box(ax, 13.7, 0.2, 7.6, 1.6, "REJECT_FAILSAFE → khay Loại", "mọi ngoại lệ, timeout AI, ESP32 không ACK,\nquá 3 s không SORT (ESP32 tự loại)", fc=GREY_FILL, ec=GREY, fs=9)
    arrow(ax, (9.3, 9.0), (13.7, 1.3), color=GREY, dashed=True, rad=-0.25, label="lỗi bất kỳ\ntrên đường xử lý", lpos=0.35, loff=(1.3, 0.1))
    label(ax, 7.0, -0.6, "", fs=1)
    save(fig, OUT / "d04_decision.png")


# ----------------------------------------------------------------------------- 5. Khung UART + ACK/gửi lại
def fig_uart():
    fig, ax = new_fig(12, 6.2, (0, 24), (0, 12.4))
    label(ax, 12, 11.9, "Bố cục khung UART (115200 baud, 8N1, little-endian)", fs=10, bold=True)
    fields = [("SOF", "2 B", "AA 55", 2.4, BLUE_FILL), ("VER", "1 B", "01", 1.6, BLUE_FILL), ("SEQ", "1 B", "0–255\ntăng vòng", 2.0, BLUE_FILL),
              ("TYPE", "1 B", "0x01…0xA1", 2.2, BLUE_FILL), ("LEN", "1 B", "0–64", 1.6, BLUE_FILL), ("PAYLOAD", "LEN B", "struct packed\nu8/u16/u32", 4.6, GREEN_FILL),
              ("CRC16", "2 B", "CCITT-FALSE\nLE", 2.6, ORANGE_FILL)]
    x = 1.0
    for name, sz, val, w, fc in fields:
        box(ax, x, 8.6, w, 2.2, f"{name}\n{sz}", val, fc=fc, ec=GREY, fs=8.5)
        x += w + 0.15
    # dấu ngoặc CRC covers
    ax.plot([3.55, 3.55, 14.65, 14.65], [8.35, 8.1, 8.1, 8.35], color=ORANGE, lw=1.2)
    label(ax, 9.1, 7.75, "CRC-16/CCITT-FALSE (poly 0x1021, init 0xFFFF) tính trên VER…PAYLOAD · CRC(\"123456789\") = 0x29B1", fs=8, color=ORANGE)
    label(ax, 12, 7.0, "Ví dụ SORT(A, OK, pkg_seq=0x0102) seq=42:  AA 55 01 2A 20 04 01 00 02 01 <crc_lo> <crc_hi>", fs=8.5, color="#333", bg="#F3F3F3")

    # timeline ACK / retry
    label(ax, 6.0, 5.9, "Chờ ACK và gửi lại (P-07 / E-10)", fs=10, bold=True)
    label(ax, 18.0, 5.9, "Chống lặp theo SEQ", fs=10, bold=True)
    xp, xe = 1.5, 10.5
    ax.plot([xp, xp], [0.6, 5.4], color="#999", linestyle=(0, (4, 3)))
    ax.plot([xe, xe], [0.6, 5.4], color="#999", linestyle=(0, (4, 3)))
    label(ax, xp, 5.55, "Pi", fs=9, bold=True, bg=BLUE_FILL)
    label(ax, xe, 5.55, "ESP32", fs=9, bold=True, bg=TEAL_FILL)
    arrow(ax, (xp, 4.8), (xe, 4.5), color=BLUE, label="SORT seq=7", lpos=0.4)
    label(ax, xe + 1.4, 4.4, "khung lỗi CRC → bỏ,\ncrc_err++", fs=7.5, color=RED)
    label(ax, xp + 1.2, 3.9, "200 ms không ACK", fs=7.5, color=ORANGE, italic=True)
    arrow(ax, (xp, 3.5), (xe, 3.2), color=BLUE, label="SORT seq=7 (gửi lại lần 1)", lpos=0.45)
    arrow(ax, (xe, 2.8), (xp, 2.5), color=GREY, dashed=True, label="ACK(7)", lpos=0.5)
    label(ax, xp + 1.0, 1.9, "tối đa 3 lần gửi lại,\nsau đó TIMEOUT → REJECT_FAILSAFE", fs=7.5, color=RED, italic=True, ha="left")
    xp2, xe2 = 14.5, 23.0
    ax.plot([xp2, xp2], [0.6, 5.4], color="#999", linestyle=(0, (4, 3)))
    ax.plot([xe2, xe2], [0.6, 5.4], color="#999", linestyle=(0, (4, 3)))
    label(ax, xp2, 5.55, "Pi", fs=9, bold=True, bg=BLUE_FILL)
    label(ax, xe2, 5.55, "ESP32", fs=9, bold=True, bg=TEAL_FILL)
    arrow(ax, (xp2, 4.8), (xe2, 4.5), color=BLUE, label="START seq=3", lpos=0.4)
    label(ax, xe2 - 2.2, 4.0, "thực hiện, nhớ seq=3", fs=7.5, color=TEAL)
    arrow(ax, (xe2, 3.8), (xp2, 3.5), color=GREY, dashed=True, label="ACK(3) — bị mất", lpos=0.5)
    arrow(ax, (xp2, 3.0), (xe2, 2.7), color=BLUE, label="START seq=3 (gửi lại)", lpos=0.45)
    label(ax, xe2 - 2.2, 2.2, "SEQ trùng → chỉ ACK lại,\nKHÔNG chạy START lần 2", fs=7.5, color=TEAL)
    arrow(ax, (xe2, 1.7), (xp2, 1.4), color=GREY, dashed=True, label="ACK(3)", lpos=0.5)
    label(ax, 18.7, 0.9, "PING → PONG(state) · GET_STATUS → STATUS thay cho ACK · ACK/NACK/PONG/STATUS không cần ACK", fs=7.5, color="#444", italic=True)
    save(fig, OUT / "d05_uart.png")


# ----------------------------------------------------------------------------- 6. Tạo và xác thực QR
def fig_qr():
    fig, ax = new_fig(13, 6.4, (0, 27), (0, 12.8))
    region(ax, 0.3, 6.4, 26.4, 6.1, "Tạo mã (pi/tools/qr_generator.py, chạy ở nơi giữ khóa)", fc="#F7FAFF", ec=BLUE)
    a = box(ax, 0.8, 8.2, 4.6, 2.4, "Dữ liệu kiện", "PKG000123 | A | 1761631080\n(mã · kho · epoch UTC)")
    k = box(ax, 6.6, 10.1, 3.6, 1.6, "Khóa bí mật", "32 byte, file 600", fc=ORANGE_FILL, ec=ORANGE)
    h = box(ax, 6.6, 7.6, 3.6, 1.8, "HMAC-SHA256", "RFC 2104")
    arrow(ax, right(a), left(h), color=BLUE)
    arrow(ax, bottom(k), top(h), color=ORANGE)
    b64 = box(ax, 11.6, 7.6, 3.6, 1.8, "base64url", "không padding\n43 ký tự")
    arrow(ax, right(h), left(b64), color=BLUE, label="32 byte")
    txt = box(ax, 16.6, 7.6, 9.9, 1.8, "Chuỗi QR", "PKG000123|A|1761631080|<chữ ký 43 ký tự>", fc=GREEN_FILL, ec=GREEN)
    arrow(ax, right(b64), left(txt), color=BLUE, label="chữ ký")
    arrow(ax, (3.1, 8.2), (16.6, 7.9), color=BLUE, dashed=True, rad=0.2)
    label(ax, 9.5, 6.75, "3 trường đầu (không mã hóa, chỉ ký)", fs=7.5, color=BLUE, bg="white")
    label(ax, 21.5, 6.75, "in QR mức sửa lỗi M, cạnh ≥ 3 cm, A4 lưới 3×4; --forge tạo chữ ký sai để demo", fs=7.5, color="#444", italic=True)

    region(ax, 0.3, 0.3, 26.4, 5.7, "Xác thực trên Pi (pi/sorter/qr_security.py — S-03), xét lần lượt, sai ở đâu dừng ở đó", fc="#FFFBF5", ec=ORANGE)
    steps = [("Đọc QR", "pyzbar\n≤ 3 khung", "NO_QR\nMULTI_QR"), ("Định dạng", "4 trường, regex\n^PKG\\d{6}$", "BAD_FORMAT"),
             ("Kho", "A hoặc B", "BAD_WAREHOUSE"), ("Chữ ký", "HMAC lại,\ncompare_digest", "BAD_SIGNATURE"),
             ("Thời gian", "ts ≤ now + 300 s\nnow − ts ≤ 30 ngày", "FUTURE_TIMESTAMP\nEXPIRED"), ("Chống trùng", "INSERT used_codes\n(giao dịch)", "DUPLICATE")]
    x = 0.8
    prev = None
    for name, body, err in steps:
        bx = box(ax, x, 2.6, 3.3, 2.0, name, body, fc=BLUE_FILL, ec=BLUE, fs=8.5)
        if prev:
            arrow(ax, right(prev), left(bx), color=GREEN, label="đúng", fs=7)
        eb = box(ax, x + 0.15, 0.6, 3.0, 1.2, err, fc=RED_FILL, ec=RED, fs=7.8)
        arrow(ax, bottom(bx), top(eb), color=RED)
        prev = bx
        x += 3.85
    ok = box(ax, 23.3, 2.6, 3.1, 2.0, "VALID", "→ chạy AI\nQrResult(pkg_id, kho, ts)", fc=GREEN_FILL, ec=GREEN, fs=9)
    arrow(ax, right(prev), left(ok), color=GREEN, label="đúng", fs=7)
    label(ax, 24.85, 1.2, "mọi mã lỗi →\nREJECT_SECURITY\n+ security_event", fs=7.8, color=RED, bold=True)
    save(fig, OUT / "d06_qr.png")


# ----------------------------------------------------------------------------- 7. Secure OTA
def fig_ota():
    fig, ax = new_fig(12, 9.6, (0, 18), (0, 21))
    xa, xe, xp, xt = 2.3, 7.5, 12.2, 16.3
    lifeline(ax, xa, 19.8, 0.4, "App Secure OTA\n(laptop, HTTPS 8443)", fc=PURPLE_FILL, ec=PURPLE, w=3.4, h=0.9)
    lifeline(ax, xe, 19.8, 0.4, "ESP32 ota_secure", fc=TEAL_FILL, ec=TEAL, w=3.0, h=0.9)
    lifeline(ax, xp, 19.8, 0.4, "Pi sorter-core", w=2.8, h=0.9)
    lifeline(ax, xt, 19.8, 0.4, "ThingsBoard", fc=PURPLE_FILL, ec=PURPLE, w=2.6, h=0.9)
    y = 18.8
    selfmsg(ax, xa, y, "O-01 đăng nhập release_manager (bcrypt, TOTP)\nO-03 kiểm tra .bin (0xE9, esp_app_desc), SHA-256", side=1); y -= 1.1
    selfmsg(ax, xa, y, "O-04 manifest.json 1 dòng sort_keys → ký ECDSA P-256\n→ manifest.sig (DER base64); tự xác minh lại", side=1); y -= 1.1
    selfmsg(ax, xa, y, "O-05 phục vụ 3 file: manifest.json, manifest.sig, firmware.bin", side=1); y -= 1.0
    msg(ax, xt, xp, y, "RPC otaCheck (Admin)", color=RED); y -= 0.6
    msg(ax, xp, xe, y, "UART OTA_CHECK (chỉ khi IDLE)", color=BLUE); y -= 0.6
    msg(ax, xe, xp, y, "EVT_OTA_STATUS(CHECKING)", color=TEAL, dashed=True); y -= 0.45
    msg(ax, xp, xt, y, "ota_stage = CHECKING", color=PURPLE, dashed=True); y -= 0.7
    selfmsg(ax, xe, y, "FSM → OTA, băng dừng · bật Wi-Fi (STA) · chờ IP ≤ 20 s", side=1); y -= 0.9
    msg(ax, xe, xa, y, "GET /ota/manifest.json, /ota/manifest.sig — TLS xác minh bằng CA nhúng (lỗi → TLS_ERROR)", color=TEAL); y -= 0.8
    selfmsg(ax, xe, y, "O-10 SHA-256(manifest) + mbedtls_pk_verify(pubkey nhúng) → sai: REJECTED SIGNATURE_INVALID", side=1); y -= 0.9
    selfmsg(ax, xe, y, "O-11 device_model == sorter-esp32 (MODEL_MISMATCH) · version_code > đang chạy và > max_ver NVS (ROLLBACK_REJECTED)", side=1); y -= 0.9
    msg(ax, xe, xa, y, "GET /ota/firmware.bin — khối 4 KB: esp_ota_write + SHA-256, báo DOWNLOADING mỗi 10 %", color=TEAL); y -= 0.6
    msg(ax, xe, xp, y, "EVT_OTA_STATUS(DOWNLOADING, progress) → ota_progress", color=TEAL, dashed=True); y -= 0.8
    selfmsg(ax, xe, y, "O-12 size/hash ≠ manifest → esp_ota_abort, REJECTED HASH_MISMATCH (phân vùng cũ giữ nguyên)\nđúng → esp_ota_end, VERIFIED, set_boot_partition, lưu max_ver", side=1); y -= 1.2
    msg(ax, xe, xa, y, "POST /ota/report {device, version_code, result, reason} (best-effort)", color=TEAL, dashed=True); y -= 0.6
    msg(ax, xe, xp, y, "EVT_OTA_STATUS(REBOOTING) → esp_restart", color=TEAL, dashed=True); y -= 0.9
    selfmsg(ax, xe, y, "O-13 boot bản mới ở PENDING_VERIFY → self-test: driver OK + nhận PING từ Pi trong 30 s", side=1); y -= 1.0
    msg(ax, xp, xe, y, "PING (mỗi 500 ms) — là bằng chứng liên kết cho self-test", color=BLUE, dashed=True); y -= 0.7
    selfmsg(ax, xe, y, "đạt → mark_app_valid_cancel_rollback → SUCCESS\nkhông đạt → mark_app_invalid_rollback_and_reboot → bản cũ báo ROLLED_BACK", side=1); y -= 1.1
    msg(ax, xe, xp, y, "EVT_OTA_STATUS(SUCCESS, version_code)", color=TEAL); y -= 0.45
    msg(ax, xp, xt, y, "ota_stage = SUCCESS · attribute fw_version", color=PURPLE); y -= 0.8
    label(ax, 9.0, y, "Tab Demo của app: sửa 1 byte → HASH_MISMATCH · giả manifest → SIGNATURE_INVALID · hạ phiên bản → ROLLBACK_REJECTED",
          fs=8, color=RED, bg=RED_FILL, bold=True)
    save(fig, OUT / "d07_ota.png")


# ----------------------------------------------------------------------------- 8. Pipeline AI
def fig_ai():
    fig, ax = new_fig(12.5, 7.4, (0, 25), (0, 14.8))
    region(ax, 0.3, 7.4, 24.4, 7.1, "Huấn luyện (ai/train_packaging_damage.ipynb — Google Colab T4)", fc="#F7FAFF", ec=BLUE)
    src = box(ax, 0.7, 10.2, 4.2, 3.6, "5 nguồn ảnh", "① tự chụp tại trạm (×3 trọng số)\n② Roboflow Universe\n③ Kaggle  ④ Hugging Face\n⑤ cào web (CC)", fc=GREY_FILL, ec=GREY, fs=8.5)
    clean = box(ax, 5.5, 10.2, 4.0, 3.6, "Chuẩn hóa & lọc", "map_label → OK/Lỗi + loại\nbỏ ảnh hỏng, pHash trùng\nCLIP lọc ảnh web", fs=8.5)
    split = box(ax, 10.1, 10.2, 3.8, 3.6, "Chia theo hộp", "GroupShuffle theo box_id\n70 / 15 / 15\nweb không vào test", fs=8.5)
    aug = box(ax, 14.5, 10.2, 4.0, 3.6, "Tăng cường", "xoay, lật, sáng, nhiễu, JPEG\n+ sinh lỗi tổng hợp\n(rách, móp, bẹp, ướt)", fs=8.5)
    for a, b in [(src, clean), (clean, split), (split, aug)]:
        arrow(ax, right(a), left(b), color=BLUE)
    teacher = box(ax, 19.1, 12.2, 5.3, 1.7, "Thầy ConvNeXt-V2-Tiny", "fine-tune, 2 đầu ra", fc=PURPLE_FILL, ec=PURPLE, fs=8.5)
    student = box(ax, 19.1, 10.0, 5.3, 1.7, "Trò MobileNetV4-Conv-M", "chưng cất KD (α 0.5, T 4)", fc=PURPLE_FILL, ec=PURPLE, fs=8.5)
    arrow(ax, right(aug), (19.1, 13.0), color=BLUE)
    arrow(ax, right(aug), (19.1, 10.8), color=BLUE)
    arrow(ax, bottom(teacher), top(student), color=PURPLE, label="soft labels")
    cal = box(ax, 10.1, 7.8, 6.0, 1.8, "Hiệu chỉnh + ngưỡng", "temperature scaling · chọn ngưỡng recall Lỗi ≥ 95 %\nđánh giá riêng test_own · Grad-CAM", fc=GREEN_FILL, ec=GREEN, fs=8.5)
    arrow(ax, (19.1, 10.4), (16.1, 8.7), color=PURPLE, rad=0.1)
    exp = box(ax, 17.0, 7.8, 7.4, 1.8, "Xuất", "packaging_cls.onnx (FP32 / INT8 nếu không giảm độ chính xác)\nmodel_card.json · labels.json · metrics.json", fc=GREEN_FILL, ec=GREEN, fs=8.5)
    arrow(ax, right(cal), left(exp), color=GREEN)

    region(ax, 0.3, 0.3, 24.4, 6.6, "Suy luận trên Raspberry Pi 4 (pi/sorter/classifier_onnx.py — P-05, AI-06, AI-07)", fc="#F4FBF5", ec=GREEN)
    arrow(ax, (20.7, 7.8), (20.7, 6.9), color=GREEN, label="scp → /opt/sorter/models/", loff=(2.2, 0))
    fr = box(ax, 0.7, 2.6, 3.4, 2.6, "Khung ảnh", "RGB 1280×960\n(Picamera2)", fc=GREY_FILL, ec=GREY, fs=8.5)
    roi = box(ax, 4.6, 2.6, 3.4, 2.6, "Cắt ROI", "x, y, w, h\ntừ config", fs=8.5)
    pre = box(ax, 8.5, 2.6, 3.8, 2.6, "Tiền xử lý", "resize INTER_AREA → S×S\n/255 → float32 NCHW\n(chuẩn hóa đã nhúng)", fs=8.5)
    ort = box(ax, 12.8, 2.6, 4.0, 2.6, "ONNX Runtime", "CPU 4 luồng\ninput \"image\"\nwarm-up 3 lần", fc=PURPLE_FILL, ec=PURPLE, fs=8.5)
    outp = box(ax, 17.3, 2.6, 3.4, 2.6, "Đầu ra", "probs [OK, DEFECT]\ntype_probs\n[tear, dent, crush, wet]", fc=PURPLE_FILL, ec=PURPLE, fs=8.5)
    res = box(ax, 21.2, 2.6, 3.4, 2.6, "ClassResult", "label · confidence\ndefect_types · infer_ms\nmodel_version", fc=GREEN_FILL, ec=GREEN, fs=8.5)
    for a, b in [(fr, roi), (roi, pre), (pre, ort), (ort, outp), (outp, res)]:
        arrow(ax, right(a), left(b), color=GREEN)
    card = box(ax, 8.5, 0.6, 8.3, 1.4, "model_card.json", "input.size · defect_threshold · damage_types · version", fc=ORANGE_FILL, ec=ORANGE, fs=8.5)
    arrow(ax, (10.4, 2.0), (10.4, 2.6), color=ORANGE)
    arrow(ax, (14.8, 2.0), (14.8, 2.6), color=ORANGE)
    label(ax, 21.0, 1.2, "p_defect ≥ ngưỡng → DEFECT\np_ok ≥ min_confidence → OK\ncòn lại → UNCERTAIN (loại)", fs=8, color=RED, bg=RED_FILL)
    save(fig, OUT / "d08_ai.png")


# ----------------------------------------------------------------------------- 9. Triển khai mạng
def fig_deployment():
    fig, ax = new_fig(12.5, 7.0, (-1.6, 25), (0, 14.0))
    region(ax, 0.3, 5.6, 9.6, 7.7, "Laptop trong LAN (Windows/Linux, Docker)", fc="#FBF7FF", ec=PURPLE)
    tb = box(ax, 0.7, 9.4, 8.8, 3.3, "ThingsBoard CE — thingsboard/tb-postgres", "Docker Compose · volume data/, logs/ · certs/ mount :ro\nTCP 8080 → 9090 web/REST · TCP 8883 MQTT TLS (X.509)\nKHÔNG publish 1883 · inactivity 30 s · khóa 5 lần sai", fc=PURPLE_FILL, ec=PURPLE, fs=8.5)
    app = box(ax, 0.7, 5.9, 8.8, 3.1, "App Secure OTA (PySide6)", "HTTPS 8443: /ota/manifest.json · manifest.sig · firmware.bin · POST /ota/report\nkhóa ECDSA riêng (PEM mã hóa) · SQLite users/releases/reports", fc=PURPLE_FILL, ec=PURPLE, fs=8.5)
    usr = box(ax, 0.7, 0.8, 8.8, 3.4, "Trình duyệt người dùng", "Admin (Tenant Administrator + 2FA TOTP) → dashboard Điều khiển\nViewer (Customer User) → dashboard Giám sát, thiết bị gương sorter-view", fc=GREY_FILL, ec=GREY, fs=8.5)
    arrow(ax, (0.7, 2.5), (0.7, 11.0), color=GREY, rad=0.32, label="HTTP 8080\nREST / WebSocket", lpos=0.5, loff=(0.0, 0), fs=7.5)

    region(ax, 10.6, 5.6, 8.4, 7.7, "Raspberry Pi 4 (Raspberry Pi OS Bookworm 64-bit)", fc="#F7FAFF", ec=BLUE)
    pi = box(ax, 11.0, 8.4, 7.6, 4.3, "sorter.service (user sorter, systemd)", "/etc/sorter/config.yaml · secrets/qr_hmac.key (600)\ncerts/ca.pem, device.pem, device.key (600)\n/opt/sorter/models/*.onnx + model_card.json\n/var/lib/sorter/sorter.db, defects/ · /var/log/sorter/*.jsonl\nufw: chỉ 22/tcp từ LAN · fail2ban · SSH key", fc=BLUE_FILL, ec=BLUE, fs=8.2)
    cam = box(ax, 11.0, 5.9, 3.5, 2.1, "Pi Camera CSI", "Picamera2\nđèn LED cố định", fc=GREY_FILL, ec=GREY, fs=8.5)
    uart = box(ax, 15.1, 5.9, 3.5, 2.1, "/dev/serial0", "UART PL011\ndtoverlay=disable-bt", fc=TEAL_FILL, ec=TEAL, fs=8.5)
    arrow(ax, top(cam), (12.75, 8.4), color=GREY)
    arrow(ax, top(uart), (16.85, 8.4), color=TEAL)
    arrow(ax, (10.6, 11.2), (9.5, 11.2), color=PURPLE, label="MQTT/TLS 8883\nmutual X.509", lpos=0.5, loff=(0, 0.6), fs=7.5)
    arrow(ax, (9.5, 10.2), (10.6, 10.2), color=RED, label="RPC whitelist", lpos=0.5, loff=(0, -0.3), fs=7.5)

    region(ax, 19.7, 5.6, 5.0, 7.7, "ESP32 DevKit", fc="#F3FBF9", ec=TEAL)
    esp = box(ax, 20.0, 8.4, 4.4, 4.3, "Firmware ESP-IDF", "UART2 115200\nFSM · motor · servo\nsensor · LED/còi · E-stop\nSecure Boot V2 + Flash Enc.\nWi-Fi CHỈ khi OTA", fc=TEAL_FILL, ec=TEAL, fs=8.2)
    per = box(ax, 20.0, 5.9, 4.4, 2.1, "Ngoại vi", "L298N, 2 servo, cảm biến,\nLED, còi, nút E-stop", fc=GREY_FILL, ec=GREY, fs=8.2)
    arrow(ax, top(per), bottom(esp), color=TEAL)
    arrow(ax, (18.6, 7.0), (20.0, 7.0), color=TEAL, label="GPIO16/17\nGND chung", lpos=0.5, loff=(0, 0.55), fs=7.5)
    ax.plot([20.0, 19.35, 19.35, 10.2, 10.2], [10.0, 10.0, 5.1, 5.1, 7.0], color=PURPLE, lw=1.3, linestyle=(0, (4, 3)))
    arrow(ax, (10.2, 7.0), (9.5, 7.0), color=PURPLE, dashed=True)
    label(ax, 14.8, 4.85, "HTTPS 8443 — chỉ trong lúc OTA (Wi-Fi tạm) · CA nhúng · ECDSA pubkey nhúng", fs=7.5, color=PURPLE, bg="white")
    lan = box(ax, 10.6, 0.8, 14.1, 3.4, "Mạng LAN / Wi-Fi chung", "Cổng mở: 8080 (web), 8883 (MQTTS), 8443 (OTA HTTPS), 22 (SSH Pi, chỉ từ LAN)\nĐóng: 1883 (MQTT thường), mọi cổng khác trên Pi\nNTP: Pi và laptop phải đúng giờ (hạn chứng chỉ, thời gian QR)", fc="#FFFDE7", ec=ORANGE, fs=8.5)
    save(fig, OUT / "d09_deployment.png")


# ----------------------------------------------------------------------------- 10. Đấu nối ESP32
def fig_wiring():
    fig, ax = new_fig(12, 7.2, (0, 24), (0, 14.4))
    esp = box(ax, 8.6, 2.2, 6.8, 10.4, "ESP32 DevKit", "", fc=TEAL_FILL, ec=TEAL, fs=11)
    label(ax, 12.0, 12.1, "ESP32 DevKit (rev ≥ v3.0)", fs=10, bold=True)

    def pin(side, y, name):
        x = 8.6 if side == "L" else 15.4
        ax.add_patch(__import__("matplotlib").patches.Rectangle((x - 0.55, y - 0.22), 1.1, 0.44, fc="#FFFFFF", ec=TEAL, lw=1))
        label(ax, x, y, name, fs=6.5, bold=True)
        return (x - 0.35 if side == "L" else x + 0.35, y)

    # trái: Pi, cảm biến, E-stop
    pi = box(ax, 0.6, 9.0, 5.6, 3.2, "Raspberry Pi 4 (GPIO 40 chân)", "chân 8  GPIO14 TXD\nchân 10 GPIO15 RXD\nchân 6  GND", fc=BLUE_FILL, ec=BLUE, fs=8.5)
    p16 = pin("L", 11.3, "GPIO16")
    p17 = pin("L", 10.5, "GPIO17")
    pgnd = pin("L", 9.7, "GND")
    arrow(ax, (6.2, 11.3), p16, color=BLUE, label="Pi TX → RX2 (3,3 V)", loff=(0, 0.22), style="-")
    arrow(ax, (6.2, 10.5), p17, color=BLUE, label="Pi RX ← TX2", loff=(0, 0.22), style="-")
    arrow(ax, (6.2, 9.7), pgnd, color=GREY, label="GND chung (bắt buộc)", loff=(0, 0.22), style="-")
    sens = box(ax, 0.6, 5.6, 5.6, 2.8, "Cảm biến hàng (chọn 1)", "TCRT5000: DO → GPIO34 (pull-up ngoài)\nHC-SR04: TRIG GPIO13, ECHO GPIO35\nqua chia áp 1 kΩ – 2 kΩ", fc=GREY_FILL, ec=GREY, fs=8)
    p34 = pin("L", 7.9, "GPIO34")
    p13 = pin("L", 7.1, "GPIO13")
    p35 = pin("L", 6.3, "GPIO35")
    arrow(ax, (6.2, 7.9), p34, color=GREY, style="-")
    arrow(ax, (6.2, 7.1), p13, color=GREY, style="-")
    arrow(ax, (6.2, 6.3), p35, color=GREY, style="-")
    est = box(ax, 0.6, 2.4, 5.6, 2.6, "Nút E-stop loại nấm (2 NC)", "NC1: cắt nguồn 12 V động cơ (phần cứng)\nNC2: GPIO39 ↔ GND, kéo lên 10 kΩ\nLOW = bình thường, HIGH = dừng", fc=RED_FILL, ec=RED, fs=8)
    p39 = pin("L", 3.7, "GPIO39")
    arrow(ax, (6.2, 3.7), p39, color=RED, style="-")

    # phải: motor, servo, LED, còi
    mot = box(ax, 17.8, 9.6, 5.8, 2.8, "L298N + động cơ DC 12 V", "ENA ← GPIO25 (PWM 1 kHz, bỏ jumper)\nIN1 ← GPIO26, IN2 ← GPIO27\nnguồn 12 V ≥ 2 A, GND chung", fc=ORANGE_FILL, ec=ORANGE, fs=8)
    p25 = pin("R", 11.6, "GPIO25")
    p26 = pin("R", 10.8, "GPIO26")
    p27 = pin("R", 10.0, "GPIO27")
    for p in (p25, p26, p27):
        arrow(ax, p, (17.8, p[1]), color=ORANGE, style="-")
    srv = box(ax, 17.8, 6.4, 5.8, 2.6, "Servo A / Servo B (SG90/MG996R)", "tín hiệu A ← GPIO18, B ← GPIO19\n50 Hz, xung 500–2500 µs\nnguồn 5 V ≥ 3 A riêng (hạ áp 12→5 V)", fc=ORANGE_FILL, ec=ORANGE, fs=8)
    p18 = pin("R", 8.4, "GPIO18")
    p19 = pin("R", 7.6, "GPIO19")
    arrow(ax, p18, (17.8, 8.4), color=ORANGE, style="-")
    arrow(ax, p19, (17.8, 7.6), color=ORANGE, style="-")
    led = box(ax, 17.8, 3.6, 5.8, 2.2, "LED xanh / LED đỏ / còi", "xanh ← GPIO32, đỏ ← GPIO33 (220 Ω)\ncòi ← GPIO23 qua transistor NPN", fc=GREEN_FILL, ec=GREEN, fs=8)
    p32 = pin("R", 5.4, "GPIO32")
    p33 = pin("R", 4.6, "GPIO33")
    p23 = pin("R", 3.8, "GPIO23")
    for p in (p32, p33, p23):
        arrow(ax, p, (17.8, p[1]), color=GREEN, style="-")
    pwr = box(ax, 17.8, 0.6, 5.8, 2.4, "Nguồn", "Pi: 5 V 3 A USB-C · ESP32: 5 V USB/VIN\nĐộng cơ: 12 V · Servo: 5 V ≥ 3 A\nTẤT CẢ GND nối chung", fc="#FFFDE7", ec=ORANGE, fs=8)
    label(ax, 12.0, 1.4, "Pi Camera CSI gắn thẳng cổng CSI của Pi (không qua ESP32)", fs=8, color=BLUE, italic=True, bg=BLUE_FILL)
    save(fig, OUT / "d10_wiring.png")


# ----------------------------------------------------------------------------- 11. Phát hiện bất thường
def fig_anomaly():
    fig, ax = new_fig(12, 6.0, (0, 24), (0, 12))
    srcs = [("Orchestrator", "qr_bad_signature\nqr_duplicate · qr_bad_format\npackages · estop_count"), ("RpcHandler", "rpc_total\nrpc_rejected"),
            ("UartLink", "uart_crc_errors"), ("ThingsBoardClient", "mqtt_reconnects"), ("journalctl -u ssh", "ssh_failed")]
    y = 10.4
    for name, body in srcs:
        b = box(ax, 0.5, y - 1.7, 4.6, 1.7, name, body, fc=GREY_FILL, ec=GREY, fs=8)
        arrow(ax, right(b), (6.0, 7.0), color=GREY, rad=0.0)
        y -= 1.95
    cnt = box(ax, 6.0, 5.6, 3.6, 2.8, "MetricCounters", "thread-safe\nsnapshot_and_reset()\nmỗi 10 s", fc=BLUE_FILL, ec=BLUE, fs=8.5)
    win = box(ax, 10.4, 5.6, 3.6, 2.8, "Cửa sổ 10 s", "vector 10 đặc trưng\n→ bảng anomaly_windows\n(SQLite)", fc=BLUE_FILL, ec=BLUE, fs=8.5)
    arrow(ax, right(cnt), left(win), color=BLUE)
    iff = box(ax, 14.8, 7.3, 4.2, 2.6, "IsolationForest", "StandardScaler → IF\n200 cây · contamination 0.02\nscore = −score_samples", fc=PURPLE_FILL, ec=PURPLE, fs=8.5)
    rules = box(ax, 14.8, 3.9, 4.2, 2.6, "Luật cứng (A-04)", "≥ 5 QR sai/phút\n≥ 5 SSH thất bại/phút\nRPC lạ → báo ngay", fc=ORANGE_FILL, ec=ORANGE, fs=8.5)
    arrow(ax, right(win), left(iff), color=PURPLE)
    arrow(ax, right(win), left(rules), color=ORANGE)
    thr = box(ax, 19.8, 7.3, 3.9, 2.6, "Ngưỡng & cooldown", "ngưỡng = phân vị 99\ntrên dữ liệu bình thường\ncooldown 60 s", fc=PURPLE_FILL, ec=PURPLE, fs=8.5)
    arrow(ax, right(iff), left(thr), color=PURPLE, label="score")
    out = box(ax, 19.8, 3.9, 3.9, 2.6, "ThingsBoard", "anomaly_score · anomaly_flag\nanomaly_top_features\nalarm Anomaly / Security", fc=RED_FILL, ec=RED, fs=8.5)
    arrow(ax, bottom(thr), top(out), color=RED, label="flag + ANOMALY\nCRITICAL", loff=(-1.5, 0))
    arrow(ax, right(rules), left(out), color=ORANGE, label="RULE_*", loff=(0, 0.25))
    train = box(ax, 10.4, 1.4, 8.6, 1.8, "Huấn luyện (A-02): python -m sorter.anomaly.train --db … --from … --to …", "≥ 30 phút ≈ 180 cửa sổ bình thường → anomaly_if.joblib + metadata.json", fc=GREEN_FILL, ec=GREEN, fs=8)
    arrow(ax, (12.2, 5.6), (12.2, 3.2), color=GREEN, dashed=True, label="cửa sổ bình thường", lpos=0.5, loff=(1.7, 0))
    arrow(ax, (16.9, 3.2), (16.9, 7.3), color=GREEN, dashed=True, label="model", lpos=0.85, loff=(0.7, 0))
    label(ax, 14.7, 0.55, "Đánh giá (A-06): attack_sim.py --i-own-this-system ghi attack_log.jsonl → anomaly.evaluate: tỉ lệ phát hiện, số báo nhầm, biểu đồ điểm", fs=7.5, color="#444", italic=True)
    save(fig, OUT / "d11_anomaly.png")


# ----------------------------------------------------------------------------- 12. Các lớp bảo mật
def fig_security():
    fig, ax = new_fig(12, 6.6, (0, 24), (0, 13.2))
    layers = [
        ("7. Giám sát & phản ứng", "Isolation Forest + luật cứng (A-01…A-06) · security_events (S-09) · alarm ThingsBoard · rpc_audit", PURPLE_FILL, PURPLE),
        ("6. Secure OTA", "manifest ký ECDSA P-256 · SHA-256 · version_code tăng nghiêm ngặt (chống rollback) · app rollback + self-test · CA nhúng (O-08…O-15)", PURPLE_FILL, PURPLE),
        ("5. Nền tảng ThingsBoard", "Admin/Viewer tách vai trò · 2FA TOTP · khóa 5 lần sai · thiết bị gương cho Viewer · Audit Logs (S-02, T-08)", BLUE_FILL, BLUE),
        ("4. Mạng", "MQTT TLS ≥ 1.2 cổng 8883, mutual X.509 (S-01, S-04) · RPC whitelist + rate limit (S-05) · 1883 đóng · ufw/fail2ban trên Pi", BLUE_FILL, BLUE),
        ("3. Raspberry Pi", "QR HMAC-SHA256 + chống replay (S-03) · khóa/cert quyền 600, user sorter không root · SSH key, cấm root (S-07) · fail-safe mọi ngoại lệ", GREEN_FILL, GREEN),
        ("2. ESP32", "Secure Boot V2 · Flash Encryption · tắt JTAG (S-08) · UART CRC16 + whitelist lệnh + miền tham số + SEQ (S-06) · watchdog liên kết 2 s", TEAL_FILL, TEAL),
        ("1. Phần cứng", "Nút E-stop cắt nguồn động cơ trực tiếp (NC, đứt dây cũng dừng) · hàng không gạt tự rơi vào khay Loại (fail-safe cơ khí)", RED_FILL, RED),
    ]
    y = 11.7
    for i, (t, body, fc, ec) in enumerate(layers):
        box(ax, 0.7, y - 1.4, 22.6, 1.4, t, body, fc=fc, ec=ec, fs=9, bfs=7.8, align="left")
        y -= 1.55
    label(ax, 12, 12.5, "Bảo vệ nhiều lớp: tấn công phải vượt qua mọi lớp bên trên để tới phần cứng; mỗi lớp có cách chứng minh riêng khi demo", fs=9, bold=True, color="#333")
    label(ax, 12, 0.35, "Giới hạn trung thực: CRC không chống giả mạo · requestedBy chỉ hiển thị · Flash Encryption Development còn cho nạp lại · IF có thể báo nhầm", fs=8, italic=True, color=RED)
    save(fig, OUT / "d12_security.png")


if __name__ == "__main__":
    for f in [fig_dataflow, fig_sequence, fig_fsm, fig_decision, fig_uart, fig_qr, fig_ota, fig_ai, fig_deployment, fig_wiring, fig_anomaly, fig_security]:
        f()
        print("ok", f.__name__)
