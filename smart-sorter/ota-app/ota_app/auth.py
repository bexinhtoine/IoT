"""O-01 — Đăng nhập app Secure OTA.

- Tài khoản lưu SQLite (bảng ``users``), mật khẩu băm bcrypt cost 12.
- Khóa 5 phút sau 5 lần sai; trạng thái khóa lưu trong DB nên còn hiệu lực sau khi
  khởi động lại app.
- TOTP tùy chọn (pyotp): sinh secret + provisioning URI + ảnh QR để quét.
- ``Session`` tự hết hạn sau 15 phút không thao tác.
- Vai trò: ``release_manager`` (được ký/phát hành) và ``viewer`` (chỉ xem).

Mọi thao tác thời gian đi qua ``clock`` để test có thể monkeypatch.
"""
from __future__ import annotations

import io
import logging
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Literal, Optional

import bcrypt

log = logging.getLogger(__name__)

Role = Literal["release_manager", "viewer"]
ROLES: tuple[str, ...] = ("release_manager", "viewer")

BCRYPT_ROUNDS = 12
MIN_PASSWORD_LEN = 10
MAX_FAILED = 5
LOCK_SECONDS = 5 * 60
SESSION_TIMEOUT_SECONDS = 15 * 60
TOTP_ISSUER = "SmartSorter-OTA"

LoginError = Literal["WRONG_PASSWORD", "LOCKED", "TOTP_REQUIRED", "TOTP_INVALID"]


class AuthError(Exception):
    """Lỗi nghiệp vụ auth (mật khẩu yếu, trùng tên, vai trò sai...)."""


@dataclass
class User:
    username: str
    role: str
    totp_enabled: bool
    failed_count: int
    locked_until: float


@dataclass
class Session:
    """Phiên đăng nhập; hết hạn sau SESSION_TIMEOUT_SECONDS không thao tác."""

    username: str
    role: str
    started_at: float
    last_activity: float
    clock: Callable[[], float] = field(default=time.time, repr=False, compare=False)

    @property
    def can_release(self) -> bool:
        return self.role == "release_manager"

    def touch(self) -> None:
        """Gọi mỗi khi người dùng thao tác để gia hạn phiên."""
        self.last_activity = self.clock()

    def idle_seconds(self) -> float:
        return self.clock() - self.last_activity

    def is_expired(self, timeout: float = SESSION_TIMEOUT_SECONDS) -> bool:
        return self.idle_seconds() >= timeout


@dataclass
class LoginResult:
    ok: bool
    error: Optional[LoginError] = None
    session: Optional[Session] = None
    locked_remaining: float = 0.0  # giây còn bị khóa (khi error == LOCKED)


@dataclass
class TotpSetup:
    """Dữ liệu để người dùng quét bằng app xác thực; secret chỉ lưu DB sau khi xác nhận."""

    secret: str
    provisioning_uri: str
    qr_png: Optional[bytes]  # None nếu thiếu thư viện qrcode


def hash_password(password: str, rounds: int = BCRYPT_ROUNDS) -> str:
    """Băm bcrypt (cost 12 mặc định). bcrypt giới hạn 72 byte, cắt trước cho rõ ràng."""
    return bcrypt.hashpw(password.encode("utf-8")[:72], bcrypt.gensalt(rounds=rounds)).decode("ascii")


