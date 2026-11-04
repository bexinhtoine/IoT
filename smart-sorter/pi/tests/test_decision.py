"""P-06 — Test hàm decide() cho mọi hàng của bảng 1.2 + các trường hợp biên."""
from __future__ import annotations

import pytest

from sorter.decision import REJECT_DEFECT, REJECT_FAILSAFE, REJECT_SECURITY, SORT_A, SORT_B, decide
from sorter.interfaces import LABEL_DEFECT, LABEL_OK, LABEL_UNCERTAIN, ClassResult
from sorter.protocol_gen import SortReason, SortTarget
from sorter.qr_security import (
    BAD_FORMAT,
    BAD_SIGNATURE,
    BAD_WAREHOUSE,
    DUPLICATE,
    EXPIRED,
    FUTURE_TIMESTAMP,
    MULTI_QR,
    NO_QR,
    VALID,
    QrResult,
)


def qr_valid(wh: str) -> QrResult:
    return QrResult(VALID, "PKG000001", wh, 1761631080)


def ai(label: str, conf: float = 0.9, types: dict | None = None) -> ClassResult:
    return ClassResult(label=label, confidence=conf, defect_types=types or {})


def test_row1_valid_A_ok():
    d = decide(qr_valid("A"), ai(LABEL_OK))
    assert (d.target, d.code, d.reason) == (SortTarget.A, SORT_A, SortReason.OK)


def test_row2_valid_B_ok():
    d = decide(qr_valid("B"), ai(LABEL_OK))
    assert (d.target, d.code, d.reason) == (SortTarget.B, SORT_B, SortReason.OK)


def test_row3_valid_defect():
    d = decide(qr_valid("A"), ai(LABEL_DEFECT, 0.95, {"tear": 0.9, "dent": 0.1}))
    assert (d.target, d.code, d.reason) == (SortTarget.REJECT, REJECT_DEFECT, SortReason.DEFECT)
    assert "tear" in d.detail


def test_row3_valid_uncertain():
    d = decide(qr_valid("B"), ai(LABEL_UNCERTAIN, 0.5))
    assert (d.target, d.code, d.reason) == (SortTarget.REJECT, REJECT_DEFECT, SortReason.DEFECT)


@pytest.mark.parametrize("status", [NO_QR, MULTI_QR, BAD_FORMAT, BAD_WAREHOUSE, BAD_SIGNATURE, FUTURE_TIMESTAMP, EXPIRED, DUPLICATE])
def test_row4_invalid_qr_rejects_security_without_ai(status: str):
    # AI có trả OK cũng không được vào A/B
    d = decide(QrResult(status, "PKG000001", "A", 1), ai(LABEL_OK, 0.99))
    assert (d.target, d.code, d.reason) == (SortTarget.REJECT, REJECT_SECURITY, SortReason.SECURITY)
    assert d.detail == status
    d2 = decide(QrResult(status), None)
    assert d2.code == REJECT_SECURITY


def test_row5_failsafe_flag():
    d = decide(qr_valid("A"), ai(LABEL_OK), failsafe=True)
    assert (d.target, d.code, d.reason) == (SortTarget.REJECT, REJECT_FAILSAFE, SortReason.FAILSAFE)


def test_row5_ai_none_after_valid_qr_is_failsafe():
    d = decide(qr_valid("A"), None)
    assert (d.target, d.code, d.reason) == (SortTarget.REJECT, REJECT_FAILSAFE, SortReason.FAILSAFE)


def test_qr_none_is_security():
    d = decide(None, None)
    assert d.code == REJECT_SECURITY and d.target == SortTarget.REJECT


def test_unknown_warehouse_never_sorts():
    d = decide(QrResult(VALID, "PKG000001", "C", 1), ai(LABEL_OK))
    assert d.target == SortTarget.REJECT


def test_never_a_or_b_unless_valid_and_ok():
    for status in (NO_QR, BAD_SIGNATURE, DUPLICATE):
        for label in (LABEL_OK, LABEL_DEFECT, LABEL_UNCERTAIN):
            assert decide(QrResult(status, "PKG000001", "A", 1), ai(label)).target == SortTarget.REJECT
    for label in (LABEL_DEFECT, LABEL_UNCERTAIN):
        assert decide(qr_valid("A"), ai(label)).target == SortTarget.REJECT
