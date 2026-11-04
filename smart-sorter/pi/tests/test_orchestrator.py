"""P-08 / P-14 — Test end-to-end ở chế độ sim với ảnh fixtures: FolderCamera + DummyClassifier + QrVerifier
+ UartLink + FakeEsp32 + LogTelemetrySink. Kiểm tra lệnh SORT nhận được ở ESP32 giả và telemetry."""
from __future__ import annotations

import time
from pathlib import Path

import cv2
import numpy as np
import pytest

from sorter.camera import FolderCamera
from sorter.classifier import DummyClassifier
from sorter.config import load_config
from sorter.counters import Counters
from sorter.decision import REJECT_DEFECT, REJECT_FAILSAFE, REJECT_SECURITY, SORT_A, SORT_B
from sorter.defect_images import DefectImageStore
from sorter.fake_esp32 import FakeEsp32
from sorter.interfaces import SEC_QR_BAD_SIGNATURE, SEC_QR_DUPLICATE, SEC_QR_EXPIRED, LogTelemetrySink
from sorter.orchestrator import Orchestrator
from sorter.protocol_gen import MsgType, SortReason, SortTarget, State
from sorter.qr_reader import read_qr
from sorter.qr_security import QrVerifier, load_hmac_key
from sorter.security_events import SecurityLog
from sorter.storage import Storage
from sorter.uart_link import UartLink

PI = Path(__file__).resolve().parents[1]
IMAGES = PI / "tests" / "fixtures" / "images"


def _qr_decodable() -> bool:
    p = IMAGES / "01_ok_A.jpg"
    if not p.exists():
        return False
    img = cv2.cvtColor(cv2.imread(str(p)), cv2.COLOR_BGR2RGB)
    return read_qr([img]).status == "VALID"


pytestmark = pytest.mark.skipif(
    not _qr_decodable(),
    reason="Không có ảnh fixtures hoặc không bộ giải mã QR nào (pyzbar/cv2.QRCodeDetector) đọc được chúng",
)


class SimRig:
    def __init__(self, tmp_path: Path):
        cfg = load_config(PI / "config.sim.yaml")
        cfg.storage.db_path = str(tmp_path / "sorter.db")
        cfg.storage.defect_dir = str(tmp_path / "defects")
        cfg.storage.log_dir = str(tmp_path / "log")
        cfg.camera.settle_ms = 0
        cfg.thingsboard.health_interval_s = 1
        self.cfg = cfg
        self.storage = Storage(cfg.storage.db_path)
        self.counters = Counters(self.storage)
        self.telemetry = LogTelemetrySink()
        self.security = SecurityLog(self.storage, self.telemetry)
        self.verifier = QrVerifier(load_hmac_key(cfg.qr.hmac_key_file), self.storage, cfg.qr.max_age_days, cfg.qr.future_skew_s)
        self.camera = FolderCamera(cfg.camera.folder)
        self.classifier = DummyClassifier(cfg.ai, label_hint=self.camera.next_name)
        # t_clear 125-250 ms; t_decision_timeout giữ 3 s thật (60000 ms * 0.05)
        self.fake = FakeEsp32(params={10: 60000}, time_scale=0.05, status_period_s=0)
        self.fake.start()
        self.uart = UartLink(cfg.uart, transport=self.fake.pi_transport())
        self.uart.start()
        self.orch = Orchestrator(
            cfg, self.camera, self.classifier, self.verifier, self.uart, self.storage, self.counters,
            self.telemetry, self.security, fake=self.fake,
            defect_store=DefectImageStore(cfg.storage.defect_dir, 3, cfg.thingsboard.image_max_kb),
        )

    def close(self):
        self.orch.stop()
        self.uart.stop()
        self.fake.stop()
        self.storage.close()

    def telemetry_for(self, pkg_id: str | None, decision: str | None = None) -> dict:
        for d in reversed(self.telemetry.sent):
            if "decision" in d and d.get("pkg_id") == (pkg_id or "") and (decision is None or d["decision"] == decision):
                return d
        raise AssertionError(f"không có telemetry cho {pkg_id}")


@pytest.fixture
def rig(tmp_path):
    r = SimRig(tmp_path)
    yield r
    r.close()


