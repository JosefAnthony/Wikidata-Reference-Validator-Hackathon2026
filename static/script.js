/**************************************
 * AUTOCOMPLETE SEARCH (UNCHANGED FLOW)
 **************************************/
document.getElementById("search").addEventListener("input", async function () {
  const query = this.value;
  const suggestions = document.getElementById("suggestions");
  suggestions.innerHTML = "";

  if (query.length < 3) return;

  const response = await fetch(
    `https://www.wikidata.org/w/api.php?action=wbsearchentities&search=${encodeURIComponent(
      query
    )}&language=en&format=json&origin=*`
  );
  const data = await response.json();

  data.search.forEach(item => {
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
      const input = document.getElementById("search");
      input.value = item.label;
      input.setAttribute("data-qid", item.id);
      input.setAttribute("data-label", item.label);
      suggestions.innerHTML = "";
    };

    suggestions.appendChild(li);
  });
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

/*VALIDATE REFERENCES (FIXED + SAFE)*/
async function validate() {
  const input = document.getElementById("search");
  const qid = input.getAttribute("data-qid");
  const label = input.getAttribute("data-label") || input.value;
  const resultDiv = document.getElementById("result");
  const searchSection = document.getElementById("searchSection");

  // if (!qid && !label) {
  //   resultDiv.innerHTML = `
  //     <div class="alert alert-warning">
  //       <i class="bi bi-exclamation-triangle"></i>
  //       Please type a label or select an item first.
  //     </div>`;
  //   return;
  // }

  searchSection.style.display = "none";

  let remaining = 30;
  resultDiv.innerHTML = `
    <div class="d-flex flex-column align-items-center justify-content-center my-5">
      <div class="fancy-loader"></div>
      <p class="mt-3 text-light fw-bold">...</p>
      <p id="countdownTimer" class="text-info small">
        ${wrvMsg("wrv-loading-estimated-time", remaining)}
      </p>
    </div>
  `;

  const countdownInterval = setInterval(() => {
    remaining--;
    const el = document.getElementById("countdownTimer");
    if (el) {
      el.textContent =
        remaining > 0
          ? wrvMsg("wrv-loading-estimated-time", remaining)
          : wrvMsg("wrv-loading-finalizing");
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
          <p class="small text-muted">${wrvMsg("wrv-completed-in", duration)}</p>
          <button class="btn btn-lg btn-glass-action mt-3"
                  onclick="resetSearch()">🔄 ${wrvMsg("wrv-search-another")}</button>
        </div>`;
      return;
    }

    if (data.message) {
      resultDiv.innerHTML = `
        <div class="card p-4 text-center">
          <div class="alert alert-info">
            <i class="bi bi-info-circle"></i> ${data.message}
          </div>
          <p class="small text-muted">${wrvMsg("wrv-completed-in", duration)}</p>
          <button class="btn btn-lg btn-glass-action mt-3"
                  onclick="resetSearch()">🔄 ${wrvMsg("wrv-search-another")}</button>
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
              📊 ${wrvMsg("wrv-view-report")}
            </button>
            <a href="https://www.wikidata.org/wiki/${qid}"
               target="_blank"
               class="btn btn-glass-warning btn-sm">
              ✏️ ${wrvMsg("wrv-edit-item")}
            </a>
          </div>
          <h3 class="mb-0 text-light">
            <i class="bi bi-list-check"></i>
            ${wrvMsg("wrv-results-for", label, qid)}
          </h3>
        </div>

        <p>
          <span class="badge bg-success">
            <i class="bi bi-check-circle"></i> ${wrvMsg("wrv-alive-count", aliveCount)}
          </span>
          <span class="badge bg-danger ms-2">
            <i class="bi bi-x-octagon"></i> ${wrvMsg("wrv-dead-count", deadCount)}
          </span>
        </p>

        <ul class="list-group mt-3">
    `;

    data.forEach((ref, i) => {
      const badge = ref.status === "alive" ? "success" : "danger";
      const icon = ref.status === "alive" ? "check-circle" : "x-octagon";
      const collapseId = `collapseRef${i}`;
      const statusText = ref.status === "alive"
        ? wrvMsg("wrv-status-alive")
        : wrvMsg("wrv-status-dead");

      html += `
        <li class="list-group-item p-3 rounded shadow-sm mb-2">
          <div class="d-flex justify-content-between align-items-center gap-2">
            <a href="${ref.url}" target="_blank" class="ref-link text-break">
              ${ref.url}
            </a>
            <div class="d-flex align-items-center gap-2">
              <span class="badge bg-${badge}">
                <i class="bi bi-${icon} me-1"></i> ${statusText}
              </span>
              <button class="btn btn-sm btn-outline-secondary"
                      data-bs-toggle="collapse"
                      data-bs-target="#${collapseId}">
                ${wrvMsg("wrv-details-button")}
              </button>
            </div>
          </div>

          <div class="collapse mt-2" id="${collapseId}">
            <div class="p-2 border rounded bg-light">
              <div class="fw-bold">${wrvMsg("wrv-statement-label")}</div>
              <div>
                <strong>${ref.propertyLabel}</strong>
                ${ref.statementValue ? `: <em>${ref.statementValue}</em>` : ""}
              </div>

              ${
                ref.status === "dead"
                  ? `
                    <div class="mt-2 alert alert-warning p-2 mb-1">
                      <i class="bi bi-exclamation-triangle me-1"></i>
                      <strong>${wrvMsg("wrv-reference-unavailable")}</strong>
                      ${wrvMsg("wrv-review-reference")}
                    </div>
                  `
                  : ""
              }

              ${
                ref.status === "dead" && ref.suggestedArchive
                  ? `
                    <div class="mt-2 small">
                      🔁 <strong>${wrvMsg("wrv-suggested-archive")}</strong>
                      <a href="${ref.suggestedArchive.archiveUrl}"
                         target="_blank"
                         class="fw-semibold link-primary">
                        ${wrvMsg("wrv-view-wayback")}
                      </a>
                    </div>
                  `
                  : ""
              }

              ${
                ref.status === "dead" && !ref.suggestedArchive
                  ? `
                    <div class="mt-2 small text-muted">
                      📭 ${wrvMsg("wrv-no-archive")}
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
        <p class="small text-muted mt-2">${wrvMsg("wrv-completed-in", duration)}</p>
        <div class="text-center mt-3">
          <button class="btn btn-lg btn-glass-action"
                  onclick="resetSearch()">🔄 ${wrvMsg("wrv-search-another")}</button>
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
          <i class="bi bi-bug"></i> ${wrvMsg("wrv-error", err)}
        </div>
        <button class="btn btn-lg btn-glass-action mt-3"
                onclick="resetSearch()">🔄 ${wrvMsg("wrv-search-another")}</button>
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
  if (btn) btn.textContent = wrvMsg("wrv-hide-button");
});

document.addEventListener("hidden.bs.collapse", e => {
  const btn = document.querySelector(
    `[data-bs-target="#${e.target.id}"]`
  );
  if (btn) btn.textContent = wrvMsg("wrv-details-button");
});

/**************************************
 * RESET SEARCH (UNCHANGED)
 **************************************/
function resetSearch() {
  const searchSection = document.getElementById("searchSection");
  document.getElementById("search").value = "";
  document.getElementById("search").removeAttribute("data-qid");
  document.getElementById("search").removeAttribute("data-label");
  document.getElementById("result").innerHTML = "";
  searchSection.style.display = "block";
}