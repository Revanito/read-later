"""Storage for the Books tab: series, books (with a manual read order) and cached AI recommendations.

Read order is `books.sort_order` inside a series. It starts out as the Hardcover series order but is
yours to rearrange (prequels, novellas, chronological vs. publication order...). `position_label` is
just the series number ("1", "2.5") shown next to the title, and never drives ordering by itself
except when you hit "Reset to series order".
"""
import json
import re
import sqlite3
from datetime import datetime, timedelta, timezone

from db import get_conn

STATUSES = {
    "want": "To read",
    "reading": "Reading",
    "read": "Read",
    "skip": "Skipping",
}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS series (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    author TEXT,
    hc_id INTEGER,
    hc_slug TEXT,
    notes TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS books (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    series_id INTEGER,
    sort_order INTEGER NOT NULL DEFAULT 0,
    position_label TEXT,
    title TEXT NOT NULL,
    author TEXT,
    year INTEGER,
    cover_url TEXT,
    ol_key TEXT,
    hc_id INTEGER,
    hc_slug TEXT,
    subjects TEXT,
    status TEXT NOT NULL DEFAULT 'want',
    notes TEXT,
    added_at TEXT NOT NULL,
    finished_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_books_series ON books (series_id, sort_order);
CREATE TABLE IF NOT EXISTS followed_authors (
    hc_id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    slug TEXT,
    followed_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS new_releases (
    hc_id INTEGER PRIMARY KEY,
    title TEXT NOT NULL,
    author TEXT,
    release_date TEXT NOT NULL,
    cover_url TEXT,
    hc_slug TEXT,
    series_hc_id INTEGER,
    series_name TEXT,
    position TEXT,
    reason TEXT,
    found_at TEXT NOT NULL,
    dismissed INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS lookup_queue (
    book_id INTEGER PRIMARY KEY,
    added_at TEXT NOT NULL,
    tried_at TEXT
);
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT
);
CREATE TABLE IF NOT EXISTS book_recs (
    seed_type TEXT NOT NULL,
    seed_id INTEGER NOT NULL,
    payload TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (seed_type, seed_id)
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def init_books_db() -> None:
    with get_conn() as conn:
        conn.executescript(_SCHEMA)


_NUMBER_WORDS = {w: str(i) for i, w in enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve".split())}


def _norm_title(title: str) -> str:
    """Comparable title: lowercase, punctuation dropped, number words as digits - so
    "No Safe Ground, Part One" == "No Safe Ground: Part 1"."""
    words = re.sub(r"[^a-z0-9]+", " ", (title or "").lower()).split()
    return " ".join(_NUMBER_WORDS.get(w, w) for w in words)


# --- series ---------------------------------------------------------------------------------------

def list_series() -> list[dict]:
    """Every series with its books in read order, plus progress counts and the next unread book."""
    with get_conn() as conn:
        series = conn.execute("SELECT * FROM series ORDER BY name COLLATE NOCASE").fetchall()
        books = conn.execute(
            "SELECT * FROM books WHERE series_id IS NOT NULL ORDER BY series_id, sort_order, id"
        ).fetchall()
    by_series: dict[int, list[sqlite3.Row]] = {}
    for b in books:
        by_series.setdefault(b["series_id"], []).append(b)
    out = []
    for s in series:
        items = by_series.get(s["id"], [])
        counted = [b for b in items if b["status"] != "skip"]
        done = sum(1 for b in counted if b["status"] == "read")
        next_up = next((b for b in items if b["status"] in ("reading", "want")), None)
        out.append({
            "series": s,
            "books": items,
            "total": len(counted),
            "done": done,
            "reading": any(b["status"] == "reading" for b in items),
            "next_up": next_up,
        })
    return out


def get_series(series_id: int) -> sqlite3.Row | None:
    with get_conn() as conn:
        return conn.execute("SELECT * FROM series WHERE id = ?", (series_id,)).fetchone()


def all_series_names() -> list[sqlite3.Row]:
    with get_conn() as conn:
        return conn.execute("SELECT id, name FROM series ORDER BY name COLLATE NOCASE").fetchall()


def find_series_by_hc(hc_id: int) -> sqlite3.Row | None:
    with get_conn() as conn:
        return conn.execute("SELECT * FROM series WHERE hc_id = ?", (hc_id,)).fetchone()


def create_series(name: str, author: str = "", hc_id: int | None = None, hc_slug: str = "") -> int:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO series (name, author, hc_id, hc_slug, created_at) VALUES (?, ?, ?, ?, ?)",
            (name.strip() or "Untitled series", author.strip(), hc_id, hc_slug, _now()),
        )
        return cur.lastrowid


def update_series(series_id: int, name: str, author: str, notes: str) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE series SET name = ?, author = ?, notes = ? WHERE id = ?",
            (name.strip() or "Untitled series", author.strip(), notes.strip(), series_id),
        )


def link_series_hc(series_id: int, hc_id: int, hc_slug: str) -> None:
    with get_conn() as conn:
        conn.execute("UPDATE series SET hc_id = ?, hc_slug = ? WHERE id = ?", (hc_id, hc_slug, series_id))


def delete_series(series_id: int) -> None:
    with get_conn() as conn:
        ids = [r["id"] for r in conn.execute("SELECT id FROM books WHERE series_id = ?", (series_id,))]
        conn.execute("DELETE FROM books WHERE series_id = ?", (series_id,))
        conn.execute("DELETE FROM series WHERE id = ?", (series_id,))
        conn.execute("DELETE FROM book_recs WHERE seed_type = 'series' AND seed_id = ?", (series_id,))
        conn.executemany("DELETE FROM book_recs WHERE seed_type = 'book' AND seed_id = ?", [(i,) for i in ids])
        conn.executemany("DELETE FROM lookup_queue WHERE book_id = ?", [(i,) for i in ids])


def series_books(series_id: int) -> list[sqlite3.Row]:
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM books WHERE series_id = ? ORDER BY sort_order, id", (series_id,)
        ).fetchall()


def _next_sort_order(conn: sqlite3.Connection, series_id: int | None) -> int:
    if series_id is None:
        return 0
    row = conn.execute("SELECT MAX(sort_order) AS m FROM books WHERE series_id = ?", (series_id,)).fetchone()
    return (row["m"] if row["m"] is not None else -1) + 1


def _renumber(conn: sqlite3.Connection, ordered_ids: list[int]) -> None:
    conn.executemany("UPDATE books SET sort_order = ? WHERE id = ?", [(i, bid) for i, bid in enumerate(ordered_ids)])


def move_book(book_id: int, direction: str) -> None:
    with get_conn() as conn:
        b = conn.execute("SELECT series_id FROM books WHERE id = ?", (book_id,)).fetchone()
        if b is None or b["series_id"] is None:
            return
        ids = [r["id"] for r in conn.execute(
            "SELECT id FROM books WHERE series_id = ? ORDER BY sort_order, id", (b["series_id"],))]
        i = ids.index(book_id)
        j = i - 1 if direction == "up" else i + 1
        if 0 <= j < len(ids):
            ids[i], ids[j] = ids[j], ids[i]
        _renumber(conn, ids)


def set_order(series_id: int, ordered_ids: list[int]) -> None:
    """Apply a full drag-and-drop order. Unknown ids are ignored; books missing from the list keep
    their relative order at the end, so a stale page can't drop books out of the series."""
    with get_conn() as conn:
        current = [r["id"] for r in conn.execute(
            "SELECT id FROM books WHERE series_id = ? ORDER BY sort_order, id", (series_id,))]
        known = set(current)
        wanted = [i for i in dict.fromkeys(ordered_ids) if i in known]
        rest = [i for i in current if i not in set(wanted)]
        _renumber(conn, wanted + rest)


def reset_order(series_id: int) -> None:
    """Sort by series number; books without a numeric position keep their order, after the rest."""
    def key(row):
        try:
            return (0, float(row["position_label"]), row["sort_order"])
        except (TypeError, ValueError):
            return (1, 0.0, row["sort_order"])

    with get_conn() as conn:
        rows = conn.execute("SELECT id, position_label, sort_order FROM books WHERE series_id = ?",
                            (series_id,)).fetchall()
        _renumber(conn, [r["id"] for r in sorted(rows, key=key)])


# --- books ----------------------------------------------------------------------------------------

def find_book(hc_id: int | None = None, ol_key: str = "") -> sqlite3.Row | None:
    with get_conn() as conn:
        if hc_id:
            row = conn.execute("SELECT * FROM books WHERE hc_id = ?", (hc_id,)).fetchone()
            if row:
                return row
        if ol_key:
            return conn.execute("SELECT * FROM books WHERE ol_key = ?", (ol_key,)).fetchone()
    return None


def add_book(hit: dict, series_id: int | None = None, position_label: str = "", status: str = "want") -> int:
    status = status if status in STATUSES else "want"
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO books (series_id, sort_order, position_label, title, author, year, cover_url,
                   ol_key, hc_id, hc_slug, subjects, status, added_at, finished_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                series_id, _next_sort_order(conn, series_id), position_label or "",
                hit.get("title") or "Untitled", hit.get("author") or "", hit.get("year") or None,
                hit.get("cover_url") or "", hit.get("ol_key") or "", hit.get("hc_id") or None,
                hit.get("hc_slug") or "", ",".join(hit.get("subjects") or []), status, _now(),
                _now() if status == "read" else None,
            ),
        )
        return cur.lastrowid


def get_book(book_id: int) -> sqlite3.Row | None:
    with get_conn() as conn:
        return conn.execute("SELECT * FROM books WHERE id = ?", (book_id,)).fetchone()


def standalone_books() -> list[sqlite3.Row]:
    with get_conn() as conn:
        return conn.execute(
            """SELECT * FROM books WHERE series_id IS NULL
               ORDER BY CASE status WHEN 'reading' THEN 0 WHEN 'want' THEN 1 WHEN 'read' THEN 2 ELSE 3 END,
                        added_at DESC"""
        ).fetchall()


def set_status(book_id: int, status: str) -> None:
    if status not in STATUSES:
        return
    with get_conn() as conn:
        conn.execute(
            """UPDATE books SET status = ?,
                   finished_at = CASE WHEN ? = 'read' THEN COALESCE(finished_at, ?) ELSE NULL END
               WHERE id = ?""",
            (status, status, _now(), book_id),
        )


def mark_read_before(book_id: int) -> int:
    """Mark every book earlier in this book's read order as read (catching up on a long series in one
    click). Skipped books stay skipped and the clicked book itself is left alone. Returns how many
    books changed."""
    with get_conn() as conn:
        b = conn.execute("SELECT id, series_id, sort_order FROM books WHERE id = ?", (book_id,)).fetchone()
        if b is None or b["series_id"] is None:
            return 0
        # Same tie-break as the page's ORDER BY sort_order, id.
        cur = conn.execute(
            """UPDATE books SET status = 'read', finished_at = COALESCE(finished_at, ?)
               WHERE series_id = ? AND status IN ('want', 'reading')
                 AND (sort_order < ? OR (sort_order = ? AND id < ?))""",
            (_now(), b["series_id"], b["sort_order"], b["sort_order"], b["id"]),
        )
        return cur.rowcount


def update_book(book_id: int, title: str, author: str, year: int | None, position_label: str, notes: str) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE books SET title = ?, author = ?, year = ?, position_label = ?, notes = ? WHERE id = ?",
            (title.strip() or "Untitled", author.strip(), year, position_label.strip(), notes.strip(), book_id),
        )


