#!/usr/bin/env python3
"""T-02 / T-05 / T-08 (tùy chọn) — Tạo nhanh device profile "Sorter", device sorter-pi-01 (X.509), sorter-view,
customer "Viewer" và gán sorter-view cho customer qua REST API, thay cho thao tác tay trong SETUP.md mục 2-4.

Endpoint dùng (ThingsBoard CE 3.x — đối chiếu Swagger tại <TB_URL>/swagger-ui/ nếu khác phiên bản):
  POST /api/auth/login
  GET  /api/deviceProfiles?pageSize=100&page=0&textSearch=   |  POST /api/deviceProfile
  GET  /api/tenant/devices?deviceName=                        |  POST /api/device-with-credentials
  GET  /api/tenant/customers?pageSize=&page=&textSearch=      |  POST /api/customer
  POST /api/customer/{customerId}/device/{deviceId}
  POST /api/plugins/telemetry/DEVICE/{deviceId}/attributes/SERVER_SCOPE  (inactivityTimeout = 30000 ms)

Alarm rules của device profile (--with-alarms) dùng JSON profileData.alarms theo định dạng 3.x; nếu phiên bản
từ chối (HTTP 400) thì tạo profile không alarm và làm alarm rules bằng tay (SETUP.md mục 2b).
Rule chain sao chép sang sorter-view và dashboard KHÔNG tạo ở đây (làm trên UI, SETUP.md mục 4, 7).

Chạy:  python tb_provision.py --device-cert ../pki/out/sorter-pi-01.pem [--with-alarms]
"""
from __future__ import annotations

import argparse
import getpass
import logging
import os
import sys
from pathlib import Path
from typing import Any

from rpc_cli import TbSession  # cùng thư mục

log = logging.getLogger("tb_provision")

PROFILE_NAME = "Sorter"
CUSTOMER_NAME = "Viewer"


def _ts_bool_rule(key: str, severity_rule: dict[str, Any] | None = None) -> dict[str, Any]:
    """Điều kiện: time-series <key> == true."""
    return {
        "condition": [{
            "key": {"type": "TIME_SERIES", "key": key},
            "valueType": "BOOLEAN",
            "value": None,
            "predicate": {"type": "BOOLEAN", "operation": "EQUAL", "value": {"defaultValue": True, "userValue": None, "dynamicValue": None}},
        }],
        "spec": {"type": "SIMPLE"},
    }


def _ts_str_eq(key: str, value: str) -> dict[str, Any]:
    return {
        "key": {"type": "TIME_SERIES", "key": key},
        "valueType": "STRING",
        "value": None,
        "predicate": {"type": "STRING", "operation": "EQUAL", "ignoreCase": False,
                      "value": {"defaultValue": value, "userValue": None, "dynamicValue": None}},
    }


def _security_rule(level: str) -> dict[str, Any]:
    cond = _ts_bool_rule("security_alert")
    cond["condition"].append(_ts_str_eq("security_level", level))
    return {"condition": cond, "schedule": None, "alarmDetails": "Sự kiện bảo mật ${security_event_type}: ${security_detail}", "dashboardId": None}


