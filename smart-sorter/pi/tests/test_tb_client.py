"""Test P-10 / S-04 — ThingsBoardClient với paho được mock (không cần mạng, không cần cert thật).

Kiểm tra: không bao giờ tls_insecure_set(True); tls_set với 3 file; publish đúng topic/payload;
hàng đợi khi chưa kết nối và xả khi on_connect; RPC request -> handler -> response đúng topic.
Skip gọn nếu thiếu paho-mqtt.
"""
from __future__ import annotations

import json
import ssl
import time
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

mqtt = pytest.importorskip("paho.mqtt.client", reason="thiếu paho-mqtt")

from sorter.config import ThingsboardCfg  # noqa: E402
from sorter.interfaces import F_MQTT_RECONNECTS, MetricCounters  # noqa: E402
from sorter import tb_client  # noqa: E402
from sorter.tb_client import (  # noqa: E402
    TOPIC_ATTRIBUTES,
    TOPIC_RPC_REQUEST_SUB,
    TOPIC_RPC_RESPONSE_PREFIX,
    TOPIC_TELEMETRY,
    ThingsBoardClient,
    check_tls_files,
)


class FakeMsgInfo:
    rc = 0
    mid = 1


class FakeMqttClient(MagicMock):
    """MagicMock có publish trả rc=0 (có thể bật lỗi), ghi lại mọi lời gọi."""


