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
CREATE TABLE IF NOT EXISTS order_research (
    series_id INTEGER PRIMARY KEY,
    status TEXT NOT NULL,
    model TEXT,
    payload TEXT,
    error TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS book_recs (
    seed_type TEXT NOT NULL,
    seed_id INTEGER NOT NULL,
    payload TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (seed_type, seed_id)
);
-- Reading maps (see the "reading maps" section below and presets.py).
CREATE TABLE IF NOT EXISTS map_meta (
    series_id INTEGER PRIMARY KEY,
    preset TEXT,
    col INTEGER NOT NULL DEFAULT 150,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS map_sections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    series_id INTEGER NOT NULL,
    sort_order INTEGER NOT NULL DEFAULT 0,
    kind TEXT NOT NULL,
    name TEXT NOT NULL,
    color TEXT NOT NULL,
    hint TEXT,
    auto_wire INTEGER NOT NULL DEFAULT 1,
    exit_book INTEGER
);
CREATE TABLE IF NOT EXISTS map_groups (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    section_id INTEGER NOT NULL,
    sort_order INTEGER NOT NULL DEFAULT 0,
    name TEXT NOT NULL,
    color TEXT NOT NULL,
    hint TEXT,
    loose INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS map_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    group_id INTEGER NOT NULL,
    book_id INTEGER NOT NULL,
    sort_order INTEGER NOT NULL DEFAULT 0,
    row INTEGER NOT NULL DEFAULT 0,
    indent INTEGER NOT NULL DEFAULT 0,
    sub TEXT,
    lines TEXT,
    stories TEXT,
    list_order INTEGER
);
CREATE INDEX IF NOT EXISTS idx_map_entries_book ON map_entries (book_id);
CREATE TABLE IF NOT EXISTS map_rows (
    group_id INTEGER NOT NULL,
    row INTEGER NOT NULL,
    PRIMARY KEY (group_id, row)
);
CREATE TABLE IF NOT EXISTS map_links (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    series_id INTEGER NOT NULL,
    from_ref TEXT NOT NULL,
    to_ref TEXT NOT NULL,
    kind TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS map_lines (
    series_id INTEGER NOT NULL,
    code TEXT NOT NULL,
    name TEXT NOT NULL,
    fill TEXT NOT NULL,
    outline TEXT NOT NULL,
    PRIMARY KEY (series_id, code)
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# Columns added after a table first shipped: CREATE TABLE IF NOT EXISTS won't add them to an existing
# database, so they're added here on start-up.
_ADDED_COLUMNS = {
    "books": {"title_fr": "TEXT", "fr_source": "TEXT", "fr_checked_at": "TEXT"},
    "series": {"name_fr": "TEXT", "merged_into": "INTEGER"},
    "map_sections": {"name_fr": "TEXT", "hint_fr": "TEXT"},
    "map_groups": {"name_fr": "TEXT", "hint_fr": "TEXT"},
    "map_entries": {"sub_fr": "TEXT"},
    "map_lines": {"name_fr": "TEXT"},
}


def init_books_db() -> None:
    with get_conn() as conn:
        conn.executescript(_SCHEMA)
        for table, cols in _ADDED_COLUMNS.items():
            have = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
            for col, typ in cols.items():
                if col not in have:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {typ}")


# --- French titles / names -------------------------------------------------------------------------
# A book's French title comes from Hardcover (most popular French edition, fr_source='hc') or is typed
# in by hand (fr_source='manual', never overwritten). Display helpers pick it when the UI is French.

def _row_get(row, key):
    try:
        return row[key]
    except (KeyError, IndexError):
        return None


def display_title(row) -> str:
    from i18n import get_lang
    if get_lang() == "fr":
        fr = _row_get(row, "title_fr")
        if fr:
            return fr
    return _row_get(row, "title") or ""


def display_name(row) -> str:
    from i18n import get_lang
    if row is None:
        return ""
    if get_lang() == "fr":
        fr = _row_get(row, "name_fr")
        if fr:
            return fr
    return _row_get(row, "name") or ""


def books_needing_fr(limit: int, max_age_days: int = 30, series_id: int | None = None) -> list[sqlite3.Row]:
    """Hardcover-linked books whose French title was never looked up (or long ago), skipping manual ones."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=max_age_days)).isoformat()
    sql = """SELECT * FROM books WHERE hc_id IS NOT NULL AND COALESCE(fr_source, '') != 'manual'
             AND (fr_checked_at IS NULL OR fr_checked_at < ?)"""
    args: list = [cutoff]
    if series_id is not None:
        sql = """SELECT * FROM books WHERE hc_id IS NOT NULL AND COALESCE(fr_source, '') != 'manual'
                 AND series_id = ?"""
        args = [series_id]
    with get_conn() as conn:
        return conn.execute(sql + " ORDER BY fr_checked_at IS NOT NULL, id LIMIT ?", (*args, limit)).fetchall()


def store_fr_titles(found: dict[int, str], checked_ids: list[int]) -> int:
    """Save looked-up French titles (by book id); mark every checked book so it isn't retried soon.
    Manual titles are never touched. Returns how many titles were set or changed."""
    changed = 0
    now = _now()
    with get_conn() as conn:
        for book_id in checked_ids:
            row = conn.execute("SELECT title_fr, fr_source FROM books WHERE id = ?", (book_id,)).fetchone()
            if row is None or row["fr_source"] == "manual":
                continue
            title = found.get(book_id)
            if title:
                if row["title_fr"] != title:
                    changed += 1
                conn.execute("UPDATE books SET title_fr = ?, fr_source = 'hc', fr_checked_at = ? WHERE id = ?",
                             (title, now, book_id))
            else:
                conn.execute("UPDATE books SET fr_checked_at = ? WHERE id = ?", (now, book_id))
    return changed


def set_manual_fr_title(book_id: int, title_fr: str) -> None:
    """A French title typed on the book page. Empty clears it and lets Hardcover fill it again."""
    title_fr = title_fr.strip()
    with get_conn() as conn:
        if title_fr:
            conn.execute("UPDATE books SET title_fr = ?, fr_source = 'manual' WHERE id = ?", (title_fr, book_id))
        else:
            conn.execute(
                """UPDATE books SET title_fr = CASE WHEN fr_source = 'manual' THEN NULL ELSE title_fr END,
                       fr_source = CASE WHEN fr_source = 'manual' THEN NULL ELSE fr_source END,
                       fr_checked_at = CASE WHEN fr_source = 'manual' THEN NULL ELSE fr_checked_at END
                   WHERE id = ?""", (book_id,))


def set_series_name_fr(series_id: int, name_fr: str) -> None:
    with get_conn() as conn:
        conn.execute("UPDATE series SET name_fr = ? WHERE id = ?", (name_fr.strip() or None, series_id))


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
        # Series folded into another one (merged_into) stay only for new-release watching: no card.
        series = conn.execute("SELECT * FROM series WHERE merged_into IS NULL ORDER BY name COLLATE NOCASE").fetchall()
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
        return conn.execute("SELECT id, name FROM series WHERE merged_into IS NULL ORDER BY name COLLATE NOCASE").fetchall()


def find_series_by_hc(hc_id: int) -> sqlite3.Row | None:
    """The local series for a Hardcover series; a folded series answers with the one it lives in now."""
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM series WHERE hc_id = ?", (hc_id,)).fetchone()
        if row is not None and row["merged_into"]:
            target = conn.execute("SELECT * FROM series WHERE id = ?", (row["merged_into"],)).fetchone()
            return target or row
        return row


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
        # Series folded into this one are empty shells kept for release watching: they go with it.
        conn.execute("DELETE FROM series WHERE merged_into = ?", (series_id,))
        conn.execute("DELETE FROM book_recs WHERE seed_type = 'series' AND seed_id = ?", (series_id,))
        conn.execute("DELETE FROM order_research WHERE series_id = ?", (series_id,))
        conn.executemany("DELETE FROM book_recs WHERE seed_type = 'book' AND seed_id = ?", [(i,) for i in ids])
        conn.executemany("DELETE FROM lookup_queue WHERE book_id = ?", [(i,) for i in ids])
        _clear_map(conn, series_id)
        conn.executemany("DELETE FROM map_entries WHERE book_id = ?", [(i,) for i in ids])


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


def all_books() -> list[sqlite3.Row]:
    with get_conn() as conn:
        return conn.execute("SELECT * FROM books ORDER BY id").fetchall()


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
        _forget_book_in_maps(conn, book_id)


def delete_book(book_id: int) -> None:
    with get_conn() as conn:
        conn.execute("DELETE FROM books WHERE id = ?", (book_id,))
        conn.execute("DELETE FROM book_recs WHERE seed_type = 'book' AND seed_id = ?", (book_id,))
        conn.execute("DELETE FROM lookup_queue WHERE book_id = ?", (book_id,))
        _forget_book_in_maps(conn, book_id)


# --- folding one series into another ---------------------------------------------------------------
# "The Horus Heresy: Primarchs" imported from Hardcover can be folded into "The Horus Heresy + Siege of
# Terra": its books move over, copies of a book already there are merged, and the emptied series stays
# behind with merged_into set. It no longer shows anywhere, but its Hardcover link keeps feeding the
# new-release check, and adding a release from it goes to the series it was folded into.

STATUS_RANK = {"want": 0, "skip": 1, "reading": 2, "read": 3}


def same_book(a, b) -> bool:
    """Two rows for the same book: same Hardcover id, or same title by the same author."""
    if a["hc_id"] and b["hc_id"]:
        return a["hc_id"] == b["hc_id"]
    return titles_match(a["title"], b["title"]) and same_author(a["author"], b["author"])


def merge_book_into(keep_id: int, dup_id: int) -> None:
    """Fold a duplicate row into the one that stays: the further-along status wins, missing details
    (Hardcover link, cover, year, French title) are filled in, then the duplicate is deleted."""
    keep, dup = get_book(keep_id), get_book(dup_id)
    if keep is None or dup is None or keep_id == dup_id:
        return
    status, finished = keep["status"], keep["finished_at"]
    if STATUS_RANK.get(dup["status"], 0) > STATUS_RANK.get(status, 0):
        status, finished = dup["status"], dup["finished_at"]
    fr_title, fr_source = keep["title_fr"], keep["fr_source"]
    if dup["title_fr"] and (not fr_title or (dup["fr_source"] == "manual" and fr_source != "manual")):
        fr_title, fr_source = dup["title_fr"], dup["fr_source"]
    with get_conn() as conn:
        conn.execute(
            """UPDATE books SET status = ?, finished_at = ?, title_fr = ?, fr_source = ?,
                   hc_id = COALESCE(hc_id, ?), hc_slug = COALESCE(NULLIF(hc_slug, ''), ?),
                   cover_url = COALESCE(NULLIF(cover_url, ''), ?), year = COALESCE(year, ?)
               WHERE id = ?""",
            (status, finished, fr_title, fr_source, dup["hc_id"], dup["hc_slug"], dup["cover_url"], dup["year"],
             keep_id))
        if keep["hc_id"] is None and dup["hc_id"] is not None:
            conn.execute("DELETE FROM lookup_queue WHERE book_id = ?", (keep_id,))
    delete_book(dup_id)


def fold_series(src_id: int, dst_id: int) -> dict:
    """Move every book of series `src_id` into `dst_id` (merging copies of books already there) and
    mark `src_id` as folded into it. Returns {"moved": n, "merged": n}."""
    stats = {"moved": 0, "merged": 0}
    if src_id == dst_id or get_series(src_id) is None or get_series(dst_id) is None:
        return stats
    dst_books = list(series_books(dst_id))
    for b in series_books(src_id):
        twin = next((d for d in dst_books if same_book(d, b)), None)
        if twin is not None:
            merge_book_into(twin["id"], b["id"])
            stats["merged"] += 1
        else:
            set_book_series_raw(b["id"], dst_id)
            dst_books.append(b)
            stats["moved"] += 1
    with get_conn() as conn:
        _clear_map(conn, src_id)
        conn.execute("UPDATE series SET merged_into = ? WHERE id = ?", (dst_id, src_id))
        conn.execute("UPDATE series SET merged_into = ? WHERE merged_into = ?", (dst_id, src_id))
        conn.execute("DELETE FROM book_recs WHERE seed_type = 'series' AND seed_id = ?", (src_id,))
    renumber_from_map(dst_id)
    return stats


def get_research(series_id: int) -> sqlite3.Row | None:
    with get_conn() as conn:
        return conn.execute("SELECT * FROM order_research WHERE series_id = ?", (series_id,)).fetchone()


def start_research(series_id: int, model: str) -> None:
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO order_research (series_id, status, model, payload, error, created_at)
               VALUES (?, 'running', ?, NULL, NULL, ?)
               ON CONFLICT(series_id) DO UPDATE SET status = 'running', model = excluded.model, payload = NULL,
                   error = NULL, created_at = excluded.created_at""",
            (series_id, model, _now()))


def finish_research(series_id: int, payload: str | None, error: str | None) -> None:
    with get_conn() as conn:
        conn.execute("UPDATE order_research SET status = ?, payload = ?, error = ? WHERE series_id = ?",
                     ("failed" if error else "done", payload, error, series_id))


def delete_research(series_id: int) -> None:
    with get_conn() as conn:
        conn.execute("DELETE FROM order_research WHERE series_id = ?", (series_id,))


_LINE_COLORS = ["#7fb6d8", "#d4934a", "#9a9ccf", "#5ec2a3", "#d77fd2", "#d9c04a", "#d07a7a", "#8fb26f"]


def build_line_map(series_id: int, lines: list[tuple[str, list[tuple[int, str]]]]) -> None:
    """Replace the series' map with one single-column section per line (main series, then each
    spin-off), chained one after the other. lines = [(name, [(book_id, label), ...]), ...]"""
    clear_map(series_id)
    set_map_meta(series_id, None, 150)
    for k, (name, entries) in enumerate(lines):
        color = _LINE_COLORS[k % len(_LINE_COLORS)]
        sid = create_section(series_id, "solo", name, color, auto_wire=True)
        gid = create_group(sid, name, color)
        for bid, sub in entries:
            add_entry(gid, bid, sub=sub)
    renumber_from_map(series_id)


def folded_series(series_id: int) -> list[sqlite3.Row]:
    with get_conn() as conn:
        return conn.execute("SELECT * FROM series WHERE merged_into = ? ORDER BY name COLLATE NOCASE",
                            (series_id,)).fetchall()


# --- manually added books -------------------------------------------------------------------------
# A manual book sits in lookup_queue until it's linked to Hardcover. The background check (watcher.py)
# retries the lookup; Open Library links fill in cover/year meanwhile but keep it queued for Hardcover.

# Matching books by title alone is wrong as soon as two authors used the same title (Chris Fox's and
# D.J. Molles's "Sanctuary"), so title matches also need the same author or the same series.

# Marketing tails that databases glue onto titles: "Sanctuary: A Remaining Universe Novel",
# "The Valley: A Lee Harden Novel", "Book 3 - A Saga". Only tails naming a novel/book/story are cut.
_TITLE_TAIL = re.compile(r"\s*(?::|\s-\s|\s–\s)\s*(?:an?\s+|the\s+)?[^:]*\b(?:novel|novella|book|story|saga)\b[^:]*$", re.I)


def core_title(title: str) -> str:
    return _norm_title(_TITLE_TAIL.sub("", title or "") or title or "")


def titles_match(a: str, b: str) -> bool:
    return _norm_title(a) == _norm_title(b) or core_title(a) == core_title(b)


def _title_variants(title: str) -> set[str]:
    """A title plus the part after its last colon/dash ("Arisen: Genesis" -> "genesis",
    "Arisen: Operators, Volume II- Pipe Hitters" -> "pipe hitters"), when that part is a real title."""
    out = {_norm_title(title), core_title(title)}
    tail = re.split(r"\s*(?::|\s-\s|\s–\s|-\s)\s*", title or "")[-1]
    if tail and tail != title and len(_norm_title(tail)) >= 4:
        out.add(_norm_title(tail))
    return {v for v in out if v}


def loose_title_match(mine: str, proposed: str) -> bool:
    """Same book under a series-prefixed or subtitled name: used to line up an AI-proposed reading
    order with the books in a series (never to decide duplicates on its own)."""
    return titles_match(mine, proposed) or bool(_title_variants(mine) & _title_variants(proposed))


def head_title_match(mine: str, proposed: str) -> bool:
    """One side is the other minus its subtitle ("The Shattering" / "The Shattering: Prelude to
    Cataclysm"). Weaker than loose_title_match: only tried for books nothing else matched."""
    def head(t):
        return _norm_title(re.split(r"\s*:\s*", t or "", maxsplit=1)[0]) if ":" in (t or "") else ""
    a, b = head(mine), head(proposed)
    return bool((a and len(a) >= 4 and a == _norm_title(proposed)) or (b and len(b) >= 4 and b == _norm_title(mine)))


def author_surnames(authors: str | None) -> set[str]:
    out = set()
    for name in (authors or "").split(","):
        parts = re.sub(r"[^a-z ]+", " ", name.lower()).split()
        if parts:
            out.add(parts[-1])
    return out


def same_author(mine: str | None, theirs: str | None) -> bool:
    """Surname overlap: 'DJ Molles' matches 'D.J. Molles'. An empty author never matches anything."""
    return bool(author_surnames(mine) & author_surnames(theirs))


def find_duplicate(title: str, author: str = "", series_id: int | None = None) -> sqlite3.Row | None:
    """The book a manual add would duplicate: same title (tails like ": A … Novel" ignored) and either the
    same author, or the same series. Same title by someone else, elsewhere, is a different book."""
    with get_conn() as conn:
        for r in conn.execute("SELECT * FROM books"):
            if not titles_match(r["title"], title):
                continue
            if same_author(author, r["author"]) or (series_id is not None and r["series_id"] == series_id):
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


def find_unlinked_by_title(title: str, author: str | None = None) -> sqlite3.Row | None:
    """A not-yet-linked book with this title; with `author`, only one by the same author."""
    with get_conn() as conn:
        for r in conn.execute("SELECT * FROM books WHERE hc_id IS NULL"):
            if titles_match(r["title"], title) and (author is None or same_author(r["author"], author)):
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


def library_keys() -> tuple[set[int], dict[str, list[str]]]:
    """Hardcover ids of everything saved, and core title -> authors, to tell 'new to me' from 'already
    have'. Use owns_title() on the second: a title only counts as owned by the same author."""
    with get_conn() as conn:
        rows = conn.execute("SELECT hc_id, title, author FROM books").fetchall()
    titles: dict[str, list[str]] = {}
    for r in rows:
        titles.setdefault(core_title(r["title"]), []).append(r["author"] or "")
    return {r["hc_id"] for r in rows if r["hc_id"]}, titles


def owns_title(titles: dict[str, list[str]], title: str, author: str | None) -> bool:
    return any(same_author(a, author) for a in titles.get(core_title(title), []))


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
            "SELECT COALESCE(merged_into, id) AS id, hc_id FROM series WHERE hc_id IS NOT NULL")}
    cutoff = (datetime.fromisoformat(today) - timedelta(days=recent_days)).date().isoformat()
    out = []
    for r in rows:
        owned = r["hc_id"] in hc_ids or owns_title(titles, r["title"], r["author"])
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


