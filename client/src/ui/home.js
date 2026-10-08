/**
 * Web app home: the launcher as a page. Search or paste a link, see the
 * running sessions, and launch from a grid of applications; a tile opens
 * the launch options in a window that unfolds from the tile, as a desktop
 * opens an application (a sheet on a narrow window), and the play button in
 * its corner launches at once with the options last used.
 *
 * The page is the user's own: applications starred as favorites come first,
 * in the order they drag them into (or move them from the panel), hidden
 * ones leave the grid until shown again, and the background is theirs to
 * pick, a gradient, a color, or a picture of their own, blurred and dimmed
 * to taste; a picture is kept as data, never fetched from an address.
 * That layout is kept in the browser under `home_layout`, beside the launch
 * options under `simple_launch_profile`.
 *
 * A running session is a card with its desktop on it, a thumbnail the
 * server takes of the container (`/api/sessions/<id>/screenshot`). A
 * screenshot reads a frame back from the GPU, which a session's user can
 * feel, so the page asks when someone is looking: a fresh one of every
 * session when the page is landed on, loaded, shown again, or come back to,
 * and then now and then while the page is being used, never while it is
 * hidden or has been idle. A session that opens in this tab grows out of
 * its card: a copy of the card's desktop animates over the page, the host
 * folds its rail away (`bridge.leaving`), and the tab then goes to the
 * session.
 *
 * A launch shows its progress on the tile and as a card in the sessions
 * row with the launching page's checklist of stages (`launch-view.js`), and the
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
import { createLaunchView } from '../lib/launch-view.js';

const RECENT_MAX = 8;
const SESSIONS_REFRESH_MS = 10000;
const SHOTS_REFRESH_MS = 15000;
// With no pointer, key, or scroll for this long, the page is not being looked at.
const IDLE_MS = 60000;
const PROGRESS_MS = 1000;
const ZOOM_MS = 340;
const ZOOM_EASING = 'cubic-bezier(0.22, 1, 0.36, 1)';
const VANISH_MS = 360;
const SLIDE_MS = 320;
// A launching page that opens after a failure still asks what happened.
const CHANNEL_LINGER_MS = 120000;
const DEFAULT_SEARCH_ENGINE = 'https://google.com/search?q=';
const LOOKS_LIKE_URL = /^(https?:\/\/\S+|[a-z0-9-]+(\.[a-z0-9-]+)+(:\d+)?(\/\S*)?)$/i;
const LAYOUT_KEY = 'home_layout';
// A picked-up tile is a drag once the pointer has moved this far.
const DRAG_THRESHOLD = 6;
// An uploaded background is scaled to fit this and kept under the size, as browser storage is small.
const BG_MAX_EDGE = 1920;
const BG_MAX_BYTES = 1200000;
// Preset colors for a color background; the styles paint gradients out of whichever is chosen.
const SWATCHES = ['#7c6cf0', '#e04fb0', '#3b82f6', '#06b6d4', '#14b8a6', '#22c55e', '#f59e0b', '#ef4444', '#64748b', '#1f2937'];
const STYLES = ['solid', 'glow', 'diagonal', 'aurora', 'vignette'];
// How a picture sits on the page: fill it, fit inside it, stretch to it, or sit at its natural size in the middle.
const FITS = ['cover', 'contain', 'stretch', 'center'];
const DEFAULT_BACKGROUND = { kind: 'default', color: '#7c6cf0', style: 'glow', image: '', fit: 'cover', blur: 0, dim: 30 };
// Where the gradients of the first layouts map onto the styles.
const LEGACY_GRADIENTS = { dusk: '#7c6cf0', ocean: '#06b6d4', ember: '#f59e0b', forest: '#22c55e' };

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
// The latest thumbnail of each session, `{image, taken_at}` by session id, and the sessions being asked for one.
const shots = new Map();
const shotFlights = new Set();
let lastActive = Date.now();
// Sessions the server is stopping; their cards take no clicks meanwhile.
const stopping = new Set();
// What the user may know of the cluster, or null on a server that is one node.
let cluster = null;
// The link or file to open, from the shell, the search box, or a drop.
let context = null;
// `{apps: {<id>: options}, recent: [<id>]}`, kept in the shell under `simple_launch_profile`.
let memory = { apps: {}, recent: [] };
// `{favorites: [<id>], hidden: [<id>], background}`, kept in the shell under `home_layout`.
let layout = { favorites: [], hidden: [], background: { ...DEFAULT_BACKGROUND } };
let activeType = 'all';
let drawerApp = null;
// The tile the launch window opened from, which it folds back into, and the fold under way.
let anchorTile = null;
let closing = null;
const WINDOW_MS = 260;
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
    const fresh = await secureFetch('/api/sessions', { method: 'GET' });
    // The server drops a session from its list before its container has stopped; its card stays until the stop answers.
    const held = sessions.filter((s) => stopping.has(s.session_id) && !fresh.some((f) => f.session_id === s.session_id));
    sessions = [...fresh, ...held].sort((a, b) => b.created_at - a.created_at);
    renderSessions();
    renderStatusChips();
  } catch (e) { /* the next round tries again */ }
  // A session new to the list is shown at once; the others wait for the next round.
  refreshShots({ missing: true });
}

