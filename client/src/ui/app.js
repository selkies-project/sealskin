/**
 * Web app: SealSkin in a browser tab or an installed app, with no extension.
 *
 * This page runs what the extension's background runs (the E2EE handshake,
 * JWT signing, the launch context) over the shared `chrome.*` polyfill and
 * frames the served pages, as the mobile app does. Session content is served
 * from this same origin, so private keys never rest in the browser in the
 * clear: the active key and a saved, not yet active, one are wrapped under a
 * key derived from the browser's one passphrase (PBKDF2, AES-GCM), or, for a
 * user who keeps no key in this browser, never stored at all, and only this
 * page holds them unwrapped while it is open. The server sends this page
 * with a cross-origin opener policy and no framing, and session tabs get no
 * opener, so session content cannot reach it.
 *
 * A user signing in through the server's identity provider comes back with a
 * one-time grant in the URL fragment; this page registers a key it generates,
 * which never leaves it, and connects with it until the tab closes.
 *
 * Launch contexts arrive through `?url=` (links, the bookmarklet, and
 * `web+sealskin:` addresses), `?q=` (OpenSearch and a selection sent by the
 * bookmarklet), the manifest's share target (parked by `sw.js`), and its file
 * handlers (`launchQueue`).
 */

import { KEY_REF, hooks, keepInMemory, reserveTab } from './web-polyfill.js';
import '../shell/background.js';
import { initHost, pageTransport } from '../shell/host.js';
import { callBackground } from '../lib/host-bridge.js';
import { MIN_PASSPHRASE, arrayBufferToBase64, arrayBufferToPem, pemToArrayBuffer, setSigningKey } from '../lib/crypto-utils.js';
import { storePendingFile, takeShared } from '../lib/context-store.js';
import { loadTranslator } from '../lib/i18n.js';

const KEYRING = 'sealskinKeyring';
const PENDING = 'sealskinPendingConfig';
// Stored in place of the saved configuration's PEM.
const PENDING_REF = 'sealed-pending-key';
const SIGNING = { name: 'RSASSA-PKCS1-v1_5', hash: 'SHA-256' };
const PBKDF2_ITERATIONS = 600000;
// Firefox stops a service worker 30 s after its last event, cutting a download short without an error.
const KEEPALIVE_MS = 10000;

let kek = null;
let hostApi = null;
// The identity provider the configuration signed in through, and a refusal the connection page shows.
let signedIn = null;
let signInError = null;
// Set while the user keeps no key in this browser: the keys, by slot, live in this page alone.
let tabKeys = null;

const fromBase64 = (value) => (Uint8Array.fromBase64 ? Uint8Array.fromBase64(value) : Uint8Array.from(atob(value), (c) => c.charCodeAt(0)));
const keyring = () => JSON.parse(localStorage.getItem(KEYRING) || 'null');

async function deriveKek(passphrase, salt) {
  const material = await crypto.subtle.importKey('raw', new TextEncoder().encode(passphrase), 'PBKDF2', false, ['deriveKey']);
  return crypto.subtle.deriveKey(
    { name: 'PBKDF2', salt, iterations: PBKDF2_ITERATIONS, hash: 'SHA-256' },
    material,
    { name: 'AES-GCM', length: 256 },
    false,
    ['wrapKey', 'unwrapKey'],
  );
}

function unwrap(entry, extractable, key = kek) {
  return crypto.subtle.unwrapKey('pkcs8', fromBase64(entry.key), key, { name: 'AES-GCM', iv: fromBase64(entry.iv) }, SIGNING, extractable, ['sign']);
}

/**
 * Open the keyring with `passphrase` and sign with its active key from now on.
 *
 * @param {string} passphrase
 * @throws {Error} On the wrong passphrase, which fails the AES-GCM tag.
 */
async function unlock(passphrase) {
  const { salt, keys } = keyring();
  const candidate = await deriveKek(passphrase, fromBase64(salt));
  for (const [slot, entry] of Object.entries(keys)) {
    const key = await unwrap(entry, false, candidate);
    if (slot === 'config') setSigningKey(KEY_REF, key);
  }
  kek = candidate;
}

