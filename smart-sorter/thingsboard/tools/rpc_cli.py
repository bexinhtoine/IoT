#!/usr/bin/env python3
"""T-04 / S-05 / A-05 — Gọi RPC two-way tới thiết bị qua REST API ThingsBoard để test whitelist từ laptop.

Luồng: POST /api/auth/login -> JWT; GET /api/tenant/devices?deviceName=<tên> -> deviceId;
       POST /api/plugins/rpc/twoway/<deviceId> {"method", "params", "timeout"} -> kết quả do Pi trả.

Ví dụ:
  python rpc_cli.py --device sorter-pi-01 --method getStatus
  python rpc_cli.py --device sorter-pi-01 --method start --params '{"speed": 60}'
  python rpc_cli.py --device sorter-pi-01 --method reboot              # A-05: phương thức lạ -> METHOD_NOT_ALLOWED
  for i in 1 2 3 4 5 6; do python rpc_cli.py --method getStatus; done   # lệnh thứ 6 -> RATE_LIMITED

Mật khẩu hỏi bằng getpass (hoặc biến môi trường TB_PASSWORD khi chạy script tự động), không ghi ra file.
TLS: KHÔNG BAO GIỜ tắt xác minh. Nếu web UI dùng HTTPS với CA nội bộ, truyền --ca-cert pki/out/ca.pem.
"""
from __future__ import annotations

import argparse
import getpass
import json
import logging
import os
import sys
from dataclasses import dataclass
from typing import Any

try:
    import requests
except ImportError:  # pragma: no cover
    sys.exit("Thiếu thư viện requests: pip install -r requirements.txt")

log = logging.getLogger("rpc_cli")

# Đường dẫn RPC: /api/rpc/twoway (3.3+) và /api/plugins/rpc/twoway (mọi bản 3.x). Thử cái đầu, lỗi 404 thì dùng cái sau.
RPC_PATHS = ("/api/rpc/twoway/{id}", "/api/plugins/rpc/twoway/{id}")


@dataclass
class TbSession:
    """Phiên REST với JWT. `verify` là True hoặc đường dẫn CA — không bao giờ False."""

    base_url: str
    token: str
    verify: bool | str = True

    @classmethod
    def login(cls, base_url: str, user: str, password: str, verify: bool | str = True, timeout: float = 15) -> "TbSession":
        r = requests.post(f"{base_url}/api/auth/login", json={"username": user, "password": password},
                          verify=verify, timeout=timeout)
        if r.status_code == 401:
            raise SystemExit("Đăng nhập thất bại (401): sai tài khoản/mật khẩu hoặc tài khoản bị khóa")
        r.raise_for_status()
        tok = r.json().get("token")
        if not tok:
            raise SystemExit("Đăng nhập không trả token (nếu bật 2FA: REST cần bước xác thực thêm — dùng tài khoản dịch vụ không 2FA cho test)")
        return cls(base_url, tok, verify)

    @property
    def headers(self) -> dict[str, str]:
        return {"X-Authorization": f"Bearer {self.token}", "Content-Type": "application/json"}

    def get(self, path: str, **params: Any) -> requests.Response:
        return requests.get(f"{self.base_url}{path}", headers=self.headers, params=params, verify=self.verify, timeout=15)

    def post(self, path: str, body: Any, timeout: float = 20) -> requests.Response:
        return requests.post(f"{self.base_url}{path}", headers=self.headers, json=body, verify=self.verify, timeout=timeout)

    def device_id(self, name: str) -> str:
        r = self.get("/api/tenant/devices", deviceName=name)
        if r.status_code == 404:
            raise SystemExit(f"Không tìm thấy device '{name}'")
        r.raise_for_status()
        return r.json()["id"]["id"]

    def rpc_twoway(self, device_id: str, method: str, params: dict[str, Any], timeout_ms: int = 10000) -> tuple[int, Any]:
        body = {"method": method, "params": params, "timeout": timeout_ms}
        last: requests.Response | None = None
        for path in RPC_PATHS:
            r = self.post(path.format(id=device_id), body, timeout=timeout_ms / 1000 + 5)
            last = r
            if r.status_code == 404 and "rpc" not in r.text.lower():
                continue  # endpoint không tồn tại ở phiên bản này -> thử đường dẫn kế
            break
        assert last is not None
        try:
            data = last.json()
        except ValueError:
            data = last.text
        return last.status_code, data


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Gọi RPC two-way tới thiết bị ThingsBoard (test whitelist S-05)")
    ap.add_argument("--tb-url", default=os.environ.get("TB_URL", "http://localhost:8080"))
    ap.add_argument("--user", default=os.environ.get("TB_USER", "tenant@thingsboard.org"))
    ap.add_argument("--password", default=None, help="mặc định hỏi bằng getpass (hoặc biến TB_PASSWORD)")
    ap.add_argument("--device", default=os.environ.get("TB_DEVICE", "sorter-pi-01"))
    ap.add_argument("--method", required=True, help="start | stop | emergencyStop | resetEstop | resetCounters | resetUsedCodes | otaCheck | getStatus | <lạ để test A-05>")
    ap.add_argument("--params", default="{}", help='JSON, ví dụ \'{"speed": 60}\'')
    ap.add_argument("--requested-by", default=None, help="giá trị requestedBy (mặc định = --user)")
    ap.add_argument("--timeout", type=int, default=10000, help="timeout RPC (ms), T-04 = 10000")
    ap.add_argument("--ca-cert", default=os.environ.get("TB_CA_CERT"), help="CA để xác minh HTTPS của web UI (nếu dùng HTTPS)")
    ap.add_argument("--repeat", type=int, default=1, help="gọi lặp n lần (test rate limit)")
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if a.verbose else logging.INFO, format="%(levelname)s %(message)s")

    try:
        params = json.loads(a.params)
    except ValueError as e:
        ap.error(f"--params không phải JSON: {e}")
    if not isinstance(params, dict):
        ap.error("--params phải là một object JSON")
    params.setdefault("requestedBy", a.requested_by or a.user)

    password = a.password or os.environ.get("TB_PASSWORD") or getpass.getpass(f"Mật khẩu {a.user}: ")
    verify: bool | str = a.ca_cert if a.ca_cert else True  # không có đường nào đặt False
    if a.tb_url.startswith("https://") and not a.ca_cert:
        log.info("HTTPS không có --ca-cert: dùng kho CA hệ thống (cert CA nội bộ sẽ bị từ chối — đó là hành vi đúng)")

    s = TbSession.login(a.tb_url, a.user, password, verify)
    dev_id = s.device_id(a.device)
    log.debug("device %s -> %s", a.device, dev_id)

    rc = 0
    for i in range(a.repeat):
        status, data = s.rpc_twoway(dev_id, a.method, params, a.timeout)
        tag = f"[{i + 1}/{a.repeat}] " if a.repeat > 1 else ""
        if status == 200:
            print(f"{tag}{json.dumps(data, ensure_ascii=False)}")
            if isinstance(data, dict) and data.get("ok") is False:
                rc = 1
        elif status == 504 or status == 408:
            print(f"{tag}TIMEOUT ({status}): thiết bị không trả lời trong {a.timeout} ms (Pi offline hoặc handler treo)")
            rc = 2
        else:
            print(f"{tag}HTTP {status}: {data}")
            rc = 3
    return rc


if __name__ == "__main__":
    sys.exit(main())
