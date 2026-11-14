# 🌍 Wikidata Reference Validator (Hackathon Edition)

This tool checks the external reference URLs on a Wikidata item (QID) to determine if they're still alive or dead — helping maintain the integrity and quality of Wikidata statements.

---

## 🚀 What it does

* 🔍 Autocompletes and selects Wikidata items
* 🌐 Searches and labels items in a selectable display language (English by default, used as the fallback)
* 🔐 Optional sign-in with a Wikimedia account (OAuth 2.0, PKCE or client secret)
* 🔗 Extracts all reference URLs from the item's statements
* ⚡ Checks if the referenced URLs are reachable (alive or dead)
* ❌ Flags dead links and shows which statement they belong to
* ✏️ Provides a one-click "Edit" button to fix or update the reference

---

## 🧪 Live Demo

Coming soon — for now, run locally 👇

---

## 🛠 Tech Stack

* Python 3.10+
* Flask (Backend)
* Aiohttp + Asyncio (for fast URL checking)


---

## ⚙️ How to Run Locally

### Prerequisites

**Install Python 3.10 or newer**
Download from: https://www.python.org/downloads/
During installation, tick ✅ **"Add Python to PATH"**.

**Verify Python is installed**

```bash
python --version
```

> ⚠️ **No virtual environment is included in this repository.**
> Virtual environments are machine-specific (they contain absolute paths and
> platform-specific binaries), so they can't be shared or committed. Always
> create a fresh one after cloning or forking.

### 1. Clone the repository

```bash
git clone https://github.com/JosefAnthony/Wikidata-Reference-Validator-Hackathon2026.git
cd Wikidata-Reference-Validator-Hackathon2026
```

### 2. Create and activate a virtual environment

```bash
# Create it
python -m venv .venv

# Activate — Windows (PowerShell)
.venv\Scripts\Activate.ps1

# Activate — Windows (Command Prompt)
.venv\Scripts\activate.bat

# Activate — macOS / Linux
source .venv/bin/activate
```

Your prompt should now start with `(.venv)`.

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

### 4. Run the app

```bash
python app.py
```

Open your browser and go to:
👉 http://127.0.0.1:5000

Sign-in is optional and needs the settings described in
[Sign in with Wikimedia](#-sign-in-with-wikimedia-oauth-20); everything else
works without it.

### 5. Run the tests

```bash
python -m unittest discover -s tests -t . -v
```

`python -m unittest` also works from the project root.

---

## ⚡ Caching

To avoid asking Wikidata and the Internet Archive the same question again and
again, the app keeps recent answers in memory for a while.

| What is cached | Kept for | Setting |
| --- | --- | --- |
| Autocomplete search results (per search text and language) | 10 minutes | `CACHE_TTL_SEARCH` |
| Item and property labels (per ID and language) | 1 hour | `CACHE_TTL_LABELS` |
| Wayback Machine snapshot found for a dead URL | 6 hours | `CACHE_TTL_WAYBACK` |
| Wayback Machine said "no snapshot" for a dead URL | 1 hour | `CACHE_TTL_WAYBACK_MISS` |

* **Expiry:** once an entry is older than its time limit, the next request
  fetches it again and the fresh answer is cached.
* **Size limit:** each cache holds at most **500 entries**
  (`CACHE_MAX_ENTRIES`); when it is full, the least recently used entry is
  dropped.
* **Only good answers are cached.** If Wikidata or the Wayback Machine fails
  or times out, nothing is stored and the next request tries again.
* **Not cached:** the item's statements and the alive/dead check of each
  reference URL. Validation always shows the current state, so you can fix a
  reference and check the item again straight away.

### Bypassing the cache

Add `refresh=1` to skip cached answers. The fresh answers replace the cached
ones.

```bash
# Fresh autocomplete results
curl "http://127.0.0.1:5000/search?q=Douglas%20Adams&refresh=1"

# Validate with fresh labels and Wayback lookups
curl -X POST "http://127.0.0.1:5000/validate?refresh=1" \
     -H "Content-Type: application/json" -d '{"qid": "Q42"}'
```

For `/validate`, `"refresh": true` in the JSON body works too. Restarting the
app also empties the cache.

### Settings

All settings are optional environment variables (they can go in `.env`), in
seconds except `CACHE_MAX_ENTRIES`. Setting a time to `0` turns that cache
off, which can be handy while debugging.

```dotenv
CACHE_TTL_SEARCH=600
CACHE_TTL_LABELS=3600
CACHE_TTL_WAYBACK=21600
CACHE_TTL_WAYBACK_MISS=3600
CACHE_MAX_ENTRIES=500
```

The cache lives in the memory of each app process. On Toolforge, every worker
has its own cache, and it is empty again after a restart or redeploy.

---

## 🔐 Sign in with Wikimedia (OAuth 2.0)

Signing in is **optional** — searching and validating work without an account.
When configured, users can log in with their Wikimedia account from the
**Log in** link in the header.

This app uses the OAuth 2.0 authorization-code flow against
`meta.wikimedia.org`. It only reads the basic profile (`username`, `sub`,
`editcount`, `groups`, `blocked`); it never sees a password and never edits
anything on the user's behalf.

### 1. Configure the environment

Copy `.env.example` to `.env` and fill in the values:

```bash
cp .env.example .env
```

```dotenv
SECRET_KEY=<long random string>
WIKIMEDIA_CLIENT_ID=a7d62252493d736604d7219ca46e4f05
WIKIMEDIA_CLIENT_SECRET=         # required for this consumer
WIKIMEDIA_USE_PKCE=false         # only for consumers registered without a secret
WIKIMEDIA_REDIRECT_URI=http://127.0.0.1:5000/login/callback
SESSION_COOKIE_SECURE=false
```

`SECRET_KEY` signs the session cookie that holds the OAuth state. Generate one with:

```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

### 2. Register the callback URL

Wikimedia only redirects to the callback URL that is **registered for the
consumer** (exact match; prefix matching is disabled), so the two must be
identical.

The consumer for this project is registered with:

```
https://wikidata-reference-validator.toolforge.org/login/callback
```

That is the value to use on the Toolforge deployment:

```dotenv
WIKIMEDIA_REDIRECT_URI=https://wikidata-reference-validator.toolforge.org/login/callback
```

For local development, either deploy to Toolforge or register a separate
consumer whose callback is `http://127.0.0.1:5000/login/callback` (use
“Create new app based on this” on the consumer page, then put that consumer's
key and secret in your local `.env`). The app serves both `/login/callback`
and `/oauth/callback`, but the URL must match the registration exactly.

