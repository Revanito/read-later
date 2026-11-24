"""Single-password auth: a login form that sets an HMAC-signed, expiring session cookie.

No user table and no extra dependency - the cookie is just `<expiry>.<hmac(expiry)>`. Anything that
isn't /login or /static redirects to the login page until that cookie checks out.
"""
import hashlib
import hmac
import time
from collections import defaultdict, deque

from fastapi.requests import Request

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


def make_session_token() -> str:
    expiry = str(int(time.time()) + settings.session_days * 86400)
    return f"{expiry}.{_sign('v1:' + expiry)}"


def is_valid_token(token: str | None) -> bool:
    if not token or "." not in token:
        return False
    expiry, sig = token.split(".", 1)
    if not expiry.isdigit() or int(expiry) < time.time():
        return False
    return hmac.compare_digest(sig, _sign("v1:" + expiry))


def check_password(candidate: str) -> bool:
    return hmac.compare_digest(candidate.encode("utf-8"), settings.app_password.encode("utf-8"))


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
