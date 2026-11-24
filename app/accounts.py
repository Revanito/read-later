"""Profiles: the admin creates accounts for friends, each with a library of their own.

Every profile has its own SQLite file: the admin keeps the original `DB_PATH` (so nothing moved when
profiles were added), everyone else gets `<data dir>/users/<id>.db`, created empty. A request is pointed
at the right file by `activate()` (see main.py's middleware), and all the existing code keeps calling
db.get_conn() as before: there is no user_id column anywhere, so one profile can't see another's books,
articles, follows or notifications by construction.

The `users` table itself lives in the admin's file (always opened directly, whatever profile is active).
Passwords are scrypt hashes. A new account (or a reset) gets a random password that the admin hands
over once; the friend must change it at first login.
"""
import hashlib
import hmac
import os
import re
import secrets
import sqlite3
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone

import db
from config import settings

_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE COLLATE NOCASE,
    pw_hash TEXT NOT NULL,
    is_admin INTEGER NOT NULL DEFAULT 0,
    must_change INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    last_login_at TEXT
);
"""
USERNAME_RE = re.compile(r"^[A-Za-z0-9_.-]{2,32}$")
MIN_PASSWORD = 8

_current: ContextVar[sqlite3.Row | None] = ContextVar("rl_user", default=None)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def _conn():
    """The admin's file, which holds the users table, regardless of the active profile."""
    conn = sqlite3.connect(settings.db_path)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


# --- passwords ------------------------------------------------------------------------------------

def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=2 ** 14, r=8, p=1, dklen=32)
    return f"scrypt${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, salt, digest = stored.split("$")
        got = hashlib.scrypt(password.encode("utf-8"), salt=bytes.fromhex(salt), n=2 ** 14, r=8, p=1, dklen=32)
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(got.hex(), digest)


def random_password() -> str:
    """Easy to read out or paste: 4 groups of 4, no look-alike characters."""
    alphabet = "abcdefghjkmnpqrstuvwxyz23456789"
    return "-".join("".join(secrets.choice(alphabet) for _ in range(4)) for _ in range(4))


# --- users ----------------------------------------------------------------------------------------

def init() -> None:
    """Create the users table; on the very first start, turn the existing single-password setup into
    the admin account (username ADMIN_USERNAME, password APP_PASSWORD, library = the existing DB)."""
    os.makedirs(os.path.dirname(settings.db_path) or ".", exist_ok=True)
    with _conn() as conn:
        conn.executescript(_SCHEMA)
        if conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0:
            conn.execute(
                "INSERT INTO users (id, username, pw_hash, is_admin, must_change, created_at) VALUES (1, ?, ?, 1, 0, ?)",
                (settings.admin_username, hash_password(settings.app_password), _now()))


def list_users() -> list[sqlite3.Row]:
    with _conn() as conn:
        return conn.execute("SELECT * FROM users ORDER BY id").fetchall()


def get_user(user_id: int) -> sqlite3.Row | None:
    with _conn() as conn:
        return conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()


def find_user(username: str) -> sqlite3.Row | None:
    with _conn() as conn:
        return conn.execute("SELECT * FROM users WHERE username = ?", (username.strip(),)).fetchone()


def authenticate(username: str, password: str) -> sqlite3.Row | None:
    """The user for these credentials, or None. A blank username means the admin account."""
    user = find_user(username) if username.strip() else get_user(1)
    if user is None:
        hash_password(password)  # same work either way, so timing doesn't reveal which usernames exist
        return None
    if not verify_password(password, user["pw_hash"]):
        return None
    with _conn() as conn:
        conn.execute("UPDATE users SET last_login_at = ? WHERE id = ?", (_now(), user["id"]))
    return user


def db_path_for(user_id: int) -> str:
    if user_id == 1:
        return settings.db_path
    return os.path.join(os.path.dirname(settings.db_path) or ".", "users", f"{user_id}.db")


def init_user_db(user_id: int) -> None:
    """Create / migrate a profile's own file (tables are created and upgraded exactly like the admin's)."""
    import books_db
    path = db_path_for(user_id)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    token = db.use_path(path)
    try:
        db.init_db()
        books_db.init_books_db()
    finally:
        db.reset_path(token)


def create_user(username: str, is_admin: bool = False) -> tuple[int, str]:
    """New profile with an empty library. Returns (id, the one-time password to hand over)."""
    username = username.strip()
    if not USERNAME_RE.match(username):
        raise ValueError("invalid username")
    if find_user(username) is not None:
        raise ValueError("username taken")
    password = random_password()
    with _conn() as conn:
        cur = conn.execute(
            "INSERT INTO users (username, pw_hash, is_admin, must_change, created_at) VALUES (?, ?, ?, 1, ?)",
            (username, hash_password(password), 1 if is_admin else 0, _now()))
        user_id = cur.lastrowid
    init_user_db(user_id)
    return user_id, password


def reset_password(user_id: int) -> str:
    password = random_password()
    with _conn() as conn:
        conn.execute("UPDATE users SET pw_hash = ?, must_change = 1 WHERE id = ?", (hash_password(password), user_id))
    return password


def set_password(user_id: int, password: str) -> None:
    with _conn() as conn:
        conn.execute("UPDATE users SET pw_hash = ?, must_change = 0 WHERE id = ?", (hash_password(password), user_id))


def delete_user(user_id: int) -> None:
    """Remove a profile and its whole library. The original admin (id 1) can't be deleted."""
    if user_id == 1:
        raise ValueError("the main admin account can't be deleted")
    with _conn() as conn:
        conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
    path = db_path_for(user_id)
    for suffix in ("", "-wal", "-shm", "-journal"):
        if os.path.exists(path + suffix):
            os.remove(path + suffix)


def library_size(user_id: int) -> tuple[int, int]:
    """(series, books) in a profile's library, for the admin page."""
    path = db_path_for(user_id)
    if not os.path.exists(path):
        return 0, 0
    conn = sqlite3.connect(path)
    try:
        s = conn.execute("SELECT COUNT(*) FROM series WHERE merged_into IS NULL").fetchone()[0]
        b = conn.execute("SELECT COUNT(*) FROM books").fetchone()[0]
    except sqlite3.Error:
        return 0, 0
    finally:
        conn.close()
    return s, b


# --- the active profile ---------------------------------------------------------------------------

def activate(user: sqlite3.Row) -> None:
    """Point this request (or background task) at `user`'s library."""
    _current.set(user)
    db.use_path(db_path_for(user["id"]))


def current() -> sqlite3.Row | None:
    return _current.get()
