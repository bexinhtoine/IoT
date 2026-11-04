"""Điểm vào sorter-core: python -m sorter.main --config PATH [--sim] [--log-level INFO] [--packages N]

--sim: ép camera.source=folder, uart.port=sim, thingsboard.enabled=false (P-14); chạy N kiện từ ảnh
       trong thư mục rồi in bảng quyết định và thoát.
"""
from __future__ import annotations

import argparse
import json
import logging
import signal
import sys
import threading
from typing import Any

from .config import ConfigError, SorterConfig, load_config
from .counters import Counters
from .defect_images import DefectImageStore
from .factories import make_anomaly, make_camera, make_classifier, make_rpc_handler, make_telemetry, make_uart
from .logging_setup import setup_logging
from .orchestrator import Orchestrator
from .qr_security import QrVerifier, load_hmac_key
from .security_events import SecurityLog
from .storage import Storage

log = logging.getLogger("sorter.main")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(prog="sorter", description="Smart Logistics Sorter — sorter-core (Raspberry Pi)")
    ap.add_argument("--config", required=True, help="đường dẫn config.yaml")
    ap.add_argument("--sim", action="store_true", help="chế độ mô phỏng: FolderCamera + FakeEsp32 + LogTelemetrySink")
    ap.add_argument("--log-level", default="INFO", help="DEBUG | INFO | WARNING | ERROR")
    ap.add_argument("--packages", type=int, default=None, help="số kiện chạy ở --sim (mặc định = số ảnh trong thư mục)")
    ap.add_argument("--no-log-file", action="store_true", help="không ghi file log (chỉ console)")
    return ap.parse_args(argv)


def apply_sim(cfg: SorterConfig) -> None:
    if cfg.camera.source != "folder":
        cfg.camera.source = "folder"
    if cfg.uart.port != "sim":
        cfg.uart.port = "sim"
    if cfg.thingsboard.enabled:
        cfg.thingsboard.enabled = False
    if cfg.ai.backend != "dummy":
        log.info("--sim: giữ ai.backend=%s", cfg.ai.backend)


class App:
    """Khởi tạo và giữ mọi thành phần; stop() dừng sạch theo thứ tự ngược."""

    def __init__(self, cfg: SorterConfig) -> None:
        self.cfg = cfg
        self.storage = Storage(cfg.storage.target, outbox_path=cfg.storage.db_path + ".outbox.db")
        self.counters = Counters(self.storage)
        self.telemetry = make_telemetry(cfg)
        self.security_log = SecurityLog(self.storage, self.telemetry)
        key = load_hmac_key(cfg.qr.hmac_key_file)
        self.verifier = QrVerifier(key, self.storage, cfg.qr.max_age_days, cfg.qr.future_skew_s)
        del key
        self.camera = make_camera(cfg)
        self.classifier = make_classifier(cfg, label_hint=self.camera.next_name)
        self.uart, self.fake = make_uart(cfg)
        self.defect_store = DefectImageStore(cfg.storage.defect_dir, cfg.storage.max_defect_images, cfg.thingsboard.image_max_kb)
        self.orchestrator = Orchestrator(
            cfg, self.camera, self.classifier, self.verifier, self.uart, self.storage, self.counters,
            self.telemetry, self.security_log, fake=self.fake, defect_store=self.defect_store,
        )
        self.rpc_handler: Any = None
        self.anomaly: Any = None
        self._stopped = False

    def start(self) -> None:
        self.telemetry.start()
        self.telemetry.send_attributes({
            "sw_version": self.cfg.device.sw_version,
            "model_version": getattr(self.classifier, "model_version", "unknown"),
            "qr_format_version": self.cfg.device.qr_format_version,
        })
        self.rpc_handler = make_rpc_handler(
            self.cfg, self.uart, self.telemetry,
            reset_counters=self._rpc_reset_counters,
            reset_used_codes=self._rpc_reset_used_codes,
            get_status=self.orchestrator.health_snapshot,
            on_security_event=self.security_log.emit_event,
            audit=self._rpc_audit,
        )
        self.anomaly = make_anomaly(self.cfg, self.telemetry, self.security_log.emit_event)
        if self.anomaly is not None:
            self.anomaly.start()
        self.uart.start()
        self.storage.insert_system_event("STARTUP", f"sim={self.cfg.sim} config={self.cfg.source_path}")
        if self.cfg.sim and self.cfg.device.demo_mode and self.cfg.storage.backend == "sqlite":
            # Sim/demo: xóa mã đã dùng để chạy lại cùng bộ ảnh vẫn cho kết quả như nhau
            n = self.storage.clear_used_codes()
            if n:
                log.info("Sim: đã xóa %d mã QR đã dùng (used_codes)", n)
        self.orchestrator.start()

    def stop(self) -> None:
        if self._stopped:
            return
        self._stopped = True
        log.info("Đang dừng...")
        for name, fn in (
            ("orchestrator", self.orchestrator.stop),
            ("anomaly", lambda: self.anomaly.stop() if self.anomaly is not None else None),
            ("uart", self.uart.stop),
            ("fake", lambda: self.fake.stop() if self.fake is not None else None),
            ("camera", self.camera.close),
            ("classifier", self.classifier.close),
            ("telemetry", self.telemetry.stop),
        ):
            try:
                fn()
            except Exception as e:
                log.warning("Dừng %s lỗi: %s", name, e)
        try:
            self.storage.insert_system_event("SHUTDOWN", "")
            self.storage.close()
        except Exception as e:
            log.warning("Đóng storage lỗi: %s", e)

    # --- callback cho RPC (P5) ----------------------------------------
    def _rpc_reset_counters(self) -> dict[str, int]:
        cnt = self.counters.reset()
        self.telemetry.send_telemetry(cnt)
        return cnt

    def _rpc_reset_used_codes(self) -> int:
        return self.storage.clear_used_codes()

    def _rpc_audit(self, rec: dict[str, Any]) -> None:
        """Ghi rpc_audit (S-05). rec theo RpcCallbacks.audit của P5:
        {ts_ms, request_id, method, params_json, requested_by, result_json, ok} (json có thể là chuỗi hoặc dict)."""

        def _as_dict(v: Any) -> dict[str, Any] | None:
            if isinstance(v, dict):
                return v
            if isinstance(v, str):
                try:
                    out = json.loads(v)
                    return out if isinstance(out, dict) else {"value": out}
                except ValueError:
                    return {"raw": v}
            return None

        self.storage.insert_rpc_audit(
            rec.get("request_id"), str(rec.get("method", "?")), _as_dict(rec.get("params_json", rec.get("params"))),
            rec.get("requested_by"), _as_dict(rec.get("result_json", rec.get("result"))), bool(rec.get("ok")),
            ts_ms=rec.get("ts_ms"),
        )


