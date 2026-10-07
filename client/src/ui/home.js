/**
 * Web app home: the launcher as a page. Search or paste a link, see the
 * running sessions, and launch from a grid of applications; a tile opens the
 * launch panel, and its play button launches with the options last used.
 * `?view=sessions` shows the sessions alone.
 *
 * A launch shows its progress on the tile and in the sessions row, and the
 * web app goes to the session when it is ready; asked to open in a new tab,
 * the launch takes a tab at the click, which shows `launching.html` until
 * then. The launch logic mirrors `popup.js`, which the extension and the
 * mobile app keep. The drawer's copy-link control writes the application's
 * address with the options chosen, `/app/<app id>/?<options>`, which opens
 * the same session again from a bookmark or an installed web app.
 */

import { bridge, request } from '../lib/bridge.js';
import { secureFetch, getContextBlob, uploadInChunks, openPage } from '../lib/api.js';
import { loadTranslator, applyTranslations } from '../lib/i18n.js';
import { defaultLanguage as nearestLanguage, supportedLangs } from '../lib/languages.js';
import { browserTimezone } from '../lib/timezone.js';
import { announce, escapeHtml, formatLogoSrc, hydrateLogos, timeAgo, currentLocale, showToast } from '../lib/dom.js';
import { noticeDialog } from '../lib/modal.js';

const RECENT_MAX = 8;
const SESSIONS_REFRESH_MS = 10000;
const PROGRESS_MS = 1000;
// A launching page that opens after a failure still asks what happened.
const CHANNEL_LINGER_MS = 120000;
const DEFAULT_SEARCH_ENGINE = 'https://google.com/search?q=';
const LOOKS_LIKE_URL = /^(https?:\/\/\S+|[a-z0-9-]+(\.[a-z0-9-]+)+(:\d+)?(\/\S*)?)$/i;

const $ = (id) => document.getElementById(id);
const heroInput = $('hero-input');
const appGrid = $('app-grid');
const sessionsRow = $('sessions-row');
const drawer = $('drawer');
const homeDirSelect = $('homeDirectory');
const gpuSelect = $('gpuSelect');
const whereSelect = $('whereSelect');
const languageSelect = $('language');

let t;
let info;
let status = {};
let apps = [];
let sessions = [];
let homeDirs = [];
// What the user may know of the cluster, or null on a server that is one node.
let cluster = null;
// The link or file to open, from the shell, the search box, or a drop.
let context = null;
// `{apps: {<id>: options}, recent: [<id>]}`, kept in the shell under `simple_launch_profile`.
let memory = { apps: {}, recent: [] };
let activeType = 'all';
let drawerApp = null;
// Launches in flight, by launch id.
const flights = new Map();

const notify = (message, isError = false) => showToast(escapeHtml(message), isError);
// What the shell answers when it cannot open a tab for a session, in the user's words.
const shellError = (error) => ({
  noSessionOrigin: t('popup.status.noSessionOrigin'),
  popupBlocked: t('web.home.popupBlocked'),
}[error.message] || error.message);

// --- Data ---------------------------------------------------------------------

async function loadAll() {
  const [statusData, appsData, sessionsData] = await Promise.all([
    secureFetch('/api/admin/status', { method: 'POST', body: '{}' }),
    secureFetch('/api/applications', { method: 'POST', body: '{}' }),
    secureFetch('/api/sessions', { method: 'GET' }),
  ]);
  status = statusData;
  apps = appsData || [];
  sessions = sessionsData || [];
  const [mine, homes] = await Promise.all([
    status.clustered ? secureFetch('/api/cluster', { method: 'GET' }).catch(() => null) : null,
    status.settings.persistent_storage ? secureFetch('/api/homedirs', { method: 'GET' }).catch(() => null) : null,
  ]);
  cluster = mine && mine.clustered ? mine : null;
  homeDirs = (homes && homes.home_dirs) || [];
}

async function refreshSessions() {
  try {
    sessions = await secureFetch('/api/sessions', { method: 'GET' });
    renderSessions();
    renderStatusChips();
  } catch (e) { /* the next round tries again */ }
}

function saveMemory() {
  return bridge.storageSet({ simple_launch_profile: memory }).catch((e) => console.warn('Could not save launch options:', e));
}

