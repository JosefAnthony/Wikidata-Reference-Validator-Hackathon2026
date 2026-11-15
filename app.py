from flask import Flask, request, jsonify, render_template
import requests
import aiohttp
import asyncio
import time
from threading import Lock

app = Flask(__name__)

WIKIDATA_API = "https://www.wikidata.org/w/api.php"
WAYBACK_API = "https://archive.org/wayback/available"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/115.0.0.0 Safari/537.36"
}
# ---------------------------------------------------
# CACHE (TTL + size limit)
# ---------------------------------------------------

MISS = object()  # returned when nothing valid is stored


class TTLCache:
    """Small in-memory cache. Entries expire after ttl_seconds,
    and the cache never holds more than max_entries items."""

    def __init__(self, ttl_seconds, max_entries=500):
        self.ttl = ttl_seconds
        self.max_entries = max_entries
        self.data = {}  # key -> (value, expiry_time)
        self.lock = Lock()

    def get(self, key):
        with self.lock:
            item = self.data.get(key)
            if item is None:
                return MISS
            value, expires_at = item
            if expires_at <= time.time():
                del self.data[key]  # expired, remove it
                return MISS
            return value

    def set(self, key, value):
        with self.lock:
            self.data.pop(key, None)  # re-adding makes it the newest entry
            while len(self.data) >= self.max_entries:
                oldest_key = next(iter(self.data))
                del self.data[oldest_key]
            self.data[key] = (value, time.time() + self.ttl)


# One cache per kind of data (times are in seconds)
SEARCH_CACHE = TTLCache(ttl_seconds=10 * 60)       # autocomplete: 10 minutes
LABEL_CACHE = TTLCache(ttl_seconds=24 * 60 * 60)   # labels: 24 hours
WAYBACK_CACHE = TTLCache(ttl_seconds=60 * 60)      # Wayback: 1 hour



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
    query = request.args.get("q")
    if not query:
        return jsonify([])

    refresh = request.args.get("refresh") == "1"
    cache_key = query.strip().lower()

    if not refresh:
        cached = SEARCH_CACHE.get(cache_key)
        if cached is not MISS:
            print(f"[CACHE HIT] search: {cache_key}")
            return jsonify(cached)

    print(f"[CACHE MISS] search: {cache_key} (refresh={refresh})")

    params = {
        "action": "wbsearchentities",
        "search": query,
        "language": "en",
        "format": "json"
    }

    r = requests.get(WIKIDATA_API, params=params)
    data = r.json()

    results = []
    for item in data.get("search", []):
        results.append({
            "id": item.get("id"),
            "label": item.get("label"),
            "description": item.get("description", "")
        })

    if r.ok:  # never cache a failed request
        SEARCH_CACHE.set(cache_key, results)

    return jsonify(results)


# ---------------------------------------------------
# URL STATUS CHECK
# ---------------------------------------------------

async def check_url(session, url):
    try:
        async with session.head(url, allow_redirects=True, timeout=5) as resp:
            if resp.status < 400 or resp.status in (401, 403):
                return {"url": url, "status": "alive"}
    except Exception:
        pass

    try:
        async with session.get(url, allow_redirects=True, timeout=10) as resp:
            if resp.status < 400 or resp.status in (401, 403):
                return {"url": url, "status": "alive"}
            return {"url": url, "status": "dead"}
    except Exception:
        return {"url": url, "status": "dead"}


# ---------------------------------------------------
# WAYBACK MACHINE HELPER  ✅ FIXED
# ---------------------------------------------------

