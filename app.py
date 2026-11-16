import base64
import hashlib
import json
import os
import re
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode, urlparse

from flask import Flask, jsonify, redirect, render_template, request, session, url_for
import requests
import aiohttp
import asyncio

try:
    # Optional: lets local runs read credentials from a .env file.
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

app = Flask(__name__)

# History is stored locally in SQLite. It is keyed to the Wikimedia user ID
# and never contains OAuth tokens or other credentials.
HISTORY_DB_PATH = os.path.join(app.instance_path, "history.db")
SEARCH_HISTORY_PAGE_SIZE = 10
HISTORY_SCHEMA = """
CREATE TABLE IF NOT EXISTS search_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    username TEXT NOT NULL,
    qid TEXT NOT NULL,
    label TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    total INTEGER NOT NULL DEFAULT 0,
    alive INTEGER NOT NULL DEFAULT 0,
    dead INTEGER NOT NULL DEFAULT 0,
    archived INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_search_history_user_created
    ON search_history (user_id, created_at DESC);

CREATE TABLE IF NOT EXISTS edit_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    username TEXT NOT NULL,
    qid TEXT NOT NULL,
    label TEXT NOT NULL,
    property_id TEXT NOT NULL DEFAULT '',
    property_label TEXT NOT NULL DEFAULT '',
    statement_value TEXT NOT NULL DEFAULT '',
    original_url TEXT NOT NULL DEFAULT '',
    new_url TEXT NOT NULL DEFAULT '',
    before_json TEXT NOT NULL DEFAULT '[]',
    change_json TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL,
    completed_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_edit_history_user_created
    ON edit_history (user_id, created_at DESC);
"""

# Sessions hold the OAuth state and the signed-in user, so the key must be
# stable across restarts. Set SECRET_KEY in the environment for real use.
app.secret_key = os.environ.get("SECRET_KEY") or secrets.token_hex(32)
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.environ.get("SESSION_COOKIE_SECURE", "").lower() in {"1", "true", "yes"},
    PERMANENT_SESSION_LIFETIME=timedelta(days=30),
)

WIKIDATA_API = "https://www.wikidata.org/w/api.php"
WAYBACK_API = "https://archive.org/wayback/available"

# Wikimedia API requires a descriptive User-Agent header per policy
WIKIDATA_HEADERS = {
    "User-Agent": "WikidataReferenceValidator/2.0 (https://github.com/JosefAnthony/Wikidata-Reference-Validator-Hackathon2026; Hackathon 2026)"
}

# External sites often block non-browser User-Agents
EXTERNAL_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/122.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
}


# ---------------------------------------------------
# LANGUAGES
# ---------------------------------------------------
# Languages offered in the UI. The template renders this list, so adding a
# language here is all that is needed for it to appear in the picker.
SUPPORTED_LANGUAGES = [
    ("en", "English"),
    ("fr", "French"),
    ("es", "Spanish"),
    ("de", "German"),
    ("it", "Italian"),
    ("pt", "Portuguese"),
    ("sw", "Swahili"),
    ("ar", "Arabic"),
    ("zh", "Chinese"),
    ("ja", "Japanese"),
    ("nl", "Dutch"),
]
LANGUAGE_CODES = {code for code, _ in SUPPORTED_LANGUAGES}
DEFAULT_LANGUAGE = "en"


def normalize_language(language):
    """Return a supported language code, falling back to English."""
    if not isinstance(language, str):
        return DEFAULT_LANGUAGE
    code = language.strip().lower()
    return code if code in LANGUAGE_CODES else DEFAULT_LANGUAGE


def language_fallbacks(language):
    """
    Lookup order for labels: the requested language, then English, then the
    language-neutral 'mul' term that Wikidata increasingly uses.
    """
    return list(dict.fromkeys([normalize_language(language), DEFAULT_LANGUAGE, "mul"]))


@app.context_processor
def inject_languages():
    return {"languages": SUPPORTED_LANGUAGES, "default_language": DEFAULT_LANGUAGE}


# ---------------------------------------------------
# WIKIMEDIA LOGIN (OAuth 2.0)
# ---------------------------------------------------
# Register an OAuth 2.0 consumer at
# https://meta.wikimedia.org/wiki/Special:OAuthConsumerRegistration/propose
# then copy .env.example to .env and fill in the credentials. Client IDs are
# public; the client secret must never be committed to the repository.
WIKIMEDIA_OAUTH_CLIENT_ID = os.environ.get(
    "WIKIMEDIA_CLIENT_ID", "a7d62252493d736604d7219ca46e4f05"
)
WIKIMEDIA_OAUTH_CLIENT_SECRET = os.environ.get("WIKIMEDIA_CLIENT_SECRET", "")
WIKIMEDIA_OAUTH_REDIRECT_URI = os.environ.get("WIKIMEDIA_REDIRECT_URI", "")

# Wikimedia consumers that have a client secret are confidential clients and
# must send it to the token endpoint. PKCE is only for consumers registered
# without a secret, so it has to be opted into explicitly.
WIKIMEDIA_OAUTH_USE_PKCE = os.environ.get("WIKIMEDIA_USE_PKCE", "").lower() in {
    "1", "true", "yes",
}

