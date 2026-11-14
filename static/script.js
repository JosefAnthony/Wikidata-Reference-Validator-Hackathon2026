/**************************************
 * AUTOCOMPLETE SEARCH
 **************************************/
const refreshCache = new URLSearchParams(window.location.search).get('refresh') === '1';
const searchInput = document.getElementById('search');
const suggestions = document.getElementById('suggestions');
const searchWrapper = searchInput.closest('.position-relative');
let searchDebounceTimer;
let searchRequestController;
let lastSearchQuery = '';
let lastSearchResults = [];

function hideSuggestions() {
  suggestions.hidden = true;
}

function showSuggestionMessage(message, className = 'text-muted') {
  suggestions.innerHTML = '';
  const item = document.createElement('li');
  item.className = `list-group-item ${className}`;
  item.textContent = message;
  suggestions.appendChild(item);
  suggestions.hidden = false;
}

function renderSuggestions(items) {
  suggestions.innerHTML = '';
  items.forEach((item) => {
    const li = document.createElement('li');
    li.className = 'list-group-item list-group-item-action';

    const content = document.createElement('div');
    const label = document.createElement('strong');
    label.textContent = item.label || item.id || 'Unnamed item';
    content.appendChild(label);

    if (item.description) {
      const description = document.createElement('div');
      description.className = 'small text-muted';
      description.textContent = item.description;
      content.appendChild(description);
    }

    li.appendChild(content);

    li.addEventListener('click', () => {
      searchInput.value = item.label || '';
      searchInput.setAttribute('data-qid', item.id || '');
      searchInput.setAttribute('data-label', item.label || '');
      suggestions.innerHTML = '';
      hideSuggestions();
    });

    suggestions.appendChild(li);
  });

  if (items.length) {
    suggestions.hidden = false;
  } else {
    hideSuggestions();
  }
}

async function fetchSuggestions(query) {
  searchRequestController = new AbortController();
  const controller = searchRequestController;
  showSuggestionMessage('Searching...');

  try {
    const searchParams = new URLSearchParams({ q: query });
    if (refreshCache) searchParams.set('refresh', '1');
    const response = await fetch(`/search?${searchParams}`, {
      signal: controller.signal,
    });
    if (!response.ok) {
      throw new Error(`Search failed (HTTP ${response.status}). Please try again.`);
    }

    const data = await response.json();
    if (!Array.isArray(data)) {
      throw new Error('The search service returned an unexpected response.');
    }

    if (searchInput.value.trim() !== query || searchRequestController !== controller) {
      return;
    }

    lastSearchQuery = query;
    lastSearchResults = data;
    renderSuggestions(data);
    if (!data.length) showSuggestionMessage('No matching items found.');
  } catch (error) {
    if (error.name === 'AbortError') return;
    if (searchInput.value.trim() === query && searchRequestController === controller) {
      showSuggestionMessage(error.message || 'Search failed. Please try again.', 'text-danger');
    }
  } finally {
    if (searchRequestController === controller) {
      searchRequestController = null;
    }
  }
}

function scheduleSearch(query) {
  clearTimeout(searchDebounceTimer);
  searchDebounceTimer = null;

  if (searchRequestController) {
    searchRequestController.abort();
    searchRequestController = null;
  }

  if (query.length < 3) {
    lastSearchQuery = '';
    lastSearchResults = [];
    suggestions.innerHTML = '';
    hideSuggestions();
    return;
  }

  searchDebounceTimer = setTimeout(() => {
    searchDebounceTimer = null;
    fetchSuggestions(query);
  }, 500);
}

searchInput.addEventListener('input', function () {
  const query = this.value.trim();
  this.removeAttribute('data-qid');
  this.removeAttribute('data-label');
  suggestions.innerHTML = '';

  if (query.length >= 3) {
    showSuggestionMessage('Searching...');
  } else {
    hideSuggestions();
  }

  scheduleSearch(query);
});

searchInput.addEventListener('focus', function () {
  const query = this.value.trim();
  if (query.length < 3) return;

  if (query === lastSearchQuery) {
    renderSuggestions(lastSearchResults);
    if (!lastSearchResults.length) showSuggestionMessage('No matching items found.');
  } else if (!searchDebounceTimer && !searchRequestController) {
    scheduleSearch(query);
  } else {
    suggestions.hidden = false;
  }
});

document.addEventListener('click', (event) => {
  if (!searchWrapper.contains(event.target)) {
    clearTimeout(searchDebounceTimer);
    searchDebounceTimer = null;
    if (searchRequestController) {
      searchRequestController.abort();
      searchRequestController = null;
    }
    hideSuggestions();
  }
});

/**************************************
 * Property and item labels are fetched and cached by the backend.
 **************************************/
