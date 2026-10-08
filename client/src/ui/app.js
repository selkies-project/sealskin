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
import { escapeHtml, formatBytes, formatDate } from '../lib/format.js';
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
  files: ['files', {}],
  options: ['options', {}],
};
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
/** The status of whoever is signed in, as the profile shows it. */
let lastStatus = null;
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
  lastStatus = status;
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
  if (!$('#profile').hidden) renderProfile(status);
}

const esc = escapeHtml;
/** A limit of the settings as text: a negative value sets none. */
const limitOf = (value, text) => (value === null || value === undefined || value < 0 ? t('web.profile.unlimited') : text(value));
/** A chip of a switch of the settings, lit when it is on. */
const chip = (on, label) => `<span class="profile-chip ${on ? 'on' : 'off'}"><i class="fas ${on ? 'fa-check' : 'fa-minus'}"></i>${esc(label)}</span>`;
const chips = (names) => `<div class="profile-chips">${names.map((name) => `<span class="profile-chip">${esc(name)}</span>`).join('')}</div>`;
const section = (title, body) => `<section class="profile-section"><h4>${esc(title)}</h4>${body}</section>`;
const rows = (pairs) => `<dl class="profile-rows">${pairs.filter(Boolean).map(([k, v]) => `<dt>${esc(k)}</dt><dd>${v}</dd>`).join('')}</dl>`;
/** A used-of-limit row with a meter under it, where a limit is set. */
const meter = (used, limit) => (limit > 0 ? `<progress class="profile-meter" max="100" value="${Math.min(100, (used / limit) * 100)}"></progress>` : '');

/**
 * Fill the profile with a status: how the user is signed in, the groups
 * behind their settings, and what those settings let them do.
 *
 * @param {object} status The status response.
 */
function renderProfile(status) {
  const { settings } = status;
  const sections = [];
  const signIn = [
    [t('web.profile.method'), esc($('.account-via').textContent)],
    status.expires ? [t('web.profile.until'), esc(formatDate(status.expires))] : null,
    status.via === 'proxy' && !status.expires ? [t('web.profile.until'), esc(t('web.profile.proxyKeeps'))] : null,
    status.clustered && status.node_id ? [t('web.profile.node'), esc(status.node_id)] : null,
  ];
  sections.push(section(t('web.profile.signIn'), rows(signIn)));
  const groups = settings.groups || [];
  let membership = groups.length ? chips(groups) : `<p class="profile-note">${esc(t('web.profile.noGroups'))}</p>`;
  const provided = status.provider_groups || [];
  if (provided.length) membership += `<p class="profile-note" style="margin-top: 0.5rem;">${esc(t(status.via === 'proxy' ? 'web.profile.proxyGroups' : 'web.profile.providerGroups'))}</p>${chips(provided)}`;
  sections.push(section(t('web.profile.groups'), membership));
  if (status.is_admin) {
    sections.push(section(t('web.profile.access'), `<p class="profile-note">${esc(t('web.profile.adminNote'))}</p>`));
  } else {
    const can = [
      ['persistent_storage', 'web.profile.canStorage'],
      ['public_sharing', 'web.profile.canShare'],
      ['gpu', 'web.profile.canGpu'],
      ['edit_templates', 'web.profile.canTemplates'],
      ['home_migration', 'web.profile.canMigrate'],
    ];
    sections.push(section(t('web.profile.access'), `<div class="profile-chips">${can.map(([key, label]) => chip(Boolean(settings[key]), t(label))).join('')}</div>`));
    const { allowance } = status;
    const storage = settings.storage_limit;
    const limits = [
      [t('web.profile.sessions'), `<span id="profile-sessions">${esc(limitOf(settings.session_limit, (n) => t('web.profile.sessionsOf', { limit: n })))}</span>`],
      [t('web.profile.cpus'), esc(limitOf(settings.session_cpus, (n) => String(n)))],
      [t('web.profile.memory'), esc(limitOf(settings.session_memory_mb, (n) => formatBytes(n * 1024 * 1024, t, 0)))],
      [t('web.profile.hours'), esc(limitOf(settings.session_hours, (n) => t('web.profile.hoursValue', { hours: n })))],
      settings.persistent_storage ? [
        t('web.profile.storage'),
        esc(formatBytes(status.storage_used || 0, t, 1) + (storage > 0 ? ` / ${storage} ${t('common.gb')}` : '')) + meter(status.storage_used || 0, storage * 1024 ** 3),
      ] : null,
      allowance ? [
        t('web.home.allowance'),
        esc(t('web.home.allowanceValue', { used: Number(allowance.used).toFixed(1), hours: allowance.hours, period: t(`options.periods.${allowance.period}`).toLowerCase() })) + meter(allowance.used, allowance.hours),
      ] : null,
    ];
    sections.push(section(t('web.profile.limits'), rows(limits)));
  }
  if (status.clustered) {
    const pools = settings.pools || [];
    const denied = settings.pools_denied || [];
    let where = pools.length ? chips(pools) : `<p class="profile-note">${esc(t('web.profile.anyPool'))}</p>`;
    if (denied.length) where += `<p class="profile-note" style="margin-top: 0.5rem;">${esc(t('web.profile.poolsDenied'))}</p>${chips(denied)}`;
    sections.push(section(t('web.profile.pools'), where));
  }
  if (settings.proot_catalog) sections.push(section(t('web.profile.catalog'), `<p class="profile-note">${esc(settings.proot_catalog)}</p>`));
  $('#profile-body').innerHTML = sections.join('');
}