WIKIMEDIA_OAUTH_AUTHORIZE_URL = "https://meta.wikimedia.org/w/rest.php/oauth2/authorize"
WIKIMEDIA_OAUTH_TOKEN_URL = "https://meta.wikimedia.org/w/rest.php/oauth2/access_token"
WIKIMEDIA_OAUTH_PROFILE_URL = "https://meta.wikimedia.org/w/rest.php/oauth2/resource/profile"

LOGIN_ERRORS = {
    "not_configured": "Wikimedia sign-in is not configured on this server yet.",
    "denied": "You cancelled the Wikimedia sign-in.",
    "state": "That sign-in attempt expired. Please start again.",
    "failed": "Wikimedia sign-in failed. Please try again.",
}


def oauth_configured():
    """Login needs a client secret, unless PKCE was explicitly enabled."""
    if not WIKIMEDIA_OAUTH_CLIENT_ID:
        return False
    return bool(WIKIMEDIA_OAUTH_CLIENT_SECRET) or WIKIMEDIA_OAUTH_USE_PKCE


def oauth_redirect_uri():
    """Callback URL that must exactly match the one registered for the consumer."""
    return WIKIMEDIA_OAUTH_REDIRECT_URI or url_for("oauth_callback", _external=True)


def pkce_pair():
    """Return (code_verifier, code_challenge) for the PKCE S256 method."""
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


def safe_next(target):
    """Only allow same-site relative paths as a post-login redirect."""
    if isinstance(target, str) and target.startswith("/") and not target.startswith("//"):
        return target
    return url_for("home")


@app.context_processor
def inject_user():
    return {"user": session.get("user")}


@app.route("/login")
def login():
    if session.get("user"):
        return redirect(url_for("home"))
    return render_template(
        "login.html",
        oauth_configured=oauth_configured(),
        error=LOGIN_ERRORS.get(request.args.get("error", "")),
        next=safe_next(request.args.get("next")),
    )


@app.route("/oauth/login")
def oauth_login():
    if session.get("user"):
        return redirect(url_for("home"))
    if not oauth_configured():
        return redirect(url_for("login", error="not_configured"))

    state = secrets.token_urlsafe(32)
    session["oauth_state"] = state
    session["oauth_next"] = safe_next(request.args.get("next"))

    params = {
        "response_type": "code",
        "client_id": WIKIMEDIA_OAUTH_CLIENT_ID,
        "redirect_uri": oauth_redirect_uri(),
        "state": state,
    }

    # Without a client secret we act as a public client, which needs PKCE.
    if not WIKIMEDIA_OAUTH_CLIENT_SECRET:
        verifier, challenge = pkce_pair()
        session["oauth_code_verifier"] = verifier
        params["code_challenge"] = challenge
        params["code_challenge_method"] = "S256"

    return redirect(f"{WIKIMEDIA_OAUTH_AUTHORIZE_URL}?{urlencode(params)}")


@app.route("/oauth/callback")
@app.route("/login/callback")
def oauth_callback():
    error = request.args.get("error")
    if error:
        return redirect(url_for("login", error="denied" if error == "access_denied" else "failed"))

    code = request.args.get("code", "")
    state = request.args.get("state", "")
    expected_state = session.pop("oauth_state", None)

    if not code or not state or not expected_state or not secrets.compare_digest(state, expected_state):
        app.logger.warning("OAuth callback rejected: code or state missing/mismatched.")
        return redirect(url_for("login", error="state"))

    payload = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": oauth_redirect_uri(),
    }
    auth = None
    if WIKIMEDIA_OAUTH_CLIENT_SECRET:
        auth = (WIKIMEDIA_OAUTH_CLIENT_ID, WIKIMEDIA_OAUTH_CLIENT_SECRET)
    else:
        payload["client_id"] = WIKIMEDIA_OAUTH_CLIENT_ID
        verifier = session.pop("oauth_code_verifier", None)
        if verifier:
            payload["code_verifier"] = verifier
            payload["code_challenge_method"] = "S256"

    try:
        token_response = requests.post(
            WIKIMEDIA_OAUTH_TOKEN_URL,
            data=payload,
            auth=auth,
            headers=WIKIDATA_HEADERS,
            timeout=15,
        )
        token_response.raise_for_status()
        access_token = (token_response.json() or {}).get("access_token")
        if not access_token:
            raise ValueError("token response did not contain an access_token")

        profile_response = requests.get(
            WIKIMEDIA_OAUTH_PROFILE_URL,
            headers={**WIKIDATA_HEADERS, "Authorization": f"Bearer {access_token}"},
            timeout=15,
        )
        profile_response.raise_for_status()
        profile = profile_response.json() or {}
    except Exception as e:
        app.logger.warning("Wikimedia login failed: %s", e)
        return redirect(url_for("login", error="failed"))

    if not profile.get("username"):
        app.logger.warning("Wikimedia profile response had no username.")
        return redirect(url_for("login", error="failed"))

    session.permanent = True
    session["user"] = {
        "id": profile.get("sub"),
        "username": profile["username"],
        "editcount": profile.get("editcount"),
        "groups": profile.get("groups", []),
        "blocked": bool(profile.get("blocked")),
    }
    app.logger.info("Wikimedia login succeeded for %s", profile["username"])

    target = session.pop("oauth_next", None)
    return redirect(safe_next(target))


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect(url_for("home"))


