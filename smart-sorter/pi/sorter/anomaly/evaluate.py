"""P9 / A-06 — Đánh giá hiệu quả phát hiện bất thường.

Đối chiếu cửa sổ đã chấm điểm (anomaly_windows) với nhật ký attack_sim để tính:
- Tỉ lệ phát hiện từng kịch bản tấn công (có flag trong [start, end + 20 s]).
- Số báo nhầm (false positive) trong khoảng chạy bình thường.
Xuất bảng Markdown và biểu đồ PNG điểm theo thời gian, tô vùng tấn công.

CLI:
  python -m sorter.anomaly.evaluate --db PATH --attack-log attack_log.jsonl \
      --normal-from "YYYY-mm-dd HH:MM" --normal-to ... \
      --out docs/anomaly_eval.md --png docs/anomaly_scores.png
"""
from __future__ import annotations

import argparse
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .store import AnomalyStore
from .train import _parse_time_to_ms

log = logging.getLogger(__name__)

# Cửa sổ ân hạn sau khi tấn công kết thúc vẫn tính là phát hiện đúng
GRACE_MS = 20_000


@dataclass
class ScenarioResult:
    scenario: str
    start_ms: int
    end_ms: int
    n_windows: int
    n_flagged: int

    @property
    def detected(self) -> bool:
        return self.n_flagged > 0

    @property
    def rate(self) -> float:
        return (self.n_flagged / self.n_windows) if self.n_windows else 0.0


def load_attack_log(path: str) -> list[dict[str, Any]]:
    """Đọc attack_log.jsonl: mỗi dòng {scenario, start_ms, end_ms}."""
    entries: list[dict[str, Any]] = []
    p = Path(path)
    if not p.exists():
        return entries
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            log.warning("Bỏ dòng không hợp lệ trong attack-log: %s", line[:80])
    return entries


def evaluate_scenarios(
    store: AnomalyStore, attacks: list[dict[str, Any]]
) -> list[ScenarioResult]:
    results: list[ScenarioResult] = []
    for a in attacks:
        start = int(a["start_ms"])
        end = int(a["end_ms"])
        rows = store.fetch(start, end + GRACE_MS)
        n_flagged = sum(1 for r in rows if r.get("flag"))
        results.append(
            ScenarioResult(
                scenario=str(a.get("scenario", "?")),
                start_ms=start,
                end_ms=end,
                n_windows=len(rows),
                n_flagged=n_flagged,
            )
        )
    return results


def count_false_positives(store: AnomalyStore, from_ms: int, to_ms: int) -> tuple[int, int]:
    """Đếm (số báo nhầm, tổng cửa sổ) trong khoảng bình thường."""
    rows = store.fetch(from_ms, to_ms)
    fp = sum(1 for r in rows if r.get("flag"))
    return fp, len(rows)


def render_markdown(
    results: list[ScenarioResult], fp: int, n_normal: int, png: str | None
) -> str:
    lines: list[str] = []
    lines.append("# Đánh giá phát hiện bất thường (A-06)\n")
    lines.append("## Tỉ lệ phát hiện theo kịch bản tấn công\n")
    lines.append("| Kịch bản | Số cửa sổ | Cửa sổ bị gắn cờ | Tỉ lệ cửa sổ | Phát hiện |")
    lines.append("|---|---|---|---|---|")
    for r in results:
        lines.append(
            f"| {r.scenario} | {r.n_windows} | {r.n_flagged} | {r.rate*100:.1f}% | "
            f"{'CÓ' if r.detected else 'KHÔNG'} |"
        )
    n_detected = sum(1 for r in results if r.detected)
    lines.append("")
    lines.append(f"- Kịch bản phát hiện được: **{n_detected}/{len(results)}**")
    lines.append("")
    lines.append("## Báo nhầm trong khoảng chạy bình thường\n")
    rate = (fp / n_normal * 100) if n_normal else 0.0
    lines.append(f"- Số cửa sổ bình thường: {n_normal}")
    lines.append(f"- Số báo nhầm (false positive): **{fp}** ({rate:.2f}%)")
    lines.append("")
    if png:
        lines.append(f"![Điểm bất thường theo thời gian]({Path(png).name})")
    lines.append("")
    return "\n".join(lines)


def plot_scores(
    store: AnomalyStore,
    attacks: list[dict[str, Any]],
    from_ms: int,
    to_ms: int,
    png_path: str,
) -> bool:
    """Vẽ điểm theo thời gian, tô vùng tấn công. Trả False nếu thiếu matplotlib."""
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception:  # noqa: BLE001
        log.warning("Thiếu matplotlib — bỏ qua biểu đồ PNG")
        return False

    # Lấy toàn bộ dải thời gian (bình thường + tấn công)
    lo = min([from_ms] + [int(a["start_ms"]) for a in attacks]) if attacks else from_ms
    hi = max([to_ms] + [int(a["end_ms"]) + GRACE_MS for a in attacks]) if attacks else to_ms
    rows = store.fetch(lo, hi)
    xs = [r["ts_ms"] / 1000.0 for r in rows if r.get("score") is not None]
    ys = [r["score"] for r in rows if r.get("score") is not None]

    fig, ax = plt.subplots(figsize=(11, 4))
    if xs:
        ax.plot(xs, ys, lw=1.0, color="#1f77b4", label="anomaly_score")
    for a in attacks:
        ax.axvspan(
            int(a["start_ms"]) / 1000.0,
            (int(a["end_ms"]) + GRACE_MS) / 1000.0,
            color="red",
            alpha=0.15,
        )
    ax.set_xlabel("Thời gian (epoch giây)")
    ax.set_ylabel("anomaly_score")
    ax.set_title("Điểm bất thường theo thời gian (vùng đỏ = đang tấn công)")
    ax.legend(loc="upper right")
    fig.tight_layout()
    Path(png_path).parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(png_path, dpi=110)
    plt.close(fig)
    return True


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description="Đánh giá phát hiện bất thường (A-06)")
    ap.add_argument("--db", required=True)
    ap.add_argument("--attack-log", required=True)
    ap.add_argument("--normal-from", required=True)
    ap.add_argument("--normal-to", required=True)
    ap.add_argument("--out", default="docs/anomaly_eval.md")
    ap.add_argument("--png", default="docs/anomaly_scores.png")
    args = ap.parse_args(argv)

    store = AnomalyStore(args.db)
    try:
        attacks = load_attack_log(args.attack_log)
        results = evaluate_scenarios(store, attacks)
        from_ms = _parse_time_to_ms(args.normal_from)
        to_ms = _parse_time_to_ms(args.normal_to)
        fp, n_normal = count_false_positives(store, from_ms, to_ms)
        has_png = plot_scores(store, attacks, from_ms, to_ms, args.png)
        md = render_markdown(results, fp, n_normal, args.png if has_png else None)
    finally:
        store.close()

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(md, encoding="utf-8")
    log.info("Đã ghi báo cáo: %s", out)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
