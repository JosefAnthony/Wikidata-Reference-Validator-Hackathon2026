# 🌍 Wikidata Reference Validator (Hackathon Edition)

This tool checks the external reference URLs on a Wikidata item (QID) to determine if they're still alive or dead — helping maintain the integrity and quality of Wikidata statements.

---

## 🚀 What it does

* 🔍 Autocompletes and selects Wikidata items
* 🔗 Extracts all reference URLs from the item's statements
* ⚡ Checks if the referenced URLs are reachable (alive or dead)
* ❌ Flags dead links and shows which statement they belong to
* ✏️ Provides a one-click "Edit" button to fix or update the reference

## ⚡ External response caching

The Flask backend keeps a process-local, in-memory cache for Wikidata autocomplete
results, Wikidata item/property labels, and Wayback Machine snapshot results.
Entries expire after one hour, and the cache evicts least-recently-used entries
when it exceeds 500 entries. Successful “no snapshot found” Wayback responses
are cached too; failed external requests are not.

To bypass cached data and refresh results from the external services, add
`?refresh=1` to the app URL (for example,
`http://127.0.0.1:5000/?refresh=1`). The autocomplete and validation requests
honor this flag, including label and Wayback lookups during validation. Without
the flag, expired entries are fetched again automatically.

The cache is per running Flask process and is cleared when the process restarts.

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

