"""P-06 — Hàm quyết định thuần theo bảng 1.2.

| QR                      | AI                 | Mã quyết định    | Khay   |
| Hợp lệ, kho A           | OK                 | SORT_A           | A      |
| Hợp lệ, kho B           | OK                 | SORT_B           | B      |
| Hợp lệ                  | DEFECT / UNCERTAIN | REJECT_DEFECT    | Loại   |
| Không hợp lệ (mọi mã)   | — (không chạy AI)  | REJECT_SECURITY  | Loại   |
| Lỗi hệ thống / timeout  | —                  | REJECT_FAILSAFE  | Loại   |

Fail-safe: mọi trường hợp không chắc chắn -> REJECT, không bao giờ A/B.
"""
from __future__ import annotations

from dataclasses import dataclass

from .interfaces import LABEL_OK, ClassResult
from .protocol_gen import SortReason, SortTarget
from .qr_security import VALID, QrResult

SORT_A = "SORT_A"
SORT_B = "SORT_B"
REJECT_DEFECT = "REJECT_DEFECT"
REJECT_SECURITY = "REJECT_SECURITY"
REJECT_FAILSAFE = "REJECT_FAILSAFE"
DECISION_CODES: tuple[str, ...] = (SORT_A, SORT_B, REJECT_DEFECT, REJECT_SECURITY, REJECT_FAILSAFE)


@dataclass(frozen=True)
class Decision:
    target: SortTarget
    code: str
    reason: SortReason
    detail: str = ""

    @property
    def is_reject(self) -> bool:
        return self.target == SortTarget.REJECT


FAILSAFE_DECISION = Decision(SortTarget.REJECT, REJECT_FAILSAFE, SortReason.FAILSAFE, "system error")


def decide(qr: QrResult | None, ai: ClassResult | None, failsafe: bool = False) -> Decision:
    """Quyết định thuần (không I/O). failsafe=True ép REJECT_FAILSAFE (lỗi hệ thống/timeout)."""
    if failsafe:
        return FAILSAFE_DECISION
    # Hàng 4: QR không hợp lệ -> loại ngay, không cần AI
    if qr is None:
        return Decision(SortTarget.REJECT, REJECT_SECURITY, SortReason.SECURITY, "NO_QR")
    if qr.status != VALID:
        return Decision(SortTarget.REJECT, REJECT_SECURITY, SortReason.SECURITY, qr.status)
    # Hàng 5: QR hợp lệ nhưng không có kết quả AI = lỗi hệ thống
    if ai is None:
        return Decision(SortTarget.REJECT, REJECT_FAILSAFE, SortReason.FAILSAFE, "no AI result")
    # Hàng 3: lỗi bao bì hoặc dưới ngưỡng tin cậy
    if ai.label != LABEL_OK:
        detail = ai.label
        top = ai.top_defect_type
        if top:
            detail = f"{ai.label}:{top}"
        return Decision(SortTarget.REJECT, REJECT_DEFECT, SortReason.DEFECT, detail)
    # Hàng 1, 2
    if qr.warehouse == "A":
        return Decision(SortTarget.A, SORT_A, SortReason.OK, "A")
    if qr.warehouse == "B":
        return Decision(SortTarget.B, SORT_B, SortReason.OK, "B")
    # Không bao giờ tới đây nếu verifier đúng; vẫn fail-safe
    return Decision(SortTarget.REJECT, REJECT_SECURITY, SortReason.SECURITY, f"BAD_WAREHOUSE:{qr.warehouse}")
