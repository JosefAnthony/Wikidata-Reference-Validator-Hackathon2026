import base64
import hashlib
import os
import re
import secrets
import threading
import time
from collections import OrderedDict
from datetime import timedelta
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
# CACHING
# ---------------------------------------------------
# Search results, labels and Wayback lookups are requested again and again
# (every keystroke, every validation), so keep recent answers in memory for a
# while instead of asking Wikidata and the Internet Archive each time. The
# item itself and the alive/dead URL checks are deliberately NOT cached:
# editors fix a reference and validate again expecting the current state.

MISSING = object()  # sentinel: "not in the cache" (None is a valid cached value)


def _env_int(name, default):
    """Read a non-negative integer setting from the environment."""
    try:
        value = int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default
    return value if value >= 0 else default


class TTLCache:
    """
    Thread-safe in-memory cache whose entries expire after `ttl` seconds.

    It holds at most `maxsize` entries; when full, the least recently used
    entry is dropped. A ttl of 0 disables the cache.
    """

    def __init__(self, ttl, maxsize=500):
        self.ttl = ttl
        self.maxsize = maxsize
        self._data = OrderedDict()  # key -> (expires_at, value)
        self._lock = threading.Lock()

    def get(self, key):
        with self._lock:
            entry = self._data.get(key)
            if entry is None:
                return MISSING
            expires_at, value = entry
            if time.monotonic() >= expires_at:
                # Expired: forget it so the caller fetches a fresh value.
                del self._data[key]
                return MISSING
            self._data.move_to_end(key)
            return value

    def set(self, key, value, ttl=None):
        ttl = self.ttl if ttl is None else ttl
        if ttl <= 0 or self.maxsize <= 0:
            return
        with self._lock:
            self._data[key] = (time.monotonic() + ttl, value)
            self._data.move_to_end(key)
            while len(self._data) > self.maxsize:
                self._data.popitem(last=False)

    def clear(self):
        with self._lock:
            self._data.clear()

    def __len__(self):
        with self._lock:
            return len(self._data)


CACHE_MAX_ENTRIES = _env_int("CACHE_MAX_ENTRIES", 500)
SEARCH_CACHE = TTLCache(_env_int("CACHE_TTL_SEARCH", 10 * 60), CACHE_MAX_ENTRIES)
LABEL_CACHE = TTLCache(_env_int("CACHE_TTL_LABELS", 60 * 60), CACHE_MAX_ENTRIES)
WAYBACK_CACHE = TTLCache(_env_int("CACHE_TTL_WAYBACK", 6 * 60 * 60), CACHE_MAX_ENTRIES)
# "No snapshot found" is kept for less time, so a page archived since then
# shows up sooner.
WAYBACK_MISS_TTL = _env_int("CACHE_TTL_WAYBACK_MISS", 60 * 60)


def clear_caches():
    for cache in (SEARCH_CACHE, LABEL_CACHE, WAYBACK_CACHE):
        cache.clear()


def wants_refresh():
    """True when the request asks to bypass the cache (?refresh=1, or "refresh": true in a JSON body)."""
    flag = request.args.get("refresh")
    if flag is None:
        body = request.get_json(silent=True)
        flag = body.get("refresh") if isinstance(body, dict) else None
    return str(flag).strip().lower() in {"1", "true", "yes"}


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


@app.route("/search")
def search():
    query = (request.args.get("q") or "").strip()
    if not query:
        return jsonify([])

    language = normalize_language(request.args.get("language"))
    refresh = wants_refresh()
    cache_key = (" ".join(query.split()).casefold(), language)
    if not refresh:
        cached = SEARCH_CACHE.get(cache_key)
        if cached is not MISSING:
            return jsonify(cached)

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
    fallback_labels, labels_complete = (
        _fetch_labels(missing_labels, language, refresh) if missing_labels else ({}, True)
    )

    results = []
    for item in items:
        item_id = item.get("id")
        results.append({
            "id": item_id,
            "label": item.get("label") or fallback_labels.get(item_id) or item_id,
            "description": item.get("description", "")
        })

    # Only keep complete answers: if a label lookup failed, try again next time.
    if labels_complete:
        SEARCH_CACHE.set(cache_key, results)
    return jsonify(results)


# ---------------------------------------------------
# URL STATUS & WAYBACK CHECK (ASYNC + DEDUPLICATED)
# ---------------------------------------------------

def get_error_message(status_code):
    """Return a short explanation for common HTTP status codes."""
    if not status_code:
        return None
    messages = {
        400: "Bad Request",
        401: "Unauthorized",
        403: "Forbidden",
        404: "Not Found",
        408: "Request Timeout",
        429: "Too Many Requests",
        500: "Internal Server Error",
        502: "Bad Gateway",
        503: "Service Unavailable",
        504: "Gateway Timeout",
    }
    return messages.get(status_code, f"HTTP {status_code}")