/**
 * Wrap `pem` into a keyring slot. A new keyring takes `passphrase` as the
 * browser's passphrase; an existing one must open with it.
 *
 * @param {'config'|'pending'} slot
 * @param {string} pem
 * @param {string} passphrase
 */
async function seal(slot, pem, passphrase) {
  let ring = keyring();
  if (ring) {
    await unlock(passphrase);
  } else {
    if (passphrase.length < MIN_PASSPHRASE) throw new Error('passphraseTooShort');
    ring = { salt: arrayBufferToBase64(crypto.getRandomValues(new Uint8Array(16))), keys: {} };
    kek = await deriveKek(passphrase, fromBase64(ring.salt));
  }
  const key = await crypto.subtle.importKey('pkcs8', pemToArrayBuffer(pem), SIGNING, true, ['sign']);
  const iv = crypto.getRandomValues(new Uint8Array(12));
  const wrapped = await crypto.subtle.wrapKey('pkcs8', key, kek, { name: 'AES-GCM', iv });
  ring.keys[slot] = { iv: arrayBufferToBase64(iv), key: arrayBufferToBase64(wrapped) };
  localStorage.setItem(KEYRING, JSON.stringify(ring));
  if (slot === 'config') setSigningKey(KEY_REF, await unwrap(ring.keys.config, false));
}

function drop(slot) {
  const ring = keyring();
  if (!ring) return;
  delete ring.keys[slot];
  if (Object.keys(ring.keys).length) {
    localStorage.setItem(KEYRING, JSON.stringify(ring));
  } else {
    localStorage.removeItem(KEYRING);
    kek = null;
  }
}

/**
 * The page transport, sealing private keys on their way into storage and
 * handing the connection page the PEMs it exports. Saving a configuration
 * that keeps no key in this browser holds the configurations in this page's
 * memory from then on, and saving one that does puts them back in storage;
 * either change drops what the other way kept.
 *
 * @param {object} message
 */
async function transport(message) {
  const { type, payload } = message;
  const draft = type === 'saveConfig' ? payload.config : type === 'storageSet' && payload.items && payload.items[PENDING];
  if (draft) {
    const { passphrase, remember, ...config } = draft;
    const [slot, ref] = type === 'saveConfig' ? ['config', KEY_REF] : ['pending', PENDING_REF];
    if (remember !== undefined && remember === Boolean(tabKeys)) {
      ['config', 'pending'].forEach(drop);
      tabKeys = remember ? null : {};
      keepInMemory(!remember);
    }
    if (config.clientPrivateKey && config.clientPrivateKey !== ref) {
      if (tabKeys) {
        tabKeys[slot] = config.clientPrivateKey;
        if (slot === 'config') setSigningKey(KEY_REF, await crypto.subtle.importKey('pkcs8', pemToArrayBuffer(tabKeys.config), SIGNING, false, ['sign']));
      } else {
        if (!passphrase) return { success: false, error: 'passphraseRequired' };
        try {
          await seal(slot, config.clientPrivateKey, passphrase);
        } catch (e) {
          // Unwrapping under the wrong passphrase fails the AES-GCM tag.
          return { success: false, error: e.name === 'OperationError' ? 'wrongPassphrase' : e.message };
        }
      }
      config.clientPrivateKey = ref;
    }
    if (slot === 'config') payload.config = config; else payload.items[PENDING] = config;
  }
  if (type === 'clearConfig' && signedIn) {
    await callBackground(pageTransport, 'secureFetch', { url: '/api/auth/signout', options: { method: 'POST', body: '{}' } }).catch(() => {});
    signedIn = null;
  }
  if (type === 'clearConfig') ['config', 'pending'].forEach((slot) => { drop(slot); if (tabKeys) delete tabKeys[slot]; });
  if (type === 'storageRemove' && [].concat(payload.keys).includes(PENDING)) {
    drop('pending');
    if (tabKeys) delete tabKeys.pending;
  }
  const reply = await pageTransport(message);
  if (type === 'getFullConfig' && reply.data) {
    const { keys } = keyring() || { keys: {} };
    for (const [field, slot] of [['config', 'config'], ['pendingConfig', 'pending']]) {
      if (!reply.data[field]) continue;
      if (tabKeys && tabKeys[slot]) reply.data[field].clientPrivateKey = tabKeys[slot];
      else if (kek && keys[slot]) reply.data[field].clientPrivateKey = arrayBufferToPem(await crypto.subtle.exportKey('pkcs8', await unwrap(keys[slot], true)), 'PRIVATE');
    }
    reply.data.remember = !tabKeys;
    reply.data.signInError = signInError;
    signInError = null;
  }
  if (signedIn && type === 'secureFetch' && !reply.success && /status: 401\b/.test(reply.error)) await signInEnded();
  return reply;
}