def fetch_wayback_snapshot(url, refresh=False):
    """
    Fetch latest available Wayback Machine snapshot for a URL.
    Returns None if no archive exists. Results are cached.
    """
    if not refresh:
        cached = WAYBACK_CACHE.get(url)
        if cached is not MISS:
            print(f"[CACHE HIT] wayback: {url}")
            return cached

    print(f"[CACHE MISS] wayback: {url} (refresh={refresh})")

    try:
        params = {"url": url}
        r = requests.get(WAYBACK_API, params=params, timeout=10)
        r.raise_for_status()

        data = r.json()
        snapshot = data.get("archived_snapshots", {}).get("closest")

        if snapshot and snapshot.get("available"):
            result = {
                "archiveUrl": snapshot.get("url"),
                "timestamp": snapshot.get("timestamp"),
                "status": snapshot.get("status")
            }
        else:
            result = None  # Wayback answered: no snapshot exists

        WAYBACK_CACHE.set(url, result)
        return result

    except Exception as e:
        # A failed request is NOT cached, so it is tried again next time
        print(f"[Wayback] Failed for {url}: {e}")

    return None


# ---------------------------------------------------
# LABEL FETCHING
# ---------------------------------------------------

def fetch_labels(ids):
    labels = {}
    if not ids:
        return labels

    id_list = list(ids)
    chunks = [id_list[i:i + 50] for i in range(0, len(id_list), 50)]

    for chunk in chunks:
        params = {
            "action": "wbgetentities",
            "ids": "|".join(chunk),
            "format": "json",
            "props": "labels",
            "languages": "en"
        }

        try:
            r = requests.get(WIKIDATA_API, params=params, headers=HEADERS, timeout=10)
            r.raise_for_status()
            entities = r.json().get("entities", {})

            for eid, data in entities.items():
                label = data.get("labels", {}).get("en", {}).get("value")
                if label:
                    labels[eid] = label

        except Exception as e:
            print(f"[Label fetch failed] {chunk}: {e}")

    return labels


# ---------------------------------------------------
# VALIDATE REFERENCES  ✅ CORRECT ROUTE
# ---------------------------------------------------

@app.route("/validate", methods=["POST"])
def validate_references():
    data = request.get_json(force=True)
    if not data or "qid" not in data:
       return jsonify({"error": "Missing QID"}), 400

       qid = (data.get("qid") or "").strip()
    if not qid:
        return jsonify({"error": "Please select a Wikidata item first"}), 400
    refresh = request.args.get("refresh") == "1"

    try:
        params = {
            "action": "wbgetentities",
            "ids": qid,
            "format": "json"
        }

        r = requests.get(WIKIDATA_API, params=params, headers=HEADERS, timeout=15)
        r.raise_for_status()

        entity = r.json()["entities"].get(qid, {})
        claims = entity.get("claims", {})

        refs_data = []
        all_ids_to_label = set()

        for prop, statements in claims.items():
            for stmt in statements:
                statement_value = None
                mainsnak = stmt.get("mainsnak", {})
                datavalue = mainsnak.get("datavalue", {}).get("value")

                if isinstance(datavalue, dict) and "id" in datavalue:
                    statement_value = datavalue["id"]

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
                                if statement_value and statement_value.startswith("Q"):
                                    all_ids_to_label.add(statement_value)

        if not refs_data:
            return jsonify({"message": "No references found"}), 200

        async def run_checks():
            async with aiohttp.ClientSession(headers=HEADERS) as session:
                tasks = [check_url(session, ref["url"]) for ref in refs_data]
                return await asyncio.gather(*tasks)

                url_statuses = asyncio.run(run_checks())
        labels = fetch_labels(all_ids_to_label)

        results = []
        for ref_data, status_data in zip(refs_data, url_statuses):
            result = {
                "url": ref_data["url"],
                "status": status_data["status"],
                "propertyLabel": labels.get(ref_data["property"], ref_data["property"]),
                "statementValue": labels.get(
                    ref_data["statementValue"],
                    ref_data["statementValue"]
                ) if ref_data["statementValue"] else ""
            }

            # Wayback suggestion ONLY for dead refs
            if status_data["status"] == "dead":
                archive = fetch_wayback_snapshot(ref_data["url"], refresh)
                if archive:
                    result["suggestedArchive"] = archive

            results.append(result)

        return jsonify(results)

    except Exception as e:
        return jsonify({"error": str(e)}), 500