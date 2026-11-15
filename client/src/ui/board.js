/**
 * The home page's dashboard: one grid of tiles between the search box and
 * the page's end, the tabs that switch between dashboards, and the edit
 * mode in which the user arranges them.
 *
 * A dashboard comes from the server (`/api/dashboard`) or is a page of the
 * user's own; `board-model.js` lays the user's changes over the former.
 * Every tile has its place on a grid twelve cells wide. The catalog of
 * applications is a tile as well, a band the width of the page that grows
 * with what it holds: the grid is drawn in stretches above and below it.
 * Left off a page, the catalog shows last while the user searches. The
 * running sessions are no part of the dashboard: the page shows them above
 * it, under the search box. A row that holds headings alone is only as
 * high as their text. A
 * window too narrow for twelve cells shows the tiles in reading order on
 * fewer, where they are not rearranged.
 *
 * Out of edit mode the page is a launcher and nothing moves. In edit mode a
 * tile is dragged to any cell, resized by its corner, edited, or removed,
 * and whatever it lands on moves down to make room; the page is exported,
 * imported, filled from a browser's bookmarks file, tidied, or reset to the
 * dashboard as the administrator made it. Every change is kept at once, in
 * the browser. A locked dashboard, or a user whose settings withhold it,
 * has no edit mode.
 *
 * A tile's icon is a Font Awesome class, a name from an icon pack, which
 * the server fetches, a picture the user uploads, kept in the browser, or
 * a site's own icon, which the server reads. The look of the tiles (their
 * card, labels, density, the page's width, and an accent color) is the
 * dashboard's until the user picks their own.
 *
 * An administrator's edit mode can also work on the shared dashboard itself
 * (`board-admin.js`), which is then saved to the server for everyone it
 * names.
 */

import { downloadBlob, escapeHtml, hydrateLogos, apiUrl } from '../lib/dom.js';
import { confirmDialog } from '../lib/modal.js';
import {
  BANDS, COLUMNS, FA_ICON, HEADING_SCALES, ICON_DATA_MAX, LOOK, MAX_H, PACK_ICON,
  firstFit, insideAddress, newId, opmlFeeds, ownBoard, ownTile, parseBookmarks, placed, readLook, reflow, resolveBoard, settle, snapshot, tidy, typedLink,
} from '../lib/board-model.js';
import { WIDGET_ICONS, drawWidget } from '../lib/widget-view.js';

const WIDGETS_REFRESH_MS = 60000;
const DRAG_THRESHOLD = 6;
const EASING = 'cubic-bezier(0.22, 1, 0.36, 1)';
const IMPORT_MAX_BYTES = 2000000;
const BOOKMARKS_MAX = 300;
const ICON_EDGE = 128;
// The user's own widgets that the page draws without asking the server.
const LOCAL_TYPES = ['clock', 'notes'];
const DEFAULT_LOOK = { style: 'solid', labels: true, density: 'normal', width: 'normal' };
const CELL = { compact: 88, normal: 104, roomy: 124 };
// The height of a row that holds headings alone, by the size of their text.
const HEADING_ROW = { s: 30, m: 42, l: 56 };
// Rows of empty grid kept under the tiles in edit mode, to drop onto.
const SPARE_ROWS = 2;

const $ = (id) => document.getElementById(id);
const browserZone = () => {
  try { return Intl.DateTimeFormat().resolvedOptions().timeZone || ''; } catch (error) { return ''; }
};
const copy = (value) => JSON.parse(JSON.stringify(value));

/** The accent palette of the stylesheets, mixed from one color as the server mixes the brand's (`branding.accent_css`). */
function accentVars(color) {
  const rgb = [1, 3, 5].map((at) => parseInt(color.slice(at, at + 2), 16));
  const mix = (toward, amount) => `#${rgb.map((channel, index) => Math.round(channel + (toward[index] - channel) * amount).toString(16).padStart(2, '0')).join('')}`;
  const linear = rgb.map((channel) => (channel / 255 <= 0.03928 ? channel / 255 / 12.92 : ((channel / 255 + 0.055) / 1.055) ** 2.4));
  const light = 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2] > 0.45;
  const white = [255, 255, 255];
  return {
    '--accent-primary': color,
    '--accent-primary-hover': mix(white, 0.1),
    '--accent-text': mix(white, 0.35),
    '--accent-text-strong': mix(white, 0.5),
    '--accent-contrast': light ? '#0d1117' : '#ffffff',
    '--accent-soft': `rgba(${rgb.join(', ')}, 0.14)`,
    '--accent-border': `rgba(${rgb.join(', ')}, 0.5)`,
    '--accent-glow': `rgba(${rgb.join(', ')}, 0.35)`,
    '--accent-gradient': `linear-gradient(135deg, ${color} 0%, ${mix([0, 0, 0], 0.18)} 100%)`,
  };
}

/**
 * @param {object} host What the home page lends the dashboard.
 * @param {function} host.t Translator.
 * @param {string} host.locale
 * @param {object} host.info The shell's hello.
 * @param {function(): object[]} host.apps The applications the user may launch.
 * @param {function(): object} host.layout The layout kept in the browser.
 * @param {function(): Promise} host.saveLayout
 * @param {function(object, object): string} host.tileHtml An application's tile.
 * @param {function(): Map} host.busyFlights Launches in flight, by application id.
 * @param {function(string, string, object=): void} host.openIsolated Open an address in a session, of the named application or one the user picks, through the VPN profile named.
 * @param {function(): object[]} host.vpns The VPN profiles the user may launch through.
 * @param {function(string, boolean=): void} host.notify
 * @param {function(string, object=): Promise} host.fetch `secureFetch`.
 * @param {function(): boolean} host.looking Whether someone is looking at the page.
 * @param {function(): boolean} host.isAdmin
 * @param {function(string): object|undefined} host.sessionOf The user's running session of an application, if any.
 * @param {function(string): string} host.shotOf The latest picture of a session's desktop, as an address, or nothing.
 * @param {function(object, Element): void} host.openSession Bring a session forward.
 * @param {function(): Promise} host.resetLayout Forget everything the browser keeps for the page.
 * @param {function(): void} host.changed The dashboard changed what the rest of the page shows.
 */