# --- reading maps ---------------------------------------------------------------------------------
# A series can have a reading map: ordered sections, each holding groups (arc columns for kind "cols",
# exactly one group otherwise), each group holding entries (books, by row for "chains"). Links wire
# things together; their refs are "book:<id>", "gate:<section id>" (a section's header),
# "colsfirst:<section id>" / "colslast:<section id>" (the first/last book of every arc in a cols
# section). Kinds: seq/rowseq are implied inside groups; stored links are elbow, branch, cross, vdash,
# toGate, fromGate and logic (not drawn, only decides "Up next").

SECTION_KINDS = {
    "cols": "Arc columns",
    "row": "Row (left to right)",
    "chains": "Rows with branches",
    "solo": "Single column",
    "loose": "Any order",
}
LINK_KINDS = {"cross": "Crossover (dashed, side to side)", "vdash": "Side story (dashed)", "elbow": "Path (solid)"}


def _clear_map(conn: sqlite3.Connection, series_id: int) -> None:
    sections = [r["id"] for r in conn.execute("SELECT id FROM map_sections WHERE series_id = ?", (series_id,))]
    for sid in sections:
        groups = [r["id"] for r in conn.execute("SELECT id FROM map_groups WHERE section_id = ?", (sid,))]
        conn.executemany("DELETE FROM map_entries WHERE group_id = ?", [(g,) for g in groups])
        conn.executemany("DELETE FROM map_rows WHERE group_id = ?", [(g,) for g in groups])
        conn.execute("DELETE FROM map_groups WHERE section_id = ?", (sid,))
    conn.execute("DELETE FROM map_sections WHERE series_id = ?", (series_id,))
    conn.execute("DELETE FROM map_links WHERE series_id = ?", (series_id,))
    conn.execute("DELETE FROM map_lines WHERE series_id = ?", (series_id,))
    conn.execute("DELETE FROM map_meta WHERE series_id = ?", (series_id,))


