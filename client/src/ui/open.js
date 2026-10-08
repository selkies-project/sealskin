/**
 * An application's address, `/app/<app id>/?<options>`, as a page: what a
 * bookmark or an installed web app opens. It signs the visitor in where
 * nobody is, by way of the web app, then takes the browser to the oldest
 * running session of the application with the same storage and the same
 * kind, room or not, and launches one when there is none, showing the
 * launch as the launching page does.
 *
 * The options are the launcher's, as the dashboard's copy-link control
 * writes them: `home` (`auto`, `cleanroom`, or a storage directory), `gpu`
 * (a device), `room` (`1`), `lang` (a locale), `wayland` (`0` for X11), and
 * `where` (`pool:<name>` or `node:<id>`). Storage and room decide which
 * session is the one; the rest only say how to start a new one. A launch
 * this page started is remembered in the tab's session storage, so a
 * reload follows it instead of starting another.
 *
 * Starting a container changes the machine's network, on which a browser
 * drops the requests it has in flight, the launch request among them. So
 * every request here is sent again after a network error, the launch under
 * one idempotency key, which the server answers once however often it is
 * asked, and the launch is followed by its progress whatever became of the
 * request that started it.
 */

import { loadTranslator } from '../lib/i18n.js';
import { createLaunchView } from '../lib/launch-view.js';
import { defaultLanguage, supportedLangs } from '../lib/languages.js';
import { browserTimezone } from '../lib/timezone.js';

const POLL_MS = 600;
const POLL_SLOW_MS = 1500;
const SLOW_AFTER_MS = 30000;
// A launch remembered this long ago is not followed.
const REMEMBER_MS = 15 * 60 * 1000;
// Pauses between the tries of a request the network dropped.
const RETRY_MS = [1000, 2000, 3000, 4000, 6000, 8000];
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