def check_password(password: str, pw_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8")[:72], pw_hash.encode("ascii"))
    except ValueError:
        return False


def validate_password(password: str) -> None:
    """Chính sách mật khẩu: tối thiểu 10 ký tự, không toàn khoảng trắng."""
    if len(password) < MIN_PASSWORD_LEN:
        raise AuthError(f"Mật khẩu phải có ít nhất {MIN_PASSWORD_LEN} ký tự")
    if not password.strip():
        raise AuthError("Mật khẩu không được toàn khoảng trắng")


def make_qr_png(data: str) -> Optional[bytes]:
    """Sinh ảnh QR PNG (bytes) cho provisioning URI; trả None nếu thiếu qrcode/Pillow."""
    try:
        import qrcode  # type: ignore
    except ImportError:  # pragma: no cover
        return None
    try:
        img = qrcode.make(data)
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()
    except Exception as exc:  # pragma: no cover - phụ thuộc Pillow
        log.warning("Không tạo được ảnh QR: %s", exc)
        return None


class AuthStore:
    """Kho tài khoản trên SQLite. Thread-safe ở mức đơn giản (một lock)."""

    SCHEMA = """
    CREATE TABLE IF NOT EXISTS users (
        username     TEXT PRIMARY KEY,
        pw_hash      TEXT NOT NULL,
        role         TEXT NOT NULL CHECK(role IN ('release_manager','viewer')),
        totp_secret  TEXT,
        failed_count INTEGER NOT NULL DEFAULT 0,
        locked_until REAL NOT NULL DEFAULT 0,
        created_at   REAL NOT NULL
    );
    """

    def __init__(
        self,
        db_path: Path | str,
        clock: Callable[[], float] = time.time,
        bcrypt_rounds: int = BCRYPT_ROUNDS,
    ) -> None:
        self.db_path = Path(db_path)
        self.clock = clock
        self.bcrypt_rounds = bcrypt_rounds
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._conn:
            self._conn.executescript(self.SCHEMA)

    def close(self) -> None:
        self._conn.close()

    # ---------------- tài khoản ----------------
    def has_users(self) -> bool:
        """False ở lần chạy đầu -> UI bắt tạo tài khoản quản trị release_manager."""
        row = self._conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()
        return int(row["n"]) > 0

    def create_user(self, username: str, password: str, role: str) -> User:
        username = username.strip()
        if not username or len(username) > 64:
            raise AuthError("Tên đăng nhập không hợp lệ (1-64 ký tự)")
        if role not in ROLES:
            raise AuthError(f"Vai trò phải là một trong {ROLES}")
        validate_password(password)
        pw_hash = hash_password(password, self.bcrypt_rounds)
        with self._lock, self._conn:
            try:
                self._conn.execute(
                    "INSERT INTO users(username, pw_hash, role, created_at) VALUES (?,?,?,?)",
                    (username, pw_hash, role, self.clock()),
                )
            except sqlite3.IntegrityError as exc:
                raise AuthError("Tên đăng nhập đã tồn tại") from exc
        log.info("Tạo tài khoản %s (role=%s)", username, role)
        return User(username, role, False, 0, 0.0)

    def create_admin_first_run(self, username: str, password: str) -> User:
        """Lần chạy đầu: bắt buộc tạo tài khoản quản trị vai trò release_manager."""
        if self.has_users():
            raise AuthError("Đã có tài khoản; không thể tạo quản trị lần đầu nữa")
        return self.create_user(username, password, "release_manager")

    def get_user(self, username: str) -> Optional[User]:
        row = self._conn.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
        if row is None:
            return None
        return User(
            username=row["username"],
            role=row["role"],
            totp_enabled=row["totp_secret"] is not None,
            failed_count=int(row["failed_count"]),
            locked_until=float(row["locked_until"]),
        )

    def list_users(self) -> list[User]:
        rows = self._conn.execute("SELECT username FROM users ORDER BY username").fetchall()
        return [u for u in (self.get_user(r["username"]) for r in rows) if u]

    def change_password(self, username: str, old_password: str, new_password: str) -> None:
        row = self._conn.execute("SELECT pw_hash FROM users WHERE username=?", (username,)).fetchone()
        if row is None or not check_password(old_password, row["pw_hash"]):
            raise AuthError("Mật khẩu cũ không đúng")
        validate_password(new_password)
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE users SET pw_hash=? WHERE username=?",
                (hash_password(new_password, self.bcrypt_rounds), username),
            )

    # ---------------- TOTP ----------------
    def begin_totp_setup(self, username: str) -> TotpSetup:
        """Sinh secret mới (chưa lưu). UI hiện QR; người dùng nhập mã để xác nhận."""
        import pyotp

        secret = pyotp.random_base32()
        uri = pyotp.TOTP(secret).provisioning_uri(name=username, issuer_name=TOTP_ISSUER)
        return TotpSetup(secret=secret, provisioning_uri=uri, qr_png=make_qr_png(uri))

    def confirm_totp_setup(self, username: str, secret: str, code: str) -> bool:
        """Lưu secret vào DB nếu mã nhập đúng với secret vừa sinh."""
        import pyotp

        if not pyotp.TOTP(secret).verify(code.strip(), valid_window=1):
            return False
        with self._lock, self._conn:
            self._conn.execute("UPDATE users SET totp_secret=? WHERE username=?", (secret, username))
        log.info("Bật TOTP cho %s", username)
        return True

    def disable_totp(self, username: str) -> None:
        with self._lock, self._conn:
            self._conn.execute("UPDATE users SET totp_secret=NULL WHERE username=?", (username,))
        log.info("Tắt TOTP cho %s", username)

    # ---------------- đăng nhập ----------------
    def _record_failure(self, username: str, failed_count: int) -> None:
        failed_count += 1
        locked_until = 0.0
        if failed_count >= MAX_FAILED:
            locked_until = self.clock() + LOCK_SECONDS
            failed_count = 0  # đếm lại sau khi hết khóa
            log.warning("Tài khoản %s bị khóa %d giây sau %d lần sai", username, LOCK_SECONDS, MAX_FAILED)
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE users SET failed_count=?, locked_until=? WHERE username=?",
                (failed_count, locked_until, username),
            )

    def _reset_failures(self, username: str) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE users SET failed_count=0, locked_until=0 WHERE username=?", (username,)
            )

    def login(self, username: str, password: str, totp_code: Optional[str] = None) -> LoginResult:
        """Đăng nhập. Không phân biệt 'sai user' và 'sai mật khẩu' để tránh dò tên."""
        import pyotp

        now = self.clock()
        row = self._conn.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
        if row is None:
            # Vẫn tốn thời gian tương đương bcrypt để không lộ user tồn tại hay không
            check_password(password, hash_password("dummy-password-0", 4))
            log.warning("Đăng nhập thất bại: user không tồn tại")
            return LoginResult(False, "WRONG_PASSWORD")

        locked_until = float(row["locked_until"])
        if locked_until > now:
            remaining = locked_until - now
            log.warning("Tài khoản %s đang bị khóa, còn %.0f s", username, remaining)
            return LoginResult(False, "LOCKED", locked_remaining=remaining)

        if not check_password(password, row["pw_hash"]):
            self._record_failure(username, int(row["failed_count"]))
            log.warning("Đăng nhập thất bại: sai mật khẩu cho %s", username)
            return LoginResult(False, "WRONG_PASSWORD")

        secret = row["totp_secret"]
        if secret:
            if not totp_code:
                return LoginResult(False, "TOTP_REQUIRED")
            if not pyotp.TOTP(secret).verify(totp_code.strip(), valid_window=1):
                self._record_failure(username, int(row["failed_count"]))
                log.warning("Đăng nhập thất bại: TOTP sai cho %s", username)
                return LoginResult(False, "TOTP_INVALID")

        self._reset_failures(username)
        session = Session(
            username=username, role=row["role"], started_at=now, last_activity=now, clock=self.clock
        )
        log.info("Đăng nhập thành công: %s (role=%s)", username, row["role"])
        return LoginResult(True, None, session)
