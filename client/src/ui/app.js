/**
 * Web app: SealSkin in a browser tab or an installed app, with no extension.
 * Served at `/`, the server's own address; its assets, the pages it frames,
 * and its service worker live under `/ui/`, which is the worker's scope.
 *
 * This page runs the shells' background over the shared `chrome.*` polyfill
 * and frames the served pages, as the mobile app does, inside a frame of its
 * own: a rail of destinations (a tab bar on a narrow window) with who is
 * signed in. The destination is kept in `?page=`, so reloading and going back
 * work. It holds no key: the
 * server signs the browser in with an `HttpOnly` cookie, and every API call
 * is a plain request to this origin under it. The page asks the server who is
 * signed in, and shows the sign-in panel when nobody is: the identity
 * providers the server configures, and the root token. A provider sends the
 * browser back with a one-time grant in the URL fragment, which this page
 * exchanges for the cookie. The server sends this page with a cross-origin
 * opener policy and no framing, and session tabs get no opener.
 *
 * A session opens in this tab, taking the web app's place, unless the
 * launcher asked for a tab of its own: then a launch opens `launching.html`
 * in a tab at the click, and that page shows the launch's progress and takes
 * itself to the session. With session isolation a session has an origin of
 * its own, whose suffix this page probes for ahead of time and hands it.
 *
 * `?next=/app/<app id>/...` is an application's address that sent the
 * browser here to sign in; it is kept through the provider's round trip and
 * taken up again once a sign-in holds.
 *
 * Launch contexts arrive through `?url=` (links, the bookmarklet, and
 * `web+sealskin:` addresses), `?q=` (OpenSearch and a selection sent by the
 * bookmarklet), the manifest's share target (parked by `sw.js`), and its file
 * handlers (`launchQueue`).
 */

import { adoptLaunchTab, hooks, openLaunchTab, reserveTab } from './web-polyfill.js';
import '../shell/background.js';
import { initHost, pageTransport } from '../shell/host.js';
import { callBackground } from '../lib/host-bridge.js';
import { apiError } from '../lib/api-error.js';
import { storePendingFile, takeShared } from '../lib/context-store.js';
import { loadTranslator } from '../lib/i18n.js';
import { sendForgedSignIn } from '../lib/proxy-check.js';

const DEFAULT_SEARCH_ENGINE = 'https://google.com/search?q=';
// Firefox stops a service worker 30 s after its last event, cutting a download short without an error.
const KEEPALIVE_MS = 10000;
const WORKER_WAIT_MS = 3000;

let t = (key) => key;
let hostApi = null;
// Why the sign-in panel is showing, as a `web.signIn` key.
let notice = null;

const fromBase64 = (value) => (Uint8Array.fromBase64 ? Uint8Array.fromBase64(value) : Uint8Array.from(atob(value), (c) => c.charCodeAt(0)));
const api = (url, options) => callBackground(pageTransport, 'secureFetch', { url, options });
const post = (url, body) => api(url, { method: 'POST', body: JSON.stringify(body) });

// The rail's destinations as the page and parameters the host frames.
const DESTINATIONS = {
  home: ['home', {}],
  sessions: ['home', { view: 'sessions' }],
  files: ['files', {}],
  options: ['options', {}],
};
const BADGE_REFRESH_MS = 15000;
const LAUNCH_CHANNEL_MS = 30 * 60 * 1000;

const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => document.querySelectorAll(selector);

