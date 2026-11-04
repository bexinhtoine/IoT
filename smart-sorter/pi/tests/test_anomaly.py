"""P9 — Test cho module phát hiện bất thường (A-01 → A-06) với dữ liệu tổng hợp."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import numpy as np
import pytest

from sorter import interfaces
from sorter.anomaly.detector import AnomalyDetector
from sorter.anomaly.features import FeatureCollector
from sorter.anomaly.rules import RuleEngine
from sorter.anomaly.store import AnomalyStore
from sorter.anomaly.train import save_model, train_from_matrix
from sorter.config import AnomalyCfg
from sorter.interfaces import LogTelemetrySink, MetricCounters

FEATURE_NAMES = interfaces.FEATURE_NAMES
IDX = {name: i for i, name in enumerate(FEATURE_NAMES)}

# Lambda Poisson cho dữ liệu "bình thường" (nhỏ) theo thứ tự FEATURE_NAMES
_NORMAL_LAMBDA = {
    "rpc_total": 3.0,
    "rpc_rejected": 0.02,
    "qr_bad_signature": 0.02,
    "qr_duplicate": 0.02,
    "qr_bad_format": 0.05,
    "uart_crc_errors": 0.1,
    "mqtt_reconnects": 0.05,
    "ssh_failed": 0.05,
    "estop_count": 0.01,
    "packages": 4.0,
}


def _normal_matrix(n: int, seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    cols = [rng.poisson(_NORMAL_LAMBDA[name], size=n) for name in FEATURE_NAMES]
    return np.stack(cols, axis=1).astype(float)


def _attack_features() -> dict[str, int]:
    """Cửa sổ tấn công: nhiều RPC bị từ chối và QR chữ ký sai."""
    d = {name: 0 for name in FEATURE_NAMES}
    d["rpc_total"] = 10
    d["rpc_rejected"] = 5
    d["qr_bad_signature"] = 8
    d["packages"] = 2
    return d


# ---------------------------------------------------------------------------
# A-02 — Huấn luyện và ngưỡng
# ---------------------------------------------------------------------------
def test_train_from_matrix_detects_attack_and_low_false_positive():
    X = _normal_matrix(300, seed=1)
    pipeline, threshold = train_from_matrix(X)

    # Cửa sổ tấn công phải vượt ngưỡng
    x_attack = FeatureCollector.to_vector(_attack_features())
    score_attack = float(-pipeline.score_samples(x_attack.reshape(1, -1))[0])
    assert score_attack > threshold

    # Dữ liệu bình thường mới: >= 98% dưới ngưỡng
    X_new = _normal_matrix(200, seed=99)
    scores = -pipeline.score_samples(X_new)
    below = float(np.mean(scores < threshold))
    assert below >= 0.98


# ---------------------------------------------------------------------------
# A-04 — Luật cứng
# ---------------------------------------------------------------------------
def test_rule_qr_forgery_fires_once_in_cooldown():
    events: list = []
    engine = RuleEngine(on_security_event=events.append)

    # 5 QR chữ ký sai ở giây 1000 -> kích hoạt đúng một lần
    engine.update({"qr_bad_signature": 5}, now_s=1000.0)
    # Thêm trong cùng cửa sổ cooldown 60 s -> không báo dồn
    engine.update({"qr_bad_signature": 5}, now_s=1001.0)
    engine.update({"qr_bad_signature": 5}, now_s=1030.0)

    qr_events = [
        e for e in events
        if e.event_type == interfaces.SEC_RULE and e.extra.get("rule") == "RULE_QR_FORGERY"
    ]
    assert len(qr_events) == 1
    assert qr_events[0].level.value == "CRITICAL"


def test_rule_rpc_unknown_fires_immediately():
    events: list = []
    engine = RuleEngine(on_security_event=events.append)
    engine.update({"rpc_rejected": 1}, now_s=500.0)
    rpc_events = [e for e in events if e.extra.get("rule") == "RULE_RPC_UNKNOWN"]
    assert len(rpc_events) == 1


# ---------------------------------------------------------------------------
# store.py — tạo bảng, lưu/đọc
# ---------------------------------------------------------------------------
def test_store_create_insert_fetch(tmp_path):
    db = str(tmp_path / "sorter.db")
    store = AnomalyStore(db)
    try:
        assert store.count() == 0
        feats = {name: i for i, name in enumerate(FEATURE_NAMES)}
        wid = store.insert_window(1000, feats, score=0.5, flag=True, top_features="rpc_total=3.0")
        assert wid > 0
        store.insert_window(2000, {name: 0 for name in FEATURE_NAMES})
        assert store.count() == 2

        rows = store.fetch(0, 1500)
        assert len(rows) == 1
        r = rows[0]
        assert r["ts_ms"] == 1000
        assert r["flag"] == 1
        assert r["rpc_rejected"] == IDX["rpc_rejected"]

        store.update_score(wid, 1.23, False, "packages=9.9")
        rows = store.fetch(0, 1500)
        assert abs(rows[0]["score"] - 1.23) < 1e-9
        assert rows[0]["flag"] == 0
    finally:
        store.close()


# ---------------------------------------------------------------------------
# features.py (A-01) — ssh_reader giả
# ---------------------------------------------------------------------------
def test_feature_collector_with_fake_ssh_reader():
    m = MetricCounters()
    m.inc(interfaces.F_RPC_TOTAL, 4)
    m.inc(interfaces.F_QR_BAD_SIGNATURE, 2)
    collector = FeatureCollector(metrics=m, ssh_reader=lambda since_s: 7, window_s=10)

    d = collector.collect(now_ms=123456)
    assert d["rpc_total"] == 4
    assert d["qr_bad_signature"] == 2
    assert d["ssh_failed"] == 7  # từ ssh_reader giả

    # snapshot_and_reset đã reset bộ đếm
    d2 = collector.collect(now_ms=223456)
    assert d2["rpc_total"] == 0
    assert d2["ssh_failed"] == 7

    vec = FeatureCollector.to_vector(d)
    assert vec.shape == (len(FEATURE_NAMES),)
    assert vec[IDX["ssh_failed"]] == 7.0


# ---------------------------------------------------------------------------
# detector.py (A-03) — score_once + telemetry
# ---------------------------------------------------------------------------
@pytest.fixture
def trained_model_path(tmp_path):
    X = _normal_matrix(300, seed=7)
    pipeline, threshold = train_from_matrix(X)
    out = tmp_path / "anomaly_if.joblib"
    save_model(str(out), pipeline, threshold, X)
    return str(out)


def _make_detector(tmp_path, model_path, metrics, events):
    cfg = AnomalyCfg(enabled=True, window_s=10, model_path=model_path, cooldown_s=60)
    telemetry = LogTelemetrySink()
    det = AnomalyDetector(
        cfg=cfg,
        db_path=str(tmp_path / "sorter.db"),
        telemetry=telemetry,
        on_security_event=events.append,
        metrics=metrics,
        ssh_reader=lambda since_s: 0,
    )
    return det, telemetry


def test_detector_score_once_flags_attack(tmp_path, trained_model_path):
    events: list = []
    det, _ = _make_detector(tmp_path, trained_model_path, MetricCounters(), events)
    try:
        assert det.has_model

        score, flag, top = det.score_once(_attack_features())
        assert flag is True
        assert score > det._threshold
        # top_features dạng "feat=z;feat=z;feat=z"
        assert top.count(";") == 2
        assert "qr_bad_signature=" in top or "rpc_rejected=" in top

        # Cửa sổ bình thường không bị gắn cờ
        normal = {name: 0 for name in FEATURE_NAMES}
        normal["rpc_total"] = 3
        normal["packages"] = 4
        _, flag_n, _ = det.score_once(normal)
        assert flag_n is False
    finally:
        det.stop()


def test_detector_process_window_sends_telemetry_and_event(tmp_path, trained_model_path):
    events: list = []
    m = MetricCounters()
    for k, v in _attack_features().items():
        if v:
            m.inc(k, v)
    det, telemetry = _make_detector(tmp_path, trained_model_path, m, events)
    try:
        det._process_window(now_ms=1_700_000_000_000)

        assert telemetry.sent, "phải gửi telemetry"
        last = telemetry.sent[-1]
        assert "anomaly_score" in last and isinstance(last["anomaly_score"], float)
        assert "anomaly_flag" in last and isinstance(last["anomaly_flag"], bool)
        assert "anomaly_top_features" in last and isinstance(last["anomaly_top_features"], str)
        assert last["anomaly_flag"] is True

        anomaly_events = [e for e in events if e.event_type == interfaces.SEC_ANOMALY]
        assert len(anomaly_events) == 1
        assert anomaly_events[0].level.value == "CRITICAL"

        # Cửa sổ đã được lưu vào store
        assert det.store.count() == 1
    finally:
        det.stop()


def test_detector_runs_without_model(tmp_path):
    """Chưa có model: vẫn ghi cửa sổ + chạy luật, không sập."""
    events: list = []
    cfg = AnomalyCfg(enabled=True, window_s=10, model_path=str(tmp_path / "khong_co.joblib"), cooldown_s=60)
    m = MetricCounters()
    m.inc(interfaces.F_QR_BAD_SIGNATURE, 6)  # kích hoạt luật cứng
    det = AnomalyDetector(
        cfg=cfg,
        db_path=str(tmp_path / "sorter.db"),
        telemetry=LogTelemetrySink(),
        on_security_event=events.append,
        metrics=m,
        ssh_reader=lambda since_s: 0,
    )
    try:
        assert not det.has_model
        det._process_window(now_ms=1_700_000_000_000)
        assert det.store.count() == 1
        # Luật cứng vẫn báo dù chưa có model
        rule_events = [e for e in events if e.event_type == interfaces.SEC_RULE]
        assert len(rule_events) >= 1
    finally:
        det.stop()


# ---------------------------------------------------------------------------
# attack_sim.py (A-05) — thiếu cờ -> exit 2
# ---------------------------------------------------------------------------
def _load_attack_sim():
    tool_path = Path(__file__).resolve().parents[1] / "tools" / "attack_sim.py"
    spec = importlib.util.spec_from_file_location("attack_sim", tool_path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["attack_sim"] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def test_attack_sim_requires_ownership_flag(capsys):
    attack_sim = _load_attack_sim()
    rc = attack_sim.main([])  # thiếu --i-own-this-system
    assert rc == 2
    err = capsys.readouterr().err
    assert "i-own-this-system" in err


def test_attack_sim_uart_sim_writes_log(tmp_path):
    attack_sim = _load_attack_sim()
    log_file = tmp_path / "attack_log.jsonl"
    rc = attack_sim.main(
        ["--i-own-this-system", "--scenario", "uart", "--log", str(log_file)]
    )
    assert rc == 0
    assert log_file.exists()
    content = log_file.read_text(encoding="utf-8").strip()
    assert '"scenario": "uart"' in content
