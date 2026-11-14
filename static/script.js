/**************************************
 * AUTOCOMPLETE SEARCH
 **************************************/
const searchInput = document.getElementById("search");
const suggestionsList = document.getElementById("suggestions");
const QID_PATTERN = /^Q[1-9]\d*$/i;

function clearSelectedItem() {
  searchInput.removeAttribute("data-qid");
  searchInput.removeAttribute("data-label");
}

function showSearchError(message) {
  const feedback = document.getElementById("searchFeedback");
  searchInput.classList.add("is-invalid");
  if (feedback) feedback.textContent = message;

  document.getElementById("result").innerHTML = `
    <div class="card p-4 text-center">
      <div class="alert alert-warning mb-0" role="alert">
        <i class="bi bi-exclamation-triangle"></i>
        <span id="searchErrorMessage"></span>
      </div>
    </div>`;

  document.getElementById("searchErrorMessage").textContent = message;
  searchInput.focus();
}

function clearSearchError() {
  const feedback = document.getElementById("searchFeedback");
  searchInput.classList.remove("is-invalid");
  if (feedback) feedback.textContent = "";
}

searchInput.addEventListener("input", async function () {
  const query = this.value.trim();

  // Any manual edit invalidates the previously selected item.
  clearSelectedItem();
  clearSearchError();
  suggestionsList.innerHTML = "";

  if (query.length < 3) return;

  try {
    const response = await fetch(
      `https://www.wikidata.org/w/api.php?action=wbsearchentities&search=${encodeURIComponent(
        query
      )}&language=en&format=json&origin=*`
    );
    const data = await response.json();

    (data.search || []).forEach(item => {
      if (!item.id || !QID_PATTERN.test(item.id)) return;

      const li = document.createElement("li");
      li.className = "list-group-item list-group-item-action";

      // label + description (Wikidata-style)
      li.innerHTML = `
        <div>
          <strong>${item.label}</strong>
          <div class="small text-muted">${item.description || ""}</div>
        </div>
      `;

      li.onclick = () => {
        searchInput.value = item.label;
        searchInput.setAttribute("data-qid", item.id);
        searchInput.setAttribute("data-label", item.label);
        clearSearchError();
        suggestionsList.innerHTML = "";
        searchInput.focus();
      };

      suggestionsList.appendChild(li);
    });
  } catch (err) {
    suggestionsList.innerHTML = "";
  }
});

/**************************************
 * PROPERTY LABEL CACHE (UNCHANGED)
 **************************************/
const propertyLabelCache = {};

async function getPropertyLabel(pid) {
  if (!pid) return "";
  if (propertyLabelCache[pid]) return propertyLabelCache[pid];

  try {
    const response = await fetch(
      `https://www.wikidata.org/w/api.php?action=wbgetentities&ids=${pid}&format=json&languages=en&origin=*`
    );
    const data = await response.json();
    const label = data.entities[pid]?.labels?.en?.value || "";
    propertyLabelCache[pid] = label;
    return label;
  } catch {
    return "";
  }
}

/**************************************
 * ITEM RESOLUTION
 **************************************/
async function resolveQidFromLabel(label) {
  const trimmed = label.trim();

  if (!trimmed) {
    return { error: "Please select a Wikidata item before validating." };
  }

  // Allow users to paste a QID directly.
  if (QID_PATTERN.test(trimmed)) {
    const qid = trimmed.toUpperCase();
    return { qid, label: qid };
  }

  let results;
  try {
    const response = await fetch(`/search?q=${encodeURIComponent(trimmed)}`);
    if (!response.ok) throw new Error("Lookup failed");
    results = await response.json();
  } catch (err) {
    return {
      error: "Could not look up that item. Please select one from the suggestions."
    };
  }

  const normalized = trimmed.toLowerCase();
  const exactMatches = (Array.isArray(results) ? results : []).filter(
    item =>
      item.id &&
      QID_PATTERN.test(item.id) &&
      (item.label || "").trim().toLowerCase() === normalized
  );

  if (exactMatches.length === 1) {
    return { qid: exactMatches[0].id, label: exactMatches[0].label };
  }

  if (exactMatches.length > 1) {
    return {
      error: `Multiple Wikidata items match "${trimmed}". Please choose one from the suggestions.`
    };
  }

  return {
    error: `No Wikidata item found for "${trimmed}". Please select one from the suggestions.`
  };
}

/**************************************
 * VALIDATE REFERENCES (FIXED + SAFE)
 **************************************/