# ---------------------------------------------------
# SOURCE CATEGORIES
# ---------------------------------------------------
# Heuristic grouping of reference URLs by the kind of site they point to.
# Order matters: the first matching category wins.

SOURCE_CATEGORY_DOMAINS = {
    "archive": {
        "archive.org", "archive.today", "archive.ph", "archive.is", "archive.li",
        "webcitation.org", "perma.cc", "webarchive.org.uk", "webarchive.nla.gov.au",
    },
    "wikimedia": {
        "wikipedia.org", "wikimedia.org", "wikidata.org", "wikisource.org",
        "wikiquote.org", "wikinews.org", "wiktionary.org", "wikivoyage.org",
        "wikibooks.org", "wikiversity.org", "mediawiki.org",
    },
    "reference": {
        "viaf.org", "worldcat.org", "isni.org", "d-nb.info", "bnf.fr", "id.loc.gov",
        "idref.fr", "openlibrary.org", "musicbrainz.org", "discogs.com", "imdb.com",
        "geonames.org", "britannica.com", "encyclopedia.com", "findagrave.com",
        "deutsche-biographie.de", "catalogue.bnf.fr", "data.bnf.fr", "nla.gov.au",
        "trove.nla.gov.au", "allmusic.com", "rottentomatoes.com", "themoviedb.org",
        "goodreads.com", "librarything.com", "britishmuseum.org", "europeana.eu",
        "getty.edu", "unesco.org", "olympedia.org", "sports-reference.com",
        "transfermarkt.com", "fifa.com", "census.gov", "citypopulation.de",
    },
    "academic": {
        "doi.org", "arxiv.org", "jstor.org", "ncbi.nlm.nih.gov", "europepmc.org",
        "orcid.org", "researchgate.net", "scholar.google.com", "semanticscholar.org",
        "springer.com", "link.springer.com", "sciencedirect.com", "wiley.com",
        "nature.com", "science.org", "tandfonline.com", "cambridge.org",
        "oup.com", "academic.oup.com", "plos.org", "crossref.org", "ieee.org",
        "acm.org", "ssrn.com", "mathgenealogy.org", "zbmath.org", "dblp.org",
    },
    "news": {
        "bbc.co.uk", "bbc.com", "nytimes.com", "theguardian.com", "reuters.com",
        "apnews.com", "cnn.com", "washingtonpost.com", "aljazeera.com",
        "bloomberg.com", "ft.com", "wsj.com", "npr.org", "telegraph.co.uk",
        "independent.co.uk", "lemonde.fr", "spiegel.de", "elpais.com", "time.com",
        "forbes.com", "economist.com", "latimes.com", "usatoday.com", "abc.net.au",
        "cbc.ca", "dw.com", "france24.com", "lefigaro.fr", "corriere.it",
        "repubblica.it", "zeit.de", "faz.net", "sueddeutsche.de", "nzz.ch",
        "thehindu.com", "timesofindia.indiatimes.com", "hindustantimes.com",
        "scmp.com", "smh.com.au", "punchng.com", "premiumtimesng.com",
        "vanguardngr.com", "thecable.ng", "channelstv.com", "guardian.ng",
        "dailytrust.com", "thisdaylive.com", "nation.africa", "news24.com",
        "variety.com", "hollywoodreporter.com", "rollingstone.com", "billboard.com",
    },
    "social": {
        "twitter.com", "x.com", "facebook.com", "instagram.com", "youtube.com",
        "youtu.be", "linkedin.com", "tiktok.com", "reddit.com", "flickr.com",
        "vimeo.com", "medium.com", "threads.net", "mastodon.social", "bsky.app",
        "soundcloud.com", "spotify.com", "github.com", "t.me",
    },
}

GOVERNMENT_LABELS = {"gov", "gob", "gouv", "govt", "go", "mil", "gv"}
ACADEMIC_LABELS = {"edu", "ac"}


def _matches_domain(host, domains):
    return any(host == d or host.endswith("." + d) for d in domains)