def _forget_book_in_maps(conn: sqlite3.Connection, book_id: int) -> None:
    conn.execute("DELETE FROM map_entries WHERE book_id = ?", (book_id,))
    ref = f"book:{book_id}"
    conn.execute("DELETE FROM map_links WHERE from_ref = ? OR to_ref = ?", (ref, ref))
    conn.execute("UPDATE map_sections SET exit_book = NULL WHERE exit_book = ?", (book_id,))


def clear_map(series_id: int) -> None:
    with get_conn() as conn:
        _clear_map(conn, series_id)


def has_map(series_id: int) -> bool:
    with get_conn() as conn:
        return conn.execute("SELECT 1 FROM map_sections WHERE series_id = ? LIMIT 1", (series_id,)).fetchone() is not None


def set_map_meta(series_id: int, preset: str | None, col: int = 150) -> None:
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO map_meta (series_id, preset, col, created_at) VALUES (?, ?, ?, ?)
               ON CONFLICT(series_id) DO UPDATE SET preset = excluded.preset, col = excluded.col""",
            (series_id, preset, col, _now()),
        )


def get_map_meta(series_id: int) -> sqlite3.Row | None:
    with get_conn() as conn:
        return conn.execute("SELECT * FROM map_meta WHERE series_id = ?", (series_id,)).fetchone()


def set_map_lines(series_id: int, lines: dict) -> None:
    with get_conn() as conn:
        conn.execute("DELETE FROM map_lines WHERE series_id = ?", (series_id,))
        conn.executemany(
            "INSERT INTO map_lines (series_id, code, name, fill, outline, name_fr) VALUES (?, ?, ?, ?, ?, ?)",
            [(series_id, code, v[0], v[1], v[2], v[3] if len(v) > 3 else None) for code, v in lines.items()],
        )


def title_keys(title: str) -> list[str]:
    """Match keys for a title: normalized, plus the same without a leading "the"."""
    n = _norm_title(title)
    return [n, n[4:]] if n.startswith("the ") else [n]


def related_series(conn: sqlite3.Connection, series_id: int, match_words: list[str]) -> list[int]:
    """Other (not folded) series whose name contains one of `match_words`, oldest first."""
    return [r["id"] for r in conn.execute("SELECT id, name FROM series WHERE merged_into IS NULL ORDER BY id")
            if r["id"] != series_id and any(w in r["name"].lower() for w in match_words)]


def related_series_ids(series_id: int, match_words: list[str]) -> list[int]:
    with get_conn() as conn:
        return related_series(conn, series_id, match_words)


def preset_candidates(series_id: int, match_words: list[str]) -> dict[str, list[sqlite3.Row]]:
    """Books a preset may claim: the target series first, then series whose name contains one of
    `match_words`, then standalone books. Indexed by title key."""
    with get_conn() as conn:
        related = related_series(conn, series_id, match_words)
        rows = list(conn.execute("SELECT * FROM books WHERE series_id = ? ORDER BY sort_order, id", (series_id,)))
        for sid in related:
            rows += list(conn.execute("SELECT * FROM books WHERE series_id = ? ORDER BY sort_order, id", (sid,)))
        rows += list(conn.execute("SELECT * FROM books WHERE series_id IS NULL ORDER BY id"))
    index: dict[str, list[sqlite3.Row]] = {}
    for r in rows:
        for k in title_keys(r["title"]):
            index.setdefault(k, []).append(r)
    return index


def set_book_series_raw(book_id: int, series_id: int) -> None:
    with get_conn() as conn:
        conn.execute("UPDATE books SET series_id = ? WHERE id = ?", (series_id, book_id))
        _forget_book_in_maps(conn, book_id)


def copy_cover(src_id: int, dst_id: int) -> None:
    with get_conn() as conn:
        conn.execute(
            """UPDATE books SET cover_url = COALESCE(NULLIF(cover_url, ''), (SELECT cover_url FROM books WHERE id = ?))
               WHERE id = ?""", (src_id, dst_id))


def mark_unread(book_ids: list[int]) -> None:
    with get_conn() as conn:
        conn.executemany("UPDATE books SET status = 'want', finished_at = NULL WHERE id = ?", [(i,) for i in book_ids])


def _next_map_order(conn: sqlite3.Connection, table: str, parent_col: str, parent_id: int) -> int:
    row = conn.execute(f"SELECT MAX(sort_order) AS m FROM {table} WHERE {parent_col} = ?", (parent_id,)).fetchone()
    return (row["m"] if row["m"] is not None else -1) + 1


def create_section(series_id: int, kind: str, name: str, color: str, hint: str = "", auto_wire: bool = True,
                   name_fr: str = "", hint_fr: str = "") -> int:
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO map_sections (series_id, sort_order, kind, name, color, hint, auto_wire, name_fr, hint_fr)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (series_id, _next_map_order(conn, "map_sections", "series_id", series_id), kind,
             name.strip() or "Untitled", color, hint.strip(), 1 if auto_wire else 0,
             name_fr.strip() or None, hint_fr.strip() or None),
        )
        return cur.lastrowid


def create_group(section_id: int, name: str, color: str, hint: str = "", loose: bool = False,
                 name_fr: str = "", hint_fr: str = "") -> int:
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO map_groups (section_id, sort_order, name, color, hint, loose, name_fr, hint_fr)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (section_id, _next_map_order(conn, "map_groups", "section_id", section_id), name.strip() or "Untitled",
             color, hint.strip(), 1 if loose else 0, name_fr.strip() or None, hint_fr.strip() or None),
        )
        return cur.lastrowid


def set_row_noseq(group_id: int, row: int) -> None:
    with get_conn() as conn:
        conn.execute("INSERT OR IGNORE INTO map_rows (group_id, row) VALUES (?, ?)", (group_id, row))


def set_section_exit(section_id: int, book_id: int | None) -> None:
    with get_conn() as conn:
        conn.execute("UPDATE map_sections SET exit_book = ? WHERE id = ?", (book_id, section_id))


def add_entry(group_id: int, book_id: int, row: int = 0, indent: int = 0, sub: str = "", lines: str = "",
              stories: str = "", list_order: int | None = None, sub_fr: str = "") -> int:
    with get_conn() as conn:
        conn.execute("DELETE FROM map_entries WHERE book_id = ?", (book_id,))
        cur = conn.execute(
            """INSERT INTO map_entries (group_id, book_id, sort_order, row, indent, sub, lines, stories, list_order, sub_fr)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (group_id, book_id, _next_map_order(conn, "map_entries", "group_id", group_id), row, indent, sub, lines,
             stories, list_order, sub_fr or None),
        )
        return cur.lastrowid


