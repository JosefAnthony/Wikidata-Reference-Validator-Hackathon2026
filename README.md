# 🌍 Wikidata Reference Validator (Hackathon Edition)
## ⚡ Caching

To reduce repeated requests to Wikidata and the Wayback Machine, the app keeps recent results in a lightweight in-memory cache.

### What is cached

| Data | Used by | Expires after |
|---|---|---|
| Wikidata autocomplete search results | `/search` | 10 minutes |
| Wikidata item and property labels | `/validate` | 24 hours |
| Wayback Machine snapshot results | `/validate` (dead references only) | 1 hour |

URL status checks (alive or dead) are **not** cached, so link status is always checked live.

### How it works

* If the same request is made again before its entry expires, the saved result is returned and no outside request is sent.
* Expired entries are refreshed automatically the next time they are requested.
* Each cache holds at most **500 entries**. When a cache is full, the oldest entry is removed to make room.
* Failed requests are never cached, so they are retried next time.
* A Wayback result of "no snapshot found" is cached like any other result.
* The cache lives in memory, so it is cleared whenever the app restarts.
* The terminal prints `[CACHE HIT]` or `[CACHE MISS]` lines, which makes the behaviour easy to follow.

### Bypassing the cache

Add `?refresh=1` to a request to skip the cache and fetch fresh data. The fresh result then replaces the saved one.

* Search: `http://127.0.0.1:5000/search?q=Douglas&refresh=1`
* Validation: send a `POST` to `/validate?refresh=1`

### Changing the settings

The expiry times and size limit are set near the top of `app.py`:

```python
SEARCH_CACHE = TTLCache(ttl_seconds=10 * 60)
LABEL_CACHE = TTLCache(ttl_seconds=24 * 60 * 60)
WAYBACK_CACHE = TTLCache(ttl_seconds=60 * 60)
```

The default limit is 500 entries per cache (`max_entries=500`).


This tool checks the external reference URLs on a Wikidata item (QID) to determine if they're still alive or dead — helping maintain the integrity and quality of Wikidata statements.

---

## 🚀 What it does

* 🔍 Autocompletes and selects Wikidata items
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

1. Install Python
Download from: https://www.python.org/downloads/
During installation:
✅ Tick “Add Python to PATH”

2. Verify Python

Open Command Prompt and run:

python --version

### 1. Clone the repository

```bash
git clone https://github.com/JosefAnthony/Wikidata-Reference-Validator-Hackathon2026.git
cd Wikidata-Reference-Validator-Hackathon2026
```

### 2. Create virtual environment (optional but recommended)

```bash
python -m venv venv

# Windows:
venv\Scripts\activate

# Mac/Linux:
source venv/bin/activate
```

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

---

## 🧩 Hackathon Tasks

We are using Phabricator to manage all hackathon tasks.

👉 Browse and pick a task here:
https://phabricator.wikimedia.org/project/board/8311/

### 🪜 How to participate

1. Pick a task from Phabricator
2. Assign the task to yourself
3. Clone this repository
4. Create a new branch
5. Work on your task
6. Submit a Pull Request on GitHub

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


