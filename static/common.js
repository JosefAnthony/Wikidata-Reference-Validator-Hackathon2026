/**************************************
 * SHARED HELPERS (loaded on every page)
 **************************************/

/** Escape a value for safe insertion into HTML (text or attribute). */
function escapeHtml(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

/** Only allow http(s) links; anything else (javascript:, data:, ...) becomes "#". */
function safeUrl(url) {
  try {
    const parsed = new URL(url, window.location.origin);
    return parsed.protocol === "http:" || parsed.protocol === "https:" ? parsed.href : "#";
  } catch {
    return "#";
  }
}

/** Kinds of source a reference can point to (keys match app.py categorize_source). */
const SOURCE_CATEGORIES = {
  news:       { label: "News & media",        icon: "bi-newspaper" },
  government: { label: "Government",          icon: "bi-bank" },
  academic:   { label: "Academic",            icon: "bi-mortarboard" },
  reference:  { label: "Databases & catalogs", icon: "bi-journal-bookmark" },
  wikimedia:  { label: "Wikimedia projects",  icon: "bi-wikipedia" },
  archive:    { label: "Web archives",        icon: "bi-archive" },
  social:     { label: "Social & video",      icon: "bi-people" },
  other:      { label: "Other websites",      icon: "bi-globe2" },
};

function sourceCategoryMeta(key) {
  return SOURCE_CATEGORIES[key] || SOURCE_CATEGORIES.other;
}

/** Wayback timestamp "20190314120000" -> "14 Mar 2019". */
function formatArchiveDate(ts) {
  if (!ts || ts.length < 8) return "";
  const date = new Date(Date.UTC(+ts.slice(0, 4), +ts.slice(4, 6) - 1, +ts.slice(6, 8)));
  if (isNaN(date)) return "";
  return date.toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric", timeZone: "UTC" });
}

/** Read a CSS custom property from the current theme. */
function cssVar(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

/**************************************
 * LIGHT / DARK THEME
 **************************************/
(function () {
  const root = document.documentElement;
  const toggle = document.getElementById("themeToggle");
  const media = window.matchMedia ? window.matchMedia("(prefers-color-scheme: dark)") : null;

  function storedTheme() {
    try { return localStorage.getItem("theme"); } catch { return null; }
  }

  function apply(theme) {
    root.setAttribute("data-theme", theme);
    if (toggle) {
      const next = theme === "dark" ? "light" : "dark";
      toggle.setAttribute("aria-label", `Switch to ${next} mode`);
    }
    document.dispatchEvent(new CustomEvent("themechange", { detail: { theme } }));
  }

  apply(root.getAttribute("data-theme") || "light");

  if (toggle) {
    toggle.addEventListener("click", () => {
      const next = root.getAttribute("data-theme") === "dark" ? "light" : "dark";
      try { localStorage.setItem("theme", next); } catch { /* storage unavailable */ }
      apply(next);
    });
  }

  // Follow the OS setting until the user picks a theme explicitly.
  if (media) {
    media.addEventListener("change", e => {
      const saved = storedTheme();
      if (saved !== "light" && saved !== "dark") apply(e.matches ? "dark" : "light");
    });
  }
})();
