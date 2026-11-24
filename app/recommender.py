from pathlib import Path

import books_db
from config import settings
from llm import LLMError, complete, parse_json

_PROMPT = (Path(__file__).parent / "prompts" / "recommender_system_prompt.txt").read_text(encoding="utf-8").strip()


class RecommendError(Exception):
    pass


def _seed_text(seed_type: str, seed_id: int) -> str:
    if seed_type == "series":
        s = books_db.get_series(seed_id)
        if s is None:
            raise RecommendError("series not found")
        titles = [b["title"] for b in books_db.series_books(seed_id)][:12]
        subjects = books_db.series_subjects(seed_id)
        text = f"Series: {s['name']} by {s['author'] or 'unknown author'}\nBooks: {'; '.join(titles) or 'n/a'}"
    else:
        b = books_db.get_book(seed_id)
        if b is None:
            raise RecommendError("book not found")
        subjects = [x for x in (b["subjects"] or "").split(",") if x]
        text = f"Book: {b['title']} by {b['author'] or 'unknown author'}" + (f" ({b['year']})" if b["year"] else "")
    if subjects:
        text += f"\nGenres/subjects: {', '.join(subjects)}"
    return text


async def recommend(seed_type: str, seed_id: int) -> list[dict]:
    library = "\n".join(f"- {line}" for line in books_db.library_overview()) or "- (empty)"
    from i18n import get_lang
    lang_note = ('\n\nWrite each "why" in French. Keep titles as they were published (use the French edition '
                 "title when one exists).") if get_lang() == "fr" else ""
    prompt = f"{_PROMPT}{lang_note}\n\nSeed:\n{_seed_text(seed_type, seed_id)}\n\nAlready in the library:\n{library}"
    try:
        parsed = parse_json(await complete(prompt, settings.model_recommender))
    except LLMError as exc:
        raise RecommendError(str(exc)) from exc

    items = []
    for r in parsed.get("recommendations", []):
        if not isinstance(r, dict) or not str(r.get("title", "")).strip():
            continue
        kind = str(r.get("type", "book")).lower()
        items.append({
            "title": str(r["title"]).strip(),
            "author": str(r.get("author", "")).strip(),
            "type": "series" if kind == "series" else "book",
            "why": str(r.get("why", "")).strip(),
        })
    books_db.save_recs(seed_type, seed_id, items)
    return items
