"""P-10 / T-02, T-03, T-04 / S-01, S-04 — Kết nối ThingsBoard qua MQTT TLS 8883 với chứng chỉ X.509.

- paho-mqtt 2.x (CallbackAPIVersion.VERSION2), client_id = tên thiết bị.
- Mutual TLS: CA nội bộ xác minh server (check_hostname = True), cert/key thiết bị để
  ThingsBoard nhận diện theo CN (không cần username / access token).
  TLS tối thiểu 1.2. KHÔNG có bất kỳ tùy chọn nào bỏ qua xác minh (S-04).
- Tự kết nối lại trong thread mạng của paho (loop_start) với backoff 1, 2, 4 … ≤ 60 s
  (reconnect_delay_set); đếm số lần kết nối lại cho A-01 (metrics mqtt_reconnects).
- Hàng đợi khi mất mạng: deque(maxlen = cfg.queue_max), bỏ bản tin cũ nhất; xả khi kết nối lại.
- Topic đúng 4.3: v1/devices/me/telemetry, v1/devices/me/attributes,
  v1/devices/me/rpc/request/+  ->  v1/devices/me/rpc/response/<id>.
- RPC handler chạy trong ThreadPoolExecutor 1 worker để không chặn vòng mạng.
"""
from __future__ import annotations

import json
import logging
import os
import ssl
import stat
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from inspect import Parameter, signature
from pathlib import Path
from typing import Any, Callable

from sorter import interfaces
from sorter.config import ThingsboardCfg
from sorter.interfaces import F_MQTT_RECONNECTS, MetricCounters, TelemetrySink

try:  # paho là phụ thuộc chạy thật; test có thể chạy không cần nó
    import paho.mqtt.client as mqtt
except ImportError:  # pragma: no cover
    mqtt = None  # type: ignore[assignment]

log = logging.getLogger(__name__)

TOPIC_TELEMETRY = "v1/devices/me/telemetry"
TOPIC_ATTRIBUTES = "v1/devices/me/attributes"
TOPIC_RPC_REQUEST_SUB = "v1/devices/me/rpc/request/+"
TOPIC_RPC_REQUEST_PREFIX = "v1/devices/me/rpc/request/"
TOPIC_RPC_RESPONSE_PREFIX = "v1/devices/me/rpc/response/"

QOS = 1
KEEPALIVE_S = 30
RECONNECT_MIN_S = 1
RECONNECT_MAX_S = 60
# Dự phòng thêm cho JSON bao quanh ảnh (khóa, ts, nhãn...)
PAYLOAD_OVERHEAD_KB = 8

RpcHandlerFn = Callable[..., dict[str, Any]]


@dataclass(frozen=True)
class _Queued:
    topic: str
    payload: bytes


# ---------------------------------------------------------------------------
# Kiểm tra file chứng chỉ (S-01)
# ---------------------------------------------------------------------------
def check_tls_files(cfg: ThingsboardCfg) -> list[str]:
    """Trả danh sách lỗi (rỗng = ổn). Cảnh báo (log) nếu khóa thiết bị có quyền lỏng hơn 600 trên POSIX."""
    errs: list[str] = []
    for name in ("ca_cert", "client_cert", "client_key"):
        p = Path(getattr(cfg, name))
        if not p.is_file():
            errs.append(f"thingsboard.{name} không tồn tại: {p}")
    key = Path(cfg.client_key)
    if key.is_file() and os.name == "posix":
        mode = stat.S_IMODE(key.stat().st_mode)
        if mode & 0o077:
            log.warning("Khóa thiết bị %s có quyền %s; nên chmod 600", key, oct(mode))
    return errs


def _harden_context(ctx: ssl.SSLContext | None) -> None:
    """Phòng vệ nhiều lớp: dù paho đã tạo context PROTOCOL_TLS_CLIENT, ép lại tối thiểu TLS 1.2 + xác minh."""
    if ctx is None:
        return
    try:
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        ctx.verify_mode = ssl.CERT_REQUIRED
        ctx.check_hostname = True
    except (AttributeError, ValueError) as e:  # pragma: no cover - context lạ (mock)
        log.debug("không thể ép cấu hình TLS: %s", e)


