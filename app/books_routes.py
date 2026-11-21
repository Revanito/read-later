"""Books tab: series in your own read order, Open Library + Hardcover search, subject browse and AI
"recommend similar"."""
import asyncio
import logging
from datetime import date
from urllib.parse import quote

from fastapi import APIRouter, Form
from fastapi.requests import Request
from fastapi.responses import JSONResponse, RedirectResponse

import auth
import books_db
import hardcover
import openlibrary
import recommender
import watcher
from web import templates

log = logging.getLogger("books")
router = APIRouter(prefix="/books")


def _redirect(url: str, notice: str = "") -> RedirectResponse:
    if notice:
        # The query string has to go before any #fragment, or the notice never reaches the server.
        path, hash_, fragment = url.partition("#")
        url = path + ("&" if "?" in path else "?") + "notice=" + quote(notice) + hash_ + fragment
    return RedirectResponse(url, status_code=303)


def _int_or_none(value: str | None) -> int | None:
    try:
        return int(value) if value not in (None, "") else None
    except ValueError:
        return None


def _mark_saved(hits: list[dict]) -> list[dict]:
    for h in hits:
        existing = books_db.find_book(h.get("hc_id"), h.get("ol_key", ""))
        h["saved_id"] = existing["id"] if existing else None
        if h.get("series") and h["series"].get("hc_id"):
            local = books_db.find_series_by_hc(h["series"]["hc_id"])
            h["series"]["local_id"] = local["id"] if local else None
    return hits


# --- overview -------------------------------------------------------------------------------------

@router.get("")
def books_home(request: Request, show: str = "active", notice: str = ""):
    series = books_db.list_series()
    if show == "active":
        # "Active" hides series you've finished (every non-skipped book read); "all" shows everything.
        series = [s for s in series if s["total"] == 0 or s["done"] < s["total"]]
    standalone = books_db.standalone_books()
    if show == "active":
        standalone = [b for b in standalone if b["status"] in ("want", "reading")]
    return templates.TemplateResponse(request, "books.html", {
        "tab": "books", "series_list": series, "standalone": standalone, "show": show, "notice": notice,
    })


# --- search & add ---------------------------------------------------------------------------------

@router.get("/search")
async def search(request: Request, q: str = "", type: str = "book", series_id: str = ""):
    q = q.strip()
    kind = "series" if type == "series" else "book"
    target_series = books_db.get_series(_int_or_none(series_id)) if _int_or_none(series_id) else None
    ctx = {
        "tab": "books", "q": q, "kind": kind, "target_series": target_series,
        "hc_hits": [], "ol_hits": [], "series_hits": [], "errors": [],
        "all_series": books_db.all_series_names(),
    }
    if not q:
        return templates.TemplateResponse(request, "book_search.html", ctx)

    if kind == "series":
        if not hardcover.enabled():
            ctx["errors"].append("Series search needs Hardcover. Set HARDCOVER_TOKEN in .env, or search books "
                                 "and build the series by hand.")
        else:
            try:
                hits = await hardcover.search_series(q)
                for h in hits:
                    local = books_db.find_series_by_hc(h["hc_id"])
                    h["local_id"] = local["id"] if local else None
                ctx["series_hits"] = hits
            except hardcover.HardcoverError as exc:
                ctx["errors"].append(str(exc))
        return templates.TemplateResponse(request, "book_search.html", ctx)

    tasks = [openlibrary.search_books(q)]
    if hardcover.enabled():
        tasks.append(hardcover.search_books(q))
    results = await asyncio.gather(*tasks, return_exceptions=True)
    for res, label in zip(results, ("ol_hits", "hc_hits")):
        if isinstance(res, Exception):
            log.warning("book search failed (%s): %s", label, res)
            ctx["errors"].append(str(res))
        else:
            ctx[label] = _mark_saved(res)
    return templates.TemplateResponse(request, "book_search.html", ctx)


@router.get("/subject")
async def subject(request: Request, s: str = ""):
    s = s.strip()
    ctx = {"tab": "books", "subject": s, "hits": [], "errors": [], "all_series": books_db.all_series_names(),
           "target_series": None}
    if s:
        try:
            ctx["hits"] = _mark_saved(await openlibrary.books_by_subject(s))
        except openlibrary.OpenLibraryError as exc:
            ctx["errors"].append(str(exc))
    return templates.TemplateResponse(request, "book_subject.html", ctx)


