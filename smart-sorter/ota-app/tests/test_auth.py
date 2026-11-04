"""Test O-01: tạo tài khoản, bcrypt, khóa 5 phút sau 5 lần sai, TOTP, phiên hết hạn."""
from __future__ import annotations

from pathlib import Path

import pytest

from ota_app.auth import (
    LOCK_SECONDS,
    MAX_FAILED,
    SESSION_TIMEOUT_SECONDS,
    AuthError,
    AuthStore,
    Session,
    hash_password,
)


class FakeClock:
    def __init__(self, t: float = 1_000_000.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t

    def advance(self, s: float) -> None:
        self.t += s


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def store(tmp_path: Path, clock: FakeClock) -> AuthStore:
    # bcrypt_rounds=4 để test nhanh; test riêng kiểm tra cost 12 mặc định
    s = AuthStore(tmp_path / "ota_app.db", clock=clock, bcrypt_rounds=4)
    yield s
    s.close()


def test_first_run_creates_release_manager(store: AuthStore):
    assert not store.has_users()
    with pytest.raises(AuthError):
        store.create_admin_first_run("admin", "short")  # < 10 ký tự
    u = store.create_admin_first_run("admin", "correct-horse-battery")
    assert u.role == "release_manager" and store.has_users()
    with pytest.raises(AuthError):
        store.create_admin_first_run("admin2", "correct-horse-battery")
    with pytest.raises(AuthError):
        store.create_user("admin", "correct-horse-battery", "viewer")  # trùng tên
    with pytest.raises(AuthError):
        store.create_user("x", "correct-horse-battery", "root")  # vai trò lạ


def test_bcrypt_cost_12_default():
    h = hash_password("correct-horse-battery")
    assert h.startswith("$2b$12$")


def test_login_ok_and_wrong(store: AuthStore):
    store.create_admin_first_run("admin", "correct-horse-battery")
    r = store.login("admin", "correct-horse-battery")
    assert r.ok and r.session is not None and r.session.can_release
    r = store.login("admin", "wrong-password-1")
    assert not r.ok and r.error == "WRONG_PASSWORD"
    r = store.login("nobody", "correct-horse-battery")
    assert not r.ok and r.error == "WRONG_PASSWORD"
    store.create_user("view", "viewer-password-1", "viewer")
    assert not store.login("view", "viewer-password-1").session.can_release


def test_lock_after_5_failures_and_unlock_after_5_minutes(tmp_path: Path, clock: FakeClock):
    db = tmp_path / "ota_app.db"
    store = AuthStore(db, clock=clock, bcrypt_rounds=4)
    store.create_admin_first_run("admin", "correct-horse-battery")
    for i in range(MAX_FAILED - 1):
        assert store.login("admin", "bad").error == "WRONG_PASSWORD"
    # lần sai thứ 5 vẫn trả WRONG_PASSWORD nhưng kích hoạt khóa
    assert store.login("admin", "bad").error == "WRONG_PASSWORD"
    r = store.login("admin", "correct-horse-battery")
    assert r.error == "LOCKED" and 0 < r.locked_remaining <= LOCK_SECONDS
    store.close()

    # Khởi động lại app (mở DB mới) -> vẫn khóa vì trạng thái lưu DB
    clock.advance(60)
    store2 = AuthStore(db, clock=clock, bcrypt_rounds=4)
    assert store2.login("admin", "correct-horse-battery").error == "LOCKED"
    # sau 5 phút -> mở khóa
    clock.advance(LOCK_SECONDS)
    r = store2.login("admin", "correct-horse-battery")
    assert r.ok
    assert store2.get_user("admin").failed_count == 0
    store2.close()


def test_totp_flow(store: AuthStore):
    pyotp = pytest.importorskip("pyotp")
    store.create_admin_first_run("admin", "correct-horse-battery")
    setup = store.begin_totp_setup("admin")
    assert setup.provisioning_uri.startswith("otpauth://totp/")
    assert "secret=" in setup.provisioning_uri
    assert store.confirm_totp_setup("admin", setup.secret, pyotp.TOTP(setup.secret).now())
    assert store.get_user("admin").totp_enabled
    assert store.login("admin", "correct-horse-battery").error == "TOTP_REQUIRED"
    assert store.login("admin", "correct-horse-battery", "123456").error in ("TOTP_INVALID", None)
    r = store.login("admin", "correct-horse-battery", pyotp.TOTP(setup.secret).now())
    assert r.ok
    store.disable_totp("admin")
    assert store.login("admin", "correct-horse-battery").ok


def test_session_expiry(clock: FakeClock):
    s = Session("admin", "release_manager", clock(), clock(), clock=clock)
    assert not s.is_expired()
    clock.advance(SESSION_TIMEOUT_SECONDS - 1)
    assert not s.is_expired()
    s.touch()
    clock.advance(SESSION_TIMEOUT_SECONDS - 1)
    assert not s.is_expired()
    clock.advance(2)
    assert s.is_expired()
