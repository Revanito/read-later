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
from i18n import _, ngettext
import hardcover
import openlibrary
import presets
import recommender
import research
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
def books_home(request: Request, notice: str = ""):
    # A series is finished once every non-skipped book is read; finished series and read standalone
    # books move to the "Already read" section at the bottom instead of being hidden.
    series = books_db.list_series()
    finished = [s for s in series if s["total"] and s["done"] >= s["total"]]
    active = [s for s in series if not (s["total"] and s["done"] >= s["total"])]
    standalone = books_db.standalone_books()
    return templates.TemplateResponse(request, "books.html", {
        "tab": "books", "notice": notice,
        "series_list": active, "finished_series": finished,
        "standalone": [b for b in standalone if b["status"] != "read"],
        "read_standalone": [b for b in standalone if b["status"] == "read"],
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
            ctx["errors"].append(_("Series search needs Hardcover. Set HARDCOVER_TOKEN in .env, or search books "
                                   "and build the series by hand."))
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
        return _redirect(f"/books/book/{existing['id']}", _("Already in your library."))

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
        return _redirect(f"/books/series/{target}", _("Added “{title}”.", title=title))
    return _redirect(f"/books/book/{book_id}", _("Added to your library."))


@router.post("/add-manual")
def add_manual(
    title: str = Form(...), author: str = Form(""), year: str = Form(""), position: str = Form(""),
    series_id: str = Form(""), new_series: str = Form(""), status: str = Form("want"),
):
    """A book neither database lists yet (e.g. a self-published release from this week). It's queued for
    lookup and links itself to Hardcover / Open Library once it shows up there."""
    title = title.strip()
    if not title:
        return _redirect("/books", _("A title is needed to add a book."))
    target = _int_or_none(series_id)
    if target is not None and books_db.get_series(target) is None:
        target = None
    # Same title by another author is another book (Chris Fox's "Sanctuary" vs D.J. Molles's): only the same
    # title by the same author, or already in this series, is a duplicate.
    existing = books_db.find_duplicate(title, author.strip(), target)
    if existing:
        return _redirect(f"/books/book/{existing['id']}", _("Already in your library."))
    if series_id == "new" and new_series.strip():
        target = books_db.create_series(new_series, author)

    book_id = books_db.add_book({"title": title, "author": author.strip(), "year": _int_or_none(year)},
                                target, position.strip(), status)
    books_db.queue_lookup(book_id)
    if target:
        books_db.place_by_position(book_id)
        return _redirect(f"/books/series/{target}#b{book_id}",
                         _("Added “{title}”. It links itself to Hardcover or Open Library once it's listed there.", title=title))
    return _redirect(f"/books/book/{book_id}", _("Added. It links itself to Hardcover or Open Library once it's listed there."))


@router.post("/import-series")
async def import_series(hc_id: str = Form(...)):
    hc = _int_or_none(hc_id)
    if hc is None:
        return _redirect("/books", _("Invalid Hardcover series id."))
    try:
        data = await hardcover.get_series(hc)
    except hardcover.HardcoverError as exc:
        return _redirect("/books", _("Import failed: {error}", error=exc))

    local = books_db.find_series_by_hc(hc)
    series_id = local["id"] if local else books_db.create_series(data["name"], data["author"], hc, data["slug"])
    added = books_db.import_series_books(series_id, data["books"])
    msg = (ngettext("Imported {n} book from Hardcover.", "Imported {n} books from Hardcover.", added) if not local
           else ngettext("Synced with Hardcover: {n} new book.", "Synced with Hardcover: {n} new books.", added))
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
        return _redirect("/books/new", _("Check failed: {error}", error=result["error"]))
    n, linked = result["found"], result.get("linked", 0)
    msg = (ngettext("Checked. {n} new release found.", "Checked. {n} new releases found.", n) if n
           else _("Checked. Nothing new."))
    if linked:
        msg += " " + ngettext("Linked {n} manually added book.", "Linked {n} manually added books.", linked)
    return _redirect("/books/new", msg)


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
        return _redirect(target, _("Following {name}. You'll see their new books here.", name=name))
    if not name:
        return _redirect(target)
    if not hardcover.enabled():
        return _redirect(target, _("Following authors needs Hardcover (HARDCOVER_TOKEN)."))
    try:
        hits = await hardcover.search_authors(name)
    except hardcover.HardcoverError as exc:
        return _redirect(target, _("Couldn't look up {name}: {error}", name=name, error=exc))
    exact = [h for h in hits if books_db.norm_name(h["name"]) == books_db.norm_name(name)]
    if len(exact) >= 1:
        # Several exact-name hits are usually duplicates; Typesense ranks the real one first.
        books_db.follow_author(exact[0]["hc_id"], exact[0]["name"], exact[0]["slug"])
        return _redirect(target, _("Following {name}. You'll see their new books here.", name=exact[0]["name"]))
    return _redirect(f"/books/new?pick={quote(name)}", _("No exact match for “{name}”. Pick the right author below.", name=name))


@router.post("/unfollow")
def unfollow(hc_id: int = Form(...), next: str = Form("/books/new")):
    books_db.unfollow_author(hc_id)
    return _redirect(auth.safe_next(next), _("Unfollowed."))


# --- series ---------------------------------------------------------------------------------------

@router.post("/series/new")
def new_series(name: str = Form(...), author: str = Form("")):
    sid = books_db.create_series(name, author)
    return _redirect(f"/books/series/{sid}", _("Series created. Add books to it with search."))


@router.get("/series/{series_id}")
def series_page(request: Request, series_id: int, notice: str = ""):
    s = books_db.get_series(series_id)
    if s is None:
        return _redirect("/books", _("That series no longer exists."))
    if s["merged_into"]:  # folded into another series (e.g. Primarchs into the Heresy map)
        return RedirectResponse(f"/books/series/{s['merged_into']}", status_code=303)
    books = books_db.series_books(series_id)
    counted = [b for b in books if b["status"] != "skip"]
    return templates.TemplateResponse(request, "book_series.html", {
        "tab": "books", "s": s, "books": books, "notice": notice,
        "done": sum(1 for b in counted if b["status"] == "read"), "total": len(counted),
        "subjects": books_db.series_subjects(series_id),
        "recs": books_db.get_recs("series", series_id),
        "map": books_db.map_json(series_id),
        "research": research.state(series_id),
        "series_names": {r["id"]: r["name"] for r in books_db.all_series_names()},
    })


# --- reading-order research (AI + web search, see research.py) -------------------------------------

@router.post("/series/{series_id}/research")
async def research_start(series_id: int):
    if books_db.get_series(series_id) is None:
        return _redirect("/books", _("That series no longer exists."))
    research.start(series_id)
    return RedirectResponse(f"/books/series/{series_id}#research", status_code=303)


@router.post("/series/{series_id}/research/apply")
def research_apply(series_id: int, add: list[str] = Form([]), reorder: str = Form(""), build_map: str = Form(""),
                   build_lanes: str = Form("")):
    if books_db.get_series(series_id) is None:
        return _redirect("/books", _("That series no longer exists."))
    picked = {i for i in (_int_or_none(x) for x in add) if i is not None}
    st = research.apply(series_id, picked, bool(reorder), bool(build_map), bool(build_lanes))
    books_db.delete_research(series_id)
    parts = [ngettext("{n} book added", "{n} books added", st["added"])]
    if st["moved"]:
        parts.append(ngettext("{n} moved in from another series", "{n} moved in from other series", st["moved"]))
    if st["ordered"]:
        parts.append(_("read order updated"))
    if st["mapped"]:
        parts.append(_("reading map rebuilt"))
    view = "?view=map" if st["mapped"] else ""
    return _redirect(f"/books/series/{series_id}{view}", _("Research applied: {details}.", details=", ".join(parts)))


@router.post("/series/{series_id}/research/discard")
def research_discard(series_id: int):
    books_db.delete_research(series_id)
    return RedirectResponse(f"/books/series/{series_id}", status_code=303)


# --- reading maps: presets ------------------------------------------------------------------------

@router.get("/presets")
def presets_page(request: Request, series_id: str = "", notice: str = ""):
    items = [{"p": p, "size": presets.preset_size(p), "guess": presets.guess_target(p)} for p in presets.PRESETS.values()]
    return templates.TemplateResponse(request, "book_presets.html", {
        "tab": "books", "items": items, "all_series": books_db.all_series_names(), "notice": notice,
        "preselect": _int_or_none(series_id),
    })


@router.post("/presets/{key}/apply")
def apply_preset(key: str, series_id: str = Form("new"), mark_unread: str = Form("")):
    if key not in presets.PRESETS:
        return _redirect("/books/presets", _("Unknown preset."))
    target = None if series_id == "new" else _int_or_none(series_id)
    if target is not None and books_db.get_series(target) is None:
        return _redirect("/books/presets", _("That series no longer exists."))
    sid, st = presets.apply_preset(key, target, mark_unread=bool(mark_unread))
    moved = " " + _("({n} moved in from other series)", n=st["moved"]) if st["moved"] else ""
    msg = (ngettext("Reading map applied: {n} book matched", "Reading map applied: {n} books matched", st["matched"])
           + moved + _(", {n} added.", n=st["created"]))
    if st["folded"]:
        msg += " " + ngettext("{n} related series folded in", "{n} related series folded in", st["folded"])
        msg += (" " + ngettext("({n} duplicate merged).", "({n} duplicates merged).", st["merged"])
                if st["merged"] else ".")
    return _redirect(f"/books/series/{sid}?view=map", msg)


# --- reading maps: status from the map/list views -------------------------------------------------

@router.post("/book/{book_id}/set-status")
async def set_status_json(request: Request, book_id: int):
    try:
        status = (await request.json()).get("status")
    except (ValueError, AttributeError):
        status = None
    if status not in books_db.STATUSES or books_db.get_book(book_id) is None:
        return JSONResponse({"ok": False}, status_code=400)
    books_db.set_status(book_id, status)
    return {"ok": True, "status": status}


# --- reading maps: editor -------------------------------------------------------------------------

def _editor(series_id: int, anchor: str = "", notice: str = "") -> RedirectResponse:
    return _redirect(f"/books/series/{series_id}/map/edit" + (f"#{anchor}" if anchor else ""), notice)


@router.get("/series/{series_id}/map/edit")
def map_edit_page(request: Request, series_id: int, notice: str = ""):
    s = books_db.get_series(series_id)
    if s is None:
        return _redirect("/books")
    return templates.TemplateResponse(request, "book_map_edit.html", {
        "tab": "books", "s": s, "notice": notice, "data": books_db.map_editor_data(series_id),
        "links": books_db.map_links_for_edit(series_id), "kinds": books_db.SECTION_KINDS,
        "link_kinds": books_db.LINK_KINDS, "lane_link_kinds": books_db.LANE_LINK_KINDS,
        "meta": books_db.get_map_meta(series_id),
    })


@router.post("/series/{series_id}/map/start")
def map_start(series_id: int):
    if books_db.get_series(series_id) is None:
        return _redirect("/books")
    books_db.start_map(series_id)
    return _editor(series_id, notice=_("Map started with one arc holding every book. Add sections and arcs, then place books."))


@router.post("/series/{series_id}/map/delete")
def map_delete(series_id: int):
    books_db.clear_map(series_id)
    return _redirect(f"/books/series/{series_id}", _("Reading map removed. The books are untouched."))


@router.post("/series/{series_id}/map/section")
def map_add_section(series_id: int, kind: str = Form("row"), name: str = Form(""), color: str = Form("#5ec2a3"),
                    hint: str = Form("")):
    if books_db.get_series(series_id) is None:
        return _redirect("/books")
    if not books_db.has_map(series_id):
        books_db.set_map_meta(series_id, None, 150)
    sid = books_db.add_section(series_id, kind, name or _("New section"), color, hint)
    return _editor(series_id, f"sec{sid}", _("Section added."))


@router.post("/map/section/{section_id}/{action}")
def map_section_action(section_id: int, action: str, name: str = Form(""), color: str = Form("#5ec2a3"),
                       hint: str = Form(""), kind: str = Form(""), auto_wire: str = Form(""), direction: str = Form("up"),
                       name_fr: str = Form(""), hint_fr: str = Form("")):
    series_id = books_db.section_series(section_id)
    if series_id is None:
        return _redirect("/books")
    if action == "edit":
        books_db.update_section(section_id, name, color, hint, kind, bool(auto_wire), name_fr, hint_fr)
        return _editor(series_id, f"sec{section_id}", _("Section saved."))
    if action == "move":
        books_db.move_section(section_id, direction)
        return _editor(series_id, f"sec{section_id}")
    if action == "delete":
        books_db.delete_section(section_id)
        return _editor(series_id, notice=_("Section removed. Its books are back under “Not placed”."))
    if action == "add-group":
        gid = books_db.add_group(section_id, name or _("New arc"), color)
        return _editor(series_id, f"grp{gid}", _("Arc added."))
    return _editor(series_id)


@router.post("/map/group/{group_id}/{action}")
def map_group_action(group_id: int, action: str, name: str = Form(""), color: str = Form("#5ec2a3"),
                     hint: str = Form(""), loose: str = Form(""), direction: str = Form("up"), name_fr: str = Form("")):
    series_id = books_db.group_series(group_id)
    if series_id is None:
        return _redirect("/books")
    if action == "edit":
        books_db.update_group(group_id, name, color, hint, bool(loose), name_fr)
        return _editor(series_id, f"grp{group_id}", _("Arc saved."))
    if action == "move":
        books_db.move_group(group_id, direction)
        return _editor(series_id, f"grp{group_id}")
    if action == "delete":
        books_db.delete_group(group_id)
        return _editor(series_id, notice=_("Arc removed. Its books are back under “Not placed”."))
    return _editor(series_id)


@router.post("/series/{series_id}/map/place")
def map_place(series_id: int, book_id: int = Form(...), group_id: str = Form("")):
    b = books_db.get_book(book_id)
    if b is None or b["series_id"] != series_id:
        return _editor(series_id)
    gid = _int_or_none(group_id)
    if gid is None:
        books_db.unplace_book(book_id)
        return _editor(series_id, "unplaced")
    if books_db.group_series(gid) != series_id:
        return _editor(series_id)
    books_db.place_book(book_id, gid)
    return _editor(series_id, f"grp{gid}")


@router.post("/map/entry/{entry_id}/{action}")
def map_entry_action(entry_id: int, action: str, direction: str = Form("up"), row: str = Form("0"),
                     indent: str = Form("0"), sub: str = Form("")):
    series_id = books_db.entry_series(entry_id)
    if series_id is None:
        return _redirect("/books")
    if action == "move":
        books_db.move_entry(entry_id, direction)
    elif action == "edit":
        books_db.update_entry(entry_id, _int_or_none(row) or 0, _int_or_none(indent) or 0, sub)
    return _editor(series_id, f"ent{entry_id}")


@router.post("/series/{series_id}/map/link")
def map_add_link(series_id: int, from_id: int = Form(...), to_id: int = Form(...), kind: str = Form("cross")):
    a, b = books_db.get_book(from_id), books_db.get_book(to_id)
    if not a or not b or a["series_id"] != series_id or b["series_id"] != series_id or from_id == to_id:
        return _editor(series_id, "links", _("Pick two different books from this series."))
    ok = kind in books_db.LINK_KINDS or kind in books_db.LANE_LINK_KINDS
    books_db.add_link(series_id, f"book:{from_id}", f"book:{to_id}", kind if ok else "cross")
    return _editor(series_id, "links", _("Link added."))


@router.post("/series/{series_id}/map/link/{link_id}/delete")
def map_delete_link(series_id: int, link_id: int):
    books_db.delete_link(link_id, series_id)
    return _editor(series_id, "links", _("Link removed."))


@router.post("/series/{series_id}/edit")
def edit_series(series_id: int, name: str = Form(...), author: str = Form(""), notes: str = Form(""),
                name_fr: str = Form("")):
    books_db.update_series(series_id, name, author, notes)
    books_db.set_series_name_fr(series_id, name_fr)
    return _redirect(f"/books/series/{series_id}", _("Saved."))


@router.post("/series/{series_id}/delete")
def delete_series(series_id: int):
    books_db.delete_series(series_id)
    return _redirect("/books", _("Series deleted."))


@router.post("/series/{series_id}/reset-order")
def reset_order(series_id: int):
    books_db.reset_order(series_id)
    return _redirect(f"/books/series/{series_id}", _("Read order reset to series numbering."))


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
        return _redirect(f"/books/series/{series_id}", _("Sync failed: {error}", error=exc))
    added = books_db.import_series_books(series_id, data["books"])
    return _redirect(f"/books/series/{series_id}",
                     ngettext("Synced with Hardcover: {n} new book.", "Synced with Hardcover: {n} new books.", added))


@router.post("/series/{series_id}/french-titles")
async def french_titles(series_id: int):
    if books_db.get_series(series_id) is None:
        return _redirect("/books")
    checked, changed = await watcher.fill_french_titles(series_id)
    books = books_db.series_books(series_id)
    with_fr = sum(1 for b in books if b["title_fr"])
    if not checked:
        return _redirect(f"/books/series/{series_id}",
                         _("No books here are linked to Hardcover yet, so there's nothing to look up."))
    return _redirect(f"/books/series/{series_id}",
                     _("French titles: {found} of {total} books have one ({changed} new or updated).",
                       found=with_fr, total=len(books), changed=changed))


@router.get("/series/{series_id}/link")
async def link_series_page(request: Request, series_id: int, q: str = ""):
    s = books_db.get_series(series_id)
    if s is None:
        return _redirect("/books")
    q = q.strip() or s["name"]
    ctx = {"tab": "books", "s": s, "q": q, "hits": [], "errors": []}
    if not hardcover.enabled():
        ctx["errors"].append(_("Hardcover is not configured (HARDCOVER_TOKEN is empty)."))
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
        return _redirect(f"/books/series/{other['id']}", _("That Hardcover series is already linked to this one."))
    try:
        data = await hardcover.get_series(hc)
    except hardcover.HardcoverError as exc:
        return _redirect(f"/books/series/{series_id}", _("Link failed: {error}", error=exc))
    books_db.link_series_hc(series_id, hc, data["slug"])
    added = books_db.import_series_books(series_id, data["books"])
    return _redirect(f"/books/series/{series_id}",
                     ngettext("Linked to Hardcover, {n} book added.", "Linked to Hardcover, {n} books added.", added))


@router.post("/series/{series_id}/recommend")
async def recommend_series(series_id: int):
    try:
        await recommender.recommend("series", series_id)
    except recommender.RecommendError as exc:
        return _redirect(f"/books/series/{series_id}", _("Recommendation failed: {error}", error=exc))
    return _redirect(f"/books/series/{series_id}#recs")


# --- single book ----------------------------------------------------------------------------------

@router.get("/book/{book_id}")
def book_page(request: Request, book_id: int, notice: str = ""):
    b = books_db.get_book(book_id)
    if b is None:
        return _redirect("/books", _("That book no longer exists."))
    return templates.TemplateResponse(request, "book_detail.html", {
        "tab": "books", "b": b, "notice": notice,
        "series": books_db.get_series(b["series_id"]) if b["series_id"] else None,
        "all_series": books_db.all_series_names(),
        "subjects": [x for x in (b["subjects"] or "").split(",") if x],
        "recs": books_db.get_recs("book", book_id),
        "pending_lookup": books_db.is_pending_lookup(book_id),
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


@router.post("/book/{book_id}/lookup")
async def book_lookup(book_id: int):
    b = books_db.get_book(book_id)
    if b is None:
        return _redirect("/books")
    source = await watcher.lookup_one(b)
    if source:
        return _redirect(f"/books/book/{book_id}", _("Found it on {source}. Cover and details filled in.", source=source))
    return _redirect(f"/books/book/{book_id}",
                     _("Not listed on Hardcover or Open Library yet. The app keeps checking every few hours."))


@router.post("/book/{book_id}/read-above")
def book_read_above(book_id: int):
    b = books_db.get_book(book_id)
    if b is None or not b["series_id"]:
        return _redirect("/books")
    n = books_db.mark_read_before(book_id)
    msg = ngettext("Marked {n} book as read.", "Marked {n} books as read.", n) if n else _("Everything above was already read.")
    return _redirect(f"/books/series/{b['series_id']}#b{book_id}", msg)


@router.post("/book/{book_id}/edit")
def book_edit(book_id: int, title: str = Form(...), author: str = Form(""), year: str = Form(""),
              position: str = Form(""), notes: str = Form(""), title_fr: str | None = Form(None)):
    before = books_db.get_book(book_id)
    books_db.update_book(book_id, title, author, _int_or_none(year), position, notes)
    # Only a changed French title counts as yours: saving with Hardcover's untouched keeps it automatic.
    if before is not None and title_fr is not None and title_fr.strip() != (before["title_fr"] or ""):
        books_db.set_manual_fr_title(book_id, title_fr)
    return _redirect(f"/books/book/{book_id}", _("Saved."))


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
    return _redirect(f"/books/book/{book_id}", _("Moved to series.") if target else _("Now a standalone book."))


@router.post("/book/{book_id}/delete")
def book_delete(book_id: int):
    b = books_db.get_book(book_id)
    books_db.delete_book(book_id)
    if b and b["series_id"]:
        return _redirect(f"/books/series/{b['series_id']}", _("Book removed."))
    return _redirect("/books", _("Book removed."))


@router.post("/book/{book_id}/recommend")
async def recommend_book(book_id: int):
    try:
        await recommender.recommend("book", book_id)
    except recommender.RecommendError as exc:
        return _redirect(f"/books/book/{book_id}", _("Recommendation failed: {error}", error=exc))
    return _redirect(f"/books/book/{book_id}#recs")
