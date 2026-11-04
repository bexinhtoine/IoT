"""P-08 — Vòng điều phối: chờ EVT_PKG_DETECTED -> pipeline một kiện (2.5) -> SORT; xử lý các sự kiện khác.

Pipeline một kiện (process_package):
  settle_ms -> chụp tới max_qr_frames khung (dừng sớm khi đọc được QR) -> read_qr -> verify (S-03)
  -> nếu VALID: classifier.predict(frame, roi) -> decide (P-06) -> SORT qua UART (chờ ACK, gửi lại)
  -> ghi packages, counters, telemetry (4.3), ảnh lỗi, sự kiện bảo mật.
Mọi exception -> REJECT_FAILSAFE + system event + cố gửi SORT REJECT/FAILSAFE; vòng chính không bao giờ chết.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from .camera import Camera
from .config import SorterConfig
from .counters import Counters
from .decision import REJECT_DEFECT, REJECT_FAILSAFE, REJECT_SECURITY, Decision, decide
from .defect_images import DefectImageStore
from .health import read_health
from .interfaces import (
    F_ESTOP_COUNT,
    F_PACKAGES,
    SEC_ESTOP,
    SEC_LINK_LOST,
    SEC_QR_BAD_FORMAT,
    SEC_QR_BAD_SIGNATURE,
    SEC_QR_BAD_WAREHOUSE,
    SEC_QR_DUPLICATE,
    SEC_QR_EXPIRED,
    SEC_QR_FUTURE,
    SEC_QR_MULTI,
    ClassResult,
    Classifier,
    SecurityLevel,
    TelemetrySink,
    metrics,
)
from .protocol_gen import (
    EstopSource,
    EvtEstopPayload,
    EvtFaultPayload,
    EvtOtaStatusPayload,
    EvtPkgDetectedPayload,
    EvtSortDonePayload,
    FaultCode,
    MsgType,
    OtaReason,
    OtaStage,
    SortPayload,
    SortReason,
    SortTarget,
    StartPayload,
    State,
    StatusPayload,
)
from .qr_reader import read_qr
from .qr_security import (
    BAD_FORMAT,
    BAD_SIGNATURE,
    BAD_WAREHOUSE,
    DUPLICATE,
    EXPIRED,
    FUTURE_TIMESTAMP,
    MULTI_QR,
    NO_QR,
    VALID,
    QrResult,
    QrVerifier,
)
from .security_events import SecurityLog
from .storage import Storage
from .uart_codec import Frame
from .uart_link import UartLink

log = logging.getLogger(__name__)

# Mã QR không hợp lệ -> loại sự kiện bảo mật (NO_QR không phải tấn công nên chỉ log INFO, không alarm)
QR_STATUS_TO_SEC: dict[str, str] = {
    BAD_SIGNATURE: SEC_QR_BAD_SIGNATURE,
    DUPLICATE: SEC_QR_DUPLICATE,
    BAD_FORMAT: SEC_QR_BAD_FORMAT,
    BAD_WAREHOUSE: SEC_QR_BAD_WAREHOUSE,
    EXPIRED: SEC_QR_EXPIRED,
    FUTURE_TIMESTAMP: SEC_QR_FUTURE,
    MULTI_QR: SEC_QR_MULTI,
}


@dataclass
class PackageRecord:
    """Kết quả xử lý một kiện (cũng là bản ghi packages)."""

    pkg_seq: int
    ts_ms: int
    decision: str = REJECT_FAILSAFE
    target: str = "REJECT"
    reason: str = "FAILSAFE"
    qr_status: str = NO_QR
    pkg_id: str | None = None
    warehouse: str | None = None
    ai_label: str | None = None
    ai_conf: float | None = None
    ai_types: dict[str, float] = field(default_factory=dict)
    t_capture_ms: float = 0.0
    t_qr_ms: float = 0.0
    t_verify_ms: float = 0.0
    t_ai_ms: float = 0.0
    latency_ms: float = 0.0
    defect_image_path: str | None = None
    sort_sent: bool = False
    sort_error: str | None = None
    error: str | None = None
    image_name: str | None = None

    def to_db(self) -> dict[str, Any]:
        return {
            "ts_ms": self.ts_ms,
            "pkg_seq": self.pkg_seq,
            "pkg_id": self.pkg_id,
            "warehouse": self.warehouse,
            "qr_status": self.qr_status,
            "ai_label": self.ai_label,
            "ai_conf": self.ai_conf,
            "ai_types_json": json.dumps(self.ai_types, ensure_ascii=False) if self.ai_types else None,
            "decision": self.decision,
            "reason": self.reason,
            "target": self.target,
            "t_capture_ms": round(self.t_capture_ms, 2),
            "t_qr_ms": round(self.t_qr_ms, 2),
            "t_verify_ms": round(self.t_verify_ms, 2),
            "t_ai_ms": round(self.t_ai_ms, 2),
            "latency_ms": round(self.latency_ms, 2),
            "defect_image_path": self.defect_image_path,
        }

    def to_telemetry(self) -> dict[str, Any]:
        """Nhóm "Kiện hàng" của 4.3."""
        d: dict[str, Any] = {
            "pkg_id": self.pkg_id or "",
            "warehouse": self.warehouse or "",
            "qr_status": self.qr_status,
            "ai_label": self.ai_label or "",
            "ai_conf": round(float(self.ai_conf), 4) if self.ai_conf is not None else 0.0,
            "decision": self.decision,
            "reason": self.reason,
            "latency_ms": int(round(self.latency_ms)),
        }
        if self.ai_types and self.ai_label and self.ai_label != "OK":  # chỉ gửi loại lỗi khi hàng không OK
            d["ai_defect_type"] = max(self.ai_types.items(), key=lambda kv: kv[1])[0]
        return d


class Orchestrator:
    """Vòng chính (P-08). uart phải đã start() trước khi gọi start()."""

    def __init__(
        self,
        cfg: SorterConfig,
        camera: Camera,
        classifier: Classifier,
        verifier: QrVerifier,
        uart: UartLink,
        storage: Storage,
        counters: Counters,
        telemetry: TelemetrySink,
        security_log: SecurityLog,
        fake: Any = None,
        defect_store: DefectImageStore | None = None,
    ) -> None:
        self.cfg = cfg
        self.camera = camera
        self.classifier = classifier
        self.verifier = verifier
        self.uart = uart
        self.storage = storage
        self.counters = counters
        self.telemetry = telemetry
        self.security_log = security_log
        self.fake = fake
        self.defect_store = defect_store or DefectImageStore(
            cfg.storage.defect_dir, cfg.storage.max_defect_images, cfg.thingsboard.image_max_kb
        )
        self.conveyor_state: str = "UNKNOWN"
        self.fw_version: str | None = None
        self.records: list[PackageRecord] = []
        self.packages_done = 0
        self._pkg_cond = threading.Condition()
        self._stop = threading.Event()
        self._main_thread: threading.Thread | None = None
        self._health_thread: threading.Thread | None = None
        self._pipeline_lock = threading.Lock()  # mỗi lúc chỉ xử lý 1 kiện
        self._last_frame: np.ndarray | None = None

    # ------------------------------------------------------------------ vòng đời
    def start(self, send_start: bool = True) -> None:
        self._stop.clear()
        self._main_thread = threading.Thread(target=self._main_loop, name="orchestrator", daemon=True)
        self._main_thread.start()
        self._health_thread = threading.Thread(target=self._health_loop, name="health", daemon=True)
        self._health_thread.start()
        if send_start:
            self.start_conveyor()

    def stop(self) -> None:
        self._stop.set()
        for t in (self._main_thread, self._health_thread):
            if t is not None and t.is_alive() and t is not threading.current_thread():
                t.join(timeout=3.0)
        log.info("Orchestrator dừng; đã xử lý %d kiện", self.packages_done)

    def start_conveyor(self) -> bool:
        """Gửi START(belt_speed) khi ESP32 ở IDLE; ESTOP/FAULT thì không gửi."""
        st = self.uart.get_status(timeout_ms=max(self.cfg.uart.ack_timeout_ms, 500))
        state = State(st.state) if st is not None else self.uart.state
        if st is not None:
            self._update_fw_version(st)
        if state is None:
            log.warning("ESP32 chưa trả lời GET_STATUS; không gửi START")
            self._set_conveyor_state("UNKNOWN")
            return False
        self._set_conveyor_state(state.name)
        if state in (State.ESTOP, State.FAULT):
            log.warning("ESP32 đang %s — không gửi START (cần ESTOP_RESET)", state.name)
            return False
        if state != State.IDLE:
            log.info("ESP32 đang %s — không cần START", state.name)
            return state in (State.RUNNING, State.DETECTED, State.SORTING)
        res = self.uart.send_command(MsgType.START, StartPayload(self.cfg.belt_speed))
        if not res.ok:
            log.error("START thất bại: %s", res.error)
            self.storage.insert_system_event("START_FAILED", str(res.error))
            return False
        self._set_conveyor_state(State.RUNNING.name)
        log.info("Băng chuyền chạy (speed=%d%%)", self.cfg.belt_speed)
        return True

    # ------------------------------------------------------------------ vòng chính
    def _main_loop(self) -> None:
        while not self._stop.is_set():
            frame = self.uart.next_event(timeout_s=0.2)
            if frame is None:
                continue
            try:
                self._dispatch(frame)
            except Exception as e:  # không để vòng chính chết
                log.exception("Lỗi xử lý sự kiện %s: %s", frame, e)
                self._safe_system_event("EVENT_ERROR", f"{frame.type_name}: {e}")

    def _dispatch(self, f: Frame) -> None:
        t = f.type
        if t == MsgType.EVT_PKG_DETECTED:
            p = EvtPkgDetectedPayload.unpack(f.payload)
            self._set_conveyor_state(State.DETECTED.name)
            self.process_package(p.pkg_seq)
        elif t == MsgType.EVT_SORT_DONE:
            p = EvtSortDonePayload.unpack(f.payload)
            target = SortTarget(p.target).name if p.target in (1, 2, 3) else str(p.target)
            log.info("SORT_DONE pkg_seq=%d target=%s", p.pkg_seq, target)
            self._set_conveyor_state(State.RUNNING.name)
            self._send_telemetry({"sort_done_pkg_seq": p.pkg_seq, "sort_done_target": target})
        elif t == MsgType.EVT_ESTOP:
            p = EvtEstopPayload.unpack(f.payload)
            src = EstopSource(p.source).name if p.source in (1, 2) else str(p.source)
            metrics.inc(F_ESTOP_COUNT)
            self._set_conveyor_state(State.ESTOP.name)
            self.security_log.emit(SEC_ESTOP, f"Dừng khẩn cấp ({src})", SecurityLevel.INFO, source=src)
            self._safe_system_event("ESTOP", src)
        elif t == MsgType.EVT_ESTOP_CLEARED:
            self._set_conveyor_state(State.IDLE.name)
            self._safe_system_event("ESTOP_CLEARED", "")
            log.info("E-stop đã được reset; ESP32 về IDLE (cần START để chạy lại)")
        elif t == MsgType.EVT_FAULT:
            p = EvtFaultPayload.unpack(f.payload)
            code = FaultCode(p.code).name if p.code in FaultCode._value2member_map_ else str(p.code)
            self._set_conveyor_state(State.FAULT.name)
            self._safe_system_event("FAULT", code)
            self._send_telemetry({"fault_code": code})
            if p.code == FaultCode.LINK_LOST:
                self.security_log.emit(SEC_LINK_LOST, "ESP32 báo mất liên kết với Pi (LINK_LOST)")
            elif p.code == FaultCode.DECISION_TIMEOUT:
                log.error("ESP32 tự loại kiện vì Pi không gửi SORT kịp (DECISION_TIMEOUT)")
        elif t == MsgType.EVT_OTA_STATUS:
            p = EvtOtaStatusPayload.unpack(f.payload)
            stage = OtaStage(p.stage).name if p.stage in OtaStage._value2member_map_ else str(p.stage)
            reason = OtaReason(p.reason).name if p.reason in OtaReason._value2member_map_ else str(p.reason)
            self._send_telemetry({"ota_stage": stage, "ota_reason": reason, "ota_progress": int(p.progress)})
            self._safe_system_event("OTA", f"{stage} {reason} {p.progress}% v{p.version_code}")
            if p.stage in (OtaStage.SUCCESS, OtaStage.UP_TO_DATE):
                self._send_attributes({"fw_version": str(p.version_code)})
                self.fw_version = str(p.version_code)
        elif t == MsgType.STATUS:
            st = StatusPayload.unpack(f.payload)
            self._update_fw_version(st)
            try:
                self._set_conveyor_state(State(st.state).name)
            except ValueError:
                pass
        else:
            log.debug("Bỏ qua sự kiện %s", f.type_name)

    def _update_fw_version(self, st: StatusPayload) -> None:
        v = str(st.fw_version_code)
        if v != self.fw_version:
            self.fw_version = v
            self._send_attributes({"fw_version": v})

    # ------------------------------------------------------------------ pipeline một kiện
    def process_package(self, pkg_seq: int) -> PackageRecord:
        with self._pipeline_lock:
            return self._process_package(pkg_seq)

    def _process_package(self, pkg_seq: int) -> PackageRecord:
        t0 = time.perf_counter()
        rec = PackageRecord(pkg_seq=pkg_seq, ts_ms=int(time.time() * 1000))
        metrics.inc(F_PACKAGES)
        qr: QrResult | None = None
        ai: ClassResult | None = None
        frame: np.ndarray | None = None
        decision: Decision
        try:
            self.camera.on_package_start()
            rec.image_name = self.camera.next_name()
            # 1. chờ băng ổn định rồi chụp (tối đa max_qr_frames khung, dừng sớm khi đọc được QR)
            if self.cfg.camera.settle_ms > 0:
                time.sleep(self.cfg.camera.settle_ms / 1000.0)
            tc = time.perf_counter()
            frames: list[np.ndarray] = []
            read = None
            tq_total = 0.0
            for _ in range(max(1, self.cfg.camera.max_qr_frames)):
                frame = self.camera.capture()
                frames.append(frame)
                tq = time.perf_counter()
                read = read_qr([frame])
                tq_total += time.perf_counter() - tq
                if read.status != NO_QR:
                    break
            rec.t_capture_ms = (time.perf_counter() - tc) * 1000.0 - tq_total * 1000.0
            rec.t_qr_ms = tq_total * 1000.0
            self._last_frame = frame
            assert read is not None
            # 2. xác thực QR (S-03)
            tv = time.perf_counter()
            qr = self.verifier.verify_read(read)
            rec.t_verify_ms = (time.perf_counter() - tv) * 1000.0
            rec.qr_status = qr.status
            rec.pkg_id, rec.warehouse = qr.pkg_id, qr.warehouse
            # 3. AI chỉ khi QR hợp lệ
            if qr.status == VALID and frame is not None:
                ta = time.perf_counter()
                ai = self.classifier.predict(frame, tuple(self.cfg.camera.roi))  # type: ignore[arg-type]
                rec.t_ai_ms = (time.perf_counter() - ta) * 1000.0
                rec.ai_label, rec.ai_conf, rec.ai_types = ai.label, float(ai.confidence), dict(ai.defect_types)
            # 4. quyết định (P-06)
            decision = decide(qr, ai)
        except Exception as e:
            log.exception("Pipeline lỗi (pkg_seq=%d): %s", pkg_seq, e)
            rec.error = f"{type(e).__name__}: {e}"
            self._safe_system_event("PIPELINE_ERROR", rec.error)
            decision = decide(None, None, failsafe=True)

        rec.decision, rec.target, rec.reason = decision.code, decision.target.name, decision.reason.name
        # 5. gửi SORT (chờ ACK, gửi lại) — latency đo tới lúc gửi xong
        res = None
        try:
            res = self.uart.send_command(MsgType.SORT, SortPayload(int(decision.target), int(decision.reason), pkg_seq & 0xFFFF))
            rec.sort_sent = res.ok
            rec.sort_error = res.error
        except Exception as e:
            rec.sort_error = f"{type(e).__name__}: {e}"
            log.exception("Gửi SORT lỗi: %s", e)
        rec.latency_ms = (time.perf_counter() - t0) * 1000.0
        if not rec.sort_sent:
            # ESP32 sẽ tự failsafe sau t_decision_timeout; ghi nhận là REJECT_FAILSAFE
            log.error("SORT pkg_seq=%d không được ACK (%s) — ESP32 tự failsafe", pkg_seq, rec.sort_error)
            self._safe_system_event("SORT_FAILED", f"pkg_seq={pkg_seq} {rec.sort_error} decision={decision.code}")
            if decision.code in ("SORT_A", "SORT_B"):
                rec.decision, rec.target, rec.reason = REJECT_FAILSAFE, SortTarget.REJECT.name, SortReason.FAILSAFE.name
        else:
            self._set_conveyor_state(State.SORTING.name)

        # 6. hậu xử lý (không ảnh hưởng tới an toàn — mọi lỗi chỉ log)
        try:
            self._post_process(rec, qr, ai, frame, decision)
        except Exception as e:
            log.exception("Hậu xử lý kiện lỗi: %s", e)
            self._safe_system_event("POST_PROCESS_ERROR", str(e))

        log.info(
            "PKG seq=%d id=%s qr=%s ai=%s decision=%s latency=%.0fms (cap %.0f qr %.0f ver %.0f ai %.0f) img=%s",
            pkg_seq, rec.pkg_id, rec.qr_status, rec.ai_label, rec.decision, rec.latency_ms,
            rec.t_capture_ms, rec.t_qr_ms, rec.t_verify_ms, rec.t_ai_ms, rec.image_name,
            extra={"package": rec.to_db()},
        )
        with self._pkg_cond:
            self.records.append(rec)
            if len(self.records) > 500:
                del self.records[:250]
            self.packages_done += 1
            self._pkg_cond.notify_all()
        return rec

    def _post_process(self, rec: PackageRecord, qr: QrResult | None, ai: ClassResult | None, frame: np.ndarray | None, decision: Decision) -> None:
        # Ảnh lỗi (chỉ REJECT_DEFECT)
        image_b64: str | None = None
        if rec.decision == REJECT_DEFECT and frame is not None:
            try:
                path, image_b64 = self.defect_store.save(frame, rec.pkg_id, rec.ts_ms)
                rec.defect_image_path = str(path)
            except Exception as e:
                log.error("Không lưu được ảnh lỗi: %s", e)
        self.storage.insert_package(rec.to_db())
        cnt = self.counters.record(rec.decision)
        data = rec.to_telemetry()
        data.update(cnt)
        if image_b64 is not None:
            data["defect_image"] = image_b64
            data["defect_label"] = data.get("ai_defect_type") or (rec.ai_label or "DEFECT")
        self._send_telemetry(data, ts_ms=rec.ts_ms)
        # Sự kiện bảo mật khi QR không hợp lệ (NO_QR chỉ log, không alarm)
        if rec.decision == REJECT_SECURITY and qr is not None:
            sec_type = QR_STATUS_TO_SEC.get(qr.status)
            if sec_type is not None:
                self.security_log.emit(sec_type, f"QR {qr.status} pkg_id={qr.pkg_id or '?'} seq={rec.pkg_seq}", pkg_id=qr.pkg_id, pkg_seq=rec.pkg_seq)
            else:
                log.info("Kiện seq=%d không có mã QR đọc được (NO_QR) -> REJECT_SECURITY", rec.pkg_seq)
        if rec.decision == REJECT_FAILSAFE:
            self._safe_system_event("REJECT_FAILSAFE", f"seq={rec.pkg_seq} {rec.error or rec.sort_error or decision.detail}")

    # ------------------------------------------------------------------ sức khỏe (P-12)
    def _health_loop(self) -> None:
        interval = max(1, int(self.cfg.thingsboard.health_interval_s))
        self._send_health()
        while not self._stop.wait(interval):
            self._send_health()

    def _send_health(self) -> None:
        try:
            h = read_health()
            state_name = self.uart.state.name if self.uart.state is not None else "UNKNOWN"
            data = {
                "cpu_temp": h["cpu_temp"],
                "cpu_load": h["cpu_load"],
                "mem_used_pct": h["mem_used_pct"],
                "disk_used_pct": h["disk_used_pct"],
                "uptime_s": h["uptime_s"],
                "conveyor_state": state_name,
                "esp_link": bool(self.uart.connected),
                "uart_crc_errors": self.uart.crc_errors,
            }
            self.conveyor_state = state_name
            self._send_telemetry(data)
        except Exception as e:
            log.error("Gửi health lỗi: %s", e)

    def health_snapshot(self) -> dict[str, Any]:
        """Dùng cho RPC getStatus."""
        return {
            "conveyor_state": self.uart.state.name if self.uart.state is not None else "UNKNOWN",
            "esp_link": bool(self.uart.connected),
            "fw_version": self.fw_version,
            "packages_done": self.packages_done,
            "counters": self.counters.values,
            **read_health(),
        }

    # ------------------------------------------------------------------ tiện ích
    def _set_conveyor_state(self, name: str) -> None:
        if name != self.conveyor_state:
            self.conveyor_state = name
            self._send_telemetry({"conveyor_state": name, "esp_link": bool(self.uart.connected)})

    def _send_telemetry(self, data: dict[str, Any], ts_ms: int | None = None) -> None:
        try:
            self.telemetry.send_telemetry(data, ts_ms)
        except Exception as e:
            log.error("Telemetry lỗi: %s", e)

    def _send_attributes(self, data: dict[str, Any]) -> None:
        try:
            self.telemetry.send_attributes(data)
        except Exception as e:
            log.error("Attributes lỗi: %s", e)

    def _safe_system_event(self, kind: str, detail: str) -> None:
        try:
            self.storage.insert_system_event(kind, detail)
        except Exception as e:
            log.error("Không ghi được system_event: %s", e)

    def wait_packages(self, n: int, timeout_s: float = 10.0) -> bool:
        """Chờ tới khi đã xử lý >= n kiện (dùng cho sim/test)."""
        deadline = time.monotonic() + timeout_s
        with self._pkg_cond:
            while self.packages_done < n:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._pkg_cond.wait(remaining)
        return True

    def run_once_sim(self, n: int, gap_s: float = 0.05, timeout_s: float = 15.0) -> list[PackageRecord]:
        """Chế độ sim: kích cảm biến giả n lần, chờ xử lý xong từng kiện. Trả các bản ghi."""
        if self.fake is None:
            raise RuntimeError("run_once_sim cần FakeEsp32 (uart.port=sim)")
        start_idx = self.packages_done
        out: list[PackageRecord] = []
        for i in range(n):
            deadline = time.monotonic() + timeout_s
            while self.fake.state != State.RUNNING:
                if time.monotonic() > deadline:
                    raise TimeoutError(f"FakeEsp32 không về RUNNING (đang {self.fake.state.name})")
                if self.fake.state in (State.ESTOP, State.FAULT, State.IDLE):
                    raise RuntimeError(f"FakeEsp32 đang {self.fake.state.name}; không thể chạy sim")
                time.sleep(0.005)
            if not self.fake.trigger_sensor():
                raise RuntimeError("trigger_sensor thất bại")
            if not self.wait_packages(start_idx + i + 1, timeout_s):
                raise TimeoutError(f"Kiện {i + 1} chưa được xử lý sau {timeout_s}s")
            out.append(self.records[-1])
            if gap_s > 0:
                time.sleep(gap_s)
        return out