async function validate() {
  const input = searchInput;
  const typedValue = input.value.trim();
  const selectedLabel = (input.getAttribute("data-label") || "").trim();
  let label = selectedLabel || typedValue;
  let qid = (input.getAttribute("data-qid") || "").trim().toUpperCase();

  // A previous selection is only valid while the input still shows its label.
  if (
    qid &&
    selectedLabel &&
    selectedLabel.toLowerCase() !== typedValue.toLowerCase()
  ) {
    qid = "";
  }

  if (qid && !QID_PATTERN.test(qid)) {
    qid = "";
  }

  // If nothing was picked from the autocomplete, try to resolve the typed
  // label. Ambiguous or unknown labels are rejected with a clear message.
  if (!qid) {
    const resolved = await resolveQidFromLabel(typedValue);
    if (!resolved.qid) {
      showSearchError(resolved.error);
      return;
    }

    qid = resolved.qid;
    label = resolved.label || label;

    input.value = label;
    input.setAttribute("data-qid", qid);
    input.setAttribute("data-label", label);
  }

  clearSearchError();
  await runValidation(qid, label);
}

/**************************************
 * VALIDATE AGAIN (CURRENT ITEM)
 **************************************/
async function validateAgain() {
  const qid = (searchInput.getAttribute("data-qid") || "").trim().toUpperCase();
  const label =
    (searchInput.getAttribute("data-label") || "").trim() ||
    searchInput.value.trim() ||
    qid;

  if (!QID_PATTERN.test(qid)) {
    document.getElementById("searchSection").style.display = "block";
    showSearchError("Please select a Wikidata item before validating again.");
    return;
  }

  clearSearchError();
  await runValidation(qid, label);
}

/**************************************
 * RUN VALIDATION
 **************************************/
