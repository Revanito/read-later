"""Login: username + password (accounts.py), then an HMAC-signed, expiring session cookie.

The cookie is `<user id>.<expiry>.<hmac>`; the signature also covers a slice of the user's password
hash, so changing or resetting a password logs out that user's other sessions. Anything that isn't
/login, /static or /lang redirects to the login page until the cookie checks out.
"""
import hashlib
import hmac
import time
from collections import defaultdict, deque

from fastapi.requests import Request

import accounts
from config import settings

COOKIE_NAME = "rl_session"

_PUBLIC_PREFIXES = ("/login", "/static/", "/lang/")

# Brute-force brake: after this many failed logins from one IP inside the window, that IP is
# refused until the oldest failure ages out.
_MAX_FAILURES = 5
_FAILURE_WINDOW = 15 * 60
_failures: dict[str, deque[float]] = defaultdict(deque)


def _secret() -> bytes:
    if settings.secret_key:
        return settings.secret_key.encode("utf-8")
    return hashlib.sha256(f"read-later-session:{settings.app_password}".encode("utf-8")).digest()


def _sign(payload: str) -> str:
    return hmac.new(_secret(), payload.encode("utf-8"), hashlib.sha256).hexdigest()


def _payload(user, expiry: str) -> str:
    return f"v2:{user['id']}:{expiry}:{user['pw_hash'][-16:]}"


def make_session_token(user) -> str:
    expiry = str(int(time.time()) + settings.session_days * 86400)
    return f"{user['id']}.{expiry}.{_sign(_payload(user, expiry))}"


def user_from_token(token: str | None):
    """The logged-in user for a session cookie, or None (expired, tampered, password changed, deleted)."""
    parts = (token or "").split(".")
    if len(parts) != 3 or not parts[0].isdigit() or not parts[1].isdigit() or int(parts[1]) < time.time():
        return None
    user = accounts.get_user(int(parts[0]))
    if user is None or not hmac.compare_digest(parts[2], _sign(_payload(user, parts[1]))):
        return None
    return user


def is_valid_token(token: str | None) -> bool:
    return user_from_token(token) is not None


def client_ip(request: Request) -> str:
    # nginx sets X-Real-IP (see deploy/nginx-reverse-proxy.conf); fall back to the socket peer for
    # direct LAN access on :8086.
    return request.headers.get("x-real-ip") or (request.client.host if request.client else "unknown")


def is_locked_out(ip: str) -> bool:
    q = _failures[ip]
    cutoff = time.time() - _FAILURE_WINDOW
    while q and q[0] < cutoff:
        q.popleft()
    return len(q) >= _MAX_FAILURES


def record_failure(ip: str) -> None:
    _failures[ip].append(time.time())


def clear_failures(ip: str) -> None:
    _failures.pop(ip, None)


def is_public_path(path: str) -> bool:
    return path.startswith(_PUBLIC_PREFIXES)


def is_https(request: Request) -> bool:
    return request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"


def safe_next(target: str | None) -> str:
    # Only allow same-site relative paths, so /login?next=https://evil.example can't bounce you away.
    if target and target.startswith("/") and not target.startswith("//"):
        return target
    return "/"
