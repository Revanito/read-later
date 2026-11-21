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
        result = {"found": 0, "error": None}
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
        owned = b["hc_id"] in hc_ids or books_db._norm_title(b["title"]) in titles
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


async def loop() -> None:
    await asyncio.sleep(20)  # let the app finish starting before the first check
    while True:
        try:
            if hardcover.enabled() and _is_due():
                await run_check()
        except Exception:
            log.exception("new-release check crashed")
        await asyncio.sleep(_TICK_SECONDS)
