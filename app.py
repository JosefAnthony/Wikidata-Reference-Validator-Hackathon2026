from flask import Flask, request, jsonify, render_template
import requests
import aiohttp
import asyncio

app = Flask(__name__)

WIKIDATA_API = "https://www.wikidata.org/w/api.php"
WAYBACK_API = "https://archive.org/wayback/available"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/115.0.0.0 Safari/537.36"
}


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

    return jsonify(results)


@app.route("/archive", methods=["GET", "POST"])
def archive_lookup():
    """
    Fetch the latest archived snapshot from the Internet Archive API (Wayback Machine)
    for a given URL.
    Supports:
      - GET /archive?url=<url>
      - POST /archive with JSON {"url": "<url>"}
    """
    if request.method == "POST":
        data = request.get_json(silent=True) or {}
        url = data.get("url")
    else:
        url = request.args.get("url")

    if not url:
        return jsonify({"error": "Missing URL parameter"}), 400

    snapshot = fetch_wayback_snapshot(url.strip())
    if snapshot:
        return jsonify({
            "success": True,
            "url": url,
            "archive": snapshot
        }), 200

    return jsonify({
        "success": False,
        "url": url,
        "archive": None,
        "message": "No archived snapshot found on Wayback Machine"
    }), 404


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
# WAYBACK MACHINE HELPERS (Internet Archive API)
# ---------------------------------------------------

def parse_wayback_timestamp(ts):
    """
    Parses a 14-digit Wayback timestamp (YYYYMMDDhhmmss) into an ISO date (YYYY-MM-DD)
    and a human-friendly format (e.g. Aug 15, 2020) suitable for Wikidata P2960.
    """
    if not ts:
        return None, None
    ts_str = str(ts).strip()
    if len(ts_str) < 8:
        return None, None
    try:
        from datetime import datetime
        year = ts_str[0:4]
        month = ts_str[4:6]
        day = ts_str[6:8]
        iso_date = f"{year}-{month}-{day}"
        dt = datetime.strptime(iso_date, "%Y-%m-%d")
        friendly_label = dt.strftime("%b %d, %Y")
        return iso_date, friendly_label
    except Exception:
        return None, None


async def fetch_wayback_snapshot_async(session, url):
    """
    Asynchronously fetch latest available Wayback Machine snapshot for a URL
    using the Internet Archive Availability API.
    Returns snapshot dict or None if no archive exists.
    """
    try:
        params = {"url": url}
        timeout = aiohttp.ClientTimeout(total=8)
        async with session.get(WAYBACK_API, params=params, timeout=timeout) as resp:
            if resp.status == 200:
                data = await resp.json(content_type=None)
                snapshot = data.get("archived_snapshots", {}).get("closest")
                if snapshot and snapshot.get("available"):
                    timestamp = snapshot.get("timestamp")
                    archive_date, archive_date_label = parse_wayback_timestamp(timestamp)
                    return {
                        "archiveUrl": snapshot.get("url"),
                        "timestamp": timestamp,
                        "archiveDate": archive_date,
                        "archiveDateLabel": archive_date_label,
                        "status": snapshot.get("status")
                    }
    except Exception as e:
        print(f"[Wayback async] Failed for {url}: {e}")

    return None


def fetch_wayback_snapshot(url):
    """
    Fetch latest available Wayback Machine snapshot for a URL synchronously.
    Returns snapshot dict or None if no archive exists.
    """
    try:
        params = {"url": url}
        r = requests.get(WAYBACK_API, params=params, headers=HEADERS, timeout=10)
        r.raise_for_status()

        data = r.json()
        snapshot = data.get("archived_snapshots", {}).get("closest")

        if snapshot and snapshot.get("available"):
            timestamp = snapshot.get("timestamp")
            archive_date, archive_date_label = parse_wayback_timestamp(timestamp)
            return {
                "archiveUrl": snapshot.get("url"),
                "timestamp": timestamp,
                "archiveDate": archive_date,
                "archiveDateLabel": archive_date_label,
                "status": snapshot.get("status")
            }

    except Exception as e:
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

    qid = data["qid"].strip()

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

        async def run_checks_and_archives():
            async with aiohttp.ClientSession(headers=HEADERS) as session:
                # Step 1: Check alive/dead status for all reference URLs concurrently
                status_tasks = [check_url(session, ref["url"]) for ref in refs_data]
                statuses = await asyncio.gather(*status_tasks)

                # Step 2: Concurrently query Internet Archive API for any dead references
                async def no_op():
                    return None

                archive_tasks = [
                    fetch_wayback_snapshot_async(session, ref["url"])
                    if status.get("status") == "dead" else no_op()
                    for ref, status in zip(refs_data, statuses)
                ]
                archives = await asyncio.gather(*archive_tasks)
                return statuses, archives

        url_statuses, archive_results = asyncio.run(run_checks_and_archives())
        labels = fetch_labels(all_ids_to_label)

        results = []
        for ref_data, status_data, archive in zip(refs_data, url_statuses, archive_results):
            result = {
                "url": ref_data["url"],
                "status": status_data["status"],
                "propertyLabel": labels.get(ref_data["property"], ref_data["property"]),
                "statementValue": labels.get(
                    ref_data["statementValue"],
                    ref_data["statementValue"]
                ) if ref_data["statementValue"] else ""
            }

            # 🔁 Wayback suggestion for dead refs
            if status_data["status"] == "dead" and archive:
                result["suggestedArchive"] = archive

            results.append(result)

        return jsonify(results)

    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ---------------------------------------------------

if __name__ == "__main__":
    app.run(debug=True)
