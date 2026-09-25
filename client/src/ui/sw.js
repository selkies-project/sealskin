/**
 * Web app service worker: receives what the manifest's share target sends.
 *
 * The POST carries a file, a link, or text; the worker parks it where the
 * app picks it up (`context-store.js`) and opens the app. Nothing is cached,
 * so every page still comes from the server.
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

self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', (event) => event.waitUntil(self.clients.claim()));
self.addEventListener('fetch', (event) => {
  if (event.request.method === 'POST' && event.request.url === new URL('share', self.registration.scope).href) {
    event.respondWith(receiveShare(event.request));
  }
});
