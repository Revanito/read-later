import logging
from contextlib import asynccontextmanager
from urllib.parse import urlparse

from fastapi import FastAPI, Form
from fastapi.requests import Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

import db
from extractor import ExtractionError, fetch_and_extract
from summarizer import SummarizeError, summarize

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("main")

templates = Jinja2Templates(directory="templates")


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    yield


app = FastAPI(lifespan=lifespan)
app.mount("/static", StaticFiles(directory="static"), name="static")


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
def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


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
def articles(request: Request, archived: bool = False):
    items = db.list_articles(archived=archived)
    return templates.TemplateResponse("articles.html", {"request": request, "items": items, "archived": archived})


@app.get("/articles/{id_}")
def article(request: Request, id_: str):
    row = db.get_article(id_)
    if row is None:
        return RedirectResponse("/articles", status_code=303)
    return templates.TemplateResponse("article.html", {"request": request, "a": row})


@app.post("/articles/{id_}/retry")
async def retry(id_: str):
    row = db.get_article(id_)
    if row is None:
        return RedirectResponse("/articles", status_code=303)
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
    return RedirectResponse("/articles", status_code=303)


@app.post("/articles/{id_}/unarchive")
def unarchive(id_: str):
    db.set_archived(id_, False)
    return RedirectResponse(f"/articles/{id_}", status_code=303)


@app.post("/articles/{id_}/delete")
def delete(id_: str):
    db.delete_article(id_)
    return RedirectResponse("/articles", status_code=303)