// --- Launch options -----------------------------------------------------------

/** The session locale nearest the browser's, as the launcher has always picked it. */
function defaultLanguage() {
  return nearestLanguage(currentLocale());
}

const nodeName = (id) => {
  const node = cluster && cluster.nodes.find((n) => n.id === id);
  return node ? node.name || node.id : id;
};
const hasStorage = (app) => Boolean(status.settings.persistent_storage && app.home_directories);
const namedHomes = () => homeDirs.filter((dir) => dir !== '_sealskin_shared_files' && !dir.startsWith('auto-'));

/** The GPUs an application can use, none when the user may not. */
function gpusFor(app) {
  if (!status.settings.gpu) return [];
  return (status.gpus || []).filter((gpu) => (gpu.driver === 'nvidia' ? app.nvidia_support : app.dri3_support));
}

function whereChoices() {
  if (!cluster) return [];
  return [...cluster.pools.map((pool) => `pool:${pool}`), ...cluster.nodes.filter((node) => node.alive).map((node) => `node:${node.id}`)];
}

/** The options an application launches with: those last used where they still hold, else the defaults. */
function optionsFor(app) {
  const saved = memory.apps[app.id] || {};
  const homes = app.is_meta_app ? [] : namedHomes();
  const homeDir = ['auto', 'cleanroom', ...homes].includes(saved.homeDir) ? saved.homeDir : 'auto';
  return {
    homeDir: hasStorage(app) ? homeDir : 'cleanroom',
    gpu: gpusFor(app).some((gpu) => gpu.device === saved.gpu) ? saved.gpu : null,
    where: whereChoices().includes(saved.where) ? saved.where : '',
    language: Object.values(supportedLangs).includes(saved.language) ? saved.language : defaultLanguage(),
    waylandMode: saved.waylandMode !== false,
    room: Boolean(saved.room),
    openFile: saved.openFile !== false,
    newTab: Boolean(saved.newTab),
  };
}

/**
 * The application's address with `options`, which opens the same session
 * again: `home` names the storage (`auto` is left out, being the default),
 * `room` marks a collaborative session, and the rest say how a new one
 * starts.
 *
 * @param {object} app
 * @param {object} options See `optionsFor`.
 * @returns {string} An absolute URL.
 */
function addressFor(app, options) {
  const query = new URLSearchParams();
  if (hasStorage(app) && options.homeDir && options.homeDir !== 'auto') query.set('home', options.homeDir);
  if (options.gpu) query.set('gpu', options.gpu);
  if (options.room) query.set('room', '1');
  if (options.language && options.language !== defaultLanguage()) query.set('lang', options.language);
  if (!options.waylandMode) query.set('wayland', '0');
  if (options.where) query.set('where', options.where);
  const search = query.toString();
  return `${location.origin}/app/${encodeURIComponent(app.id)}/${search ? `?${search}` : ''}`;
}

// --- Rendering ----------------------------------------------------------------

function renderStatusChips() {
  const chips = [];
  const { allowance } = status;
  if (allowance) {
    chips.push([t('web.home.allowance'), t('web.home.allowanceValue', {
      used: Number(allowance.used).toFixed(1), hours: allowance.hours, period: t(`options.periods.${allowance.period}`).toLowerCase(),
    })]);
  }
  const limit = cluster ? cluster.session_limit : status.settings.session_limit;
  if (limit >= 0) chips.push([t('common.sessions'), `${sessions.length} / ${limit}`]);
  $('status-chips').innerHTML = chips
    .map(([label, value]) => `<div class="stat-chip"><small>${escapeHtml(label)}</small><strong>${escapeHtml(value)}</strong></div>`)
    .join('');
}

function stageText(flight) {
  if (flight.stage === 'uploading') return t('web.launch.uploading', { done: flight.detail.done || 0, total: flight.detail.total || 0 });
  const key = `web.launch.stages.${flight.stage}`;
  const text = t(key, { node: flight.detail.node || '' });
  return text === key ? t('web.launch.stages.requesting') : text;
}

