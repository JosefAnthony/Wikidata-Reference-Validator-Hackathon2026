/**************************************
 * ELEMENTS & STATE
 **************************************/
const els = {
  form: document.getElementById("searchForm"),
  input: document.getElementById("search"),
  clear: document.getElementById("searchClear"),
  suggestions: document.getElementById("suggestions"),
  result: document.getElementById("result"),
};

const state = {
  selected: null,        // { qid, label } picked from autocomplete
  suggestions: [],
  activeIndex: -1,
  searchRequestId: 0,
  report: null,          // last successful /validate response (+ duration)
  filters: { status: "all", category: "all", text: "" },
};

const QID_PATTERN = /^[Qq]\d+$/;

/**************************************
 * AUTOCOMPLETE
 **************************************/
function debounce(fn, wait) {
  let timer;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), wait);
  };
}

function closeSuggestions() {
  state.suggestions = [];
  state.activeIndex = -1;
  els.suggestions.innerHTML = "";
  els.suggestions.hidden = true;
  els.input.setAttribute("aria-expanded", "false");
  els.input.removeAttribute("aria-activedescendant");
}

function renderSuggestions() {
  if (!state.suggestions.length) {
    closeSuggestions();
    return;
  }

  els.suggestions.innerHTML = state.suggestions.map((item, i) => `
    <li id="suggestion-${i}" role="option" class="suggestion ${i === state.activeIndex ? "is-active" : ""}"
        aria-selected="${i === state.activeIndex}" data-index="${i}">
      <span class="suggestion__text">
        <span class="suggestion__label">${escapeHtml(item.label)}</span>
        ${item.description ? `<span class="suggestion__desc">${escapeHtml(item.description)}</span>` : ""}
      </span>
      <span class="qid-pill">${escapeHtml(item.id)}</span>
    </li>
  `).join("");

  els.suggestions.hidden = false;
  els.input.setAttribute("aria-expanded", "true");
  if (state.activeIndex >= 0) {
    els.input.setAttribute("aria-activedescendant", `suggestion-${state.activeIndex}`);
    document.getElementById(`suggestion-${state.activeIndex}`)?.scrollIntoView({ block: "nearest" });
  } else {
    els.input.removeAttribute("aria-activedescendant");
  }
}

const fetchSuggestions = debounce(async query => {
  const requestId = ++state.searchRequestId;
  try {
    const response = await fetch(`/search?q=${encodeURIComponent(query)}`);
    const data = await response.json();
    // Ignore responses that arrive after a newer query was typed.
    if (requestId !== state.searchRequestId) return;
    state.suggestions = Array.isArray(data) ? data : [];
    state.activeIndex = -1;
    renderSuggestions();
  } catch {
    if (requestId === state.searchRequestId) closeSuggestions();
  }
}, 220);

function selectSuggestion(index) {
  const item = state.suggestions[index];
  if (!item) return;
  state.selected = { qid: item.id, label: item.label };
  els.input.value = item.label;
  closeSuggestions();
  validate(item.id, item.label);
}

els.input.addEventListener("input", () => {
  const query = els.input.value.trim();
  state.selected = null;
  els.clear.hidden = !els.input.value;

  if (query.length < 2) {
    state.searchRequestId++;
    closeSuggestions();
    return;
  }
  fetchSuggestions(query);
});

els.input.addEventListener("keydown", e => {
  const open = !els.suggestions.hidden && state.suggestions.length > 0;

  if (e.key === "ArrowDown" && open) {
    e.preventDefault();
    state.activeIndex = (state.activeIndex + 1) % state.suggestions.length;
    renderSuggestions();
  } else if (e.key === "ArrowUp" && open) {
    e.preventDefault();
    state.activeIndex = state.activeIndex <= 0 ? state.suggestions.length - 1 : state.activeIndex - 1;
    renderSuggestions();
  } else if (e.key === "Enter" && open && state.activeIndex >= 0) {
    e.preventDefault();
    selectSuggestion(state.activeIndex);
  } else if (e.key === "Escape") {
    closeSuggestions();
  }
});

