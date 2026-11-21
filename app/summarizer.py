from pathlib import Path

from config import settings
from llm import LLMError, complete, parse_json

_PROMPTS_DIR = Path(__file__).parent / "prompts"
_SYSTEM_PROMPT = (_PROMPTS_DIR / "summarizer_system_prompt.txt").read_text(encoding="utf-8").strip()


class SummarizeError(Exception):
    pass


async def summarize(title: str, text: str) -> dict:
    body_text = text[: settings.max_article_chars]
    prompt = f"{_SYSTEM_PROMPT}\n\nTitle: {title}\n\nArticle:\n{body_text}"

    try:
        parsed = parse_json(await complete(prompt, settings.model_summarizer))
    except LLMError as exc:
        raise SummarizeError(str(exc)) from exc

    return {
        "summary": str(parsed.get("summary", "")).strip(),
        "key_points": [str(p).strip() for p in parsed.get("key_points", []) if str(p).strip()],
        "tags": [str(t).strip().lower() for t in parsed.get("tags", []) if str(t).strip()],
    }