function renderSessions() {
  const only = new URLSearchParams(location.search).get('view') === 'sessions';
  const launching = [...flights.values()].map((flight) => `
        <div class="session-tile launching" data-flight="${flight.id}">
            <img data-logo-src="${escapeHtml(flight.app.logo)}" src="icons/icon128.png" alt="">
            <div class="session-info">
                <div class="name">${escapeHtml(flight.app.name)}</div>
                <div class="meta"><span class="inline-spinner"></span><span class="stage">${escapeHtml(stageText(flight))}</span></div>
            </div>
        </div>`);
  const running = sessions.map((s) => {
    const badges = [];
    if (s.is_collaboration) badges.push(`<span class="badge"><i class="fas fa-users"></i>${escapeHtml(t('web.home.room'))}</span>`);
    if (s.gpu) badges.push(`<span class="badge"><i class="fas fa-microchip"></i>${escapeHtml(t('common.gpu'))}</span>`);
    if (s.home) badges.push(`<span class="badge" title="${escapeHtml(s.home)}"><i class="fas fa-hdd"></i>${escapeHtml(s.home)}</span>`);
    const meta = [t('web.home.started', { when: timeAgo(s.created_at, t) })];
    if (cluster && s.node) meta.push(s.node);
    if (s.launch_context) meta.push(s.launch_context.value);
    return `
        <div class="session-tile" data-session-id="${escapeHtml(s.session_id)}">
            <img data-logo-src="${escapeHtml(s.app_logo)}" src="icons/icon128.png" alt="">
            <div class="session-info">
                <div class="name">${escapeHtml(s.app_name)}</div>
                <div class="meta" title="${escapeHtml(meta.join(' · '))}">${escapeHtml(meta.join(' · '))}</div>
                ${badges.length ? `<div class="badges">${badges.join('')}</div>` : ''}
            </div>
            <div class="session-actions">
                <button class="primary" data-action="open">${escapeHtml(t('common.open'))}</button>
                <button class="secondary" data-action="stop" title="${escapeHtml(t('common.stop'))}"><i class="fas fa-stop"></i></button>
            </div>
        </div>`;
  });
  const cards = [...launching, ...running];
  sessionsRow.innerHTML = cards.join('');
  $('sessions-section').hidden = !only && cards.length === 0;
  $('sessions-empty').hidden = !only || cards.length > 0;
  hydrateLogos(sessionsRow);
}

/** The applications the search, the chips, and what is being opened leave, recent ones first. */
function visibleApps() {
  let list = apps;
  if (context && context.action === 'url') {
    list = list.filter((app) => app.url_support);
  } else if (context && context.filename && context.filename.includes('.')) {
    const extension = context.filename.split('.').pop().toLowerCase();
    const matching = list.filter((app) => (app.extensions || []).includes(extension));
    if (matching.length) list = matching;
  }
  const term = context && context.typed ? '' : heroInput.value.trim().toLowerCase();
  if (term) list = list.filter((app) => app.name.toLowerCase().includes(term));
  if (activeType === 'recent') list = list.filter((app) => memory.recent.includes(app.id));
  else if (activeType !== 'all') list = list.filter((app) => (app.type || '') === activeType);
  const rank = (app) => {
    const at = memory.recent.indexOf(app.id);
    return at < 0 ? RECENT_MAX : at;
  };
  return [...list].sort((a, b) => rank(a) - rank(b) || a.name.localeCompare(b.name));
}

function renderTypeChips() {
  const types = [...new Set(apps.map((app) => app.type || ''))].sort();
  const chips = [['all', t('web.home.typeAll')]];
  if (memory.recent.some((id) => apps.some((app) => app.id === id))) chips.push(['recent', t('web.home.typeRecent')]);
  // One kind of application needs no filter.
  if (types.length > 1) types.forEach((type) => chips.push([type, type || t('web.home.typeOther')]));
  if (!chips.some(([value]) => value === activeType)) activeType = 'all';
  $('type-chips').innerHTML = chips.length > 1
    ? chips.map(([value, label]) => `<button type="button" class="secondary${value === activeType ? ' active' : ''}" data-type="${escapeHtml(value)}">${escapeHtml(label)}</button>`).join('')
    : '';
}

