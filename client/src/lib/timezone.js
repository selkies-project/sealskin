/**
 * Browser timezone reporting.
 *
 * Every request that makes the server start a container carries the browser's
 * IANA zone so the container's TZ matches the user's clock. No dependencies:
 * this is also imported by the self-contained collaboration room page.
 */

/**
 * The browser's IANA zone name ("Europe/Berlin"), or `null` when the runtime
 * cannot report one. The server falls back to its own TZ, then UTC.
 */
export function browserTimezone() {
  try {
    const zone = Intl.DateTimeFormat().resolvedOptions().timeZone;
    return typeof zone === 'string' && zone ? zone : null;
  } catch (e) {
    return null;
  }
}
