import json
from pathlib import Path

import httpx

from config import settings

_TIMEOUT = httpx.Timeout(90.0)
_PROMPTS_DIR = Path(__file__).parent / "prompts"
_SYSTEM_PROMPT = (_PROMPTS_DIR / "summarizer_system_prompt.txt").read_text(encoding="utf-8").strip()


class SummarizeError(Exception):
    pass


async def summarize(title: str, text: str) -> dict:
    body_text = text[: settings.max_article_chars]
    prompt = f"{_SYSTEM_PROMPT}\n\nTitle: {title}\n\nArticle:\n{body_text}"
    url = f"{settings.one_min_base_url}/api/chat-with-ai"
    body = {
        "type": "UNIFY_CHAT_WITH_AI",
        "model": settings.model_summarizer,
        "promptObject": {"prompt": prompt},
    }
    headers = {"API-KEY": settings.one_min_api_key, "Content-Type": "application/json"}

    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        try:
            resp = await client.post(url, headers=headers, json=body)
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise SummarizeError(f"1min.ai request failed: {exc}") from exc

    try:
        data = resp.json()
        raw = data["aiRecord"]["aiRecordDetail"]["resultObject"][0]
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise SummarizeError(f"unexpected 1min.ai response shape: {exc}") from exc

    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.strip("`")
        raw = raw.split("\n", 1)[1] if "\n" in raw else raw

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SummarizeError(f"could not parse summarizer JSON: {raw[:300]}") from exc

    return {
        "summary": str(parsed.get("summary", "")).strip(),
        "key_points": [str(p).strip() for p in parsed.get("key_points", []) if str(p).strip()],
        "tags": [str(t).strip().lower() for t in parsed.get("tags", []) if str(t).strip()],
    }