export function createBoard(host) {
  const { t } = host;
  const root = $('board');
  // What the server sent: `{boards, widgets, policy, user_types}`.
  let server = { boards: [], widgets: [], policy: { customize: false, links: false, feeds: false, schemes: ['http', 'https'] }, user_types: [] };
  let active = null;
  let tiles = [];
  let editing = false;
  // Set by the administrator's tools while the shared dashboard itself is being edited.
  let shared = null;
  let admin = null;
  let columns = COLUMNS;
  const answers = new Map();
  // Where the page loads each icon pack name from, as the server answered.
  const packImages = new Map();

  const schemes = () => server.policy.schemes;
  const libraryWidget = (id) => server.widgets.find((widget) => widget.id === id);
  // A kiosk dashboard is all its users see of the page: the others, and pages of their own, stay away.
  const kiosk = () => !shared && server.boards.some((board) => board.kiosk);
  const boards = () => (kiosk() ? server.boards.filter((board) => board.kiosk) : [...server.boards, ...host.layout().own.map((board) => ownBoard(board, schemes())).filter(Boolean)]);
  const canCustomize = () => Boolean(shared || (active && server.policy.customize && !active.locked && !active.kiosk));
  // Edit mode on a page that may be changed; an administrator's edit mode on a locked one only offers the shared side.
  const arranging = () => editing && canCustomize();
  // Tiles are put down where the pointer says only on the grid they were arranged for.
  const placing = () => arranging() && columns === COLUMNS;
  const findTile = (id) => tiles.find((tile) => tile.id === id);
  const bottom = () => tiles.reduce((most, tile) => Math.max(most, tile.y + tile.h), 0);
  /** Where a new tile goes: the first free place above the catalog, or a new row there, the catalog moving down. */
  const freeSpot = (w, h) => {
    const catalog = tiles.find((tile) => tile.kind === 'apps');
    const spot = firstFit(tiles, w, h);
    return catalog && spot.y > catalog.y ? { x: 0, y: catalog.y } : spot;
  };

  // --- Keeping ----------------------------------------------------------------

  /** Keep the page as it stands: the shared dashboard in memory until saved, the user's own in the browser. */
  function persist() {
    if (shared) {
      shared.dirty = true;
      shared.board.tiles = tiles;
      if (admin) admin.show();
      return Promise.resolve();
    }
    const layout = host.layout();
    if (active.own) {
      const at = layout.own.findIndex((board) => board.id === active.id);
      const kept = { id: active.id, name: active.name, tiles };
      if (at >= 0) layout.own[at] = kept;
      else layout.own.push(kept);
    } else {
      layout.boards[active.id] = snapshot(active, tiles);
    }
    return host.saveLayout();
  }

  function selectBoard(id) {
    const all = boards();
    active = all.find((board) => board.id === id) || all[0] || { id: 'default', name: '', tiles: [] };
    host.layout().active = active.id;
    tiles = active.own ? copy(active.tiles) : resolveBoard(active, host.layout().boards[active.id], schemes());
    answers.clear();
  }

  // --- The look ---------------------------------------------------------------

  /** The look in force: the dashboard's, with the user's own choices over it; on the shared side, the dashboard's alone. */
  const look = () => ({ ...DEFAULT_LOOK, ...readLook((shared ? shared.board.look : active && active.look) || {}), ...(shared ? {} : readLook(host.layout().look)) });

  function applyLook() {
    const now = look();
    const body = document.body;
    LOOK.style.forEach((style) => body.classList.toggle(`tiles-${style}`, now.style === style));
    body.classList.toggle('labels-off', !now.labels);
    body.classList.toggle('page-wide', now.width === 'wide');
    root.style.setProperty('--cell', `${CELL[now.density]}px`);
    const style = document.documentElement.style;
    const vars = now.accent ? accentVars(now.accent) : {};
    Object.keys(accentVars('#000000')).forEach((name) => (vars[name] ? style.setProperty(name, vars[name]) : style.removeProperty(name)));
    $('look-style').querySelectorAll('button').forEach((button) => button.classList.toggle('active', button.dataset.value === now.style));
    $('look-density').querySelectorAll('button').forEach((button) => button.classList.toggle('active', button.dataset.value === now.density));
    $('look-width').querySelectorAll('button').forEach((button) => button.classList.toggle('active', button.dataset.value === now.width));
    $('look-labels').checked = now.labels;
    $('look-accent').value = now.accent || getComputedStyle(document.documentElement).getPropertyValue('--accent-primary').trim() || '#8b7cf6';
    $('look-accent-group').hidden = Boolean(shared);
    $('look-note').textContent = t(shared ? 'web.board.lookShared' : 'web.board.lookMine');
  }

  /** Change one choice of the look: the shared dashboard's on its side, the user's own otherwise. */
  function setLook(change) {
    if (shared) {
      shared.board.look = { ...DEFAULT_LOOK, ...shared.board.look, ...readLook({ ...shared.board.look, ...change }) };
      shared.dirty = true;
      if (admin) admin.show();
    } else {
      host.layout().look = readLook({ ...host.layout().look, ...change });
      host.saveLayout();
    }
    render();
  }

  // --- Drawing ----------------------------------------------------------------

  const tools = (buttons) => buttons.map(([act, icon, title]) => `<button type="button" class="tool" data-act="${act}" title="${escapeHtml(title)}" aria-label="${escapeHtml(title)}"><i class="fas ${icon}"></i></button>`).join('');

  /** A tile's icon: an uploaded picture, one the server serves, a pack's by name, or Font Awesome classes. */
  function iconHtml(tile, fallback) {
    const invert = /^mdi-/.test(tile.icon || '') ? ' invert' : '';
    if (tile.iconData) return `<img class="glyph" src="${escapeHtml(tile.iconData)}" alt="" draggable="false">`;
    if (tile.image) return `<img class="glyph${invert}" src="${escapeHtml(apiUrl(tile.image))}" alt="" draggable="false">`;
    if (PACK_ICON.test(tile.icon || '')) {
      const known = packImages.get(tile.icon);
      return `<img class="glyph${invert}" data-pack="${escapeHtml(tile.icon)}" ${known ? `src="${escapeHtml(apiUrl(known))}"` : ''} alt="" draggable="false">`;
    }
    const classes = FA_ICON.test(tile.icon || '') ? (tile.icon.includes(' ') ? tile.icon : `fas ${tile.icon}`) : `fas ${fallback}`;
    return `<i class="glyph ${escapeHtml(classes)}"></i>`;
  }

  /** Ask the server where the pack icons on the page are loaded from, and show them. */
  async function loadPackIcons() {
    const names = [...new Set([...document.querySelectorAll('img[data-pack]:not([src])')].map((image) => image.dataset.pack))];
    if (!names.length) return;
    try {
      const { images } = await host.fetch('/api/dashboard/icons', { method: 'POST', body: JSON.stringify({ names }) });
      Object.entries(images).forEach(([name, address]) => packImages.set(name, address));
      document.querySelectorAll('img[data-pack]:not([src])').forEach((image) => { if (packImages.has(image.dataset.pack)) image.src = apiUrl(packImages.get(image.dataset.pack)); });
    } catch (error) { /* the tiles keep their names without a picture */ }
  }

  const hostOf = (url) => {
    try { return new URL(url).host || url; } catch (error) { return url; }
  };

  function widgetOf(tile) {
    const known = tile.widget ? libraryWidget(tile.widget) : null;
    const type = known ? known.type : tile.type;
    return { type, name: tile.name || (known ? known.name : t(`web.board.types.${type}`)) };
  }

  /** A tile's markup at a place on the grid, or nothing for one that cannot be shown: an application or a widget the user has no access to. */
  function tileHtml(tile, place, base, busy) {
    const at = `data-tile="${escapeHtml(tile.id)}" style="grid-column:${place.x + 1} / span ${place.w};grid-row:${place.y - base + 1} / span ${place.h}"`;
    const shape = `${place.w > 1 && place.h === 1 ? ' wide' : ''}${place.h > 1 ? ' tall' : ''}`;
    const extra = tile.description ? `<span class="detail">${escapeHtml(tile.description)}</span>` : '';
    if (tile.kind === 'app') {
      const app = host.apps().find((candidate) => candidate.id === tile.app);
      if (!app) return arranging() ? `<div class="tile app-tile missing" ${at}><i class="glyph fas fa-question"></i><span class="name">${escapeHtml(tile.name || tile.app)}</span></div>` : '';
      return host.tileHtml(app, busy.get(app.id)).replace('class="app-tile', `${at} class="tile app-tile${shape}`);
    }
    if (tile.kind === 'heading') return `<div class="tile heading-tile scale-${HEADING_SCALES.includes(tile.scale) ? tile.scale : 'm'}" ${at}><span>${escapeHtml(tile.name)}</span></div>`;
    if (tile.kind === 'bundle') {
      return `<button type="button" class="tile app-tile link-tile${shape}" ${at} data-bundle title="${escapeHtml(`${tile.name}\n${tile.urls.join('\n')}`)}">
        ${iconHtml(tile, 'fa-layer-group')}<span class="name">${escapeHtml(tile.name)}</span>${extra}
        <span class="where count">${tile.urls.length}</span></button>`;
    }
    if (tile.kind === 'link') {
      // An administrator's link may show whether its address answers the server.
      const dot = tile.status && !tile.own ? '<span class="reach" data-reach></span>' : '';
      const body = `${iconHtml(tile, 'fa-link')}<span class="name">${escapeHtml(tile.name)}</span>${extra || `<span class="detail">${escapeHtml(hostOf(tile.url))}</span>`}${dot}`;
      const title = escapeHtml(`${tile.name}\n${tile.url}`);
      if (tile.open === 'isolated') {
        return `<button type="button" class="tile app-tile link-tile${shape}" ${at} data-isolated title="${title}\n${escapeHtml(t(insideAddress(tile.url) ? 'web.board.opensInside' : 'web.board.opensSession'))}">${body}<i class="fas ${insideAddress(tile.url) ? 'fa-network-wired' : 'fa-shield-alt'} where"></i></button>`;
      }
      const web = /^https?:/i.test(tile.url);
      // The page's own tab is the web app's; a shell has none to give, and another scheme leaves the page where it is.
      const target = !web ? '' : (tile.open === 'same' && host.info.shell === 'web' ? ' target="_top"' : ' target="_blank"');
      return `<a class="tile app-tile link-tile${shape}" ${at} href="${escapeHtml(tile.url)}"${target} rel="noopener noreferrer" title="${title}">${body}<i class="fas ${web ? (tile.open === 'same' ? 'fa-arrow-right' : 'fa-external-link-alt') : 'fa-bolt'} where"></i></a>`;
    }
    const { type, name } = widgetOf(tile);
    if (!type) return '';
    return `<div class="tile widget-tile type-${escapeHtml(type)}${shape}" ${at} data-widget="${escapeHtml(type)}">
      <header><i class="fas ${WIDGET_ICONS[type] || 'fa-puzzle-piece'}"></i><span>${escapeHtml(name)}</span></header>
      <div class="widget-body"><span class="inline-spinner"></span></div></div>`;
  }

  function tileTools(tile) {
    return `<span class="tile-tools">${tools([
      // A dashboard's own tile is the administrator's to change; a user moves it, sizes it, or takes it off.
      ...(tile.kind === 'app' || (!shared && !tile.own) ? [] : [['edit-tile', 'fa-pen', t('common.edit')]]),
      ['remove-tile', 'fa-times', t('common.remove')],
    ])}</span>${placing() ? `<span class="tile-resize" title="${escapeHtml(t('web.board.resize'))}"><i class="fas fa-expand-alt"></i></span>` : ''}`;
  }

  function renderTabs() {
    const all = boards();
    const tabs = $('board-tabs');
    tabs.hidden = all.length < 2 && !(editing && server.policy.customize && !kiosk());
    tabs.innerHTML = all.map((board) => `
      <button type="button" class="board-tab${board.id === active.id ? ' active' : ''}" data-board="${escapeHtml(board.id)}">
        ${board.icon ? `<i class="${escapeHtml(board.icon.includes(' ') ? board.icon : `fas ${board.icon}`)}"></i>` : ''}<span>${escapeHtml(board.name || t('common.home'))}</span>
        ${board.locked ? '<i class="fas fa-lock lock"></i>' : ''}</button>`).join('')
      + (editing && server.policy.customize && !shared && !kiosk() ? `<button type="button" class="board-tab add" data-act="new-page"><i class="fas fa-plus"></i><span>${escapeHtml(t('web.board.newPage'))}</span></button>` : '');
  }

  /** The grid's width in cells for the room there is: the full twelve, or fewer in a narrow window. */
  const columnsFor = (width) => (width >= 900 ? COLUMNS : width >= 560 ? 8 : 4);

  /** One stretch of the grid: the tiles between two bands, from row `base`. */
  function gridNode(base, members, places, busy) {
    const rows = members.reduce((most, tile) => Math.max(most, places.get(tile.id).y + places.get(tile.id).h - base), 0);
    const shown = members.map((tile) => [tile, tileHtml(tile, places.get(tile.id), base, busy)]).filter(([, html]) => html);
    if (!shown.length && !arranging()) return null;
    const grid = document.createElement('div');
    grid.className = 'tile-grid';
    grid.dataset.base = base;
    grid.dataset.rows = rows + (arranging() ? SPARE_ROWS : 0);
    grid.style.setProperty('--columns', columns);
    // A row every tile on which is a heading is as high as the largest of them; the rest are a cell high.
    const cell = CELL[look().density];
    grid.heights = Array.from({ length: Number(grid.dataset.rows) }, (_, row) => {
      const on = members.filter((tile) => places.get(tile.id).y - base <= row && row < places.get(tile.id).y - base + places.get(tile.id).h);
      return on.length && on.every((tile) => tile.kind === 'heading') ? Math.max(...on.map((tile) => HEADING_ROW[tile.scale] || HEADING_ROW.m)) : cell;
    });
    grid.style.gridTemplateRows = grid.heights.map((height) => `${height}px`).join(' ');
    grid.innerHTML = shown.map(([, html]) => html).join('');
    if (arranging()) {
      shown.forEach(([tile]) => {
        const element = grid.querySelector(`[data-tile="${CSS.escape(tile.id)}"]`);
        if (element) element.insertAdjacentHTML('beforeend', tileTools(tile));
      });
    }
    hydrateLogos(grid);
    return grid;
  }

  /** Draw the dashboard: its tabs, the grid in stretches above and below the catalog, and its widgets. */
  function render() {
    renderTabs();
    applyLook();
    columns = columnsFor(root.clientWidth || 1200);
    document.body.classList.toggle('board-editing', arranging());
    document.body.classList.toggle('board-placing', placing());
    document.body.classList.toggle('kiosk', Boolean(active && active.kiosk) && !shared && !editing);
    root.querySelectorAll('.tile-grid').forEach((node) => node.remove());
    const fixed = { apps: $('apps-section') };
    const busy = host.busyFlights();
    const places = columns === COLUMNS ? new Map(tiles.map((tile) => [tile.id, tile])) : reflow(tiles, columns);
    const ordered = [...tiles].sort((a, b) => places.get(a.id).y - places.get(b.id).y || places.get(a.id).x - places.get(b.id).x);
    Object.values(fixed).forEach((node) => {
      delete node.dataset.tile;
      node.querySelector('.section-tools').innerHTML = '';
    });
    let base = 0;
    let members = [];
    const flush = () => {
      const grid = gridNode(base, members, places, busy);
      if (grid) root.append(grid);
      members = [];
    };
    ordered.forEach((tile) => {
      if (!BANDS.includes(tile.kind)) {
        members.push(tile);
        return;
      }
      flush();
      const node = fixed[tile.kind];
      node.dataset.tile = tile.id;
      if (tile.name) node.querySelector('h2').textContent = tile.name;
      node.querySelector('.section-tools').innerHTML = arranging() ? `${placing() ? `<span class="band-grip tool" title="${escapeHtml(t('web.board.dragBand'))}"><i class="fas fa-grip-lines"></i></span>` : ''}${tools([['remove-tile', 'fa-times', t('common.remove')]])}` : '';
      root.append(node);
      base = places.get(tile.id).y + 1;
    });
    flush();
    // Left off the dashboard, the catalog shows last while it is searched.
    if (!tiles.some((tile) => tile.kind === 'apps')) root.append(fixed.apps);
    fixed.apps.dataset.transient = String(!tiles.some((tile) => tile.kind === 'apps'));
    renderEditBar();
    drawWidgets();
    loadPackIcons();
    live();
    host.changed();
  }

  /**
   * Show the user's running sessions on their applications' tiles: a mark
   * on every tile of an application that has one, and on a tile two rows
   * high or more its desktop, which a click then opens.
   */
  function live() {
    root.querySelectorAll('.tile-grid .app-tile[data-app]').forEach((tile) => {
      const session = host.sessionOf(tile.dataset.app);
      tile.classList.toggle('running', Boolean(session));
      const shot = session && tile.classList.contains('tall') ? host.shotOf(session.session_id) : '';
      tile.classList.toggle('live', Boolean(shot));
      let picture = tile.querySelector('.live-shot');
      if (!shot) {
        if (picture) picture.remove();
        return;
      }
      if (!picture) {
        picture = Object.assign(document.createElement('img'), { className: 'live-shot', alt: '', draggable: false });
        tile.prepend(picture);
      }
      if (picture.src !== shot) picture.src = shot;
    });
  }

  function renderEditBar() {
    $('edit-bar').hidden = !editing;
    $('edit-button').hidden = !(canCustomize() || host.isAdmin());
    $('edit-button').classList.toggle('active', editing);
    if (!editing) return;
    $('edit-title').textContent = shared ? t('web.board.editingShared', { name: shared.board.name })
      : t(canCustomize() ? 'web.board.editingMine' : 'web.board.lockedPage', { name: active.name || t('common.home') });
    const mine = !shared;
    $('edit-menu').querySelectorAll('[data-act="rename-page"], [data-act="delete-page"]').forEach((item) => { item.hidden = !(mine && active.own); });
    $('edit-menu').querySelector('[data-act="reset-page"]').hidden = !(mine && !active.own);
    $('edit-menu').querySelector('[data-act="import-page"]').hidden = !mine;
    $('edit-menu').querySelector('[data-act="reset-all"]').hidden = !mine;
    $('edit-add-tile').disabled = !canCustomize();
    $('edit-more').disabled = !canCustomize();
    $('edit-narrow').hidden = !(arranging() && columns !== COLUMNS);
    if (admin) admin.show();
  }

  // --- Widgets ----------------------------------------------------------------

  const theme = () => {
    const style = getComputedStyle(document.documentElement);
    return Object.fromEntries(['--accent-primary', '--text-primary', '--text-secondary', '--text-muted', '--bg-1', '--bg-2', '--border-color'].map((name) => [name.slice(2), style.getPropertyValue(name).trim()]));
  };

  function drawOne(tile, answer) {
    const body = root.querySelector(`[data-tile="${CSS.escape(tile.id)}"] .widget-body`);
    if (!body) return;
    // A notice outside its time is shown to no one but who arranges the page, and dimmed there.
    const off = answer.ok && answer.data && answer.data.active === false;
    const element = body.closest('.tile');
    element.hidden = off && !arranging();
    element.classList.toggle('off-schedule', off);
    const stamp = JSON.stringify(answer.ok ? answer.data : answer.error);
    if (body.dataset.stamp === stamp) return;
    body.dataset.stamp = stamp;
    drawWidget(body, widgetOf(tile).type, answer, { t, locale: host.locale, theme: theme() });
  }

  function drawNotes(tile) {
    const body = root.querySelector(`[data-tile="${CSS.escape(tile.id)}"] .widget-body`);
    if (!body || body.querySelector('textarea')) return;
    body.innerHTML = `<textarea class="w-notes" maxlength="4000" placeholder="${escapeHtml(t('web.board.notesPlaceholder'))}"></textarea>`;
    const area = body.firstElementChild;
    area.value = (tile.config && tile.config.text) || '';
    let timer = null;
    area.addEventListener('input', () => {
      tile.config = { ...tile.config, text: area.value };
      clearTimeout(timer);
      timer = setTimeout(persist, 600);
    });
  }

  /** Draw every widget on the page: the local ones at once, the others from the last answers and then from the server's. */
  async function drawWidgets(fresh = false) {
    const asked = [];
    tiles.forEach((tile) => {
      if (tile.kind !== 'widget') return;
      if (!tile.widget && tile.type === 'notes') drawNotes(tile);
      else if (!tile.widget && LOCAL_TYPES.includes(tile.type)) drawOne(tile, { ok: true, data: tile.config || {} });
      else {
        if (answers.has(tile.id)) drawOne(tile, answers.get(tile.id));
        asked.push(tile);
      }
    });
    const watched = tiles.filter((tile) => tile.kind === 'link' && tile.status && !tile.own);
    if (!asked.length && !watched.length) return;
    const board = shared ? shared.board.id : active.id;
    const items = [
      ...asked.map((tile) => (tile.widget ? { key: tile.id, widget: tile.widget } : { key: tile.id, type: tile.type, config: tile.config || {} })),
      ...watched.map((tile) => ({ key: `reach:${tile.id}`, link: tile.id, board })),
    ];
    try {
      const { results } = await host.fetch('/api/dashboard/data', { method: 'POST', body: JSON.stringify({ items, fresh }) });
      watched.forEach((tile) => {
        const answer = results[`reach:${tile.id}`];
        const dot = root.querySelector(`[data-tile="${CSS.escape(tile.id)}"] [data-reach]`);
        if (!dot || !answer || !answer.ok) return;
        dot.className = `reach ${answer.data.up ? 'up' : 'down'}`;
        dot.title = answer.data.up ? t('web.board.reachUp', { ms: answer.data.ms }) : t('web.board.reachDown');
      });
      asked.forEach((tile) => {
        const answer = results[tile.id];
        if (!answer) return;
        answers.set(tile.id, answer);
        drawOne(tile, answer);
      });
    } catch (error) { /* the next round asks again */ }
  }

  // --- Changing ---------------------------------------------------------------

  function changed() {
    render();
    return persist();
  }

  /** A centered window over the page: a form with a title and the buttons given. */
  function openWindow({ title, icon = 'fa-pen', body, confirm = t('common.save'), onConfirm, wide = false }) {
    closeWindow();
    const backdrop = document.createElement('div');
    backdrop.id = 'board-window-backdrop';
    const win = document.createElement('aside');
    win.id = 'board-window';
    win.className = `drawer window${wide ? ' wide' : ''}`;
    win.setAttribute('role', 'dialog');
    win.setAttribute('aria-modal', 'true');
    win.innerHTML = `
      <header class="drawer-head"><i class="fas ${icon} drawer-icon"></i><div><h3>${escapeHtml(title)}</h3></div>
        <button type="button" class="secondary icon-only" data-role="close" title="${escapeHtml(t('common.close'))}"><i class="fas fa-times"></i></button></header>
      <form class="drawer-body" novalidate>${body}<p class="form-error" hidden></p></form>
      <footer class="drawer-foot"><button type="button" class="secondary" data-role="close">${escapeHtml(t('common.cancel'))}</button>
        ${onConfirm ? `<button type="button" class="primary" data-role="confirm">${escapeHtml(confirm)}</button>` : ''}</footer>`;
    const fail = (message) => {
      const line = win.querySelector('.form-error');
      line.textContent = message;
      line.hidden = !message;
    };
    const submit = async () => {
      fail('');
      try {
        if ((await onConfirm(win, fail)) !== false) closeWindow();
      } catch (error) {
        fail(error.message);
      }
    };
    win.addEventListener('click', (event) => {
      if (event.target.closest('[data-role="close"]')) closeWindow();
      else if (event.target.closest('[data-role="confirm"]')) submit();
    });
    win.querySelector('form').addEventListener('submit', (event) => {
      event.preventDefault();
      if (onConfirm) submit();
    });
    backdrop.addEventListener('click', closeWindow);
    document.body.append(backdrop, win);
    const first = win.querySelector('.drawer-body input:not([type="hidden"]):not([type="file"]), .drawer-body select, .drawer-body textarea');
    if (first) first.focus();
    return win;
  }

  function closeWindow() {
    ['board-window', 'board-window-backdrop'].forEach((id) => { if ($(id)) $(id).remove(); });
  }

  const field = (label, control, hint = '') => `<div class="form-group"><label>${escapeHtml(label)}</label>${control}${hint ? `<small class="field-hint">${escapeHtml(hint)}</small>` : ''}</div>`;
  const input = (name, value = '', attrs = '') => `<input type="text" name="${name}" value="${escapeHtml(value)}" autocomplete="off" spellcheck="false" ${attrs}>`;
  const select = (name, choices, value) => `<select name="${name}">${choices.map(([choice, label]) => `<option value="${escapeHtml(choice)}"${String(choice) === String(value) ? ' selected' : ''}>${escapeHtml(label)}</option>`).join('')}</select>`;

  function askName(title, value, onName) {
    openWindow({
      title,
      body: field(t('common.name'), input('name', value, 'maxlength="60"')),
      onConfirm: (win, fail) => {
        const name = win.querySelector('[name="name"]').value.trim();
        if (!name) return fail(t('web.board.needName')) || false;
        return onName(name);
      },
    });
  }

  // --- Icons --------------------------------------------------------------------

  /** A picked picture as a small data URL, the size a tile shows it at. */
  async function pictureIcon(source) {
    const bitmap = await createImageBitmap(source);
    const scale = Math.min(1, ICON_EDGE / Math.max(bitmap.width, bitmap.height));
    const canvas = document.createElement('canvas');
    canvas.width = Math.max(1, Math.round(bitmap.width * scale));
    canvas.height = Math.max(1, Math.round(bitmap.height * scale));
    canvas.getContext('2d').drawImage(bitmap, 0, 0, canvas.width, canvas.height);
    bitmap.close();
    const data = [canvas.toDataURL('image/png'), canvas.toDataURL('image/webp', 0.85), canvas.toDataURL('image/jpeg', 0.8)].find((candidate) => candidate.length <= ICON_DATA_MAX && /^data:image\/(png|webp|jpeg)/.test(candidate));
    if (!data) throw new Error(t('web.board.iconTooLarge'));
    return data;
  }

  /** The icon part of a tile's form: what it shows now, a name to type, a picture to upload, and the site's own icon. */
  function iconField(current, withSite) {
    return `<div class="form-group icon-field"><label>${escapeHtml(t('web.board.icon'))}</label>
      <div class="icon-row"><span class="icon-preview">${iconHtml(current, 'fa-link')}</span>
        ${input('icon', current.iconData ? '' : (current.icon || ''), 'placeholder="fa-book, di-plex, si-github"')}
        <button type="button" class="secondary" data-role="icon-upload" title="${escapeHtml(t('web.board.iconUpload'))}"><i class="fas fa-upload"></i></button>
        ${withSite ? `<button type="button" class="secondary" data-role="icon-site" title="${escapeHtml(t('web.board.iconSite'))}"><i class="fas fa-magic"></i></button>` : ''}
        <button type="button" class="secondary" data-role="icon-clear" title="${escapeHtml(t('web.board.iconClear'))}"><i class="fas fa-times"></i></button></div>
      <input type="hidden" name="iconData" value="${escapeHtml(current.iconData || '')}"><input type="file" name="iconFile" accept="image/png,image/jpeg,image/webp,image/gif,image/x-icon" hidden>
      <small class="field-hint">${escapeHtml(t(shared ? 'web.board.iconHintAdmin' : 'web.board.iconHint'))}</small></div>`;
  }

  /** Take a picture as the form's icon: kept on the server for a shared dashboard, in the form for the user's own tile. */
  async function takeIcon(win, data) {
    const name = win.querySelector('[name="icon"]');
    const kept = win.querySelector('[name="iconData"]');
    if (shared) {
      const saved = await host.fetch('/api/admin/dashboards/icons', { method: 'POST', body: JSON.stringify({ data }) });
      name.value = saved.icon;
      kept.value = '';
      win.querySelector('.icon-preview').innerHTML = `<img class="glyph" src="${escapeHtml(apiUrl(saved.image))}" alt="">`;
      win.dataset.image = saved.image;
    } else {
      name.value = '';
      kept.value = data;
      win.querySelector('.icon-preview').innerHTML = `<img class="glyph" src="${escapeHtml(data)}" alt="">`;
    }
  }

  function bindIconField(win) {
    win.addEventListener('click', async (event) => {
      const role = (event.target.closest('[data-role]') || { dataset: {} }).dataset.role;
      try {
        if (role === 'icon-upload') win.querySelector('[name="iconFile"]').click();
        else if (role === 'icon-clear') {
          win.querySelector('[name="icon"]').value = '';
          win.querySelector('[name="iconData"]').value = '';
          win.querySelector('.icon-preview').innerHTML = iconHtml({}, 'fa-link');
        } else if (role === 'icon-site') {
          const url = typedLink(win.querySelector('[name="url"]').value, schemes(), true);
          if (!url) throw new Error(t('web.board.badWebLink'));
          const { data } = await host.fetch('/api/dashboard/site-icon', { method: 'POST', body: JSON.stringify({ url }) });
          await takeIcon(win, await pictureIcon(await (await fetch(data)).blob()));
        }
      } catch (error) {
        host.notify(error.message, true);
      }
    });
    win.addEventListener('change', async (event) => {
      if (!event.target.matches('[name="iconFile"]') || !event.target.files[0]) return;
      try {
        await takeIcon(win, await pictureIcon(event.target.files[0]));
      } catch (error) {
        host.notify(error.message || t('web.board.iconTooLarge'), true);
      }
      event.target.value = '';
    });
    win.addEventListener('input', (event) => {
      if (!event.target.matches('[name="icon"]')) return;
      win.querySelector('[name="iconData"]').value = '';
      win.querySelector('.icon-preview').innerHTML = iconHtml({ icon: event.target.value.trim() }, 'fa-link');
      loadPackIcons();
    });
  }

  // --- A tile's form --------------------------------------------------------------

  /** The widget types a tile may be given here: the library's, and the user's own on a page that is theirs. */
  function widgetChoices() {
    const library = server.widgets.map((widget) => [`lib:${widget.id}`, widget.name]);
    const own = shared ? [] : server.user_types.filter((type) => server.policy.feeds || !['feed', 'calendar'].includes(type)).map((type) => [`own:${type}`, t(`web.board.types.${type}`)]);
    return { library, own };
  }

  function widgetFields(type, config = {}) {
    if (type === 'clock') {
      // A new clock starts in the browser's own time zone.
      return field(t('web.board.timezone'), input('timezone', config.timezone === undefined ? browserZone() : config.timezone, 'list="timezones" placeholder="Europe/Berlin"'), t('web.board.timezoneHint'))
        + field(t('web.board.label'), input('label', config.label || '', 'maxlength="60"'))
        + `<div class="form-group"><label><input type="checkbox" name="seconds"${config.seconds ? ' checked' : ''}> ${escapeHtml(t('web.board.showSeconds'))}</label></div>`;
    }
    if (type === 'weather') {
      return field(t('web.board.place'), `<div class="inline-row">${input('place', config.label || '', `placeholder="${escapeHtml(t('web.board.placePlaceholder'))}"`)}<button type="button" class="secondary" data-role="find-place">${escapeHtml(t('common.search'))}</button></div>
          <select name="found" hidden></select><input type="hidden" name="latitude" value="${escapeHtml(config.latitude ?? '')}"><input type="hidden" name="longitude" value="${escapeHtml(config.longitude ?? '')}">`)
        + field(t('web.board.units'), select('units', [['metric', t('web.board.metric')], ['imperial', t('web.board.imperial')]], config.units || 'metric'));
    }
    if (type === 'stocks') return field(t('web.board.symbols'), input('symbols', (config.symbols || []).join(', '), 'placeholder="AAPL, MSFT, ^GSPC, BTC-USD"'), t('web.board.symbolsHint'));
    if (type === 'feed' || type === 'calendar') {
      return field(t(type === 'feed' ? 'web.board.feedUrls' : 'web.board.calendarUrls'), `<textarea name="urls" rows="3" spellcheck="false" placeholder="https://">${escapeHtml((config.urls || []).join('\n'))}</textarea>`, t(type === 'feed' ? 'web.board.onePerLine' : 'web.board.calendarHint'))
        + (type === 'feed' ? `<button type="button" class="secondary small" data-role="opml"><i class="fas fa-file-import"></i> ${escapeHtml(t('web.board.importOpml'))}</button>` : '');
    }
    return '';
  }

  /** Read a widget form back into its configuration, or throw with what is missing. */
  function readWidgetFields(win, type) {
    const value = (name) => (win.querySelector(`[name="${name}"]`) ? win.querySelector(`[name="${name}"]`).value.trim() : '');
    if (type === 'clock') return { timezone: value('timezone'), label: value('label'), seconds: win.querySelector('[name="seconds"]').checked };
    if (type === 'weather') {
      if (!value('latitude') || !value('longitude')) throw new Error(t('web.board.needPlace'));
      return { latitude: Number(value('latitude')), longitude: Number(value('longitude')), label: value('place'), units: value('units') };
    }
    if (type === 'stocks') {
      const symbols = value('symbols').split(/[\s,]+/).map((symbol) => symbol.toUpperCase()).filter(Boolean);
      if (!symbols.length || !symbols.every((symbol) => /^[A-Z0-9.^=-]{1,15}$/.test(symbol))) throw new Error(t('web.board.symbolsHint'));
      return { symbols: symbols.slice(0, 12) };
    }
    if (type === 'feed' || type === 'calendar') {
      // A calendar program hands out its address as `webcal://`, which is the `https` one.
      const urls = value('urls').split('\n').map((url) => typedLink(url.trim().replace(/^webcals?:\/\//i, 'https://'), schemes(), true)).filter(Boolean);
      if (!urls.length) throw new Error(t('web.board.needUrl'));
      return { urls };
    }
    return {};
  }

  /** Look a place up for a weather widget's form, and offer what was found. */
  async function findPlace(win) {
    const found = win.querySelector('[name="found"]');
    try {
      const { places } = await host.fetch('/api/dashboard/places', { method: 'POST', body: JSON.stringify({ name: win.querySelector('[name="place"]').value }) });
      if (!places.length) throw new Error(t('web.board.noPlace'));
      found.innerHTML = places.map((place, index) => `<option value="${index}">${escapeHtml(place.label)}</option>`).join('');
      found.hidden = false;
      const take = () => {
        const place = places[Number(found.value)];
        win.querySelector('[name="latitude"]').value = place.latitude;
        win.querySelector('[name="longitude"]').value = place.longitude;
        win.querySelector('[name="place"]').value = place.label;
      };
      found.onchange = take;
      take();
    } catch (error) {
      host.notify(error.message, true);
    }
  }

  /** Fill a feed form's addresses from an OPML file, the list of subscriptions a feed reader exports. */
  async function takeOpml(win) {
    const file = await pickFile('.opml,.xml,text/xml,text/x-opml');
    const feeds = file ? opmlFeeds(file.text) : [];
    if (!feeds.length) {
      host.notify(t('web.board.noFeeds'), true);
      return;
    }
    const area = win.querySelector('[name="urls"]');
    const merged = [...new Set([...area.value.split('\n').map((line) => line.trim()).filter(Boolean), ...feeds])];
    area.value = merged.slice(0, 10).join('\n');
    host.notify(t(merged.length > 10 ? 'web.board.feedsCapped' : 'web.board.feedsTaken', { count: Math.min(10, merged.length), found: feeds.length }));
  }

  /**
   * The window that adds a tile to the page, or edits one.
   *
   * @param {object} [tile] The tile to edit; left out, a new one is made and put in the first free place.
   */
  function tileWindow(tile = null) {
    const kinds = ['app', ...(shared || server.policy.links ? ['link', 'bundle'] : []), 'widget', 'heading'];
    let kind = tile ? tile.kind : kinds[0];
    const apps = host.apps();
    const choices = widgetChoices();
    const draw = (win) => {
      const holder = win.querySelector('.kind-fields');
      const current = tile && tile.kind === kind ? tile : {};
      const numbers = (most) => Array.from({ length: most }, (_, index) => [index + 1, String(index + 1)]);
      const base = kind === 'widget' ? [3, 2] : kind === 'heading' ? [COLUMNS, 1] : [1, 1];
      const ownSize = { clock: [2, 1], notes: [3, 2], weather: [3, 3], stocks: [3, 2], feed: [4, 3], calendar: [3, 3] };
      const size = `<div class="inline-row size-row">${select('w', numbers(COLUMNS), current.w || base[0])}<span>×</span>${select('h', numbers(MAX_H), current.h || base[1])}</div>`;
      let html = '';
      if (kind === 'app') {
        html = field(t('web.board.kinds.app'), select('app', apps.map((app) => [app.id, app.name]), current.app));
      } else if (kind === 'link') {
        html = field(t('common.name'), input('name', current.name || '', 'maxlength="80"'))
          + field(t('web.board.address'), input('url', current.url || '', 'placeholder="https://"'), t('web.board.schemesHint', { schemes: schemes().join(', ') }))
          + field(t('web.board.opens'), select('open', [['tab', t('web.board.openTab')], ['same', t('web.board.openSame')], ['isolated', t('web.board.openIsolated')]], current.open || 'tab'))
          + '<p class="field-hint inside-hint" hidden></p>'
          + `<div class="isolated-only">${field(t('web.board.openIn'), select('app', [['', t('web.board.appDefault')], ...apps.filter((app) => app.url_support).map((app) => [app.id, app.name])], current.app || ''))}
            ${host.vpns().length || current.vpn ? field(t('web.board.leavesThrough'), select('vpn', [['', t('web.board.noVpn')], ...host.vpns().flatMap((vpn) => (vpn.endpoints.length > 1 ? vpn.endpoints.map((endpoint) => [`${vpn.id}/${endpoint.id}`, `${vpn.name}: ${endpoint.label}`]) : [[`${vpn.id}/`, vpn.name]]))], current.vpn ? `${current.vpn}/${host.vpns().some((vpn) => vpn.id === current.vpn && vpn.endpoints.length > 1) ? (current.vpn_endpoint || '') : ''}` : ''), t('web.board.leavesThroughHint')) : ''}</div>`
          + (shared ? `<div class="form-group"><label><input type="checkbox" name="status"${current.status ? ' checked' : ''}> ${escapeHtml(t('web.board.showStatus'))}</label><small class="field-hint">${escapeHtml(t('web.board.showStatusHint'))}</small></div>` : '')
          + iconField(current, true)
          + field(t('web.board.description'), input('description', current.description || '', 'maxlength="200"'));
      } else if (kind === 'bundle') {
        html = field(t('common.name'), input('name', current.name || '', 'maxlength="80"'))
          + field(t('web.board.bundleUrls'), `<textarea name="urls" rows="4" spellcheck="false" placeholder="https://">${escapeHtml((current.urls || []).join('\n'))}</textarea>`, t('web.board.bundleHint'))
          + iconField(current, false);
      } else if (kind === 'widget') {
        const chosen = current.widget ? `lib:${current.widget}` : current.type ? `own:${current.type}` : ((choices.library[0] || choices.own[0] || [''])[0]);
        const groups = [[t('web.board.fromAdmin'), choices.library], [t('web.board.yourOwn'), choices.own]].filter(([, list]) => list.length);
        html = !groups.length ? `<p class="grid-empty">${escapeHtml(t('web.board.noWidgets'))}</p>`
          : field(t('web.board.kinds.widget'), `<select name="widget">${groups.map(([label, list]) => `<optgroup label="${escapeHtml(label)}">${list.map(([value, name]) => `<option value="${escapeHtml(value)}"${value === chosen ? ' selected' : ''}>${escapeHtml(name)}</option>`).join('')}</optgroup>`).join('')}</select>`)
            + field(t('web.board.title'), input('name', current.name || '', 'maxlength="80"'), t('web.board.titleHint'))
            + '<div class="widget-fields"></div>';
      } else {
        html = field(t('web.board.headingText'), input('name', current.name || '', 'maxlength="80"'))
          + field(t('web.board.headingSize'), select('scale', HEADING_SCALES.map((scale) => [scale, t(`web.board.scales.${scale}`)]), current.scale || 'm'), t('web.board.headingHint'));
      }
      holder.innerHTML = html + field(t('web.board.size'), size, t('web.board.sizeHint'));
      const sync = () => {
        const open = win.querySelector('[name="open"]');
        if (open) {
          win.querySelector('.isolated-only').hidden = open.value !== 'isolated';
          const address = typedLink(win.querySelector('[name="url"]').value, schemes(), true);
          const inside = Boolean(address && insideAddress(address));
          const hint = win.querySelector('.inside-hint');
          hint.hidden = !inside;
          hint.textContent = inside ? t(open.value === 'isolated' ? 'web.board.insideSession' : 'web.board.insideTab') : '';
        }
        const picked = win.querySelector('[name="widget"]');
        if (picked) {
          const type = picked.value.startsWith('own:') ? picked.value.slice(4) : '';
          win.querySelector('.widget-fields').innerHTML = widgetFields(type, current.type === type ? current.config : {});
        }
      };
      // A new tile of a library widget starts at the size its administrator gave it.
      const sized = () => {
        const picked = win.querySelector('[name="widget"]');
        const known = picked && (libraryWidget(picked.value.slice(4)) || (ownSize[picked.value.slice(4)] && { w: ownSize[picked.value.slice(4)][0], h: ownSize[picked.value.slice(4)][1] }));
        if (known && !current.w) {
          win.querySelector('[name="w"]').value = known.w;
          win.querySelector('[name="h"]').value = known.h;
        }
      };
      holder.onchange = (event) => {
        // An internal address typed into a new link opens in a session unless the way it opens was chosen already.
        if (event.target.matches('[name="url"]')) {
          const address = typedLink(event.target.value, schemes(), true);
          const open = win.querySelector('[name="open"]');
          if (address && insideAddress(address) && !current.url && !open.dataset.chosen) open.value = 'isolated';
          sync();
        }
        if (event.target.matches('[name="open"]')) event.target.dataset.chosen = '1';
        if (event.target.matches('[name="open"], [name="widget"]')) sync();
        if (event.target.matches('[name="widget"]')) sized();
      };
      sync();
      sized();
      loadPackIcons();
    };
    const win = openWindow({
      title: t(tile ? 'web.board.editTile' : 'web.board.addTile'),
      icon: tile ? 'fa-pen' : 'fa-plus',
      body: `${tile ? '' : `<div class="form-group"><div class="seg kinds">${kinds.map((name) => `<button type="button" data-kind="${name}">${escapeHtml(t(`web.board.kinds.${name}`))}</button>`).join('')}</div></div>`}
        <div class="kind-fields"></div><datalist id="timezones">${(Intl.supportedValuesOf ? Intl.supportedValuesOf('timeZone') : []).map((zone) => `<option value="${zone}">`).join('')}</datalist>`,
      confirm: t(tile ? 'common.save' : 'web.board.add'),
      onConfirm: (form) => {
        const value = (name) => (form.querySelector(`[name="${name}"]`) ? form.querySelector(`[name="${name}"]`).value.trim() : '');
        const made = { id: tile ? tile.id : newId(shared ? 't' : 'u'), kind, name: value('name'), icon: value('icon'), iconData: value('iconData'), description: value('description') };
        const size = { w: Number(value('w')) || 1, h: Number(value('h')) || 1 };
        Object.assign(made, size, tile ? { x: tile.x, y: tile.y } : freeSpot(size.w, size.h));
        if (kind === 'app') made.app = value('app');
        if (kind === 'heading') made.scale = value('scale');
        if (kind === 'link') {
          made.open = value('open');
          made.app = made.open === 'isolated' ? value('app') : '';
          [made.vpn, made.vpn_endpoint] = made.open === 'isolated' && value('vpn') ? value('vpn').split('/') : ['', ''];
          if (shared) made.status = Boolean(form.querySelector('[name="status"]') && form.querySelector('[name="status"]').checked);
          made.url = typedLink(value('url'), schemes(), made.open !== 'tab');
          if (!made.url) throw new Error(t(made.open === 'tab' ? 'web.board.badLink' : 'web.board.badWebLink', { schemes: schemes().join(', ') }));
        }
        if (kind === 'bundle') {
          const lines = value('urls').split('\n').map((line) => line.trim()).filter(Boolean);
          made.urls = lines.map((line) => typedLink(line, schemes(), true));
          if (!made.urls.length || made.urls.includes(null)) throw new Error(t('web.board.badWebLink'));
        }
        if (kind === 'widget') {
          const picked = value('widget');
          if (!picked) throw new Error(t('web.board.noWidgets'));
          if (picked.startsWith('lib:')) made.widget = picked.slice(4);
          else Object.assign(made, { type: picked.slice(4), config: { ...(tile && tile.type === picked.slice(4) ? tile.config : {}), ...readWidgetFields(form, picked.slice(4)) } });
        }
        if (['link', 'bundle', 'heading'].includes(kind) && !made.name) throw new Error(t('web.board.needName'));
        if (made.icon && !(FA_ICON.test(made.icon) || PACK_ICON.test(made.icon) || (shared && /^(icon:[0-9a-f]{16}|https:\/\/\S+)$/.test(made.icon)))) throw new Error(t(shared ? 'web.board.iconHintAdmin' : 'web.board.iconHint'));
        // A shared dashboard's tile is the server's to check; its picture shows from where the upload was kept until the save.
        const image = form.dataset.image && made.icon.startsWith('icon:') ? form.dataset.image : (tile && tile.icon === made.icon ? tile.image : '');
        const kept = shared ? placed({ ...made, iconData: undefined, image }) : ownTile(made, schemes());
        if (!kept) throw new Error(t('web.board.badTile'));
        const at = tiles.findIndex((candidate) => candidate.id === kept.id);
        if (at >= 0) tiles[at] = kept;
        else tiles.push(kept);
        settle(tiles, kept);
        answers.delete(kept.id);
        changed();
      },
    });
    win.addEventListener('click', (event) => {
      const button = event.target.closest('[data-kind]');
      if (button) {
        kind = button.dataset.kind;
        win.querySelectorAll('[data-kind]').forEach((other) => other.classList.toggle('active', other === button));
        draw(win);
      } else if (event.target.closest('[data-role="find-place"]')) findPlace(win);
      else if (event.target.closest('[data-role="opml"]')) takeOpml(win);
    });
    // Enter in the place box looks the place up; anywhere else it saves the tile.
    win.addEventListener('keydown', (event) => {
      if (event.key !== 'Enter' || !event.target.matches('[name="place"]')) return;
      event.preventDefault();
      event.stopPropagation();
      findPlace(win);
    }, true);
    bindIconField(win);
    const first = win.querySelector(`[data-kind="${kind}"]`);
    if (first) first.classList.add('active');
    draw(win);
  }

  /** Put the catalog back on a page that left it off, under everything else. */
  function addBand(kind) {
    if (tiles.some((tile) => tile.kind === kind)) return;
    const band = placed({ id: kind, kind, name: '', own: !shared, y: bottom() });
    tiles.push(band);
    settle(tiles, band);
    changed();
  }

  // --- Favorites --------------------------------------------------------------

  const favoriteOf = (app) => tiles.find((tile) => tile.kind === 'app' && tile.app === app.id && tile.favorite);

  /** Star or unstar an application: a tile of it in the first free place of the page. */
  function toggleFavorite(app, keep = true) {
    const tile = favoriteOf(app);
    if (tile) tiles = tiles.filter((candidate) => candidate !== tile);
    else {
      const favorite = { id: newId('fav'), kind: 'app', app: app.id, own: true, favorite: true, w: 1, h: 1, name: '', icon: '', iconData: '', description: '', ...freeSpot(1, 1) };
      tiles.push(favorite);
      settle(tiles, favorite);
    }
    return keep ? changed() : null;
  }

  // --- Export, import, reset ---------------------------------------------------

  function exportPage() {
    const page = { sealskin_dashboard: 2, name: active.name, tiles: tiles.map((tile) => ({ ...tile, image: undefined })) };
    downloadBlob(new Blob([JSON.stringify(page, null, 2)], { type: 'application/json' }), `${(active.name || 'dashboard').replace(/[^\w.-]+/g, '-')}.json`);
  }

  function pickFile(accept) {
    return new Promise((resolve) => {
      const picker = Object.assign(document.createElement('input'), { type: 'file', accept });
      picker.addEventListener('change', async () => {
        const file = picker.files[0];
        resolve(file && file.size <= IMPORT_MAX_BYTES ? { name: file.name, text: await file.text() } : null);
      });
      picker.click();
    });
  }

  /** Read an exported page in as a page of the user's own, every id made anew. */
  async function importPage() {
    const file = await pickFile('.json,application/json');
    if (!file) return;
    let page;
    try {
      page = JSON.parse(file.text);
    } catch (error) {
      page = null;
    }
    const anew = (tile) => ({ ...tile, id: BANDS.includes(tile.kind) ? tile.kind : newId('u') });
    const made = page && [1, 2].includes(page.sealskin_dashboard) && ownBoard({
      id: newId('p'),
      name: page.name,
      ...(Array.isArray(page.tiles) ? { tiles: page.tiles.map(anew) } : { sections: (page.sections || []).map((section) => ({ ...section, tiles: (section.tiles || []).map(anew) })) }),
    }, schemes());
    if (!made || !made.tiles.length) {
      host.notify(t('web.board.importFailed'), true);
      return;
    }
    host.layout().own.push(made);
    selectBoard(made.id);
    render();
    await host.saveLayout();
    host.notify(t('web.board.imported', { name: made.name }));
  }

  /**
   * Put links on the page as tiles under a heading per group, above the catalog.
   *
   * @param {Map<string, object[]>} groups Links (`{name, url, icon, image, description}`) by the heading they go under.
   * @param {string} mode `tab`, `isolated`, or `auto`: internal addresses in a session, the rest in a tab.
   */
  function placeLinks(groups, mode) {
    const catalog = tiles.find((tile) => tile.kind === 'apps');
    const start = catalog ? catalog.y : bottom();
    const made = [];
    let row = start;
    const mine = !shared;
    const id = () => newId(mine ? 'u' : 't');
    groups.forEach((list, name) => {
      if (name) made.push({ id: id(), kind: 'heading', own: mine, name, scale: 'm', x: 0, y: row++, w: COLUMNS, h: 1, icon: '', description: '' });
      list.forEach((link, index) => made.push({
        id: id(), kind: 'link', own: mine, name: link.name, url: link.url, app: '', iconData: '', icon: link.icon || '', image: link.image || '', description: link.description || '',
        open: mode === 'auto' ? (insideAddress(link.url) ? 'isolated' : 'tab') : mode,
        x: index % COLUMNS, y: row + Math.floor(index / COLUMNS), w: 1, h: 1,
      }));
      row += Math.ceil(list.length / COLUMNS);
    });
    tiles.filter((tile) => tile.y >= start).forEach((tile) => { tile.y += row - start; });
    tiles.push(...made);
    return changed();
  }

  const openModes = () => [['auto', t('web.board.modeAuto')], ['tab', t('web.board.openTab')], ['isolated', t('web.board.openIsolated')]];

  /** Read a browser's bookmarks file in as link tiles under a heading per folder, opened the way the user then picks. */
  async function importBookmarks() {
    const file = await pickFile('.html,.htm,text/html');
    if (!file) return;
    const links = parseBookmarks(file.text, schemes()).slice(0, BOOKMARKS_MAX);
    if (!links.length) {
      host.notify(t('web.board.noBookmarks'), true);
      return;
    }
    const groups = new Map();
    links.forEach((link) => {
      const name = link.folder || t('web.board.bookmarks');
      if (!groups.has(name)) groups.set(name, []);
      groups.get(name).push(link);
    });
    openWindow({
      title: t('web.board.importBookmarks'),
      icon: 'fa-bookmark',
      body: `<p class="field-hint">${escapeHtml(t('web.board.bookmarksFound', { count: links.length, inside: links.filter((link) => insideAddress(link.url)).length }))}</p>${field(t('web.board.opens'), select('mode', openModes(), 'auto'), t('web.board.modeHint'))}`,
      confirm: t('web.board.import'),
      onConfirm: async (win) => {
        await placeLinks(groups, win.querySelector('[name="mode"]').value);
        host.notify(t('web.board.bookmarksImported', { count: links.length }));
      },
    });
  }

  async function resetPage() {
    if (!(await confirmDialog(t, { title: t('web.board.resetPage'), message: t('web.board.resetConfirm'), confirm: t('web.home.reset'), danger: true }))) return;
    delete host.layout().boards[active.id];
    selectBoard(active.id);
    render();
    await host.saveLayout();
  }

  /** Forget every choice the browser keeps for the home page: arrangements, pages of the user's own, the look, the background, hidden applications. */
  async function resetAll() {
    if (!(await confirmDialog(t, { title: t('web.board.resetAll'), message: t('web.board.resetAllConfirm'), confirm: t('web.home.reset'), danger: true }))) return;
    await host.resetLayout();
    selectBoard('');
    editing = false;
    render();
  }

  async function deletePage() {
    if (!(await confirmDialog(t, { title: t('web.board.deletePage'), message: t('web.board.deletePageConfirm', { name: active.name }), confirm: t('common.delete'), danger: true }))) return;
    const layout = host.layout();
    layout.own = layout.own.filter((board) => board.id !== active.id);
    selectBoard('');
    render();
    await host.saveLayout();
  }

  function newPage() {
    askName(t('web.board.newPage'), '', async (name) => {
      const page = { id: newId('p'), name, tiles: [{ id: 'apps', kind: 'apps', y: 2 }] };
      host.layout().own.push(page);
      selectBoard(page.id);
      render();
      await host.saveLayout();
    });
  }

  // --- Opening ----------------------------------------------------------------

  /** Open every link of a bundle, each in a tab that has no handle on this page; a browser may hold all but the first back until pop-ups are allowed. */
  function openBundle(tile) {
    tile.urls.forEach((url) => window.open(url, '_blank', 'noopener'));
    if (tile.urls.length > 1) host.notify(t('web.board.bundleOpened', { count: tile.urls.length }));
  }

  // --- Drag and resize ---------------------------------------------------------

  /** The cells of a stretch of grid in pixels: where it starts, and how far apart its columns and rows are. */
  function metrics(grid) {
    const rect = grid.getBoundingClientRect();
    const style = getComputedStyle(grid);
    const gap = parseFloat(style.columnGap) || 0;
    const cell = parseFloat(style.getPropertyValue('--cell')) || 104;
    // Where each row starts, as rows of headings are shorter than a cell; past the last, rows are a cell high.
    const tops = [];
    let top = 0;
    (grid.heights || []).forEach((height) => {
      tops.push(top);
      top += height + gap;
    });
    const rowAt = (offset) => {
      if (offset >= top) return tops.length + Math.round((offset - top) / (cell + gap));
      let nearest = 0;
      tops.forEach((start, row) => { if (Math.abs(start - offset) < Math.abs(tops[nearest] - offset)) nearest = row; });
      return nearest;
    };
    return { left: rect.left, top: rect.top, stepX: (rect.width + gap) / COLUMNS, stepY: cell + gap, base: Number(grid.dataset.base), rowAt };
  }

  /**
   * In edit mode a tile is picked up and put down on any cell, a band is
   * moved to another row by its grip, and a tile is resized by its corner;
   * a mark shows where it will land, and what is there moves down.
   */
  function bindDrag() {
    let drag = null;
    let sizing = null;
    const mark = Object.assign(document.createElement('div'), { className: 'drop-mark' });

    /** The stretch of grid a point is over, or the nearest above or below it. */
    const gridAt = (y) => {
      const near = (grid) => {
        const rect = grid.getBoundingClientRect();
        return y >= rect.top && y <= rect.bottom ? 0 : Math.min(Math.abs(y - rect.top), Math.abs(y - rect.bottom));
      };
      return [...root.querySelectorAll('.tile-grid')].sort((a, b) => near(a) - near(b))[0];
    };

    /** Show the mark where the dragged tile would land, and remember the place. */
    const aim = (event) => {
      const grid = gridAt(event.clientY);
      if (!grid) return;
      const cells = metrics(grid);
      const { w, h } = drag.model;
      const left = drag.band ? cells.left : event.clientX - drag.offset.x;
      const top = drag.band ? event.clientY : event.clientY - drag.offset.y;
      const x = Math.min(COLUMNS - w, Math.max(0, Math.round((left - cells.left) / cells.stepX)));
      const row = Math.max(0, cells.rowAt(top - cells.top));
      drag.target = { x, y: cells.base + row };
      mark.style.gridColumn = `${x + 1} / span ${w}`;
      mark.style.gridRow = `${row + 1} / span ${h}`;
      if (mark.parentElement !== grid) grid.append(mark);
    };

    root.addEventListener('pointerdown', (event) => {
      if (!placing() || event.button !== 0) return;
      const grip = event.target.closest('.band-grip');
      const element = grip ? grip.closest('[data-tile]') : event.target.closest('.tile-grid > .tile');
      if (!element || event.target.closest('.tile-tools, textarea')) return;
      const model = findTile(element.dataset.tile);
      if (!model) return;
      if (event.target.closest('.tile-resize')) {
        const rect = element.getBoundingClientRect();
        sizing = { element, model, x: event.clientX, y: event.clientY, width: rect.width, height: rect.height, cells: metrics(element.parentElement), id: event.pointerId };
        element.classList.add('resizing');
        root.setPointerCapture(event.pointerId);
        event.preventDefault();
        return;
      }
      const selection = window.getSelection();
      if (selection && !selection.isCollapsed) selection.removeAllRanges();
      drag = { element, model, band: Boolean(grip), x: event.clientX, y: event.clientY, ghost: null, id: event.pointerId };
      if (grip) event.preventDefault();
    });
    root.addEventListener('dragstart', (event) => { if (arranging()) event.preventDefault(); });

    root.addEventListener('pointermove', (event) => {
      if (sizing && event.pointerId === sizing.id) {
        const { model, cells } = sizing;
        const w = Math.min(COLUMNS - model.x, Math.max(1, Math.round((sizing.width + event.clientX - sizing.x) / cells.stepX)));
        const h = Math.min(MAX_H, Math.max(1, Math.round((sizing.height + event.clientY - sizing.y) / cells.stepY)));
        if (w !== model.w || h !== model.h) {
          Object.assign(model, { w, h });
          const grid = sizing.element.parentElement;
          sizing.element.style.gridColumn = `${model.x + 1} / span ${w}`;
          sizing.element.style.gridRow = `${model.y - cells.base + 1} / span ${h}`;
          sizing.element.classList.toggle('wide', w > 1 && h === 1);
          sizing.element.classList.toggle('tall', h > 1);
        }
        return;
      }
      if (!drag || event.pointerId !== drag.id) return;
      if (!drag.ghost) {
        if (Math.hypot(event.clientX - drag.x, event.clientY - drag.y) < DRAG_THRESHOLD) return;
        const rect = drag.element.getBoundingClientRect();
        drag.ghost = drag.band ? Object.assign(document.createElement('div'), { className: 'band-ghost', textContent: drag.element.querySelector('h2').textContent }) : drag.element.cloneNode(true);
        drag.ghost.classList.add('drag-ghost');
        drag.ghost.style.cssText = `left: ${rect.left}px; top: ${drag.band ? event.clientY - 20 : rect.top}px; width: ${rect.width}px; height: ${drag.band ? 40 : rect.height}px;`;
        drag.offset = { x: event.clientX - rect.left, y: drag.band ? 20 : event.clientY - rect.top };
        document.body.append(drag.ghost);
        drag.element.classList.add('drag-source');
        document.body.classList.add('board-dragging');
        root.setPointerCapture(event.pointerId);
      }
      drag.ghost.style.left = `${event.clientX - drag.offset.x}px`;
      drag.ghost.style.top = `${event.clientY - drag.offset.y}px`;
      aim(event);
    });

    const drop = async (event) => {
      if (sizing && event.pointerId === sizing.id) {
        sizing.element.classList.remove('resizing');
        settle(tiles, sizing.model);
        sizing = null;
        await changed();
        return;
      }
      if (!drag || event.pointerId !== drag.id) return;
      const { element, ghost, model, target } = drag;
      drag = null;
      if (!ghost) return;
      document.body.classList.remove('board-dragging');
      element.classList.remove('drag-source');
      if (target) {
        const to = mark.getBoundingClientRect();
        await ghost.animate([{ left: ghost.style.left, top: ghost.style.top }, { left: `${to.left}px`, top: `${to.top}px` }], { duration: 140, easing: EASING, fill: 'forwards' }).finished.catch(() => {});
        Object.assign(model, target);
        settle(tiles, model);
      }
      ghost.remove();
      mark.remove();
      await changed();
    };
    root.addEventListener('pointerup', drop);
    root.addEventListener('pointercancel', drop);
  }

  // --- Events -----------------------------------------------------------------

  async function act(name, node) {
    const element = node.closest('[data-tile]');
    const tile = element ? findTile(element.dataset.tile) : null;
    if (name === 'add-tile') tileWindow();
    else if (name === 'edit-tile' && tile) tileWindow(tile);
    else if (name === 'remove-tile' && tile) {
      tiles = tiles.filter((candidate) => candidate !== tile);
      await changed();
    } else if (name === 'tidy') {
      tidy(tiles);
      await changed();
    } else if (name === 'new-page') newPage();
    else if (name === 'rename-page') {
      askName(t('web.board.renamePage'), active.name, (value) => {
        active.name = value;
        changed();
      });
    } else if (name === 'delete-page') await deletePage();
    else if (name === 'reset-page') await resetPage();
    else if (name === 'reset-all' && !shared) await resetAll();
    else if (name === 'export-page') exportPage();
    else if (name === 'import-page') await importPage();
    else if (name === 'import-bookmarks') await importBookmarks();
    else if (name === 'show-apps') addBand('apps');
  }

  function setEditing(on) {
    if (!on && shared && admin && !admin.leave()) return;
    editing = on;
    $('edit-menu').hidden = true;
    render();
  }

  function bind() {
    // In edit mode nothing on the grid launches or leads anywhere; its tools alone answer.
    root.addEventListener('click', (event) => {
      const tool = event.target.closest('[data-act]');
      if (tool && root.contains(tool)) {
        event.preventDefault();
        event.stopPropagation();
        act(tool.dataset.act, tool);
        return;
      }
      const node = event.target.closest('.tile-grid [data-tile]');
      if (arranging()) {
        if (node && !event.target.closest('textarea')) {
          event.preventDefault();
          event.stopPropagation();
        }
        return;
      }
      const tile = node ? findTile(node.dataset.tile) : null;
      if (!tile) return;
      // A tile that shows its application's running desktop opens that session; its play button still launches.
      if (tile.kind === 'app' && node.classList.contains('live') && !event.target.closest('.quick')) {
        event.preventDefault();
        event.stopPropagation();
        host.openSession(host.sessionOf(tile.app), node);
        return;
      }
      if (tile.kind === 'bundle') openBundle(tile);
      else if (tile.kind === 'link' && tile.open === 'isolated') host.openIsolated(tile.url, tile.app || (active && active.isolated_app) || '', { vpn: tile.vpn || '', vpnEndpoint: tile.vpn_endpoint || '' });
    }, true);
    root.addEventListener('keydown', (event) => {
      if (arranging() && (event.key === 'Enter' || event.key === ' ') && event.target.closest('.tile-grid .tile') && !event.target.closest('textarea, .tool')) {
        event.preventDefault();
        event.stopPropagation();
      }
    }, true);
    $('board-tabs').addEventListener('click', (event) => {
      const tab = event.target.closest('[data-board]');
      if (tab) {
        if (shared && admin && !admin.leave()) return;
        selectBoard(tab.dataset.board);
        render();
        host.saveLayout();
      } else if (event.target.closest('[data-act="new-page"]')) newPage();
    });
    $('edit-button').addEventListener('click', () => setEditing(!editing));
    $('edit-done').addEventListener('click', () => setEditing(false));
    $('edit-add-tile').addEventListener('click', () => tileWindow());
    $('edit-more').addEventListener('click', () => {
      $('edit-menu').querySelector('[data-act="show-apps"]').hidden = tiles.some((tile) => tile.kind === 'apps');
      $('edit-menu').hidden = !$('edit-menu').hidden;
    });
    $('edit-menu').addEventListener('click', (event) => {
      const item = event.target.closest('[data-act]');
      if (!item) return;
      $('edit-menu').hidden = true;
      act(item.dataset.act, item);
    });
    document.addEventListener('click', (event) => { if (!event.target.closest('.menu-wrap')) $('edit-menu').hidden = true; });
    document.addEventListener('keydown', (event) => {
      if (event.key !== 'Escape') return;
      if ($('board-window')) closeWindow();
      else if (editing && !document.querySelector('.modal[style*="block"]')) setEditing(false);
    });
    for (const [id, name] of [['look-style', 'style'], ['look-density', 'density'], ['look-width', 'width']]) {
      $(id).addEventListener('click', (event) => {
        const button = event.target.closest('button[data-value]');
        if (button) setLook({ [name]: button.dataset.value });
      });
    }
    $('look-labels').addEventListener('change', (event) => setLook({ labels: event.target.checked }));
    $('look-accent').addEventListener('input', (event) => setLook({ accent: event.target.value }));
    $('look-reset').addEventListener('click', () => {
      if (!shared) host.layout().look = {};
      setLook(shared ? DEFAULT_LOOK : {});
    });
    bindDrag();
    // The grid has as many cells across as the window has room for.
    new ResizeObserver(() => { if (active && columnsFor(root.clientWidth) !== columns) render(); }).observe(root);
    setInterval(() => { if (host.looking()) drawWidgets(); }, WIDGETS_REFRESH_MS);
  }

  bind();

  return {
    /**
     * Take what the server sent and show the dashboard the user last looked at.
     *
     * @param {object} sent The answer of `/api/dashboard`.
     * @param {string[]} [favorites] Favorites of a layout kept before dashboards, to place as tiles.
     */
    load(sent, favorites = []) {
      if (sent) server = sent;
      selectBoard(host.layout().active);
      if (favorites.length && canCustomize()) {
        favorites.map((id) => host.apps().find((app) => app.id === id)).filter(Boolean).forEach((app) => { if (!favoriteOf(app)) toggleFavorite(app, false); });
        persist();
      }
      render();
    },
    render,
    live,
    /** Take the dashboards as the server has them now, when the page is come back to and nothing is being edited. */
    async refresh() {
      if (editing || $('board-window')) return;
      try {
        const sent = await host.fetch('/api/dashboard', { method: 'GET' });
        if (JSON.stringify(sent) === JSON.stringify(server)) return;
        server = sent;
        selectBoard(active.id);
        render();
      } catch (error) { /* the next landing asks again */ }
    },
    isFavorite: (app) => Boolean(favoriteOf(app)),
    toggleFavorite,
    canCustomize,
    isEditing: () => editing,
    /** Mark the application tiles of launches in flight, and clear the others. */
    syncApps() {
      const busy = host.busyFlights();
      root.querySelectorAll('.tile-grid .app-tile[data-app]').forEach((tile) => {
        const flight = busy.get(tile.dataset.app);
        tile.classList.toggle('busy', Boolean(flight));
        if (!flight) tile.querySelector('.stage').innerHTML = '';
      });
    },
    /** What the administrator's tools work with. */
    core: {
      t, host, openWindow, closeWindow, field, input, select, render, askName,
      server: () => server,
      active: () => active,
      tiles: () => tiles,
      setTiles(next) { tiles = next; },
      shared: () => shared,
      setShared(next) { shared = next; answers.clear(); },
      selectBoard,
      setEditing,
      pickFile,
      placeLinks,
      openModes,
      attach(tools) { admin = tools; },
      reload: async () => {
        server = await host.fetch('/api/dashboard', { method: 'GET' });
        selectBoard(active.id);
        render();
      },
    },
  };
}