els.suggestions.addEventListener("mousedown", e => {
  // mousedown (not click) so the input doesn't lose focus first
  const li = e.target.closest("[data-index]");
  if (!li) return;
  e.preventDefault();
  selectSuggestion(Number(li.dataset.index));
});

document.addEventListener("click", e => {
  if (!els.form.contains(e.target)) closeSuggestions();
});

els.clear.addEventListener("click", () => {
  els.input.value = "";
  els.clear.hidden = true;
  state.selected = null;
  closeSuggestions();
  els.input.focus();
});

els.form.addEventListener("submit", e => {
  e.preventDefault();
  const typed = els.input.value.trim();
  if (!typed) {
    els.input.focus();
    els.form.classList.add("is-invalid");
    setTimeout(() => els.form.classList.remove("is-invalid"), 600);
    return;
  }
  closeSuggestions();
  if (state.selected) {
    validate(state.selected.qid, state.selected.label);
  } else if (QID_PATTERN.test(typed)) {
    validate(typed.toUpperCase(), typed.toUpperCase());
  } else {
    validate(null, typed);
  }
});

document.querySelectorAll("[data-example-qid]").forEach(btn => {
  btn.addEventListener("click", () => {
    els.input.value = btn.dataset.exampleLabel;
    els.clear.hidden = false;
    state.selected = { qid: btn.dataset.exampleQid, label: btn.dataset.exampleLabel };
    validate(state.selected.qid, state.selected.label);
  });
});

/**************************************
 * VALIDATION
 **************************************/
let elapsedTimer = null;

function showLoading(label) {
  document.body.classList.add("has-results");
  const start = Date.now();
  els.result.innerHTML = `
    <div class="loading" role="status">
      <div class="loading__head">
        <span class="spinner" aria-hidden="true"></span>
        <div>
          <p class="loading__title">Checking references for <strong>${escapeHtml(label)}</strong>&hellip;</p>
          <p class="loading__hint">
            <span id="elapsed">0s</span> elapsed &middot; items with many references can take up to a minute
          </p>
        </div>
      </div>
      <div class="skeleton" aria-hidden="true">
        <span class="skeleton__line w-40"></span>
        <span class="skeleton__line w-90"></span>
        <span class="skeleton__line w-75"></span>
        <span class="skeleton__line w-85"></span>
        <span class="skeleton__line w-60"></span>
      </div>
    </div>`;

  clearInterval(elapsedTimer);
  elapsedTimer = setInterval(() => {
    const el = document.getElementById("elapsed");
    if (el) el.textContent = `${Math.floor((Date.now() - start) / 1000)}s`;
  }, 1000);
}

function showNotice(kind, message, { retry } = {}) {
  const icon = kind === "error" ? "bi-exclamation-octagon" : "bi-info-circle";
  els.result.innerHTML = `
    <div class="notice notice--${kind}" role="${kind === "error" ? "alert" : "status"}">
      <i class="bi ${icon} notice__icon" aria-hidden="true"></i>
      <div class="notice__body">
        <p class="notice__message">${escapeHtml(message)}</p>
        <div class="notice__actions">
          ${retry ? `<button type="button" class="btn-ghost" data-action="retry"><i class="bi bi-arrow-clockwise"></i> Try again</button>` : ""}
          <button type="button" class="btn-ghost" data-action="new-search"><i class="bi bi-search"></i> New search</button>
        </div>
      </div>
    </div>`;
  if (retry) els.result.querySelector('[data-action="retry"]').addEventListener("click", retry);
}