/**
 * Finish a sign-in the identity provider sent this page back from: register a
 * key generated here, never extractable, and connect with it from this page's
 * memory.
 */
async function takeSignIn() {
  const fragment = new URLSearchParams(location.hash.slice(1));
  if (!fragment.has('sso') && !fragment.has('sso-error')) return;
  history.replaceState(null, '', location.pathname + location.search);
  signInError = fragment.get('sso-error');
  if (signInError) return;
  let pair;
  let registration;
  try {
    pair = await crypto.subtle.generateKey({ ...SIGNING, modulusLength: 2048, publicExponent: new Uint8Array([1, 0, 1]) }, false, ['sign', 'verify']);
    const response = await fetch('/api/auth/register', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ grant: fragment.get('sso'), public_key: arrayBufferToPem(await crypto.subtle.exportKey('spki', pair.publicKey), 'PUBLIC') }),
    });
    registration = await response.json();
    if (!response.ok) throw new Error(registration.detail);
  } catch (e) {
    signInError = /^[a-zA-Z]+$/.test(e.message) ? e.message : 'failed';
    return;
  }
  ['config', 'pending'].forEach(drop);
  tabKeys = {};
  keepInMemory(true);
  setSigningKey(KEY_REF, pair.privateKey);
  const port = location.port || '443';
  await pageTransport({
    type: 'saveConfig',
    payload: {
      config: {
        serverIp: location.hostname,
        apiPort: port,
        sessionPort: port,
        username: registration.username,
        keyId: registration.kid,
        clientPrivateKey: KEY_REF,
        serverPublicKey: registration.server_public_key,
        signIn: registration.via,
      },
    },
  });
  signedIn = registration.via;
  // After a login land on the dashboard, as the connection page does.
  history.replaceState(null, '', `${location.pathname}?page=options`);
  const status = await callBackground(pageTransport, 'secureFetch', { url: '/api/admin/status', options: { method: 'POST', body: '{}' } }).catch(() => null);
  if (status) await callBackground(pageTransport, 'updateConfig', { userSettings: { ...status.settings, is_admin: status.is_admin } });
}

/** Forget a sign-in the identity provider ended and show the connection page to sign in again. */
async function signInEnded() {
  signedIn = null;
  signInError = 'ended';
  await pageTransport({ type: 'clearConfig', payload: {} });
  if (hostApi) hostApi.openPage('connect');
}

/**
 * Ask for the passphrase until it opens the keyring, or the user forgets this browser.
 * A masked text field keeps it out of the password manager, which would fill it into
 * any page of this origin, session pages included.
 */
function unlockScreen(t) {
  const panel = document.getElementById('host-panel');
  const form = document.createElement('form');
  form.className = 'host-box';
  form.innerHTML = `
    <img class="host-logo" src="icons/icon128.png" alt="SealSkin">
    <h2></h2>
    <p class="host-muted"></p>
    <input type="text" name="passphrase" class="passphrase" autocomplete="off" autocapitalize="off" spellcheck="false" required>
    <p class="host-error" hidden></p>
    <div class="host-actions"><button type="submit" class="primary"></button><button type="button" class="secondary"></button></div>`;
  const [title, body, error] = [form.querySelector('h2'), form.querySelector('p'), form.querySelector('.host-error')];
  const [submit, forget] = form.querySelectorAll('button');
  title.textContent = t('web.unlockTitle');
  body.textContent = t('web.unlockBody');
  form.passphrase.placeholder = t('web.passphrase');
  error.textContent = t('web.wrongPassphrase');
  submit.textContent = t('web.unlock');
  forget.textContent = t('options.dashboard.logout');
  panel.replaceChildren(form);
  panel.hidden = false;
  form.passphrase.focus();
  return new Promise((resolve) => {
    form.addEventListener('submit', async (event) => {
      event.preventDefault();
      submit.disabled = true;
      try {
        await unlock(form.passphrase.value);
        resolve();
      } catch (e) {
        error.hidden = false;
        form.passphrase.select();
      }
      submit.disabled = false;
    });
    forget.addEventListener('click', async () => {
      if (!confirm(t('options.dashboard.confirmLogout'))) return;
      await transport({ type: 'clearConfig', payload: {} });
      resolve();
    });
  });
}

