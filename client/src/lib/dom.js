/**
 * DOM and formatting helpers shared by the served pages.
 */

import { bridge } from './bridge.js';
import { secureFetch } from './api.js';

/** Escape a value for insertion into HTML text or attribute context. */
export function escapeHtml(value) {
  return String(value ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

/** Locale reported by the shell, falling back to the browser's. */
export function currentLocale() {
  return (bridge.info && bridge.info.locale) || navigator.language;
}

/**
 * Translate a key, returning `fallback` when the key is missing.
 *
 * @param {function} t Translator.
 * @param {string} key
 * @param {string} fallback
 */
export function tOr(t, key, fallback) {
  const value = t(key);
  return value === key ? fallback : value;
}

/**
 * Human readable size.
 *
 * @param {number} bytes
 * @param {function} t Translator (uses common.bytes .. common.pb).
 * @param {number} [decimals]
 */
export function formatBytes(bytes, t, decimals = 2) {
  if (!bytes || bytes === 0) return `0 ${t('common.bytes')}`;
  const k = 1024;
  const dm = decimals < 0 ? 0 : decimals;
  const sizes = [t('common.bytes'), t('common.kb'), t('common.mb'), t('common.gb'), t('common.tb'), t('common.pb')];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return parseFloat((bytes / Math.pow(k, i)).toFixed(dm)) + ' ' + sizes[i];
}

/**
 * Relative time in the past ("5 minutes ago").
 *
 * @param {number} timestamp Unix seconds.
 * @param {function} t Translator (uses common.justNow).
 */
export function timeAgo(timestamp, t) {
  const seconds = Math.floor((new Date() - new Date(timestamp * 1000)) / 1000);
  if (seconds < 60) return t('common.justNow');
  const rtf = new Intl.RelativeTimeFormat(currentLocale(), { style: 'long', numeric: 'auto' });
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return rtf.format(-minutes, 'minute');
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return rtf.format(-hours, 'hour');
  return rtf.format(-Math.floor(hours / 24), 'day');
}

/**
 * Relative time until a future timestamp ("in 3 days"), or "never".
 *
 * @param {number|null|undefined} timestamp Unix seconds.
 * @param {function} t Translator (uses common.never).
 */
export function timeUntil(timestamp, t) {
  if (!timestamp) return t('common.never');
  const seconds = Math.floor((new Date(timestamp * 1000) - new Date()) / 1000);
  const rtf = new Intl.RelativeTimeFormat(currentLocale(), { numeric: 'auto' });
  const days = Math.round(seconds / 86400);
  if (Math.abs(days) > 0) return rtf.format(days, 'day');
  return rtf.format(Math.round(seconds / 3600), 'hour');
}

/** Absolute date and time for a unix-seconds timestamp. */
export function formatDate(timestamp) {
  return new Date(timestamp * 1000).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' });
}

/**
 * Resolve an app logo reference to an `img` src. Secure icons are fetched
 * through the API and returned as data URLs.
 *
 * @param {string} logoData URL, `/api/app_icon/...` path, or empty.
 * @returns {Promise<string>}
 */
export async function formatLogoSrc(logoData) {
  if (!logoData) return 'icons/icon128.png';
  if (logoData.startsWith('http')) return logoData;
  if (logoData.startsWith('/api/app_icon/')) {
    try {
      const response = await secureFetch(logoData, { method: 'GET' });
      if (response && response.icon_data_b64) {
        return `data:image/png;base64,${response.icon_data_b64}`;
      }
    } catch (error) {
      console.error(`Failed to fetch secure icon for ${logoData}:`, error);
    }
  }
  return 'icons/icon128.png';
}

/** Swap every `img[data-logo-src]` under `scope` for its resolved logo. */
export function hydrateLogos(scope) {
  scope.querySelectorAll('img[data-logo-src]').forEach(async (img) => {
    const src = await formatLogoSrc(img.dataset.logoSrc);
    if (src) img.src = src;
  });
}

/**
 * Show a transient toast in the corner of the page.
 *
 * @param {string} message HTML-safe message (already translated).
 * @param {boolean} [isError]
 * @param {number} [durationMs]
 */
export function showToast(message, isError = false, durationMs) {
  document.querySelectorAll('.status-toast').forEach((el) => el.remove());
  const toast = document.createElement('div');
  toast.className = `status-toast ${isError ? 'error' : 'success'}`;
  toast.innerHTML = `<i class="fas ${isError ? 'fa-exclamation-circle' : 'fa-check-circle'}"></i> ${message}`;
  document.body.appendChild(toast);
  setTimeout(() => toast.classList.add('visible'), 10);
  setTimeout(() => {
    toast.classList.remove('visible');
    toast.addEventListener('transitionend', () => toast.remove());
  }, durationMs || (isError ? 6000 : 3000));
}

/**
 * Trigger a browser download of a Blob from the page itself.
 *
 * @param {Blob} blob
 * @param {string} filename
 */
export function downloadBlob(blob, filename) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

/**
 * Mark the document with the shell and platform classes and return the
 * HelloInfo. Called first thing by every served page.
 */
export async function announce() {
  const info = await bridge.hello();
  document.documentElement.classList.add(`shell-${info.shell}`, `platform-${info.platform}`);
  return info;
}

/** Insert a mobile safe-area spacer at the top of the body. */
export function addMobileSafeArea() {
  const pad = document.createElement('div');
  pad.className = 'mobile-safe-area';
  pad.style.paddingTop = 'max(40px, env(safe-area-inset-top))';
  pad.style.width = '100%';
  pad.style.backgroundColor = 'var(--bg-card)';
  document.body.insertBefore(pad, document.body.firstChild);
  return pad;
}

/**
 * Prepend a back-arrow button to `header`.
 *
 * @param {Element} header
 * @param {function} onClick
 */
export function addMobileBackButton(header, onClick) {
  header.style.display = 'flex';
  header.style.alignItems = 'center';
  const backBtn = document.createElement('button');
  backBtn.className = 'mobile-back-btn';
  backBtn.innerHTML = '<i class="fas fa-arrow-left"></i>';
  backBtn.onclick = (e) => {
    e.preventDefault();
    onClick();
  };
  header.insertBefore(backBtn, header.firstChild);
  return backBtn;
}
