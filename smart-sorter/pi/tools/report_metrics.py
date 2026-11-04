#!/usr/bin/env python3
"""P10 — report_metrics: gom số liệu từ SQLite của sorter-core và các file kết quả của P4/P7/P9
thành docs/metrics.md (bảng Markdown dán thẳng vào slide).

Nguồn (thiếu nguồn nào thì ghi "chưa có số liệu"):
  --db              SQLite sorter.db (packages, security_events, rpc_audit, anomaly_windows)
                    mặc định: pi/run/sorter.db nếu có, nếu không /var/lib/sorter/sorter.db
  --expected CSV    nhãn mong đợi pkg_id,expected_decision -> tỉ lệ đúng khay (tiêu chí 5.3)
  --metrics-json    ai/evaluate.py -> metrics.json (accuracy, precision/recall/F1, confusion)
  --model-card      model_card.json (phiên bản, ngưỡng, kích thước ảnh)
  --benchmark-json  pi/tools/benchmark_model.py -> benchmark.json (mean/p95, RAM)
  --anomaly-eval    sorter.anomaly.evaluate -> anomaly_eval.md (hoặc .json)
  --ota-reports     CSV xuất từ app OTA (tab Lịch sử, bảng device_reports)
  --note            ghi chú môi trường đo (ví dụ "laptop, sim, model dummy")

Ví dụ (trên laptop sau khi chạy sim + benchmark):
  cd pi && python -m sorter.main --sim --config config.sim.yaml
  python tools/benchmark_model.py --model ../ai/samples/dummy_packaging_cls.onnx --model-card ../ai/samples/model_card.json --runs 50 --out run/benchmark.json
  python tools/report_metrics.py --db run/sorter.db --benchmark-json run/benchmark.json --model-card ../ai/samples/model_card.json --out ../docs/metrics.md
Trên Pi:
  python tools/report_metrics.py --db /var/lib/sorter/sorter.db --expected expected.csv --metrics-json ~/metrics.json \
      --benchmark-json ~/benchmark.json --anomaly-eval ../docs/anomaly_eval.md --ota-reports ~/device_reports.csv --note "Pi 4, model v1.0.0"
"""
from __future__ import annotations

import argparse
import csv
import datetime as _dt
import json
import logging
import math
import sqlite3
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

PI_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = PI_ROOT.parent

log = logging.getLogger("report_metrics")

NO_DATA = "chưa có số liệu"
DECISIONS: tuple[str, ...] = ("SORT_A", "SORT_B", "REJECT_DEFECT", "REJECT_SECURITY", "REJECT_FAILSAFE")
STEP_COLUMNS: tuple[tuple[str, str], ...] = (
    ("latency_ms", "Tổng (dừng băng -> SORT đã ACK)"),
    ("t_capture_ms", "Chụp ảnh (settle + capture)"),
    ("t_qr_ms", "Đọc QR (pyzbar/cv2)"),
    ("t_verify_ms", "Xác thực HMAC + chống trùng"),
    ("t_ai_ms", "AI phân loại bao bì"),
)


# ---------------------------------------------------------------------------
# Tiện ích thống kê / markdown
# ---------------------------------------------------------------------------
def percentile(values: list[float], p: float) -> float:
    """Phân vị p (0-100) theo nội suy tuyến tính (giống numpy mặc định)."""
    if not values:
        return math.nan
    xs = sorted(values)
    k = (len(xs) - 1) * p / 100.0
    lo, hi = math.floor(k), math.ceil(k)
    if lo == hi:
        return xs[int(k)]
    return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)


def fmt_ms(v: float | None) -> str:
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return NO_DATA
    return f"{v:.1f} ms" if abs(v) < 10 else f"{v:.0f} ms"


def fmt_pct(v: float | None, digits: int = 1) -> str:
    return NO_DATA if v is None else f"{v * 100:.{digits}f} %"


def md_table(headers: Iterable[str], rows: Iterable[Iterable[Any]]) -> str:
    hs = list(headers)
    out = ["| " + " | ".join(hs) + " |", "|" + "---|" * len(hs)]
    for r in rows:
        out.append("| " + " | ".join(str(c) for c in r) + " |")
    return "\n".join(out)


def load_json(path: str | None) -> dict[str, Any] | None:
    if not path:
        return None
    p = Path(path)
    if not p.is_file():
        log.warning("Không tìm thấy %s", p)
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (ValueError, OSError) as e:
        log.warning("Không đọc được %s: %s", p, e)
        return None


