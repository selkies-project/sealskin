/**
 * The browser's half of the check of a reverse proxy that signs users in
 * (the server's `app.proxy_auth`).
 */

// What the server looks for in a sign-in header: no user or group has this name.
const FORGED = 'sealskin forged';
const CHECK_PATH = '/api/auth/proxy';

/**
 * Send the server a request whose sign-in headers this page wrote itself. A
 * proxy that signs users in replaces them on the way; the server refuses the
 * proxy's sign-ins while one arrives as it was sent.
 *
 * @returns {Promise<void>} Settles either way: the server also checks the proxy itself.
 */
export async function sendForgedSignIn() {
  try {
    const seen = await (await fetch(CHECK_PATH, { cache: 'no-store' })).json();
    const headers = Object.fromEntries((seen.headers || []).map((name) => [name, FORGED]));
    if (Object.keys(headers).length) await fetch(CHECK_PATH, { cache: 'no-store', headers });
  } catch (e) {
    // Nothing to learn from a request that did not arrive.
  }
}
