#!/usr/bin/env python3
"""P10 — demo_check: chạy 12 kịch bản demo (SPEC mục 5.1 / 5.2) và in bảng PASS/FAIL/SKIP.

Hai chế độ:
  --mode sim  (mặc định)  Tự động, không cần phần cứng: dựng App thật (sorter.main.App) với
                           config.sim.yaml + FakeEsp32 + FolderCamera + LogTelemetrySink, DB/log/ảnh trong
                           thư mục tạm (không làm bẩn repo). Mỗi kịch bản chọn đúng ảnh fixture rồi kích
                           cảm biến giả, kiểm tra quyết định, lệnh SORT tới ESP32 giả, telemetry, DB.
  --mode real              Trên Pi với ESP32 thật: hướng dẫn người đặt hộp từng bước (input()), chờ kết quả
                           từ orchestrator.records / trạng thái băng, so với mong đợi.

Dùng:
  cd pi && PYTHONUTF8=1 python tools/demo_check.py                      # sim, tất cả kịch bản
  python tools/demo_check.py --only 1,2,3,4,7                           # chọn kịch bản
  python tools/demo_check.py --mode real --port /dev/serial0 --config /etc/sorter/config.yaml

Mã kịch bản: 1 2 3 4 5 6a 6b 7 8 9 10 11 12 (theo bảng 5.1 và 5.2). Thoát mã 1 nếu có FAIL.
Kịch bản 9 (Viewer/Admin, 2FA) chỉ in hướng dẫn kiểm tra tay (MANUAL) vì cần ThingsBoard thật.
"""
from __future__ import annotations

import argparse
import logging
import shutil
import sys
import tempfile
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable

PI_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = PI_ROOT.parent
sys.path.insert(0, str(PI_ROOT))

from sorter import interfaces  # noqa: E402
from sorter.camera import FolderCamera  # noqa: E402
from sorter.classifier import DummyClassifier  # noqa: E402
from sorter.config import SorterConfig, load_config  # noqa: E402
from sorter.decision import REJECT_DEFECT, REJECT_SECURITY, SORT_A, SORT_B  # noqa: E402
from sorter.interfaces import F_QR_BAD_SIGNATURE, LogTelemetrySink, SecurityLevel  # noqa: E402
from sorter.main import App, apply_sim  # noqa: E402
from sorter.orchestrator import PackageRecord  # noqa: E402
from sorter.protocol_gen import MsgType, SortReason, SortTarget, State  # noqa: E402

log = logging.getLogger("demo_check")

PASS, FAIL, SKIP, MANUAL = "PASS", "FAIL", "SKIP", "MANUAL"
ALL_SCENARIOS: tuple[str, ...] = ("1", "2", "3", "4", "5", "6a", "6b", "7", "8", "9", "10", "11", "12")

# Tên kịch bản (đúng bảng 5.1 / 5.2 của SPEC)
SCENARIO_NAMES: dict[str, str] = {
    "1": "Hàng tốt, QR kho A -> khay A, cnt_a tăng",
    "2": "Hàng tốt, QR kho B -> khay B, cnt_b tăng",
    "3": "Hàng rách, QR hợp lệ -> khay Loại + ảnh lỗi",
    "4": "QR giả (chữ ký sai) -> khay Loại + alarm BAD_SIGNATURE",
    "5": "DỪNG KHẨN CẤP từ dashboard (RPC emergencyStop)",
    "6a": "Secure OTA bản hợp lệ -> xác minh OK",
    "6b": "Secure OTA sửa 1 byte / giả manifest -> HASH_MISMATCH / SIGNATURE_INVALID",
    "7": "Quét lại QR đã dùng -> DUPLICATE (chống replay)",
    "8": "E-stop vật lý: dừng ngay; chỉ reset khi đã nhả nút",
    "9": "Đăng nhập Viewer rồi Admin (2FA) — kiểm tra tay",
    "10": "Mô phỏng tấn công -> luật cứng RULE_* / anomaly",
    "11": "Rút dây UART -> ESP32 FAULT LINK_LOST sau 2 s",
    "12": "OTA ký hợp lệ nhưng phiên bản cũ -> ROLLBACK_REJECTED",
}


@dataclass
class ScenarioResult:
    """Kết quả một kịch bản."""

    sid: str
    name: str
    status: str
    detail: str = ""
    seconds: float = 0.0


class CheckFailed(Exception):
    """Một khẳng định của kịch bản không đạt."""


def check(cond: bool, msg: str) -> None:
    if not cond:
        raise CheckFailed(msg)


