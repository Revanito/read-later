import json
import re

import httpx
import trafilatura
from readability import Document

_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; read-later/1.0)"}
_TIMEOUT = httpx.Timeout(30.0)

# Below this length, readability's result is treated as too thin to trust and trafilatura is tried
# as a fallback - chosen well above typical "empty article shell" lengths (a few hundred chars of
# nav/boilerplate) but well below any real article body.
_MIN_TEXT_LEN = 200


class ExtractionError(Exception):
    pass


def _html_to_text(html_fragment: str) -> str:
    text = re.sub("<[^>]+>", " ", html_fragment)
    return re.sub(r"\s+", " ", text).strip()


def _extract_with_readability(html: str) -> tuple[str, str]:
    doc = Document(html)
    return doc.short_title() or "", _html_to_text(doc.summary())


def _extract_with_trafilatura(html: str, url: str) -> tuple[str, str]:
    extracted = trafilatura.extract(
        html, url=url, output_format="json", with_metadata=True,
        include_comments=False, include_tables=False,
    )
    if not extracted:
        return "", ""
    data = json.loads(extracted)
    return data.get("title") or "", (data.get("text") or "").strip()


async def fetch_and_extract(url: str) -> dict:
    async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True, headers=_HEADERS) as client:
        try:
            resp = await client.get(url)
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise ExtractionError(f"could not fetch page: {exc}") from exc
        html = resp.text

    # readability-lxml (Firefox Reader View's algorithm) goes first - its link-density scoring is
    # built specifically to avoid nav/sidebar/related-article blocks, which trafilatura was observed
    # to latch onto on some news sites (e.g. theregister.com, where it grabbed a "related stories"
    # teaser list instead of the article). Trafilatura is the fallback for when readability comes
    # back too short, which happens on very long, section-heavy pages (e.g. Wikipedia) where its
    # single-biggest-node heuristic stops at the first major section.
    title, text = _extract_with_readability(html)
    if len(text) < _MIN_TEXT_LEN:
        fallback_title, fallback_text = _extract_with_trafilatura(html, url)
        if len(fallback_text) > len(text):
            title, text = fallback_title or title, fallback_text

    if not text:
        raise ExtractionError("could not extract article content (page may be paywalled or JS-rendered)")

    return {
        "title": title or url,
        "text": text,
    }