@router.post("/add")
def add_book(
    title: str = Form(...), author: str = Form(""), year: str = Form(""), cover_url: str = Form(""),
    ol_key: str = Form(""), hc_id: str = Form(""), hc_slug: str = Form(""), subjects: str = Form(""),
    position: str = Form(""), series_id: str = Form(""), new_series: str = Form(""), status: str = Form("want"),
):
    hc = _int_or_none(hc_id)
    existing = books_db.find_book(hc, ol_key)
    if existing:
        return _redirect(f"/books/book/{existing['id']}", "Already in your library.")

    target = _int_or_none(series_id)
    if series_id == "new" and new_series.strip():
        target = books_db.create_series(new_series, author)
    elif target is not None and books_db.get_series(target) is None:
        target = None

    hit = {
        "title": title, "author": author, "year": _int_or_none(year), "cover_url": cover_url,
        "ol_key": ol_key, "hc_id": hc, "hc_slug": hc_slug,
        "subjects": [x.strip() for x in subjects.split(",") if x.strip()],
    }
    book_id = books_db.add_book(hit, target, position, status)
    if target:
        return _redirect(f"/books/series/{target}", f"Added “{title}”.")
    return _redirect(f"/books/book/{book_id}", "Added to your library.")


@router.post("/import-series")
async def import_series(hc_id: str = Form(...)):
    hc = _int_or_none(hc_id)
    if hc is None:
        return _redirect("/books", "Invalid Hardcover series id.")
    try:
        data = await hardcover.get_series(hc)
    except hardcover.HardcoverError as exc:
        return _redirect("/books", f"Import failed: {exc}")

    local = books_db.find_series_by_hc(hc)
    series_id = local["id"] if local else books_db.create_series(data["name"], data["author"], hc, data["slug"])
    added = books_db.import_series_books(series_id, data["books"])
    msg = f"Imported {added} book{'s' if added != 1 else ''} from Hardcover." if not local else \
        f"Synced with Hardcover: {added} new book{'s' if added != 1 else ''}."
    return _redirect(f"/books/series/{series_id}", msg)


# --- new releases & following ---------------------------------------------------------------------

@router.get("/new")
async def new_releases_page(request: Request, pick: str = "", notice: str = ""):
    followed = books_db.followed_authors()
    followed_norm = {books_db.norm_name(a["name"]) for a in followed}
    ctx = {
        "tab": "books", "notice": notice, "followed": followed, "pick": pick.strip(), "candidates": [],
        "errors": [], "series": books_db.linked_series(), "status": watcher.status(),
        "releases": books_db.active_releases(date.today().isoformat()),
        "suggestions": [n for n in books_db.library_authors() if books_db.norm_name(n) not in followed_norm][:40],
    }
    if ctx["pick"] and hardcover.enabled():
        try:
            ctx["candidates"] = await hardcover.search_authors(ctx["pick"])
        except hardcover.HardcoverError as exc:
            ctx["errors"].append(str(exc))
    return templates.TemplateResponse(request, "book_new.html", ctx)


@router.post("/new/check")
async def check_now():
    result = await watcher.run_check()
    if result["error"]:
        return _redirect("/books/new", f"Check failed: {result['error']}")
    n = result["found"]
    return _redirect("/books/new", f"Checked. {n} new release{'s' if n != 1 else ''} found." if n else "Checked. Nothing new.")


@router.post("/new/{hc_id}/dismiss")
def dismiss_release(hc_id: int, next: str = Form("/books/new")):
    books_db.dismiss_release(hc_id)
    return _redirect(auth.safe_next(next))