def add_link(series_id: int, from_ref: str, to_ref: str, kind: str) -> None:
    with get_conn() as conn:
        conn.execute("INSERT INTO map_links (series_id, from_ref, to_ref, kind) VALUES (?, ?, ?, ?)",
                     (series_id, from_ref, to_ref, kind))


def _map_structure(conn: sqlite3.Connection, series_id: int):
    sections = conn.execute("SELECT * FROM map_sections WHERE series_id = ? ORDER BY sort_order, id",
                            (series_id,)).fetchall()
    out = []
    for s in sections:
        groups = []
        for g in conn.execute("SELECT * FROM map_groups WHERE section_id = ? ORDER BY sort_order, id", (s["id"],)):
            entries = conn.execute(
                """SELECT e.* FROM map_entries e JOIN books b ON b.id = e.book_id
                   WHERE e.group_id = ? AND b.series_id = ? ORDER BY e.row, e.sort_order, e.id""",
                (g["id"], series_id)).fetchall()
            noseq = {r["row"] for r in conn.execute("SELECT row FROM map_rows WHERE group_id = ?", (g["id"],))}
            groups.append({"g": g, "entries": entries, "noseq": noseq})
        out.append({"s": s, "groups": groups})
    return out


def renumber_from_map(series_id: int) -> None:
    """Make the plain series order (Order view, "Read all above") follow the map's list order."""
    with get_conn() as conn:
        ordered: list[int] = []
        for sec in _map_structure(conn, series_id):
            entries = [e for g in sec["groups"] for e in g["entries"]]
            if sec["s"]["kind"] == "cols" and any(e["list_order"] is not None for e in entries):
                entries.sort(key=lambda e: (e["list_order"] is None, e["list_order"] or 0))
            ordered += [e["book_id"] for e in entries if e["book_id"] not in ordered]
        rest = [r["id"] for r in conn.execute("SELECT id FROM books WHERE series_id = ? ORDER BY sort_order, id",
                                              (series_id,)) if r["id"] not in set(ordered)]
        _renumber(conn, ordered + rest)


