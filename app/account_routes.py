"""Your account (change password) and, for admins, the profiles page (create / reset / delete)."""
import logging

from fastapi import APIRouter, Form
from fastapi.requests import Request
from fastapi.responses import RedirectResponse

import accounts
import auth
from books_routes import _redirect
from i18n import _
from web import templates

log = logging.getLogger("accounts")
router = APIRouter()


def _account_page(request: Request, error: str = "", notice: str = "", status: int = 200):
    return templates.TemplateResponse(request, "account.html", {
        "tab": "account", "user": accounts.current(), "error": error, "notice": notice,
        "min_len": accounts.MIN_PASSWORD,
    }, status_code=status)


@router.get("/account")
def account(request: Request, notice: str = ""):
    return _account_page(request, notice=notice)


@router.get("/account/password")
def account_password(request: Request):
    return _account_page(request)


@router.post("/account/password")
def change_password(request: Request, current: str = Form(""), new: str = Form(""), confirm: str = Form("")):
    user = accounts.current()
    # Someone on a temporary password just logged in with it: no need to type it again.
    if not user["must_change"] and not accounts.verify_password(current, user["pw_hash"]):
        return _account_page(request, _("Your current password is wrong."), status=400)
    if len(new) < accounts.MIN_PASSWORD:
        return _account_page(request, _("The new password needs at least {n} characters.", n=accounts.MIN_PASSWORD),
                             status=400)
    if new != confirm:
        return _account_page(request, _("The two new passwords don't match."), status=400)
    if accounts.verify_password(new, user["pw_hash"]):
        return _account_page(request, _("Choose a password different from the current one."), status=400)
    accounts.set_password(user["id"], new)
    # The session is tied to the password hash, so hand out a fresh cookie for the new one.
    resp = _redirect("/", _("Password changed."))
    resp.set_cookie(auth.COOKIE_NAME, auth.make_session_token(accounts.get_user(user["id"])),
                    max_age=auth.settings.session_days * 86400, httponly=True, samesite="lax",
                    secure=auth.is_https(request))
    return resp


# --- admin: profiles ------------------------------------------------------------------------------

def _users_page(request: Request, notice: str = "", error: str = "", issued: dict | None = None, status: int = 200):
    rows = [{"u": u, "size": accounts.library_size(u["id"])} for u in accounts.list_users()]
    return templates.TemplateResponse(request, "admin_users.html", {
        "tab": "users", "rows": rows, "notice": notice, "error": error, "issued": issued,
        "me": accounts.current(),
    }, status_code=status)


def _admin_only():
    user = accounts.current()
    return None if user is not None and user["is_admin"] else RedirectResponse("/", status_code=303)


@router.get("/admin/users")
def users_page(request: Request, notice: str = ""):
    return _admin_only() or _users_page(request, notice=notice)


@router.post("/admin/users")
def create_user(request: Request, username: str = Form(""), is_admin: str = Form("")):
    if (deny := _admin_only()) is not None:
        return deny
    try:
        _new_id, password = accounts.create_user(username, is_admin=bool(is_admin))
    except ValueError as exc:
        msg = (_("That username is already taken.") if "taken" in str(exc)
               else _("Usernames are 2 to 32 letters, digits, dots, dashes or underscores."))
        return _users_page(request, error=msg, status=400)
    log.info("profile %r created", username)
    # Shown once, on this response only: it's never stored in clear or put in a URL.
    return _users_page(request, issued={"username": username.strip(), "password": password, "new": True})


@router.post("/admin/users/{user_id}/reset")
def reset_user(request: Request, user_id: int):
    if (deny := _admin_only()) is not None:
        return deny
    user = accounts.get_user(user_id)
    if user is None or user_id == accounts.current()["id"]:
        return _users_page(request, error=_("Use your Account page to change your own password."), status=400)
    password = accounts.reset_password(user_id)
    log.info("password reset for %r", user["username"])
    return _users_page(request, issued={"username": user["username"], "password": password, "new": False})


@router.post("/admin/users/{user_id}/delete")
def delete_user(request: Request, user_id: int):
    if (deny := _admin_only()) is not None:
        return deny
    user = accounts.get_user(user_id)
    if user is None or user_id == 1 or user_id == accounts.current()["id"]:
        return _users_page(request, error=_("That profile can't be deleted."), status=400)
    accounts.delete_user(user_id)
    log.info("profile %r deleted", user["username"])
    return RedirectResponse("/admin/users?notice=" + _("Profile {name} and its library deleted.", name=user["username"]),
                            status_code=303)