def assign_series(book_id: int, series_id: int | None) -> None:
    with get_conn() as conn:
        b = conn.execute("SELECT series_id FROM books WHERE id = ?", (book_id,)).fetchone()
        if b is None or b["series_id"] == series_id:
            return
        conn.execute("UPDATE books SET series_id = ?, sort_order = ? WHERE id = ?",
                     (series_id, _next_sort_order(conn, series_id), book_id))


def delete_book(book_id: int) -> None:
    with get_conn() as conn:
        conn.execute("DELETE FROM books WHERE id = ?", (book_id,))
        conn.execute("DELETE FROM book_recs WHERE seed_type = 'book' AND seed_id = ?", (book_id,))
        conn.execute("DELETE FROM lookup_queue WHERE book_id = ?", (book_id,))


# --- manually added books -------------------------------------------------------------------------
# A manual book sits in lookup_queue until it's linked to Hardcover. The background check (watcher.py)
# retries the lookup; Open Library links fill in cover/year meanwhile but keep it queued for Hardcover.

def find_by_title(title: str) -> sqlite3.Row | None:
    key = _norm_title(title)
    with get_conn() as conn:
        for r in conn.execute("SELECT * FROM books"):
            if _norm_title(r["title"]) == key:
                return r
    return None


def queue_lookup(book_id: int) -> None:
    with get_conn() as conn:
        conn.execute("INSERT OR IGNORE INTO lookup_queue (book_id, added_at) VALUES (?, ?)", (book_id, _now()))