def map_json(series_id: int) -> dict | None:
    """Everything the series page's map/list views need, as plain JSON."""
    with get_conn() as conn:
        structure = _map_structure(conn, series_id)
        if not structure:
            return None
        books = {b["id"]: b for b in conn.execute(
            "SELECT * FROM books WHERE series_id = ? ORDER BY sort_order, id", (series_id,))}
        links = conn.execute("SELECT * FROM map_links WHERE series_id = ? ORDER BY id", (series_id,)).fetchall()
        line_rows = conn.execute("SELECT * FROM map_lines WHERE series_id = ?", (series_id,)).fetchall()
        meta = conn.execute("SELECT * FROM map_meta WHERE series_id = ?", (series_id,)).fetchone()

    from i18n import _, get_lang
    fr = get_lang() == "fr"

    def loc(row, field):
        """The French variant of a field when the UI is French and one exists."""
        if fr:
            v = _row_get(row, f"{field}_fr")
            if v:
                return v
        return _row_get(row, field) or ""

    lines = {r["code"]: [loc(r, "name"), r["fill"], r["outline"]] for r in line_rows}

    def default_sub(b):
        return f"#{b['position_label']}" if b["position_label"] else (str(b["year"]) if b["year"] else "")

    def node(b, e=None):
        return {
            "id": b["id"], "title": display_title(b), "title_orig": b["title"], "status": b["status"],
            "cover": b["cover_url"] or "", "sub": (loc(e, "sub") if e else "") or default_sub(b),
            "lines": ((e["lines"] if e else "") or "").split(),
            "stories": [x for x in ((e["stories"] if e else "") or "").split("\n") if x],
            "o": e["list_order"] if e else None, "entry": e["id"] if e else None,
        }

    nodes, placed, sections, sec_index = {}, set(), [], {}
    for i, sec in enumerate(structure):
        s = sec["s"]
        sec_index[s["id"]] = i
        groups = []
        for g in sec["groups"]:
            rows: dict[int, dict] = {}
            for e in g["entries"]:
                b = books[e["book_id"]]
                r = rows.setdefault(e["row"], {"indent": e["indent"], "seq": e["row"] not in g["noseq"], "ids": []})
                r["ids"].append(b["id"])
                placed.add(b["id"])
                nodes[b["id"]] = node(b, e)
            groups.append({"id": g["g"]["id"], "name": loc(g["g"], "name"), "color": g["g"]["color"],
                           "hint": loc(g["g"], "hint"), "loose": bool(g["g"]["loose"]) or s["kind"] == "loose",
                           "rows": [rows[k] for k in sorted(rows)] or [{"indent": 0, "seq": True, "ids": []}]})
        sections.append({"id": s["id"], "kind": s["kind"], "name": loc(s, "name"), "color": s["color"], "hint": loc(s, "hint"),
                         "auto": bool(s["auto_wire"]), "exit": s["exit_book"] if s["exit_book"] in placed else None,
                         "groups": groups})

    unplaced = [b for b in books.values() if b["id"] not in placed]
    for b in unplaced:
        nodes[b["id"]] = node(b)
    if unplaced:
        not_placed = _("Not placed in the map yet")
        sections.append({"id": None, "kind": "loose", "name": not_placed, "color": "#7f8a99",
                         "hint": _("Books in this series the map doesn't include. Place them from Edit map, or leave them here."),
                         "auto": False, "exit": None, "virtual": True,
                         "groups": [{"id": None, "name": not_placed, "color": "#7f8a99", "hint": "",
                                     "loose": True, "rows": [{"indent": 0, "seq": False, "ids": [b["id"] for b in unplaced]}]}]})

    def conv(ref: str) -> str | None:
        kind, _, rest = ref.partition(":")
        if not rest.isdigit():
            return None
        if kind == "book":
            return f"b:{rest}" if int(rest) in placed else None
        if int(rest) not in sec_index:
            return None
        return f"{kind}:{sec_index[int(rest)]}"

    out_links = []
    for l in links:
        a, b = conv(l["from_ref"]), conv(l["to_ref"])
        if a and b:
            out_links.append([a, b, l["kind"]])

    # Auto-wiring between consecutive sections (anything built by hand): previous section's exit
    # feeds the next section's start; arc columns merge into the next section's header.
    real = [i for i, s in enumerate(sections) if not s.get("virtual") and s["kind"] != "loose"]
    for prev, cur in zip(real, real[1:]):
        a, b = sections[prev], sections[cur]
        if not b["auto"]:
            continue
        firsts = ([f"colsfirst:{cur}"] if b["kind"] == "cols"
                  else [f"b:{b['groups'][0]['rows'][0]['ids'][0]}"] if b["groups"][0]["rows"][0]["ids"] else [])
        if not firsts:
            continue
        if a["kind"] == "cols":
            out_links.append([f"colslast:{prev}", f"gate:{cur}", "toGate"])
            out_links += [[f"colslast:{prev}", f, "logic"] for f in firsts]
        else:
            ids = [i for g in a["groups"] for r in g["rows"] for i in r["ids"]]
            exit_id = a["exit"] or (ids[-1] if ids else None)
            if exit_id is None:
                continue
            out_links += [[f"b:{exit_id}", f, "branch"] for f in firsts]
            out_links += [[f"b:{exit_id}", f, "logic"] for f in firsts]

    return {"series_id": series_id, "col": (meta["col"] if meta else 150), "preset": meta["preset"] if meta else None,
            "sections": sections, "nodes": nodes, "links": out_links, "lines": lines,
            "statuses": {k: _(v) for k, v in STATUSES.items()}}


