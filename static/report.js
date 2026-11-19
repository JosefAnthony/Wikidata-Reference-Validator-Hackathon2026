/**************************************
 * REPORT PAGE
 **************************************/
(function () {
  let report = null;
  try {
    report = JSON.parse(localStorage.getItem("reportData") || "null");
  } catch { /* storage unavailable or corrupt */ }

  const refs = report && Array.isArray(report.references) ? report.references : null;
  if (!refs) {
    document.getElementById("report").hidden = true;
    document.getElementById("reportEmpty").hidden = false;
    return;
  }

  const alive = refs.filter(r => r.status === "alive").length;
  const dead = refs.filter(r => r.status === "dead").length;
  const archived = refs.filter(r => r.status === "dead" && r.suggestedArchive).length;
  const health = refs.length ? Math.round((alive / refs.length) * 100) : 0;

  /* ---------- Back links ---------- */
  if (report.qid) {
    const back = document.getElementById("backToResults");
    const lang = report.language && report.language !== "en" ? `&lang=${encodeURIComponent(report.language)}` : "";
    back.href = `/?item=${encodeURIComponent(report.qid)}${lang}`;
    back.hidden = false;
  }

  /* ---------- Header & summary ---------- */
  document.title = `${report.label} report – Wikidata Reference Validator`;
  document.getElementById("reportTitle").textContent = report.label || report.qid;
  const checked = report.checkedAt ? new Date(report.checkedAt).toLocaleString() : "";
  document.getElementById("reportMeta").innerHTML = `
    <a href="https://www.wikidata.org/wiki/${encodeURIComponent(report.qid)}" target="_blank" rel="noopener">${escapeHtml(report.qid)}</a>
    ${checked ? `<span class="sep">/</span><span>Checked ${escapeHtml(checked)}</span>` : ""}
    ${report.duration != null ? `<span class="sep">/</span><span>${escapeHtml(report.duration)}s</span>` : ""}`;

  document.getElementById("reportSummary").innerHTML = `
    <div class="summary__cell">
      <div class="summary__label">Health</div>
      <div class="summary__value">${health}%<small>reachable</small></div>
      <div class="meter">
        <span class="meter__alive" style="width:${refs.length ? (alive / refs.length) * 100 : 0}%"></span>
        <span class="meter__dead" style="width:${refs.length ? (dead / refs.length) * 100 : 0}%"></span>
      </div>
    </div>
    <div class="summary__cell summary__cell--alive">
      <div class="summary__label">Alive</div><div class="summary__value">${alive}<small>/ ${refs.length}</small></div>
    </div>
    <div class="summary__cell summary__cell--dead">
      <div class="summary__label">Unreachable</div><div class="summary__value">${dead}</div>
    </div>
    <div class="summary__cell summary__cell--archive">
      <div class="summary__label">Archived</div><div class="summary__value">${archived}</div>
    </div>`;

  /* ---------- Table ---------- */
  const sorted = [...refs].sort((a, b) => (a.status === "dead" ? 0 : 1) - (b.status === "dead" ? 0 : 1));
  document.getElementById("reportRows").innerHTML = sorted.map(r => {
    const meta = sourceCategoryMeta(r.sourceCategory);
    const isDead = r.status === "dead";
    return `
      <tr>
        <td data-label="Status"><span class="status-pill status-pill--${isDead ? "dead" : "alive"}">${isDead ? "Unreachable" : "Alive"}</span></td>
        <td data-label="Statement">
          <div>
            <div>${escapeHtml(r.propertyLabel || r.propertyId || "")}</div>
            ${r.statementValue ? `<div style="color: var(--text-faint); font-size: 0.8rem; overflow-wrap: anywhere">${escapeHtml(r.statementValue)}</div>` : ""}
          </div>
        </td>
        <td class="url-cell" data-label="URL"><a href="${escapeHtml(safeUrl(r.url))}" target="_blank" rel="noopener noreferrer" title="${escapeHtml(r.url)}">${escapeHtml(r.url)}</a></td>
        <td data-label="Source"><span class="tag"><i class="bi ${meta.icon}"></i> ${meta.label}</span></td>
        <td class="mono" data-label="HTTP">${r.httpStatus ? escapeHtml(r.httpStatus) : "—"}</td>
        <td data-label="Archive">${r.suggestedArchive
          ? `<a href="${escapeHtml(safeUrl(r.suggestedArchive.archiveUrl))}" target="_blank" rel="noopener noreferrer">${escapeHtml(formatArchiveDate(r.suggestedArchive.timestamp) || "Snapshot")}</a>`
          : `<span style="color: var(--text-faint)">—</span>`}</td>
      </tr>`;
  }).join("");

  /* ---------- Charts (re-drawn when the theme changes) ---------- */
  const categoryKeys = Object.keys(SOURCE_CATEGORIES).filter(k => refs.some(r => (r.sourceCategory || "other") === k));
  let charts = [];

  function drawCharts() {
    if (typeof Chart === "undefined") return;
    charts.forEach(c => c.destroy());

    const text = cssVar("--text-muted");
    const grid = cssVar("--border");
    const aliveColor = cssVar("--alive");
    const deadColor = cssVar("--dead");
    const font = { family: cssVar("--font-sans") || "sans-serif", size: 12 };

    Chart.defaults.color = text;
    Chart.defaults.font = font;

    charts = [
      new Chart(document.getElementById("statusChart"), {
        type: "doughnut",
        data: {
          labels: ["Alive", "Unreachable"],
          datasets: [{ data: [alive, dead], backgroundColor: [aliveColor, deadColor], borderColor: cssVar("--bg"), borderWidth: 3 }],
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          cutout: "72%",
          plugins: { legend: { position: "bottom", labels: { usePointStyle: true, boxWidth: 8 } } },
        },
      }),
      new Chart(document.getElementById("categoryChart"), {
        type: "bar",
        data: {
          labels: categoryKeys.map(k => SOURCE_CATEGORIES[k].label),
          datasets: [
            { label: "Alive", data: categoryKeys.map(k => refs.filter(r => (r.sourceCategory || "other") === k && r.status === "alive").length), backgroundColor: aliveColor, borderRadius: 4 },
            { label: "Unreachable", data: categoryKeys.map(k => refs.filter(r => (r.sourceCategory || "other") === k && r.status === "dead").length), backgroundColor: deadColor, borderRadius: 4 },
          ],
        },
        options: {
          indexAxis: "y",
          responsive: true,
          maintainAspectRatio: false,
          plugins: { legend: { position: "bottom", labels: { usePointStyle: true, boxWidth: 8 } } },
          scales: {
            x: { stacked: true, beginAtZero: true, ticks: { precision: 0 }, grid: { color: grid }, border: { display: false } },
            y: { stacked: true, grid: { display: false }, border: { display: false } },
          },
        },
      }),
    ];
  }

  drawCharts();
  document.addEventListener("themechange", drawCharts);

  /* ---------- Downloads ---------- */
  function download(filename, content, type) {
    const blob = new Blob([content], { type });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  }

  function csvCell(value) {
    const s = String(value ?? "");
    return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
  }

  const base = `references-${report.qid}`;

  document.getElementById("downloadCsv").addEventListener("click", () => {
    const header = ["status", "http_status", "property_id", "property", "statement_value", "url", "domain", "source_category", "archive_url", "archive_timestamp"];
    const rows = refs.map(r => [
      r.status, r.httpStatus, r.propertyId, r.propertyLabel, r.statementValue, r.url, r.domain,
      r.sourceCategory, r.suggestedArchive?.archiveUrl, r.suggestedArchive?.timestamp,
    ].map(csvCell).join(","));
    download(`${base}.csv`, [header.join(","), ...rows].join("\n"), "text/csv;charset=utf-8");
  });

  document.getElementById("downloadJson").addEventListener("click", () => {
    download(`${base}.json`, JSON.stringify(report, null, 2), "application/json");
  });

  /* ---------- Excel (.xlsx) export ---------- */
  function loadSheetJS() {
    if (window.XLSX) return Promise.resolve(window.XLSX);
    return new Promise((resolve, reject) => {
      const existing = document.querySelector('script[src*="xlsx"]');
      if (existing) {
        existing.addEventListener("load", () => resolve(window.XLSX));
        existing.addEventListener("error", reject);
        return;
      }
      const s = document.createElement("script");
      s.src = "https://cdn.jsdelivr.net/npm/xlsx@0.18.5/dist/xlsx.full.min.js";
      s.async = true;
      s.onload = () => resolve(window.XLSX);
      s.onerror = () => reject(new Error("Unable to load SheetJS library"));
      document.head.appendChild(s);
    });
  }

  const downloadCsvBtn = document.getElementById("downloadCsv");
  let downloadExcelBtn = document.getElementById("downloadExcel");
  if (!downloadExcelBtn && downloadCsvBtn) {
    downloadExcelBtn = document.createElement("button");
    downloadExcelBtn.type = "button";
    downloadExcelBtn.className = "btn-ghost";
    downloadExcelBtn.id = "downloadExcel";
    downloadExcelBtn.innerHTML = '<i class="bi bi-filetype-xlsx"></i> Excel';
    downloadCsvBtn.insertAdjacentElement("afterend", downloadExcelBtn);
  }

  if (downloadExcelBtn) {
    downloadExcelBtn.addEventListener("click", async () => {
      const originalHtml = downloadExcelBtn.innerHTML;
      try {
        downloadExcelBtn.disabled = true;
        downloadExcelBtn.innerHTML = '<i class="bi bi-hourglass-split"></i> Exporting...';
        const XLSX = await loadSheetJS();

        const wb = XLSX.utils.book_new();

        // 1. Summary sheet
        const summaryData = [
          ["Metric", "Value"],
          ["Entity Label", report.label || ""],
          ["QID", report.qid || ""],
          ["Checked At", checked || report.checkedAt || ""],
          ["Duration", report.duration != null ? `${report.duration}s` : ""],
          ["Health Score", `${health}%`],
          ["Total References", refs.length],
          ["Alive References", alive],
          ["Unreachable References", dead],
          ["Archived Suggestions", archived],
        ];
        const wsSummary = XLSX.utils.aoa_to_sheet(summaryData);
        wsSummary["!cols"] = [{ wch: 24 }, { wch: 36 }];
        XLSX.utils.book_append_sheet(wb, wsSummary, "Summary");

        // 2. References sheet
        const refHeaders = [
          "Status",
          "HTTP Status",
          "Property ID",
          "Property",
          "Statement Value",
          "URL",
          "Domain",
          "Source Category",
          "Archive URL",
          "Archive Date",
        ];
        const refRows = refs.map((r) => [
          r.status ?? "",
          r.httpStatus ?? "",
          r.propertyId ?? "",
          r.propertyLabel ?? "",
          r.statementValue ?? "",
          r.url ?? "",
          r.domain ?? "",
          r.sourceCategory ?? "",
          r.suggestedArchive?.archiveUrl ?? "",
          r.suggestedArchive?.timestamp ? (formatArchiveDate(r.suggestedArchive.timestamp) || r.suggestedArchive.timestamp) : "",
        ]);
        const wsRefs = XLSX.utils.aoa_to_sheet([refHeaders, ...refRows]);
        wsRefs["!cols"] = [
          { wch: 14 },
          { wch: 12 },
          { wch: 14 },
          { wch: 24 },
          { wch: 30 },
          { wch: 45 },
          { wch: 22 },
          { wch: 18 },
          { wch: 45 },
          { wch: 20 },
        ];
        XLSX.utils.book_append_sheet(wb, wsRefs, "References");

        XLSX.writeFile(wb, `${base}.xlsx`);
      } catch (err) {
        console.error("Excel export error:", err);
        alert("Failed to export Excel file. Please verify network access to the CDN.");
      } finally {
        downloadExcelBtn.disabled = false;
        downloadExcelBtn.innerHTML = originalHtml;
      }
    });
  }
})();
