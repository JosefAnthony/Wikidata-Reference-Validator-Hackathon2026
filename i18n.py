"""
Simple translation (i18n) helper for the Wikidata Reference Validator.

Messages live in the i18n/ folder, one JSON file per language:
    i18n/en.json  -> English (the source language)
    i18n/sw.json  -> Swahili
"""
import json
import os

from flask import g, request

I18N_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "i18n")
DEFAULT_LANG = "en"
SUPPORTED_LANGS = ["en", "sw"]
COOKIE_NAME = "wrv_lang"

# All messages, loaded once at start-up: { "en": {key: text}, "sw": {...} }
_messages = {}


def load_messages():
    """Read every language file from the i18n/ folder."""
    for lang in SUPPORTED_LANGS:
        path = os.path.join(I18N_DIR, f"{lang}.json")
        if not os.path.exists(path):
            _messages[lang] = {}
            continue
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        data.pop("@metadata", None)  # authors list, not a message
        _messages[lang] = data


def get_language():
    """Choose the language: ?uselang=  ->  cookie  ->  browser  ->  English."""
    lang = request.args.get("uselang")
    if lang in SUPPORTED_LANGS:
        return lang

    lang = request.cookies.get(COOKIE_NAME)
    if lang in SUPPORTED_LANGS:
        return lang

    return request.accept_languages.best_match(SUPPORTED_LANGS) or DEFAULT_LANG


def msg(key, *params):
    """
    Return the text for `key` in the current language.
    Falls back to English, then to ⧼key⧽ so missing keys are easy to spot.
    $1, $2 ... are replaced by params, e.g. msg("wrv-alive-count", 6).
    """
    lang = g.get("lang", DEFAULT_LANG)
    text = (
        _messages.get(lang, {}).get(key)
        or _messages.get(DEFAULT_LANG, {}).get(key)
        or f"⧼{key}⧽"
    )
    for number, value in enumerate(params, start=1):
        text = text.replace(f"${number}", str(value))
    return text


def init_app(app):
    """Connect the helper to the Flask app."""
    load_messages()

    @app.before_request
    def set_language():
        g.lang = get_language()

    @app.after_request
    def remember_language(response):
        # Save the choice only when the user picked it with ?uselang=
        if request.args.get("uselang") in SUPPORTED_LANGS:
            response.set_cookie(COOKIE_NAME, g.lang, max_age=60 * 60 * 24 * 365)
        return response

    @app.context_processor
    def add_to_templates():
        # Makes {{ msg('key') }} and {{ lang }} work in every template
        return {"msg": msg, "lang": g.get("lang", DEFAULT_LANG)}