def categorize_source(url):
    """Return (domain, category) for a reference URL using simple domain heuristics."""
    try:
        host = (urlparse(url).hostname or "").lower()
    except ValueError:
        host = ""
    if host.startswith("www."):
        host = host[4:]
    if not host:
        return "", "other"

    labels = host.split(".")

    for category in ("archive", "wikimedia", "reference", "academic"):
        if _matches_domain(host, SOURCE_CATEGORY_DOMAINS[category]):
            return host, category

    # .edu, .ac.uk, .edu.ng ...
    if labels[-1] == "edu" or (len(labels) >= 3 and labels[-2] in ACADEMIC_LABELS and len(labels[-1]) == 2):
        return host, "academic"

    # .gov, .mil, .gov.uk, .gov.ng, .gouv.fr, .go.jp, europa.eu ...
    if labels[-1] in ("gov", "mil") or host.endswith("europa.eu") or \
            (len(labels) >= 2 and labels[-2] in GOVERNMENT_LABELS and len(labels[-1]) == 2):
        return host, "government"

    for category in ("news", "social"):
        if _matches_domain(host, SOURCE_CATEGORY_DOMAINS[category]):
            return host, category
    if labels[0] == "news" or (len(labels) >= 2 and "news" in labels[-2]):
        return host, "news"

    return host, "other"


# ---------------------------------------------------
# USER HISTORY
# ---------------------------------------------------