async function validate(qid, label, { updateHistory = true } = {}) {
  showLoading(label || qid);
  const start = Date.now();

  try {
    const response = await fetch("/validate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ qid, query: qid ? "" : label }),
    });

    let data;
    try {
      data = await response.json();
    } catch {
      throw new Error(`The server returned an unexpected response (HTTP ${response.status}).`);
    }
    const duration = Math.round((Date.now() - start) / 1000);

    if (data.error) {
      showNotice("error", data.error, { retry: () => validate(qid, label) });
      return;
    }

    if (updateHistory && data.qid) {
      const url = `${window.location.pathname}?item=${encodeURIComponent(data.qid)}`;
      history.pushState({ qid: data.qid, label: data.label }, "", url);
    }
    if (data.label) {
      document.title = `${data.label} – Wikidata Reference Validator`;
      els.input.value = data.label;
      els.clear.hidden = false;
      state.selected = { qid: data.qid, label: data.label };
    }

    if (data.message) {
      const retry = data.qid
        ? () => validate(data.qid, data.label || data.qid, { updateHistory: false })
        : undefined;
      showNotice("info", data.message, { retry });
      return;
    }

    state.report = { ...data, duration };
    state.filters = { status: "all", category: "all", text: "" };
    saveReport(state.report);
    renderReport();
  } catch (err) {
    showNotice("error", err.message || String(err), { retry: () => validate(qid, label) });
  } finally {
    clearInterval(elapsedTimer);
  }
}

function saveReport(report) {
  const refs = report.references;
  try {
    localStorage.setItem("reportData", JSON.stringify({
      qid: report.qid,
      label: report.label,
      description: report.description,
      aliveCount: refs.filter(r => r.status === "alive").length,
      deadCount: refs.filter(r => r.status === "dead").length,
      references: refs,
      duration: report.duration,
      checkedAt: new Date().toISOString(),
    }));
  } catch { /* storage unavailable: report page will show its empty state */ }
}

/**************************************
 * RESULTS RENDERING
 **************************************/
function summarize(refs) {
  const counts = { total: refs.length, alive: 0, dead: 0, archived: 0 };
  const categories = {};
  const domains = new Set();
  refs.forEach(r => {
    if (r.status === "alive") counts.alive++;
    if (r.status === "dead") counts.dead++;
    if (r.status === "dead" && r.suggestedArchive) counts.archived++;
    const cat = r.sourceCategory || "other";
    categories[cat] = categories[cat] || { total: 0, dead: 0 };
    categories[cat].total++;
    if (r.status === "dead") categories[cat].dead++;
    if (r.domain) domains.add(r.domain);
  });
  counts.domains = domains.size;
  counts.statements = new Set(refs.map(r => r.propertyId)).size;
  return { counts, categories };
}

function groupByProperty(refs) {
  const groups = new Map();
  refs.forEach(r => {
    const key = r.propertyId || r.propertyLabel || "other";
    if (!groups.has(key)) {
      groups.set(key, { id: key, label: r.propertyLabel || key, refs: [], dead: 0 });
    }
    const g = groups.get(key);
    g.refs.push(r);
    if (r.status === "dead") g.dead++;
  });
  // Statements with broken references first, then alphabetical.
  return [...groups.values()].sort((a, b) => (b.dead - a.dead) || a.label.localeCompare(b.label));
}

function matchesFilters(ref) {
  const { status, category, text } = state.filters;
  if (status === "dead" && ref.status !== "dead") return false;
  if (status === "alive" && ref.status !== "alive") return false;
  if (status === "archived" && !(ref.status === "dead" && ref.suggestedArchive)) return false;
  if (category !== "all" && (ref.sourceCategory || "other") !== category) return false;
  if (text) {
    const haystack = `${ref.url} ${ref.propertyLabel} ${ref.statementValue} ${ref.domain}`.toLowerCase();
    if (!haystack.includes(text)) return false;
  }
  return true;
}

function displayUrl(url) {
  return url.replace(/^https?:\/\/(www\.)?/i, "").replace(/\/$/, "");
}

function wikidataUrl(qid, pid) {
  return `https://www.wikidata.org/wiki/${encodeURIComponent(qid)}${pid ? `#${encodeURIComponent(pid)}` : ""}`;
}