# --- map editing (manual maps) ----------------------------------------------------------------------

def start_map(series_id: int) -> None:
    """A first map for a series built by hand: one arc column holding every book in its current order."""
    with get_conn() as conn:
        _clear_map(conn, series_id)
    set_map_meta(series_id, None, 150)
    sid = create_section(series_id, "cols", "Arcs", "#5ec2a3")
    gid = create_group(sid, "Main arc", "#5ec2a3")
    for b in series_books(series_id):
        add_entry(gid, b["id"])


def section_series(section_id: int) -> int | None:
    with get_conn() as conn:
        r = conn.execute("SELECT series_id FROM map_sections WHERE id = ?", (section_id,)).fetchone()
    return r["series_id"] if r else None


def group_series(group_id: int) -> int | None:
    with get_conn() as conn:
        r = conn.execute("""SELECT s.series_id FROM map_groups g JOIN map_sections s ON s.id = g.section_id
                            WHERE g.id = ?""", (group_id,)).fetchone()
    return r["series_id"] if r else None


def entry_series(entry_id: int) -> int | None:
    with get_conn() as conn:
        r = conn.execute("""SELECT s.series_id FROM map_entries e JOIN map_groups g ON g.id = e.group_id
                            JOIN map_sections s ON s.id = g.section_id WHERE e.id = ?""", (entry_id,)).fetchone()
    return r["series_id"] if r else None


