/**
 * API helpers for the served pages.
 *
 * Every call goes through the shell bridge: the extension and the mobile app
 * sign the JWT and handle the E2EE session, and the web app calls the server
 * under its sign-in cookie. Pages never see a key.
 */

import { bridge, request } from './bridge.js';
import { apiError } from './api-error.js';

/**
 * API call through the shell.
 *
 * Mirrors the historical page-side wrapper: an empty reply to a DELETE or POST
 * resolves to `{}` so callers can destructure without null checks.
 *
 * @param {string} url Path beginning with `/api/`.
 * @param {object} [options] fetch-like options: method, headers, body (string).
 * @param {object} [opts] Bridge request options, such as `timeout`.
 * @returns {Promise<any>} JSON body.
 * @throws {Error} With the HTTP status as `status` and, in the web app, the server's `detail` as its message.
 */
export async function secureFetch(url, options = {}, opts = {}) {
  let data;
  try {
    data = await bridge.secureFetch(url, options, opts);
  } catch (error) {
    const { status, detail } = apiError(error);
    if (!status) throw error;
    // The web app shows the server's sentence; the other shells keep the message they always showed.
    throw Object.assign(bridge.info && bridge.info.shell === 'web' ? new Error(detail) : error, { status });
  }
  if (data === null && (options.method === 'DELETE' || options.method === 'POST')) {
    return {};
  }
  return data;
}

/**
 * Load the template editor schema served by the API (plain, same origin).
 *
 * @returns {Promise<{settings: Array<object>}>}
 */
export async function fetchSchema() {
  const url = new URL('/api/ui/template_schema', document.baseURI);
  const res = await fetch(url, { cache: 'no-cache' });
  if (!res.ok) throw new Error(`Failed to load template schema: ${res.status}`);
  return res.json();
}

/**
 * Resolve the Blob for a launch context: an attached File wins, otherwise the
 * host fetches the target URL with its privileges.
 *
 * @param {object} context Launch context from bridge.getContext().
 * @returns {Promise<Blob>}
 */
export async function getContextBlob(context) {
  if (context.file) return context.file;
  if (!context.targetUrl) throw new Error('No file or URL in launch context.');
  return bridge.fetchBlob(context.targetUrl);
}

/** @param {Blob} blob @returns {Promise<string>} base64 without the data: prefix */
function readBlobAsBase64(blob) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result.split(',')[1]);
    reader.onerror = (error) => reject(error);
    reader.readAsDataURL(blob);
  });
}

/**
 * Upload a Blob through the chunked upload API.
 *
 * @param {Blob} blob
 * @param {string} filename
 * @param {object} [opts]
 * @param {number} [opts.chunkSize] Bytes per chunk (default 1 MiB).
 * @param {function(number, number): void} [opts.onProgress] Called after each
 *   chunk with (chunksDone, totalChunks).
 * @returns {Promise<{uploadId: string, totalChunks: number}>}
 */
export async function uploadInChunks(blob, filename, opts = {}) {
  const chunkSize = opts.chunkSize || 1024 * 1024;
  const { upload_id: uploadId } = await secureFetch('/api/upload/initiate', {
    method: 'POST',
    body: JSON.stringify({ filename, total_size: blob.size }),
  });
  const totalChunks = Math.ceil(blob.size / chunkSize);
  for (let i = 0; i < totalChunks; i++) {
    const chunk = blob.slice(i * chunkSize, Math.min((i + 1) * chunkSize, blob.size));
    const chunkDataB64 = await readBlobAsBase64(chunk);
    await secureFetch('/api/upload/chunk', {
      method: 'POST',
      body: JSON.stringify({ upload_id: uploadId, chunk_index: i, chunk_data_b64: chunkDataB64 }),
    });
    if (opts.onProgress) opts.onProgress(i + 1, totalChunks);
  }
  return { uploadId, totalChunks };
}

/**
 * Open a shell page with optional query parameters. `bridge.openPage` covers
 * the plain case; this variant forwards `params` for hosts that support it
 * (e.g. preselecting a home directory in the file manager).
 *
 * @param {string} page
 * @param {object} [params]
 */
export function openPage(page, params) {
  return request('openPage', params ? { page, params } : { page });
}