@contextmanager
def _history_db():
    """Yield a connection to the local SQLite history database."""
    os.makedirs(app.instance_path, exist_ok=True)
    conn = sqlite3.connect(HISTORY_DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript(HISTORY_SCHEMA)
        with conn:
            yield conn
    finally:
        conn.close()


def _history_user():
    """Return the signed-in user as {id, name}, or None for anonymous visitors."""
    user = session.get("user") or {}
    user_id = user.get("id")
    if user_id is None:
        return None
    return {
        "id": str(user_id),
        "name": str(user.get("username") or user.get("name") or ""),
    }


def _history_cutoff(*, minutes=0, hours=0):
    return (
        datetime.now(timezone.utc) - timedelta(minutes=minutes, hours=hours)
    ).isoformat(timespec="seconds")


def _clean_history_text(value, limit):
    return str(value or "").strip()[:limit]


def _clean_history_url(value):
    url = _clean_history_text(value, 2000)
    if not url:
        return ""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return ""
    return url


def _clean_reference_snapshot(value):
    refs = []
    if not isinstance(value, list):
        return refs
    for item in value[:300]:
        if not isinstance(item, dict):
            continue
        url = _clean_history_url(item.get("url"))
        if not url:
            continue
        refs.append({
            "url": url,
            "propertyId": _clean_history_text(item.get("propertyId"), 32).upper(),
            "propertyLabel": _clean_history_text(item.get("propertyLabel"), 200),
            "statementValue": _clean_history_text(item.get("statementValue"), 300),
        })
    return refs


def record_search_history(qid, label, description, results):
    """Store a successful validation for the signed-in user."""
    user = _history_user()
    if not user:
        return

    results = results or []
    counts = {
        "total": len(results),
        "alive": sum(1 for ref in results if ref.get("status") == "alive"),
        "dead": sum(1 for ref in results if ref.get("status") == "dead"),
        "archived": sum(
            1 for ref in results
            if ref.get("status") == "dead" and ref.get("suggestedArchive")
        ),
    }
    qid = _clean_history_text(qid, 32).upper()
    label = _clean_history_text(label, 300) or qid
    description = _clean_history_text(description, 500)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    cutoff = _history_cutoff(minutes=10)

    try:
        with _history_db() as conn:
            # Consecutive re-validations of the same item refresh one row
            # instead of filling the history with duplicates.
            recent = conn.execute(
                """
                SELECT id FROM search_history
                WHERE user_id = ? AND qid = ? AND created_at >= ?
                ORDER BY created_at DESC, id DESC
                LIMIT 1
                """,
                (user["id"], qid, cutoff),
            ).fetchone()
            if recent:
                conn.execute(
                    """
                    UPDATE search_history
                    SET username = ?, label = ?, description = ?, total = ?,
                        alive = ?, dead = ?, archived = ?, created_at = ?
                    WHERE id = ?
                    """,
                    (
                        user["name"], label, description, counts["total"],
                        counts["alive"], counts["dead"], counts["archived"],
                        now, recent["id"],
                    ),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO search_history
                        (user_id, username, qid, label, description, total,
                         alive, dead, archived, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        user["id"], user["name"], qid, label, description,
                        counts["total"], counts["alive"], counts["dead"],
                        counts["archived"], now,
                    ),
                )
    except Exception:
        app.logger.warning("Could not save search history", exc_info=True)


def record_edit_click(data):
    """Store an Edit-link click and a snapshot of the item's references.

    The snapshot is compared with the next successful validation of the same
    item, which lets us show the original reference URL and the new one.
    """
    user = _history_user()
    if not user:
        return None

    data = data if isinstance(data, dict) else {}
    qid = _clean_history_text(data.get("qid"), 32).upper()
    if not re.fullmatch(r"Q\d+", qid):
        raise ValueError("A valid Wikidata QID is required.")

    label = _clean_history_text(data.get("label"), 300) or qid
    property_id = _clean_history_text(data.get("propertyId"), 32).upper()
    if not re.fullmatch(r"P\d+", property_id):
        property_id = ""
    property_label = _clean_history_text(data.get("propertyLabel"), 200)
    statement_value = _clean_history_text(data.get("statementValue"), 300)
    original_url = _clean_history_url(data.get("url"))
    before = _clean_reference_snapshot(data.get("references"))
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    cutoff = _history_cutoff(hours=1)

    try:
        with _history_db() as conn:
            recent = conn.execute(
                """
                SELECT id FROM edit_history
                WHERE user_id = ? AND qid = ? AND property_id = ?
                  AND original_url = ? AND status = 'pending'
                  AND created_at >= ?
                ORDER BY created_at DESC, id DESC
                LIMIT 1
                """,
                (user["id"], qid, property_id, original_url, cutoff),
            ).fetchone()

            if recent:
                conn.execute(
                    """
                    UPDATE edit_history
                    SET username = ?, label = ?, property_label = ?,
                        statement_value = ?, before_json = ?, created_at = ?
                    WHERE id = ?
                    """,
                    (
                        user["name"], label, property_label, statement_value,
                        json.dumps(before, ensure_ascii=False), now,
                        recent["id"],
                    ),
                )
                return recent["id"]

            cursor = conn.execute(
                """
                INSERT INTO edit_history
                    (user_id, username, qid, label, property_id, property_label,
                     statement_value, original_url, before_json, status, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)
                """,
                (
                    user["id"], user["name"], qid, label, property_id,
                    property_label, statement_value, original_url,
                    json.dumps(before, ensure_ascii=False), now,
                ),
            )
            return cursor.lastrowid
    except Exception:
        app.logger.warning("Could not save edit history", exc_info=True)
        raise


def reconcile_pending_edits(qid, results):
    """Compare pending edit snapshots with freshly validated references."""
    user = _history_user()
    if not user:
        return

    current = []
    for ref in results or []:
        url = _clean_history_url(ref.get("url"))
        if not url:
            continue
        current.append({
            "url": url,
            "propertyId": _clean_history_text(ref.get("propertyId"), 32).upper(),
            "propertyLabel": _clean_history_text(ref.get("propertyLabel"), 200),
            "statementValue": _clean_history_text(ref.get("statementValue"), 300),
        })

    current_keys = {(ref["url"], ref["propertyId"]) for ref in current}
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")

    try:
        with _history_db() as conn:
            rows = conn.execute(
                """
                SELECT id, property_id, original_url, before_json
                FROM edit_history
                WHERE user_id = ? AND qid = ? AND status = 'pending'
                ORDER BY created_at ASC, id ASC
                LIMIT 50
                """,
                (user["id"], qid.upper()),
            ).fetchall()

            for row in rows:
                try:
                    before = json.loads(row["before_json"] or "[]")
                except (TypeError, ValueError):
                    before = []
                if not isinstance(before, list):
                    before = []
                before = [
                    ref for ref in before
                    if isinstance(ref, dict) and ref.get("url")
                ]
                before_keys = {
                    (ref.get("url"), _clean_history_text(ref.get("propertyId"), 32).upper())
                    for ref in before
                }

                removed = [
                    ref for ref in before
                    if (ref.get("url"), _clean_history_text(ref.get("propertyId"), 32).upper())
                    not in current_keys
                ]
                added = [
                    ref for ref in current
                    if (ref["url"], ref["propertyId"]) not in before_keys
                ]

                original_url = row["original_url"] or ""
                property_id = _clean_history_text(row["property_id"], 32).upper()

                # Leave reference-specific edits pending until the clicked
                # URL itself is no longer present in the new snapshot.
                if original_url and (original_url, property_id) in current_keys:
                    continue
                if not removed and not added:
                    continue

                new_url = ""
                if property_id:
                    same_property = [
                        ref for ref in added
                        if ref["propertyId"] == property_id
                    ]
                    if len(same_property) == 1:
                        new_url = same_property[0]["url"]
                if not new_url and len(added) == 1:
                    new_url = added[0]["url"]

                change = {"removed": removed, "added": added}
                conn.execute(
                    """
                    UPDATE edit_history
                    SET status = 'completed', new_url = ?, change_json = ?,
                        completed_at = ?
                    WHERE id = ?
                    """,
                    (
                        new_url, json.dumps(change, ensure_ascii=False),
                        now, row["id"],
                    ),
                )
    except Exception:
        app.logger.warning("Could not reconcile edit history", exc_info=True)


def fetch_search_history(user_id, limit=100, offset=0):
    try:
        with _history_db() as conn:
            rows = conn.execute(
                """
                SELECT qid, label, description, total, alive, dead,
                       archived, created_at
                FROM search_history
                WHERE user_id = ?
                ORDER BY created_at DESC, id DESC
                LIMIT ? OFFSET ?
                """,
                (user_id, limit, offset),
            ).fetchall()
            return [dict(row) for row in rows]
    except Exception:
        app.logger.warning("Could not load search history", exc_info=True)
        return []


def count_search_history(user_id):
    try:
        with _history_db() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS total FROM search_history WHERE user_id = ?",
                (user_id,),
            ).fetchone()
            return int(row["total"]) if row else 0
    except Exception:
        app.logger.warning("Could not count search history", exc_info=True)
        return 0


