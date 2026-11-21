import asyncio
import logging
from contextlib import asynccontextmanager
from urllib.parse import quote, urlparse

from fastapi import FastAPI, Form
from fastapi.requests import Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

import auth
import books_db
import db
import watcher
from books_routes import router as books_router
from extractor import ExtractionError, fetch_and_extract
from summarizer import SummarizeError, summarize
from web import templates

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("main")


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    books_db.init_books_db()
    watch_task = asyncio.create_task(watcher.loop())
    yield
    watch_task.cancel()


app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
app.mount("/static", StaticFiles(directory="static"), name="static")
app.include_router(books_router)


@app.middleware("http")
async def require_login(request: Request, call_next):
    if auth.is_public_path(request.url.path) or auth.is_valid_token(request.cookies.get(auth.COOKIE_NAME)):
        return await call_next(request)
    target = request.url.path + (f"?{request.url.query}" if request.url.query else "")
    # A POST from an expired session lands back on the page it came from after logging in, not on the
    # POST-only URL (which would 405 on a GET).
    if request.method != "GET":
        target = auth.safe_next(urlparse(request.headers.get("referer", "")).path or "/")
    return RedirectResponse(f"/login?next={quote(target, safe='')}", status_code=303)


# --- auth -----------------------------------------------------------------------------------------

@app.get("/login")
def login_page(request: Request, next: str = "/"):
    if auth.is_valid_token(request.cookies.get(auth.COOKIE_NAME)):
        return RedirectResponse(auth.safe_next(next), status_code=303)
    return templates.TemplateResponse(request, "login.html", {"next": auth.safe_next(next), "error": None})


@app.post("/login")
async def login(request: Request, password: str = Form(""), next: str = Form("/")):
    ip = auth.client_ip(request)
    target = auth.safe_next(next)
    if auth.is_locked_out(ip):
        log.warning("login refused for %s: too many failed attempts", ip)
        return templates.TemplateResponse(request, "login.html", {
            "next": target, "error": "Too many failed attempts. Try again in a few minutes.",
        }, status_code=429)
    if not auth.check_password(password):
        auth.record_failure(ip)
        log.warning("failed login from %s", ip)
        await asyncio.sleep(1)  # slows down guessing without needing a real rate-limiter
        return templates.TemplateResponse(request, "login.html", {
            "next": target, "error": "Wrong password.",
        }, status_code=401)

    auth.clear_failures(ip)
    resp = RedirectResponse(target, status_code=303)
    resp.set_cookie(
        auth.COOKIE_NAME, auth.make_session_token(),
        max_age=auth.settings.session_days * 86400, httponly=True, samesite="lax", secure=auth.is_https(request),
    )
    return resp


@app.post("/logout")
def logout():
    resp = RedirectResponse("/login", status_code=303)
    resp.delete_cookie(auth.COOKIE_NAME)
    return resp


# --- articles -------------------------------------------------------------------------------------

async def _process(id_: str, url: str) -> None:
    try:
        extracted = await fetch_and_extract(url)
    except ExtractionError as exc:
        db.mark_failed(id_, str(exc))
        return
    except Exception as exc:
        log.exception("fetch failed for %s", url)
        db.mark_failed(id_, f"fetch failed: {exc}")
        return

    try:
        result = await summarize(extracted["title"], extracted["text"])
    except SummarizeError as exc:
        db.mark_failed(id_, str(exc))
        return

    db.mark_done(id_, extracted["title"], result["summary"], result["key_points"], result["tags"], extracted["text"])


@app.get("/")
def index(request: Request, archived: bool = False):
    # The article list lives under the "save a link" box; there's no separate Articles tab anymore.
    items = db.list_articles(archived=archived)
    return templates.TemplateResponse(request, "index.html", {"tab": "save", "items": items, "archived": archived})


@app.post("/add")
async def add(url: str = Form(...)):
    url = url.strip()
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    domain = urlparse(url).netloc
    id_ = db.article_id(url)
    db.upsert_pending(id_, url, domain)
    await _process(id_, url)
    return RedirectResponse(f"/articles/{id_}", status_code=303)


@app.get("/articles")
def articles(archived: bool = False):
    # Old bookmarks/links to the former Articles tab land on the home page instead.
    return RedirectResponse("/?archived=true" if archived else "/", status_code=303)


@app.get("/articles/{id_}")
def article(request: Request, id_: str):
    row = db.get_article(id_)
    if row is None:
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse(request, "article.html", {"tab": "save", "a": row})


@app.post("/articles/{id_}/retry")
async def retry(id_: str):
    row = db.get_article(id_)
    if row is None:
        return RedirectResponse("/", status_code=303)
    db.upsert_pending(id_, row["url"], row["domain"])
    await _process(id_, row["url"])
    return RedirectResponse(f"/articles/{id_}", status_code=303)


@app.post("/articles/{id_}/read")
def mark_read(id_: str):
    db.mark_read(id_)
    return RedirectResponse(f"/articles/{id_}", status_code=303)


@app.post("/articles/{id_}/archive")
def archive(id_: str):
    db.set_archived(id_, True)
    return RedirectResponse("/", status_code=303)


@app.post("/articles/{id_}/unarchive")
def unarchive(id_: str):
    db.set_archived(id_, False)
    return RedirectResponse(f"/articles/{id_}", status_code=303)


@app.post("/articles/{id_}/delete")
def delete(id_: str):
    db.delete_article(id_)
    return RedirectResponse("/", status_code=303)