function renderApps() {
  const list = visibleApps();
  const busy = new Map([...flights.values()].map((flight) => [flight.app.id, flight]));
  appGrid.innerHTML = list.map((app) => {
    const flight = busy.get(app.id);
    return `
        <div class="app-tile${flight ? ' busy' : ''}" role="button" tabindex="0" data-app="${escapeHtml(app.id)}">
            <img data-logo-src="${escapeHtml(app.logo)}" src="icons/icon128.png" alt="">
            <span class="name">${escapeHtml(app.name)}</span>
            <span class="stage">${flight ? `<span class="inline-spinner"></span>${escapeHtml(stageText(flight))}` : ''}</span>
            <button type="button" class="primary quick" title="${escapeHtml(t('web.home.quickLaunch'))}"><i class="fas fa-play"></i></button>
        </div>`;
  }).join('');
  hydrateLogos(appGrid);
  const empty = list.length === 0;
  $('apps-empty').hidden = !empty;
  if (empty) {
    const none = apps.length === 0;
    $('apps-empty-text').textContent = t(none ? (status.is_admin ? 'web.home.noAppsAdmin' : 'web.home.noApps') : 'web.home.noMatch');
    $('apps-empty-admin').hidden = !(none && status.is_admin);
  }
}

function renderSkeleton() {
  appGrid.innerHTML = Array.from({ length: 12 }, () => '<div class="app-tile skeleton"><div class="bone icon"></div><div class="bone line"></div></div>').join('');
}

/** Show a launch's stage where its tile and its card are, without redrawing either. */
function showFlight(flight) {
  const text = stageText(flight);
  const card = sessionsRow.querySelector(`[data-flight="${flight.id}"] .stage`);
  if (card) card.textContent = text;
  const tile = appGrid.querySelector(`[data-app="${CSS.escape(flight.app.id)}"] .stage`);
  if (tile) tile.innerHTML = `<span class="inline-spinner"></span>${escapeHtml(text)}`;
}

function renderContext() {
  const bar = $('context-bar');
  bar.hidden = !context;
  if (!context) return;
  const isUrl = context.action === 'url';
  $('context-icon').className = `fas ${isUrl ? 'fa-link' : 'fa-file-alt'}`;
  $('context-text').textContent = isUrl
    ? t('web.home.openingUrl', { url: context.targetUrl })
    : t('web.home.openingFile', { filename: context.filename || '' });
}

function setContext(next) {
  context = next;
  renderContext();
  renderApps();
}

// --- Launch panel -------------------------------------------------------------

function fillSelect(select, choices, value) {
  select.innerHTML = choices.map(([choice, label]) => `<option value="${escapeHtml(choice)}">${escapeHtml(label)}</option>`).join('');
  select.value = value;
}

function openDrawer(app) {
  drawerApp = app;
  const options = optionsFor(app);
  $('drawer-name').textContent = app.name;
  $('drawer-context').textContent = context ? $('context-text').textContent : '';
  $('drawer-logo').src = 'icons/icon128.png';
  formatLogoSrc(app.logo).then((src) => { if (drawerApp === app) $('drawer-logo').src = src; });

  // In a cluster each home is named with the node that holds it.
  const held = (dir) => (cluster && cluster.homes[dir] ? ` (${nodeName(cluster.homes[dir])})` : '');
  const homes = app.is_meta_app ? [] : namedHomes();
  fillSelect(homeDirSelect, [
    ['auto', t('popup.launchView.autoHome')],
    ['cleanroom', t('popup.launchView.cleanroom')],
    ...homes.map((dir) => [dir, dir + held(dir)]),
  ], options.homeDir);
  $('homedir-form-group').hidden = !hasStorage(app);

  const gpus = gpusFor(app);
  fillSelect(gpuSelect, [
    ['', t('popup.launchView.noGpu')],
    ...gpus.map((gpu) => [gpu.device, `${gpu.device.split('/').pop()} (${gpu.driver})`]),
  ], options.gpu || '');
  $('gpu-form-group').hidden = gpus.length === 0;

  fillSelect(whereSelect, [
    ['', t('popup.launchView.whereAuto')],
    ...(cluster ? cluster.pools.map((pool) => [`pool:${pool}`, t('web.home.wherePool', { pool })]) : []),
    ...(cluster ? cluster.nodes.filter((node) => node.alive).map((node) => [`node:${node.id}`, node.name || node.id]) : []),
  ], options.where);
  $('where-form-group').hidden = !cluster;

  languageSelect.value = options.language;
  $('collaborationMode').checked = options.room;
  $('waylandMode').checked = options.waylandMode;
  $('openInTab').checked = options.newTab;
  $('openFileOnLaunch').checked = options.openFile;
  $('open-file-group').hidden = !(context && context.action === 'file');

  drawer.hidden = false;
  $('drawer-backdrop').hidden = false;
  $('launch-btn').focus();
}