async function runValidation(qid, label) {
  const resultDiv = document.getElementById("result");
  const searchSection = document.getElementById("searchSection");

  searchSection.style.display = "none";

  let remaining = 30;
  resultDiv.innerHTML = `
    <div class="d-flex flex-column align-items-center justify-content-center my-5">
      <div class="fancy-loader"></div>
      <p class="mt-3 text-light fw-bold">...</p>
      <p id="countdownTimer" class="text-info small">
        Estimated time: ${remaining}s
      </p>
    </div>
  `;

  const countdownInterval = setInterval(() => {
    remaining--;
    const el = document.getElementById("countdownTimer");
    if (el) {
      el.textContent =
        remaining > 0 ? `Estimated time: ${remaining}s` : "Finalizing...";
    }
  }, 1000);

  const start = Date.now();

  try {
    const response = await fetch("/validate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ qid })
    });

    const data = await response.json();
    clearInterval(countdownInterval);
    const duration = Math.floor((Date.now() - start) / 1000);

    if (data.error) {
      resultDiv.innerHTML = `
        <div class="card p-4 text-center">
          <div class="alert alert-danger">
            <i class="bi bi-x-circle"></i> ${data.error}
          </div>
          <p class="small text-muted">Completed in ${duration}s</p>
          <button class="btn btn-lg btn-glass-action mt-3"
                  onclick="resetSearch()">🔄 Search Another Item</button>
        </div>`;
      return;
    }

    if (data.message) {
      resultDiv.innerHTML = `
        <div class="card p-4 text-center">
          <div class="alert alert-info">
            <i class="bi bi-info-circle"></i> ${data.message}
          </div>
          <p class="small text-muted">Completed in ${duration}s</p>
          <div class="d-flex justify-content-center gap-2 flex-wrap mt-3">
            <button class="btn btn-lg btn-glass-action"
                    onclick="validateAgain()">🔁 Validate Again</button>
            <button class="btn btn-lg btn-glass-action"
                    onclick="resetSearch()">🔄 Search Another Item</button>
          </div>
        </div>`;
      return;
    }

    const aliveCount = data.filter(r => r.status === "alive").length;
    const deadCount = data.filter(r => r.status === "dead").length;

    localStorage.setItem("reportData", JSON.stringify({
      qid: qid || label,
      label,
      aliveCount,
      deadCount,
      references: data,
      duration
    }));

    let html = `
      <div class="card p-4">
        <div class="d-flex justify-content-between align-items-center mb-3">
          <div class="d-flex gap-2">
            <button id="viewReportBtn" class="btn btn-info btn-sm">
              📊 View Report
            </button>
            <a href="https://www.wikidata.org/wiki/${qid}"
               target="_blank"
               class="btn btn-glass-warning btn-sm">
              ✏️ Edit Item
            </a>
          </div>
          <h3 class="mb-0 text-light">
            <i class="bi bi-list-check"></i>
            Results for ${label} (${qid})
          </h3>
        </div>

        <p>
          <span class="badge bg-success">
            <i class="bi bi-check-circle"></i> Alive: ${aliveCount}
          </span>
          <span class="badge bg-danger ms-2">
            <i class="bi bi-x-octagon"></i> Dead: ${deadCount}
          </span>
        </p>

        <ul class="list-group mt-3">
    `;

    data.forEach((ref, i) => {
      const badge = ref.status === "alive" ? "success" : "danger";
      const icon = ref.status === "alive" ? "check-circle" : "x-octagon";
      const collapseId = `collapseRef${i}`;

      html += `
        <li class="list-group-item p-3 rounded shadow-sm mb-2">
          <div class="d-flex justify-content-between align-items-center gap-2">
            <a href="${ref.url}" target="_blank" class="ref-link text-break">
              ${ref.url}
            </a>
            <div class="d-flex align-items-center gap-2">
              <span class="badge bg-${badge}">
                <i class="bi bi-${icon} me-1"></i> ${ref.status}
              </span>
              <button class="btn btn-sm btn-outline-secondary"
                      data-bs-toggle="collapse"
                      data-bs-target="#${collapseId}">
                Details
              </button>
            </div>
          </div>

          <div class="collapse mt-2" id="${collapseId}">
            <div class="p-2 border rounded bg-light">
              <div class="fw-bold">Statement:</div>
              <div>
                <strong>${ref.propertyLabel}</strong>
                ${ref.statementValue ? `: <em>${ref.statementValue}</em>` : ""}
              </div>

              ${
                ref.status === "dead"
                  ? `
                    <div class="mt-2 alert alert-warning p-2 mb-1">
                      <i class="bi bi-exclamation-triangle me-1"></i>
                      <strong>Reference unavailable.</strong>
                      Please review or update this reference.
                    </div>
                  `
                  : ""
              }

              ${
                ref.status === "dead" && ref.suggestedArchive
                  ? `
                    <div class="mt-2 small">
                      🔁 <strong>Suggested archived version:</strong>
                      <a href="${ref.suggestedArchive.archiveUrl}"
                         target="_blank"
                         class="fw-semibold link-primary">
                        View on Wayback Machine
                      </a>
                    </div>
                  `
                  : ""
              }

              ${
                ref.status === "dead" && !ref.suggestedArchive
                  ? `
                    <div class="mt-2 small text-muted">
                      📭 No archived snapshot found on Wayback Machine.
                    </div>
                  `
                  : ""
              }
            </div>
          </div>
        </li>
      `;
    });

    html += `
        </ul>
        <p class="small text-muted mt-2">Completed in ${duration}s</p>
        <div class="text-center mt-3 d-flex justify-content-center gap-2 flex-wrap">
          <button class="btn btn-lg btn-glass-action"
                  onclick="validateAgain()">🔁 Validate Again</button>
          <button class="btn btn-lg btn-glass-action"
                  onclick="resetSearch()">🔄 Search Another Item</button>
        </div>
      </div>
    `;

    resultDiv.innerHTML = html;

    document.getElementById("viewReportBtn").onclick = () => {
      window.open("/report", "_blank");
    };

  } catch (err) {
    clearInterval(countdownInterval);
    resultDiv.innerHTML = `
      <div class="card p-4 text-center">
        <div class="alert alert-danger">
          <i class="bi bi-bug"></i> Error: ${err}
        </div>
        <button class="btn btn-lg btn-glass-action mt-3"
                onclick="resetSearch()">🔄 Search Another Item</button>
      </div>`;
  }
}

/**************************************
 * GLOBAL COLLAPSE BUTTON TOGGLE (FIX)
 **************************************/
document.addEventListener("shown.bs.collapse", e => {
  const btn = document.querySelector(
    `[data-bs-target="#${e.target.id}"]`
  );
  if (btn) btn.textContent = "Hide";
});

document.addEventListener("hidden.bs.collapse", e => {
  const btn = document.querySelector(
    `[data-bs-target="#${e.target.id}"]`
  );
  if (btn) btn.textContent = "Details";
});

/**************************************
 * RESET SEARCH (UNCHANGED)
 **************************************/
function resetSearch() {
  const searchSection = document.getElementById("searchSection");
  searchInput.value = "";
  clearSelectedItem();
  clearSearchError();
  suggestionsList.innerHTML = "";
  document.getElementById("result").innerHTML = "";
  searchSection.style.display = "block";
  searchInput.focus();
}
