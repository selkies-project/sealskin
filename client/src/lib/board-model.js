/**
 * The home page's dashboards as data: what the server lays out, what the
 * user changed, and the two laid over each other.
 *
 * A dashboard is one grid, `COLUMNS` cells wide, and every tile has a place
 * on it: a column `x`, a row `y`, and a size in cells. The catalog of
 * applications is a tile too (`apps`), which runs the width of the grid on
 * a row of its own; the running sessions are the page's, above the grid.
 *
 * The server sends dashboards (`/api/dashboard`). The user's changes to one
 * are kept in the browser as a layout of it: where each of the dashboard's
 * tiles now sits (`{ref, x, y, w, h}`), the tiles of the user's own written
 * out (`own`), and the ids of those the user took off (`gone`).
 * `resolveBoard` lays that over the dashboard as it is now, so a tile the
 * administrator adds later appears, one they remove disappears, and one
 * they change is changed, while the user's placement and own tiles stay. A
 * locked dashboard is shown as sent.
 *
 * A link is checked here as the server checks an administrator's
 * (`dashboards.check_link`): `http`, `https`, or a scheme the server lists,
 * and never one a browser runs inside the page.
 */

export const LAYOUT_VERSION = 3;
export const COLUMNS = 12;
export const MAX_H = 8;
export const MAX_ROW = 2000;
export const KINDS = ['app', 'link', 'bundle', 'widget', 'heading', 'apps'];
export const BANDS = ['apps'];
export const HEADING_SCALES = ['s', 'm', 'l'];
export const OPEN_MODES = ['tab', 'same', 'isolated'];
export const LOOK = { style: ['solid', 'glass', 'flat'], density: ['compact', 'normal', 'roomy'], width: ['normal', 'wide'] };
// Schemes a browser runs or reads inside the page that holds the link.
const NEVER = new Set(['javascript', 'data', 'vbscript', 'blob', 'file', 'about', 'view-source', 'filesystem', 'chrome', 'chrome-extension', 'moz-extension', 'resource', 'jar', 'ws', 'wss']);
const WEB = ['http', 'https'];
export const FA_ICON = /^(fa[sbrl]? )?fa-[a-z0-9-]+$/;
export const PACK_ICON = /^(di|sh|si|mdi)-[a-z0-9][a-z0-9-]{0,60}$/;
// A picture the user uploaded as an icon, scaled down before it is kept.
const ICON_DATA = /^data:image\/(png|jpeg|webp);base64,[A-Za-z0-9+/=]+$/;
export const ICON_DATA_MAX = 90000;
const ID = /^[A-Za-z0-9_-]{1,48}$/;

/** A random id for something the user made. */
export function newId(prefix = 'u') {
  const bytes = crypto.getRandomValues(new Uint8Array(8));
  return `${prefix}-${[...bytes].map((b) => b.toString(36).padStart(2, '0')).join('').slice(0, 12)}`;
}

/**
 * Check a link a tile may carry.
 *
 * @param {string} url
 * @param {string[]} schemes The schemes the server allows.
 * @param {boolean} [webOnly] Take `http` and `https` alone.
 * @returns {string|null} The link trimmed, or null for one that is refused.
 */
