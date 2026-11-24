"""Background new-release check: followed authors + series imported from Hardcover.

Runs inside the app (started from main.py's lifespan): wakes every few minutes, and when the last
check is older than WATCH_INTERVAL_HOURS it asks Hardcover once for every recent/upcoming book by a
followed author or in a linked series. The last-run time lives in SQLite, so container restarts don't
trigger extra checks. Results feed the notification panel; nothing is sent anywhere else.
"""
import asyncio
import logging
from datetime import date, datetime, timedelta, timezone

import books_db
import hardcover
import openlibrary
from config import settings

log = logging.getLogger("watcher")

_TICK_SECONDS = 300
_lock = asyncio.Lock()


def _today() -> str:
    return date.today().isoformat()


def status() -> dict:
    return {
        "last_check_at": books_db.meta_get("watch_last_check_at"),
        "last_error": books_db.meta_get("watch_last_error"),
        "last_found": books_db.meta_get("watch_last_found"),
    }


def _is_due() -> bool:
    last = books_db.meta_get("watch_last_check_at")
    if not last:
        return True
    try:
        elapsed = datetime.now(timezone.utc) - datetime.fromisoformat(last)
    except ValueError:
        return True
    return elapsed >= timedelta(hours=settings.watch_interval_hours)


async def run_check() -> dict:
    """One check. Returns {"found": n, "error": str | None}."""
    async with _lock:
        result = {"found": 0, "linked": 0, "error": None}
        follows = books_db.followed_authors()
        series = books_db.linked_series()
        if not hardcover.enabled():
            result["error"] = "Hardcover is not configured (HARDCOVER_TOKEN is empty)."
        elif follows or series:
            since = (date.today() - timedelta(days=settings.watch_lookback_days)).isoformat()
            try:
                books = await hardcover.watch_books([a["hc_id"] for a in follows], [s["hc_id"] for s in series], since)
            except hardcover.HardcoverError as exc:
                result["error"] = str(exc)
                books = []
            result["found"] = _store(books, follows, series)
        result["linked"] = await link_manual_books()
        try:
            await fill_french_titles()
        except Exception:  # French titles are a nice-to-have; never let them break the release check
            log.exception("French title lookup crashed")

        books_db.meta_set("watch_last_check_at", datetime.now(timezone.utc).isoformat())
        books_db.meta_set("watch_last_error", result["error"])
        books_db.meta_set("watch_last_found", str(result["found"]))
        if result["error"]:
            log.warning("new-release check failed: %s", result["error"])
        else:
            log.info("new-release check: %d new", result["found"])
        return result


def _store(books: list[dict], follows, series) -> int:
    author_names = {a["hc_id"]: a["name"] for a in follows}
    series_names = {s["hc_id"]: s["name"] for s in series}
    hc_ids, titles = books_db.library_keys()
    today = _today()
    found = 0
    for b in books:
        if b["hc_id"] not in hc_ids and books_db.owns_title(titles, b["title"], b["author"]):
            # A book you added by hand just showed up on Hardcover: link it instead of alerting.
            manual = books_db.find_unlinked_by_title(b["title"], b["author"])
            if manual:
                books_db.link_book(manual["id"], {**b, "hc_slug": b["slug"]}, "hc", _series_position(manual, b))
                hc_ids.add(b["hc_id"])
                log.info("linked manual book %r to Hardcover %s", manual["title"], b["hc_id"])
        # Same title by another author (two different "Sanctuary"s) doesn't count as owning it.
        owned = b["hc_id"] in hc_ids or books_db.owns_title(titles, b["title"], b["author"])
        # Something you already have only matters if it hasn't come out yet (release-day heads-up).
        if owned and b["release_date"] < today:
            continue
        followed = [author_names[i] for i in b["author_ids"] if i in author_names]
        if followed:
            reason = f"You follow {followed[0]}"
        elif b["series_hc_id"] in series_names:
            reason = f"New in {series_names[b['series_hc_id']]}"
        else:
            reason = "In a series you track"
        if books_db.upsert_release(b, reason):
            found += 1
    return found