/** Show who is signed in and the destinations open to them. */
/** Where signing out sends a user a reverse proxy signed in. */
let signOutUrl = '';
/** How this page's user was last signed in; a reload forgets it. */
let signedInVia = '';
/** Whether the server serves sessions on origins of their own (see `session-origin.js`). */
let isolated = false;
const NEXT_KEY = 'sealskin-next';
const NEXT_ADDRESS = /^\/app\/[A-Za-z0-9_-]+\/(\?[^#\s]*)?$/;

/**
 * The application's address this page is to go on to once signed in: from
 * `?next=`, kept in the tab's session storage across the provider's round
 * trip, and only ever an address under `/app/` of this origin.
 *
 * @returns {string|null}
 */
function nextAddress() {
  const params = new URLSearchParams(location.search);
  let next = params.get('next');
  if (next !== null) {
    params.delete('next');
    const search = params.toString();
    history.replaceState(null, '', location.pathname + (search ? `?${search}` : '') + location.hash);
    try { sessionStorage.setItem(NEXT_KEY, next); } catch (e) { /* no storage */ }
  } else {
    try { next = sessionStorage.getItem(NEXT_KEY); } catch (e) { next = null; }
  }
  return next && NEXT_ADDRESS.test(next) ? next : null;
}

/** Go on to the application's address that sent the browser here, if one did. */
function leaveForNext() {
  const next = nextAddress();
  if (!next) return false;
  try { sessionStorage.removeItem(NEXT_KEY); } catch (e) { /* no storage */ }
  location.replace(next);
  return true;
}

function showAccount(status) {
  document.body.classList.toggle('signed-out', !status);
  signOutUrl = (status && status.sign_out_url) || '';
  if (status) signedInVia = status.via;
  // A proxy's sign-in ends at the proxy: with no page of its to open, there is nothing to offer.
  $$('.sign-out').forEach((button) => { button.hidden = Boolean(status) && status.via === 'proxy' && !signOutUrl; });
  // The root token is the way to an administrator where the proxy's provider names none.
  $$('.root-sign-in').forEach((button) => { button.hidden = !status || status.via !== 'proxy'; });
  if (!status) return;
  const via = t(`options.via.${status.via}`);
  $$('.avatar').forEach((el) => { el.textContent = status.username.slice(0, 1); });
  $$('.account-name').forEach((el) => { el.textContent = status.username; el.title = status.username; });
  $$('.account-via').forEach((el) => { el.textContent = via === `options.via.${status.via}` ? status.via : via; });
  $('[data-dest="files"]').hidden = !status.settings.persistent_storage;
}

let addressed = false;

/** Mark the destination the frame shows and keep it in the address. */
function showDestination(page, params = {}) {
  if (page === 'connect') {
    document.body.classList.add('signed-out');
    return;
  }
  const current = page === 'home' && params.view === 'sessions' ? 'sessions' : page;
  $$('.rail-links button').forEach((button) => {
    if (button.dataset.dest === current) button.setAttribute('aria-current', 'page');
    else button.removeAttribute('aria-current');
  });
  const search = `?${new URLSearchParams({ page, ...params })}`;
  // Going back or forward arrives with the address already there; the first page takes the address it loaded at.
  if (search !== location.search) history[addressed ? 'pushState' : 'replaceState'](null, '', location.pathname + search);
  addressed = true;
  refreshBadge();
}

async function refreshBadge() {
  if (document.body.classList.contains('signed-out') || document.hidden) return;
  const sessions = await api('/api/sessions', { method: 'GET' }).catch(() => null);
  if (!sessions) {
    renewProxySignIn();
    return;
  }
  $('#sessions-badge').textContent = sessions.length;
  $('#sessions-badge').hidden = sessions.length === 0;
}

async function signOut() {
  $('#account-menu').hidden = true;
  const leave = signOutUrl;
  await post('/api/auth/signout', {}).catch(() => {});
  if (leave) location.assign(leave);
  else hostApi.openPage('connect');
}

function bindShell() {
  const labels = { home: 'web.nav.home', sessions: 'web.nav.sessions', files: 'web.nav.files', options: 'web.nav.settings' };
  $$('.rail-links button').forEach((button) => {
    const { dest } = button.dataset;
    if (labels[dest]) button.querySelector('span').textContent = t(labels[dest]);
    button.addEventListener('click', () => hostApi.openPage(...DESTINATIONS[dest]));
  });
  $$('.sign-out').forEach((button) => {
    button.querySelector('span').textContent = t('options.dashboard.signOut');
    button.addEventListener('click', signOut);
  });
  $$('.root-sign-in').forEach((button) => {
    button.querySelector('span').textContent = t('web.rootToken');
    button.addEventListener('click', () => {
      location.hash = '#root';
      location.reload();
    });
  });
  const menu = $('#account-menu');
  $('#account-button').addEventListener('click', (event) => {
    event.stopPropagation();
    menu.hidden = !menu.hidden;
    $('#account-button').setAttribute('aria-expanded', String(!menu.hidden));
  });
  document.addEventListener('click', (event) => { if (!menu.contains(event.target)) menu.hidden = true; });
  // A click in the frame never reaches this document.
  window.addEventListener('blur', () => { menu.hidden = true; });
  window.addEventListener('popstate', () => {
    const { page = 'home', ...params } = Object.fromEntries(new URLSearchParams(location.search));
    hostApi.openPage(page, params);
  });
  setInterval(refreshBadge, BADGE_REFRESH_MS);
}

/** Ask the background for the session-origin suffix, which it probes for once. */
const sessionOrigin = () => callBackground(pageTransport, 'sessionOrigin').catch(() => null);
// The suffix once the probe has answered: undefined before, null when no origin is reachable.
let knownSuffix;

async function warmSessionOrigin() {
  if (!isolated) {
    knownSuffix = null;
    return null;
  }
  knownSuffix = undefined;
  knownSuffix = await sessionOrigin();
  return knownSuffix;
}

/**
 * Take the tab of a launch at its click and show the launching page in it,
 * which is told the session-origin suffix here or once the probe answers.
 *
 * @param {boolean} reserve
 * @param {{id: string, app: string, logo: string, room: boolean}} [launch] None for a blank tab.
 * @throws {Error} `noSessionOrigin` or `popupBlocked`, before anything is launched.
 */
function reserveLaunchTab(reserve, launch) {
  if (!launch) {
    reserveTab(reserve);
    return;
  }
  if (isolated && knownSuffix === null) {
    warmSessionOrigin();
    throw new Error('noSessionOrigin');
  }
  // The logo and the suffix go over the channel, never in the page's address (see launching.js).
  const params = new URLSearchParams({ id: launch.id, app: launch.app || '', room: launch.room ? '1' : '0' });
  if (!openLaunchTab(launch.id, `launching.html?${params}`)) throw new Error('popupBlocked');
  const channel = new BroadcastChannel(`sealskin-launch-${launch.id}`);
  const tell = async () => {
    channel.postMessage({ logo: launch.logo || '' });
    if (!isolated) {
      channel.postMessage({ shared: true });
      return;
    }
    const suffix = knownSuffix === undefined ? await warmSessionOrigin() : knownSuffix;
    channel.postMessage(suffix ? { suffix } : { error: 'noSessionOrigin' });
  };
  channel.onmessage = (event) => { if (event.data && event.data.hello) tell(); };
  setTimeout(() => channel.close(), LAUNCH_CHANNEL_MS);
}

/** @returns {Promise<object|null>} The status of whoever the cookie signs in, or null for nobody. */
async function whoAmI() {
  try {
    return await post('/api/admin/status', {});
  } catch (e) {
    if (apiError(e).status === 401) return null;
    throw e;
  }
}

/**
 * Store who is signed in as the configuration the host and the pages read,
 * or, with no status, that nobody is. The search engine choice outlives both.
 *
 * @param {object|null} status
 */
async function remember(status) {
  const { sealskinConfig: old } = await chrome.storage.local.get('sealskinConfig');
  const config = { searchEngineUrl: (old && old.searchEngineUrl) || DEFAULT_SEARCH_ENGINE };
  if (status) {
    const port = location.port || '443';
    Object.assign(config, {
      serverIp: location.hostname,
      apiPort: port,
      sessionPort: port,
      username: status.username,
      signIn: status.via,
      userSettings: { ...status.settings, is_admin: status.is_admin },
    });
  }
  await callBackground(pageTransport, 'saveConfig', { config });
  showAccount(status);
  isolated = Boolean(status && status.session_isolation);
  // A user the proxy signed in is whom the proxy passes on: the server sees what it does to the headers.
  if (status && status.via === 'proxy') sendForgedSignIn();
  // Probed now, the session origin is known by the first launch.
  if (status) warmSessionOrigin();
}

/**
 * Whether the reverse proxy in front answers for itself, with a redirect to
 * its sign-in page or a refusal, where the server would answer anyone.
 */
async function proxyWantsSignIn() {
  try {
    const response = await fetch('/api/auth/config', { cache: 'no-store', redirect: 'manual' });
    return response.type === 'opaqueredirect' || response.status === 401 || response.status === 403;
  } catch (e) {
    return false;
  }
}

/**
 * Reload where a reverse proxy signed this page's user in and now asks for its
 * sign-in again: the navigation is what the proxy sends to its sign-in page.
 * A sign-in that ran out shows to a fetch as a redirect it cannot follow, or
 * as the proxy's refusal.
 *
 * @returns {Promise<boolean>} True when the page is reloading.
 */
async function renewProxySignIn() {
  if (signedInVia !== 'proxy' || !(await proxyWantsSignIn())) return false;
  location.reload();
  return true;
}

/** The page transport; a call the server answers 401 means the sign-in ended. */
async function transport(message) {
  const reply = await pageTransport(message);
  const failed = message.type === 'secureFetch' && reply && reply.success === false ? apiError(reply.error).status : null;
  if ((failed === 0 || failed === 401) && await renewProxySignIn()) return reply;
  if (failed === 401 && !notice) {
    notice = 'ended';
    if (hostApi) hostApi.openPage('connect');
  }
  return reply;
}

/**
 * Exchange the grant an identity provider sent this page back with for the
 * sign-in cookie, or note why the provider flow failed.
 *
 * @returns {Promise<boolean>} True when the exchange signed the browser in.
 */
async function takeSignIn() {
  const fragment = new URLSearchParams(location.hash.slice(1));
  if (!fragment.has('sso') && !fragment.has('sso-error')) return false;
  history.replaceState(null, '', location.pathname + location.search);
  notice = fragment.get('sso-error');
  if (notice) return false;
  try {
    await post('/api/auth/register', { grant: fragment.get('sso') });
    return true;
  } catch (e) {
    const { detail } = apiError(e);
    notice = /^[a-zA-Z]+$/.test(detail) ? detail : 'failed';
    return false;
  }
}

/**
 * Show the sign-in panel in place of the frame: the server's identity
 * providers and the root token. The token is typed into a masked text field
 * rather than a password field, so browsers do not offer to save it.
 *
 * @param {string} [failure] Why the server could not be asked who is signed in.
 */
async function signInPanel(failure) {
  const offered = failure ? {} : await api('/api/auth/config', { method: 'GET' }).catch(() => ({ root: true }));
  const panel = document.getElementById('host-panel');
  const form = document.createElement('form');
  form.className = 'host-box';
  form.innerHTML = `
    <img class="host-logo" src="icons/icon128.png" alt="SealSkin">
    <h2></h2>
    <p class="host-error" data-part="error" hidden></p>
    <div class="host-actions" data-part="providers" hidden>
      <button type="button" class="primary" data-via="oidc" hidden></button>
      <button type="button" class="primary" data-via="saml" hidden></button>
    </div>
    <div class="host-field" data-part="root" hidden>
      <label for="root-token"></label>
      <input type="text" id="root-token" name="token" class="masked" autocomplete="off" autocapitalize="off" spellcheck="false">
      <p class="host-muted host-small"></p>
      <div class="host-actions"><button type="submit"></button></div>
    </div>
    <p class="host-muted" data-part="nothing" hidden></p>
    <div class="host-actions" data-part="retry" hidden><button type="button" class="primary"></button></div>
    <p class="host-muted host-small host-apps" data-part="apps">
      <span></span>
      <a href="https://chromewebstore.google.com/detail/sealskin-isolation/lclgfmnljgacfdpmmmjmfpdelndbbfhk" target="_blank" rel="noopener">Chrome</a>
      <a href="https://addons.mozilla.org/en-US/firefox/addon/sealskin-isolation/" target="_blank" rel="noopener">Firefox</a>
      <a href="https://play.google.com/store/apps/details?id=io.linuxserver.sealskin" target="_blank" rel="noopener">Android</a>
      <a href="https://apps.apple.com/us/app/sealskin/id6758210210" target="_blank" rel="noopener">iOS</a>
    </p>`;
  const [error, providers, root, nothing, retry] = ['error', 'providers', 'root', 'nothing', 'retry']
    .map((part) => form.querySelector(`[data-part="${part}"]`));
  const submit = root.querySelector('button');
  const say = (message) => {
    error.textContent = message;
    error.hidden = !message;
  };

  form.querySelector('h2').textContent = t(failure ? 'shell.host.unreachableTitle' : 'web.signInTitle');
  root.querySelector('label').textContent = t('web.rootToken');
  root.querySelector('p').textContent = t('web.rootTokenHelp');
  submit.textContent = t('web.rootSignIn');
  nothing.textContent = t('web.signIn.notConfigured');
  form.querySelector('.host-apps span').textContent = t('web.apps');
  retry.querySelector('button').textContent = t('shell.host.retry');
  retry.querySelector('button').addEventListener('click', () => connect());

  for (const button of providers.querySelectorAll('button')) {
    const { via } = button.dataset;
    button.textContent = t(`web.signInWith.${via}`);
    button.hidden = !offered[via];
    button.addEventListener('click', () => location.assign(`/api/auth/${via}/login`));
  }
  const provided = Boolean(offered.oidc || offered.saml);
  providers.hidden = !provided;
  root.hidden = !offered.root;
  submit.className = provided ? 'secondary' : 'primary';
  nothing.hidden = Boolean(failure) || provided || Boolean(offered.root);
  retry.hidden = !failure;
  if (failure) say(failure);
  else if (notice) say(t(`web.signIn.${notice}`) === `web.signIn.${notice}` ? t('web.signIn.failed') : t(`web.signIn.${notice}`));
  notice = null;

  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    const token = form.token.value.trim();
    if (!token) return;
    submit.disabled = true;
    try {
      await post('/api/auth/root', { token });
      form.token.value = '';
      await enter('home');
    } catch (e) {
      const { status, detail } = apiError(e);
      say(status === 403 ? t('web.rootRefused') : status === 429 ? t('web.rootLocked') : detail);
      form.token.select();
    }
    submit.disabled = false;
  });

  document.body.classList.add('signed-out');
  document.getElementById('app-frame').hidden = true;
  panel.replaceChildren(form);
  panel.hidden = false;
  if (!root.hidden) form.token.focus();
}