async def check_url(session, semaphore, url):
    async with semaphore:
        head_timeout = aiohttp.ClientTimeout(total=6)
        try:
            async with session.head(url, allow_redirects=True, timeout=head_timeout) as resp:
                if resp.status < 400 or resp.status in (401, 403, 429):
                    return {"url": url, "status": "alive", "httpStatus": resp.status, "error": get_error_message(resp.status) if resp.status >= 400 else None}
                return {"url": url, "status": "dead", "httpStatus": resp.status, "error": get_error_message(resp.status)}
        except asyncio.TimeoutError:
            return {"url": url, "status": "dead", "httpStatus": None, "error": "Timeout"}
        except aiohttp.ClientConnectorError:
            return {"url": url, "status": "dead", "httpStatus": None, "error": "DNS Error"}
        except aiohttp.ClientError as e:
            return {"url": url, "status": "dead", "httpStatus": None, "error": str(e)}

        get_timeout = aiohttp.ClientTimeout(total=10)
        try:
            async with session.get(url, allow_redirects=True, timeout=get_timeout) as resp:
                if resp.status < 400 or resp.status in (401, 403, 429):
                    return {"url": url, "status": "alive", "httpStatus": resp.status, "error": get_error_message(resp.status) if resp.status >= 400 else None}
                return {"url": url, "status": "dead", "httpStatus": resp.status, "error": get_error_message(resp.status)}
        except asyncio.TimeoutError:
            return {"url": url, "status": "dead", "httpStatus": None, "error": "Timeout"}
        except aiohttp.ClientConnectorError:
            return {"url": url, "status": "dead", "httpStatus": None, "error": "DNS Error"}
        except aiohttp.ClientError as e:
            return {"url": url, "status": "dead", "httpStatus": None, "error": str(e)}


async def fetch_wayback_snapshot_async(session, semaphore, url, refresh=False):
    """
    Fetch latest available Wayback Machine snapshot for a dead URL asynchronously.
    Returns None if no archive exists or the lookup failed.

    Answers are cached (a missing snapshot for less time); failed lookups are
    not, so they are retried on the next validation.
    """
    if not refresh:
        cached = WAYBACK_CACHE.get(url)
        if cached is not MISSING:
            return cached

    async with semaphore:
        try:
            timeout = aiohttp.ClientTimeout(total=8)
            async with session.get(WAYBACK_API, params={"url": url}, timeout=timeout) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()
        except Exception as e:
            print(f"[Wayback] Failed for {url}: {e}")
            return None

    snapshots = data.get("archived_snapshots") if isinstance(data, dict) else None
    if not isinstance(snapshots, dict):
        return None  # unexpected answer: treat as a failure, don't cache it

    snapshot = snapshots.get("closest")
    if isinstance(snapshot, dict) and snapshot.get("available"):
        result = {
            "archiveUrl": snapshot.get("url"),
            "timestamp": snapshot.get("timestamp"),
            "status": snapshot.get("status")
        }
        WAYBACK_CACHE.set(url, result)
        return result

    WAYBACK_CACHE.set(url, None, ttl=WAYBACK_MISS_TTL)
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


def fetch_labels(ids, language=DEFAULT_LANGUAGE, refresh=False):
    """Return {entity id: label} in the language's fallback chain; ids without a label are left out."""
    return _fetch_labels(ids, language, refresh)[0]


def _fetch_labels(ids, language=DEFAULT_LANGUAGE, refresh=False):
    """
    Like fetch_labels(), but also says whether every lookup succeeded.

    Labels are cached per (id, language). Only the ids that are not cached
    (or all of them, with refresh) are requested from Wikidata.
    """
    labels = {}
    if not ids:
        return labels, True

    language = normalize_language(language)
    languages = "|".join(language_fallbacks(language))

    to_fetch = []
    for eid in sorted(set(ids)):
        cached = MISSING if refresh else LABEL_CACHE.get((eid, language))
        if cached is MISSING:
            to_fetch.append(eid)
        elif cached:
            labels[eid] = cached

    complete = True
    chunks = [to_fetch[i:i + 50] for i in range(0, len(to_fetch), 50)]

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
                if not isinstance(data, dict):
                    continue
                label = pick_label(data.get("labels"), language=language)
                if label:
                    labels[eid] = label
                # Cache "no label" too ("" ), so it isn't requested every time.
                LABEL_CACHE.set((eid, language), label or "")

        except Exception as e:
            complete = False
            print(f"[Label fetch failed] {chunk}: {e}")

    return labels, complete


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
    refresh = wants_refresh()

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
                    wb_tasks = [fetch_wayback_snapshot_async(session, semaphore, u, refresh)
                                for u in dead_urls]
                    wb_results = await asyncio.gather(*wb_tasks)
                    wayback_map = {u: wb for u, wb in zip(dead_urls, wb_results) if wb}

                return status_map, wayback_map

        status_map, wayback_map = asyncio.run(run_checks())
        labels = fetch_labels(all_ids_to_label, language, refresh)

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
                "error": status_info.get("error"),
                "propertyId": prop_id,
                "propertyLabel": labels.get(prop_id, prop_id),
                "statementValue": labels.get(stmt_val, stmt_val) if stmt_val else ""
            }

            if status_info["status"] == "dead" and url in wayback_map:
                result["suggestedArchive"] = wayback_map[url]

            results.append(result)

        # Sort dead links first so editors see broken references immediately
        results.sort(key=lambda r: (0 if r["status"] == "dead" else 1, r["propertyLabel"]))

        # Log dead references for monitoring and review
        dead_refs = [r for r in results if r["status"] == "dead"]
        for ref in dead_refs:
            app.logger.warning(
                "Dead reference detected",
                extra={
                    "qid": qid,
                    "url": ref["url"],
                    "httpStatus": ref.get("httpStatus"),
                    "error": ref.get("error"),
                    "property": ref.get("propertyId"),
                    "propertyLabel": ref.get("propertyLabel"),
                    "statementValue": ref.get("statementValue"),
                    "sourceCategory": ref.get("sourceCategory"),
                    "hasArchive": bool(ref.get("suggestedArchive")),
                }
            )

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