const $ = (name) => document.getElementById(name);
const appId = (location.pathname.match(/^\/app\/([A-Za-z0-9_-]+)\//) || [])[1] || '';
const params = new URLSearchParams(location.search);
const started = Date.now();
const flag = (value) => ['1', 'true'].includes(String(value || '').toLowerCase());

let t = (key) => key;
let view = null;

// The page's assets resolve under /ui/ (its <base>); its manifest and icon are its own, beside the address.
document.querySelector('link[rel="manifest"]').href = `${location.pathname}manifest.json${location.search}`;
document.querySelector('link[rel="icon"]').href = `${location.pathname}icon.png`;

async function once(url, options) {
  const response = await fetch(url, { credentials: 'same-origin', cache: 'no-store', ...options });
  if (response.status === 401) throw Object.assign(new Error(t('web.signIn.ended')), { status: 401 });
  if (!response.ok) {
    let detail = response.statusText;
    try {
      const body = await response.json();
      if (typeof body.detail === 'string') detail = body.detail;
    } catch (e) { /* no body */ }
    throw Object.assign(new Error(detail), { status: response.status });
  }
  return response.status === 204 ? {} : response.json();
}

/**
 * Call the API, again after a network error.
 *
 * @param {string} url
 * @param {object} [options] fetch options.
 * @param {string} [key] An idempotency key for a request that must not act twice.
 */
async function api(url, options = {}, key = '') {
  const headers = { ...(options.body ? { 'Content-Type': 'application/json' } : {}), ...(key ? { 'X-Idempotency-Key': key } : {}) };
  for (let attempt = 0; ; attempt++) {
    try {
      return await once(url, { ...options, headers });
    } catch (error) {
      if (error.status || attempt >= RETRY_MS.length) throw error;
      await sleep(RETRY_MS[attempt]);
    }
  }
}

/** Sign in through the web app, which comes back here afterwards. */
function toSignIn() {
  location.replace(`/?next=${encodeURIComponent(location.pathname + location.search)}`);
}

function go(url) {
  view.finish();
  location.replace(url);
}

/** The storage directory the address names, as the launcher names it: `auto` is the application's own. */
function wantedHome(app, status) {
  if (!status.settings.persistent_storage || app.is_meta_app) return app.is_meta_app ? 'auto' : 'cleanroom';
  const home = (params.get('home') || 'auto').trim();
  if (home.toLowerCase() === 'cleanroom') return 'cleanroom';
  if (home === 'auto') return `auto-${app.name.toLowerCase().replace(/[\s_]+/g, '-').replace(/[^a-z0-9-]/g, '')}`;
  return home;
}

function rememberKey(home) {
  return `sealskin-open:${appId}:${home}:${flag(params.get('room')) ? 'room' : 'single'}`;
}

function remembered(key) {
  try {
    const kept = JSON.parse(sessionStorage.getItem(key) || 'null');
    return kept && Date.now() - kept.at < REMEMBER_MS ? kept.id : null;
  } catch (e) {
    return null;
  }
}

function remember(key, id) {
  try {
    if (id) sessionStorage.setItem(key, JSON.stringify({ id, at: Date.now() }));
    else sessionStorage.removeItem(key);
  } catch (e) { /* no storage */ }
}

/**
 * Follow a launch's progress until it ends.
 *
 * @param {string} id The launch id.
 * @param {boolean} [known] Whether the server already has the launch; a 404 then ends it.
 * @returns {Promise<object>} The progress at `ready`.
 */
function follow(id, known = false) {
  return new Promise((resolve, reject) => {
    let settled = false;
    const end = (fn, value) => { if (!settled) { settled = true; fn(value); } };
    const poll = async () => {
      if (settled || view.done()) return;
      let wait = Date.now() - started > SLOW_AFTER_MS ? POLL_SLOW_MS : POLL_MS;
      try {
        const response = await fetch(`/api/launch/progress/${encodeURIComponent(id)}`, { credentials: 'same-origin', cache: 'no-store' });
        if (response.status === 401) end(reject, Object.assign(new Error(t('web.signIn.ended')), { status: 401 }));
        else if (response.status === 404 && known) end(reject, new Error(t('web.launch.failed')));
        else if (response.ok) {
          const progress = await response.json();
          if (progress.stage === 'failed') end(reject, new Error(progress.error || t('web.launch.failed')));
          else if (progress.stage === 'ready') end(resolve, progress);
          else view.show(progress.stage, progress.detail || {});
        }
      } catch (e) {
        wait = POLL_SLOW_MS;
      }
      if (!settled) setTimeout(poll, wait);
    };
    poll();
  });
}

/** Start a session with the address's options and wait for it. */
async function launch(app, status, home) {
  const id = crypto.randomUUID();
  const key = rememberKey(home);
  if (home !== 'cleanroom' && home.startsWith('auto-') && !app.is_meta_app) {
    const { home_dirs: homes } = await api('/api/homedirs');
    if (!homes.includes(home)) await api('/api/homedirs', { method: 'POST', body: JSON.stringify({ home_name: home }) });
  } else if (home !== 'cleanroom' && !app.is_meta_app) {
    const { home_dirs: homes } = await api('/api/homedirs');
    if (!homes.includes(home)) throw new Error(t('web.open.noHome', { home }));
  }
  const gpu = params.get('gpu') || '';
  const language = params.get('lang') || '';
  const payload = {
    launch_id: id,
    application_id: app.id,
    home_name: app.is_meta_app ? null : home,
    language: Object.values(supportedLangs).includes(language) ? language : defaultLanguage(navigator.language),
    timezone: browserTimezone(),
    selected_gpu: (status.gpus || []).some((item) => item.device === gpu) ? gpu : null,
    launch_in_room_mode: flag(params.get('room')),
    wayland_mode: !['0', 'false'].includes(String(params.get('wayland') || '').toLowerCase()),
  };
  const [whereKind, whereName] = (params.get('where') || '').split(/:(.*)/);
  if ((whereKind === 'pool' || whereKind === 'node') && whereName) payload[whereKind] = whereName;

  remember(key, id);
  view.show('requesting');
  const following = follow(id);
  // A request the network dropped for good is not the launch: the server may well have it.
  const request = api('/api/launch/simple', { method: 'POST', body: JSON.stringify(payload) }, id).catch((error) => {
    if (error.status) throw error;
    return follow(id, true);
  });
  try {
    const result = await Promise.race([request, following]);
    remember(key, null);
    return result.session_url;
  } catch (error) {
    remember(key, null);
    throw error;
  }
}

async function start() {
  t = await loadTranslator(navigator.language);
  view = createLaunchView(t, $);
  $('app-name').textContent = '…';
  $('close').textContent = t('web.open.openSealSkin');
  $('close').addEventListener('click', () => location.assign('/'));
  setInterval(() => view.tick(), 1000);
  if (!appId) {
    view.fail(t('web.open.notYours'));
    return;
  }
  try {
    view.show('signingIn', {}, t('web.open.signingIn'));
    let status;
    try {
      status = await api('/api/admin/status', { method: 'POST', body: '{}' });
    } catch (error) {
      if (error.status === 401) {
        toSignIn();
        return;
      }
      throw error;
    }
    const app = (await api('/api/applications', { method: 'POST', body: '{}' })).find((item) => item.id === appId);
    if (!app) {
      view.fail(t('web.open.notYours'));
      return;
    }
    document.title = t('web.open.title', { app: app.name });
    $('app-name').textContent = app.name;
    view.showLogo(app.logo || '').catch(() => {});

    view.show('looking', {}, t('web.open.looking'));
    const home = wantedHome(app, status);
    const room = flag(params.get('room'));
    const sessions = (await api('/api/sessions'))
      .filter((s) => s.app_id === appId && Boolean(s.is_collaboration) === room && (s.home || 'cleanroom') === (home === 'cleanroom' ? 'cleanroom' : home))
      .sort((a, b) => a.created_at - b.created_at || a.session_id.localeCompare(b.session_id));
    if (sessions.length) {
      view.show('attaching', {}, t('web.open.attaching'));
      go(sessions[0].session_url);
      return;
    }
    const key = rememberKey(home);
    const pending = remembered(key);
    if (pending) {
      try {
        const progress = await follow(pending, true);
        remember(key, null);
        go(progress.session_url);
        return;
      } catch (error) {
        if (error.status === 401) throw error;
        remember(key, null);
      }
    }
    go(await launch(app, status, home));
  } catch (error) {
    if (error.status === 401) {
      toSignIn();
      return;
    }
    view.fail(error.message || t('web.launch.failed'));
  }
}

start();
