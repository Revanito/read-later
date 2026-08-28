import hashlib
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

from config import settings

_SCHEMA = """
CREATE TABLE IF NOT EXISTS articles (
    id TEXT PRIMARY KEY,
    url TEXT NOT NULL,
    domain TEXT NOT NULL,
    title TEXT,
    summary TEXT,
    key_points TEXT,
    tags TEXT,
    full_text TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    error TEXT,
    added_at TEXT NOT NULL,
    read_at TEXT,
    archived INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_articles_added ON articles (added_at DESC);
"""


def article_id(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]


@contextmanager
def get_conn():
    conn = sqlite3.connect(settings.db_path)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with get_conn() as conn:
        conn.executescript(_SCHEMA)


def upsert_pending(id_: str, url: str, domain: str) -> None:
    now = datetime.now(timezone.utc).isoformat()
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO articles (id, url, domain, status, added_at) VALUES (?, ?, ?, 'pending', ?)
               ON CONFLICT(id) DO UPDATE SET status = 'pending', error = NULL""",
            (id_, url, domain, now),
        )


def mark_done(id_: str, title: str, summary: str, key_points: list[str], tags: list[str], full_text: str) -> None:
    with get_conn() as conn:
        conn.execute(
            """UPDATE articles SET status = 'done', title = ?, summary = ?, key_points = ?, tags = ?,
                   full_text = ?, error = NULL WHERE id = ?""",
            (title, summary, "\n".join(key_points), ",".join(tags), full_text, id_),
        )


def mark_failed(id_: str, error: str) -> None:
    with get_conn() as conn:
        conn.execute("UPDATE articles SET status = 'failed', error = ? WHERE id = ?", (error, id_))


def get_article(id_: str) -> sqlite3.Row | None:
    with get_conn() as conn:
        return conn.execute("SELECT * FROM articles WHERE id = ?", (id_,)).fetchone()


def list_articles(archived: bool = False, limit: int = 200) -> list[sqlite3.Row]:
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM articles WHERE archived = ? ORDER BY added_at DESC LIMIT ?",
            (1 if archived else 0, limit),
        ).fetchall()


def mark_read(id_: str) -> None:
    now = datetime.now(timezone.utc).isoformat()
    with get_conn() as conn:
        conn.execute("UPDATE articles SET read_at = ? WHERE id = ?", (now, id_))


def set_archived(id_: str, archived: bool) -> None:
    with get_conn() as conn:
        conn.execute("UPDATE articles SET archived = ? WHERE id = ?", (1 if archived else 0, id_))


def delete_article(id_: str) -> None:
    with get_conn() as conn:
        conn.execute("DELETE FROM articles WHERE id = ?", (id_,))
