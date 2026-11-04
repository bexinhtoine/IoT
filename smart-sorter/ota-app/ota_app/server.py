"""O-05 — Máy chủ HTTPS phát hành OTA (aiohttp), cổng 8443, chạy trong thread riêng.

- TLS: ``ssl.SSLContext(PROTOCOL_TLS_SERVER)``, tối thiểu TLS 1.2, cert/key của máy chủ OTA
  do CA nội bộ ký (thingsboard/pki/make_ota_server_cert.sh của P5). ESP32 xác minh bằng CA nhúng.
- Đúng 4 route: GET /ota/manifest.json, GET /ota/manifest.sig, GET /ota/firmware.bin,
  POST /ota/report. Mọi đường dẫn/method khác -> 404. Không liệt kê thư mục.
- POST /ota/report: JSON <= 2 KB với device(str<=64), version_code(int), result ∈
  SUCCESS|REJECTED|ROLLED_BACK, reason(str<=200) -> ghi bảng device_reports (O-06).
- Mọi request được log (IP, method, route, thời gian, mã) vào logging + bảng request_log và
  gửi qua callback ``on_request`` (được gọi TỪ THREAD SERVER — UI phải chuyển sang thread Qt).
"""
from __future__ import annotations

import asyncio
import json
import logging
import socket
import ssl
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

from aiohttp import web

from .history import REPORT_RESULTS, HistoryStore
from .manifest import FIRMWARE_NAME, MANIFEST_NAME, SIG_NAME

log = logging.getLogger(__name__)

DEFAULT_PORT = 8443
MAX_REPORT_BYTES = 2048
MAX_DEVICE_LEN = 64
MAX_REASON_LEN = 200

RequestCallback = Callable[["RequestLogEntry"], None]


@dataclass
class RequestLogEntry:
    ts: float
    remote_ip: str
    method: str
    route: str
    status: int
    duration_ms: float

    def __str__(self) -> str:
        t = time.strftime("%H:%M:%S", time.localtime(self.ts))
        return f"{t} {self.remote_ip} {self.method} {self.route} -> {self.status} ({self.duration_ms:.1f} ms)"


class ServerError(Exception):
    pass


def make_ssl_context(cert: Path | str, key: Path | str) -> ssl.SSLContext:
    """SSLContext phía server: TLS >= 1.2, cert chain + private key của máy chủ OTA."""
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.load_cert_chain(certfile=str(cert), keyfile=str(key))
    return ctx


def validate_report(obj: Any) -> dict[str, Any]:
    """Kiểm tra JSON báo cáo thiết bị; sai -> ValueError với thông điệp ngắn."""
    if not isinstance(obj, dict):
        raise ValueError("body phải là JSON object")
    device = obj.get("device")
    if not isinstance(device, str) or not device or len(device) > MAX_DEVICE_LEN:
        raise ValueError("device phải là chuỗi 1-64 ký tự")
    vc = obj.get("version_code")
    if not isinstance(vc, int) or isinstance(vc, bool) or vc < 0:
        raise ValueError("version_code phải là số nguyên không âm")
    result = obj.get("result")
    if result not in REPORT_RESULTS:
        raise ValueError("result phải thuộc SUCCESS|REJECTED|ROLLED_BACK")
    reason = obj.get("reason", "")
    if not isinstance(reason, str) or len(reason) > MAX_REASON_LEN:
        raise ValueError("reason phải là chuỗi <= 200 ký tự")
    return {"device": device, "version_code": vc, "result": result, "reason": reason}


def local_urls(port: int = DEFAULT_PORT, path: str = "/ota/") -> list[str]:
    """Danh sách URL https theo các IP của máy (để hiển thị trong tab Máy chủ)."""
    ips: list[str] = []
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("10.255.255.255", 1))  # UDP connect không gửi gói, chỉ chọn interface
            ips.append(s.getsockname()[0])
        finally:
            s.close()
    except OSError:
        pass
    try:
        for ip in socket.gethostbyname_ex(socket.gethostname())[2]:
            if ip not in ips:
                ips.append(ip)
    except socket.gaierror:
        pass
    if "127.0.0.1" not in ips:
        ips.append("127.0.0.1")
    return [f"https://{ip}:{port}{path}" for ip in ips]