def fetch_edit_history(user_id, limit=100):
    try:
        with _history_db() as conn:
            rows = conn.execute(
                """
                SELECT qid, label, property_id, property_label,
                       statement_value, original_url, new_url, change_json,
                       status, created_at, completed_at
                FROM edit_history
                WHERE user_id = ?
                ORDER BY created_at DESC, id DESC
                LIMIT ?
                """,
                (user_id, limit),
            ).fetchall()
    except Exception:
        app.logger.warning("Could not load edit history", exc_info=True)
        return []

    edits = []
    for row in rows:
        item = dict(row)
        try:
            change = json.loads(item.pop("change_json", "") or "{}")
        except (TypeError, ValueError):
            change = {}
        if not isinstance(change, dict):
            change = {}
        item["removed"] = change.get("removed", [])
        item["added"] = change.get("added", [])
        edits.append(item)
    return edits


@app.template_filter("history_time")
def history_time(value):
    try:
        moment = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return value or ""
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.strftime("%d %b %Y, %H:%M UTC")


# ---------------------------------------------------
# ROUTES
# ---------------------------------------------------
@app.route("/")
def home():
    return render_template("index.html")


@app.route("/about")
def about():
    return render_template("about.html")


@app.route("/use")
def use():
    return render_template("use.html")


@app.route("/report")
def report():
    return render_template("report.html")


@app.route("/history")
def history():
    user = _history_user()
    if not user:
        return redirect(url_for("login"))

    total_searches = count_search_history(user["id"])
    total_pages = max(
        1,
        (total_searches + SEARCH_HISTORY_PAGE_SIZE - 1)
        // SEARCH_HISTORY_PAGE_SIZE,
    )
    page = request.args.get("page", default=1, type=int) or 1
    page = min(max(page, 1), total_pages)

    searches = fetch_search_history(
        user["id"],
        limit=SEARCH_HISTORY_PAGE_SIZE,
        offset=(page - 1) * SEARCH_HISTORY_PAGE_SIZE,
    )
    search_start = (
        (page - 1) * SEARCH_HISTORY_PAGE_SIZE + 1
        if total_searches else 0
    )
    search_end = min(page * SEARCH_HISTORY_PAGE_SIZE, total_searches)

    return render_template(
        "history.html",
        searches=searches,
        edits=fetch_edit_history(user["id"]),
        search_page=page,
        search_total=total_searches,
        search_total_pages=total_pages,
        search_start=search_start,
        search_end=search_end,
    )


@app.route("/history/clear", methods=["POST"])
def clear_history():
    user = _history_user()
    if not user:
        return redirect(url_for("login"))

    table = {
        "searches": "search_history",
        "edits": "edit_history",
    }.get(request.form.get("kind"))
    if table:
        try:
            with _history_db() as conn:
                conn.execute(
                    f"DELETE FROM {table} WHERE user_id = ?",
                    (user["id"],),
                )
        except Exception:
            app.logger.warning("Could not clear history", exc_info=True)
    return redirect(url_for("history"))


@app.route("/api/history/edit-click", methods=["POST"])
def history_edit_click():
    if not _history_user():
        return jsonify({"error": "Log in to record edit activity."}), 401

    data = request.get_json(silent=True) or {}
    try:
        record_id = record_edit_click(data)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception:
        return jsonify({"error": "Could not record edit activity."}), 500

    return jsonify({"ok": True, "id": record_id})


@app.after_request
def no_store_history_pages(response):
    if request.endpoint in ("history", "clear_history"):
        response.headers["Cache-Control"] = "no-store"
    return response


@app.route("/search")
def search():
    query = (request.args.get("q") or "").strip()
    if not query:
        return jsonify([])

    language = normalize_language(request.args.get("language"))
    params = {
        "action": "wbsearchentities",
        "search": query,
        "language": language,
        "uselang": language,
        "format": "json",
        "limit": 8
    }

    try:
        r = requests.get(WIKIDATA_API, params=params, headers=WIKIDATA_HEADERS, timeout=10)
        r.raise_for_status()
        data = r.json()
        if not isinstance(data, dict):
            raise ValueError("Unexpected search response")
    except Exception as e:
        print(f"[Search failed] {query}: {e}")
        return jsonify({"error": "Wikidata search is temporarily unavailable."}), 502

    items = [item for item in data.get("search", []) if isinstance(item, dict)]

    # wbsearchentities only returns a label when one exists in the requested
    # language, so fill the gaps from the fallback chain.
    missing_labels = {item.get("id") for item in items if item.get("id") and not item.get("label")}
    fallback_labels = fetch_labels(missing_labels, language) if missing_labels else {}

    results = []
    for item in items:
        item_id = item.get("id")
        results.append({
            "id": item_id,
            "label": item.get("label") or fallback_labels.get(item_id) or item_id,
            "description": item.get("description", "")
        })

    return jsonify(results)


# ---------------------------------------------------
# URL STATUS & WAYBACK CHECK (ASYNC + DEDUPLICATED)
# ---------------------------------------------------