def print_decision_table(records: list[Any]) -> str:
    rows = [f"{'#':>3} {'image':<24} {'pkg_id':<10} {'qr_status':<17} {'ai_label':<10} {'decision':<16} {'lat_ms':>7}"]
    for i, r in enumerate(records, 1):
        rows.append(
            f"{i:>3} {(r.image_name or '-')[:24]:<24} {(r.pkg_id or '-'):<10} {r.qr_status:<17} {(r.ai_label or '-'):<10} {r.decision:<16} {r.latency_ms:>7.0f}"
        )
    table = "\n".join(rows)
    log.info("Bảng quyết định:\n%s", table)
    return table


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    setup_logging(args.log_level, None)
    try:
        cfg = load_config(args.config)
    except ConfigError as e:
        logging.getLogger("sorter.config").error("%s", e)
        sys.stderr.write(f"Lỗi cấu hình: {e}\n")
        return 2
    if args.sim:
        apply_sim(cfg)
    setup_logging(args.log_level, None if args.no_log_file else cfg.storage.log_dir)
    log.info("sorter-core %s khởi động (config=%s, sim=%s)", cfg.device.sw_version, cfg.source_path, cfg.sim)

    try:
        app = App(cfg)
    except (ConfigError, FileNotFoundError, ValueError, RuntimeError, ConnectionError) as e:
        log.error("Khởi tạo thất bại: %s", e)
        sys.stderr.write(f"Lỗi khởi tạo: {e}\n")
        return 2

    stop_event = threading.Event()

    def _on_signal(signum: int, _frame: Any) -> None:
        log.info("Nhận tín hiệu %s — dừng", signum)
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, _on_signal)
        except (ValueError, OSError):  # không ở main thread
            pass

    rc = 0
    try:
        app.start()
        if args.sim and app.fake is not None:
            n = args.packages if args.packages is not None else len(getattr(app.camera, "files", []) or [1])
            records = app.orchestrator.run_once_sim(n)
            print_decision_table(records)
            log.info("Bộ đếm: %s", app.counters.to_telemetry())
            # ESP32 giả: chờ SORT_DONE cuối cùng rồi thoát
            stop_event.wait(0.3)
        else:
            while not stop_event.wait(0.5):
                pass
    except KeyboardInterrupt:
        pass
    except Exception as e:
        log.exception("Lỗi không mong đợi: %s", e)
        rc = 1
    finally:
        app.stop()
    return rc


if __name__ == "__main__":
    sys.exit(main())