/** Whether a stream can be handed to the service worker, which is how a download reaches it. */
function streamsTransfer() {
  try {
    const stream = new ReadableStream();
    structuredClone(stream, { transfer: [stream] });
    return true;
  } catch (e) {
    return false;
  }
}

/**
 * Save a file from the file manager to disk as it arrives: the stream pulls
 * one chunk at a time through the encrypted API, and the service worker
 * answers a download address with it.
 */
async function streamDownload(home, path, filename) {
  const { active } = await navigator.serviceWorker.ready;
  const keepalive = setInterval(() => active.postMessage(null), KEEPALIVE_MS);
  let index = 0;
  const stream = new ReadableStream({
    async pull(controller) {
      const params = new URLSearchParams({ path, chunk_index: index++ });
      try {
        const chunk = await callBackground(pageTransport, 'secureFetch', { url: `/api/files/download/chunk/${home}?${params}`, options: { method: 'GET' } });
        if (chunk.chunk_data_b64) controller.enqueue(fromBase64(chunk.chunk_data_b64));
        if (chunk.is_last_chunk) {
          controller.close();
          clearInterval(keepalive);
        }
      } catch (e) {
        controller.error(e);
        clearInterval(keepalive);
      }
    },
    cancel: () => clearInterval(keepalive),
  });
  const id = crypto.randomUUID();
  active.postMessage({ id, filename, stream }, [stream]);
  // WebKit starts a download from a frame, not from a link.
  const frame = document.createElement('iframe');
  frame.hidden = true;
  frame.src = `download/${id}`;
  document.body.append(frame);
}

/** Hand the popup what this page was opened with: a link, a search, or a share. */
async function takeLaunchContext() {
  const params = new URLSearchParams(location.search);
  let context = null;
  if (params.has('url')) context = { action: 'url', targetUrl: params.get('url').replace(/^web\+sealskin:/i, '') };
  else if (params.has('q')) context = { action: 'search', selectionText: params.get('q') };
  else if (params.has('shared')) context = await takeShared();
  if (context) await pageTransport({ type: 'setContext', payload: { context } });
  if (params.has('url') || params.has('q') || params.has('shared')) history.replaceState(null, '', location.pathname);
}

async function start() {
  // No worker registers on an untrusted certificate; downloads then go through memory.
  const worker = 'serviceWorker' in navigator && navigator.serviceWorker.register('sw.js').then(() => true, () => false);
  if ('launchQueue' in window) {
    window.launchQueue.setConsumer(async ({ files }) => {
      if (!files || !files.length) return;
      const file = await files[0].getFile();
      await storePendingFile(file);
      await pageTransport({ type: 'setContext', payload: { context: { action: 'file', filename: file.name, hasFile: true } } });
      if (hostApi) hostApi.openPage('popup');
    });
  }
  await takeLaunchContext();
  await takeSignIn();
  if (keyring() && !signedIn) await unlockScreen(await loadTranslator(navigator.language));
  hostApi = initHost({
    shell: 'web',
    transport,
    reserveTab,
    signIn: (via) => { if (via === 'oidc' || via === 'saml') location.assign(`/api/auth/${via}/login`); },
    streamDownload: (await worker) && streamsTransfer() ? streamDownload : undefined,
    onPageChange: (page) => { document.body.dataset.frame = page; },
  });
  hooks.openPopup = () => hostApi.openPage('popup');
}

start();