/** Whether someone is looking at the page: shown, and used within `IDLE_MS`. */
const looking = () => !document.hidden && Date.now() - lastActive < IDLE_MS;

/**
 * Ask for the thumbnail of every session, each shown as it arrives; a
 * session without one keeps its logo.
 *
 * @param {object} [options]
 * @param {boolean} [options.fresh] Have the server capture now rather than answer with a recent one.
 * @param {boolean} [options.missing] Only the sessions that have no thumbnail yet.
 */
function refreshShots({ fresh = false, missing = false } = {}) {
  const alive = new Set(sessions.map((s) => s.session_id));
  [...shots.keys()].filter((id) => !alive.has(id)).forEach((id) => shots.delete(id));
  sessions.forEach(async ({ session_id: id }) => {
    if (shotFlights.has(id) || (missing && shots.has(id))) return;
    shotFlights.add(id);
    try {
      const shot = await secureFetch(`/api/sessions/${id}/screenshot${fresh ? '?fresh=1' : ''}`, { method: 'GET' });
      if (shot && shot.image) {
        shots.set(id, shot);
        paintShot(id);
      }
    } catch (e) { /* no desktop to show yet */ }
    shotFlights.delete(id);
  });
}

/** Put a session's latest thumbnail on its card, without redrawing the card. */
function paintShot(id) {
  const tile = sessionsRow.querySelector(`[data-session-id="${CSS.escape(id)}"]`);
  const shot = shots.get(id);
  if (!tile || !shot) return;
  tile.querySelector('.preview').src = shot.image;
  tile.classList.add('has-shot');
}

function saveMemory() {
  return bridge.storageSet({ simple_launch_profile: memory }).catch((e) => console.warn('Could not save launch options:', e));
}

function saveLayout() {
  return bridge.storageSet({ [LAYOUT_KEY]: layout }).catch((e) => console.warn('Could not save the layout:', e));
}

const isFavorite = (app) => layout.favorites.includes(app.id);
const isHidden = (app) => layout.hidden.includes(app.id);
const favoriteApps = () => layout.favorites.map((id) => apps.find((app) => app.id === id)).filter(Boolean);

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

// Stand-ins for the parts of the launching page a card does not show.
const spares = new Map();

/** A lookup of the launching page's parts inside a launch's card, for its `createLaunchView`. */
function partsOf(launchId) {
  return (name) => {
    const part = sessionsRow.querySelector(`[data-flight="${launchId}"] [data-part="${name}"]`);
    if (part) return part;
    if (!spares.has(name)) spares.set(name, document.createElement('span'));
    return spares.get(name);
  };
}

function launchingCard(flight) {
  return `
        <div class="session-tile launching" data-key="flight:${flight.id}" data-flight="${flight.id}">
            <div class="launch-card">
                <div class="launch-head">
                    <img class="logo" data-logo-src="${escapeHtml(flight.app.logo)}" src="icons/icon128.png" alt="">
                    <div class="session-info">
                        <div class="name">${escapeHtml(flight.app.name)}</div>
                        <div class="meta" data-part="node" hidden></div>
                    </div>
                    <span class="meta elapsed" data-part="elapsed"></span>
                </div>
                <p class="stage" data-part="stage"></p>
                <p class="meta hint" data-part="hint" hidden></p>
                <ol class="steps" data-part="steps"></ol>
            </div>
        </div>`;
}

function sessionMeta(s) {
  const meta = [t('web.home.started', { when: timeAgo(s.created_at, t) })];
  if (cluster && s.node) meta.push(s.node);
  if (s.launch_context) meta.push(s.launch_context.value);
  return meta.join(' · ');
}

function sessionCard(s) {
  const badges = [];
  if (s.is_collaboration) badges.push(`<span class="badge"><i class="fas fa-users"></i>${escapeHtml(t('web.home.room'))}</span>`);
  if (s.gpu) badges.push(`<span class="badge"><i class="fas fa-microchip"></i>${escapeHtml(t('common.gpu'))}</span>`);
  if (s.home) badges.push(`<span class="badge" title="${escapeHtml(s.home)}"><i class="fas fa-hdd"></i>${escapeHtml(s.home)}</span>`);
  const meta = sessionMeta(s);
  const shot = shots.get(s.session_id);
  return `
        <div class="session-tile${shot ? ' has-shot' : ''}" data-key="${escapeHtml(s.session_id)}" data-session-id="${escapeHtml(s.session_id)}">
            <button type="button" class="shot" data-action="open" title="${escapeHtml(t('common.open'))}" aria-label="${escapeHtml(`${t('common.open')}: ${s.app_name}`)}">
                <img class="preview"${shot ? ` src="${shot.image}"` : ''} alt="">
                <img class="logo" data-logo-src="${escapeHtml(s.app_logo)}" src="icons/icon128.png" alt="">
                <span class="play"><i class="fas fa-play"></i></span>
            </button>
            <div class="session-foot">
                <div class="session-info">
                    <div class="name">${escapeHtml(s.app_name)}</div>
                    <div class="meta" title="${escapeHtml(meta)}">${escapeHtml(meta)}</div>
                    ${badges.length ? `<div class="badges">${badges.join('')}</div>` : ''}
                </div>
                <div class="session-actions">
                    <button type="button" class="primary" data-action="open">${escapeHtml(t('common.open'))}</button>
                    <button type="button" class="secondary" data-action="stop" title="${escapeHtml(t('common.stop'))}"><i class="fas fa-stop"></i></button>
                </div>
            </div>
        </div>`;
}