/** Open the profile and count the sessions running against their limit. */
async function openProfile() {
  if (!lastStatus) return;
  renderProfile(lastStatus);
  $('#profile').hidden = false;
  $('#profile-close').focus();
  if (lastStatus.is_admin) return;
  try {
    const sessions = await api('/api/sessions', { method: 'GET' });
    const count = $('#profile-sessions');
    if (!count) return;
    const limit = lastStatus.settings.session_limit;
    count.textContent = typeof limit === 'number' && limit >= 0
      ? t('web.profile.sessionsRunningOf', { running: sessions.length, limit })
      : t('web.profile.sessionsRunning', { running: sessions.length });
  } catch (e) { /* the limit alone is shown */ }
}

function closeProfile() {
  $('#profile').hidden = true;
}

let addressed = false;

/** Mark the destination the frame shows and keep it in the address. */
function showDestination(page, params = {}) {
  if (page === 'connect') {
    document.body.classList.add('signed-out');
    return;
  }
  $$('.rail-links button').forEach((button) => {
    if (button.dataset.dest === page) button.setAttribute('aria-current', 'page');
    else button.removeAttribute('aria-current');
  });
  const search = `?${new URLSearchParams({ page, ...params })}`;
  // Going back or forward arrives with the address already there; the first page takes the address it loaded at.
  if (search !== location.search) history[addressed ? 'pushState' : 'replaceState'](null, '', location.pathname + search);
  addressed = true;
  // Back in the frame, the rail is back too.
  document.body.classList.remove('leaving');
}

async function signOut() {
  closeProfile();
  const leave = signOutUrl;
  await post('/api/auth/signout', {}).catch(() => {});
  if (leave) location.assign(leave);
  else hostApi.openPage('connect');
}

