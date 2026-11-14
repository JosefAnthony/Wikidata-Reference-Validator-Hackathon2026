from flask import Flask, request, jsonify, render_template
import requests
import aiohttp
import asyncio
import re
from urllib.parse import urlparse

app = Flask(__name__)

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

    params = {
        "action": "wbsearchentities",
        "search": query,
        "language": "en",
        "format": "json",
        "limit": 8
    }

    try:
        r = requests.get(WIKIDATA_API, params=params, headers=WIKIDATA_HEADERS, timeout=10)
        r.raise_for_status()
        data = r.json()

        results = []
        for item in data.get("search", []):
            results.append({
                "id": item.get("id"),
                "label": item.get("label") or item.get("id"),
                "description": item.get("description", "")
            })

        return jsonify(results)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


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


def pick_label(terms, fallback=""):
    """Pick the English term, falling back to the language-neutral 'mul' term Wikidata now uses."""
    for lang in ("en", "mul"):
        value = (terms or {}).get(lang, {}).get("value")
        if value:
            return value
    return fallback


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
            "languages": "en|mul"
        }

        try:
            r = requests.get(WIKIDATA_API, params=params, headers=WIKIDATA_HEADERS, timeout=10)
            r.raise_for_status()
            entities = r.json().get("entities", {})

            for eid, data in entities.items():
                label = pick_label(data.get("labels"))
                if label:
                    labels[eid] = label

        except Exception as e:
            print(f"[Label fetch failed] {chunk}: {e}")

    return labels


def resolve_qid_from_query(query_str):
    """
    If user typed a raw QID (e.g. 'Q937') or a search term without clicking autocomplete,
    resolve it to a valid QID.
    """
    cleaned = query_str.strip()
    if re.match(r"^[Qq]\d+$", cleaned):
        return cleaned.upper()

    params = {
        "action": "wbsearchentities",
        "search": cleaned,
        "language": "en",
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

    try:
        qid = resolve_qid_from_query(target_input)
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

        entity_label = pick_label(entity.get("labels"), qid)
        entity_desc = pick_label(entity.get("descriptions"))
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
                "label": entity_label
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
        labels = fetch_labels(all_ids_to_label)

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

        return jsonify({
            "qid": qid,
            "label": entity_label,
            "description": entity_desc,
            "references": results
        })

    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ---------------------------------------------------

if __name__ == "__main__":
    app.run(debug=True)
