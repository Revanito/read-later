"""Hardcover (hardcover.app) GraphQL client, used for series and read-order data.

Docs: https://docs.hardcover.app/api/getting-started/ (source: github.com/hardcoverapp/hardcover-docs)
- Token needs only the `read:catalog` scope.
- Free plan: 60 requests/min, 5,000/day; each top-level field in a query costs one request.
- `search` is backed by Typesense and returns raw hits as JSON (`results.hits[].document`).
- Search queries time out server-side after 2s, other queries after 30s.
"""
import json

import httpx

from config import settings

_URL = "https://api.hardcover.app/v1/graphql"
_TIMEOUT = httpx.Timeout(20.0)

# Series contents the way hardcover.app's own series page shows them, per the "Getting All Books in a
# Series" guide: skip merged duplicates, partial editions and compilations, and keep the most popular
# book per position.
_SERIES_QUERY = """
query SeriesBooks($id: Int!) {
  series_by_pk(id: $id) {
    id
    name
    slug
    author { name }
    book_series(
      distinct_on: position
      order_by: [{position: asc}, {book: {users_count: desc}}]
      where: {
        book: {canonical_id: {_is_null: true}, is_partial_book: {_eq: false}},
        compilation: {_eq: false}
      }
    ) {
      position
      details
      book {
        id
        title
        slug
        release_year
        image { url }
        contributions(where: {contributor_role: {slug: {_eq: "author"}}}) { author { name } }
      }
    }
  }
}
"""

_SEARCH_QUERY = """
query Search($q: String!, $type: String!, $perPage: Int!) {
  search(query: $q, query_type: $type, per_page: $perPage, page: 1) { results }
}
"""


class HardcoverError(Exception):
    pass


def enabled() -> bool:
    return bool(settings.hardcover_token.strip())


def _auth_header() -> str:
    token = settings.hardcover_token.strip()
    # The API key page may hand the token over with or without the "Bearer " prefix; accept both.
    if token.lower().startswith("bearer "):
        token = token[7:].strip()
    return f"Bearer {token}"


async def _query(query: str, variables: dict) -> dict:
    if not enabled():
        raise HardcoverError("Hardcover is not configured (HARDCOVER_TOKEN is empty)")
    headers = {
        "authorization": _auth_header(),
        "content-type": "application/json",
        "user-agent": "read-later/1.0 (self-hosted personal reading tracker)",
    }
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        try:
            resp = await client.post(_URL, headers=headers, json={"query": query, "variables": variables})
        except httpx.HTTPError as exc:
            raise HardcoverError(f"Hardcover request failed: {exc or type(exc).__name__}") from exc
    try:
        payload = resp.json()
    except ValueError:
        payload = {}
    if resp.status_code != 200:
        detail = payload.get("error_description") or payload.get("error") or payload.get("message") or resp.text[:200]
        raise HardcoverError(f"Hardcover returned HTTP {resp.status_code}: {detail}")
    if payload.get("errors"):
        first = payload["errors"][0]
        raise HardcoverError(f"Hardcover query error: {first.get('message', first) if isinstance(first, dict) else first}")
    return payload.get("data") or {}


def _hits(data: dict) -> list[dict]:
    results = (data.get("search") or {}).get("results") or {}
    if isinstance(results, str):
        try:
            results = json.loads(results)
        except ValueError:
            return []
    return [h.get("document") or {} for h in results.get("hits", [])]


def _int(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _position_label(position) -> str:
    if position is None:
        return ""
    try:
        f = float(position)
    except (TypeError, ValueError):
        return str(position)
    return str(int(f)) if f.is_integer() else str(f)


def _image_url(image) -> str:
    if not isinstance(image, dict):
        return ""
    return image.get("url") or ""


def _book_hit(doc: dict) -> dict:
    fs = doc.get("featured_series")
    fs = fs if isinstance(fs, dict) else {}
    fs_series = fs.get("series")
    fs_series = fs_series if isinstance(fs_series, dict) else {}
    series = None
    if fs_series.get("id"):
        series = {
            "hc_id": _int(fs_series.get("id")),
            "name": fs_series.get("name") or "",
            "slug": fs_series.get("slug") or "",
            "position": _position_label(fs.get("position", doc.get("featured_series_position"))),
        }
    return {
        "source": "hc",
        "title": doc.get("title") or "Untitled",
        "author": ", ".join((doc.get("author_names") or [])[:2]),
        "year": doc.get("release_year"),
        "cover_url": _image_url(doc.get("image")),
        "ol_key": "",
        "hc_id": _int(doc.get("id")),
        "hc_slug": doc.get("slug") or "",
        "subjects": [str(g) for g in (doc.get("genres") or [])][:8],
        "series": series,
    }


def _series_hit(doc: dict) -> dict:
    books = doc.get("books") or []
    titles = [b if isinstance(b, str) else (b or {}).get("title", "") for b in books]
    return {
        "hc_id": _int(doc.get("id")),
        "name": doc.get("name") or "Untitled series",
        "slug": doc.get("slug") or "",
        "author": doc.get("author_name") or "",
        "books_count": doc.get("primary_books_count") or doc.get("books_count") or 0,
        "sample_titles": [t for t in titles if t][:4],
    }


async def search_books(query: str, limit: int = 15) -> list[dict]:
    data = await _query(_SEARCH_QUERY, {"q": query, "type": "Book", "perPage": limit})
    return [_book_hit(d) for d in _hits(data)]


async def search_series(query: str, limit: int = 8) -> list[dict]:
    data = await _query(_SEARCH_QUERY, {"q": query, "type": "Series", "perPage": limit})
    return [s for s in (_series_hit(d) for d in _hits(data)) if s["hc_id"]]


async def get_series(series_id: int) -> dict:
    """Return {"hc_id", "name", "slug", "author", "books": [book hit...]} in series order."""
    data = await _query(_SERIES_QUERY, {"id": series_id})
    s = data.get("series_by_pk")
    if not s:
        raise HardcoverError(f"Hardcover series {series_id} not found")
    series_author = (s.get("author") or {}).get("name") or ""
    books = []
    for entry in s.get("book_series") or []:
        b = entry.get("book") or {}
        if not b.get("id"):
            continue
        authors = [((c or {}).get("author") or {}).get("name") for c in b.get("contributions") or []]
        authors = [a for a in authors if a]
        books.append({
            "source": "hc",
            "title": b.get("title") or "Untitled",
            "author": ", ".join(authors[:2]) or series_author,
            "year": b.get("release_year"),
            "cover_url": _image_url(b.get("image")),
            "ol_key": "",
            "hc_id": _int(b.get("id")),
            "hc_slug": b.get("slug") or "",
            "subjects": [],
            "position": _position_label(entry.get("position")),
        })
    return {
        "hc_id": _int(s.get("id")),
        "name": s.get("name") or "Untitled series",
        "slug": s.get("slug") or "",
        "author": series_author,
        "books": books,
    }