function renderReport() {
  const report = state.report;
  const { counts, categories } = summarize(report.references);
  const health = counts.total ? Math.round((counts.alive / counts.total) * 100) : 0;
  const deadPct = counts.total ? Math.round((counts.dead / counts.total) * 100) : 0;
  const groups = groupByProperty(report.references);
  const qid = report.qid;

  const lede = counts.dead
    ? `<strong>${escapeHtml(report.label)}</strong> has <strong>${counts.total}</strong> referenced URLs across
       <strong>${counts.statements}</strong> statements. <strong class="text-dead">${counts.dead}</strong>
       (${deadPct}%) could not be reached${counts.archived ? `; archived copies were found for <strong>${counts.archived}</strong> of them` : ""}.`
    : `<strong>${escapeHtml(report.label)}</strong> has <strong>${counts.total}</strong> referenced URLs across
       <strong>${counts.statements}</strong> statements, and every one of them responded. Nice.`;

  const categoryOrder = Object.keys(SOURCE_CATEGORIES).filter(k => categories[k]);

  els.result.innerHTML = `
    <div class="article-layout">
      <nav class="toc" aria-label="Contents">
        <details class="toc__box" open>
          <summary class="toc__title">Contents</summary>
          <ol class="toc__list">
            <li><a href="#overview">Overview</a></li>
            ${groups.map(g => `
              <li data-toc-for="${escapeHtml(g.id)}">
                <a href="#sec-${escapeHtml(g.id)}">
                  <span class="toc__label">${escapeHtml(g.label)}</span>
                  ${g.dead ? `<span class="toc__badge" title="${g.dead} unreachable">${g.dead}</span>` : ""}
                </a>
              </li>`).join("")}
            <li><a href="#categories">Categories</a></li>
          </ol>
        </details>
      </nav>

      <article class="article">
        <header class="article__header">
          <div>
            <h1 class="article__title">${escapeHtml(report.label)}</h1>
            <p class="article__tagline">
              <a href="${wikidataUrl(qid)}" target="_blank" rel="noopener">${escapeHtml(qid)}</a>
              <span class="sep">/</span>
              <span>Checked against the live web in ${report.duration}s</span>
              <span class="sep">/</span>
              <span>${counts.domains} domains</span>
            </p>
          </div>
          <div class="article__actions">
            <a class="btn-ghost" href="${wikidataUrl(qid)}" target="_blank" rel="noopener" title="Edit on Wikidata">
              <i class="bi bi-pencil-square"></i><span>Edit</span>
            </a>
            <a class="btn-ghost" href="/report" target="_blank" rel="noopener" title="Open full report">
              <i class="bi bi-bar-chart"></i><span>Report</span>
            </a>
            <button type="button" class="btn-ghost" data-action="revalidate" title="Validate this item again">
              <i class="bi bi-arrow-clockwise"></i><span>Validate again</span>
            </button>
            <button type="button" class="btn-ghost" data-action="share" title="Copy link to this result">
              <i class="bi bi-link-45deg"></i><span>Share</span>
            </button>
          </div>
        </header>
        ${report.description ? `<p class="article__desc">${escapeHtml(report.description)}</p>` : ""}

        <div class="summary" role="group" aria-label="Reference health summary">
          <div class="summary__cell">
            <div class="summary__label">Health</div>
            <div class="summary__value">${health}%<small>reachable</small></div>
            <div class="meter" role="img" aria-label="${counts.alive} alive, ${counts.dead} unreachable">
              <span class="meter__alive" style="width:${counts.total ? (counts.alive / counts.total) * 100 : 0}%"></span>
              <span class="meter__dead" style="width:${counts.total ? (counts.dead / counts.total) * 100 : 0}%"></span>
            </div>
          </div>
          <div class="summary__cell summary__cell--alive">
            <div class="summary__label">Alive</div>
            <div class="summary__value">${counts.alive}<small>/ ${counts.total}</small></div>
          </div>
          <div class="summary__cell summary__cell--dead">
            <div class="summary__label">Unreachable</div>
            <div class="summary__value">${counts.dead}</div>
          </div>
          <div class="summary__cell summary__cell--archive">
            <div class="summary__label">Archived</div>
            <div class="summary__value">${counts.archived}</div>
          </div>
        </div>

        <p class="article__lede">${lede}</p>

        <section class="filters" id="overview" aria-label="Filter references">
          <div class="filters__row">
            <div class="segmented" role="radiogroup" aria-label="Status">
              ${[
                ["all", "All", counts.total],
                ["dead", "Unreachable", counts.dead],
                ["archived", "Archived", counts.archived],
                ["alive", "Alive", counts.alive],
              ].map(([value, text, n]) => `
                <button type="button" role="radio" class="segmented__btn" data-status="${value}"
                        aria-checked="${state.filters.status === value}">
                  ${text}<span class="count">${n}</span>
                </button>`).join("")}
            </div>
            <div class="filter-text">
              <i class="bi bi-search" aria-hidden="true"></i>
              <label class="visually-hidden" for="filterText">Filter by URL, domain or statement</label>
              <input type="search" id="filterText" placeholder="Filter by URL, domain or statement">
            </div>
          </div>

          <div class="category-chips" role="radiogroup" aria-label="Source type">
            <button type="button" role="radio" class="chip chip--filter" data-category="all"
                    aria-checked="${state.filters.category === "all"}">All sources</button>
            ${categoryOrder.map(key => {
              const meta = sourceCategoryMeta(key);
              const c = categories[key];
              return `
                <button type="button" role="radio" class="chip chip--filter" data-category="${key}"
                        aria-checked="${state.filters.category === key}">
                  <i class="bi ${meta.icon}"></i> ${meta.label}<span class="count">${c.total}</span>${c.dead ? `<span class="count count--dead" title="${c.dead} unreachable">${c.dead}</span>` : ""}
                </button>`;
            }).join("")}
          </div>
        </section>

        <div id="sections"></div>

        <footer class="catlinks" id="categories">
          <span class="catlinks__label">Categories:</span>
          <ul class="catlinks__list">
            ${counts.dead ? `<li><button type="button" data-status-link="dead">Items with unreachable references</button></li>` : ""}
            ${counts.archived ? `<li><button type="button" data-status-link="archived">References with archived copies</button></li>` : ""}
            ${counts.dead && counts.archived < counts.dead ? `<li><span>Dead references without archives (${counts.dead - counts.archived})</span></li>` : ""}
            ${categoryOrder.map(key => `
              <li><button type="button" data-category-link="${key}">${sourceCategoryMeta(key).label} (${categories[key].total})</button></li>
            `).join("")}
          </ul>
        </footer>
      </article>
    </div>`;

  renderSections();
  bindReportEvents();
  els.result.querySelector(".article")?.scrollIntoView({ behavior: "smooth", block: "start" });
}