def add_section(series_id: int, kind: str, name: str, color: str, hint: str = "") -> int:
    kind = kind if kind in SECTION_KINDS else "row"
    sid = create_section(series_id, kind, name, color, hint, auto_wire=True)
    create_group(sid, "New arc" if kind == "cols" else name, color, "", loose=(kind == "loose"))
    return sid


def update_section(section_id: int, name: str, color: str, hint: str, kind: str, auto_wire: bool,
                   name_fr: str = "", hint_fr: str = "") -> None:
    with get_conn() as conn:
        s = conn.execute("SELECT kind FROM map_sections WHERE id = ?", (section_id,)).fetchone()
        if s is None:
            return
        # Arc-column sections hold several groups, the others exactly one: don't convert between them.
        if kind not in SECTION_KINDS or (kind == "cols") != (s["kind"] == "cols"):
            kind = s["kind"]
        conn.execute(
            """UPDATE map_sections SET name = ?, color = ?, hint = ?, kind = ?, auto_wire = ?, name_fr = ?, hint_fr = ?
               WHERE id = ?""",
            (name.strip() or "Untitled", color, hint.strip(), kind, 1 if auto_wire else 0,
             name_fr.strip() or None, hint_fr.strip() or None, section_id))
        if kind != "cols":
            conn.execute("UPDATE map_groups SET name = ?, color = ?, loose = ?, name_fr = ? WHERE section_id = ?",
                         (name.strip() or "Untitled", color, 1 if kind == "loose" else 0, name_fr.strip() or None, section_id))


def _swap(conn: sqlite3.Connection, table: str, parent_col: str, item_id: int, direction: str) -> None:
    row = conn.execute(f"SELECT {parent_col} AS p FROM {table} WHERE id = ?", (item_id,)).fetchone()
    if row is None:
        return
    ids = [r["id"] for r in conn.execute(f"SELECT id FROM {table} WHERE {parent_col} = ? ORDER BY sort_order, id",
                                         (row["p"],))]
    i = ids.index(item_id)
    j = i - 1 if direction == "up" else i + 1
    if 0 <= j < len(ids):
        ids[i], ids[j] = ids[j], ids[i]
    conn.executemany(f"UPDATE {table} SET sort_order = ? WHERE id = ?", [(k, x) for k, x in enumerate(ids)])