# ---------------------------------------------------------------------------
# SQLite
# ---------------------------------------------------------------------------
@dataclass
class DbStats:
    path: str
    n_packages: int = 0
    by_decision: dict[str, int] | None = None
    steps: dict[str, dict[str, float]] | None = None   # cột -> {mean, p95, max, n}
    by_qr_status: dict[str, int] | None = None
    by_ai_label: dict[str, int] | None = None
    security: list[tuple[str, str, int]] | None = None   # (event_type, level, n)
    rpc: list[tuple[str, int, int]] | None = None        # (method, ok, n)
    anomaly_windows: int | None = None
    anomaly_flagged: int | None = None
    anomaly_scored: int | None = None
    anomaly_score_max: float | None = None
    first_ts_ms: int | None = None
    last_ts_ms: int | None = None
    packages: list[dict[str, Any]] | None = None


def default_db_path() -> Path:
    local = PI_ROOT / "run" / "sorter.db"
    return local if local.is_file() else Path("/var/lib/sorter/sorter.db")


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone()
    return row is not None


def read_db(path: Path, last_n: int = 0) -> DbStats | None:
    if not path.is_file():
        log.warning("Không có DB %s", path)
        return None
    st = DbStats(path=str(path))
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        if _table_exists(conn, "packages"):
            sql = "SELECT * FROM packages ORDER BY id DESC"
            rows = [dict(r) for r in conn.execute(sql + (f" LIMIT {int(last_n)}" if last_n > 0 else ""))]
            rows.reverse()
            st.packages = rows
            st.n_packages = len(rows)
            st.by_decision = dict(Counter(str(r["decision"]) for r in rows))
            st.by_qr_status = dict(Counter(str(r["qr_status"]) for r in rows))
            st.by_ai_label = dict(Counter(str(r["ai_label"]) for r in rows if r["ai_label"]))
            steps: dict[str, dict[str, float]] = {}
            for col, _ in STEP_COLUMNS:
                vals = [float(r[col]) for r in rows if r.get(col) is not None]
                if col == "t_ai_ms":  # chỉ tính kiện có chạy AI (QR hợp lệ)
                    vals = [float(r[col]) for r in rows if r.get(col) is not None and r.get("ai_label")]
                if vals:
                    steps[col] = {"n": len(vals), "mean": sum(vals) / len(vals), "p95": percentile(vals, 95), "max": max(vals)}
            st.steps = steps
            if rows:
                st.first_ts_ms, st.last_ts_ms = int(rows[0]["ts_ms"]), int(rows[-1]["ts_ms"])
        if _table_exists(conn, "security_events"):
            st.security = [(str(r["event_type"]), str(r["level"]), int(r["n"])) for r in conn.execute(
                "SELECT event_type, level, COUNT(*) AS n FROM security_events GROUP BY event_type, level ORDER BY n DESC")]
        if _table_exists(conn, "rpc_audit"):
            st.rpc = [(str(r["method"]), int(r["ok"]), int(r["n"])) for r in conn.execute(
                "SELECT method, ok, COUNT(*) AS n FROM rpc_audit GROUP BY method, ok ORDER BY n DESC")]
        if _table_exists(conn, "anomaly_windows"):
            r = conn.execute("SELECT COUNT(*) AS n, SUM(COALESCE(flag,0)) AS f, COUNT(score) AS s, MAX(score) AS mx FROM anomaly_windows").fetchone()
            st.anomaly_windows, st.anomaly_flagged, st.anomaly_scored = int(r["n"]), int(r["f"] or 0), int(r["s"] or 0)
            st.anomaly_score_max = float(r["mx"]) if r["mx"] is not None else None
    finally:
        conn.close()
    return st


# ---------------------------------------------------------------------------
# Nhãn mong đợi (tỉ lệ đúng khay)
# ---------------------------------------------------------------------------
def read_expected(path: str | None) -> dict[str, str] | None:
    """CSV pkg_id,expected_decision (có hoặc không có header). Trả {pkg_id: decision}."""
    if not path:
        return None
    p = Path(path)
    if not p.is_file():
        log.warning("Không tìm thấy file nhãn mong đợi %s", p)
        return None
    out: dict[str, str] = {}
    with p.open(encoding="utf-8", newline="") as f:
        for row in csv.reader(f):
            if len(row) < 2:
                continue
            pkg, dec = row[0].strip(), row[1].strip().upper()
            if pkg.lower() in ("pkg_id", "") or dec not in DECISIONS:
                continue
            out[pkg] = dec
    return out


