/**
 * Web app service worker: receives what the manifest's share target sends,
 * and serves the app's streamed downloads.
 *
 * The POST carries a file, a link, or text; the worker parks it where the
 * app picks it up (`context-store.js`) and opens the app. A download is a
 * stream the app hands over, answered once at `download/<id>`. Nothing is
 * cached, so every page still comes from the server.
 */
import { storePendingFile, storeShared } from '../lib/context-store.js';

async function receiveShare(request) {
  const form = await request.formData();
  const file = form.get('file');
  const link = form.get('url') || (String(form.get('text') || '').match(/https?:\/\/\S+/) || [])[0];
  if (file instanceof File && file.size) {
    await storePendingFile(file);
    await storeShared({ action: 'file', filename: file.name, hasFile: true });
  } else if (link) {
    await storeShared({ action: 'url', targetUrl: link });
  } else if (form.get('text') || form.get('title')) {
    await storeShared({ action: 'search', selectionText: String(form.get('text') || form.get('title')) });
  }
  return Response.redirect('./?shared', 303);
}

const downloads = new Map();

/** A download response; WebKit names the file right only with both filename forms. */
function attachment({ filename, stream }) {
  const ascii = filename.replace(/[^\x20-\x7e]|["\\]/g, '_');
  const encoded = encodeURIComponent(filename).replace(/'/g, '%27');
  return new Response(stream, {
    headers: {
      'Content-Type': 'application/octet-stream',
      'Content-Disposition': `attachment; filename="${ascii}"; filename*=UTF-8''${encoded}`,
    },
  });
}

self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', (event) => event.waitUntil(self.clients.claim()));
// A message with no stream only keeps the worker running while a download streams.
self.addEventListener('message', (event) => {
  const { id, filename, stream } = event.data || {};
  if (stream instanceof ReadableStream) downloads.set(id, { filename, stream });
});
self.addEventListener('fetch', (event) => {
  const { url } = event.request;
  if (event.request.method === 'POST' && url === new URL('share', self.registration.scope).href) {
    event.respondWith(receiveShare(event.request));
    return;
  }
  const id = url.split('/').pop();
  if (downloads.has(id) && url === new URL(`download/${id}`, self.registration.scope).href) {
    event.respondWith(attachment(downloads.get(id)));
    downloads.delete(id);
  }
});