@router.post("/follow")
async def follow(name: str = Form(""), hc_id: str = Form(""), slug: str = Form(""), next: str = Form("/books/new")):
    target = auth.safe_next(next)
    name = name.strip()
    if _int_or_none(hc_id):
        books_db.follow_author(int(hc_id), name or "Unknown author", slug)
        return _redirect(target, f"Following {name}. You'll see their new books here.")
    if not name:
        return _redirect(target)
    if not hardcover.enabled():
        return _redirect(target, "Following authors needs Hardcover (HARDCOVER_TOKEN).")
    try:
        hits = await hardcover.search_authors(name)
    except hardcover.HardcoverError as exc:
        return _redirect(target, f"Couldn't look up {name}: {exc}")
    exact = [h for h in hits if books_db.norm_name(h["name"]) == books_db.norm_name(name)]
    if len(exact) >= 1:
        # Several exact-name hits are usually duplicates; Typesense ranks the real one first.
        books_db.follow_author(exact[0]["hc_id"], exact[0]["name"], exact[0]["slug"])
        return _redirect(target, f"Following {exact[0]['name']}. You'll see their new books here.")
    return _redirect(f"/books/new?pick={quote(name)}", f"No exact match for “{name}”. Pick the right author below.")


@router.post("/unfollow")
def unfollow(hc_id: int = Form(...), next: str = Form("/books/new")):
    books_db.unfollow_author(hc_id)
    return _redirect(auth.safe_next(next), "Unfollowed.")


# --- series ---------------------------------------------------------------------------------------

@router.post("/series/new")
def new_series(name: str = Form(...), author: str = Form("")):
    sid = books_db.create_series(name, author)
    return _redirect(f"/books/series/{sid}", "Series created. Add books to it with search.")


@router.get("/series/{series_id}")
def series_page(request: Request, series_id: int, notice: str = ""):
    s = books_db.get_series(series_id)
    if s is None:
        return _redirect("/books", "That series no longer exists.")
    books = books_db.series_books(series_id)
    counted = [b for b in books if b["status"] != "skip"]
    return templates.TemplateResponse(request, "book_series.html", {
        "tab": "books", "s": s, "books": books, "notice": notice,
        "done": sum(1 for b in counted if b["status"] == "read"), "total": len(counted),
        "subjects": books_db.series_subjects(series_id),
        "recs": books_db.get_recs("series", series_id),
    })


@router.post("/series/{series_id}/edit")
def edit_series(series_id: int, name: str = Form(...), author: str = Form(""), notes: str = Form("")):
    books_db.update_series(series_id, name, author, notes)
    return _redirect(f"/books/series/{series_id}", "Saved.")


@router.post("/series/{series_id}/delete")
def delete_series(series_id: int):
    books_db.delete_series(series_id)
    return _redirect("/books", "Series deleted.")


@router.post("/series/{series_id}/reset-order")
def reset_order(series_id: int):
    books_db.reset_order(series_id)
    return _redirect(f"/books/series/{series_id}", "Read order reset to series numbering.")


@router.post("/series/{series_id}/order")
async def set_order(request: Request, series_id: int):
    try:
        payload = await request.json()
        ids = [int(i) for i in payload.get("ids", [])]
    except (ValueError, TypeError, AttributeError):
        return JSONResponse({"ok": False, "error": "bad payload"}, status_code=400)
    books_db.set_order(series_id, ids)
    return {"ok": True}


@router.post("/series/{series_id}/sync")
async def sync_series(series_id: int):
    s = books_db.get_series(series_id)
    if s is None:
        return _redirect("/books")
    if not s["hc_id"]:
        return _redirect(f"/books/series/{series_id}/link")
    try:
        data = await hardcover.get_series(s["hc_id"])
    except hardcover.HardcoverError as exc:
        return _redirect(f"/books/series/{series_id}", f"Sync failed: {exc}")
    added = books_db.import_series_books(series_id, data["books"])
    return _redirect(f"/books/series/{series_id}", f"Synced with Hardcover: {added} new book{'s' if added != 1 else ''}.")


@router.get("/series/{series_id}/link")
async def link_series_page(request: Request, series_id: int, q: str = ""):
    s = books_db.get_series(series_id)
    if s is None:
        return _redirect("/books")
    q = q.strip() or s["name"]
    ctx = {"tab": "books", "s": s, "q": q, "hits": [], "errors": []}
    if not hardcover.enabled():
        ctx["errors"].append("Hardcover is not configured (HARDCOVER_TOKEN is empty).")
    else:
        try:
            ctx["hits"] = await hardcover.search_series(q)
        except hardcover.HardcoverError as exc:
            ctx["errors"].append(str(exc))
    return templates.TemplateResponse(request, "book_link.html", ctx)


