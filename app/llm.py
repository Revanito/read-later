"""Thin wrapper around 1min.ai's chat-with-ai endpoint, shared by the summarizer and the book
recommender."""
import json

import httpx

from config import settings

_TIMEOUT = httpx.Timeout(90.0)


class LLMError(Exception):
    pass


async def complete(prompt: str, model: str) -> str:
    url = f"{settings.one_min_base_url}/api/chat-with-ai"
    body = {
        "type": "UNIFY_CHAT_WITH_AI",
        "model": model,
        "promptObject": {"prompt": prompt},
    }
    headers = {"API-KEY": settings.one_min_api_key, "Content-Type": "application/json"}

    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        try:
            resp = await client.post(url, headers=headers, json=body)
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise LLMError(f"1min.ai request failed: {str(exc) or type(exc).__name__}") from exc

    try:
        data = resp.json()
        return data["aiRecord"]["aiRecordDetail"]["resultObject"][0]
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise LLMError(f"unexpected 1min.ai response shape: {exc}") from exc


def parse_json(raw: str) -> dict:
    """Parse a model reply that should be a JSON object, tolerating a ```json fence around it."""
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.strip("`")
        raw = raw.split("\n", 1)[1] if "\n" in raw else raw
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise LLMError(f"could not parse model JSON: {raw[:300]}") from exc
    if not isinstance(parsed, dict):
        raise LLMError(f"model JSON was not an object: {raw[:300]}")
    return parsed