def is_pending_lookup(book_id: int) -> bool:
    with get_conn() as conn:
        return conn.execute(
            "SELECT 1 FROM lookup_queue q JOIN books b ON b.id = q.book_id WHERE q.book_id = ? AND b.hc_id IS NULL",
            (book_id,)).fetchone() is not None


def pending_lookups(limit: int, min_age_hours: float) -> list[sqlite3.Row]:
    """Queued manual books still missing a Hardcover link, not retried within min_age_hours."""
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=min_age_hours)).isoformat()
    with get_conn() as conn:
        return conn.execute(
            """SELECT b.* FROM lookup_queue q JOIN books b ON b.id = q.book_id
               WHERE b.hc_id IS NULL AND (q.tried_at IS NULL OR q.tried_at < ?)
               ORDER BY q.tried_at IS NOT NULL, q.tried_at LIMIT ?""",
            (cutoff, limit),
        ).fetchall()


def mark_lookup_tried(book_id: int) -> None:
    with get_conn() as conn:
        conn.execute("UPDATE lookup_queue SET tried_at = ? WHERE book_id = ?", (_now(), book_id))


def find_unlinked_by_title(title: str) -> sqlite3.Row | None:
    key = _norm_title(title)
    with get_conn() as conn:
        for r in conn.execute("SELECT * FROM books WHERE hc_id IS NULL"):
            if _norm_title(r["title"]) == key:
                return r
    return None