# --- manual books: link them once they appear on Hardcover / Open Library --------------------------

_LOOKUPS_PER_CHECK = 8
_LOOKUP_RETRY_HOURS = 20


# Surname overlap ('DJ Molles' matches 'D.J. Molles'); a manual book with no author never auto-links,
# since a bare title is too ambiguous.
_same_author = books_db.same_author


def _series_position(book, hit: dict) -> str:
    """Series # from Hardcover, only when its series is the one this book sits in locally."""
    hs = hit.get("series") or {}
    hit_series_id = hs.get("hc_id") if hs else hit.get("series_hc_id")
    position = hs.get("position") if hs else hit.get("position")
    if not book["series_id"] or not hit_series_id:
        return ""
    local = books_db.get_series(book["series_id"])
    if local and local["hc_id"] == hit_series_id:
        return position or ""
    return ""


async def lookup_one(book) -> str | None:
    """Try to link one manual book. Returns 'Hardcover', 'Open Library' or None."""
    query = f"{book['title']} {book['author'] or ''}".strip()
    if hardcover.enabled():
        try:
            hits = await hardcover.search_books(query, limit=10)
        except hardcover.HardcoverError as exc:
            log.warning("lookup of %r on Hardcover failed: %s", book["title"], exc)
            hits = []
        for h in hits:
            if (books_db.titles_match(h["title"], book["title"])
                    and _same_author(book["author"], h["author"]) and not books_db.find_book(h["hc_id"])):
                books_db.link_book(book["id"], h, "hc", _series_position(book, h))
                books_db.mark_lookup_tried(book["id"])
                return "Hardcover"
    if not book["ol_key"]:
        try:
            hits = await openlibrary.search_books(query, limit=10)
        except openlibrary.OpenLibraryError as exc:
            log.warning("lookup of %r on Open Library failed: %s", book["title"], exc)
            hits = []
        for h in hits:
            if (books_db.titles_match(h["title"], book["title"])
                    and _same_author(book["author"], h["author"]) and not books_db.find_book(None, h["ol_key"])):
                books_db.link_book(book["id"], h, "ol")
                books_db.mark_lookup_tried(book["id"])
                return "Open Library"
    books_db.mark_lookup_tried(book["id"])
    return None


async def link_manual_books() -> int:
    """Retry a few queued manual books per check (each Hardcover search costs 1 request)."""
    linked = 0
    for i, book in enumerate(books_db.pending_lookups(_LOOKUPS_PER_CHECK, _LOOKUP_RETRY_HOURS)):
        if i:
            await asyncio.sleep(1.2)  # stay well inside Hardcover's burst limit
        if await lookup_one(book):
            linked += 1
    return linked


# --- French titles ---------------------------------------------------------------------------------

_FR_PER_CHECK = 200


async def fill_french_titles(series_id: int | None = None) -> tuple[int, int]:
    """Look up French edition titles for Hardcover-linked books (one request per 100 books).
    With series_id, re-checks every book of that series now; otherwise the ones never/long ago checked.
    Returns (books checked, titles found or changed)."""
    if not hardcover.enabled():
        return 0, 0
    books = books_db.books_needing_fr(_FR_PER_CHECK if series_id is None else 1000, series_id=series_id)
    if not books:
        return 0, 0
    try:
        by_hc = await hardcover.french_titles([b["hc_id"] for b in books])
    except hardcover.HardcoverError as exc:
        log.warning("French title lookup failed: %s", exc)
        return 0, 0
    found = {b["id"]: by_hc[b["hc_id"]] for b in books if b["hc_id"] in by_hc}
    changed = books_db.store_fr_titles(found, [b["id"] for b in books])
    log.info("French titles: checked %d, %d new/changed", len(books), changed)
    return len(books), changed


async def loop() -> None:
    await asyncio.sleep(20)  # let the app finish starting before the first check
    while True:
        try:
            if hardcover.enabled() and _is_due():
                await run_check()
        except Exception:
            log.exception("new-release check crashed")
        await asyncio.sleep(_TICK_SECONDS)
