const I18N = { lang: "en", strings: {}, fallback: {} };

async function loadLang(lang) {
  const res = await fetch(`/static/i18n/${lang}.json`);
  if (!res.ok) throw new Error(`Missing language file: ${lang}`);
  return res.json();
}

function t(key, vars = {}) {
  let s = I18N.strings[key] ?? I18N.fallback[key] ?? key;
  for (const [name, value] of Object.entries(vars)) {
    s = s.replaceAll(`{${name}}`, value);
  }
  return s;
}

function applyTranslations() {
  document.querySelectorAll("[data-i18n]").forEach(el => {
    el.textContent = t(el.dataset.i18n);
  });
  document.querySelectorAll("[data-i18n-html]").forEach(el => {
    el.innerHTML = t(el.dataset.i18nHtml);
  });
  document.querySelectorAll("[data-i18n-placeholder]").forEach(el => {
    el.placeholder = t(el.dataset.i18nPlaceholder);
  });
  document.querySelectorAll("[data-i18n-alt]").forEach(el => {
    el.alt = t(el.dataset.i18nAlt);
  });
  document.querySelectorAll("[data-i18n-title]").forEach(el => {
    el.title = t(el.dataset.i18nTitle);
  });
  document.querySelectorAll("[data-i18n-aria]").forEach(el => {
    el.setAttribute("aria-label", t(el.dataset.i18nAria));
  });
  document.documentElement.lang = I18N.lang;
  document.dispatchEvent(new CustomEvent("i18n:applied"));
}

async function setUiLanguage(lang) {
  try {
    I18N.strings = lang === "en" ? I18N.fallback : await loadLang(lang);
    I18N.lang = lang;
    try { localStorage.setItem("lang", lang); } catch {}
  } catch (err) {
    console.error(err);
    I18N.strings = I18N.fallback;
    I18N.lang = "en";
  }
  applyTranslations();
  const select = document.getElementById("uiLanguage");
  if (select) select.value = I18N.lang;
}

const i18nReady = (async () => {
  I18N.fallback = await loadLang("en");
  let saved = "en";
  try { saved = localStorage.getItem("lang") || "en"; } catch {}
  await setUiLanguage(saved);
})();