function bindShell() {
  const labels = { home: 'web.nav.home', files: 'web.nav.files', options: 'web.nav.settings' };
  $$('.rail-links button').forEach((button) => {
    const { dest } = button.dataset;
    if (labels[dest]) button.querySelector('span').textContent = t(labels[dest]);
    button.addEventListener('click', () => hostApi.openPage(...DESTINATIONS[dest]));
  });
  $$('.project-link').forEach((link) => { link.title = t('web.nav.project'); });
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
  // The rail folds away on request, leaving a tab at the window's edge to bring it back; the choice is kept per browser.
  const RAIL_KEY = 'sealskin-rail';
  const showRail = (shown) => {
    document.body.classList.toggle('rail-hidden', !shown);
    $('#rail-peek').hidden = shown;
    $('#rail-hide').title = t('web.nav.hideRail');
    $('#rail-peek').title = t('web.nav.showRail');
    try { localStorage.setItem(RAIL_KEY, shown ? 'shown' : 'hidden'); } catch (e) { /* no storage */ }
  };
  let railShown = true;
  try { railShown = localStorage.getItem(RAIL_KEY) !== 'hidden'; } catch (e) { /* no storage */ }
  showRail(railShown);
  $('#rail-hide').addEventListener('click', () => showRail(false));
  $('#rail-peek').addEventListener('click', () => showRail(true));
  $$('.avatar[aria-haspopup]').forEach((button) => {
    button.title = t('web.profile.open');
    button.setAttribute('aria-label', t('web.profile.open'));
    button.addEventListener('click', openProfile);
  });
  $('#profile-close').title = t('common.close');
  $('#profile-close').setAttribute('aria-label', t('common.close'));
  $('#profile-close').addEventListener('click', closeProfile);
  $('#profile').addEventListener('click', (event) => { if (event.target === $('#profile')) closeProfile(); });
  document.addEventListener('keydown', (event) => { if (event.key === 'Escape') closeProfile(); });
  window.addEventListener('popstate', () => {
    const { page = 'home', ...params } = Object.fromEntries(new URLSearchParams(location.search));
    hostApi.openPage(page, params);
  });
  // Back from a session, the page may come out of the back-forward cache with its rail folded away.
  window.addEventListener('pageshow', (event) => { if (event.persisted) document.body.classList.remove('leaving'); });
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
  const offered = failure ? { root: true } : await api('/api/auth/config', { method: 'GET' }).catch(() => ({ root: true }));
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
      <div class="host-token">
        <input type="text" id="root-token" name="token" class="masked" autocomplete="off" autocapitalize="off" spellcheck="false">
        <button type="submit"><i class="fas fa-sign-in-alt" aria-hidden="true"></i></button>
      </div>
    </div>
    <p class="host-muted" data-part="nothing" hidden></p>
    <div class="host-store">
      <a href="https://chromewebstore.google.com/detail/sealskin-isolation/lclgfmnljgacfdpmmmjmfpdelndbbfhk" target="_blank" rel="noopener"><span class="logo logo-chrome"></span><span>Chrome Store</span></a>
      <a href="https://addons.mozilla.org/en-US/firefox/addon/sealskin-isolation/" target="_blank" rel="noopener"><span class="logo logo-firefox"></span><span>Firefox Add-ons</span></a>
      <a href="https://play.google.com/store/apps/details?id=io.linuxserver.sealskin" target="_blank" rel="noopener"><span class="logo logo-android"></span><span>Play Store</span></a>
      <a href="https://apps.apple.com/us/app/sealskin/id6758210210" target="_blank" rel="noopener"><span class="logo logo-ios"></span><span>App Store</span></a>
    </div>`;
  const [error, providers, root, nothing] = ['error', 'providers', 'root', 'nothing']
    .map((part) => form.querySelector(`[data-part="${part}"]`));
  const submit = root.querySelector('button');
  const say = (message) => {
    error.textContent = message;
    error.hidden = !message;
  };

  form.querySelector('h2').textContent = t('web.signInTitle');
  root.querySelector('label').textContent = t('web.rootToken');
  submit.title = t('web.rootSignIn');
  submit.setAttribute('aria-label', t('web.rootSignIn'));
  nothing.textContent = t('web.signIn.notConfigured');

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
  // The providers' buttons come first; the root token sits close under the title otherwise.
  root.classList.toggle('host-field-tight', !provided);
  nothing.hidden = Boolean(failure) || provided || Boolean(offered.root);
  // A server that could not be asked still takes the root token; sending it is the retry.
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
 * Show a user who waits for an administrator why nothing else opens: the
 * server answers no route but the status for them until they are placed in a
 * group or approved. Checking again is a reload.
 *
 * @param {object} status
 */
async function heldPanel(status) {
  const panel = document.getElementById('host-panel');
  const box = document.createElement('div');
  box.className = 'host-box';
  box.innerHTML = `
    <img class="host-logo" src="icons/icon128.png" alt="SealSkin">
    <h2></h2>
    <p class="host-muted" data-part="message"></p>
    <div class="host-actions">
      <button type="button" class="primary" data-part="check"></button>
      <button type="button" class="secondary" data-part="leave"></button>
    </div>`;
  const [message, check, leave] = ['message', 'check', 'leave'].map((part) => box.querySelector(`[data-part="${part}"]`));
  box.querySelector('h2').textContent = t('web.held.title');
  message.textContent = t('web.held.message', { username: status.username });
  check.textContent = t('web.held.check');
  check.addEventListener('click', () => location.reload());
  leave.textContent = t('options.dashboard.signOut');
  // A proxy's sign-in ends at the proxy: with no page of its to open, there is nothing to offer.
  leave.hidden = status.via === 'proxy' && !status.sign_out_url;
  leave.addEventListener('click', async () => {
    await post('/api/auth/signout', {}).catch(() => {});
    if (status.sign_out_url) location.assign(status.sign_out_url);
    else location.reload();
  });
  await remember(null);
  document.body.classList.add('signed-out');
  document.getElementById('app-frame').hidden = true;
  panel.replaceChildren(box);
  panel.hidden = false;
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
  if (status && status.held) {
    await heldPanel(status);
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
  if (status && status.held) {
    await heldPanel(status);
    return;
  }
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
    // The rail folds away while a session grows out of its card in the frame (see home.js).
    onLeave: (leaving) => document.body.classList.toggle('leaving', leaving),
  });
  hooks.openPopup = () => hostApi.openPage('home');
}

start();
