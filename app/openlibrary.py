"""Open Library (openlibrary.org) client: keyless book search, subject browse and covers.

Open Library has no reliable series data, so this module only ever returns standalone book hits;
series/read order comes from Hardcover (hardcover.py) or is set by hand.
"""
import httpx

_BASE = "https://openlibrary.org"
_COVERS = "https://covers.openlibrary.org/b/id/{}-M.jpg"
# OL asks automated clients for a descriptive User-Agent. Kept generic on purpose - no personal
# contact details go out with requests.
_HEADERS = {"User-Agent": "read-later/1.0 (self-hosted personal reading tracker)"}
_TIMEOUT = httpx.Timeout(15.0)
_FIELDS = "key,title,author_name,first_publish_year,cover_i,subject"

# OL subject lists are long and noisy ("Fiction", "Large type books", "nyt:..."); keep the first few
# that look like real genres so the subject chips stay useful.
_SUBJECT_NOISE = ("fiction", "accessible book", "protected daisy", "in library", "large type books",
                  "open library", "lending library", "english", "translations into", "reading level")
_MAX_SUBJECTS = 8


class OpenLibraryError(Exception):
    pass


def _clean_subjects(subjects: list[str] | None) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for s in subjects or []:
        s = s.strip()
        low = s.lower()
        if not s or ":" in s or len(s) > 40 or low in seen or any(n in low for n in _SUBJECT_NOISE):
            continue
        seen.add(low)
        out.append(s)
        if len(out) >= _MAX_SUBJECTS:
            break
    return out


def _to_hit(doc: dict) -> dict:
    cover_id = doc.get("cover_i")
    return {
        "source": "ol",
        "title": doc.get("title") or "Untitled",
        "author": ", ".join((doc.get("author_name") or [])[:2]),
        "year": doc.get("first_publish_year"),
        "cover_url": _COVERS.format(cover_id) if cover_id else "",
        "ol_key": doc.get("key") or "",
        "hc_id": None,
        "hc_slug": "",
        "subjects": _clean_subjects(doc.get("subject")),
        "series": None,
    }


async def _search(params: dict) -> list[dict]:
    params = {"fields": _FIELDS, **params}
    async with httpx.AsyncClient(timeout=_TIMEOUT, headers=_HEADERS) as client:
        try:
            resp = await client.get(f"{_BASE}/search.json", params=params)
            resp.raise_for_status()
            docs = resp.json().get("docs", [])
        except (httpx.HTTPError, ValueError) as exc:
            raise OpenLibraryError(f"Open Library request failed: {str(exc) or type(exc).__name__}") from exc
    return [_to_hit(d) for d in docs]


async def search_books(query: str, limit: int = 15) -> list[dict]:
    return await _search({"q": query, "limit": limit})


async def books_by_subject(subject: str, limit: int = 24) -> list[dict]:
    # sort=rating surfaces well-liked books first instead of OL's arbitrary default order.
    return await _search({"subject": subject, "limit": limit, "sort": "rating"})