@router.post("/series/{series_id}/link")
async def link_series(series_id: int, hc_id: str = Form(...)):
    hc = _int_or_none(hc_id)
    if hc is None or books_db.get_series(series_id) is None:
        return _redirect("/books")
    other = books_db.find_series_by_hc(hc)
    if other and other["id"] != series_id:
        return _redirect(f"/books/series/{other['id']}", "That Hardcover series is already linked to this one.")
    try:
        data = await hardcover.get_series(hc)
    except hardcover.HardcoverError as exc:
        return _redirect(f"/books/series/{series_id}", f"Link failed: {exc}")
    books_db.link_series_hc(series_id, hc, data["slug"])
    added = books_db.import_series_books(series_id, data["books"])
    return _redirect(f"/books/series/{series_id}", f"Linked to Hardcover, {added} book{'s' if added != 1 else ''} added.")


@router.post("/series/{series_id}/recommend")
async def recommend_series(series_id: int):
    try:
        await recommender.recommend("series", series_id)
    except recommender.RecommendError as exc:
        return _redirect(f"/books/series/{series_id}", f"Recommendation failed: {exc}")
    return _redirect(f"/books/series/{series_id}#recs")


# --- single book ----------------------------------------------------------------------------------

@router.get("/book/{book_id}")
def book_page(request: Request, book_id: int, notice: str = ""):
    b = books_db.get_book(book_id)
    if b is None:
        return _redirect("/books", "That book no longer exists.")
    return templates.TemplateResponse(request, "book_detail.html", {
        "tab": "books", "b": b, "notice": notice,
        "series": books_db.get_series(b["series_id"]) if b["series_id"] else None,
        "all_series": books_db.all_series_names(),
        "subjects": [x for x in (b["subjects"] or "").split(",") if x],
        "recs": books_db.get_recs("book", book_id),
    })


@router.post("/book/{book_id}/status")
def book_status(book_id: int, status: str = Form(...), next: str = Form("")):
    books_db.set_status(book_id, status)
    return _redirect(next if next.startswith("/books") else f"/books/book/{book_id}")


@router.post("/book/{book_id}/move")
def book_move(book_id: int, direction: str = Form(...)):
    books_db.move_book(book_id, direction)
    b = books_db.get_book(book_id)
    return _redirect(f"/books/series/{b['series_id']}#b{book_id}" if b and b["series_id"] else "/books")


@router.post("/book/{book_id}/read-above")
def book_read_above(book_id: int):
    b = books_db.get_book(book_id)
    if b is None or not b["series_id"]:
        return _redirect("/books")
    n = books_db.mark_read_before(book_id)
    msg = f"Marked {n} book{'s' if n != 1 else ''} as read." if n else "Everything above was already read."
    return _redirect(f"/books/series/{b['series_id']}#b{book_id}", msg)


@router.post("/book/{book_id}/edit")
def book_edit(book_id: int, title: str = Form(...), author: str = Form(""), year: str = Form(""),
              position: str = Form(""), notes: str = Form("")):
    books_db.update_book(book_id, title, author, _int_or_none(year), position, notes)
    return _redirect(f"/books/book/{book_id}", "Saved.")


@router.post("/book/{book_id}/series")
def book_series(book_id: int, series_id: str = Form(""), new_series: str = Form("")):
    b = books_db.get_book(book_id)
    if b is None:
        return _redirect("/books")
    if series_id == "new" and new_series.strip():
        target = books_db.create_series(new_series, b["author"] or "")
    else:
        target = _int_or_none(series_id)
        if target is not None and books_db.get_series(target) is None:
            target = None
    books_db.assign_series(book_id, target)
    return _redirect(f"/books/book/{book_id}", "Moved to series." if target else "Now a standalone book.")


@router.post("/book/{book_id}/delete")
def book_delete(book_id: int):
    b = books_db.get_book(book_id)
    books_db.delete_book(book_id)
    if b and b["series_id"]:
        return _redirect(f"/books/series/{b['series_id']}", "Book removed.")
    return _redirect("/books", "Book removed.")


@router.post("/book/{book_id}/recommend")
async def recommend_book(book_id: int):
    try:
        await recommender.recommend("book", book_id)
    except recommender.RecommendError as exc:
        return _redirect(f"/books/book/{book_id}", f"Recommendation failed: {exc}")
    return _redirect(f"/books/book/{book_id}#recs")