def alarm_rules() -> list[dict[str, Any]]:
    clear_security = {"condition": {
        "condition": [{
            "key": {"type": "TIME_SERIES", "key": "security_alert"}, "valueType": "BOOLEAN", "value": None,
            "predicate": {"type": "BOOLEAN", "operation": "EQUAL", "value": {"defaultValue": False, "userValue": None, "dynamicValue": None}},
        }], "spec": {"type": "SIMPLE"}}, "schedule": None, "alarmDetails": None, "dashboardId": None}
    return [
        {
            "id": "security-alert", "alarmType": "Security alert",
            "createRules": {"CRITICAL": _security_rule("CRITICAL"), "MAJOR": _security_rule("MAJOR"),
                            "WARNING": _security_rule("WARNING"), "MINOR": _security_rule("INFO")},
            "clearRule": clear_security, "propagate": False, "propagateToOwner": False, "propagateToTenant": False,
            "propagateRelationTypes": None,
        },
        {
            "id": "anomaly", "alarmType": "Anomaly",
            "createRules": {"CRITICAL": {"condition": _ts_bool_rule("anomaly_flag"), "schedule": None,
                                         "alarmDetails": "Anomaly score ${anomaly_score}: ${anomaly_top_features}", "dashboardId": None}},
            "clearRule": {"condition": {"condition": [{
                "key": {"type": "TIME_SERIES", "key": "anomaly_flag"}, "valueType": "BOOLEAN", "value": None,
                "predicate": {"type": "BOOLEAN", "operation": "EQUAL", "value": {"defaultValue": False, "userValue": None, "dynamicValue": None}},
            }], "spec": {"type": "SIMPLE"}}, "schedule": None, "alarmDetails": None, "dashboardId": None},
            "propagate": False, "propagateToOwner": False, "propagateToTenant": False, "propagateRelationTypes": None,
        },
        {
            "id": "high-cpu-temp", "alarmType": "High CPU temperature",
            "createRules": {"WARNING": {"condition": {"condition": [{
                "key": {"type": "TIME_SERIES", "key": "cpu_temp"}, "valueType": "NUMERIC", "value": None,
                "predicate": {"type": "NUMERIC", "operation": "GREATER", "value": {"defaultValue": 75, "userValue": None, "dynamicValue": None}},
            }], "spec": {"type": "SIMPLE"}}, "schedule": None, "alarmDetails": "CPU ${cpu_temp} °C", "dashboardId": None}},
            "clearRule": {"condition": {"condition": [{
                "key": {"type": "TIME_SERIES", "key": "cpu_temp"}, "valueType": "NUMERIC", "value": None,
                "predicate": {"type": "NUMERIC", "operation": "LESS", "value": {"defaultValue": 70, "userValue": None, "dynamicValue": None}},
            }], "spec": {"type": "SIMPLE"}}, "schedule": None, "alarmDetails": None, "dashboardId": None},
            "propagate": False, "propagateToOwner": False, "propagateToTenant": False, "propagateRelationTypes": None,
        },
    ]


def ensure_profile(s: TbSession, with_alarms: bool) -> str:
    r = s.get("/api/deviceProfiles", pageSize=100, page=0, textSearch=PROFILE_NAME)
    r.raise_for_status()
    for p in r.json().get("data", []):
        if p["name"] == PROFILE_NAME:
            log.info("Device profile '%s' đã có", PROFILE_NAME)
            return p["id"]["id"]
    body: dict[str, Any] = {
        "name": PROFILE_NAME, "type": "DEFAULT", "transportType": "MQTT", "provisionType": "DISABLED",
        "description": "Smart Logistics Sorter — MQTT TLS + X.509 (S-01)",
        "profileData": {
            "configuration": {"type": "DEFAULT"},
            "transportConfiguration": {
                "type": "MQTT", "deviceTelemetryTopic": "v1/devices/me/telemetry",
                "deviceAttributesTopic": "v1/devices/me/attributes", "sendAckOnValidationException": False,
                "transportPayloadTypeConfiguration": {"transportPayloadType": "JSON"},
            },
            "provisionConfiguration": {"type": "DISABLED", "provisionDeviceSecret": None},
            "alarms": alarm_rules() if with_alarms else [],
        },
    }
    r = s.post("/api/deviceProfile", body)
    if r.status_code == 400 and with_alarms:
        log.warning("Phiên bản từ chối alarm rules JSON (%s) — tạo profile không alarm; làm alarm bằng tay (SETUP.md 2b)", r.text[:200])
        body["profileData"]["alarms"] = []
        r = s.post("/api/deviceProfile", body)
    r.raise_for_status()
    log.info("Đã tạo device profile '%s'", PROFILE_NAME)
    return r.json()["id"]["id"]