function renderReference(ref) {
  const qid = state.report.qid;
  const isDead = ref.status === "dead";
  const meta = sourceCategoryMeta(ref.sourceCategory);
  const statusLabel = isDead ? "Unreachable" : ref.status === "alive" ? "Alive" : ref.status;
  const http = ref.httpStatus ? `HTTP ${ref.httpStatus}` : isDead ? "No response" : "";
  const archive = ref.suggestedArchive;

  return `
    <li class="ref ${isDead ? "ref--dead" : "ref--alive"}">
      <span class="ref__status" title="${escapeHtml(statusLabel)}${http ? ` (${escapeHtml(http)})` : ""}">
        <i class="bi ${isDead ? "bi-x-circle-fill" : "bi-check-circle-fill"}" aria-hidden="true"></i>
        <span class="visually-hidden">${escapeHtml(statusLabel)}</span>
      </span>

      <div class="ref__body">
        <a class="ref__url" href="${escapeHtml(safeUrl(ref.url))}" target="_blank" rel="noopener noreferrer"
           title="${escapeHtml(ref.url)}">${escapeHtml(displayUrl(ref.url))}</a>
        <div class="ref__meta">
          <span class="tag"><i class="bi ${meta.icon}"></i> ${meta.label}</span>
          ${ref.statementValue ? `<span class="ref__value" title="Statement value">${escapeHtml(ref.statementValue)}</span>` : ""}
          ${http ? `<span class="ref__http ${isDead ? "text-dead" : ""}">${escapeHtml(http)}</span>` : ""}
        </div>

        ${isDead ? `
          <div class="ref__archive ${archive ? "" : "ref__archive--none"}">
            ${archive ? `
              <i class="bi bi-archive" aria-hidden="true"></i>
              <span>Archived copy${formatArchiveDate(archive.timestamp) ? ` from <strong>${escapeHtml(formatArchiveDate(archive.timestamp))}</strong>` : ""}</span>
              <a href="${escapeHtml(safeUrl(archive.archiveUrl))}" target="_blank" rel="noopener noreferrer">Open snapshot <i class="bi bi-box-arrow-up-right"></i></a>
              <button type="button" class="link-btn" data-copy="${escapeHtml(archive.archiveUrl)}">Copy archive URL</button>
            ` : `
              <i class="bi bi-inbox" aria-hidden="true"></i>
              <span>No snapshot found automatically.</span>
              <a href="https://web.archive.org/web/*/${escapeHtml(encodeURI(ref.url))}" target="_blank" rel="noopener noreferrer">Search the Wayback Machine</a>
            `}
          </div>` : ""}
      </div>

      <div class="ref__actions">
        <a class="icon-btn" href="${wikidataUrl(qid, ref.propertyId)}" target="_blank" rel="noopener"
           title="Edit this statement on Wikidata" aria-label="Edit this statement on Wikidata">
          <i class="bi bi-pencil"></i>
        </a>
        <button type="button" class="icon-btn" data-copy="${escapeHtml(ref.url)}" title="Copy URL" aria-label="Copy URL">
          <i class="bi bi-clipboard"></i>
        </button>
      </div>
    </li>`;
}

