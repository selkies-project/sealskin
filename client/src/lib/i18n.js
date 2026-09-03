/**
 * Translation runtime for the served pages and the shells.
 *
 * Language files are emitted by the build as JSON, one per language, each
 * already deep-merged over English. The build injects `__I18N_FILES__`, a map
 * from language code to the emitted path relative to the page, so a page does
 * exactly one fetch:
 *
 *   import { loadTranslator, applyTranslations } from '../lib/i18n.js';
 *   const t = await loadTranslator(navigator.language);
 *   applyTranslations(document.body, t);
 */

/* global __I18N_FILES__ */

const FILES = typeof __I18N_FILES__ !== 'undefined' ? __I18N_FILES__ : {};
const cache = new Map();

/**
 * Reduce a locale to a supported language code.
 *
 * @param {string} locale e.g. 'pt-BR', 'en_US', 'fil'.
 * @returns {string} A key of the emitted language files, 'en' if unknown.
 */
export function resolveLanguage(locale) {
  const base = String(locale || 'en').split(/[-_]/)[0].toLowerCase();
  return Object.prototype.hasOwnProperty.call(FILES, base) ? base : 'en';
}

function lookup(dict, key) {
  return key.split('.').reduce((obj, k) => (obj && obj[k] !== undefined) ? obj[k] : undefined, dict);
}

/**
 * Build the `t` function over one dictionary. Keeps the plural and placeholder
 * semantics of the former translations.js: `{count, plural, one {..} other {..}}`
 * then `{name}` substitution.
 */
function makeT(dict) {
  return (key, variables = {}) => {
    let value = lookup(dict, key);
    if (value === undefined) {
      console.warn(`Translation key not found: ${key}`);
      return key;
    }
    if (typeof value !== 'string') {
      return value;
    }
    let processedText = value.replace(/\{(\w+),\s*plural,\s*(.*)\}/g, (match, varName, rulesStr) => {
      if (!Object.prototype.hasOwnProperty.call(variables, varName)) return match;
      const count = variables[varName];
      const rules = {};
      const ruleRegex = /(\w+)\s*\{((?:[^{}]|{[^{}]*})*)\}/g;
      let ruleMatch;
      while ((ruleMatch = ruleRegex.exec(rulesStr)) !== null) {
        rules[ruleMatch[1]] = ruleMatch[2];
      }
      if (count === 1 && rules.one) return rules.one;
      if (rules.other) return rules.other;
      return match;
    });
    for (const placeholder in variables) {
      const regex = new RegExp(`\\{${placeholder}\\}`, 'g');
      const substitution = String(variables[placeholder]);
      processedText = processedText.replace(regex, () => substitution);
    }
    return processedText;
  };
}

async function fetchLanguage(lang) {
  if (cache.has(lang)) return cache.get(lang);
  const rel = FILES[lang];
  if (!rel) throw new Error(`No translation file for '${lang}'`);
  const url = new URL(rel, document.baseURI);
  const promise = fetch(url).then((res) => {
    if (!res.ok) throw new Error(`Failed to load translations ${url}: ${res.status}`);
    return res.json();
  });
  cache.set(lang, promise);
  return promise;
}

/**
 * Load the dictionary for a locale and return its `t` function.
 *
 * Unknown locales and fetch failures fall back to English; if English itself
 * cannot be loaded the returned `t` echoes keys so the page still renders.
 *
 * @param {string} locale
 * @returns {Promise<function(string, object=): (string|any)>}
 */
export async function loadTranslator(locale) {
  const lang = resolveLanguage(locale);
  try {
    return makeT(await fetchLanguage(lang));
  } catch (e) {
    console.error(e);
    if (lang !== 'en') {
      try {
        return makeT(await fetchLanguage('en'));
      } catch (e2) {
        console.error(e2);
      }
    }
    return makeT({});
  }
}

/**
 * Apply `data-i18n`, `data-i18n-placeholder` and `data-i18n-title` attributes.
 *
 * @param {ParentNode} scope Element or document to translate.
 * @param {function} t Translator from loadTranslator().
 * @param {{html?: boolean}} [opts] Use innerHTML for data-i18n when `html` is true.
 */
export function applyTranslations(scope, t, { html = false } = {}) {
  scope.querySelectorAll('[data-i18n]').forEach((el) => {
    const value = t(el.getAttribute('data-i18n'));
    if (html) el.innerHTML = value; else el.textContent = value;
  });
  scope.querySelectorAll('[data-i18n-placeholder]').forEach((el) => {
    el.placeholder = t(el.getAttribute('data-i18n-placeholder'));
  });
  scope.querySelectorAll('[data-i18n-title]').forEach((el) => {
    el.title = t(el.getAttribute('data-i18n-title'));
  });
}
