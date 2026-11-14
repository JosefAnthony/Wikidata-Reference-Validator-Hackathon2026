# 🌍 Wikidata Reference Validator (Hackathon Edition)

This tool checks the external reference URLs on a Wikidata item (QID) to determine if they're still alive or dead — helping maintain the integrity and quality of Wikidata statements.

---

## 🚀 What it does

* 🔍 Autocompletes and selects Wikidata items
* 🔗 Extracts all reference URLs from the item's statements
* ⚡ Checks if the referenced URLs are reachable (alive or dead)
* ❌ Flags dead links and shows which statement they belong to
* ✏️ Provides a one-click "Edit" button to fix or update the reference
* 🔐 Optional Wikimedia login (OAuth 1.0a) for identifying users
* 📚 Per-user search and edit history when logged in

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

### 4. Set up Wikimedia OAuth (optional, for Wikimedia login)

Logging in is optional; validation works without an account. To enable the
**Log in** button:

1. Go to https://meta.wikimedia.org/wiki/Special:OAuthConsumerRegistration
2. Register a new OAuth consumer with either:
   - a callback URL of `http://127.0.0.1:5000/callback` (normal redirect), or
   - out-of-band (OOB) authorization, where the user copies a verification code.

   The app supports both modes and detects which one the consumer uses.
3. Copy the Consumer Key and Consumer Secret.
4. Create a `.env` file in the project root (copy `.env.example`) and fill it in:

```bash
SECRET_KEY=change-me-to-a-random-string
WIKIMEDIA_CONSUMER_KEY=your_consumer_key
WIKIMEDIA_CONSUMER_SECRET=your_consumer_secret
```

On Windows Command Prompt you can also set them for the current session:

```bat
set WIKIMEDIA_CONSUMER_KEY=your_consumer_key
set WIKIMEDIA_CONSUMER_SECRET=your_consumer_secret
```

5. If the app is deployed behind a reverse proxy, or its public URL differs from
   `http://127.0.0.1:5000`, set `WIKIMEDIA_REDIRECT_URI` to the exact callback
   URL registered for the consumer:

```bash
WIKIMEDIA_REDIRECT_URI=https://example.org/callback
```

> 🔒 `.env` and `oauth_debug.log` are ignored by git. Never commit consumer
> secrets or Flask session keys.

### 5. Run the app

```bash
python app.py
```

Open your browser and go to:
👉 http://127.0.0.1:5000

---

## 📚 User History

When you are signed in with Wikimedia, a **History** link appears in the
navigation bar. It shows:

* **Search history** — every item you validate, with alive, unreachable, and
  archived reference counts.
* **Edit history** — every Edit link you open, including the reference URL
  before the edit. After you save the change on Wikidata and validate the item
  again, the validator compares the new references with the saved snapshot and
  shows the new URL (plus any added or removed references).

History is stored locally in `instance/history.db` (SQLite), keyed to your
Wikimedia user ID. OAuth tokens and other credentials are never stored there.
Each section can be cleared from the history page. The database is created
automatically and is ignored by git.

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