def move_section(section_id: int, direction: str) -> None:
    with get_conn() as conn:
        _swap(conn, "map_sections", "series_id", section_id, direction)


def delete_section(section_id: int) -> None:
    with get_conn() as conn:
        groups = [r["id"] for r in conn.execute("SELECT id FROM map_groups WHERE section_id = ?", (section_id,))]
        conn.executemany("DELETE FROM map_entries WHERE group_id = ?", [(g,) for g in groups])
        conn.executemany("DELETE FROM map_rows WHERE group_id = ?", [(g,) for g in groups])
        conn.execute("DELETE FROM map_groups WHERE section_id = ?", (section_id,))
        conn.execute("DELETE FROM map_sections WHERE id = ?", (section_id,))
        refs = [f"{k}:{section_id}" for k in ("gate", "colsfirst", "colslast")]
        conn.executemany("DELETE FROM map_links WHERE from_ref = ? OR to_ref = ?", [(r, r) for r in refs])


def add_group(section_id: int, name: str, color: str) -> int:
    return create_group(section_id, name, color)


def update_group(group_id: int, name: str, color: str, hint: str, loose: bool, name_fr: str = "") -> None:
    with get_conn() as conn:
        conn.execute("UPDATE map_groups SET name = ?, color = ?, hint = ?, loose = ?, name_fr = ? WHERE id = ?",
                     (name.strip() or "Untitled", color, hint.strip(), 1 if loose else 0, name_fr.strip() or None, group_id))


def move_group(group_id: int, direction: str) -> None:
    with get_conn() as conn:
        _swap(conn, "map_groups", "section_id", group_id, direction)


def delete_group(group_id: int) -> None:
    with get_conn() as conn:
        conn.execute("DELETE FROM map_entries WHERE group_id = ?", (group_id,))
        conn.execute("DELETE FROM map_rows WHERE group_id = ?", (group_id,))
        conn.execute("DELETE FROM map_groups WHERE id = ?", (group_id,))


def place_book(book_id: int, group_id: int) -> None:
    """Put a book at the end of a group (moving it out of wherever it was on the map)."""
    with get_conn() as conn:
        last = conn.execute("SELECT MAX(row) AS r FROM map_entries WHERE group_id = ?", (group_id,)).fetchone()
        row = last["r"] or 0
    add_entry(group_id, book_id, row=row)


def unplace_book(book_id: int) -> None:
    with get_conn() as conn:
        _forget_book_in_maps(conn, book_id)


def move_entry(entry_id: int, direction: str) -> None:
    with get_conn() as conn:
        e = conn.execute("SELECT group_id, row FROM map_entries WHERE id = ?", (entry_id,)).fetchone()
        if e is None:
            return
        ids = [r["id"] for r in conn.execute(
            "SELECT id FROM map_entries WHERE group_id = ? ORDER BY row, sort_order, id", (e["group_id"],))]
        i = ids.index(entry_id)
        j = i - 1 if direction == "up" else i + 1
        if 0 <= j < len(ids):
            ids[i], ids[j] = ids[j], ids[i]
        conn.executemany("UPDATE map_entries SET sort_order = ? WHERE id = ?", [(k, x) for k, x in enumerate(ids)])


def update_entry(entry_id: int, row: int, indent: int, sub: str) -> None:
    with get_conn() as conn:
        conn.execute("UPDATE map_entries SET row = ?, indent = ?, sub = ? WHERE id = ?",
                     (max(0, row), max(0, indent), sub.strip(), entry_id))


def map_links_for_edit(series_id: int) -> list[dict]:
    """Book-to-book links with titles, for the editor's crossover list."""
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM map_links WHERE series_id = ? AND kind IN ('cross', 'vdash', 'elbow') ORDER BY id",
                            (series_id,)).fetchall()
        titles = {r["id"]: r["title"] for r in conn.execute("SELECT id, title FROM books WHERE series_id = ?", (series_id,))}
    out = []
    for r in rows:
        a, b = r["from_ref"].partition(":")[2], r["to_ref"].partition(":")[2]
        if r["from_ref"].startswith("book:") and r["to_ref"].startswith("book:"):
            out.append({"id": r["id"], "kind": r["kind"], "from": titles.get(int(a), "?"), "to": titles.get(int(b), "?")})
    return out


def delete_link(link_id: int, series_id: int) -> None:
    with get_conn() as conn:
        conn.execute("DELETE FROM map_links WHERE id = ? AND series_id = ?", (link_id, series_id))


def map_editor_data(series_id: int) -> dict:
    """Sections, groups and entries (with book titles) for the map editor page."""
    with get_conn() as conn:
        structure = _map_structure(conn, series_id)
        books = {b["id"]: b for b in conn.execute(
            "SELECT * FROM books WHERE series_id = ? ORDER BY sort_order, id", (series_id,))}
    placed = set()
    sections = []
    for sec in structure:
        groups = []
        for g in sec["groups"]:
            entries = []
            for e in g["entries"]:
                placed.add(e["book_id"])
                entries.append({"id": e["id"], "book_id": e["book_id"], "title": books[e["book_id"]]["title"],
                                "row": e["row"], "indent": e["indent"], "sub": e["sub"] or ""})
            groups.append({"g": g["g"], "entries": entries})
        sections.append({"s": sec["s"], "groups": groups})
    unplaced = [b for b in books.values() if b["id"] not in placed]
    return {"sections": sections, "unplaced": unplaced, "books": list(books.values())}