### 3. Client secret vs. PKCE

Wikimedia has two kinds of OAuth 2.0 consumers:

* **Confidential client (default):** the consumer has a client secret. Set
  `WIKIMEDIA_CLIENT_SECRET` to that value; the app authenticates to the token
  endpoint with HTTP Basic auth. A secret is shown when you register the
  consumer — if it was lost, reset it from the same page. **The consumer
  registered for this project is confidential**, so this is the mode to use.
* **Public client:** the consumer was registered *without* a secret. Leave
  `WIKIMEDIA_CLIENT_SECRET` empty and set `WIKIMEDIA_USE_PKCE=true`; the app
  then sends a PKCE (S256) challenge and verifier.

PKCE is opt-in because a confidential consumer rejects code exchanges that
omit its secret, and the app cannot detect which kind a consumer is up front.
Without a secret and without `WIKIMEDIA_USE_PKCE`, the login page explains that
sign-in is not configured instead of sending users into a flow that would fail.

Client IDs are public and safe to commit. Never commit `.env` or a client
secret — both are git-ignored.

### Routes

| Route | Purpose |
| --- | --- |
| `GET /login` | Sign-in page (shows an error notice when something fails) |
| `GET /oauth/login` | Starts the flow and redirects to Wikimedia |
| `GET /login/callback` | Exchanges the code, reads the profile, signs the user in (`/oauth/callback` is kept as an alias) |
| `POST /logout` | Clears the session |

---

## 🧩 Hackathon Tasks

We are using Phabricator to manage all hackathon tasks.

👉 Browse and pick a task here:
https://phabricator.wikimedia.org/project/board/8311/

### 🪜 How to participate

1. Pick a task from Phabricator
2. Assign the task to yourself
3. Fork this repository on GitHub
4. Clone your fork and set up the app locally (see **How to Run Locally** above)
5. Create a new branch
6. Work on your task
7. Submit a Pull Request on GitHub

---

## 🏷 Task Difficulty Guide

* 🟢 Beginner – UI improvements, small fixes
* 🟡 Intermediate – feature enhancements
* 🔴 Advanced – architecture, performance, integrations

---

## 🔀 Contribution Workflow

```bash
# Create a new branch
git checkout -b feature/task-name

# Make your changes, then:
git add .
git commit -m "Describe your changes"

# Push your branch
git push origin feature/task-name
```

Then open a Pull Request on GitHub.

---

## 📌 Contribution Guidelines

* Keep your code clean and readable
* Write meaningful commit messages
* Work on one task per Pull Request
* Reference the Phabricator task in your PR

---

## 💡 Example Improvements

* Add loading indicators while checking URLs
* Improve UI/UX design
* Add filtering (e.g., show only dead links)
* Optimize URL checking performance
* Add export functionality (CSV/JSON)

---

## 🤝 Code of Conduct

Be respectful, collaborative, and supportive.
This is a learning-friendly environment for everyone.

---

## 🎯 Goal

Help improve the quality of references on Wikidata by making it easier to detect and fix broken links.

---

## 👨‍💻 Maintainer

Josef Anthony