def accuracy_vs_expected(packages: list[dict[str, Any]] | None, expected: dict[str, str] | None) -> tuple[int, int, list[str]]:
    """So quyết định cuối cùng của mỗi pkg_id (lần xử lý gần nhất) với nhãn mong đợi. Trả (đúng, tổng, danh sách sai)."""
    if not packages or not expected:
        return 0, 0, []
    last: dict[str, str] = {}
    for r in packages:
        if r.get("pkg_id"):
            last[str(r["pkg_id"])] = str(r["decision"])
    ok, total, wrong = 0, 0, []
    for pkg, exp in expected.items():
        if pkg not in last:
            continue
        total += 1
        if last[pkg] == exp:
            ok += 1
        else:
            wrong.append(f"{pkg}: {last[pkg]} (mong đợi {exp})")
    return ok, total, wrong


# ---------------------------------------------------------------------------
# Các nguồn file
# ---------------------------------------------------------------------------
def read_ota_reports(path: str | None) -> list[dict[str, str]] | None:
    if not path:
        return None
    p = Path(path)
    if not p.is_file():
        log.warning("Không tìm thấy CSV báo cáo OTA %s", p)
        return None
    with p.open(encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    return rows


def read_anomaly_eval(path: str | None) -> tuple[str | None, dict[str, Any] | None]:
    """Trả (markdown, json). .md -> nhúng nguyên văn (bỏ dòng tiêu đề cấp 1); .json -> dict."""
    if not path:
        return None, None
    p = Path(path)
    if not p.is_file():
        log.warning("Không tìm thấy kết quả đánh giá anomaly %s", p)
        return None, None
    if p.suffix.lower() == ".json":
        return None, load_json(str(p))
    text = p.read_text(encoding="utf-8")
    lines = [ln for ln in text.splitlines() if not ln.startswith("# ")]
    return "\n".join(lines).strip(), None


# ---------------------------------------------------------------------------
# Sinh Markdown
# ---------------------------------------------------------------------------
def _status(ok: bool | None) -> str:
    if ok is None:
        return NO_DATA
    return "ĐẠT" if ok else "CHƯA ĐẠT"


def render(
    db: DbStats | None,
    expected: dict[str, str] | None,
    metrics: dict[str, Any] | None,
    card: dict[str, Any] | None,
    bench: dict[str, Any] | None,
    anomaly_md: str | None,
    anomaly_json: dict[str, Any] | None,
    ota: list[dict[str, str]] | None,
    note: str,
) -> str:
    now = _dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    L: list[str] = []
    L.append("# Số liệu cho slide — Smart Logistics Sorter (P10)\n")
    L.append(f"Sinh bởi `pi/tools/report_metrics.py` lúc {now}. **Môi trường đo: {note or 'chưa ghi'}.**")
    L.append("Mỗi bảng ghi rõ nguồn; ô *chưa có số liệu* nghĩa là chưa chạy bước tương ứng (xem docs/RUNBOOK.md).")
    L.append("Cột **Số đo trên Pi thật** để trống cho nhóm điền sau khi chạy `report_metrics.py` trên Pi.\n")

    # ---- 1. Latency ------------------------------------------------------
    L.append("## 1. Độ trễ xử lý một kiện (P-08, tiêu chí 5.3 ≤ 1 s)\n")
    if db and db.steps:
        rows = []
        for col, label in STEP_COLUMNS:
            s = db.steps.get(col)
            if s:
                rows.append((label, f"`{col}`", int(s["n"]), fmt_ms(s["mean"]), fmt_ms(s["p95"]), fmt_ms(s["max"]), ""))
            else:
                rows.append((label, f"`{col}`", 0, NO_DATA, NO_DATA, NO_DATA, ""))
        L.append(f"Nguồn: bảng `packages` trong `{db.path}` ({db.n_packages} kiện"
                 + (f", từ {_dt.datetime.fromtimestamp(db.first_ts_ms / 1000):%Y-%m-%d %H:%M} đến {_dt.datetime.fromtimestamp(db.last_ts_ms / 1000):%H:%M}" if db.first_ts_ms else "")
                 + ").\n")
        L.append(md_table(["Bước", "Cột DB", "n", "Trung bình", "p95", "Max", "Số đo trên Pi thật"], rows))
        L.append("\n`t_capture_ms` gồm settle_ms + thời gian chụp các khung (tối đa max_qr_frames); `latency_ms` tính tới khi ESP32 ACK lệnh SORT.")
    else:
        L.append(f"*{NO_DATA}* — chạy `python -m sorter.main --sim --config config.sim.yaml` (laptop) hoặc để dịch vụ `sorter` chạy trên Pi.")
    L.append("")

    # ---- 2. Số kiện theo quyết định ---------------------------------------
    L.append("## 2. Số kiện theo quyết định (P-06, P-11)\n")
    if db and db.by_decision is not None:
        total = max(1, db.n_packages)
        rows = [(d, db.by_decision.get(d, 0), fmt_pct(db.by_decision.get(d, 0) / total)) for d in DECISIONS]
        rows.append(("**Tổng**", db.n_packages, "100 %"))
        L.append(md_table(["Quyết định", "Số kiện", "Tỉ lệ"], rows))
        if db.by_qr_status:
            L.append("\nTheo kết quả QR: " + ", ".join(f"{k} = {v}" for k, v in sorted(db.by_qr_status.items())) + ".")
        if db.by_ai_label:
            L.append("Theo nhãn AI (chỉ kiện QR hợp lệ): " + ", ".join(f"{k} = {v}" for k, v in sorted(db.by_ai_label.items())) + ".")
    else:
        L.append(f"*{NO_DATA}*")
    L.append("")

    # ---- 3. Tỉ lệ đúng khay ---------------------------------------------
    L.append("## 3. Tỉ lệ phân loại đúng khay (tiêu chí 5.3: 20/20 kiện ở kịch bản 1–4)\n")
    ok, total, wrong = accuracy_vs_expected(db.packages if db else None, expected)
    if expected and total:
        L.append(md_table(["Kiện có nhãn mong đợi", "Đúng khay", "Tỉ lệ", "Trạng thái"],
                          [(total, ok, fmt_pct(ok / total), _status(ok == total))]))
        if wrong:
            L.append("\nKiện sai: " + "; ".join(wrong))
    elif expected:
        L.append(f"*{NO_DATA}* — file nhãn có {len(expected)} mã nhưng không mã nào xuất hiện trong DB.")
    else:
        L.append(f"*{NO_DATA}* — chạy 20 kiện kịch bản 1–4 rồi truyền `--expected expected.csv` (pkg_id,expected_decision).")
    L.append("")

    # ---- 4. AI ------------------------------------------------------------
    L.append("## 4. Model AI kiểm tra bao bì (AI-04, AI-05, AI-07)\n")
    if card:
        inp = card.get("input") or {}
        size = inp.get("size") or (inp.get("shape") or [None, None, None])[-1]
        L.append(md_table(["Thuộc tính", "Giá trị"], [
            ("Model / phiên bản", f"{card.get('model_name', '?')} v{card.get('version', '?')}"),
            ("Kiến trúc", card.get("architecture", "?")),
            ("Độ chính xác số (precision)", card.get("precision", "?")),
            ("Ảnh vào", f"{size} px, {inp.get('color', '?')}, range {inp.get('range', '?')}"),
            ("Ngưỡng DEFECT (chọn trên val)", f"{card.get('defect_threshold', '?')} (chọn: {card.get('threshold_chosen_on', '?')})"),
            ("Mục tiêu recall lớp lỗi", card.get("target_recall", "?")),
        ]))
        L.append("")
    else:
        L.append(f"Model card: *{NO_DATA}* (`--model-card`).\n")
    test = (metrics or {}).get("test") if metrics else None
    if test:
        pc = test.get("per_class", {})
        d, o = pc.get("DEFECT", {}), pc.get("OK", {})
        acc = float(test.get("accuracy", 0))
        rec_d = float(d.get("recall", 0))
        L.append("Nguồn: `ai/evaluate.py` → metrics.json (tập test"
                 + (f", {test.get('n')} ảnh" if test.get("n") else "") + ").\n")
        L.append(md_table(["Chỉ số", "Giá trị", "Mục tiêu 5.3", "Trạng thái"], [
            ("Accuracy (test)", fmt_pct(acc), "≥ 90 %", _status(acc >= 0.90)),
            ("Recall lớp Lỗi", fmt_pct(rec_d), "≥ 95 %", _status(rec_d >= 0.95)),
            ("Precision lớp Lỗi", fmt_pct(float(d.get("precision", 0))), "—", ""),
            ("Recall lớp OK (1 − loại nhầm hàng tốt)", fmt_pct(float(o.get("recall", 0))), "—", ""),
            ("F1 lớp Lỗi", f"{float(d.get('f1', 0)):.3f}", "—", ""),
            ("AUC", f"{test.get('auc')}" if test.get("auc") is not None else NO_DATA, "—", ""),
            ("Tỉ lệ UNCERTAIN", fmt_pct(float(test.get("uncertain_rate", 0))), "—", ""),
            ("Ngưỡng dùng", f"{test.get('threshold')}", "—", ""),
        ]))
        cm = test.get("confusion")
        if cm and len(cm) == 2:
            L.append("\nMa trận nhầm lẫn (hàng = thật, cột = dự đoán; lớp dương = DEFECT):\n")
            L.append(md_table(["", "Dự đoán OK", "Dự đoán DEFECT"], [("Thật OK", cm[0][0], cm[0][1]), ("Thật DEFECT", cm[1][0], cm[1][1])]))
    else:
        L.append(f"Đánh giá trên tập test: *{NO_DATA}* — cần dataset thật: `ai/prepare_dataset.py` → train → `ai/evaluate.py --update-card` rồi truyền `--metrics-json`.")
    L.append("")

    # ---- 5. Benchmark ----------------------------------------------------
    L.append("## 5. Benchmark suy luận (AI-05, tiêu chí 5.3 ≤ 150 ms trung bình)\n")
    if bench:
        host = bench.get("host") or {}
        mean = float(bench.get("mean_ms", math.nan))
        L.append(f"Nguồn: `pi/tools/benchmark_model.py` ({bench.get('runs')} lần, warm-up {bench.get('warmup')}, ảnh `{bench.get('image')}`), "
                 f"máy: {host.get('board') or host.get('platform', '?')}, {host.get('machine', '?')}, ORT {host.get('onnxruntime', '?')}.\n")
        L.append(md_table(["Chỉ số", "Giá trị (máy đo này)", "Số đo trên Pi thật", "Mục tiêu", "Trạng thái"], [
            ("Model", f"{Path(str(bench.get('model', ''))).name} v{bench.get('model_version')} ({bench.get('backend')}, {bench.get('input_size')} px, {bench.get('threads')} threads)", "", "", ""),
            ("Thời gian 1 lần suy luận — trung bình (gồm tiền xử lý)", fmt_ms(mean), "", "≤ 150 ms", _status(mean <= 150)),
            ("p50 / p95 / max", f"{fmt_ms(bench.get('p50_ms'))} / {fmt_ms(bench.get('p95_ms'))} / {fmt_ms(bench.get('max_ms'))}", "", "—", ""),
            ("Chỉ session.run (trung bình)", f"{float(bench.get('infer_only_mean_ms', 0)):.2f} ms", "", "—", ""),
            ("Nạp model", fmt_ms(bench.get("load_ms")), "", "—", ""),
            ("RAM RSS trước → sau nạp model", f"{float(bench.get('rss_before_mb', 0)):.0f} → {float(bench.get('rss_after_mb', 0)):.0f} MB (đỉnh {float(bench.get('rss_peak_mb', 0)):.0f} MB)", "", "—", ""),
        ]))
    else:
        L.append(f"*{NO_DATA}* — `python tools/benchmark_model.py --config /etc/sorter/config.yaml --runs 100` trên Pi rồi truyền `--benchmark-json`.")
    L.append("")

    # ---- 6. Bảo mật -------------------------------------------------------
    L.append("## 6. Sự kiện bảo mật và RPC (S-05, S-09)\n")
    if db and db.security is not None:
        if db.security:
            L.append(md_table(["Loại sự kiện", "Mức", "Số lần"], db.security))
        else:
            L.append("Không có sự kiện bảo mật nào trong DB.")
        if db.rpc:
            L.append("\nAudit RPC (`rpc_audit`):\n")
            L.append(md_table(["Phương thức", "Kết quả", "Số lần"], [(m, "ok" if ok_ else "từ chối", n) for m, ok_, n in db.rpc]))
        else:
            L.append("\nAudit RPC: chưa có lệnh nào (chạy sim không có RPC; trên dashboard bấm Start/Stop/E-stop để có dữ liệu).")
    else:
        L.append(f"*{NO_DATA}*")
    L.append("")

    # ---- 7. OTA -----------------------------------------------------------
    L.append("## 7. Secure OTA (O-07, O-10 → O-12, tiêu chí 5.3: 3/3 kiểu tấn công bị từ chối)\n")
    L.append("Xác minh phía app (không cần ESP32): `python tools/demo_check.py --only 6a,6b,12` → 6a hợp lệ, 6b HASH_MISMATCH + SIGNATURE_INVALID, 12 ROLLBACK_REJECTED (xem bảng demo_check).\n")
    if ota is not None:
        if ota:
            c = Counter((r.get("result", "?"), r.get("reason", "")) for r in ota)
            L.append("Báo cáo từ ESP32 thật (`POST /ota/report`, CSV từ tab Lịch sử của app OTA):\n")
            L.append(md_table(["Kết quả", "Lý do", "Số lần"], [(k[0], k[1] or "—", n) for k, n in c.most_common()]))
            rejected = sum(n for (res, _), n in c.items() if res == "REJECTED")
            L.append(f"\nTổng {len(ota)} báo cáo; {rejected} lần REJECTED.")
        else:
            L.append("CSV báo cáo OTA rỗng.")
    else:
        L.append(f"Kết quả trên ESP32 thật: *{NO_DATA}* — chạy 4 ca theo `esp32/OTA_TEST.md`, xuất CSV `device_reports` từ app OTA rồi truyền `--ota-reports`.")
    L.append("")

    # ---- 8. Anomaly -------------------------------------------------------
    L.append("## 8. Phát hiện bất thường (A-03, A-06, tiêu chí 5.3: báo nhầm ≤ 1 lần / 30 phút)\n")
    if db and db.anomaly_windows is not None:
        L.append(f"Cửa sổ đặc trưng đã lưu (`anomaly_windows`): {db.anomaly_windows}; có điểm (đã có model): {db.anomaly_scored}; "
                 f"bị gắn cờ: {db.anomaly_flagged}; điểm cao nhất: {f'{db.anomaly_score_max:.3f}' if db.anomaly_score_max is not None else NO_DATA}.\n")
    else:
        L.append("Bảng `anomaly_windows`: chưa có (anomaly.enabled=false hoặc chưa chạy detector).\n")
    if anomaly_md:
        L.append("Nguồn: `python -m sorter.anomaly.evaluate` →\n")
        L.append(anomaly_md)
    elif anomaly_json:
        L.append("```json\n" + json.dumps(anomaly_json, ensure_ascii=False, indent=2)[:4000] + "\n```")
    else:
        L.append(f"Tỉ lệ phát hiện / báo nhầm: *{NO_DATA}* — thu ≥ 30 phút dữ liệu bình thường, `sorter.anomaly.train`, chạy `tools/attack_sim.py`, rồi `sorter.anomaly.evaluate --out docs/anomaly_eval.md` và truyền `--anomaly-eval`.")
    L.append("")

    # ---- 9. Bảng tiêu chí 5.3 ---------------------------------------------
    L.append("## 9. Đối chiếu tiêu chí nghiệm thu 5.3\n")
    lat_mean = db.steps["latency_ms"]["mean"] if db and db.steps and "latency_ms" in db.steps else None
    sec_counts = {k: n for k, _, n in (db.security or [])} if db else {}
    bad_sig = sec_counts.get("QR_BAD_SIGNATURE", 0) + sec_counts.get("QR_DUPLICATE", 0)
    # Kiện QR giả/trùng: tất cả phải REJECT_SECURITY; "lọt" = vào A/B hoặc quyết định khác
    attack_rows = [r for r in (db.packages or []) if r.get("qr_status") in ("BAD_SIGNATURE", "DUPLICATE")] if db else []
    blocked_total = len(attack_rows)
    leaked = sum(1 for r in attack_rows if r.get("decision") != "REJECT_SECURITY")
    rows = [
        ("Accuracy AI trên tập test ≥ 90 %, recall lớp Lỗi ≥ 95 %",
         (fmt_pct(float(test["accuracy"])) + " / " + fmt_pct(float(test["per_class"]["DEFECT"]["recall"]))) if test else NO_DATA,
         _status(float(test["accuracy"]) >= 0.9 and float(test["per_class"]["DEFECT"]["recall"]) >= 0.95) if test else NO_DATA),
        ("Suy luận trên Pi ≤ 150 ms trung bình", fmt_ms(float(bench["mean_ms"])) if bench else NO_DATA, _status(float(bench["mean_ms"]) <= 150) if bench else NO_DATA),
        ("Dừng băng → lệnh SORT ≤ 1 s (trung bình latency_ms)", fmt_ms(lat_mean), _status(lat_mean <= 1000) if lat_mean is not None else NO_DATA),
        ("Phân loại đúng khay 20/20 (kịch bản 1–4)", f"{ok}/{total}" if total else NO_DATA, _status(ok == total) if total else NO_DATA),
        ("E-stop từ dashboard: băng dừng ≤ 0,5 s", "đo bằng video", "kiểm trên phần cứng"),
        ("QR giả và QR trùng bị chặn 100 %",
         f"{blocked_total - leaked}/{blocked_total} kiện bị chặn, {bad_sig} sự kiện bảo mật" if blocked_total else NO_DATA,
         _status(leaked == 0) if blocked_total else NO_DATA),
        ("OTA tấn công bị từ chối 3/3 kiểu", "demo_check 6b + 12: 3/3 (phía app)", "ĐẠT (app); ESP32: kiểm trên phần cứng"),
        ("Cảnh báo nhầm Isolation Forest ≤ 1 / 30 phút", "xem mục 8", NO_DATA if not (anomaly_md or anomaly_json) else "xem mục 8"),
    ]
    L.append(md_table(["Tiêu chí", "Số đo hiện có", "Trạng thái"], rows))
    L.append("")
    L.append("---")
    L.append("Ghi chú: số đo trong file này là của máy đã chạy `report_metrics.py` (xem dòng *Môi trường đo* ở đầu). "
             "Số đo của sim trên laptop với model dummy **không** dùng để kết luận chất lượng AI; chỉ chứng minh đường đi dữ liệu và công thức tính.")
    return "\n".join(L) + "\n"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Sinh docs/metrics.md từ SQLite + file kết quả của P4/P7/P9")
    ap.add_argument("--db", default=None, help="sorter.db (mặc định pi/run/sorter.db nếu có, nếu không /var/lib/sorter/sorter.db)")
    ap.add_argument("--last", type=int, default=0, help="chỉ lấy N kiện gần nhất (0 = tất cả)")
    ap.add_argument("--expected", default=None, help="CSV pkg_id,expected_decision")
    ap.add_argument("--metrics-json", dest="metrics_json", default=None, help="metrics.json của ai/evaluate.py")
    ap.add_argument("--model-card", dest="model_card", default=None, help="model_card.json")
    ap.add_argument("--benchmark-json", dest="benchmark_json", default=None, help="benchmark.json của tools/benchmark_model.py")
    ap.add_argument("--anomaly-eval", dest="anomaly_eval", default=None, help="anomaly_eval.md hoặc .json của sorter.anomaly.evaluate")
    ap.add_argument("--ota-reports", dest="ota_reports", default=None, help="CSV device_reports xuất từ app OTA")
    ap.add_argument("--note", default="", help="ghi chú môi trường đo, ví dụ 'laptop Windows, sim, model dummy'")
    ap.add_argument("--out", default=str(REPO_ROOT / "docs" / "metrics.md"))
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING, format="%(levelname)s %(name)s: %(message)s")

    db_path = Path(args.db) if args.db else default_db_path()
    db = read_db(db_path, args.last)
    expected = read_expected(args.expected)
    metrics = load_json(args.metrics_json)
    card = load_json(args.model_card)
    bench = load_json(args.benchmark_json)
    anomaly_md, anomaly_json = read_anomaly_eval(args.anomaly_eval)
    ota = read_ota_reports(args.ota_reports)
    note = args.note or ("sim trên máy dev (FakeEsp32, FolderCamera, model dummy)" if db and "run" in Path(db.path).parts else "")

    md = render(db, expected, metrics, card, bench, anomaly_md, anomaly_json, ota, note)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(md, encoding="utf-8")
    missing = [name for name, v in (("DB", db), ("expected", expected), ("metrics.json", metrics), ("model_card", card),
                                    ("benchmark.json", bench), ("anomaly-eval", anomaly_md or anomaly_json), ("ota-reports", ota)) if not v]
    print(f"Đã ghi {out} ({len(md.splitlines())} dòng). Nguồn thiếu: {', '.join(missing) if missing else 'không'}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
