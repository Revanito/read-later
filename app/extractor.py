import json

import httpx
import trafilatura

_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; read-later/1.0)"}
_TIMEOUT = httpx.Timeout(30.0)


class ExtractionError(Exception):
    pass


async def fetch_and_extract(url: str) -> dict:
    async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True, headers=_HEADERS) as client:
        try:
            resp = await client.get(url)
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise ExtractionError(f"could not fetch page: {exc}") from exc
        html = resp.text

    extracted = trafilatura.extract(
        html, url=url, output_format="json", with_metadata=True,
        include_comments=False, include_tables=False,
    )
    if not extracted:
        raise ExtractionError("could not extract article content (page may be paywalled or JS-rendered)")

    data = json.loads(extracted)
    text = (data.get("text") or "").strip()
    if not text:
        raise ExtractionError("extracted page had no readable text")

    return {
        "title": data.get("title") or url,
        "text": text,
    }