class OtaServer:
    """Server HTTPS phục vụ thư mục phát hành hiện tại; start()/stop() an toàn từ thread UI."""

    def __init__(self, history: Optional[HistoryStore] = None, on_request: Optional[RequestCallback] = None) -> None:
        self.history = history
        self.on_request = on_request
        self.release_dir: Optional[Path] = None
        self.host = "0.0.0.0"
        self.port = DEFAULT_PORT
        self._thread: Optional[threading.Thread] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._stop_event: Optional[asyncio.Event] = None
        self._ready = threading.Event()
        self._start_error: Optional[BaseException] = None

    # ---------------- aiohttp app ----------------
    def make_app(self) -> web.Application:
        """Tạo Application (dùng cho cả server thật và aiohttp test client)."""
        app = web.Application(middlewares=[self._log_middleware], client_max_size=MAX_REPORT_BYTES)
        app.router.add_get("/ota/manifest.json", self._get_manifest)
        app.router.add_get("/ota/manifest.sig", self._get_sig)
        app.router.add_get("/ota/firmware.bin", self._get_firmware)
        app.router.add_post("/ota/report", self._post_report)
        # Mọi thứ còn lại (kể cả method khác trên route trên) -> 404, không liệt kê thư mục
        app.router.add_route("*", "/{tail:.*}", self._not_found)
        return app

    @web.middleware
    async def _log_middleware(self, request: web.Request, handler: Callable[[web.Request], Awaitable[web.StreamResponse]]) -> web.StreamResponse:
        t0 = time.perf_counter()
        status = 500
        try:
            resp = await handler(request)
            status = resp.status
            return resp
        except web.HTTPException as exc:
            status = exc.status
            raise
        finally:
            self._record(request, status, (time.perf_counter() - t0) * 1000.0)

    def _record(self, request: web.Request, status: int, duration_ms: float) -> None:
        ip = request.remote or "?"
        entry = RequestLogEntry(time.time(), ip, request.method, request.path, status, duration_ms)
        log.info("OTA %s", entry)
        if self.history is not None:
            try:
                self.history.add_request_log(ip, request.method, request.path, status, ts=entry.ts)
            except Exception as exc:  # pragma: no cover - không để lỗi DB làm chết server
                log.error("Không ghi được request_log: %s", exc)
        if self.on_request is not None:
            try:
                self.on_request(entry)
            except Exception as exc:  # pragma: no cover
                log.error("Callback on_request lỗi: %s", exc)

    def _file(self, name: str) -> Path:
        if self.release_dir is None:
            raise web.HTTPNotFound()
        p = self.release_dir / name
        if not p.is_file():
            raise web.HTTPNotFound()
        return p

    async def _get_manifest(self, request: web.Request) -> web.StreamResponse:
        p = self._file(MANIFEST_NAME)
        # Trả đúng các byte của file (ESP32 xác minh chữ ký trên byte nhận được)
        return web.Response(body=p.read_bytes(), content_type="application/json")

    async def _get_sig(self, request: web.Request) -> web.StreamResponse:
        p = self._file(SIG_NAME)
        return web.Response(body=p.read_bytes(), content_type="text/plain")

    async def _get_firmware(self, request: web.Request) -> web.StreamResponse:
        p = self._file(FIRMWARE_NAME)
        return web.FileResponse(p, headers={"Content-Type": "application/octet-stream"})

    async def _post_report(self, request: web.Request) -> web.StreamResponse:
        if request.content_length is not None and request.content_length > MAX_REPORT_BYTES:
            raise web.HTTPRequestEntityTooLarge(max_size=MAX_REPORT_BYTES, actual_size=request.content_length)
        raw = await request.content.read(MAX_REPORT_BYTES + 1)
        if len(raw) > MAX_REPORT_BYTES:
            raise web.HTTPRequestEntityTooLarge(max_size=MAX_REPORT_BYTES, actual_size=len(raw))
        try:
            rep = validate_report(json.loads(raw.decode("utf-8")))
        except (ValueError, UnicodeDecodeError) as exc:
            log.warning("Báo cáo thiết bị không hợp lệ từ %s: %s", request.remote, exc)
            return web.json_response({"ok": False, "error": str(exc)}, status=400)
        if self.history is not None:
            self.history.add_device_report(
                rep["device"], rep["version_code"], rep["result"], rep["reason"], remote_ip=request.remote or ""
            )
        else:
            log.info("Báo cáo thiết bị (không lưu DB): %s", rep)
        return web.json_response({"ok": True})

    async def _not_found(self, request: web.Request) -> web.StreamResponse:
        raise web.HTTPNotFound()

    # ---------------- vòng đời ----------------
    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(
        self,
        release_dir: Path | str,
        cert: Path | str,
        key: Path | str,
        host: str = "0.0.0.0",
        port: int = DEFAULT_PORT,
        ready_timeout: float = 10.0,
    ) -> None:
        """Chạy server trong thread riêng với event loop riêng; chờ tới khi lắng nghe hoặc lỗi.

        ``port=0`` -> hệ điều hành chọn cổng, đọc lại qua ``self.port`` (dùng cho test).
        """
        if self.running:
            raise ServerError("Server đang chạy")
        self.release_dir = Path(release_dir)
        if not self.release_dir.is_dir():
            raise ServerError(f"Thư mục phát hành không tồn tại: {self.release_dir}")
        ssl_ctx = make_ssl_context(cert, key)  # lỗi cert/key ném ngay ở thread gọi
        self.host, self.port = host, port
        self._ready.clear()
        self._start_error = None
        self._thread = threading.Thread(target=self._run, args=(ssl_ctx,), name="ota-https", daemon=True)
        self._thread.start()
        if not self._ready.wait(ready_timeout):
            raise ServerError("Server không khởi động kịp thời gian")
        if self._start_error is not None:
            err = self._start_error
            self._thread.join(timeout=2)
            self._thread = None
            raise ServerError(f"Không khởi động được server: {err}") from err
        log.info("OTA HTTPS server lắng nghe %s:%d, thư mục %s", self.host, self.port, self.release_dir)

    def _run(self, ssl_ctx: ssl.SSLContext) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        try:
            loop.run_until_complete(self._serve(ssl_ctx))
        except BaseException as exc:  # pragma: no cover - lỗi đã được báo qua _start_error
            if not self._ready.is_set():
                self._start_error = exc
                self._ready.set()
            else:
                log.exception("Server HTTPS dừng bất thường: %s", exc)
        finally:
            loop.close()
            self._loop = None

    async def _serve(self, ssl_ctx: ssl.SSLContext) -> None:
        self._stop_event = asyncio.Event()
        runner = web.AppRunner(self.make_app(), access_log=None)
        await runner.setup()
        try:
            site = web.TCPSite(runner, self.host, self.port, ssl_context=ssl_ctx, reuse_address=True)
            await site.start()
            srv = getattr(site, "_server", None)
            if srv is not None and srv.sockets:
                self.port = srv.sockets[0].getsockname()[1]
            self._ready.set()
            await self._stop_event.wait()
        finally:
            await runner.cleanup()

    def stop(self, timeout: float = 10.0) -> None:
        """Dừng server và chờ thread kết thúc (an toàn khi gọi lúc chưa chạy)."""
        loop, ev, th = self._loop, self._stop_event, self._thread
        if loop is not None and ev is not None and th is not None and th.is_alive():
            try:
                loop.call_soon_threadsafe(ev.set)
            except RuntimeError:  # loop đã đóng
                pass
            th.join(timeout)
        self._thread = None
        self._stop_event = None
        log.info("OTA HTTPS server đã dừng")

    def urls(self) -> list[str]:
        return local_urls(self.port)