@pytest.fixture
def fake_client(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    inst = MagicMock(name="paho.Client")
    inst.publish.return_value = FakeMsgInfo()
    inst._ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctor = MagicMock(return_value=inst)
    monkeypatch.setattr(tb_client.mqtt, "Client", ctor)
    inst._ctor = ctor
    return inst


@pytest.fixture
def cfg(tmp_path: Path) -> ThingsboardCfg:
    ca, cert, key = tmp_path / "ca.pem", tmp_path / "device.pem", tmp_path / "device.key"
    for p in (ca, cert, key):
        p.write_text("-----BEGIN CERTIFICATE-----\nfake\n-----END CERTIFICATE-----\n")
    return ThingsboardCfg(enabled=True, host="tb.local", port=8883, ca_cert=str(ca), client_cert=str(cert),
                          client_key=str(key), image_max_kb=40, queue_max=5)


def make(cfg: ThingsboardCfg, fake_client: MagicMock) -> tuple[ThingsBoardClient, MagicMock, MetricCounters]:
    metrics = MetricCounters()
    c = ThingsBoardClient(cfg, "sorter-pi-01", metrics=metrics)
    return c, fake_client, metrics


def published(fake: MagicMock, topic: str) -> list[dict[str, Any]]:
    out = []
    for call in fake.publish.call_args_list:
        args, kwargs = call
        t = args[0] if args else kwargs["topic"]
        if t == topic:
            raw = args[1] if len(args) > 1 else kwargs["payload"]
            out.append(json.loads(raw))
    return out


# ---------------------------------------------------------------------------
# TLS (S-01, S-04)
# ---------------------------------------------------------------------------
def test_tls_set_called_with_three_files_and_never_insecure(cfg: ThingsboardCfg, fake_client: MagicMock) -> None:
    c, fake, _ = make(cfg, fake_client)
    fake.tls_set.assert_called_once()
    kw = fake.tls_set.call_args.kwargs
    assert kw["ca_certs"] == cfg.ca_cert
    assert kw["certfile"] == cfg.client_cert
    assert kw["keyfile"] == cfg.client_key
    assert kw["cert_reqs"] == ssl.CERT_REQUIRED
    assert kw["tls_version"] == ssl.PROTOCOL_TLS_CLIENT
    for call in fake.tls_insecure_set.call_args_list:
        assert call.args != (True,) and call.kwargs.get("value") is not True
    assert fake.tls_set_context.call_count == 0 or fake.tls_set_context.call_args.args[0].check_hostname is True
    # Context đã được ép tối thiểu TLS 1.2 + kiểm tra hostname
    ctx = fake._ssl_context
    assert ctx.minimum_version == ssl.TLSVersion.TLSv1_2
    assert ctx.check_hostname is True and ctx.verify_mode == ssl.CERT_REQUIRED
    # client_id = tên thiết bị, VERSION2, không username
    assert fake._ctor.call_args.kwargs["client_id"] == "sorter-pi-01"
    assert fake._ctor.call_args.kwargs["callback_api_version"] == mqtt.CallbackAPIVersion.VERSION2
    assert fake.username_pw_set.call_count == 0


def test_source_has_no_insecure_option() -> None:
    src = Path(tb_client.__file__).read_text(encoding="utf-8")
    assert "tls_insecure_set(True" not in src
    assert "CERT_NONE" not in src
    assert "check_hostname = False" not in src


def test_check_tls_files_reports_missing(cfg: ThingsboardCfg, tmp_path: Path) -> None:
    assert check_tls_files(cfg) == []
    cfg.client_key = str(tmp_path / "missing.key")
    errs = check_tls_files(cfg)
    assert len(errs) == 1 and "client_key" in errs[0]


# ---------------------------------------------------------------------------
# start / backoff
# ---------------------------------------------------------------------------
def test_start_uses_connect_async_loop_start_and_backoff(cfg: ThingsboardCfg, fake_client: MagicMock) -> None:
    c, fake, _ = make(cfg, fake_client)
    c.start()
    fake.connect_async.assert_called_once()
    assert fake.connect_async.call_args.args[:2] == ("tb.local", 8883)
    fake.loop_start.assert_called_once()
    fake.reconnect_delay_set.assert_called_once_with(min_delay=1, max_delay=60)
    c.start()  # idempotent
    fake.loop_start.assert_called_once()
    c.stop()
    fake.disconnect.assert_called_once()
    fake.loop_stop.assert_called_once()


# ---------------------------------------------------------------------------
# Telemetry / attributes
# ---------------------------------------------------------------------------
def test_telemetry_published_with_ts_and_without(cfg: ThingsboardCfg, fake_client: MagicMock) -> None:
    c, fake, _ = make(cfg, fake_client)
    c._on_connect(fake, None, {}, 0, None)
    assert c.is_connected()
    fake.subscribe.assert_called_once_with(TOPIC_RPC_REQUEST_SUB, qos=1)

    c.send_telemetry({"cnt_total": 3, "conveyor_state": "RUNNING"}, ts_ms=1700000000000)
    c.send_telemetry({"cpu_temp": 51.5})
    msgs = published(fake, TOPIC_TELEMETRY)
    assert msgs == [{"ts": 1700000000000, "values": {"cnt_total": 3, "conveyor_state": "RUNNING"}},
                    {"cpu_temp": 51.5}]
    for call in fake.publish.call_args_list:
        assert call.kwargs.get("qos") == 1


def test_attributes_published_and_resent_on_reconnect(cfg: ThingsboardCfg, fake_client: MagicMock) -> None:
    c, fake, metrics = make(cfg, fake_client)
    c._on_connect(fake, None, {}, 0, None)
    c.send_attributes({"sw_version": "1.0.0"})
    c.send_attributes({"fw_version": "1.2.3"})
    assert published(fake, TOPIC_ATTRIBUTES) == [{"sw_version": "1.0.0"}, {"fw_version": "1.2.3"}]
    # mất kết nối rồi kết nối lại -> gửi lại bộ attributes gộp
    c._on_disconnect(fake, None, None, 7, None)
    assert not c.is_connected()
    c._on_connect(fake, None, {}, 0, None)
    assert published(fake, TOPIC_ATTRIBUTES)[-1] == {"sw_version": "1.0.0", "fw_version": "1.2.3"}
    assert c.reconnect_count() == 1
    assert metrics.snapshot()[F_MQTT_RECONNECTS] == 1


def test_oversized_payload_rejected(cfg: ThingsboardCfg, fake_client: MagicMock, caplog: pytest.LogCaptureFixture) -> None:
    c, fake, _ = make(cfg, fake_client)
    c._on_connect(fake, None, {}, 0, None)
    big = "A" * ((cfg.image_max_kb + 8) * 1024 + 1)
    with caplog.at_level("WARNING"):
        c.send_telemetry({"defect_image": big})
    assert published(fake, TOPIC_TELEMETRY) == []
    assert any("giới hạn" in r.message for r in caplog.records)
    ok = "A" * (cfg.image_max_kb * 1024)
    c.send_telemetry({"defect_image": ok})
    assert len(published(fake, TOPIC_TELEMETRY)) == 1


# ---------------------------------------------------------------------------
# Hàng đợi khi mất mạng
# ---------------------------------------------------------------------------
def test_queue_while_disconnected_then_flush_on_connect(cfg: ThingsboardCfg, fake_client: MagicMock) -> None:
    c, fake, _ = make(cfg, fake_client)
    assert not c.is_connected()
    for i in range(3):
        c.send_telemetry({"pkg_id": f"P{i}"}, ts_ms=1000 + i)
    c.send_attributes({"sw_version": "1.0.0"})
    assert fake.publish.call_count == 0
    assert c.queued_count() == 4

    c._on_connect(fake, None, {}, 0, None)
    assert c.queued_count() == 0
    tele = published(fake, TOPIC_TELEMETRY)
    assert [m["values"]["pkg_id"] for m in tele] == ["P0", "P1", "P2"]  # đúng thứ tự
    # attributes: 1 lần từ hàng đợi + 1 lần gửi lại bộ đã nhớ
    assert published(fake, TOPIC_ATTRIBUTES) == [{"sw_version": "1.0.0"}, {"sw_version": "1.0.0"}]


def test_queue_drops_oldest_when_full(cfg: ThingsboardCfg, fake_client: MagicMock) -> None:
    c, fake, _ = make(cfg, fake_client)  # queue_max = 5
    for i in range(8):
        c.send_telemetry({"i": i})
    assert c.queued_count() == 5
    c._on_connect(fake, None, {}, 0, None)
    assert [m["i"] for m in published(fake, TOPIC_TELEMETRY)] == [3, 4, 5, 6, 7]


def test_publish_failure_requeues(cfg: ThingsboardCfg, fake_client: MagicMock) -> None:
    c, fake, _ = make(cfg, fake_client)
    c._on_connect(fake, None, {}, 0, None)
    bad = FakeMsgInfo()
    bad.rc = 4  # MQTT_ERR_NO_CONN
    fake.publish.return_value = bad
    c.send_telemetry({"x": 1})
    assert c.queued_count() == 1
    fake.publish.return_value = FakeMsgInfo()
    c._on_disconnect(fake, None, None, 0, None)
    c._on_connect(fake, None, {}, 0, None)
    assert c.queued_count() == 0
    assert published(fake, TOPIC_TELEMETRY)[-1] == {"x": 1}


def test_on_connect_failure_keeps_disconnected(cfg: ThingsboardCfg, fake_client: MagicMock) -> None:
    c, fake, _ = make(cfg, fake_client)
    rc = MagicMock()
    rc.is_failure = True
    c._on_connect(fake, None, {}, rc, None)
    assert not c.is_connected()
    fake.subscribe.assert_not_called()


# ---------------------------------------------------------------------------
# RPC (T-04)
# ---------------------------------------------------------------------------
def _rpc_message(request_id: str, body: Any) -> MagicMock:
    m = MagicMock()
    m.topic = f"v1/devices/me/rpc/request/{request_id}"
    m.payload = body if isinstance(body, bytes) else json.dumps(body).encode()
    return m


def _wait_response(fake: MagicMock, request_id: str, timeout: float = 3.0) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    topic = TOPIC_RPC_RESPONSE_PREFIX + request_id
    while time.monotonic() < deadline:
        msgs = published(fake, topic)
        if msgs:
            return msgs[-1]
        time.sleep(0.01)
    raise AssertionError(f"không có response cho {request_id}")


def test_rpc_request_calls_handler_and_publishes_response(cfg: ThingsboardCfg, fake_client: MagicMock) -> None:
    c, fake, _ = make(cfg, fake_client)
    c._on_connect(fake, None, {}, 0, None)
    seen: list[tuple[str, dict[str, Any], str | None]] = []

    def handler(method: str, params: dict[str, Any], request_id: str | None = None) -> dict[str, Any]:
        seen.append((method, params, request_id))
        return {"ok": True, "esp_state": "IDLE"}

    c.set_rpc_handler(handler)
    c._on_message(fake, None, _rpc_message("42", {"method": "start", "params": {"speed": 60, "requestedBy": "a@b"}}))
    resp = _wait_response(fake, "42")
    assert resp == {"ok": True, "esp_state": "IDLE"}
    assert seen == [("start", {"speed": 60, "requestedBy": "a@b"}, "42")]
    # response publish với QoS 1, đúng topic
    call = [cl for cl in fake.publish.call_args_list if cl.args[0] == TOPIC_RPC_RESPONSE_PREFIX + "42"][-1]
    assert call.kwargs["qos"] == 1
    c.stop()


def test_rpc_handler_without_request_id_signature(cfg: ThingsboardCfg, fake_client: MagicMock) -> None:
    c, fake, _ = make(cfg, fake_client)
    c._on_connect(fake, None, {}, 0, None)
    c.set_rpc_handler(lambda m, p: {"ok": False, "error": "BAD_STATE", "m": m})
    c._on_message(fake, None, _rpc_message("7", {"method": "stop"}))
    assert _wait_response(fake, "7") == {"ok": False, "error": "BAD_STATE", "m": "stop"}
    c.stop()


def test_rpc_without_handler_returns_method_not_allowed(cfg: ThingsboardCfg, fake_client: MagicMock) -> None:
    c, fake, _ = make(cfg, fake_client)
    c._on_connect(fake, None, {}, 0, None)
    c._on_message(fake, None, _rpc_message("9", {"method": "start", "params": {"speed": 50}}))
    assert _wait_response(fake, "9") == {"ok": False, "error": "METHOD_NOT_ALLOWED"}
    c.stop()


def test_rpc_bad_json_or_params_shape(cfg: ThingsboardCfg, fake_client: MagicMock) -> None:
    c, fake, _ = make(cfg, fake_client)
    c._on_connect(fake, None, {}, 0, None)
    c.set_rpc_handler(lambda m, p: {"ok": True})
    c._on_message(fake, None, _rpc_message("b1", b"{not json"))
    c._on_message(fake, None, _rpc_message("b2", {"method": "start", "params": [1, 2]}))
    c._on_message(fake, None, _rpc_message("b3", ["list"]))
    for rid in ("b1", "b2", "b3"):
        assert _wait_response(fake, rid) == {"ok": False, "error": "BAD_PARAMS"}
    c.stop()


def test_rpc_handler_exception_contained(cfg: ThingsboardCfg, fake_client: MagicMock) -> None:
    c, fake, _ = make(cfg, fake_client)
    c._on_connect(fake, None, {}, 0, None)

    def boom(m: str, p: dict[str, Any]) -> dict[str, Any]:
        raise RuntimeError("x")

    c.set_rpc_handler(boom)
    c._on_message(fake, None, _rpc_message("e1", {"method": "stop"}))
    resp = _wait_response(fake, "e1")
    assert resp["ok"] is False and resp["error"] == "INTERNAL_ERROR"
    c.stop()


def test_non_rpc_topic_ignored(cfg: ThingsboardCfg, fake_client: MagicMock) -> None:
    c, fake, _ = make(cfg, fake_client)
    c._on_connect(fake, None, {}, 0, None)
    m = MagicMock()
    m.topic = "v1/devices/me/attributes"
    m.payload = b"{}"
    c._on_message(fake, None, m)
    assert published(fake, TOPIC_RPC_RESPONSE_PREFIX) == []
    assert fake.publish.call_count == 0
