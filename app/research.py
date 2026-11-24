"""AI reading-order research for a series (1min.ai with web search).

"Research reading order" on a series page asks the model (MODEL_RESEARCH, web search on) for the
recommended order of everything in the series' universe: spin-offs, prequels, novellas. A call takes
30-90 s, so it runs as a background task and the page shows "Researching..." until the result is
stored in `order_research`. The result is only a proposal: the page lists it next to your books
(matched / missing) and nothing changes until you press Apply.
"""
import asyncio
import json
import logging
import re
from datetime import datetime, timedelta, timezone

import books_db
import llm
from config import settings
from i18n import get_lang

log = logging.getLogger("research")

_STALE_MINUTES = 10  # a "running" row older than this died with a restart
_tasks: set[asyncio.Task] = set()

_PROMPT = """You are researching the reading order of a book series for a reader's tracking app.

Series: {name}
Author(s): {author}
Books the reader already has in this series (title | series # | year | author):
{books}

Search the web (prefer the author's or publisher's own reading-order page, then fan wikis and
reading-order sites) and give the COMPLETE recommended reading order for this series' universe:
every novel, prequel, novella, short story collection and spin-off series that belongs to it, including
books the reader doesn't have yet. When spin-offs interleave with the main series, interleave them.
If sources disagree (publication vs chronological order), use the order the author recommends, or
else the one most recommended for a first read, and explain the alternative in "caveats".
Leave out: comics, art books, game guides, translations, omnibus/box-set editions of books already listed.

Answer with ONLY a JSON object, no prose before or after:
{{"order": [{{"title": "exact published title", "author": "...", "line": "main series or spin-off name",
  "number": "number within its line, e.g. 3, 4.5, Prequel", "year": 2015,
  "kind": "novel|novella|short|collection|companion", "note": "why it goes here, one short sentence"}}],
 "basis": "author|publisher|publication|chronological|fans",
 "sources": ["url", ...], "confidence": "high|medium|low",
 "caveats": "anything uncertain: unreleased books, disputed placements, books you could not verify"}}
Each book appears once, in reading order. {lang_note}"""


def _lang_note() -> str:
    return ('Write "note" and "caveats" in French (titles stay as published).' if get_lang() == "fr"
            else 'Write "note" and "caveats" in English.')


def _book_lines(series_id: int) -> str:
    rows = books_db.series_books(series_id)
    if not rows:
        return "(none yet)"
    return "\n".join(f"- {b['title']} | {b['position_label'] or '-'} | {b['year'] or '-'} | {b['author'] or '-'}"
                     for b in rows)


def state(series_id: int) -> dict | None:
    """The stored research for a series, with each proposed book matched against the library:
    {"status", "error", "created_at", "result": {...}, "items": [{..., "book": row|None, "where": ...}]}"""
    row = books_db.get_research(series_id)
    if row is None:
        return None
    out = {"status": row["status"], "error": row["error"], "created_at": row["created_at"], "model": row["model"],
           "result": None, "items": []}
    if row["status"] == "running":
        started = datetime.fromisoformat(row["created_at"])
        if datetime.now(timezone.utc) - started > timedelta(minutes=_STALE_MINUTES):
            out["status"], out["error"] = "failed", "interrupted (the app restarted while researching)"
        return out
    if row["status"] != "done":
        return out
    result = json.loads(row["payload"])
    out["result"] = result
    out["items"] = match_items(series_id, result.get("order") or [])
    out["lines"], out["interleaved"] = line_layout(out["items"])
    return out


def match_items(series_id: int, order: list[dict]) -> list[dict]:
    """Pair each proposed title with a book: in this series ("here"), elsewhere in the library
    ("elsewhere", moved in on apply) or nowhere ("missing", created on apply)."""
    mine = list(books_db.series_books(series_id))
    used: set[int] = set()
    items = []
    for i, it in enumerate(order):
        if not isinstance(it, dict) or not str(it.get("title") or "").strip():
            continue
        title, author = str(it["title"]).strip(), str(it.get("author") or "").strip()
        items.append({**it, "i": i, "title": title, "author": author, "book": None, "where": "missing"})
    # Pass 1: same title, series prefix or subtitle tail. Pass 2: a title missing its subtitle.
    for rule in (books_db.loose_title_match, books_db.head_title_match):
        for it in items:
            if it["book"] is None:
                hit = next((b for b in mine if b["id"] not in used and rule(b["title"], it["title"])), None)
                if hit is not None:
                    used.add(hit["id"])
                    it["book"], it["where"] = hit, "here"
    # Still missing: maybe it's in the library under another series (or standalone), by the same author.
    others = [b for b in books_db.all_books() if b["series_id"] != series_id]
    for it in items:
        if it["book"] is None and it["author"]:
            other = next((b for b in others if b["id"] not in used and books_db.same_author(b["author"], it["author"])
                          and books_db.loose_title_match(b["title"], it["title"])), None)
            if other is not None:
                used.add(other["id"])
                it["book"], it["where"] = other, "elsewhere"
    return items