function closeDrawer() {
  drawer.hidden = true;
  $('drawer-backdrop').hidden = true;
  drawerApp = null;
}

function drawerOptions() {
  return {
    homeDir: $('homedir-form-group').hidden ? 'cleanroom' : homeDirSelect.value,
    gpu: $('gpu-form-group').hidden ? null : gpuSelect.value || null,
    where: whereSelect.value,
    language: languageSelect.value,
    waylandMode: $('waylandMode').checked,
    room: $('collaborationMode').checked,
    openFile: $('openFileOnLaunch').checked,
    newTab: $('openInTab').checked,
  };
}

// --- Launching ----------------------------------------------------------------

/**
 * Launch an application with what is being opened, if anything. The web
 * app goes to the session once it runs; for a session in a new tab, the
 * tab is taken first, at the click, and its launching page then follows the
 * server's progress, and is told here of a failure the server never saw.
 *
 * @param {object} app
 * @param {object} options See `optionsFor`.
 */
async function launch(app, options) {
  const id = crypto.randomUUID();
  const opening = context;
  if (options.newTab) {
    try {
      await request('reserveTab', { reserve: true, launch: { id, app: app.name, logo: app.logo || '', room: options.room } });
    } catch (error) {
      notify(shellError(error), true);
      return;
    }
  }

  const flight = { id, app, stage: 'requesting', detail: {} };
  flights.set(id, flight);
  // The launching page asks for news when it loads, which may be after a failure.
  const channel = new BroadcastChannel(`sealskin-launch-${id}`);
  let news = null;
  const tell = (message) => {
    news = message;
    channel.postMessage(message);
  };
  channel.onmessage = (event) => { if (event.data && event.data.hello && news) channel.postMessage(news); };
  if (opening) setContext(null);
  else renderApps();
  heroInput.value = '';
  renderSessions();

  let posted = false;
  const poll = setInterval(async () => {
    if (!posted) return;
    try {
      const progress = await secureFetch(`/api/launch/progress/${id}`, { method: 'GET' });
      flight.stage = progress.stage;
      flight.detail = progress.detail || {};
      showFlight(flight);
    } catch (e) { /* not known to the server yet */ }
  }, PROGRESS_MS);

  try {
    let homeName = options.homeDir;
    if (homeName === 'auto') {
      homeName = `auto-${app.name.toLowerCase().replace(/[\s_]+/g, '-').replace(/[^a-z0-9-]/g, '')}`;
      if (!homeDirs.includes(homeName) && !app.is_meta_app) {
        await secureFetch('/api/homedirs', { method: 'POST', body: JSON.stringify({ home_name: homeName }) });
        homeDirs.push(homeName);
      }
    }
    const payload = {
      launch_id: id,
      application_id: app.id,
      home_name: homeName,
      language: options.language,
      timezone: browserTimezone(),
      selected_gpu: options.gpu,
      launch_in_room_mode: options.room,
      wayland_mode: options.waylandMode,
    };
    // Left out, the server picks where the session starts.
    const [whereKind, whereName] = options.where.split(/:(.*)/);
    if (whereKind) payload[whereKind] = whereName;

    let endpoint = '/api/launch/simple';
    if (opening && opening.action === 'url') {
      endpoint = '/api/launch/url';
      payload.url = opening.targetUrl;
    } else if (opening && opening.action === 'file') {
      const blob = await getContextBlob(opening);
      const filename = opening.filename || 'uploaded.file';
      const { uploadId, totalChunks } = await uploadInChunks(blob, filename, {
        onProgress: (done, total) => {
          flight.stage = 'uploading';
          flight.detail = { done, total };
          showFlight(flight);
          tell({ uploading: { done, total } });
        },
      });
      endpoint = '/api/launch/file';
      Object.assign(payload, { filename, upload_id: uploadId, total_chunks: totalChunks, open_file_on_launch: options.openFile });
      flight.stage = 'requesting';
    } else if (opening && opening.action === 'server-file') {
      if (homeName === 'cleanroom') throw new Error(t('web.home.serverFileNeedsHome'));
      endpoint = '/api/launch/file_path';
      payload.filename = opening.filename;
    }

    posted = true;
    tell({ posted: true });
    // The server answers once the session runs, which a first image pull can delay for minutes.
    const data = await secureFetch(endpoint, { method: 'POST', body: JSON.stringify(payload) }, { timeout: 0 });
    memory.apps[app.id] = options;
    memory.recent = [app.id, ...memory.recent.filter((recent) => recent !== app.id)].slice(0, RECENT_MAX);
    await saveMemory();
    // Opened in this tab, the session takes the web app's place now.
    await request('openSession', { sessionId: data.session_id, sessionUrl: data.session_url, launchId: id });
    channel.close();
  } catch (error) {
    tell({ error: shellError(error) });
    // What was being opened is still there to try again.
    if (opening && !context) setContext(opening);
    setTimeout(() => channel.close(), CHANNEL_LINGER_MS);
    notify(t('popup.status.error', { message: shellError(error) }), true);
  }
  clearInterval(poll);
  flights.delete(id);
  await refreshSessions();
  renderTypeChips();
  renderApps();
}