def link_book(book_id: int, hit: dict, source: str, position: str = "") -> None:
    """Attach a Hardcover ('hc') or Open Library ('ol') record to a book, filling only empty fields so
    nothing you typed by hand gets overwritten."""
    year = hit.get("year") or (int(hit["release_date"][:4]) if hit.get("release_date") else None)
    common = (hit.get("cover_url") or "", year, ",".join(hit.get("subjects") or []))
    with get_conn() as conn:
        if source == "hc":
            conn.execute(
                """UPDATE books SET hc_id = ?, hc_slug = ?,
                       cover_url = COALESCE(NULLIF(cover_url, ''), NULLIF(?, '')), year = COALESCE(year, ?),
                       subjects = COALESCE(NULLIF(subjects, ''), ?),
                       position_label = COALESCE(NULLIF(position_label, ''), ?) WHERE id = ?""",
                (hit["hc_id"], hit.get("hc_slug") or hit.get("slug") or "", *common, position or "", book_id),
            )
        else:
            conn.execute(
                """UPDATE books SET ol_key = ?,
                       cover_url = COALESCE(NULLIF(cover_url, ''), NULLIF(?, '')), year = COALESCE(year, ?),
                       subjects = COALESCE(NULLIF(subjects, ''), ?) WHERE id = ?""",
                (hit["ol_key"], *common, book_id),
            )


def place_by_position(book_id: int) -> None:
    """Move a newly added book to just before the first book with a higher series number, so a
    manual "#8" lands after #7 instead of at the very end. No-op without a numeric number."""
    with get_conn() as conn:
        b = conn.execute("SELECT series_id, position_label FROM books WHERE id = ?", (book_id,)).fetchone()
        if b is None or b["series_id"] is None:
            return
        try:
            p = float(b["position_label"])
        except (TypeError, ValueError):
            return
        rows = [r for r in conn.execute(
            "SELECT id, position_label FROM books WHERE series_id = ? ORDER BY sort_order, id", (b["series_id"],))
            if r["id"] != book_id]
        at = len(rows)
        for i, r in enumerate(rows):
            try:
                if float(r["position_label"]) > p:
                    at = i
                    break
            except (TypeError, ValueError):
                continue
        ids = [r["id"] for r in rows]
        ids.insert(at, book_id)
        _renumber(conn, ids)