def test_end_to_end_eight_images(rig: SimRig):
    rig.orch.start()
    assert rig.fake.state == State.RUNNING
    recs = rig.orch.run_once_sim(8, gap_s=0.0)
    by_img = {r.image_name: r for r in recs}

    assert by_img["01_ok_A.jpg"].decision == SORT_A and by_img["01_ok_A.jpg"].qr_status == "VALID"
    assert by_img["02_ok_B.jpg"].decision == SORT_B
    assert by_img["03_defect_tear_A.jpg"].decision == REJECT_DEFECT and by_img["03_defect_tear_A.jpg"].ai_label == "DEFECT"
    assert by_img["04_forged_A.jpg"].decision == REJECT_SECURITY and by_img["04_forged_A.jpg"].qr_status == "BAD_SIGNATURE"
    assert by_img["05_noqr.jpg"].decision == REJECT_SECURITY and by_img["05_noqr.jpg"].qr_status == "NO_QR"
    assert by_img["06_expired_A.jpg"].qr_status == "EXPIRED"
    assert by_img["07_dup_A.jpg"].qr_status == "DUPLICATE" and by_img["07_dup_A.jpg"].pkg_id == "PKG000001"
    assert by_img["08_uncertain_A.jpg"].decision == REJECT_DEFECT and by_img["08_uncertain_A.jpg"].ai_label == "UNCERTAIN"
    assert all(r.sort_sent for r in recs)
    assert all(r.ai_label is None for r in recs if r.qr_status != "VALID")  # không chạy AI khi QR sai

    # Lệnh SORT mà ESP32 giả nhận được: đúng target/reason/pkg_seq
    sorts = rig.fake.sent_sorts
    assert [(s.target, s.reason, s.pkg_seq) for s in sorts] == [
        (SortTarget.A, SortReason.OK, 1),
        (SortTarget.B, SortReason.OK, 2),
        (SortTarget.REJECT, SortReason.DEFECT, 3),
        (SortTarget.REJECT, SortReason.SECURITY, 4),
        (SortTarget.REJECT, SortReason.SECURITY, 5),
        (SortTarget.REJECT, SortReason.SECURITY, 6),
        (SortTarget.REJECT, SortReason.SECURITY, 7),
        (SortTarget.REJECT, SortReason.DEFECT, 8),
    ]

    # Telemetry 4.3
    t1 = rig.telemetry_for("PKG000001", SORT_A)
    assert t1["warehouse"] == "A" and t1["qr_status"] == "VALID" and t1["ai_label"] == "OK" and t1["cnt_a"] >= 1
    assert "latency_ms" in t1 and t1["reason"] == "OK"
    t3 = rig.telemetry_for("PKG000003")
    assert t3["decision"] == REJECT_DEFECT and "defect_image" in t3 and t3["defect_label"] == "tear"
    assert len(t3["defect_image"]) <= rig.cfg.thingsboard.image_max_kb * 1024
    t4 = rig.telemetry_for("PKG000004")
    assert t4["decision"] == REJECT_SECURITY and t4["qr_status"] == "BAD_SIGNATURE"

    # Sự kiện bảo mật
    sec_types = [d["security_event_type"] for d in rig.telemetry.sent if d.get("security_alert")]
    assert SEC_QR_BAD_SIGNATURE in sec_types and SEC_QR_DUPLICATE in sec_types and SEC_QR_EXPIRED in sec_types
    db_sec = {r["event_type"] for r in rig.storage.recent_security_events(20)}
    assert SEC_QR_BAD_SIGNATURE in db_sec

    # DB + bộ đếm
    assert rig.storage.count_packages() == 8
    cnt = rig.counters.to_telemetry()
    assert cnt == {"cnt_total": 8, "cnt_a": 1, "cnt_b": 1, "cnt_reject_defect": 2, "cnt_reject_security": 4, "cnt_reject_failsafe": 0}
    assert rig.storage.count_used_codes() == 4  # PKG000001, 2, 3, 8 (06 hết hạn và 04 giả không được ghi)
    assert len(list(Path(rig.cfg.storage.defect_dir).glob("*.jpg"))) == 2
    assert rig.telemetry.attributes.get("fw_version") == "10000"

    # health telemetry có conveyor_state/esp_link
    health = [d for d in rig.telemetry.sent if "cpu_load" in d]
    assert health and health[-1]["esp_link"] is True and "conveyor_state" in health[-1]


def test_pipeline_exception_is_failsafe(rig: SimRig):
    class BadCamera(FolderCamera):
        def capture(self):
            raise RuntimeError("camera chết")

    rig.orch.camera = BadCamera(rig.cfg.camera.folder)
    rig.orch.start()
    recs = rig.orch.run_once_sim(1)
    assert recs[0].decision == REJECT_FAILSAFE and recs[0].sort_sent
    assert rig.fake.sent_sorts[-1].target == SortTarget.REJECT and rig.fake.sent_sorts[-1].reason == SortReason.FAILSAFE
    kinds = {r["kind"] for r in rig.storage.recent_system_events(10)}
    assert "PIPELINE_ERROR" in kinds
    assert rig.counters.get("cnt_reject_failsafe") == 1


def test_sort_nack_recorded_as_failsafe(rig: SimRig):
    # ESP32 đã tự failsafe (không còn DETECTED) -> SORT bị NACK -> ghi REJECT_FAILSAFE, không bao giờ "SORT_A"
    rig.orch.start()
    rig.fake.params[10] = 1  # decision timeout cực ngắn (1 ms * scale)
    recs = rig.orch.run_once_sim(1)
    assert not recs[0].sort_sent and recs[0].decision == REJECT_FAILSAFE
    assert rig.fake.sent_sorts == []
    assert rig.counters.get("cnt_reject_failsafe") == 1


def test_start_not_sent_when_estop(rig: SimRig):
    rig.fake.press_estop()
    time.sleep(0.05)
    rig.orch.start()
    assert rig.fake.state == State.ESTOP
    assert rig.orch.conveyor_state == "ESTOP"
    assert not rig.fake.trigger_sensor()


def test_estop_event_logged(rig: SimRig):
    rig.orch.start()
    rig.fake.press_estop()
    end = time.monotonic() + 2
    while time.monotonic() < end and rig.orch.conveyor_state != "ESTOP":
        time.sleep(0.01)
    assert rig.orch.conveyor_state == "ESTOP"
    assert any(d.get("security_event_type") == "ESTOP" for d in rig.telemetry.sent)