async def _run(series_id: int, lang: str) -> None:
    from i18n import set_lang
    set_lang(lang)
    s = books_db.get_series(series_id)
    prompt = _PROMPT.format(name=s["name"], author=s["author"] or "unknown", books=_book_lines(series_id),
                            lang_note=_lang_note())
    try:
        raw = await llm.complete(prompt, settings.model_research, web_search=True, timeout=240)
        result = llm.parse_json(_extract_json(raw))
        if not isinstance(result.get("order"), list) or not result["order"]:
            raise llm.LLMError("the model returned no reading order" + (f": {result.get('caveats')}" if result.get("caveats") else ""))
        books_db.finish_research(series_id, json.dumps(result), None)
        log.info("research for series %s: %d titles", series_id, len(result["order"]))
    except Exception as exc:  # stored and shown on the page; never crash the app
        log.warning("research for series %s failed: %s", series_id, exc)
        books_db.finish_research(series_id, None, str(exc) or type(exc).__name__)


def _extract_json(raw: str) -> str:
    """The model sometimes wraps the object in prose or a ```json fence: keep the outermost {...}."""
    m = re.search(r"\{.*\}", raw, re.S)
    return m.group(0) if m else raw


def start(series_id: int) -> bool:
    """Kick off research in the background. False if one is already running."""
    current = state(series_id)
    if current and current["status"] == "running":
        return False
    books_db.start_research(series_id, settings.model_research)
    task = asyncio.get_running_loop().create_task(_run(series_id, get_lang()))
    _tasks.add(task)  # keep a reference so the task isn't garbage-collected mid-flight
    task.add_done_callback(_tasks.discard)
    return True


def apply(series_id: int, add: set[int], reorder: bool, build_map: bool) -> dict:
    """Apply a stored research result. `add` = indexes of missing/elsewhere items to bring in."""
    st = state(series_id)
    if not st or st["status"] != "done":
        return {"added": 0, "moved": 0, "ordered": False, "mapped": False}
    stats = {"added": 0, "moved": 0, "ordered": False, "mapped": False}
    s = books_db.get_series(series_id)
    placed: list[tuple[dict, int]] = []
    for it in st["items"]:
        if it["where"] == "here":
            placed.append((it, it["book"]["id"]))
        elif it["i"] in add and it["where"] == "elsewhere":
            books_db.set_book_series_raw(it["book"]["id"], series_id)
            placed.append((it, it["book"]["id"]))
            stats["moved"] += 1
        elif it["i"] in add and it["where"] == "missing":
            year = it.get("year") if isinstance(it.get("year"), int) else None
            author = it["author"] or (s["author"] or "")
            bid = books_db.add_book({"title": it["title"], "author": author, "year": year}, series_id,
                                    _clean_number(it.get("number")))
            if author:
                books_db.queue_lookup(bid)  # links itself to Hardcover / Open Library later
            placed.append((it, bid))
            stats["added"] += 1
    if reorder or build_map:
        ids = [bid for _, bid in placed]
        rest = [b["id"] for b in books_db.series_books(series_id) if b["id"] not in set(ids)]
        with books_db.get_conn() as conn:
            books_db._renumber(conn, ids + rest)
        stats["ordered"] = True
    if build_map:
        _, interleaved = line_layout([it for it, _ in placed])
        lines: dict[str, list[tuple[int, str]]] = {}
        for it, bid in placed:
            line = _line(it, s["name"])
            num = str(it.get("number") or "").strip()
            sub = num if not num or not num[0].isdigit() else f"#{num}"
            if interleaved:
                # Lines take turns (Warcraft's chronological order): one column in the researched order,
                # each book labelled with its line, so the order survives.
                lines.setdefault(s["name"], []).append((bid, f"{line} · {sub}" if sub else line))
            else:
                lines.setdefault(line, []).append((bid, sub))
        books_db.build_line_map(series_id, list(lines.items()))
        stats["mapped"] = True
    return stats


def _line(it: dict, default: str) -> str:
    return str(it.get("line") or default).strip() or default


def line_layout(items: list[dict]) -> tuple[list[str], bool]:
    """The lines in order of first appearance, and whether they interleave (a line comes back after
    another one started) rather than following one another."""
    order: list[str] = []
    interleaved = False
    for it in items:
        line = _line(it, "")
        if not order or order[-1] != line:
            if line in order:
                interleaved = True
            order.append(line)
    seen: list[str] = []
    for line in order:
        if line not in seen:
            seen.append(line)
    return seen, interleaved


def _clean_number(n) -> str:
    """A series # for position_label: plain numbers only ("3", "4.5"), not "Prequel"."""
    n = str(n or "").strip()
    return n if re.fullmatch(r"\d+(\.\d+)?", n) else ""