/**
 * Draw the launches under way and the running sessions as cards, keeping
 * the cards already there: a card redrawn would lose its picture for a
 * moment and rise in again, so a known one only has its words brought up
 * to date, and a card is moved only when its place changed.
 */
function renderSessions() {
  const cards = [
    ...[...flights.values()].map((flight) => [`flight:${flight.id}`, flight, null]),
    ...sessions.map((s) => [s.session_id, null, s]),
  ];
  const kept = new Map([...sessionsRow.children].map((tile) => [tile.dataset.key, tile]));
  const template = document.createElement('template');
  cards.forEach(([key, flight, session], index) => {
    let tile = kept.get(key);
    if (tile) {
      kept.delete(key);
      if (session) {
        const meta = tile.querySelector('.session-info .meta');
        meta.textContent = sessionMeta(session);
        meta.title = meta.textContent;
      }
    } else {
      template.innerHTML = (flight ? launchingCard(flight) : sessionCard(session)).trim();
      tile = template.content.firstElementChild;
      hydrateLogos(tile);
    }
    if (sessionsRow.children[index] !== tile) sessionsRow.insertBefore(tile, sessionsRow.children[index] || null);
    if (flight && flight.view) showFlight(flight);
  });
  kept.forEach((tile) => tile.remove());
  $('sessions-section').hidden = cards.length === 0;
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
  // Hidden applications show under their own chip alone, or when searched for by name.
  if (activeType === 'hidden') list = list.filter(isHidden);
  else if (!term) list = list.filter((app) => !isHidden(app));
  if (activeType === 'recent') list = list.filter((app) => memory.recent.includes(app.id));
  else if (activeType !== 'all' && activeType !== 'hidden') list = list.filter((app) => (app.type || '') === activeType);
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
  if (apps.some(isHidden)) chips.push(['hidden', t('web.home.typeHidden')]);
  if (!chips.some(([value]) => value === activeType)) activeType = 'all';
  $('type-chips').innerHTML = chips.length > 1
    ? chips.map(([value, label]) => `<button type="button" class="secondary${value === activeType ? ' active' : ''}" data-type="${escapeHtml(value)}">${escapeHtml(label)}</button>`).join('')
    : '';
}

const busyFlights = () => new Map([...flights.values()].map((flight) => [flight.app.id, flight]));

/** An application's tile: a click opens its launch options, the play button in its corner launches at once. */
function tileHtml(app, flight) {
  return `
        <div class="app-tile${flight ? ' busy' : ''}${isFavorite(app) ? ' favorite' : ''}" role="button" tabindex="0" data-app="${escapeHtml(app.id)}" title="${escapeHtml(t('web.home.options'))}">
            <img data-logo-src="${escapeHtml(app.logo)}" src="icons/icon128.png" alt="" draggable="false">
            <span class="name">${escapeHtml(app.name)}</span>
            <span class="stage">${flight ? `<span class="inline-spinner"></span>${escapeHtml(stageText(flight))}` : ''}</span>
            <button type="button" class="primary quick" title="${escapeHtml(t('web.home.quickLaunch'))}" aria-label="${escapeHtml(`${t('web.home.quickLaunch')}: ${app.name}`)}"><i class="fas fa-play"></i></button>
        </div>`;
}

function renderFavorites() {
  const list = favoriteApps();
  const busy = busyFlights();
  $('favorites-grid').innerHTML = list.map((app) => tileHtml(app, busy.get(app.id))).join('');
  hydrateLogos($('favorites-grid'));
  $('favorites-section').hidden = list.length === 0;
}

