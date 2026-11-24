"""English / French interface.

gettext style: the English text is the key, so anything without a translation simply shows in English.
`_("Added “{title}”.", title=t)` formats {placeholders}; `ngettext(one, other, n)` picks the plural form
(French treats 0 and 1 as singular). The language is per request: the `rl_lang` cookie if set (the
flags in the header), else French when the browser asks for French, else English.

The French texts live in fr_texts.py. tests check every _() / ngettext() string used in the app has one.
"""
from contextvars import ContextVar

from fr_texts import FR, FR_PLURAL

LANGS = ("en", "fr")
COOKIE = "rl_lang"
_lang: ContextVar[str] = ContextVar("rl_lang", default="en")


def pick_lang(cookie: str | None, accept_language: str | None) -> str:
    if cookie in LANGS:
        return cookie
    first = (accept_language or "").split(",")[0].strip().lower()
    return "fr" if first.startswith("fr") else "en"


def set_lang(lang: str) -> None:
    _lang.set(lang if lang in LANGS else "en")


def get_lang() -> str:
    return _lang.get()


def _(text: str, **params) -> str:
    out = FR.get(text, text) if _lang.get() == "fr" else text
    return out.format(**params) if params else out


def ngettext(one: str, other: str, n: int, **params) -> str:
    if _lang.get() == "fr":
        text = FR.get(one, one) if n in (0, 1) else FR_PLURAL.get(other) or FR.get(other, other)
    else:
        text = one if n == 1 else other
    return text.format(n=n, **params)


def js_texts() -> dict:
    """The strings the browser-side scripts need, in the current language."""
    from fr_texts import JS_KEYS
    return {k: _(k) for k in JS_KEYS}