async def check_url(session, semaphore, url):
    async with semaphore:
        head_timeout = aiohttp.ClientTimeout(total=6)
        try:
            async with session.head(url, allow_redirects=True, timeout=head_timeout) as resp:
                if resp.status < 400 or resp.status in (401, 403, 429):
                    return {"url": url, "status": "alive", "httpStatus": resp.status}
        except Exception:
            pass

        get_timeout = aiohttp.ClientTimeout(total=10)
        try:
            async with session.get(url, allow_redirects=True, timeout=get_timeout) as resp:
                if resp.status < 400 or resp.status in (401, 403, 429):
                    return {"url": url, "status": "alive", "httpStatus": resp.status}
                return {"url": url, "status": "dead", "httpStatus": resp.status}
        except Exception:
            return {"url": url, "status": "dead", "httpStatus": None}


async def fetch_wayback_snapshot_async(session, semaphore, url):
    """
    Fetch latest available Wayback Machine snapshot for a dead URL asynchronously.
    Returns None if no archive exists.
    """
    async with semaphore:
        try:
            timeout = aiohttp.ClientTimeout(total=8)
            async with session.get(WAYBACK_API, params={"url": url}, timeout=timeout) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()
                snapshot = data.get("archived_snapshots", {}).get("closest")
                if snapshot and snapshot.get("available"):
                    return {
                        "archiveUrl": snapshot.get("url"),
                        "timestamp": snapshot.get("timestamp"),
                        "status": snapshot.get("status")
                    }
        except Exception as e:
            print(f"[Wayback] Failed for {url}: {e}")
        return None


# ---------------------------------------------------
# LABEL & STATEMENT VALUE HELPERS
# ---------------------------------------------------

def extract_statement_value(mainsnak, all_ids_to_label):
    """
    Extract human-readable or entity ID representation from a statement mainsnak.
    """
    snaktype = mainsnak.get("snaktype")
    if snaktype == "somevalue":
        return "unknown value"
    if snaktype == "novalue":
        return "no value"

    datavalue = mainsnak.get("datavalue", {})
    dv_type = datavalue.get("type")
    val = datavalue.get("value")

    if val is None:
        return ""

    if dv_type == "wikibase-entityid" and isinstance(val, dict):
        eid = val.get("id", "")
        if eid:
            all_ids_to_label.add(eid)
        return eid
    elif dv_type == "string" and isinstance(val, str):
        return val
    elif dv_type == "monolingualtext" and isinstance(val, dict):
        return val.get("text", "")
    elif dv_type == "time" and isinstance(val, dict):
        time_str = val.get("time", "")
        # Format +1879-03-14T00:00:00Z -> 1879-03-14
        m = re.match(r"^[+-]?(\d{4}-\d{2}-\d{2})", time_str)
        if m:
            return m.group(1).replace("-00", "")
        return time_str
    elif dv_type == "quantity" and isinstance(val, dict):
        amount = val.get("amount", "")
        if isinstance(amount, str) and amount.startswith("+"):
            amount = amount[1:]
        return str(amount)
    elif dv_type == "globecoordinate" and isinstance(val, dict):
        lat = val.get("latitude")
        lon = val.get("longitude")
        if lat is not None and lon is not None:
            return f"{round(lat, 4)}, {round(lon, 4)}"

    return ""


def pick_label(terms, fallback="", language=DEFAULT_LANGUAGE):
    """Pick the term in the requested language, then English, then 'mul'."""
    for lang in language_fallbacks(language):
        value = (terms or {}).get(lang, {}).get("value")
        if value:
            return value
    return fallback


def fetch_labels(ids, language=DEFAULT_LANGUAGE):
    labels = {}
    if not ids:
        return labels

    language = normalize_language(language)
    languages = "|".join(language_fallbacks(language))
    id_list = sorted(set(ids))
    chunks = [id_list[i:i + 50] for i in range(0, len(id_list), 50)]

    for chunk in chunks:
        params = {
            "action": "wbgetentities",
            "ids": "|".join(chunk),
            "format": "json",
            "props": "labels",
            "languages": languages
        }

        try:
            r = requests.get(WIKIDATA_API, params=params, headers=WIKIDATA_HEADERS, timeout=10)
            r.raise_for_status()
            entities = r.json().get("entities", {})

            for eid, data in entities.items():
                label = pick_label(data.get("labels") if isinstance(data, dict) else None,
                                   language=language)
                if label:
                    labels[eid] = label

        except Exception as e:
            print(f"[Label fetch failed] {chunk}: {e}")

    return labels


def resolve_qid_from_query(query_str, language=DEFAULT_LANGUAGE):
    """
    If user typed a raw QID (e.g. 'Q937') or a search term without clicking autocomplete,
    resolve it to a valid QID.
    """
    cleaned = query_str.strip()
    if re.match(r"^[Qq]\d+$", cleaned):
        return cleaned.upper()

    language = normalize_language(language)
    params = {
        "action": "wbsearchentities",
        "search": cleaned,
        "language": language,
        "uselang": language,
        "format": "json",
        "limit": 1
    }
    r = requests.get(WIKIDATA_API, params=params, headers=WIKIDATA_HEADERS, timeout=10)
    r.raise_for_status()
    results = r.json().get("search", [])
    if results:
        return results[0].get("id")
    return None


