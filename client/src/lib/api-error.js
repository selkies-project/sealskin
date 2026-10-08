/**
 * Read the status and the `detail` out of a failed API call, which the
 * background reports as `HTTP error! status: <code> - <body>`.
 *
 * @param {Error|string} error
 * @returns {{status: number, detail: string}} Status 0 for a failure that carries no response.
 */
export function apiError(error) {
  const message = String((error && error.message) || error);
  const match = /HTTP error! status: (\d+) - ([\s\S]*)$/.exec(message);
  if (!match) return { status: 0, detail: message };
  let detail = match[2];
  try {
    const body = JSON.parse(match[2]);
    if (typeof body.detail === 'string') detail = body.detail;
  } catch (e) { /* not JSON: keep the body */ }
  return { status: Number(match[1]), detail };
}