function renderSections() {
  const container = document.getElementById("sections");
  if (!container) return;

  const groups = groupByProperty(state.report.references.filter(matchesFilters));
  const visible = new Set(groups.map(g => g.id));

  // Dim table-of-contents entries for sections hidden by the filters.
  els.result.querySelectorAll("[data-toc-for]").forEach(li => {
    li.classList.toggle("is-filtered", !visible.has(li.dataset.tocFor));
  });

  if (!groups.length) {
    container.innerHTML = `
      <div class="empty">
        <i class="bi bi-funnel" aria-hidden="true"></i>
        <p>No references match these filters.</p>
        <button type="button" class="btn-ghost" data-action="clear-filters">Clear filters</button>
      </div>`;
    return;
  }

  container.innerHTML = groups.map(g => {
    const refs = [...g.refs].sort((a, b) => (a.status === "dead" ? 0 : 1) - (b.status === "dead" ? 0 : 1));
    return `
      <section class="statement" id="sec-${escapeHtml(g.id)}">
        <h2 class="statement__title">
          <span>${escapeHtml(g.label)}</span>
          <a class="statement__pid" href="https://www.wikidata.org/wiki/Property:${encodeURIComponent(g.id)}"
             target="_blank" rel="noopener" title="View property on Wikidata">${escapeHtml(g.id)}</a>
          <span class="statement__count">
            ${refs.length} reference${refs.length === 1 ? "" : "s"}${g.dead ? ` &middot; <span class="text-dead">${g.dead} unreachable</span>` : ""}
          </span>
        </h2>
        <ul class="ref-list">${refs.map(renderReference).join("")}</ul>
      </section>`;
  }).join("");
  observeSections();
}

function setFilter(key, value) {
  state.filters[key] = value;
  els.result.querySelectorAll("[data-status]").forEach(b =>
    b.setAttribute("aria-checked", String(b.dataset.status === state.filters.status)));
  els.result.querySelectorAll("[data-category]").forEach(b =>
    b.setAttribute("aria-checked", String(b.dataset.category === state.filters.category)));
  renderSections();
}

function bindReportEvents() {
  const filterText = document.getElementById("filterText");
  filterText?.addEventListener("input", debounce(() => {
    setFilter("text", filterText.value.trim().toLowerCase());
  }, 150));
}

