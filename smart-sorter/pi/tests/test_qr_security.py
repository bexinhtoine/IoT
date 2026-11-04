"""S-03 / P-04 — Test QrVerifier cho mọi mã kết quả 4.1 (gồm replay, chữ ký sửa 1 ký tự, độ dài sai)."""
from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from sorter.interfaces import F_QR_BAD_SIGNATURE, F_QR_DUPLICATE, metrics
from sorter.qr_reader import QrReadResult
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
    QrVerifier,
    load_hmac_key,
    make_code,
    sign,
)
from sorter.storage import Storage

FIXTURES = Path(__file__).resolve().parent / "fixtures"
NOW = 1761631080


@pytest.fixture
def key() -> bytes:
    return load_hmac_key(FIXTURES / "qr_hmac.key")


@pytest.fixture
def storage():
    s = Storage(":memory:")
    yield s
    s.close()


@pytest.fixture
def verifier(key, storage) -> QrVerifier:
    return QrVerifier(key, storage, max_age_days=30, future_skew_s=300)


def test_sign_format(key):
    s = sign(key, "PKG000123", "A", NOW)
    assert len(s) == 43 and "=" not in s
    code = make_code(key, "PKG000123", "A", NOW)
    assert code == f"PKG000123|A|{NOW}|{s}"


def test_sign_known_vector():
    # HMAC-SHA256 với khóa 32 byte 0x00; đối chiếu với hashlib độc lập
    import base64
    import hashlib
    import hmac

    k = bytes(32)
    expect = base64.urlsafe_b64encode(hmac.new(k, b"PKG000123|A|1761631080", hashlib.sha256).digest()).rstrip(b"=").decode()
    assert sign(k, "PKG000123", "A", 1761631080) == expect


def test_valid(verifier, key):
    r = verifier.verify(make_code(key, "PKG000123", "A", NOW), now=NOW + 10)
    assert r.status == VALID and r.pkg_id == "PKG000123" and r.warehouse == "A" and r.ts == NOW


def test_no_qr(verifier):
    assert verifier.verify(None).status == NO_QR
    assert verifier.verify_read(QrReadResult("NO_QR", None)).status == NO_QR


def test_multi_qr(verifier):
    assert verifier.verify_read(QrReadResult("MULTI_QR", None)).status == MULTI_QR


@pytest.mark.parametrize(
    "text",
    [
        "",
        "hello",
        "PKG00123|A|1761631080|" + "a" * 43,          # 5 chữ số
        "PKG0001234|A|1761631080|" + "a" * 43,        # 7 chữ số
        "pkg000123|A|1761631080|" + "a" * 43,         # chữ thường
        "PKG000123|A|abc|" + "a" * 43,                # ts không phải số
        "PKG000123|A|1761631080",                     # thiếu chữ ký
        "PKG000123|A|1761631080|" + "a" * 42,         # chữ ký ngắn
        "PKG000123|A|1761631080|" + "a" * 44,         # chữ ký dài
        "PKG000123|A|1761631080|" + "a" * 42 + "=",   # có padding
        "PKG000123|A|1761631080|" + "a" * 42 + "+",   # base64 thường, không phải url-safe
        "PKG000123|A|1761631080|" + "a" * 43 + "|x",  # thừa trường
    ],
)
def test_bad_format(verifier, text):
    assert verifier.verify(text, now=NOW).status == BAD_FORMAT


def test_bad_warehouse(verifier, key):
    code = make_code(key, "PKG000123", "C", NOW)  # ký đúng nhưng kho C
    assert verifier.verify(code, now=NOW).status == BAD_WAREHOUSE


def test_bad_signature_one_char(verifier, key):
    code = make_code(key, "PKG000123", "A", NOW)
    head, sig = code.rsplit("|", 1)
    pos = 20
    ch = "A" if sig[pos] != "A" else "B"
    before = metrics.snapshot()[F_QR_BAD_SIGNATURE]
    assert verifier.verify(f"{head}|{sig[:pos]}{ch}{sig[pos + 1:]}", now=NOW).status == BAD_SIGNATURE
    assert metrics.snapshot()[F_QR_BAD_SIGNATURE] == before + 1