async function openSession(session) {
  try {
    await bridge.focusSession(session);
  } catch (error) {
    notify(shellError(error), true);
  }
}

async function stopSession(sessionId, button) {
  button.disabled = true;
  button.innerHTML = '<i class="fas fa-spinner fa-spin"></i>';
  try {
    await bridge.closeSession(sessionId);
  } catch (error) {
    notify(t('popup.status.errorClosingSession', { message: error.message }), true);
  }
  await refreshSessions();
}

// --- Events -------------------------------------------------------------------

/** Treat the search box's text as a link to open, or as a search for one. */
function typedContext(asSearch) {
  const text = heroInput.value.trim();
  if (LOOKS_LIKE_URL.test(text)) return { action: 'url', targetUrl: /^https?:\/\//i.test(text) ? text : `https://${text}`, typed: true };
  if (asSearch && text) {
    const engine = (info.config && info.config.searchEngineUrl) || DEFAULT_SEARCH_ENGINE;
    return { action: 'url', targetUrl: `${engine}${encodeURIComponent(text)}`, typed: true };
  }
  return null;
}

function takeFile(file) {
  if (!file) return;
  heroInput.value = '';
  setContext({ action: 'file', filename: file.name, file });
  $('apps-section').scrollIntoView({ block: 'nearest' });
}

function bindEvents() {
  heroInput.addEventListener('input', () => {
    const typed = typedContext(false);
    if (typed || (context && context.typed)) setContext(typed);
    else renderApps();
  });
  heroInput.addEventListener('keydown', (event) => {
    if (event.key !== 'Enter') return;
    const list = visibleApps();
    // Text that names no application is a search to open in one.
    if (!context && list.length === 0) setContext(typedContext(true));
    else if (list.length === 1) openDrawer(list[0]);
  });
  $('context-clear').addEventListener('click', () => {
    if (context && context.typed) heroInput.value = '';
    setContext(null);
  });

  $('type-chips').addEventListener('click', (event) => {
    const chip = event.target.closest('button[data-type]');
    if (!chip) return;
    activeType = chip.dataset.type;
    renderTypeChips();
    renderApps();
  });

  const tileApp = (event) => {
    const tile = event.target.closest('.app-tile[data-app]');
    return tile && !tile.classList.contains('busy') ? apps.find((app) => app.id === tile.dataset.app) : null;
  };
  appGrid.addEventListener('click', (event) => {
    const app = tileApp(event);
    if (!app) return;
    if (event.target.closest('.quick')) launch(app, optionsFor(app));
    else openDrawer(app);
  });
  appGrid.addEventListener('keydown', (event) => {
    if ((event.key !== 'Enter' && event.key !== ' ') || event.target.closest('.quick')) return;
    const app = tileApp(event);
    if (!app) return;
    event.preventDefault();
    openDrawer(app);
  });

  sessionsRow.addEventListener('click', (event) => {
    const button = event.target.closest('button[data-action]');
    if (!button) return;
    const { sessionId } = button.closest('.session-tile').dataset;
    if (button.dataset.action === 'stop') stopSession(sessionId, button);
    else openSession(sessions.find((s) => s.session_id === sessionId));
  });

  $('drawer-close').addEventListener('click', closeDrawer);
  $('drawer-backdrop').addEventListener('click', closeDrawer);
  document.addEventListener('keydown', (event) => { if (event.key === 'Escape' && !drawer.hidden) closeDrawer(); });
  $('launch-btn').addEventListener('click', () => {
    const app = drawerApp;
    const options = drawerOptions();
    closeDrawer();
    launch(app, options);
  });
  $('copy-link-btn').addEventListener('click', async () => {
    const address = addressFor(drawerApp, drawerOptions());
    try {
      await navigator.clipboard.writeText(address);
      notify(t('web.home.linkCopied'));
    } catch (error) {
      // A browser that refuses the clipboard leaves the address to copy by hand.
      noticeDialog(t, { title: t('web.home.copyLink'), message: t('web.home.copyLinkManual'), value: address });
    }
  });

  $('upload-button').addEventListener('click', () => $('file-input').click());
  $('file-input').addEventListener('change', (event) => {
    takeFile(event.target.files[0]);
    event.target.value = '';
  });
  // dragenter and dragleave fire for every element crossed; the count is the depth.
  let dragDepth = 0;
  const carriesFile = (event) => event.dataTransfer && [...event.dataTransfer.types].includes('Files');
  window.addEventListener('dragenter', (event) => {
    if (!carriesFile(event)) return;
    dragDepth++;
    $('drop-overlay').hidden = false;
  });
  window.addEventListener('dragleave', (event) => {
    if (!carriesFile(event)) return;
    dragDepth = Math.max(0, dragDepth - 1);
    if (!dragDepth) $('drop-overlay').hidden = true;
  });
  window.addEventListener('dragover', (event) => { if (carriesFile(event)) event.preventDefault(); });
  window.addEventListener('drop', (event) => {
    if (!carriesFile(event)) return;
    event.preventDefault();
    dragDepth = 0;
    $('drop-overlay').hidden = true;
    takeFile(event.dataTransfer.files[0]);
  });

  $('apps-empty-admin').addEventListener('click', () => openPage('options', { section: 'AppStore' }));
  setInterval(() => { if (!document.hidden) refreshSessions(); }, SESSIONS_REFRESH_MS);
}

async function init() {
  info = await announce();
  t = await loadTranslator(info.locale);
  applyTranslations(document.body, t);
  const only = new URLSearchParams(location.search).get('view') === 'sessions';
  $('hero').hidden = only;
  $('apps-section').hidden = only;
  if (!info.config || !info.config.username) {
    bridge.openPage('connect');
    return;
  }

  languageSelect.innerHTML = Object.entries(supportedLangs)
    .map(([name, value]) => `<option value="${escapeHtml(value)}">${escapeHtml(name)}</option>`)
    .join('');
  renderSkeleton();
  bindEvents();

  try {
    const [pending, stored] = await Promise.all([bridge.getContext(), bridge.storageGet(['simple_launch_profile']), loadAll()]);
    const saved = stored.simple_launch_profile || {};
    memory = { apps: saved.apps || {}, recent: saved.recent || [] };
    if (pending && pending.action === 'search') {
      const engine = info.config.searchEngineUrl || DEFAULT_SEARCH_ENGINE;
      context = { action: 'url', targetUrl: `${engine}${encodeURIComponent(pending.selectionText)}` };
    } else if (pending && pending.action) {
      context = pending;
    }
  } catch (error) {
    appGrid.innerHTML = '';
    notify(t('popup.status.error', { message: error.message }), true);
    return;
  }
  renderStatusChips();
  renderSessions();
  renderTypeChips();
  renderContext();
  renderApps();
  if (!only) heroInput.focus();
}

init();