/** Highlight the table-of-contents entry for the section currently in view. */
let sectionObserver = null;
function observeSections() {
  sectionObserver?.disconnect();
  if (!("IntersectionObserver" in window)) return;
  const links = new Map(
    [...els.result.querySelectorAll(".toc__list a")].map(a => [a.getAttribute("href").slice(1), a])
  );
  sectionObserver = new IntersectionObserver(entries => {
    entries.forEach(entry => {
      if (!entry.isIntersecting) return;
      links.forEach(a => a.classList.remove("is-current"));
      links.get(entry.target.id)?.classList.add("is-current");
    });
  }, { rootMargin: "-20% 0px -70% 0px" });
  els.result.querySelectorAll(".statement, #overview, #categories").forEach(el => sectionObserver.observe(el));
}

// "/" focuses the search box (like most search-first sites).
document.addEventListener("keydown", e => {
  if (e.key !== "/" || e.metaKey || e.ctrlKey || e.altKey) return;
  const tag = document.activeElement?.tagName;
  if (tag === "INPUT" || tag === "TEXTAREA" || document.activeElement?.isContentEditable) return;
  e.preventDefault();
  els.input.focus();
  els.input.select();
});

/**************************************
 * DELEGATED ACTIONS (results area)
 **************************************/
els.result.addEventListener("click", e => {
  const target = e.target.closest("button, a");
  if (!target) return;

  if (target.dataset.status) return setFilter("status", target.dataset.status);
  if (target.dataset.category) return setFilter("category", target.dataset.category);

  if (target.dataset.statusLink || target.dataset.categoryLink) {
    if (target.dataset.statusLink) setFilter("status", target.dataset.statusLink);
    if (target.dataset.categoryLink) setFilter("category", target.dataset.categoryLink);
    document.getElementById("overview")?.scrollIntoView({ behavior: "smooth", block: "start" });
    return;
  }

  if (target.dataset.copy) return copyText(target.dataset.copy, "Copied to clipboard");

  switch (target.dataset.action) {
    case "new-search":
      resetSearch();
      break;
    case "revalidate":
      if (state.report) {
        validate(state.report.qid, state.report.label, { updateHistory: false });
      }
      break;
    case "share":
      copyText(window.location.href, "Link copied — share it to show this result");
      break;
    case "clear-filters": {
      const filterText = document.getElementById("filterText");
      if (filterText) filterText.value = "";
      state.filters = { status: "all", category: "all", text: "" };
      setFilter("status", "all");
      break;
    }
  }
});

async function copyText(text, message) {
  try {
    await navigator.clipboard.writeText(text);
    toast(message);
  } catch {
    toast("Couldn't copy — your browser blocked clipboard access");
  }
}

function toast(message) {
  let el = document.getElementById("toast");
  if (!el) {
    el = document.createElement("div");
    el.id = "toast";
    el.className = "toast-msg";
    el.setAttribute("role", "status");
    document.body.appendChild(el);
  }
  el.textContent = message;
  el.classList.add("is-visible");
  clearTimeout(el._timer);
  el._timer = setTimeout(() => el.classList.remove("is-visible"), 2200);
}

/**************************************
 * RESET, HISTORY & DEEP LINKS
 **************************************/
function resetSearch({ updateHistory = true } = {}) {
  clearInterval(elapsedTimer);
  state.report = null;
  state.selected = null;
  els.result.innerHTML = "";
  els.input.value = "";
  els.clear.hidden = true;
  document.body.classList.remove("has-results");
  document.title = "Wikidata Reference Validator";
  if (updateHistory) history.pushState({}, "", window.location.pathname);
  window.scrollTo({ top: 0, behavior: "smooth" });
  els.input.focus();
}

function itemFromUrl() {
  const item = new URLSearchParams(window.location.search).get("item");
  return item && QID_PATTERN.test(item) ? item.toUpperCase() : null;
}

window.addEventListener("popstate", () => {
  const qid = itemFromUrl();
  if (qid) {
    els.input.value = qid;
    validate(qid, qid, { updateHistory: false });
  } else {
    resetSearch({ updateHistory: false });
  }
});

// Open a shared link like /?item=Q42 directly.
(function initFromUrl() {
  const qid = itemFromUrl();
  if (qid) {
    els.input.value = qid;
    els.clear.hidden = false;
    validate(qid, qid, { updateHistory: false });
  }
})();
