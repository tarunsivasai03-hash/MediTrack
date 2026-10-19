"""
Lightweight translations.

Templates call `_('English text')`; the catalogue in app/translations.py maps that
text to each language. Anything missing falls back to the English text, so partial
coverage degrades gracefully instead of breaking pages.
"""

import re

from flask import Blueprint, g, redirect, request, url_for
from flask import current_app, make_response

from app.translations import TRANSLATIONS
from app.translations_messages import DAYS
from app.utils import is_safe_redirect

LANGUAGES = {"en": "English", "hi": "हिन्दी", "es": "Español"}
DEFAULT = "en"
COOKIE = "lang"

i18n_bp = Blueprint("i18n", __name__)


def _pick_language() -> str:
    chosen = request.cookies.get(COOKIE)
    if chosen in LANGUAGES:
        return chosen
    return request.accept_languages.best_match(list(LANGUAGES)) or DEFAULT


class _Lazy:
    """A string translated when it is rendered, not when it is defined (form labels)."""

    def __init__(self, text: str):
        self.text = text

    def __str__(self) -> str:
        return translate(self.text)

    def __html__(self) -> str:
        from markupsafe import escape
        return str(escape(str(self)))


def lazy(text: str) -> _Lazy:
    return _Lazy(text)


_PATTERNS: dict[str, list] = {}
_WORD_GROUPS = {"status", "new"}     # placeholders filled with a status word, translated too


def _patterns(lang: str) -> list:
    """Catalogue entries containing {placeholders}, compiled once per language."""
    if lang not in _PATTERNS:
        compiled = []
        for key, target in TRANSLATIONS.get(lang, {}).items():
            if "{" not in key:
                continue
            parts = re.split(r"\{(\w+)\}", key)
            rx = "".join(re.escape(p) if i % 2 == 0 else f"(?P<{p}>.+?)" for i, p in enumerate(parts))
            compiled.append((re.compile(rx, re.S), target))
        _PATTERNS[lang] = compiled
    return _PATTERNS[lang]


def _fill(lang: str, groups: dict) -> dict:
    words = TRANSLATIONS.get(lang, {})
    out = {}
    for name, value in groups.items():
        if name in _WORD_GROUPS:
            value = words.get(value.lower(), value)
        elif name == "day":
            value = DAYS.get(lang, {}).get(value, value)
        out[name] = value
    return out


def translate(text: str, **values) -> str:
    """Translate `text` into the request's language (English fallback).

    Exact catalogue matches win; otherwise messages built from a template ("Welcome back,
    Sam!") are matched against catalogue patterns ("Welcome back, {name}!").
    """
    text = str(text)
    lang = getattr(g, "lang", DEFAULT)
    table = TRANSLATIONS.get(lang)
    out = text
    if table:
        if text in table:
            out = table[text]
        else:
            for rx, target in _patterns(lang):
                m = rx.fullmatch(text)
                if m:
                    out = target.format(**_fill(lang, m.groupdict()))
                    break
    return out.format(**values) if values else out


@i18n_bp.route("/lang/<code>")
def set_language(code):
    """Remember the visitor's language in a cookie, then go back where they were."""
    target = request.referrer if is_safe_redirect(request.referrer) else url_for("auth.index")
    resp = make_response(redirect(target))
    if code in LANGUAGES:
        resp.set_cookie(COOKIE, code, max_age=60 * 60 * 24 * 365, samesite="Lax",
                        secure=bool(current_app.config.get("SESSION_COOKIE_SECURE")))
    return resp


def init_app(app) -> None:
    app.register_blueprint(i18n_bp)
    app.jinja_env.globals["_"] = translate

    @app.before_request
    def _set_lang():
        g.lang = _pick_language()

    @app.context_processor
    def _lang_context():
        return {"current_lang": getattr(g, "lang", DEFAULT), "languages": LANGUAGES}