# ---------------------------------------------------------------------------
# ThingsBoardClient
# ---------------------------------------------------------------------------
class ThingsBoardClient(TelemetrySink):
    """TelemetrySink thật: MQTT TLS 8883 + X.509 tới ThingsBoard (P-10)."""

    def __init__(self, cfg: ThingsboardCfg, device_name: str, metrics: MetricCounters = interfaces.metrics) -> None:
        if mqtt is None:  # pragma: no cover
            raise RuntimeError("Thiếu thư viện paho-mqtt (pip install paho-mqtt>=2.0)")
        self._cfg = cfg
        self._device_name = device_name
        self._metrics = metrics
        self._max_payload = (int(cfg.image_max_kb) + PAYLOAD_OVERHEAD_KB) * 1024

        self._lock = threading.Lock()
        self._queue: deque[_Queued] = deque(maxlen=max(1, int(cfg.queue_max)))
        self._attributes: dict[str, Any] = {}
        self._connected = False
        self._ever_connected = False
        self._reconnects = 0
        self._dropped = 0
        self._started = False
        self._stopping = False

        self._rpc_handler: RpcHandlerFn | None = None
        self._rpc_handler_takes_id = False
        self._rpc_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="tb-rpc")

        self._client = self._build_client()

    # -- Khởi tạo paho ------------------------------------------------------
    def _build_client(self) -> Any:
        client = mqtt.Client(
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
            client_id=self._device_name,
            clean_session=True,
            protocol=mqtt.MQTTv311,
            reconnect_on_failure=True,
        )
        # S-01/S-04: mutual TLS, xác minh server bằng CA nội bộ, TLS >= 1.2, kiểm tra hostname.
        # PROTOCOL_TLS_CLIENT = bắt buộc cert server + kiểm tra hostname; KHÔNG có đường nào tắt xác minh (tls_insecure).
        client.tls_set(
            ca_certs=self._cfg.ca_cert,
            certfile=self._cfg.client_cert,
            keyfile=self._cfg.client_key,
            cert_reqs=ssl.CERT_REQUIRED,
            tls_version=ssl.PROTOCOL_TLS_CLIENT,
        )
        _harden_context(getattr(client, "_ssl_context", None))
        # Không đặt username: ThingsBoard nhận diện thiết bị theo CN của cert (T-02).
        client.reconnect_delay_set(min_delay=RECONNECT_MIN_S, max_delay=RECONNECT_MAX_S)
        client.on_connect = self._on_connect
        client.on_disconnect = self._on_disconnect
        client.on_message = self._on_message
        return client

    # -- TelemetrySink ---------------------------------------------------------
    def start(self) -> None:
        """Kết nối không chặn; paho tự thử lại (1, 2, 4 … 60 s) trong thread riêng."""
        if self._started:
            return
        for e in check_tls_files(self._cfg):
            log.error(e)
        self._started = True
        self._stopping = False
        try:
            self._client.connect_async(self._cfg.host, int(self._cfg.port), keepalive=KEEPALIVE_S)
        except (ValueError, OSError) as e:
            log.error("connect_async %s:%s lỗi: %s", self._cfg.host, self._cfg.port, e)
        self._client.loop_start()
        log.info("ThingsBoard: đang kết nối mqtts://%s:%s client_id=%s", self._cfg.host, self._cfg.port, self._device_name)

    def stop(self) -> None:
        self._stopping = True
        try:
            self._client.disconnect()
        except Exception:  # pragma: no cover
            log.debug("disconnect lỗi", exc_info=True)
        try:
            self._client.loop_stop()
        except Exception:  # pragma: no cover
            log.debug("loop_stop lỗi", exc_info=True)
        self._rpc_pool.shutdown(wait=False)
        self._started = False
        with self._lock:
            self._connected = False

    def is_connected(self) -> bool:
        with self._lock:
            return self._connected

    def reconnect_count(self) -> int:
        with self._lock:
            return self._reconnects

    def queued_count(self) -> int:
        with self._lock:
            return len(self._queue)

    def send_telemetry(self, data: dict[str, Any], ts_ms: int | None = None) -> None:
        """Payload {"ts":..,"values":{...}} khi có ts_ms, ngược lại gửi dict phẳng (ThingsBoard lấy giờ server)."""
        body: Any = {"ts": int(ts_ms), "values": data} if ts_ms is not None else data
        self._publish(TOPIC_TELEMETRY, body)

    def send_attributes(self, data: dict[str, Any]) -> None:
        """Gửi client attributes và nhớ lại để gửi lại khi kết nối lại (fw_version, sw_version, ...)."""
        with self._lock:
            self._attributes.update(data)
        self._publish(TOPIC_ATTRIBUTES, data)

    def set_rpc_handler(self, handler: Callable[[str, dict[str, Any]], dict[str, Any]]) -> None:
        self._rpc_handler = handler
        self._rpc_handler_takes_id = _accepts_request_id(handler)

    # -- Gửi ---------------------------------------------------------------------
    def _encode(self, body: Any) -> bytes | None:
        try:
            raw = json.dumps(body, ensure_ascii=False, separators=(",", ":"), default=str).encode("utf-8")
        except (TypeError, ValueError) as e:
            log.error("Không mã hóa được JSON: %s", e)
            return None
        if len(raw) > self._max_payload:
            keys = list(body.get("values", body).keys()) if isinstance(body, dict) else "?"
            log.warning("Bỏ bản tin %d byte > giới hạn %d byte (khóa: %s)", len(raw), self._max_payload, keys)
            return None
        return raw

    def _publish(self, topic: str, body: Any) -> None:
        raw = self._encode(body)
        if raw is None:
            return
        with self._lock:
            connected = self._connected
        if not connected:
            self._enqueue(_Queued(topic, raw))
            return
        if not self._publish_raw(topic, raw):
            self._enqueue(_Queued(topic, raw))

    def _publish_raw(self, topic: str, raw: bytes) -> bool:
        try:
            info = self._client.publish(topic, raw, qos=QOS)
        except Exception as e:  # ValueError khi payload quá lớn, OSError khi socket hỏng
            log.warning("publish %s lỗi: %s", topic, e)
            return False
        rc = getattr(info, "rc", 0)
        if rc not in (0, None):
            log.debug("publish %s rc=%s, đưa vào hàng đợi", topic, rc)
            return False
        return True

    def _enqueue(self, item: _Queued) -> None:
        with self._lock:
            if len(self._queue) == self._queue.maxlen:
                self._dropped += 1
                if self._dropped in (1, 10, 100, 1000) or self._dropped % 1000 == 0:
                    log.warning("Hàng đợi đầy (%d), đã bỏ %d bản tin cũ nhất", self._queue.maxlen, self._dropped)
            self._queue.append(item)  # deque(maxlen) tự bỏ phần tử cũ nhất

    def _flush_queue(self) -> None:
        """Xả hàng đợi theo thứ tự cũ -> mới; dừng khi gửi thất bại (giữ lại phần còn lại)."""
        while True:
            with self._lock:
                if not self._queue or not self._connected:
                    return
                item = self._queue.popleft()
            if not self._publish_raw(item.topic, item.payload):
                with self._lock:
                    self._queue.appendleft(item)
                return

    # -- Callback paho (VERSION2) ------------------------------------------------
    def _on_connect(self, client: Any, userdata: Any, flags: Any, reason_code: Any, properties: Any = None) -> None:
        if _is_failure(reason_code):
            log.error("ThingsBoard từ chối kết nối: %s", reason_code)
            return
        with self._lock:
            self._connected = True
            if self._ever_connected:
                self._reconnects += 1
                reconnects = self._reconnects
            else:
                reconnects = 0
            self._ever_connected = True
            attrs = dict(self._attributes)
            queued = len(self._queue)
        if reconnects:
            self._metrics.inc(F_MQTT_RECONNECTS)
            log.warning("ThingsBoard: kết nối lại lần %d, hàng đợi %d bản tin", reconnects, queued)
        else:
            log.info("ThingsBoard: đã kết nối, hàng đợi %d bản tin", queued)
        try:
            client.subscribe(TOPIC_RPC_REQUEST_SUB, qos=QOS)
        except Exception as e:  # pragma: no cover
            log.error("subscribe RPC lỗi: %s", e)
        self._flush_queue()
        if attrs:  # gửi lại bộ attributes đã nhớ (T-03)
            raw = self._encode(attrs)
            if raw is not None and not self._publish_raw(TOPIC_ATTRIBUTES, raw):
                self._enqueue(_Queued(TOPIC_ATTRIBUTES, raw))

    def _on_disconnect(self, client: Any, userdata: Any, disconnect_flags: Any = None,
                       reason_code: Any = None, properties: Any = None) -> None:
        with self._lock:
            self._connected = False
        if self._stopping:
            log.info("ThingsBoard: đã ngắt kết nối")
        else:
            # paho tự kết nối lại với backoff 1, 2, 4 … 60 s (reconnect_delay_set); lần kết nối lại
            # thành công được đếm trong _on_connect.
            log.warning("ThingsBoard: mất kết nối (%s); sẽ tự kết nối lại với backoff %d..%d s",
                        reason_code, RECONNECT_MIN_S, RECONNECT_MAX_S)

    def _on_message(self, client: Any, userdata: Any, msg: Any) -> None:
        topic = str(getattr(msg, "topic", ""))
        if not topic.startswith(TOPIC_RPC_REQUEST_PREFIX):
            log.debug("Bỏ qua topic lạ %s", topic)
            return
        request_id = topic[len(TOPIC_RPC_REQUEST_PREFIX):]
        payload = getattr(msg, "payload", b"") or b""
        try:
            req = json.loads(payload.decode("utf-8") if isinstance(payload, (bytes, bytearray)) else payload)
        except (ValueError, UnicodeDecodeError) as e:
            log.warning("RPC %s: JSON lỗi (%s)", request_id, e)
            self._respond(request_id, {"ok": False, "error": "BAD_PARAMS"})
            return
        if not isinstance(req, dict):
            self._respond(request_id, {"ok": False, "error": "BAD_PARAMS"})
            return
        method = str(req.get("method", ""))
        params = req.get("params")
        if params is None:
            params = {}
        elif not isinstance(params, dict):
            self._respond(request_id, {"ok": False, "error": "BAD_PARAMS"})
            return
        # Chạy handler ngoài thread mạng để không chặn PING/keepalive (T-04)
        try:
            self._rpc_pool.submit(self._run_rpc, request_id, method, params)
        except RuntimeError:  # pool đã shutdown
            log.warning("RPC %s bị bỏ qua: client đang dừng", request_id)

    def _run_rpc(self, request_id: str, method: str, params: dict[str, Any]) -> None:
        handler = self._rpc_handler
        if handler is None:
            result: dict[str, Any] = {"ok": False, "error": "METHOD_NOT_ALLOWED"}
        else:
            try:
                if self._rpc_handler_takes_id:
                    result = handler(method, params, request_id=request_id)
                else:
                    result = handler(method, params)
            except Exception as e:  # fail-safe
                log.exception("RPC %s (%s) ném ngoại lệ", method, request_id)
                result = {"ok": False, "error": "INTERNAL_ERROR", "detail": type(e).__name__}
        if not isinstance(result, dict):
            result = {"ok": False, "error": "INTERNAL_ERROR"}
        self._respond(request_id, result)

    def _respond(self, request_id: str, result: dict[str, Any]) -> None:
        raw = self._encode(result)
        if raw is None:
            raw = b'{"ok":false,"error":"INTERNAL_ERROR"}'
        # Response RPC không xếp hàng: ThingsBoard đã timeout nếu ta mất kết nối
        self._publish_raw(TOPIC_RPC_RESPONSE_PREFIX + request_id, raw)


# ---------------------------------------------------------------------------
# Hỗ trợ
# ---------------------------------------------------------------------------
def _is_failure(reason_code: Any) -> bool:
    """paho VERSION2 trả ReasonCode (có .is_failure); test có thể truyền int."""
    fail = getattr(reason_code, "is_failure", None)
    if isinstance(fail, bool):
        return fail
    try:
        return int(reason_code) != 0
    except (TypeError, ValueError):
        return False


def _accepts_request_id(handler: Callable[..., Any]) -> bool:
    """Handler có nhận tham số từ khóa request_id không (RpcHandler.handle có, lambda (m, p) thì không)."""
    try:
        params = signature(handler).parameters
    except (TypeError, ValueError):
        return False
    if "request_id" in params:
        return True
    return any(p.kind == Parameter.VAR_KEYWORD for p in params.values())


def utc_ms() -> int:
    return int(time.time() * 1000)