def ensure_device(s: TbSession, name: str, profile_id: str, cert_pem: str | None, label: str) -> str:
    r = s.get("/api/tenant/devices", deviceName=name)
    if r.status_code == 200:
        log.info("Device '%s' đã có (không đổi credentials)", name)
        return r.json()["id"]["id"]
    device = {"name": name, "label": label, "deviceProfileId": {"entityType": "DEVICE_PROFILE", "id": profile_id}}
    if cert_pem:
        body = {"device": device, "credentials": {"credentialsType": "X509_CERTIFICATE", "credentialsValue": cert_pem.strip()}}
    else:
        body = {"device": device, "credentials": {"credentialsType": "ACCESS_TOKEN", "credentialsValue": None}}
    r = s.post("/api/device-with-credentials", body)
    r.raise_for_status()
    dev_id = r.json()["id"]["id"]
    log.info("Đã tạo device '%s' (%s)", name, "X.509" if cert_pem else "token — thiết bị gương, không kết nối thật")
    # Offline sau 30 s không có dữ liệu (T-05)
    r = s.post(f"/api/plugins/telemetry/DEVICE/{dev_id}/attributes/SERVER_SCOPE", {"inactivityTimeout": 30000})
    if r.status_code >= 300:
        log.warning("Không đặt được inactivityTimeout cho %s: %s", name, r.text[:120])
    return dev_id


def ensure_customer(s: TbSession) -> str:
    r = s.get("/api/tenant/customers", pageSize=100, page=0, textSearch=CUSTOMER_NAME)
    r.raise_for_status()
    for c in r.json().get("data", []):
        if c["title"] == CUSTOMER_NAME:
            return c["id"]["id"]
    r = s.post("/api/customer", {"title": CUSTOMER_NAME})
    r.raise_for_status()
    log.info("Đã tạo customer '%s'", CUSTOMER_NAME)
    return r.json()["id"]["id"]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Provision ThingsBoard cho Smart Sorter (tùy chọn)")
    ap.add_argument("--tb-url", default=os.environ.get("TB_URL", "http://localhost:8080"))
    ap.add_argument("--user", default=os.environ.get("TB_USER", "tenant@thingsboard.org"))
    ap.add_argument("--ca-cert", default=os.environ.get("TB_CA_CERT"))
    ap.add_argument("--device", default="sorter-pi-01")
    ap.add_argument("--view-device", default="sorter-view")
    ap.add_argument("--device-cert", type=Path, default=None, help="pki/out/<device>.pem để đặt credentials X.509")
    ap.add_argument("--with-alarms", action="store_true", help="kèm 3 alarm rules (Security alert / Anomaly / High CPU temperature)")
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if a.verbose else logging.INFO, format="%(levelname)s %(message)s")

    cert_pem = a.device_cert.read_text(encoding="utf-8") if a.device_cert else None
    if cert_pem is None:
        log.warning("Không có --device-cert: device %s sẽ tạo với access token, phải đổi sang X.509 trên UI", a.device)
    password = os.environ.get("TB_PASSWORD") or getpass.getpass(f"Mật khẩu {a.user}: ")
    s = TbSession.login(a.tb_url, a.user, password, a.ca_cert if a.ca_cert else True)

    profile_id = ensure_profile(s, a.with_alarms)
    ensure_device(s, a.device, profile_id, cert_pem, "Máy phân loại thật")
    view_id = ensure_device(s, a.view_device, profile_id, None, "Thiết bị gương cho Viewer")
    cust_id = ensure_customer(s)
    r = s.post(f"/api/customer/{cust_id}/device/{view_id}", None)
    if r.status_code < 300:
        log.info("Đã gán %s cho customer %s", a.view_device, CUSTOMER_NAME)
    else:
        log.warning("Gán device cho customer lỗi: %s", r.text[:120])
    print("Xong. Còn lại làm trên UI: rule chain sao chép (SETUP.md 4), Customer User + dashboard (5, 7), 2FA (6).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
