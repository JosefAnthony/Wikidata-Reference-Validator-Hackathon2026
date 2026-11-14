from flask import Flask, request, jsonify, render_template
import requests
import aiohttp
import asyncio
import copy
import threading
import time
from collections import OrderedDict

app = Flask(__name__)

WIKIDATA_API = "https://www.wikidata.org/w/api.php"
WAYBACK_API = "https://archive.org/wayback/available"
CACHE_TTL_SECONDS = 60 * 60
CACHE_MAX_ENTRIES = 500
_CACHE_MISS = object()
_response_cache = OrderedDict()
_cache_lock = threading.Lock()

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/115.0.0.0 Safari/537.36"
}


def _cache_get(key, refresh=False):
    if refresh:
        return _CACHE_MISS

    now = time.monotonic()
    with _cache_lock:
        entry = _response_cache.get(key)
        if entry is None:
            return _CACHE_MISS

        expires_at, value = entry
        if expires_at <= now:
            del _response_cache[key]
            return _CACHE_MISS

        _response_cache.move_to_end(key)
        return copy.deepcopy(value)


def _cache_set(key, value):
    with _cache_lock:
        _response_cache[key] = (time.monotonic() + CACHE_TTL_SECONDS, copy.deepcopy(value))
        _response_cache.move_to_end(key)
        while len(_response_cache) > CACHE_MAX_ENTRIES:
            _response_cache.popitem(last=False)


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

    query = query.strip()
    if not query:
        return jsonify([])

    refresh = request.args.get("refresh") == "1"
    cache_key = ("wikidata_search", query.casefold())
    cached_results = _cache_get(cache_key, refresh=refresh)
    if cached_results is not _CACHE_MISS:
        return jsonify(cached_results)

    params = {
        "action": "wbsearchentities",
        "search": query,
        "language": "en",
        "format": "json"
    }

    r = requests.get(WIKIDATA_API, params=params, headers=HEADERS, timeout=10)
    r.raise_for_status()
    data = r.json()

    results = []
    for item in data.get("search", []):
        results.append({
            "id": item.get("id"),
            "label": item.get("label"),
            "description": item.get("description", "")
        })

    _cache_set(cache_key, results)
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
    Returns None if no archive exists.
    """
    cache_key = ("wayback_snapshot", url)
    cached_snapshot = _cache_get(cache_key, refresh=refresh)
    if cached_snapshot is not _CACHE_MISS:
        return cached_snapshot

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
            _cache_set(cache_key, result)
            return result

        _cache_set(cache_key, None)
        return None

    except Exception as e:
        print(f"[Wayback] Failed for {url}: {e}")

    return None


# ---------------------------------------------------
# LABEL FETCHING
# ---------------------------------------------------

def fetch_labels(ids, refresh=False):
    labels = {}
    if not ids:
        return labels

    id_list = sorted(set(ids))
    uncached_ids = []
    for entity_id in id_list:
        cached_label = _cache_get(("wikidata_label", entity_id), refresh=refresh)
        if cached_label is _CACHE_MISS:
            uncached_ids.append(entity_id)
        elif cached_label:
            labels[entity_id] = cached_label

    chunks = [uncached_ids[i:i + 50] for i in range(0, len(uncached_ids), 50)]

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

            for eid in chunk:
                data = entities.get(eid, {})
                label = data.get("labels", {}).get("en", {}).get("value")
                if label:
                    labels[eid] = label
                _cache_set(("wikidata_label", eid), label or "")

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

    qid = data["qid"].strip()
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
        labels = fetch_labels(all_ids_to_label, refresh=refresh)

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

            # 🔁 Wayback suggestion ONLY for dead refs
            if status_data["status"] == "dead":
                archive = fetch_wayback_snapshot(ref_data["url"], refresh=refresh)
                if archive:
                    result["suggestedArchive"] = archive

            results.append(result)

        return jsonify(results)

    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ---------------------------------------------------

if __name__ == "__main__":
    app.run(debug=True)