def wait_for(fn: Callable[[], bool], timeout_s: float, step_s: float = 0.02) -> bool:
    """Chờ tới khi fn() đúng hoặc hết timeout."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if fn():
            return True
        time.sleep(step_s)
    return bool(fn())


def normalize_only(only: Iterable[Any] | str | None) -> list[str]:
    """--only "1,2,6a" hoặc [1, 2, "6a"] -> ["1", "2", "6a"] theo thứ tự ALL_SCENARIOS."""
    if only is None:
        return list(ALL_SCENARIOS)
    if isinstance(only, str):
        tokens = [t.strip() for t in only.replace(";", ",").split(",") if t.strip()]
    else:
        tokens = [str(t).strip() for t in only]
    wanted = {t.lower() for t in tokens}
    unknown = wanted - set(ALL_SCENARIOS)
    if unknown:
        raise ValueError(f"Kịch bản không hợp lệ: {sorted(unknown)} (hợp lệ: {', '.join(ALL_SCENARIOS)})")
    return [s for s in ALL_SCENARIOS if s in wanted]


# ---------------------------------------------------------------------------
# Camera chọn ảnh theo tên (sim)
# ---------------------------------------------------------------------------
class PickCamera(FolderCamera):
    """FolderCamera có thể chọn trước ảnh cho kiện kế tiếp bằng tên file (select); không chọn thì xoay vòng."""

    def __init__(self, folder: str | Path) -> None:
        super().__init__(folder)
        self._names = [p.name for p in self._files]
        self._queue: deque[str] = deque()

    def select(self, name: str) -> None:
        if name not in self._names:
            raise FileNotFoundError(f"Không có ảnh {name} trong {self.folder} (có: {self._names})")
        with self._lock:
            self._queue.append(name)

    def on_package_start(self) -> None:
        with self._lock:
            name = self._queue.popleft() if self._queue else None
            if name is not None:
                self._idx = self._names.index(name)
                self._started = True
                return
        super().on_package_start()  # ngoài lock (Lock không tái nhập)


class DemoApp(App):
    """App thật của sorter.main nhưng camera thay bằng PickCamera để chọn ảnh theo kịch bản."""

    def __init__(self, cfg: SorterConfig) -> None:
        super().__init__(cfg)
        self.camera.close()
        self.camera = PickCamera(cfg.camera.folder)
        self.orchestrator.camera = self.camera
        if cfg.ai.backend == "dummy":
            self.classifier = DummyClassifier(cfg.ai, label_hint=self.camera.next_name)
            self.orchestrator.classifier = self.classifier


# ---------------------------------------------------------------------------
# Rig sim
# ---------------------------------------------------------------------------
class SimRig:
    """Dựng App ở chế độ sim trong thư mục tạm; tiện ích chung cho các kịch bản."""

    IMG_OK_A = "01_ok_A.jpg"
    IMG_OK_B = "02_ok_B.jpg"
    IMG_TEAR = "03_defect_tear_A.jpg"
    IMG_FORGED = "04_forged_A.jpg"
    IMG_DUP = "07_dup_A.jpg"

    def __init__(self, config_path: Path, workdir: Path) -> None:
        cfg = load_config(config_path)
        apply_sim(cfg)
        cfg.storage.db_path = str(workdir / "sorter.db")
        cfg.storage.defect_dir = str(workdir / "defects")
        cfg.storage.log_dir = str(workdir / "log")
        cfg.camera.settle_ms = min(cfg.camera.settle_ms, 10)
        self.cfg = cfg
        self.workdir = workdir
        self.app = DemoApp(cfg)
        self.app.start()
        self.telemetry: LogTelemetrySink = self.app.telemetry  # type: ignore[assignment]
        check(isinstance(self.telemetry, LogTelemetrySink), "sim cần LogTelemetrySink (thingsboard.enabled=false)")
        check(self.app.fake is not None, "sim cần FakeEsp32 (uart.port=sim)")
        check(self.app.rpc_handler is not None, "sim cần RpcHandler (sorter.rpc_handler)")
        self.fake = self.app.fake
        self.orch = self.app.orchestrator
        self.storage = self.app.storage
        self.camera: PickCamera = self.app.camera  # type: ignore[assignment]
        check(wait_for(lambda: self.fake.state == State.RUNNING, 3.0), "FakeEsp32 không vào RUNNING sau START")

    def close(self) -> None:
        self.app.stop()

    # -- tiện ích ----------------------------------------------------------
    def process(self, image_name: str, timeout_s: float = 15.0) -> PackageRecord:
        """Chọn ảnh rồi kích cảm biến giả một lần; trả bản ghi kiện vừa xử lý."""
        self.ensure_running()
        self.camera.select(image_name)
        recs = self.orch.run_once_sim(1, gap_s=0.0, timeout_s=timeout_s)
        rec = recs[0]
        check(rec.image_name == image_name, f"ảnh xử lý là {rec.image_name}, mong đợi {image_name}")
        # chờ ESP32 giả gạt xong (EVT_SORT_DONE) để kịch bản sau bắt đầu từ RUNNING
        wait_for(lambda: self.fake.state == State.RUNNING, 5.0)
        return rec

    def ensure_running(self) -> None:
        """Đưa ESP32 giả về RUNNING (reset E-stop/FAULT nếu cần rồi START)."""
        if self.fake.state == State.RUNNING:
            return
        if self.fake.state in (State.ESTOP, State.FAULT):
            self.fake.release_estop()
            res = self.app.uart.send_command(MsgType.ESTOP_RESET)
            check(res.ok, f"ESTOP_RESET thất bại: {res.error}")
            wait_for(lambda: self.fake.state == State.IDLE, 2.0)
        if self.fake.state == State.IDLE:
            check(self.orch.start_conveyor(), "START băng chuyền thất bại")
        check(wait_for(lambda: self.fake.state == State.RUNNING, 5.0), f"ESP32 giả đang {self.fake.state.name}, không về RUNNING")

    def rpc(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """Giả lập RPC từ dashboard qua LogTelemetrySink.call_rpc (đi qua RpcHandler thật)."""
        p = {"requestedBy": "demo_check"}
        p.update(params or {})
        return self.telemetry.call_rpc(method, p)

    def telemetry_for(self, pkg_id: str | None, decision: str | None = None) -> dict[str, Any]:
        for d in reversed(self.telemetry.sent):
            if "decision" in d and d.get("pkg_id") == (pkg_id or "") and (decision is None or d["decision"] == decision):
                return d
        raise CheckFailed(f"không có telemetry kiện cho pkg_id={pkg_id}")

    def security_events(self, event_type: str | None = None, n: int = 100) -> list[dict[str, Any]]:
        evs = self.storage.recent_security_events(n)
        return [e for e in evs if event_type is None or e["event_type"] == event_type]

    def counters(self) -> dict[str, int]:
        return self.app.counters.to_telemetry()


# ---------------------------------------------------------------------------
# Các kịch bản sim
# ---------------------------------------------------------------------------
def sim_1(rig: SimRig) -> str:
    before = rig.counters()
    rec = rig.process(rig.IMG_OK_A)
    after = rig.counters()
    check(rec.qr_status == "VALID" and rec.pkg_id == "PKG000001", f"qr_status={rec.qr_status} pkg_id={rec.pkg_id}")
    check(rec.ai_label == "OK", f"ai_label={rec.ai_label}")
    check(rec.decision == SORT_A and rec.sort_sent, f"decision={rec.decision} sort_sent={rec.sort_sent} ({rec.sort_error})")
    s = rig.fake.sent_sorts[-1]
    check(s.target == SortTarget.A and s.reason == SortReason.OK, f"SORT tới ESP32: target={s.target} reason={s.reason}")
    check(after["cnt_a"] == before["cnt_a"] + 1, f"cnt_a {before['cnt_a']} -> {after['cnt_a']}")
    t = rig.telemetry_for("PKG000001", SORT_A)
    check(t["warehouse"] == "A" and t["cnt_a"] == after["cnt_a"], "telemetry kiện thiếu warehouse/cnt_a")
    check("EVT_SORT_DONE" in rig.fake.stats.events_sent, "ESP32 giả chưa gửi EVT_SORT_DONE")
    return f"SORT_A, cnt_a={after['cnt_a']}, latency={rec.latency_ms:.0f} ms"


def sim_2(rig: SimRig) -> str:
    before = rig.counters()
    rec = rig.process(rig.IMG_OK_B)
    after = rig.counters()
    check(rec.qr_status == "VALID" and rec.warehouse == "B", f"qr_status={rec.qr_status} warehouse={rec.warehouse}")
    check(rec.decision == SORT_B and rec.sort_sent, f"decision={rec.decision} sort_sent={rec.sort_sent}")
    s = rig.fake.sent_sorts[-1]
    check(s.target == SortTarget.B and s.reason == SortReason.OK, f"SORT tới ESP32: target={s.target} reason={s.reason}")
    check(after["cnt_b"] == before["cnt_b"] + 1, f"cnt_b {before['cnt_b']} -> {after['cnt_b']}")
    return f"SORT_B, cnt_b={after['cnt_b']}, latency={rec.latency_ms:.0f} ms"


def sim_3(rig: SimRig) -> str:
    before = rig.counters()
    rec = rig.process(rig.IMG_TEAR)
    after = rig.counters()
    check(rec.qr_status == "VALID", f"qr_status={rec.qr_status}")
    check(rec.ai_label == "DEFECT", f"ai_label={rec.ai_label}")
    check(rec.decision == REJECT_DEFECT and rec.sort_sent, f"decision={rec.decision}")
    s = rig.fake.sent_sorts[-1]
    check(s.target == SortTarget.REJECT and s.reason == SortReason.DEFECT, f"SORT: target={s.target} reason={s.reason}")
    check(after["cnt_reject_defect"] == before["cnt_reject_defect"] + 1, "cnt_reject_defect không tăng")
    t = rig.telemetry_for(rec.pkg_id, REJECT_DEFECT)
    check("defect_image" in t and len(t["defect_image"]) > 0, "telemetry thiếu defect_image")
    check(len(t["defect_image"]) <= rig.cfg.thingsboard.image_max_kb * 1024, "defect_image vượt image_max_kb")
    check(bool(t.get("defect_label")), "telemetry thiếu defect_label")
    check(rec.defect_image_path is not None and Path(rec.defect_image_path).exists(), "ảnh lỗi chưa được lưu")
    return f"REJECT_DEFECT, nhãn={t.get('defect_label')}, defect_image {len(t['defect_image']) // 1024} KB"


def sim_4(rig: SimRig) -> str:
    before = rig.counters()
    rec = rig.process(rig.IMG_FORGED)
    after = rig.counters()
    check(rec.qr_status == "BAD_SIGNATURE", f"qr_status={rec.qr_status}")
    check(rec.ai_label is None, "AI đã chạy dù QR không hợp lệ (phải bỏ qua AI)")
    check(rec.decision == REJECT_SECURITY and rec.sort_sent, f"decision={rec.decision}")
    s = rig.fake.sent_sorts[-1]
    check(s.target == SortTarget.REJECT and s.reason == SortReason.SECURITY, f"SORT: target={s.target} reason={s.reason}")
    check(after["cnt_reject_security"] == before["cnt_reject_security"] + 1, "cnt_reject_security không tăng")
    evs = rig.security_events(interfaces.SEC_QR_BAD_SIGNATURE)
    check(bool(evs), "DB không có security event QR_BAD_SIGNATURE")
    check(evs[0]["level"] == SecurityLevel.MAJOR.value, f"mức sự kiện {evs[0]['level']} (mong đợi MAJOR)")
    alarms = [d for d in rig.telemetry.sent if d.get("security_alert") and d.get("security_event_type") == interfaces.SEC_QR_BAD_SIGNATURE]
    check(bool(alarms), "telemetry chưa gửi security_alert BAD_SIGNATURE (alarm 'Security alert')")
    return f"REJECT_SECURITY/BAD_SIGNATURE, event {evs[0]['event_type']} [{evs[0]['level']}]"


def sim_5(rig: SimRig) -> str:
    rig.ensure_running()
    res = rig.rpc("emergencyStop")
    check(res.get("ok") is True, f"emergencyStop trả {res}")
    check(wait_for(lambda: rig.fake.state == State.ESTOP, 2.0), f"ESP32 giả đang {rig.fake.state.name}, mong đợi ESTOP")
    check(wait_for(lambda: rig.orch.conveyor_state == "ESTOP", 3.0), f"conveyor_state={rig.orch.conveyor_state}")
    audit = [r for r in rig.storage.recent_rpc_audit(20) if r["method"] == "emergencyStop"]
    check(bool(audit) and audit[0]["ok"] == 1, "rpc_audit chưa ghi emergencyStop")
    check(audit[0]["requested_by"] == "demo_check", f"requested_by={audit[0]['requested_by']}")
    rpc_logs = [d for d in rig.telemetry.sent if "rpc_log" in d and "emergencyStop" in d["rpc_log"]]
    check(bool(rpc_logs), "telemetry rpc_log chưa có emergencyStop")
    evs = rig.security_events(interfaces.SEC_ESTOP)
    check(bool(evs) and "REMOTE" in (evs[0]["extra_json"] or ""), "security event ESTOP nguồn REMOTE chưa có")
    check(not rig.fake.trigger_sensor(), "ESP32 giả vẫn nhận cảm biến khi đang ESTOP")
    # Khôi phục: Reset E-stop từ dashboard rồi START lại
    res2 = rig.rpc("resetEstop")
    check(res2.get("ok") is True, f"resetEstop trả {res2}")
    check(wait_for(lambda: rig.fake.state == State.IDLE, 2.0), "ESP32 giả không về IDLE sau resetEstop")
    rig.ensure_running()
    return "ESTOP qua RPC, conveyor_state=ESTOP, rpc_audit + rpc_log ghi lệnh; resetEstop -> IDLE -> RUNNING"


def sim_7(rig: SimRig) -> str:
    if not rig.storage.is_code_used("PKG000001"):
        first = rig.process(rig.IMG_OK_A)  # điều kiện tiên quyết: mã đã dùng một lần
        check(first.decision == SORT_A, f"lần quét đầu phải SORT_A, nhận {first.decision}")
    before = rig.counters()
    rec = rig.process(rig.IMG_DUP)
    after = rig.counters()
    check(rec.pkg_id == "PKG000001" and rec.qr_status == "DUPLICATE", f"pkg_id={rec.pkg_id} qr_status={rec.qr_status}")
    check(rec.decision == REJECT_SECURITY and rec.sort_sent, f"decision={rec.decision}")
    s = rig.fake.sent_sorts[-1]
    check(s.target == SortTarget.REJECT and s.reason == SortReason.SECURITY, f"SORT: target={s.target} reason={s.reason}")
    check(after["cnt_reject_security"] == before["cnt_reject_security"] + 1, "cnt_reject_security không tăng")
    check(bool(rig.security_events(interfaces.SEC_QR_DUPLICATE)), "DB không có security event QR_DUPLICATE")
    return "REJECT_SECURITY/DUPLICATE, event QR_DUPLICATE"


def sim_8(rig: SimRig) -> str:
    rig.ensure_running()
    rig.fake.press_estop()
    check(wait_for(lambda: rig.fake.state == State.ESTOP, 1.0), "ESP32 giả không vào ESTOP khi bấm nút")
    check(wait_for(lambda: rig.orch.conveyor_state == "ESTOP", 3.0), f"conveyor_state={rig.orch.conveyor_state}")
    evs = rig.security_events(interfaces.SEC_ESTOP)
    check(bool(evs) and "PHYSICAL" in (evs[0]["extra_json"] or ""), "security event ESTOP nguồn PHYSICAL chưa có")
    # Nút còn bấm: ESTOP_RESET phải bị NACK BAD_STATE -> RPC trả BAD_STATE
    res = rig.rpc("resetEstop")
    check(res.get("ok") is False and res.get("error") == "BAD_STATE", f"resetEstop khi nút còn bấm trả {res} (mong đợi BAD_STATE)")
    check(rig.fake.state == State.ESTOP, "ESP32 giả rời ESTOP dù nút còn bấm")
    # Nhả nút rồi reset
    rig.fake.release_estop()
    res2 = rig.rpc("resetEstop")
    check(res2.get("ok") is True, f"resetEstop sau khi nhả nút trả {res2}")
    check(wait_for(lambda: rig.fake.state == State.IDLE, 2.0), "ESP32 giả không về IDLE")
    check(wait_for(lambda: rig.orch.conveyor_state == "IDLE", 3.0), "Pi chưa nhận EVT_ESTOP_CLEARED (conveyor_state != IDLE)")
    rig.ensure_running()
    return "ESTOP(PHYSICAL); reset khi còn bấm -> BAD_STATE; nhả + resetEstop -> IDLE -> START -> RUNNING"


def sim_10(rig: SimRig) -> str:
    from sorter.anomaly.features import FeatureCollector
    from sorter.anomaly.rules import RuleEngine

    interfaces.metrics.snapshot_and_reset()  # cửa sổ mới
    for _ in range(3):
        res = rig.rpc("dumpSecrets")
        check(res.get("ok") is False and res.get("error") == "METHOD_NOT_ALLOWED", f"RPC lạ trả {res}")
    rejected = rig.security_events(interfaces.SEC_RPC_REJECTED)
    check(len(rejected) >= 3, f"chỉ có {len(rejected)} sự kiện RPC_REJECTED (mong đợi >= 3)")
    # Bơm 8 QR chữ ký sai vào bộ đếm (như attack_sim --scenario qr khi chạy chung tiến trình)
    interfaces.metrics.inc(F_QR_BAD_SIGNATURE, 8)
    collector = FeatureCollector(metrics=interfaces.metrics, ssh_reader=lambda since_s: 0, window_s=rig.cfg.anomaly.window_s)
    features = collector.collect(int(time.time() * 1000))
    check(features["rpc_rejected"] >= 3 and features["qr_bad_signature"] >= 8, f"đặc trưng cửa sổ: {features}")
    engine = RuleEngine(on_security_event=rig.app.security_log.emit_event)
    fired = engine.update(features, time.time())
    rules = {e.extra.get("rule") for e in fired}
    check("RULE_RPC_UNKNOWN" in rules, f"luật RULE_RPC_UNKNOWN không bắn (bắn: {rules})")
    check("RULE_QR_FORGERY" in rules, f"luật RULE_QR_FORGERY không bắn (bắn: {rules})")
    db_rules = rig.security_events(interfaces.SEC_RULE)
    check(len(db_rules) >= 2 and all(e["level"] == "CRITICAL" for e in db_rules[:2]), "DB chưa có RULE_TRIGGERED mức CRITICAL")
    crit = [d for d in rig.telemetry.sent if d.get("security_alert") and d.get("security_level") == "CRITICAL"]
    check(bool(crit), "telemetry chưa gửi security_alert CRITICAL (alarm)")
    note = ""
    if not Path(rig.cfg.anomaly.model_path).exists():
        note = "; anomaly_flag của Isolation Forest cần model đã train (>= 30 phút dữ liệu) — chỉ kiểm luật cứng A-04"
    return f"RPC lạ x3 + 8 QR giả -> {sorted(r for r in rules if r)} CRITICAL{note}"


def sim_11(rig: SimRig) -> str:
    rig.ensure_running()
    uart = rig.app.uart
    link_timeout = float(rig.fake.link_timeout_s)
    uart.ping_enabled = False  # "rút dây": Pi ngừng gửi khung; ESP32 giả không thấy khung hợp lệ
    t0 = time.monotonic()
    try:
        check(wait_for(lambda: rig.fake.state == State.FAULT, link_timeout + 2.0), f"ESP32 giả không vào FAULT sau {link_timeout + 2:.0f}s (đang {rig.fake.state.name})")
        dt = time.monotonic() - t0
        check(rig.fake.stats.link_lost >= 1, "FakeEsp32.stats.link_lost = 0")
        check(wait_for(lambda: rig.orch.conveyor_state == "FAULT", 3.0), f"Pi chưa nhận EVT_FAULT (conveyor_state={rig.orch.conveyor_state})")
        check(bool(rig.security_events(interfaces.SEC_LINK_LOST)), "DB chưa có security event ESP_LINK_LOST")
        fault_tel = [d for d in rig.telemetry.sent if d.get("fault_code") == "LINK_LOST"]
        check(bool(fault_tel), "telemetry chưa có fault_code=LINK_LOST")
    finally:
        uart.ping_enabled = True  # "cắm lại dây"
    # Thoát FAULT bằng resetEstop (RPC cho phép ở FAULT) rồi START lại
    wait_for(lambda: uart.state == State.FAULT, 3.0)
    res = rig.rpc("resetEstop")
    if res.get("ok") is not True:  # dự phòng nếu uart.state chưa cập nhật kịp
        res_u = uart.send_command(MsgType.ESTOP_RESET)
        check(res_u.ok, f"ESTOP_RESET sau FAULT thất bại: rpc={res} uart={res_u.error}")
    check(wait_for(lambda: rig.fake.state == State.IDLE, 2.0), "ESP32 giả không về IDLE sau reset")
    rig.ensure_running()
    return f"FAULT LINK_LOST sau {dt:.1f}s (ngưỡng {link_timeout:.1f}s), Pi ghi ESP_LINK_LOST; reset -> IDLE -> RUNNING"


# ---- OTA (6a, 6b, 12): dùng ota_app.manifest + demo_attacks, không cần ESP32 --------------------
_OTA_IMPORT_ERROR: str | None = None
try:
    sys.path.insert(0, str(REPO_ROOT / "ota-app"))
    from cryptography.hazmat.primitives.asymmetric import ec as _ec  # type: ignore[import-not-found]

    from ota_app import demo_attacks as _demo  # type: ignore[import-not-found]
    from ota_app import keys as _keys  # type: ignore[import-not-found]
    from ota_app.firmware import inspect_firmware as _inspect_firmware, make_fake_firmware as _make_fake_firmware  # type: ignore[import-not-found]
    from ota_app.manifest import MANIFEST_KEYS as _MANIFEST_KEYS, verify_release_dir as _verify_release_dir, write_release as _write_release  # type: ignore[import-not-found]
except Exception as _e:  # thiếu cryptography hoặc ota-app
    _OTA_IMPORT_ERROR = f"{type(_e).__name__}: {_e}"


@dataclass
class OtaRig:
    """Thư mục phát hành giả: khóa ECDSA P-256 + firmware giả 64 KB ký version_code=3 (thiết bị đang chạy 2)."""

    workdir: Path
    device_version_code: int = 2
    release_version_code: int = 3
    private_key: Any = None
    public_key: Any = None
    key_id: str = ""
    release_dir: Path = field(default_factory=Path)

    def __post_init__(self) -> None:
        if _OTA_IMPORT_ERROR:
            raise RuntimeError(_OTA_IMPORT_ERROR)
        self.private_key = _ec.generate_private_key(_ec.SECP256R1())
        self.public_key = self.private_key.public_key()
        self.key_id = _keys.key_id_of(self.public_key)
        fw = _make_fake_firmware(self.workdir / "fw" / "app.bin", version="1.0.2", size=65536)
        self.release_dir = self.workdir / "releases" / str(self.release_version_code)
        _write_release(fw, _inspect_firmware(fw), self.release_version_code, "1.0.2", self.private_key, self.key_id, self.release_dir)

    def verify(self, min_version_code: int | None = None) -> Any:
        return _verify_release_dir(self.release_dir, self.public_key, min_version_code=min_version_code)


_ota_rig_cache: dict[str, OtaRig] = {}


def get_ota_rig(workdir: Path) -> OtaRig:
    key = str(workdir)
    if key not in _ota_rig_cache:
        _ota_rig_cache[key] = OtaRig(workdir / "ota")
    return _ota_rig_cache[key]


def sim_6a(rig: SimRig) -> str:
    ota = get_ota_rig(rig.workdir)
    manifest_bytes = (ota.release_dir / "manifest.json").read_bytes()
    check(b"\n" not in manifest_bytes.strip() and b" " not in manifest_bytes, "manifest.json phải một dòng, không khoảng trắng")
    r = ota.verify(min_version_code=ota.device_version_code)
    check(r.ok and r.reason == "NONE", f"bản hợp lệ bị từ chối: {r}")
    check(tuple(sorted(r.manifest)) == tuple(sorted(_MANIFEST_KEYS)), f"manifest thiếu/thừa khóa: {sorted(r.manifest)}")
    check(r.manifest["version_code"] == ota.release_version_code and r.manifest["device_model"] == "sorter-esp32", "manifest sai version/device_model")
    return (f"manifest v{ota.release_version_code} ký ECDSA P-256 hợp lệ, hash/size khớp (xác minh cùng thứ tự ESP32 O-10..O-12); "
            "phần ESP32 tải + reboot + SUCCESS: kiểm trên phần cứng theo esp32/OTA_TEST.md")


def sim_6b(rig: SimRig) -> str:
    ota = get_ota_rig(rig.workdir)
    import random

    a = _demo.tamper_firmware(ota.release_dir, rng=random.Random(42))
    r1 = ota.verify(min_version_code=ota.device_version_code)
    check(not r1.ok and r1.reason == "HASH_MISMATCH", f"sửa 1 byte -> {r1} (mong đợi {a.expected_reason})")
    _demo.restore_valid(ota.release_dir)
    check(ota.verify(min_version_code=ota.device_version_code).ok, "khôi phục bản hợp lệ thất bại")
    b = _demo.forge_manifest(ota.release_dir, mode="hash")
    r2 = ota.verify(min_version_code=ota.device_version_code)
    check(not r2.ok and r2.reason == "SIGNATURE_INVALID", f"giả manifest -> {r2} (mong đợi {b.expected_reason})")
    _demo.restore_valid(ota.release_dir)
    check(ota.verify(min_version_code=ota.device_version_code).ok, "khôi phục bản hợp lệ thất bại (2)")
    return "sửa 1 byte -> HASH_MISMATCH; giả manifest -> SIGNATURE_INVALID; bản hợp lệ khôi phục OK"


def sim_12(rig: SimRig) -> str:
    ota = get_ota_rig(rig.workdir)
    running = ota.release_version_code  # giả định thiết bị đã lên v3
    c = _demo.downgrade_release(ota.release_dir, ota.private_key, ota.key_id, current_device_version_code=running)
    r_sig = ota.verify(min_version_code=None)
    check(r_sig.ok, f"bản hạ phiên bản phải có chữ ký/hash hợp lệ, nhận {r_sig}")
    r = ota.verify(min_version_code=running)
    check(not r.ok and r.reason == "ROLLBACK_REJECTED", f"hạ phiên bản -> {r} (mong đợi {c.expected_reason})")
    _demo.restore_valid(ota.release_dir)
    return f"manifest v{running - 1} ký hợp lệ nhưng <= v{running} đang chạy -> ROLLBACK_REJECTED"


MANUAL_9 = (
    "Cần ThingsBoard thật. Mở 2 cửa sổ trình duyệt:\n"
    "  (1) Đăng nhập viewer@sorter.local -> chỉ thấy dashboard 'Sorter — Giám sát', KHÔNG có khối điều khiển;\n"
    "      menu Devices chỉ thấy sorter-view; rpc_cli.py --user viewer@sorter.local --method getStatus -> HTTP 403.\n"
    "  (2) Đăng nhập tenant@thingsboard.org -> ThingsBoard hỏi mã TOTP 6 số (2FA) -> dashboard 'Sorter — Điều khiển' có nút.\n"
    "  (3) Gõ sai mật khẩu 5 lần -> tài khoản bị khóa (S-02). Cấu hình: thingsboard/SETUP.md mục 5, 6."
)

SIM_SCENARIOS: dict[str, Callable[[SimRig], str] | None] = {
    "1": sim_1, "2": sim_2, "3": sim_3, "4": sim_4, "5": sim_5, "6a": sim_6a, "6b": sim_6b,
    "7": sim_7, "8": sim_8, "9": None, "10": sim_10, "11": sim_11, "12": sim_12,
}


def run_sim(
    only: Iterable[Any] | str | None = None,
    config: str | Path | None = None,
    keep_dir: bool = False,
    workdir: str | Path | None = None,
) -> list[ScenarioResult]:
    """Chạy các kịch bản ở chế độ sim. Trả danh sách ScenarioResult (dùng được từ pytest)."""
    sids = normalize_only(only)
    cfg_path = Path(config) if config else PI_ROOT / "config.sim.yaml"
    tmp = Path(workdir) if workdir else Path(tempfile.mkdtemp(prefix="demo_check_"))
    tmp.mkdir(parents=True, exist_ok=True)
    results: list[ScenarioResult] = []
    rig: SimRig | None = None
    try:
        t_rig = time.perf_counter()
        rig = SimRig(cfg_path, tmp)
        log.info("Rig sim sẵn sàng sau %.1fs (workdir=%s)", time.perf_counter() - t_rig, tmp)
        for sid in sids:
            fn = SIM_SCENARIOS.get(sid)
            name = SCENARIO_NAMES[sid]
            t0 = time.perf_counter()
            if fn is None:
                results.append(ScenarioResult(sid, name, MANUAL, MANUAL_9))
                continue
            if sid in ("6a", "6b", "12") and _OTA_IMPORT_ERROR:
                results.append(ScenarioResult(sid, name, SKIP, f"thiếu ota-app/cryptography: {_OTA_IMPORT_ERROR}"))
                continue
            try:
                detail = fn(rig)
                results.append(ScenarioResult(sid, name, PASS, detail, time.perf_counter() - t0))
            except CheckFailed as e:
                results.append(ScenarioResult(sid, name, FAIL, str(e), time.perf_counter() - t0))
                log.error("Kịch bản %s FAIL: %s", sid, e)
            except Exception as e:  # lỗi bất ngờ cũng là FAIL (không được làm sập cả bảng)
                results.append(ScenarioResult(sid, name, FAIL, f"{type(e).__name__}: {e}", time.perf_counter() - t0))
                log.exception("Kịch bản %s lỗi bất ngờ", sid)
            # Mỗi kịch bản kết thúc phải để băng chạy lại cho kịch bản sau
            try:
                rig.ensure_running()
            except CheckFailed as e:
                log.warning("Không đưa băng về RUNNING sau kịch bản %s: %s", sid, e)
    except Exception as e:
        # Không dựng được rig: mọi kịch bản còn lại FAIL với cùng lý do
        log.exception("Không dựng được rig sim")
        done = {r.sid for r in results}
        for sid in sids:
            if sid not in done:
                results.append(ScenarioResult(sid, SCENARIO_NAMES[sid], FAIL, f"rig: {type(e).__name__}: {e}"))
    finally:
        if rig is not None:
            rig.close()
        _ota_rig_cache.pop(str(tmp), None)
        if keep_dir:
            log.info("Giữ thư mục tạm: %s", tmp)
        else:
            shutil.rmtree(tmp, ignore_errors=True)
    return results


# ---------------------------------------------------------------------------
# Chế độ thật (Pi + ESP32 + hộp thật)
# ---------------------------------------------------------------------------
def _ask(prompt: str) -> str:
    try:
        return input(prompt).strip()
    except EOFError:
        return ""


def _ask_pass(prompt: str) -> bool:
    ans = _ask(f"{prompt} [y/n] ").lower()
    return ans in ("y", "yes", "d", "đ", "dat", "đạt")


class RealRig:
    """App thật trên Pi; hướng dẫn thao tác và chờ bản ghi kiện kế tiếp."""

    def __init__(self, config_path: Path, port: str | None) -> None:
        cfg = load_config(config_path)
        if cfg.uart.port == "sim" or cfg.camera.source == "folder":
            raise SystemExit("--mode real cần config thật (uart.port=/dev/serial0, camera.source=picamera2)")
        if port:
            cfg.uart.port = port
        self.cfg = cfg
        self.app = App(cfg)
        self.app.start()
        self.orch = self.app.orchestrator
        self.uart = self.app.uart
        self.storage = self.app.storage

    def close(self) -> None:
        self.app.stop()

    def wait_package(self, timeout_s: float = 60.0) -> PackageRecord | None:
        n = self.orch.packages_done
        if not self.orch.wait_packages(n + 1, timeout_s):
            return None
        return self.orch.records[-1]

    def state(self) -> str:
        return self.orch.conveyor_state

    def ensure_running(self) -> None:
        if self.uart.state == State.RUNNING:
            return
        if self.uart.state in (State.ESTOP, State.FAULT):
            r = self.uart.send_command(MsgType.ESTOP_RESET)
            if not r.ok:
                print(f"  ! ESTOP_RESET: {r.error} (nút E-stop còn bấm?)")
            wait_for(lambda: self.uart.state == State.IDLE, 3.0)
        if self.uart.state == State.IDLE:
            self.orch.start_conveyor()
        wait_for(lambda: self.uart.state == State.RUNNING, 5.0)


def _real_package_scenario(rig: RealRig, instruction: str, expect: str, qr_expect: str | None = None) -> str:
    rig.ensure_running()
    cnt_before = rig.app.counters.to_telemetry()
    _ask(f"  {instruction}\n  Băng đang {rig.state()}. Đặt hộp lên băng rồi Enter (chờ tối đa 60 s)... ")
    rec = rig.wait_package(60.0)
    check(rec is not None, "không có kiện nào được xử lý trong 60 s (cảm biến? UART?)")
    assert rec is not None
    cnt_after = rig.app.counters.to_telemetry()
    print(f"  -> pkg_id={rec.pkg_id} qr={rec.qr_status} ai={rec.ai_label} ({rec.ai_conf}) decision={rec.decision} "
          f"latency={rec.latency_ms:.0f} ms sort_sent={rec.sort_sent}")
    check(rec.decision == expect, f"decision={rec.decision}, mong đợi {expect}")
    if qr_expect:
        check(rec.qr_status == qr_expect, f"qr_status={rec.qr_status}, mong đợi {qr_expect}")
    check(rec.sort_sent, f"SORT không được ACK ({rec.sort_error})")
    wait_for(lambda: rig.uart.state == State.RUNNING, 10.0)
    diff = {k: cnt_after[k] - cnt_before[k] for k in cnt_after if cnt_after[k] != cnt_before[k]}
    return f"{rec.decision} ({rec.qr_status}/{rec.ai_label}), latency {rec.latency_ms:.0f} ms, bộ đếm {diff}"


def real_1(rig: RealRig) -> str:
    return _real_package_scenario(rig, "Hộp TỐT dán QR hợp lệ kho A.", SORT_A, "VALID") + "; nhìn: servo A gạt, bíp ngắn, dashboard cnt_a tăng"


def real_2(rig: RealRig) -> str:
    return _real_package_scenario(rig, "Hộp TỐT dán QR hợp lệ kho B.", SORT_B, "VALID") + "; nhìn: servo B gạt, cnt_b tăng"


def real_3(rig: RealRig) -> str:
    return _real_package_scenario(rig, "Hộp RÁCH/MÓP dán QR hợp lệ.", REJECT_DEFECT, "VALID") + "; nhìn: LED đỏ 1 s, ảnh lỗi + nhãn trên dashboard"


def real_4(rig: RealRig) -> str:
    return _real_package_scenario(rig, "Hộp dán QR GIẢ (qr_generator.py --forge).", REJECT_SECURITY, "BAD_SIGNATURE") + "; nhìn: đỏ nháy + 3 bíp, alarm 'Security alert'"


def real_7(rig: RealRig) -> str:
    return _real_package_scenario(rig, "Hộp dán QR hợp lệ ĐÃ QUÉT ở kịch bản 1 (hoặc bản photocopy).", REJECT_SECURITY, "DUPLICATE")


def real_5(rig: RealRig) -> str:
    rig.ensure_running()
    _ask("  Trên dashboard Admin bấm nút đỏ DỪNG KHẨN CẤP (emergencyStop), rồi Enter... ")
    check(wait_for(lambda: rig.state() == "ESTOP", 15.0), f"conveyor_state={rig.state()} (mong đợi ESTOP)")
    audit = [r for r in rig.storage.recent_rpc_audit(10) if r["method"] == "emergencyStop"]
    check(bool(audit), "rpc_audit chưa có emergencyStop (RPC chưa tới Pi?)")
    _ask("  Bấm 'Reset E-stop' (xác nhận) trên dashboard, rồi Enter... ")
    check(wait_for(lambda: rig.uart.state == State.IDLE, 15.0), f"ESP32 đang {rig.uart.state}, mong đợi IDLE")
    rig.ensure_running()
    return f"ESTOP qua RPC bởi {audit[0]['requested_by']}; reset -> IDLE -> RUNNING"


def real_8(rig: RealRig) -> str:
    rig.ensure_running()
    _ask("  BẤM và GIỮ nút E-stop vật lý (băng phải dừng ngay), rồi Enter... ")
    check(wait_for(lambda: rig.state() == "ESTOP", 5.0), f"conveyor_state={rig.state()} (mong đợi ESTOP)")
    r = rig.uart.send_command(MsgType.ESTOP_RESET)
    check(not r.ok and r.error == "NACK_BAD_STATE", f"ESTOP_RESET khi còn bấm trả {r.error} (mong đợi NACK_BAD_STATE)")
    _ask("  NHẢ nút E-stop, rồi Enter... ")
    r2 = rig.uart.send_command(MsgType.ESTOP_RESET)
    check(r2.ok, f"ESTOP_RESET sau khi nhả trả {r2.error}")
    check(wait_for(lambda: rig.uart.state == State.IDLE, 3.0), "ESP32 không về IDLE")
    rig.ensure_running()
    return "ESTOP vật lý; reset khi còn bấm -> NACK BAD_STATE; nhả + reset -> IDLE -> RUNNING"


def real_11(rig: RealRig) -> str:
    rig.ensure_running()
    _ask("  RÚT dây UART (TX/RX) giữa Pi và ESP32 khi băng đang chạy, rồi Enter; băng phải dừng sau ~2 s... ")
    check(wait_for(lambda: not rig.uart.connected, 5.0), "Pi vẫn thấy liên kết (esp_link=true) sau khi rút dây")
    _ask("  CẮM lại dây UART, rồi Enter... ")
    check(wait_for(lambda: rig.uart.connected and rig.uart.state == State.FAULT, 10.0), f"ESP32 trạng thái {rig.uart.state} (mong đợi FAULT LINK_LOST)")
    evs = [e for e in rig.storage.recent_security_events(10) if e["event_type"] == interfaces.SEC_LINK_LOST]
    check(bool(evs), "chưa nhận EVT_FAULT(LINK_LOST)")
    r = rig.uart.send_command(MsgType.ESTOP_RESET)
    check(r.ok, f"ESTOP_RESET sau FAULT: {r.error}")
    rig.ensure_running()
    return "FAULT LINK_LOST khi rút dây; cắm lại + ESTOP_RESET -> IDLE -> RUNNING"


def _real_manual(instruction: str) -> Callable[[RealRig], str]:
    def fn(rig: RealRig) -> str:
        print("  " + instruction.replace("\n", "\n  "))
        check(_ask_pass("  Kết quả đúng mong đợi?"), "người vận hành đánh giá KHÔNG đạt")
        return "người vận hành xác nhận đạt"

    return fn


REAL_SCENARIOS: dict[str, Callable[[RealRig], str]] = {
    "1": real_1, "2": real_2, "3": real_3, "4": real_4, "5": real_5, "7": real_7, "8": real_8, "11": real_11,
    "6a": _real_manual("Băng dừng (IDLE). App OTA phát hành bản hợp lệ (version_code mới). Dashboard -> 'Kiểm tra cập nhật firmware' (otaCheck).\n"
                       "Mong đợi: ota_stage CHECKING -> DOWNLOADING -> VERIFIED -> REBOOTING -> SUCCESS; fw_version mới trên dashboard; app OTA nhận report SUCCESS."),
    "6b": _real_manual("App OTA tab Demo -> 'Sửa 1 byte firmware'. Dashboard -> otaCheck.\n"
                       "Mong đợi: ota_stage REJECTED, ota_reason HASH_MISMATCH; ESP32 không reboot; fw_version giữ nguyên."),
    "9": _real_manual(MANUAL_9),
    "10": _real_manual("Trên laptop: cd pi && python tools/attack_sim.py --i-own-this-system --scenario rpc --tb-url http://<laptop>:8080 --user <admin> --device <id>\n"
                       "(thêm --scenario qr/ssh nếu có). Mong đợi: alarm 'Security alert' RPC_REJECTED; trong <= 20 s anomaly_flag=true + alarm 'Anomaly' kèm anomaly_top_features\n"
                       "(cần model anomaly_if.joblib đã train; chưa có model thì chỉ có alarm luật cứng RULE_*)."),
    "12": _real_manual("App OTA tab Demo -> 'Hạ phiên bản' (ký hợp lệ version_code = hiện tại - 1). Dashboard -> otaCheck.\n"
                       "Mong đợi: ota_stage REJECTED, ota_reason ROLLBACK_REJECTED; không tải firmware."),
}


def run_real(port: str | None, config: str | Path, only: Iterable[Any] | str | None = None) -> list[ScenarioResult]:
    sids = normalize_only(only)
    results: list[ScenarioResult] = []
    rig = RealRig(Path(config), port)
    try:
        print(f"Kết nối ESP32: state={rig.uart.state} fw={rig.orch.fw_version} connected={rig.uart.connected}")
        for sid in sids:
            name = SCENARIO_NAMES[sid]
            print(f"\n=== Kịch bản {sid}: {name} ===")
            if _ask("  Enter để bắt đầu, 's' để bỏ qua: ").lower() == "s":
                results.append(ScenarioResult(sid, name, SKIP, "người vận hành bỏ qua"))
                continue
            t0 = time.perf_counter()
            try:
                detail = REAL_SCENARIOS[sid](rig)
                results.append(ScenarioResult(sid, name, PASS, detail, time.perf_counter() - t0))
                print("  => PASS")
            except CheckFailed as e:
                results.append(ScenarioResult(sid, name, FAIL, str(e), time.perf_counter() - t0))
                print(f"  => FAIL: {e}")
            except KeyboardInterrupt:
                results.append(ScenarioResult(sid, name, SKIP, "Ctrl-C"))
                break
            except Exception as e:
                results.append(ScenarioResult(sid, name, FAIL, f"{type(e).__name__}: {e}", time.perf_counter() - t0))
                log.exception("Kịch bản %s lỗi", sid)
    finally:
        rig.close()
    return results


# ---------------------------------------------------------------------------
# Bảng kết quả + CLI
# ---------------------------------------------------------------------------
def format_table(results: list[ScenarioResult]) -> str:
    rows = [f"{'#':>3}  {'Kết quả':<7} {'Kịch bản':<60} {'giây':>5}  Lý do / chi tiết"]
    rows.append("-" * 120)
    for r in results:
        detail = r.detail.split("\n", 1)[0]  # MANUAL: chỉ dòng đầu, hướng dẫn đầy đủ in bên dưới bảng
        rows.append(f"{r.sid:>3}  {r.status:<7} {r.name[:60]:<60} {r.seconds:5.1f}  {detail}")
    counts = {s: sum(1 for r in results if r.status == s) for s in (PASS, FAIL, SKIP, MANUAL)}
    rows.append("-" * 120)
    rows.append(f"Tổng: {counts[PASS]} PASS, {counts[FAIL]} FAIL, {counts[SKIP]} SKIP, {counts[MANUAL]} MANUAL / {len(results)} kịch bản")
    manual = [r for r in results if r.status == MANUAL and "\n" in r.detail]
    for r in manual:
        rows.append(f"\nHướng dẫn kiểm tra tay kịch bản {r.sid}:\n" + r.detail)
    return "\n".join(rows)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Kiểm thử các kịch bản demo 5.1/5.2 (sim tự động hoặc hướng dẫn trên phần cứng)")
    ap.add_argument("--mode", choices=["sim", "real"], default="sim")
    ap.add_argument("--only", default=None, help="danh sách kịch bản, ví dụ 1,2,3,6a,12 (mặc định: tất cả)")
    ap.add_argument("--config", default=None, help="config.yaml (sim: mặc định pi/config.sim.yaml; real: /etc/sorter/config.yaml)")
    ap.add_argument("--port", default=None, help="cổng UART cho --mode real (ghi đè uart.port)")
    ap.add_argument("--keep", action="store_true", help="sim: giữ thư mục tạm (DB, log, ảnh lỗi) để xem")
    ap.add_argument("--log-level", default="WARNING", help="DEBUG | INFO | WARNING (log của sorter-core)")
    args = ap.parse_args(argv)
    logging.basicConfig(level=getattr(logging, args.log_level.upper(), logging.WARNING),
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        if args.mode == "sim":
            results = run_sim(args.only, args.config, keep_dir=args.keep)
        else:
            results = run_real(args.port, args.config or "/etc/sorter/config.yaml", args.only)
    except ValueError as e:
        ap.error(str(e))
        return 2
    print(format_table(results))
    return 1 if any(r.status == FAIL for r in results) else 0


if __name__ == "__main__":
    sys.exit(main())
