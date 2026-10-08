/**
 * The probe for the name under which this browser reaches a session's own
 * origin, `<session id>.<suffix>`. The shells' background runs it, and the
 * web app's launching page when no shell hands it the answer.
 */

// A name that does not resolve or whose certificate is refused fails fast; this bounds one that hangs.
const PROBE_TIMEOUT_MS = 2500;

async function reaches(suffix, port) {
  try {
    const res = await fetch(`https://${crypto.randomUUID()}.${suffix}:${port}/sealskin-origin`, {
      cache: 'no-store', credentials: 'omit', signal: AbortSignal.timeout(PROBE_TIMEOUT_MS),
    });
    return res.ok && (await res.text()) === 'sealskin';
  } catch (e) {
    return false;
  }
}

/**
 * Probe the candidate suffixes at once and take the first, in order, that
 * answers: the session domain the server names, the server's name, then its
 * parent's, where a wildcard certificate covers the server's siblings. Each
 * takes a name that resolves, a certificate the browser trusts, and the proxy
 * answering there; an IP address has no names under it.
 *
 * @param {string} host The server's host name.
 * @param {string|number} port The session port.
 * @param {string} [domain] The server's session domain, tried first.
 * @returns {Promise<string|null>} The suffix, or null when none answers.
 */
export async function reachableSessionOrigin(host, port, domain = '') {
  const named = host && !host.includes(':') && !/^[\d.]+$/.test(host);
  const labels = named ? host.split('.') : [];
  const under = named ? (labels.length > 2 ? [host, labels.slice(1).join('.')] : [host]) : [];
  const suffixes = [...new Set([domain, ...under].filter(Boolean))];
  const probes = suffixes.map((suffix) => reaches(suffix, port));
  for (const [i, probe] of probes.entries()) if (await probe) return suffixes[i];
  return null;
}

/**
 * The suffix a launch opens its session under: the one that answers
 * (`reachableSessionOrigin`), else the domain the server names, taken as it
 * is. A reverse proxy that signs users in turns the probe away, which
 * carries no credentials, and lets the session's own navigation through.
 *
 * @param {string} host The server's host name.
 * @param {string|number} port The session port.
 * @param {string} [domain] The server's session domain.
 * @returns {Promise<string|null>} The suffix, or null when none answers and the server names none.
 */
export async function probeSessionOrigin(host, port, domain = '') {
  return (await reachableSessionOrigin(host, port, domain)) || domain || null;
}
