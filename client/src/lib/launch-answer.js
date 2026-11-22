/**
 * The answer to a launch request, whatever a reverse proxy in front made of it.
 *
 * A launch answers once its session runs, which a first image pull on a node
 * can take minutes for, longer than many proxies hold a request open. Such a
 * proxy answers 502, 503, or 504 while the server carries on, so that answer
 * is not the launch's: the request's `launch_id` is followed through
 * `/api/launch/progress` instead, to the session or to the server's own reason
 * for failing, which a launch the server refused itself (503, 504) also gives.
 */

import { secureFetch } from './api.js';
import { GATEWAY_STATUSES } from './api-error.js';
const POLL_MS = 1000;
/** How long a launch the server keeps no record of may stay unseen before the request counts as lost. */
const UNSEEN_MS = 20000;

const wait = (ms) => new Promise((resolve) => { setTimeout(resolve, ms); });

/**
 * Post a launch that carries a `launch_id` and return the session it started.
 *
 * @param {string} endpoint The launch route.
 * @param {object} payload The request body.
 * @param {function} [fetcher] What makes the calls, `secureFetch` but in a test.
 * @returns {Promise<{session_id: string, session_url: string}>}
 * @throws {Error} The request's own error, or the reason the server's progress gives.
 */
export async function postLaunch(endpoint, payload, fetcher = secureFetch) {
  try {
    return await fetcher(endpoint, { method: 'POST', body: JSON.stringify(payload) }, { timeout: 0 });
  } catch (error) {
    if (!GATEWAY_STATUSES.has(error.status) || !payload.launch_id) throw error;
    let seen = Date.now();
    for (;;) {
      let progress = null;
      try {
        progress = await fetcher(`/api/launch/progress/${encodeURIComponent(payload.launch_id)}`, { method: 'GET' });
      } catch (asking) {
        // A refusal of its own (a sign-in that ended) is the answer; the proxy or the network failing again is not.
        if (asking.status && asking.status !== 404 && !GATEWAY_STATUSES.has(asking.status)) throw asking;
        if (asking.status === 404 && Date.now() - seen > UNSEEN_MS) throw error;
      }
      if (progress) {
        seen = Date.now();
        if (progress.stage === 'ready') return { session_id: progress.session_id, session_url: progress.session_url };
        if (progress.stage === 'failed') throw Object.assign(new Error(progress.error || error.message), { status: 500 });
      }
      await wait(POLL_MS);
    }
  }
}
