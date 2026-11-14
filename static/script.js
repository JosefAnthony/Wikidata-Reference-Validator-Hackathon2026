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

/**************************************
 * VALIDATE REFERENCES (FIXED + SAFE)
 **************************************/
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
          <button class="btn btn-lg btn-glass-action mt-3"
                  onclick="resetSearch()">🔄 Search Another Item</button>
        </div>`;
      return;
    }

    const aliveCount = data.filter(r => r.status === "alive").length;
    const deadCount = data.filter(r => r.status === "dead").length;
    const archivedCount = data.filter(r => r.status === "dead" && r.suggestedArchive).length;

    localStorage.setItem("reportData", JSON.stringify({
      qid: qid || label,
      label,
      aliveCount,
      deadCount,
      archivedCount,
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

        <p class="d-flex flex-wrap gap-2 align-items-center mb-3">
          <span class="badge bg-success">
            <i class="bi bi-check-circle"></i> Alive: ${aliveCount}
          </span>
          <span class="badge bg-danger">
            <i class="bi bi-x-octagon"></i> Dead: ${deadCount}
          </span>
          ${
            deadCount > 0
              ? `
              <span class="badge bg-info text-dark">
                <i class="bi bi-archive"></i> Archived available: ${archivedCount}/${deadCount}
              </span>
              `
              : ""
          }
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
                    <div class="mt-2 alert alert-warning p-2 mb-2">
                      <i class="bi bi-exclamation-triangle me-1"></i>
                      <strong>Reference unavailable.</strong>
                      Please review or update this reference on Wikidata.
                    </div>
                  `
                  : ""
              }

              ${
                ref.status === "dead" && ref.suggestedArchive
                  ? `
                    <div class="mt-2 p-3 rounded bg-white border border-info shadow-sm">
                      <div class="d-flex align-items-start justify-content-between flex-wrap gap-2">
                        <div class="flex-grow-1">
                          <div class="text-primary fw-bold mb-1">
                            <i class="bi bi-archive-fill me-1"></i> Suggested Archive (Internet Archive):
                          </div>
                          <a href="${ref.suggestedArchive.archiveUrl}"
                             target="_blank"
                             class="fw-semibold link-primary text-break">
                            ${ref.suggestedArchive.archiveUrl}
                          </a>
                          ${
                            ref.suggestedArchive.archiveDateLabel || ref.suggestedArchive.archiveDate
                              ? `
                                <div class="text-muted small mt-1">
                                  <i class="bi bi-calendar-event me-1"></i>
                                  Snapshot Date: <strong>${ref.suggestedArchive.archiveDateLabel || ref.suggestedArchive.archiveDate}</strong>
                                  ${
                                    ref.suggestedArchive.archiveDate
                                      ? ` &bull; Wikidata P2960: <code>${ref.suggestedArchive.archiveDate}</code>`
                                      : ""
                                  }
                                </div>
                              `
                              : ""
                          }
                        </div>
                        <div class="d-flex gap-2 align-self-start flex-wrap">
                          <button class="btn btn-sm btn-outline-primary"
                                  title="Copy Archive URL for Wikidata P1065"
                                  onclick="copyArchiveUrl('${encodeURIComponent(ref.suggestedArchive.archiveUrl)}', this, 'URL')">
                            <i class="bi bi-clipboard"></i> Copy URL
                          </button>
                          ${
                            ref.suggestedArchive.archiveDate
                              ? `
                              <button class="btn btn-sm btn-outline-secondary"
                                      title="Copy Archive Date for Wikidata P2960"
                                      onclick="copyArchiveUrl('${encodeURIComponent(ref.suggestedArchive.archiveDate)}', this, 'Date')">
                                <i class="bi bi-calendar-check"></i> Copy Date
                              </button>
                              `
                              : ""
                          }
                          <a href="${ref.suggestedArchive.archiveUrl}"
                             target="_blank"
                             class="btn btn-sm btn-primary">
                            <i class="bi bi-box-arrow-up-right"></i> Open Snapshot
                          </a>
                        </div>
                      </div>
                    </div>
                  `
                  : ""
              }

              ${
                ref.status === "dead" && !ref.suggestedArchive
                  ? `
                    <div class="mt-2 p-2 rounded bg-light border">
                      <div class="mt-1 small text-muted mb-2">
                        📭 No direct archived snapshot found on Wayback Machine.
                      </div>
                      <div class="d-flex gap-2 flex-wrap">
                        <a href="https://web.archive.org/web/*/${encodeURIComponent(ref.url)}"
                           target="_blank"
                           class="btn btn-sm btn-outline-secondary">
                          <i class="bi bi-search"></i> Search Wayback History
                        </a>
                        <a href="https://web.archive.org/save/${encodeURIComponent(ref.url)}"
                           target="_blank"
                           class="btn btn-sm btn-outline-secondary">
                          <i class="bi bi-cloud-arrow-up"></i> Save to Wayback
                        </a>
                      </div>
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
        <div class="text-center mt-3">
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
 * CLIPBOARD HELPER
 **************************************/
function copyArchiveUrl(encodedText, btn, type = "URL") {
  const text = decodeURIComponent(encodedText);
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(text).then(() => {
      const originalHTML = btn.innerHTML;
      btn.innerHTML = `<i class="bi bi-check-lg"></i> ${type} Copied!`;
      btn.classList.add("btn-success");
      setTimeout(() => {
        btn.innerHTML = originalHTML;
        btn.classList.remove("btn-success");
      }, 2000);
    }).catch(() => {
      prompt(`Copy ${type}:`, text);
    });
  } else {
    prompt(`Copy ${type}:`, text);
  }
}

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
