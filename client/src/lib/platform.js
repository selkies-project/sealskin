/**
 * Which client this code runs as: the name a shell gives the server in the
 * `X-SealSkin-Client` header and the pages in `hello`.
 */

/**
 * Detect the platform the shell runs on.
 *
 * @param {'extension'|'mobile'|'web'|'ui'} shell The build target or the shell name.
 * @returns {'chrome'|'firefox'|'android'|'ios'|'web'}
 */
export function detectPlatform(shell) {
  const cap = typeof window !== 'undefined' && window.Capacitor;
  if (cap && typeof cap.getPlatform === 'function') {
    const p = cap.getPlatform();
    if (p === 'android' || p === 'ios') return p;
  }
  const ua = (typeof navigator !== 'undefined' && navigator.userAgent) || '';
  if (/Android/i.test(ua) && shell === 'mobile') return 'android';
  if (/iPhone|iPad|iPod/i.test(ua) && shell === 'mobile') return 'ios';
  if (/Firefox/.test(ua)) return 'firefox';
  if (typeof chrome !== 'undefined' && chrome.runtime && chrome.runtime.id) return 'chrome';
  return 'web';
}
