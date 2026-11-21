"""Storage for the Books tab: series, books (with a manual read order) and cached AI recommendations.

Read order is `books.sort_order` inside a series. It starts out as the Hardcover series order but is
yours to rearrange (prequels, novellas, chronological vs. publication order...). `position_label` is
just the series number ("1", "2.5") shown next to the title, and never drives ordering by itself
except when you hit "Reset to series order".
"""
import json
import re
import sqlite3
from datetime import datetime, timezone

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


def _norm_title(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (title or "").lower()).strip()


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