export function checkLink(url, schemes, webOnly = false) {
  const text = String(url || '').trim();
  if (!text || text.length > 2000 || [...text].some((char) => char.charCodeAt(0) <= 32 || char.charCodeAt(0) === 127)) return null;
  const match = /^([a-z][a-z0-9+.-]*):/i.exec(text);
  const scheme = match ? match[1].toLowerCase() : '';
  const allowed = webOnly ? WEB : (schemes || WEB);
  if (!scheme || NEVER.has(scheme) || !allowed.includes(scheme)) return null;
  if (WEB.includes(scheme) && !/^https?:\/\/[^/?#]+/i.test(text)) return null;
  return text;
}

const INSIDE_SUFFIXES = ['.lan', '.local', '.internal', '.intranet', '.corp', '.home', '.localdomain', '.home.arpa', '.private'];

/**
 * Whether a web address looks like one of an internal network: a private,
 * loopback, or link-local address, a bare host name, or a name under a
 * suffix such networks use. Such an address is reached from anywhere only
 * through a session, whose browser is inside the server's network.
 *
 * @param {string} url
 */
export function insideAddress(url) {
  let host;
  try {
    const parsed = new URL(url);
    if (!/^https?:$/.test(parsed.protocol)) return false;
    host = parsed.hostname.toLowerCase().replace(/^\[|\]$/g, '');
  } catch (error) {
    return false;
  }
  const v4 = /^(\d{1,3})\.(\d{1,3})\.\d{1,3}\.\d{1,3}$/.exec(host);
  if (v4) {
    const [a, b] = [Number(v4[1]), Number(v4[2])];
    return a === 10 || a === 127 || (a === 172 && b >= 16 && b <= 31) || (a === 192 && b === 168) || (a === 169 && b === 254) || (a === 100 && b >= 64 && b <= 127);
  }
  if (host.includes(':')) return host === '::1' || /^f[cd]/.test(host) || /^fe[89ab]/.test(host);
  return !host.includes('.') || INSIDE_SUFFIXES.some((suffix) => host.endsWith(suffix));
}

/** A typed address as a link: `example.org` becomes `https://example.org`. */
export function typedLink(text, schemes, webOnly = false) {
  const value = String(text || '').trim();
  if (!value) return null;
  // `nas.lan:5000` is a host and a port, not a scheme.
  const schemed = /^[a-z][a-z0-9+.-]*:/i.test(value) && !/^[a-z0-9.-]+:\d{1,5}(\/|$)/i.test(value);
  return checkLink(schemed ? value : `https://${value}`, schemes, webOnly);
}

const clamp = (value, low, high, fallback) => {
  const number = Math.round(Number(value));
  return Number.isFinite(number) ? Math.min(high, Math.max(low, number)) : fallback;
};
const text = (value, max) => String(value == null ? '' : value).slice(0, max);

/** A tile's place held to the grid; the catalog runs its whole width on one row. */
export function placed(tile, from = tile) {
  if (BANDS.includes(tile.kind)) return Object.assign(tile, { x: 0, y: clamp(from.y, 0, MAX_ROW, 0), w: COLUMNS, h: 1 });
  const x = clamp(from.x, 0, COLUMNS - 1, 0);
  return Object.assign(tile, { x, y: clamp(from.y, 0, MAX_ROW, 0), w: Math.min(COLUMNS - x, clamp(from.w, 1, COLUMNS, 1)), h: clamp(from.h, 1, MAX_H, 1) });
}

/**
 * A tile of the user's own as kept, or null for one that does not hold up:
 * what the browser stored is read back as carefully as a stranger's file.
 *
 * @param {object} tile
 * @param {string[]} schemes
 */
export function ownTile(tile, schemes) {
  if (!tile || typeof tile !== 'object' || !KINDS.includes(tile.kind) || !ID.test(tile.id || '')) return null;
  const icon = String(tile.icon || '');
  const kept = placed({
    id: tile.id,
    kind: tile.kind,
    own: true,
    name: text(tile.name, 80),
    icon: FA_ICON.test(icon) || PACK_ICON.test(icon) ? icon : '',
    iconData: ICON_DATA.test(tile.iconData || '') && tile.iconData.length <= ICON_DATA_MAX ? tile.iconData : '',
    description: text(tile.description, 200),
  }, tile);
  if (tile.kind === 'app') {
    if (!tile.app) return null;
    kept.app = text(tile.app, 200);
    if (tile.favorite) kept.favorite = true;
  } else if (tile.kind === 'link') {
    kept.open = OPEN_MODES.includes(tile.open) ? tile.open : 'tab';
    kept.url = checkLink(tile.url, schemes, kept.open !== 'tab');
    kept.app = text(tile.app, 200);
    if (kept.open === 'isolated' && /^[A-Za-z0-9_-]{1,64}$/.test(tile.vpn || '')) {
      kept.vpn = tile.vpn;
      kept.vpn_endpoint = /^[A-Za-z0-9_-]{1,64}$/.test(tile.vpn_endpoint || '') ? tile.vpn_endpoint : '';
    }
    if (!kept.url || !kept.name) return null;
  } else if (tile.kind === 'bundle') {
    kept.urls = (Array.isArray(tile.urls) ? tile.urls : []).map((url) => checkLink(url, schemes, true)).filter(Boolean).slice(0, 20);
    if (!kept.urls.length || !kept.name) return null;
  } else if (tile.kind === 'widget') {
    if (tile.widget) kept.widget = text(tile.widget, 48);
    else if (typeof tile.type === 'string') Object.assign(kept, { type: tile.type, config: tile.config && typeof tile.config === 'object' ? tile.config : {} });
    else return null;
  } else if (tile.kind === 'heading') {
    if (!kept.name) return null;
    kept.scale = HEADING_SCALES.includes(tile.scale) ? tile.scale : 'm';
  }
  return kept;
}

const copy = (value) => JSON.parse(JSON.stringify(value));

// --- The grid ---------------------------------------------------------------------

const overlap = (a, b) => a.x < b.x + b.w && b.x < a.x + a.w && a.y < b.y + b.h && b.y < a.y + a.h;

/** The first free place for a tile of a size, looking row by row from `fromRow`. */
export function firstFit(tiles, w, h, fromRow = 0) {
  for (let y = fromRow; y <= MAX_ROW; y++) {
    for (let x = 0; x + w <= COLUMNS; x++) {
      const spot = { x, y, w, h };
      if (!tiles.some((tile) => overlap(spot, tile))) return { x, y };
    }
  }
  return { x: 0, y: MAX_ROW };
}

/**
 * Make room for a tile that was put down or resized: it keeps its place,
 * and whatever it now covers moves down below it, as does whatever those
 * then cover. A tile nothing reaches stays where it was arranged.
 *
 * @param {object[]} tiles Every tile of the page; changed in place.
 * @param {object} moved The tile that keeps its place.
 */
export function settle(tiles, moved) {
  const still = [moved];
  tiles.filter((tile) => tile !== moved).sort((a, b) => a.y - b.y || a.x - b.x).forEach((tile) => {
    for (let hit = still.find((other) => overlap(tile, other)); hit && tile.y < MAX_ROW; hit = still.find((other) => overlap(tile, other))) {
      tile.y = Math.min(MAX_ROW, hit.y + hit.h);
    }
    still.push(tile);
  });
  return tiles;
}

/** Close the empty rows of a page: every tile rises as far as it can without passing another. */
export function tidy(tiles) {
  const done = [];
  [...tiles].sort((a, b) => a.y - b.y || a.x - b.x).forEach((tile) => {
    let y = tile.y;
    while (y > 0 && !done.some((other) => overlap({ ...tile, y: y - 1 }, other))) y--;
    tile.y = y;
    done.push(tile);
  });
  return tiles;
}

/**
 * The places tiles take on a grid narrower than they were arranged for, as
 * a phone's: in reading order, each as wide as fits, where it first fits.
 *
 * @param {object[]} tiles
 * @param {number} columns
 * @returns {Map<string, {x: number, y: number, w: number, h: number}>} Places by tile id.
 */
export function reflow(tiles, columns) {
  const taken = [];
  const places = new Map();
  let floor = 0;
  [...tiles].sort((a, b) => a.y - b.y || a.x - b.x).forEach((tile) => {
    const band = BANDS.includes(tile.kind);
    // One cell stays one cell; anything wider takes its share of the narrower grid, twice over, so a widget stays readable.
    const wide = band || tile.kind === 'heading' ? columns : (tile.w === 1 ? 1 : Math.min(columns, Math.ceil((tile.w * columns) / (COLUMNS / 2))));
    let spot = null;
    for (let y = floor; !spot; y++) {
      for (let x = 0; x + wide <= columns && !spot; x++) {
        const candidate = { x, y, w: wide, h: tile.h };
        if (!taken.some((other) => overlap(candidate, other))) spot = candidate;
      }
    }
    taken.push(spot);
    places.set(tile.id, spot);
    // Nothing later in reading order sits above a row that runs the whole width.
    if (band || tile.kind === 'heading') floor = spot.y + spot.h;
  });
  return places;
}

/** The tiles of a page kept as sections of ordered tiles, each given a place in reading order. */
export function fromSections(sections) {
  const tiles = [];
  let row = 0;
  (Array.isArray(sections) ? sections : []).forEach((section) => {
    if (!section || typeof section !== 'object') return;
    if (section.kind === 'sessions') return;
    if (BANDS.includes(section.kind)) {
      tiles.push({ id: section.kind, kind: section.kind, y: row++ });
      return;
    }
    if (section.name) tiles.push({ id: newId('u'), kind: 'heading', name: section.name, x: 0, y: row++, w: COLUMNS, h: 1 });
    let column = 0;
    let height = 0;
    (Array.isArray(section.tiles) ? section.tiles : []).forEach((tile) => {
      const w = clamp(tile.w, 1, COLUMNS, 1);
      const h = clamp(tile.h, 1, MAX_H, 1);
      if (column + w > COLUMNS) {
        row += height;
        column = 0;
        height = 0;
      }
      tiles.push({ ...tile, x: column, y: row, w, h });
      column += w;
      height = Math.max(height, h);
    });
    row += height;
  });
  return tiles;
}

// --- Laying the user's page over the dashboard ----------------------------------------

/**
 * Lay the user's layout of a dashboard over the dashboard as the server
 * sends it now.
 *
 * @param {object} board The dashboard: `{id, name, locked, tiles}`.
 * @param {object|null} saved The user's layout of it, or nothing.
 * @param {string[]} schemes The link schemes the server allows.
 * @returns {object[]} The page's tiles.
 */
export function resolveBoard(board, saved, schemes) {
  const sent = copy(board.tiles || []).filter((tile) => KINDS.includes(tile.kind)).map((tile) => placed(tile));
  if (board.locked || !saved || !Array.isArray(saved.tiles)) return sent;
  const known = new Map(sent.map((tile) => [tile.id, tile]));
  const gone = new Set(saved.gone || []);
  const seen = new Set();
  const tiles = saved.tiles.map((entry) => {
    if (entry && entry.ref) {
      const tile = known.get(entry.ref);
      if (!tile || seen.has(entry.ref)) return null;
      seen.add(entry.ref);
      return placed(tile, entry);
    }
    const own = ownTile(entry, schemes);
    if (!own || seen.has(own.id) || known.has(own.id)) return null;
    seen.add(own.id);
    return own;
  }).filter(Boolean);
  // What the administrator added since goes where they put it, or to the first free place from there.
  sent.forEach((tile) => {
    if (seen.has(tile.id) || gone.has(tile.id)) return;
    if (tiles.some((other) => overlap(tile, other))) Object.assign(tile, firstFit(tiles, tile.w, tile.h, tile.y));
    tiles.push(tile);
  });
  return tiles;
}

/**
 * The user's layout of a dashboard, to keep: where its tiles sit, the
 * user's own, and what the dashboard has that the page no longer shows.
 *
 * @param {object} board The dashboard as sent.
 * @param {object[]} tiles The page's tiles.
 */
export function snapshot(board, tiles) {
  const shown = new Set();
  return {
    tiles: tiles.map((tile) => {
      if (tile.own) return tile;
      shown.add(tile.id);
      return { ref: tile.id, x: tile.x, y: tile.y, w: tile.w, h: tile.h };
    }),
    gone: (board.tiles || []).map((tile) => tile.id).filter((id) => !shown.has(id)),
  };
}

/** A page of the user's own, as kept: every tile is theirs. */
export function ownBoard(board, schemes) {
  if (!board || typeof board !== 'object' || !ID.test(board.id || '')) return null;
  const seen = new Set();
  const bands = new Set();
  const source = Array.isArray(board.tiles) ? board.tiles : fromSections(board.sections);
  const tiles = source.map((tile) => ownTile(tile, schemes)).filter((tile) => {
    if (!tile || seen.has(tile.id) || bands.has(tile.kind)) return false;
    seen.add(tile.id);
    if (BANDS.includes(tile.kind)) bands.add(tile.kind);
    return true;
  });
  return { id: board.id, name: text(board.name, 60) || 'Page', own: true, tiles };
}

/** A look as kept: only the choices there are; a choice left out is the dashboard's to make. */
export function readLook(kept = {}) {
  const look = {};
  Object.entries(LOOK).forEach(([name, choices]) => { if (choices.includes(kept[name])) look[name] = kept[name]; });
  if (typeof kept.labels === 'boolean') look.labels = kept.labels;
  if (/^#[0-9a-f]{6}$/i.test(kept.accent || '')) look.accent = kept.accent.toLowerCase();
  return look;
}

/**
 * The layout as kept, whatever version kept it. The first layouts held
 * `favorites`, handed back for the page to place as tiles; a layout of
 * sections keeps its pages, read onto the grid, and lets go of its
 * rearrangements of the dashboards.
 *
 * @param {object} kept
 * @returns {{layout: object, favorites: string[]}}
 */
export function readLayout(kept = {}) {
  const current = kept.v === LAYOUT_VERSION;
  const layout = {
    v: LAYOUT_VERSION,
    hidden: Array.isArray(kept.hidden) ? kept.hidden.filter((id) => typeof id === 'string') : [],
    background: kept.background,
    look: readLook(kept.look),
    active: typeof kept.active === 'string' ? kept.active : '',
    boards: current && kept.boards && typeof kept.boards === 'object' ? kept.boards : {},
    own: Array.isArray(kept.own) ? kept.own : [],
  };
  const favorites = !kept.v && Array.isArray(kept.favorites) ? kept.favorites.filter((id) => typeof id === 'string') : [];
  return { layout, favorites };
}

/**
 * The feed addresses of an OPML file, the list of subscriptions feed
 * readers export: every outline's `xmlUrl` that is a web address, each once.
 *
 * @param {string} xml
 * @returns {string[]}
 */
export function opmlFeeds(xml) {
  const list = new DOMParser().parseFromString(xml, 'text/xml');
  const feeds = [...list.querySelectorAll('outline')].map((outline) => checkLink(outline.getAttribute('xmlUrl') || outline.getAttribute('xmlurl'), WEB, true)).filter(Boolean);
  return [...new Set(feeds)];
}

/**
 * The links of a bookmarks file as browsers export it (the Netscape
 * bookmark format), each with the folder it sat in.
 *
 * @param {string} html
 * @param {string[]} schemes
 * @returns {Array<{name: string, url: string, folder: string}>}
 */
export function parseBookmarks(html, schemes) {
  const page = new DOMParser().parseFromString(html, 'text/html');
  return [...page.querySelectorAll('a[href]')].map((anchor) => {
    const list = anchor.closest('dl');
    const heading = list && list.previousElementSibling && list.previousElementSibling.matches('h3') ? list.previousElementSibling
      : (list && list.parentElement && list.parentElement.querySelector(':scope > h3'));
    return {
      name: text(anchor.textContent.trim() || anchor.getAttribute('href'), 80),
      url: checkLink(anchor.getAttribute('href'), schemes),
      folder: text(heading ? heading.textContent.trim() : '', 80),
    };
  }).filter((link) => link.url);
}
