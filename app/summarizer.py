from pathlib import Path

from config import settings
from llm import LLMError, complete, parse_json

_PROMPTS_DIR = Path(__file__).parent / "prompts"
_SYSTEM_PROMPT = (_PROMPTS_DIR / "summarizer_system_prompt.txt").read_text(encoding="utf-8").strip()


class SummarizeError(Exception):
    pass


def _language_note() -> str:
    """Summaries follow the interface language of whoever saved the link."""
    from i18n import get_lang
    if get_lang() == "fr":
        return "\n\nWrite the summary, the key points and the tags in French, whatever the article's language."
    return ""


async def summarize(title: str, text: str) -> dict:
    body_text = text[: settings.max_article_chars]
    prompt = f"{_SYSTEM_PROMPT}{_language_note()}\n\nTitle: {title}\n\nArticle:\n{body_text}"

    try:
        parsed = parse_json(await complete(prompt, settings.model_summarizer))
    except LLMError as exc:
        raise SummarizeError(str(exc)) from exc

    return {
        "summary": str(parsed.get("summary", "")).strip(),
        "key_points": [str(p).strip() for p in parsed.get("key_points", []) if str(p).strip()],
        "tags": [str(t).strip().lower() for t in parsed.get("tags", []) if str(t).strip()],
    }