def import_series_books(series_id: int, hc_books: list[dict]) -> int:
    """Merge Hardcover's book list into a local series. Existing books (matched by Hardcover id, then
    by normalized title) get their series number/cover/link filled in; missing ones are appended to the
    end of the read order. Returns how many books were added."""
    added = 0
    with get_conn() as conn:
        existing = conn.execute("SELECT * FROM books WHERE series_id = ?", (series_id,)).fetchall()
        by_hc = {r["hc_id"]: r for r in existing if r["hc_id"]}
        by_title = {_norm_title(r["title"]): r for r in existing}
        next_order = _next_sort_order(conn, series_id)
        for hb in hc_books:
            match = by_hc.get(hb["hc_id"]) or by_title.get(_norm_title(hb["title"]))
            if match:
                conn.execute(
                    """UPDATE books SET hc_id = COALESCE(hc_id, ?), hc_slug = COALESCE(NULLIF(hc_slug, ''), ?),
                           position_label = COALESCE(NULLIF(?, ''), position_label),
                           cover_url = COALESCE(NULLIF(cover_url, ''), ?),
                           year = COALESCE(year, ?) WHERE id = ?""",
                    (hb["hc_id"], hb["hc_slug"], hb.get("position", ""), hb["cover_url"], hb["year"], match["id"]),
                )
                continue
            conn.execute(
                """INSERT INTO books (series_id, sort_order, position_label, title, author, year, cover_url,
                       ol_key, hc_id, hc_slug, subjects, status, added_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, '', ?, ?, '', 'want', ?)""",
                (series_id, next_order, hb.get("position", ""), hb["title"], hb["author"], hb["year"],
                 hb["cover_url"], hb["hc_id"], hb["hc_slug"], _now()),
            )
            next_order += 1
            added += 1
    return added


def series_subjects(series_id: int) -> list[str]:
    seen: dict[str, None] = {}
    for b in series_books(series_id):
        for s in (b["subjects"] or "").split(","):
            if s.strip():
                seen.setdefault(s.strip(), None)
    return list(seen)[:10]


def library_overview(limit: int = 80) -> list[str]:
    """Short "Title - Author" lines for everything saved, used as do-not-recommend context."""
    with get_conn() as conn:
        series = conn.execute("SELECT name, author FROM series").fetchall()
        books = conn.execute("SELECT title, author FROM books WHERE series_id IS NULL").fetchall()
    lines = [f"{s['name']} (series) - {s['author'] or 'unknown'}" for s in series]
    lines += [f"{b['title']} - {b['author'] or 'unknown'}" for b in books]
    return lines[:limit]


# --- following & new releases ---------------------------------------------------------------------

def norm_name(name: str) -> str:
    """'James S. A. Corey' and 'James S.A. Corey' compare equal."""
    return re.sub(r"[^a-z0-9]+", "", (name or "").lower())


def follow_author(hc_id: int, name: str, slug: str = "") -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO followed_authors (hc_id, name, slug, followed_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(hc_id) DO UPDATE SET name = excluded.name, slug = excluded.slug",
            (hc_id, name, slug, _now()),
        )


def unfollow_author(hc_id: int) -> None:
    with get_conn() as conn:
        conn.execute("DELETE FROM followed_authors WHERE hc_id = ?", (hc_id,))


def followed_authors() -> list[sqlite3.Row]:
    with get_conn() as conn:
        return conn.execute("SELECT * FROM followed_authors ORDER BY name COLLATE NOCASE").fetchall()


def followed_name_map() -> dict[str, int]:
    return {norm_name(a["name"]): a["hc_id"] for a in followed_authors()}


def linked_series() -> list[sqlite3.Row]:
    with get_conn() as conn:
        return conn.execute(
            "SELECT id, name, hc_id FROM series WHERE hc_id IS NOT NULL ORDER BY name COLLATE NOCASE"
        ).fetchall()


def library_authors() -> list[str]:
    """Distinct author names across the library ("A, B" credits split), for follow suggestions."""
    with get_conn() as conn:
        rows = conn.execute("SELECT author FROM books UNION ALL SELECT author FROM series").fetchall()
    seen: dict[str, str] = {}
    for r in rows:
        for name in (r["author"] or "").split(","):
            name = name.strip()
            if name:
                seen.setdefault(norm_name(name), name)
    return sorted(seen.values(), key=str.lower)


