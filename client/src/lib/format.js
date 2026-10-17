/**
 * Text formatting helpers with no dependency on the host bridge, so the web
 * shell, which is not a framed page, can share them with the served pages.
 */

/** Escape a value for insertion into HTML text or attribute context. */
export function escapeHtml(value) {
  return String(value ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
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

/** Absolute date and time for a unix-seconds timestamp. */
export function formatDate(timestamp) {
  return new Date(timestamp * 1000).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' });
}