function renderApps() {
  const list = visibleApps();
  const busy = busyFlights();
  appGrid.innerHTML = list.map((app) => tileHtml(app, busy.get(app.id))).join('');
  hydrateLogos(appGrid);
  renderFavorites();
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
  flight.view.show(flight.stage, flight.detail, text);
  document.querySelectorAll(`.app-tile[data-app="${CSS.escape(flight.app.id)}"] .stage`).forEach((stage) => {
    stage.innerHTML = `<span class="inline-spinner"></span>${escapeHtml(text)}`;
  });
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

const isSheet = () => matchMedia('(max-width: 700px)').matches;

/** Center the launch window; on a narrow window it is a sheet, which the stylesheet places. */
function placeDrawer() {
  if (isSheet()) {
    drawer.style.cssText = '';
    return;
  }
  const left = Math.max(12, (innerWidth - drawer.offsetWidth) / 2);
  const top = Math.max(12, (innerHeight - drawer.offsetHeight) / 2);
  drawer.style.cssText = `left: ${left}px; top: ${top}px;`;
}

/**
 * The transform that puts the launch window where its tile is, at the
 * tile's size, so it can unfold from there and fold back.
 *
 * @returns {string|null} None without a tile, or as a sheet.
 */
function foldedTransform() {
  if (!anchorTile || isSheet() || reducedMotion()) return null;
  const tile = anchorTile.getBoundingClientRect();
  const win = drawer.getBoundingClientRect();
  const dx = tile.left + tile.width / 2 - (win.left + win.width / 2);
  const dy = tile.top + tile.height / 2 - (win.top + win.height / 2);
  return `translate(${dx}px, ${dy}px) scale(${tile.width / win.width}, ${tile.height / win.height})`;
}

/** Show the launch panel's favorite and hidden state for its application. */
function showPlacement(app) {
  const favorite = isFavorite(app);
  $('favorite-btn').title = t(favorite ? 'web.home.removeFavorite' : 'web.home.addFavorite');
  $('favorite-btn').classList.toggle('active', favorite);
  $('favorite-btn').querySelector('i').className = favorite ? 'fas fa-star' : 'far fa-star';
  const hidden = isHidden(app);
  $('hide-btn').title = t(hidden ? 'web.home.showApp' : 'web.home.hideApp');
  $('hide-btn').querySelector('i').className = hidden ? 'fas fa-eye' : 'fas fa-eye-slash';
  const order = $('favorite-order');
  order.hidden = !favorite || layout.favorites.length < 2;
  const at = layout.favorites.indexOf(app.id);
  order.querySelector('[data-move="-1"]').disabled = at <= 0;
  order.querySelector('[data-move="1"]').disabled = at >= layout.favorites.length - 1;
}

function openDrawer(app, anchor = null) {
  // Opened again while folding away, the window stays and takes the new application.
  if (closing) {
    closing.cancel();
    closing = null;
  }
  drawerApp = app;
  anchorTile = anchor;
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

  showPlacement(app);
  drawer.hidden = false;
  $('drawer-backdrop').hidden = false;
  placeDrawer();
  // The window unfolds from its tile, as a desktop opens an application.
  const folded = foldedTransform();
  if (folded) {
    drawer.animate(
      [{ transform: folded, opacity: 0.3 }, { transform: 'none', opacity: 1 }],
      { duration: WINDOW_MS, easing: ZOOM_EASING },
    );
  }
  $('launch-btn').focus({ preventScroll: true });
}

/** Close the launch window, folding it back into its tile when it came from one. */
function closeDrawer() {
  if (drawer.hidden || closing) return;
  const folded = foldedTransform();
  const app = drawerApp;
  drawerApp = null;
  anchorTile = null;
  const done = () => {
    drawer.hidden = true;
    $('drawer-backdrop').hidden = true;
    closing = null;
  };
  if (!folded || !app) {
    done();
    return;
  }
  closing = drawer.animate(
    [{ transform: 'none', opacity: 1 }, { transform: folded, opacity: 0 }],
    { duration: WINDOW_MS * 0.8, easing: 'cubic-bezier(0.4, 0, 1, 1)', fill: 'forwards' },
  );
  closing.finished.catch(() => {}).then(() => {
    closing.cancel();
    done();
  });
}

/** Star or unstar the panel's application; a new favorite goes last. */
async function toggleFavorite() {
  const app = drawerApp;
  layout.favorites = isFavorite(app) ? layout.favorites.filter((id) => id !== app.id) : [...layout.favorites, app.id];
  showPlacement(app);
  renderApps();
  await saveLayout();
}

/** Hide the panel's application from the grid, or show it again. */
async function toggleHidden() {
  const app = drawerApp;
  layout.hidden = isHidden(app) ? layout.hidden.filter((id) => id !== app.id) : [...layout.hidden, app.id];
  closeDrawer();
  renderTypeChips();
  renderApps();
  await saveLayout();
}

/** Move the panel's application among the favorites by `step` places. */
async function moveFavorite(step) {
  const app = drawerApp;
  const at = layout.favorites.indexOf(app.id);
  const to = at + step;
  if (at < 0 || to < 0 || to >= layout.favorites.length) return;
  const order = [...layout.favorites];
  order.splice(at, 1);
  order.splice(to, 0, app.id);
  layout.favorites = order;
  showPlacement(app);
  renderFavorites();
  await saveLayout();
}

// --- Favorites: drag to reorder --------------------------------------------------

/**
 * Pick a favorite's tile up with a mouse or pen and drop it among the
 * others: a copy follows the pointer, the tile itself takes its new place
 * as the pointer crosses the others, which slide aside, and the order is
 * kept on the drop. Touch scrolls the page instead; the panel's arrows
 * reorder there.
 */
function bindFavoriteDrag() {
  const grid = $('favorites-grid');
  let drag = null;

  const slide = (tiles, before) => {
    tiles.forEach((tile) => {
      const was = before.get(tile);
      const now = tile.getBoundingClientRect();
      const dx = was.left - now.left;
      const dy = was.top - now.top;
      if (dx || dy) tile.animate([{ transform: `translate(${dx}px, ${dy}px)` }, { transform: 'none' }], { duration: 200, easing: ZOOM_EASING });
    });
  };

  grid.addEventListener('pointerdown', (event) => {
    const tile = event.target.closest('.app-tile');
    if (!tile || event.button !== 0 || event.pointerType === 'touch' || event.target.closest('.quick')) return;
    // A selection left on the page would be what the browser drags instead of the tile.
    const selection = window.getSelection();
    if (selection && !selection.isCollapsed) selection.removeAllRanges();
    drag = { tile, x: event.clientX, y: event.clientY, ghost: null, id: event.pointerId };
  });
  grid.addEventListener('dragstart', (event) => event.preventDefault());

  grid.addEventListener('pointermove', (event) => {
    if (!drag || event.pointerId !== drag.id) return;
    if (!drag.ghost) {
      if (Math.hypot(event.clientX - drag.x, event.clientY - drag.y) < DRAG_THRESHOLD) return;
      const rect = drag.tile.getBoundingClientRect();
      drag.ghost = drag.tile.cloneNode(true);
      drag.ghost.className = 'app-tile drag-ghost';
      drag.ghost.style.cssText = `left: ${rect.left}px; top: ${rect.top}px; width: ${rect.width}px; height: ${rect.height}px;`;
      drag.offset = { x: event.clientX - rect.left, y: event.clientY - rect.top };
      document.body.append(drag.ghost);
      drag.tile.classList.add('drag-source');
      grid.classList.add('reordering');
      grid.setPointerCapture(event.pointerId);
    }
    drag.ghost.style.left = `${event.clientX - drag.offset.x}px`;
    drag.ghost.style.top = `${event.clientY - drag.offset.y}px`;
    const over = document.elementsFromPoint(event.clientX, event.clientY).find((el) => el.classList && el.classList.contains('app-tile') && el !== drag.ghost && el !== drag.tile && grid.contains(el));
    if (!over) return;
    const rect = over.getBoundingClientRect();
    const after = event.clientX > rect.left + rect.width / 2;
    const target = after ? over.nextElementSibling : over;
    if (target === drag.tile || (after && over.nextElementSibling === drag.tile)) return;
    const others = [...grid.children].filter((el) => el !== drag.tile);
    const before = new Map(others.map((el) => [el, el.getBoundingClientRect()]));
    grid.insertBefore(drag.tile, target);
    slide(others, before);
  });

  const drop = async (event) => {
    if (!drag || event.pointerId !== drag.id) return;
    const { tile, ghost } = drag;
    drag = null;
    if (!ghost) return;
    grid.classList.remove('reordering');
    const rect = tile.getBoundingClientRect();
    await ghost.animate(
      [{ left: ghost.style.left, top: ghost.style.top }, { left: `${rect.left}px`, top: `${rect.top}px` }],
      { duration: 160, easing: ZOOM_EASING, fill: 'forwards' },
    ).finished.catch(() => {});
    ghost.remove();
    tile.classList.remove('drag-source');
    layout.favorites = [...grid.children].map((el) => el.dataset.app);
    await saveLayout();
  };
  grid.addEventListener('pointerup', drop);
  grid.addEventListener('pointercancel', drop);
  // A click that ended a drag is not a launch.
  grid.addEventListener('click', (event) => { if (event.target.closest('.drag-source')) event.stopImmediatePropagation(); }, true);
}

// --- Background --------------------------------------------------------------

/**
 * A background as kept, whatever layout it was kept by: the first layouts
 * had named gradients and a picture by address, which become a color style
 * and nothing. A picture is only ever one the user uploaded.
 *
 * @param {object} kept
 */
function normalizeBackground(kept = {}) {
  const bg = { ...DEFAULT_BACKGROUND };
  if (kept.kind === 'gradient' && LEGACY_GRADIENTS[kept.value]) Object.assign(bg, { kind: 'color', color: LEGACY_GRADIENTS[kept.value], style: 'diagonal' });
  else if (kept.kind === 'color') Object.assign(bg, { kind: 'color', color: kept.color || kept.value, style: kept.style });
  else if (kept.kind === 'image') Object.assign(bg, { kind: 'image', image: kept.image || kept.value, fit: kept.fit });
  if (!/^#[0-9a-f]{6}$/i.test(bg.color || '')) bg.color = DEFAULT_BACKGROUND.color;
  if (!STYLES.includes(bg.style)) bg.style = DEFAULT_BACKGROUND.style;
  if (!FITS.includes(bg.fit)) bg.fit = DEFAULT_BACKGROUND.fit;
  if (!/^data:image\//.test(bg.image || '')) bg.image = '';
  if (bg.kind === 'image' && !bg.image) bg.kind = 'default';
  bg.blur = Math.min(24, Math.max(0, Number(kept.blur) || 0));
  bg.dim = Math.min(90, Math.max(0, Number.isFinite(Number(kept.dim)) ? Number(kept.dim) : DEFAULT_BACKGROUND.dim));
  return bg;
}

/** Paint the chosen background behind the page, and show the choice in the panel. */
function applyBackground() {
  const bg = layout.background;
  const backdrop = $('backdrop');
  const paint = bg.kind === 'color' ? `paint style-${bg.style}` : bg.kind === 'image' ? `picture fit-${bg.fit}` : '';
  backdrop.className = paint;
  backdrop.style.setProperty('--bg-color', bg.color);
  backdrop.style.backgroundImage = bg.kind === 'image' ? `url("${bg.image}")` : '';
  backdrop.style.setProperty('--bg-blur', `${bg.kind === 'image' ? bg.blur : 0}px`);
  backdrop.style.setProperty('--bg-dim', String(bg.kind === 'default' ? 0 : bg.dim / 100));
  document.body.classList.toggle('has-backdrop', bg.kind !== 'default');
  document.body.classList.toggle('has-picture', bg.kind === 'image');

  // The panel opens on the color tab; with the default background nothing on it is marked chosen.
  const tab = bg.kind === 'image' ? 'image' : 'color';
  $('bg-kinds').querySelectorAll('button').forEach((button) => button.classList.toggle('active', button.dataset.kind === tab));
  $('bg-color-group').hidden = tab !== 'color';
  $('bg-image-group').hidden = tab !== 'image';
  $('bg-dim-group').hidden = bg.kind === 'default';
  const preset = SWATCHES.includes(bg.color.toLowerCase());
  $('bg-swatches').querySelectorAll('.swatch').forEach((swatch) => {
    swatch.classList.toggle('active', bg.kind === 'color' && (swatch.dataset.color ? swatch.dataset.color === bg.color.toLowerCase() : !preset));
  });
  $('bg-color').value = bg.color;
  $('bg-styles').style.setProperty('--bg-color', bg.color);
  $('bg-styles').querySelectorAll('.bg-choice').forEach((choice) => choice.classList.toggle('active', bg.kind === 'color' && choice.dataset.style === bg.style));
  const preview = $('bg-preview');
  preview.className = `bg-preview${bg.image ? ` picture fit-${bg.fit}` : ''}`;
  preview.style.backgroundImage = bg.image ? `url("${bg.image}")` : '';
  $('bg-fits').querySelectorAll('button').forEach((button) => button.classList.toggle('active', button.dataset.fit === bg.fit));
  $('bg-blur').value = bg.blur;
  $('bg-blur-value').value = `${bg.blur}px`;
  $('bg-dim').value = bg.dim;
  $('bg-dim-value').value = `${bg.dim}%`;
}

async function setBackground(change) {
  layout.background = normalizeBackground({ ...layout.background, ...change });
  applyBackground();
  await saveLayout();
}

/**
 * Scale a picked picture to fit `BG_MAX_EDGE` and encode it small enough
 * for browser storage, or throw.
 *
 * @param {File} file
 * @returns {Promise<string>} A data URL.
 */
async function pictureData(file) {
  const bitmap = await createImageBitmap(file);
  const scale = Math.min(1, BG_MAX_EDGE / Math.max(bitmap.width, bitmap.height));
  const canvas = document.createElement('canvas');
  canvas.width = Math.round(bitmap.width * scale);
  canvas.height = Math.round(bitmap.height * scale);
  canvas.getContext('2d').drawImage(bitmap, 0, 0, canvas.width, canvas.height);
  bitmap.close();
  for (const quality of [0.85, 0.7, 0.55, 0.4]) {
    const data = canvas.toDataURL('image/jpeg', quality);
    if (data.length <= BG_MAX_BYTES) return data;
  }
  throw new Error(t('web.home.pictureTooLarge'));
}

/** Show one of the panel's tabs without changing the background. */
function showTab(tab) {
  $('bg-kinds').querySelectorAll('button').forEach((button) => button.classList.toggle('active', button.dataset.kind === tab));
  $('bg-color-group').hidden = tab !== 'color';
  $('bg-image-group').hidden = tab !== 'image';
}

function openCustomize() {
  applyBackground();
  $('customize').hidden = false;
  $('customize-backdrop').hidden = false;
}

function closeCustomize() {
  $('customize').hidden = true;
  $('customize-backdrop').hidden = true;
}

function bindCustomize() {
  $('customize-button').addEventListener('click', openCustomize);
  $('customize-close').addEventListener('click', closeCustomize);
  $('customize-backdrop').addEventListener('click', closeCustomize);
  const swatches = $('bg-swatches');
  swatches.append(...SWATCHES.map((color) => {
    const swatch = document.createElement('button');
    swatch.type = 'button';
    swatch.className = 'swatch';
    swatch.dataset.color = color;
    swatch.style.setProperty('--swatch', color);
    return swatch;
  }));
  $('bg-kinds').addEventListener('click', (event) => {
    const button = event.target.closest('button[data-kind]');
    if (!button) return;
    // The tabs show what can be chosen; the background changes with a choice on them, or at once for a picture already uploaded.
    if (button.dataset.kind === 'image' && !layout.background.image) $('bg-file').click();
    else if (button.dataset.kind === 'image') setBackground({ kind: 'image' });
    else if (layout.background.kind === 'image') showTab('color');
  });
  swatches.addEventListener('click', (event) => {
    const swatch = event.target.closest('button.swatch');
    if (swatch) setBackground({ kind: 'color', color: swatch.dataset.color });
  });
  $('bg-color').addEventListener('input', (event) => setBackground({ kind: 'color', color: event.target.value }));
  $('bg-styles').addEventListener('click', (event) => {
    const choice = event.target.closest('button[data-style]');
    if (choice) setBackground({ kind: 'color', style: choice.dataset.style });
  });
  $('bg-pick').addEventListener('click', () => $('bg-file').click());
  $('bg-fits').addEventListener('click', (event) => {
    const button = event.target.closest('button[data-fit]');
    if (button) setBackground({ fit: button.dataset.fit });
  });
  $('bg-file').addEventListener('change', async (event) => {
    const file = event.target.files[0];
    event.target.value = '';
    if (!file) return;
    try {
      await setBackground({ kind: 'image', image: await pictureData(file) });
    } catch (error) {
      notify(error.message, true);
    }
  });
  $('bg-blur').addEventListener('input', (event) => setBackground({ blur: Number(event.target.value) }));
  $('bg-dim').addEventListener('input', (event) => setBackground({ dim: Number(event.target.value) }));
  $('customize-reset').addEventListener('click', () => setBackground({ ...DEFAULT_BACKGROUND }));
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

  const flight = { id, app, stage: 'requesting', detail: {}, view: createLaunchView(t, partsOf(id)) };
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
    flight.view.tick();
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
    flight.view.finish();
    memory.apps[app.id] = options;
    memory.recent = [app.id, ...memory.recent.filter((recent) => recent !== app.id)].slice(0, RECENT_MAX);
    await saveMemory();
    // Opened in this tab, the session takes the web app's place now, growing out of its card.
    const here = !options.newTab && opensHere(status.session_isolation);
    if (here) await zoomInto(sessionsRow.querySelector(`[data-flight="${id}"]`));
    try {
      await request('openSession', { sessionId: data.session_id, sessionUrl: data.session_url, launchId: id });
    } catch (error) {
      if (here) unzoom();
      throw error;
    }
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

/**
 * Whether a session opens in this tab, which is when the page is the web
 * app's and the session is on its origin: the shells and a session on an
 * origin of its own open a tab or an app of their own.
 *
 * @param {boolean} ownOrigin
 */
function opensHere(ownOrigin) {
  return info.shell === 'web' && !ownOrigin;
}

let ghost = null;

/**
 * Grow a copy of a card's desktop from where the card is to the whole page,
 * and ask the host to fold its frame away meanwhile, so the session the tab
 * then goes to appears to open out of the card. Resolves when the copy has
 * the page; at once with reduced motion.
 *
 * @param {Element|null} tile The session's card.
 */
async function zoomInto(tile) {
  // A launch's card has no picture; the whole card grows, with the logo on it.
  const shot = tile && (tile.querySelector('.shot') || tile);
  if (!shot || reducedMotion()) return;
  if (ghost) ghost.remove();
  const from = shot.getBoundingClientRect();
  ghost = document.createElement('div');
  ghost.className = 'zoom-ghost';
  const preview = shot.querySelector('.preview');
  const logo = shot.querySelector('.logo');
  // The desktop when there is one, the application's logo on the card's backdrop otherwise.
  if (preview && preview.src && tile.classList.contains('has-shot')) ghost.append(Object.assign(new Image(), { src: preview.src }));
  else if (logo) ghost.append(Object.assign(new Image(), { src: logo.src, className: 'logo' }));
  document.body.append(ghost);
  document.body.classList.add('zooming');
  bridge.leaving(true);
  const start = { top: `${from.top}px`, left: `${from.left}px`, width: `${from.width}px`, height: `${from.height}px`, borderRadius: getComputedStyle(tile).borderRadius };
  const end = { top: '0px', left: '0px', width: '100%', height: '100%', borderRadius: '0px' };
  Object.assign(ghost.style, start);
  await ghost.animate([start, end], { duration: ZOOM_MS, easing: ZOOM_EASING, fill: 'forwards' }).finished.catch(() => {});
}

/** Take the grown copy away again: the session did not open after all. */
function unzoom() {
  if (ghost) ghost.remove();
  ghost = null;
  document.body.classList.remove('zooming');
  bridge.leaving(false);
}

async function openSession(session, tile) {
  const here = opensHere(session.own_origin);
  if (here) await zoomInto(tile);
  try {
    await bridge.focusSession(session);
  } catch (error) {
    if (here) unzoom();
    notify(shellError(error), true);
  }
}

const reducedMotion = () => matchMedia('(prefers-reduced-motion: reduce)').matches;

/**
 * Take a card out of the row: it shrinks away, and the cards after it
 * slide into its place rather than jump.
 *
 * @param {Element} tile
 */
async function vanish(tile) {
  tile.classList.add('stopping');
  if (!reducedMotion()) {
    await tile.animate(
      [{ transform: 'scale(1)', opacity: 1 }, { transform: 'scale(0.6)', opacity: 0 }],
      { duration: VANISH_MS, easing: 'cubic-bezier(0.4, 0, 1, 1)', fill: 'forwards' },
    ).finished.catch(() => {});
  }
  const others = [...sessionsRow.children].filter((other) => other !== tile);
  const before = new Map(others.map((other) => [other, other.getBoundingClientRect()]));
  tile.remove();
  $('sessions-section').hidden = sessionsRow.children.length === 0;
  if (reducedMotion()) return;
  others.forEach((other) => {
    const was = before.get(other);
    const now = other.getBoundingClientRect();
    const dx = was.left - now.left;
    const dy = was.top - now.top;
    if (dx || dy) other.animate([{ transform: `translate(${dx}px, ${dy}px)` }, { transform: 'none' }], { duration: SLIDE_MS, easing: ZOOM_EASING });
  });
}

/** Stop a session: its stop button spins while the server stops it, and its card goes once it has. */
async function stopSession(sessionId, tile, button) {
  stopping.add(sessionId);
  button.disabled = true;
  button.innerHTML = '<i class="fas fa-spinner fa-spin"></i>';
  try {
    await bridge.closeSession(sessionId);
    await vanish(tile);
    sessions = sessions.filter((s) => s.session_id !== sessionId);
    shots.delete(sessionId);
  } catch (error) {
    notify(t('popup.status.errorClosingSession', { message: error.message }), true);
    button.disabled = false;
    button.innerHTML = '<i class="fas fa-stop"></i>';
  }
  stopping.delete(sessionId);
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
  // A tile opens its launch window; the play button in its corner launches at once. Enter opens, Shift+Enter launches.
  for (const grid of [appGrid, $('favorites-grid')]) {
    grid.addEventListener('click', (event) => {
      const app = tileApp(event);
      if (!app) return;
      if (event.target.closest('.quick')) launch(app, optionsFor(app));
      else openDrawer(app, event.target.closest('.app-tile'));
    });
    grid.addEventListener('keydown', (event) => {
      if ((event.key !== 'Enter' && event.key !== ' ') || event.target.closest('.quick')) return;
      const app = tileApp(event);
      if (!app) return;
      event.preventDefault();
      if (event.shiftKey) launch(app, optionsFor(app));
      else openDrawer(app, event.target.closest('.app-tile'));
    });
  }
  bindFavoriteDrag();
  bindCustomize();

  sessionsRow.addEventListener('click', (event) => {
    const button = event.target.closest('button[data-action]');
    if (!button || ghost) return;
    const tile = button.closest('.session-tile');
    const { sessionId } = tile.dataset;
    if (stopping.has(sessionId)) return;
    if (button.dataset.action === 'stop') stopSession(sessionId, tile, button);
    else openSession(sessions.find((s) => s.session_id === sessionId), tile);
  });

  $('drawer-close').addEventListener('click', closeDrawer);
  $('drawer-backdrop').addEventListener('click', closeDrawer);
  $('favorite-btn').addEventListener('click', toggleFavorite);
  $('hide-btn').addEventListener('click', toggleHidden);
  $('favorite-order').addEventListener('click', (event) => {
    const button = event.target.closest('button[data-move]');
    if (button) moveFavorite(Number(button.dataset.move));
  });
  window.addEventListener('resize', () => { if (!drawer.hidden) placeDrawer(); });
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') {
      if (!drawer.hidden) closeDrawer();
      if (!$('customize').hidden) closeCustomize();
    }
    // Slash reaches the search box from anywhere on the page.
    if (event.key === '/' && !event.target.closest('input, select, textarea') && drawer.hidden) {
      event.preventDefault();
      heroInput.focus();
    }
  });
  $('launch-btn').addEventListener('click', () => {
    const app = drawerApp;
    const options = drawerOptions();
    // The window folds back into the tile, which then shows the launch.
    anchorTile = null;
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
  // Landing on the page again, by any road, is when the desktops must be current.
  const landed = () => {
    lastActive = Date.now();
    refreshSessions().then(() => refreshShots({ fresh: true }));
  };
  // Back from a session, the page may come out of the back-forward cache as it was left: grown over, and stale.
  window.addEventListener('pageshow', (event) => {
    if (!event.persisted) return;
    if (ghost) unzoom();
    landed();
  });
  document.addEventListener('visibilitychange', () => { if (!document.hidden) landed(); });
  // Using the page keeps the desktops refreshing; coming back to it after a while is a landing.
  const active = () => {
    const away = !looking();
    lastActive = Date.now();
    if (away && !document.hidden) refreshShots({ fresh: true });
  };
  ['pointermove', 'pointerdown', 'keydown', 'touchstart', 'wheel', 'focus'].forEach((type) => window.addEventListener(type, active, { passive: true }));
  setInterval(() => { if (!document.hidden) refreshSessions(); }, SESSIONS_REFRESH_MS);
  setInterval(() => { if (looking()) refreshShots(); }, SHOTS_REFRESH_MS);
}

async function init() {
  info = await announce();
  t = await loadTranslator(info.locale);
  applyTranslations(document.body, t);
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
    const [pending, stored] = await Promise.all([bridge.getContext(), bridge.storageGet(['simple_launch_profile', LAYOUT_KEY]), loadAll()]);
    const saved = stored.simple_launch_profile || {};
    memory = { apps: saved.apps || {}, recent: saved.recent || [] };
    const kept = stored[LAYOUT_KEY] || {};
    layout = {
      favorites: (kept.favorites || []).filter((id) => apps.some((app) => app.id === id)),
      hidden: (kept.hidden || []).filter((id) => apps.some((app) => app.id === id)),
      background: normalizeBackground(kept.background),
    };
    applyBackground();
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
  refreshShots({ fresh: true });
  heroInput.focus();
}

init();
