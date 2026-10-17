/**
 * Receives the file the pick bookmarklet fetched in the page it ran on, and
 * hands it to the web app as a share. This page is served without the app's
 * opener policy so that page can post to it; the app page it then loads cuts
 * the link. The fragment holds the app address to fall back to when the
 * opener is gone or cannot fetch the file.
 */
import { storePendingFile, storeShared } from '../lib/context-store.js';
import { loadTranslator } from '../lib/i18n.js';

addEventListener('message', async (event) => {
  const { file } = event.data || {};
  if (event.source !== opener || !(file instanceof File)) return;
  await storePendingFile(file);
  await storeShared({ action: 'file', filename: file.name, hasFile: true });
  location.replace('./?shared');
});

const fallback = new URL(decodeURIComponent(location.hash.slice(1)) || './', location.href);
if (!opener) location.replace(fallback.origin === location.origin ? fallback.href : './');
else opener.postMessage('sealskin-receive', '*');
loadTranslator(navigator.language).then((t) => { document.body.textContent = t('web.receiving'); });