/**************************************
 * VALIDATE REFERENCES (FIXED + SAFE)
 **************************************/
async function validate() {
  const input = document.getElementById('search');
  const qid = input.getAttribute('data-qid');
  const label = input.getAttribute('data-label') || input.value;
  const resultDiv = document.getElementById('result');
  const searchSection = document.getElementById('searchSection');

  if (!qid) {
    resultDiv.innerHTML = '<div class="alert alert-warning" role="alert"></div>';
    resultDiv.querySelector('.alert').textContent =
      'Select a Wikidata item from the suggestions before validating.';
    return;
  }

  searchSection.style.display = 'none';

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
    const el = document.getElementById('countdownTimer');
    if (el) {
      el.textContent = remaining > 0 ? `Estimated time: ${remaining}s` : 'Finalizing...';
    }
  }, 1000);

  const start = Date.now();

  try {
    const validateUrl = refreshCache ? '/validate?refresh=1' : '/validate';
    const response = await fetch(validateUrl, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ qid }),
    });

    let data;
    try {
      data = await response.json();
    } catch {
      throw new Error('The server returned an invalid response. Please try again.');
    }

    clearInterval(countdownInterval);
    const duration = Math.floor((Date.now() - start) / 1000);

    if (!response.ok || data.error) {
      resultDiv.innerHTML = `
        <div class="card p-4 text-center">
          <div class="alert alert-danger">
            <i class="bi bi-x-circle"></i> <span class="validation-error-message"></span>
          </div>
          <p class="small text-muted">Completed in ${duration}s</p>
          <button class="btn btn-lg btn-glass-action mt-3"
                  onclick="resetSearch()">🔄 Search Another Item</button>
        </div>`;
      resultDiv.querySelector('.validation-error-message').textContent =
        data.error || `Validation failed (HTTP ${response.status}). Please try again.`;
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

    const aliveCount = data.filter((r) => r.status === 'alive').length;
    const deadCount = data.filter((r) => r.status === 'dead').length;

    localStorage.setItem(
      'reportData',
      JSON.stringify({
        qid: qid || label,
        label,
        aliveCount,
        deadCount,
        references: data,
        duration,
      }),
    );

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
      const badge = ref.status === 'alive' ? 'success' : 'danger';
      const icon = ref.status === 'alive' ? 'check-circle' : 'x-octagon';
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
                ${ref.statementValue ? `: <em>${ref.statementValue}</em>` : ''}
              </div>

              ${
                ref.status === 'dead'
                  ? `
                    <div class="mt-2 alert alert-warning p-2 mb-1">
                      <i class="bi bi-exclamation-triangle me-1"></i>
                      <strong>Reference unavailable.</strong>
                      Please review or update this reference.
                    </div>
                  `
                  : ''
              }

              ${
                ref.status === 'dead' && ref.suggestedArchive
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
                  : ''
              }

              ${
                ref.status === 'dead' && !ref.suggestedArchive
                  ? `
                    <div class="mt-2 small text-muted">
                      📭 No archived snapshot found on Wayback Machine.
                    </div>
                  `
                  : ''
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

    document.getElementById('viewReportBtn').onclick = () => {
      window.open('/report', '_blank');
    };
  } catch (err) {
    clearInterval(countdownInterval);
    resultDiv.innerHTML = `
      <div class="card p-4 text-center">
        <div class="alert alert-danger">
          <i class="bi bi-bug"></i> <span class="validation-error-message"></span>
        </div>
        <button class="btn btn-lg btn-glass-action mt-3"
                onclick="resetSearch()">🔄 Search Another Item</button>
      </div>`;
    resultDiv.querySelector('.validation-error-message').textContent =
      `Error: ${err.message || 'The request failed. Please try again.'}`;
  }
}

/**************************************
 * GLOBAL COLLAPSE BUTTON TOGGLE (FIX)
 **************************************/
document.addEventListener('shown.bs.collapse', (e) => {
  const btn = document.querySelector(`[data-bs-target="#${e.target.id}"]`);
  if (btn) btn.textContent = 'Hide';
});

document.addEventListener('hidden.bs.collapse', (e) => {
  const btn = document.querySelector(`[data-bs-target="#${e.target.id}"]`);
  if (btn) btn.textContent = 'Details';
});

/**************************************
 * RESET SEARCH (UNCHANGED)
 **************************************/
function resetSearch() {
  const searchSection = document.getElementById('searchSection');
  document.getElementById('search').value = '';
  document.getElementById('search').removeAttribute('data-qid');
  document.getElementById('search').removeAttribute('data-label');
  document.getElementById('result').innerHTML = '';
  searchSection.style.display = 'block';
}