def library_keys() -> tuple[set[int], set[str]]:
    """Hardcover ids and normalized titles of everything saved, to tell 'new to me' from 'already have'."""
    with get_conn() as conn:
        rows = conn.execute("SELECT hc_id, title FROM books").fetchall()
    return {r["hc_id"] for r in rows if r["hc_id"]}, {_norm_title(r["title"]) for r in rows}


def upsert_release(r: dict, reason: str) -> bool:
    """Store/refresh a release. Returns True when it's newly found. Dismissed ones stay dismissed."""
    with get_conn() as conn:
        exists = conn.execute("SELECT 1 FROM new_releases WHERE hc_id = ?", (r["hc_id"],)).fetchone()
        if exists:
            # Release dates and covers often get corrected after a book is first listed.
            conn.execute(
                """UPDATE new_releases SET title = ?, author = ?, release_date = ?,
                       cover_url = COALESCE(NULLIF(?, ''), cover_url), hc_slug = ?, series_hc_id = ?,
                       series_name = ?, position = ? WHERE hc_id = ?""",
                (r["title"], r["author"], r["release_date"], r["cover_url"], r["slug"], r["series_hc_id"],
                 r["series_name"], r["position"], r["hc_id"]),
            )
            return False
        conn.execute(
            """INSERT INTO new_releases (hc_id, title, author, release_date, cover_url, hc_slug, series_hc_id,
                   series_name, position, reason, found_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (r["hc_id"], r["title"], r["author"], r["release_date"], r["cover_url"], r["slug"], r["series_hc_id"],
             r["series_name"], r["position"], reason, _now()),
        )
        return True


def active_releases(today: str, recent_days: int = 14) -> list[dict]:
    """What the notification panel shows, newest first then upcoming:
    - books you don't have yet: out now, or coming soon;
    - books already in your library (e.g. listed in a series before release): only once they're out,
      for `recent_days` days, so release day still gets a heads-up."""
    hc_ids, titles = library_keys()
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM new_releases WHERE dismissed = 0").fetchall()
        local_series = {r["hc_id"]: r["id"] for r in conn.execute(
            "SELECT id, hc_id FROM series WHERE hc_id IS NOT NULL")}
    cutoff = (datetime.fromisoformat(today) - timedelta(days=recent_days)).date().isoformat()
    out = []
    for r in rows:
        owned = r["hc_id"] in hc_ids or _norm_title(r["title"]) in titles
        out_now = r["release_date"] <= today
        if owned and not (out_now and r["release_date"] >= cutoff):
            continue
        item = dict(r)
        item["owned"] = owned
        item["out_now"] = out_now
        item["local_series_id"] = local_series.get(r["series_hc_id"])
        item["saved_id"] = None
        if owned:
            b = find_book(r["hc_id"])
            item["saved_id"] = b["id"] if b else None
        out.append(item)
    # Out-now first (most recent release on top), then upcoming (soonest first).
    released = sorted([i for i in out if i["out_now"]], key=lambda i: i["release_date"], reverse=True)
    upcoming = sorted([i for i in out if not i["out_now"]], key=lambda i: i["release_date"])
    return released + upcoming


def dismiss_release(hc_id: int) -> None:
    with get_conn() as conn:
        conn.execute("UPDATE new_releases SET dismissed = 1 WHERE hc_id = ?", (hc_id,))


def meta_get(key: str) -> str | None:
    with get_conn() as conn:
        row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


def meta_set(key: str, value: str | None) -> None:
    with get_conn() as conn:
        conn.execute("INSERT INTO meta (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                     (key, value))


# --- recommendations cache ------------------------------------------------------------------------

def get_recs(seed_type: str, seed_id: int) -> dict | None:
    with get_conn() as conn:
        row = conn.execute("SELECT payload, created_at FROM book_recs WHERE seed_type = ? AND seed_id = ?",
                           (seed_type, seed_id)).fetchone()
    if row is None:
        return None
    return {"items": json.loads(row["payload"]), "created_at": row["created_at"]}


def save_recs(seed_type: str, seed_id: int, items: list[dict]) -> None:
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO book_recs (seed_type, seed_id, payload, created_at) VALUES (?, ?, ?, ?)
               ON CONFLICT(seed_type, seed_id) DO UPDATE SET payload = excluded.payload, created_at = excluded.created_at""",
            (seed_type, seed_id, json.dumps(items), _now()),
        )