/**
 * Open a served page as whoever the cookie signs in.
 *
 * @param {string} page
 * @throws {Error} When the server knows no sign-in after all.
 */
async function enter(page) {
  const status = await whoAmI();
  if (!status) throw new Error(t('web.signIn.failed'));
  await remember(status);
  if (leaveForNext()) return;
  hostApi.openPage(page);
}

/**
 * The host's connection page: the launcher for a browser the server already
 * signs in, as a reverse proxy's sign-in does, and the sign-in panel otherwise.
 */
async function connect() {
  let status = null;
  let failure;
  try {
    status = await whoAmI();
  } catch (e) {
    failure = apiError(e).detail;
  }
  // `#root` asks for the root token although a reverse proxy already signs this browser in.
  if (status && status.via === 'proxy' && location.hash === '#root') {
    history.replaceState(null, '', location.pathname + location.search);
    showAccount(null);
    await signInPanel();
    return;
  }
  // A proxy's sign-in that ran out is renewed by a navigation, which the proxy sends to its sign-in page.
  if (!status && signedInVia === 'proxy') {
    location.reload();
    return;
  }
  await remember(status);
  if (status) {
    notice = null;
    if (leaveForNext()) return;
    hostApi.openPage('home');
  } else {
    await signInPanel(failure);
  }
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
 * one chunk at a time through the API, and the service worker
 * answers a download address with it. A worker that registered but never
 * activates fails it with `no-stream-worker`, on which the file manager
 * downloads through memory instead.
 */
async function streamDownload(home, path, filename) {
  const { active } = await Promise.race([navigator.serviceWorker.ready, new Promise((resolve) => setTimeout(resolve, WORKER_WAIT_MS, {}))]);
  if (!active) throw new Error('no-stream-worker');
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
  frame.src = `/ui/download/${id}`;
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
  // No worker registers on an untrusted certificate, and WebKit holds register() until the worker installs; downloads then go through memory.
  // Scoped to /ui/, the worker takes the share target and the downloads there, and never a session's page.
  const worker = 'serviceWorker' in navigator && Promise.race([navigator.serviceWorker.register('/ui/sw.js', { scope: '/ui/' }).then(() => true, () => false), new Promise((resolve) => setTimeout(resolve, WORKER_WAIT_MS, false))]);
  if ('launchQueue' in window) {
    window.launchQueue.setConsumer(async ({ files }) => {
      if (!files || !files.length) return;
      const file = await files[0].getFile();
      await storePendingFile(file);
      await pageTransport({ type: 'setContext', payload: { context: { action: 'file', filename: file.name, hasFile: true } } });
      if (hostApi) hostApi.openPage('home');
    });
  }
  t = await loadTranslator(navigator.language).catch(() => t);
  bindShell();
  await takeLaunchContext();
  await takeSignIn();
  // The host frames a page for a stored user and asks `connect` otherwise, as `#root` has it do.
  const status = await whoAmI().catch(() => null);
  await remember(status && status.via === 'proxy' && location.hash === '#root' ? null : status);
  if (status && location.hash !== '#root' && leaveForNext()) return;
  hostApi = initHost({
    shell: 'web',
    transport,
    launcher: 'home',
    reserveTab: reserveLaunchTab,
    adoptLaunch: adoptLaunchTab,
    connect,
    streamDownload: (await worker) && streamsTransfer() ? streamDownload : undefined,
    onPageChange: showDestination,
  });
  hooks.openPopup = () => hostApi.openPage('home');
}

start();