# ---------------------------------------------------
# VALIDATE REFERENCES
# ---------------------------------------------------

@app.route("/validate", methods=["POST"])
def validate_references():
    data = request.get_json(silent=True) or {}
    raw_qid = (data.get("qid") or "").strip()
    raw_query = (data.get("query") or "").strip()

    target_input = raw_qid or raw_query
    if not target_input:
        return jsonify({"error": "Please enter a Wikidata item name or QID (e.g., Q937)."}), 400

    language = normalize_language(data.get("language"))

    try:
        qid = resolve_qid_from_query(target_input, language)
        if not qid:
            return jsonify({"error": f"Could not find a Wikidata item matching '{target_input}'."}), 404

        params = {
            "action": "wbgetentities",
            "ids": qid,
            "format": "json"
        }

        r = requests.get(WIKIDATA_API, params=params, headers=WIKIDATA_HEADERS, timeout=15)
        r.raise_for_status()

        entities = r.json().get("entities", {})
        entity = entities.get(qid, {})
        if not entity or "missing" in entity:
            return jsonify({"error": f"Wikidata item '{qid}' does not exist."}), 404

        entity_label = pick_label(entity.get("labels"), qid, language)
        entity_desc = pick_label(entity.get("descriptions"), "", language)
        claims = entity.get("claims", {})

        refs_data = []
        all_ids_to_label = set()

        for prop, statements in claims.items():
            for stmt in statements:
                mainsnak = stmt.get("mainsnak", {})
                statement_value = extract_statement_value(mainsnak, all_ids_to_label)

                for ref in stmt.get("references", []):
                    for snaks in ref.get("snaks", {}).values():
                        for snak in snaks:
                            dv = snak.get("datavalue", {}).get("value")
                            ref_url = None

                            if isinstance(dv, dict) and "uri" in dv:
                                ref_url = dv["uri"]
                            elif isinstance(dv, str) and dv.startswith("http"):
                                ref_url = dv

                            if ref_url:
                                refs_data.append({
                                    "url": ref_url,
                                    "property": prop,
                                    "statementValue": statement_value
                                })
                                all_ids_to_label.add(prop)

        if not refs_data:
            record_search_history(qid, entity_label, entity_desc, [])
            reconcile_pending_edits(qid, [])
            return jsonify({
                "message": f"No external URL references found on {entity_label} ({qid}).",
                "qid": qid,
                "label": entity_label,
                "language": language
            }), 200

        # Deduplicate URLs so we only check each unique URL once
        unique_urls = list(dict.fromkeys(ref["url"] for ref in refs_data))

        async def run_checks():
            semaphore = asyncio.Semaphore(20)
            connector = aiohttp.TCPConnector(ssl=False, limit=30)
            async with aiohttp.ClientSession(headers=EXTERNAL_HEADERS, connector=connector) as session:
                check_tasks = [check_url(session, semaphore, u) for u in unique_urls]
                check_results = await asyncio.gather(*check_tasks)
                status_map = {item["url"]: item for item in check_results}

                dead_urls = [u for u, info in status_map.items() if info["status"] == "dead"]
                wayback_map = {}
                if dead_urls:
                    wb_tasks = [fetch_wayback_snapshot_async(session, semaphore, u) for u in dead_urls]
                    wb_results = await asyncio.gather(*wb_tasks)
                    wayback_map = {u: wb for u, wb in zip(dead_urls, wb_results) if wb}

                return status_map, wayback_map

        status_map, wayback_map = asyncio.run(run_checks())
        labels = fetch_labels(all_ids_to_label, language)

        results = []
        for ref_data in refs_data:
            url = ref_data["url"]
            status_info = status_map.get(url, {"status": "dead", "httpStatus": None})
            prop_id = ref_data["property"]
            stmt_val = ref_data["statementValue"]
            domain, source_category = categorize_source(url)

            result = {
                "url": url,
                "domain": domain,
                "sourceCategory": source_category,
                "status": status_info["status"],
                "httpStatus": status_info.get("httpStatus"),
                "propertyId": prop_id,
                "propertyLabel": labels.get(prop_id, prop_id),
                "statementValue": labels.get(stmt_val, stmt_val) if stmt_val else ""
            }

            if status_info["status"] == "dead" and url in wayback_map:
                result["suggestedArchive"] = wayback_map[url]

            results.append(result)

        # Sort dead links first so editors see broken references immediately
        results.sort(key=lambda r: (0 if r["status"] == "dead" else 1, r["propertyLabel"]))

        record_search_history(qid, entity_label, entity_desc, results)
        reconcile_pending_edits(qid, results)

        return jsonify({
            "qid": qid,
            "label": entity_label,
            "description": entity_desc,
            "language": language,
            "references": results
        })

    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ---------------------------------------------------

if __name__ == "__main__":
    app.run(debug=True)
