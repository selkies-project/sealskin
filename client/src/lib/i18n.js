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
 * The product name `{brand}` stands for in the strings: the `application-name`
 * meta a served page carries (`app.branding`), or SealSkin in a shell's own pages.
 */
export const BRAND = (typeof document !== 'undefined' && document.querySelector('meta[name="application-name"]')?.content) || 'SealSkin';

/**
 * Reduce a locale to a supported language code.
 *
 * @param {string} locale e.g. 'pt-BR', 'en_US', 'fil'.
 * @returns {string} A key of the emitted language files, 'en' if unknown.
 */
function resolveLanguage(locale) {
  const base = String(locale || 'en').split(/[-_]/)[0].toLowerCase();
  return Object.prototype.hasOwnProperty.call(FILES, base) ? base : 'en';
}

function lookup(dict, key) {
  return key.split('.').reduce((obj, k) => (obj && obj[k] !== undefined) ? obj[k] : undefined, dict);
}

/**
 * Build the `t` function over one dictionary: `{count, plural, one {..} other {..}}`
 * with the categories the language has (`few` and `many` in Russian, for one),
 * `#` standing for the count, then `{name}` substitution.
 *
 * @param {object} dict The language's strings.
 * @param {string} [lang] The language, whose plural rules pick the category.
 */
function makeT(dict, lang) {
  let pluralRules = null;
  try {
    pluralRules = new Intl.PluralRules(lang || 'en');
  } catch (e) { /* an unknown language tag: one and other */ }
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
      const category = pluralRules ? pluralRules.select(Number(count)) : 'other';
      let chosen = rules[category];
      // A `one` that spells its number out is for 1 alone, whatever else the language counts as one.
      if (category === 'one' && count !== 1 && chosen !== undefined && !/#|\{/.test(chosen)) chosen = undefined;
      chosen = chosen ?? (count === 1 ? rules.one : undefined) ?? rules.other ?? rules.many;
      return chosen === undefined ? match : chosen.replace(/#/g, String(count));
    });
    const filled = { brand: BRAND, ...variables };
    for (const placeholder in filled) {
      const regex = new RegExp(`\\{${placeholder}\\}`, 'g');
      const substitution = String(filled[placeholder]);
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
    return makeT(await fetchLanguage(lang), lang);
  } catch (e) {
    console.error(e);
    if (lang !== 'en') {
      try {
        return makeT(await fetchLanguage('en'), 'en');
      } catch (e2) {
        console.error(e2);
      }
    }
    return makeT({});
  }
}

/**
 * Apply `data-i18n`, `data-i18n-placeholder`, and `data-i18n-title` attributes.
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