def test_forged_helper(verifier, key):
    assert verifier.verify(make_code(key, "PKG000123", "A", NOW, forge=True), now=NOW).status == BAD_SIGNATURE


def test_tampered_fields_break_signature(verifier, key):
    code = make_code(key, "PKG000123", "A", NOW)
    sig = code.rsplit("|", 1)[1]
    assert verifier.verify(f"PKG000124|A|{NOW}|{sig}", now=NOW).status == BAD_SIGNATURE  # đổi mã kiện
    assert verifier.verify(f"PKG000123|B|{NOW}|{sig}", now=NOW).status == BAD_SIGNATURE  # đổi kho
    assert verifier.verify(f"PKG000123|A|{NOW + 1}|{sig}", now=NOW).status == BAD_SIGNATURE  # đổi thời gian


def test_wrong_key(verifier, key):
    other = bytes(b ^ 0xFF for b in key)
    assert verifier.verify(make_code(other, "PKG000123", "A", NOW), now=NOW).status == BAD_SIGNATURE


def test_future_timestamp(verifier, key):
    assert verifier.verify(make_code(key, "PKG000123", "A", NOW + 301), now=NOW).status == FUTURE_TIMESTAMP
    assert verifier.verify(make_code(key, "PKG000123", "A", NOW + 299), now=NOW).status == VALID


def test_expired(verifier, key):
    assert verifier.verify(make_code(key, "PKG000123", "A", NOW - 31 * 86400), now=NOW).status == EXPIRED
    assert verifier.verify(make_code(key, "PKG000124", "A", NOW - 29 * 86400), now=NOW).status == VALID


def test_duplicate_replay(verifier, key, storage):
    code = make_code(key, "PKG000777", "B", NOW)
    before = metrics.snapshot()[F_QR_DUPLICATE]
    assert verifier.verify(code, now=NOW).status == VALID
    assert verifier.verify(code, now=NOW + 5).status == DUPLICATE
    assert metrics.snapshot()[F_QR_DUPLICATE] == before + 1
    # cùng pkg_id nhưng ký lại với ts khác vẫn là trùng (chống replay theo mã kiện)
    assert verifier.verify(make_code(key, "PKG000777", "B", NOW + 1), now=NOW + 5).status == DUPLICATE
    assert storage.count_used_codes() == 1
    storage.clear_used_codes()
    assert verifier.verify(code, now=NOW).status == VALID


def test_invalid_codes_do_not_consume_pkg_id(verifier, key, storage):
    # chữ ký sai không được ghi vào used_codes (kẻ tấn công không thể "đốt" mã thật)
    assert verifier.verify(make_code(key, "PKG000900", "A", NOW, forge=True), now=NOW).status == BAD_SIGNATURE
    assert verifier.verify(make_code(key, "PKG000900", "A", NOW - 40 * 86400), now=NOW).status == EXPIRED
    assert storage.count_used_codes() == 0
    assert verifier.verify(make_code(key, "PKG000900", "A", NOW), now=NOW).status == VALID


def test_check_order_signature_before_time(verifier, key):
    # Chữ ký sai + hết hạn -> BAD_SIGNATURE (ưu tiên kiểm tra chữ ký trước thời gian)
    code = make_code(key, "PKG000123", "A", NOW - 100 * 86400, forge=True)
    assert verifier.verify(code, now=NOW).status == BAD_SIGNATURE


def test_load_hmac_key_variants(tmp_path, key):
    raw = tmp_path / "k.bin"
    raw.write_bytes(key)
    assert load_hmac_key(raw) == key
    hexf = tmp_path / "k.hex"
    hexf.write_text(key.hex() + "\n")
    assert load_hmac_key(hexf) == key
    bad = tmp_path / "bad.key"
    bad.write_bytes(b"short")
    with pytest.raises(ValueError):
        load_hmac_key(bad)
    with pytest.raises(FileNotFoundError):
        load_hmac_key(tmp_path / "missing.key")


def test_verifier_rejects_short_key(storage):
    with pytest.raises(ValueError):
        QrVerifier(b"short", storage)


def test_repr_does_not_leak_key(verifier, key):
    assert key.hex() not in repr(verifier)
