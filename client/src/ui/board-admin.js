/**
 * An administrator's tools on the home page: the shared dashboards are
 * edited where users see them, in the page's own edit mode.
 *
 * In edit mode the bar gains a switch between the administrator's own page
 * and the shared dashboard. On the shared side the page shows the dashboard
 * as kept on the server, with none of the administrator's own changes laid
 * over it, and every edit, the look of its tiles included, goes to that copy
 * until Save writes it for
 * everyone it names (`PUT /api/admin/dashboards/boards`). A page that has no
 * dashboard behind it, the built-in one or a page of the administrator's
 * own, becomes a new dashboard on its first save.
 *
 * Three windows hold the rest: a dashboard's settings (name, who it is for,
 * the lock), the list of all dashboards with their order, and the widget
 * library, where a widget is configured by its type, tried against the
 * server, and saved.
 */

import { escapeHtml } from '../lib/dom.js';
import { confirmDialog } from '../lib/modal.js';
import { newId, opmlFeeds } from '../lib/board-model.js';
import { WIDGET_ICONS, drawWidget } from '../lib/widget-view.js';

const $ = (id) => document.getElementById(id);
const copy = (value) => JSON.parse(JSON.stringify(value));
const TILE_FIELDS = ['id', 'kind', 'x', 'y', 'w', 'h', 'name', 'app', 'url', 'urls', 'open', 'icon', 'description', 'widget', 'scale', 'status', 'vpn', 'vpn_endpoint'];
const FORMATS = ['text', 'number', 'float', 'percent', 'bytes', 'bitrate', 'size', 'date', 'relativeDate', 'duration'];
const list = (value) => String(value || '').split(',').map((item) => item.trim()).filter(Boolean);

/**
 * @param {object} core What `board.js` lends: the page's state and its windows.
 */
export function attachAdmin(core) {
  const { t, host, openWindow, closeWindow, field, input, select } = core;
  // The dashboards and the library as the server keeps them, read when edit mode first needs them.
  let data = null;

  const put = (path, body) => host.fetch(`/api/admin/dashboards/${path}`, { method: 'PUT', body: JSON.stringify(body) });
  const load = async () => {
    if (!data) data = await host.fetch('/api/admin/dashboards', { method: 'GET' });
    return data;
  };

  /** The tiles of a dashboard as the server keeps them; a user's own widget has no place on one. */
  const cleanTiles = (tiles) => tiles.filter((tile) => tile.kind !== 'widget' || tile.widget).map((tile) => Object.fromEntries(TILE_FIELDS.filter((name) => tile[name] !== undefined).map((name) => [name, tile[name]])));

  function show() {
    const shared = core.shared();
    $('admin-bar').hidden = false;
    $('admin-scope').querySelectorAll('button').forEach((button) => button.classList.toggle('active', (button.dataset.scope === 'shared') === Boolean(shared)));
    $('admin-settings').hidden = $('admin-save').hidden = !shared;
    $('admin-save').disabled = !(shared && shared.dirty);
    $('admin-note').textContent = shared
      ? t(shared.board.locked ? 'web.board.sharedNoteLocked' : 'web.board.sharedNote', { who: [...shared.board.groups, ...shared.board.users].join(', ') || t('web.board.nobody') })
      : t('web.board.mineNote');
  }

  async function enterShared(id = null) {
    await load();
    const active = core.active();
    let board = data.boards.find((candidate) => candidate.id === (id || active.id));
    let isNew = false;
    if (!board) {
      // The built-in page or one of the administrator's own: the start of a new dashboard.
      isNew = true;
      board = { id: newId('d'), name: active.own ? active.name : t('common.home'), icon: '', groups: ['all'], users: [], locked: false, kiosk: false, look: {}, isolated_app: '', tiles: cleanTiles(core.tiles()) };
    }
    // The pictures of its icons come with the dashboard as users are sent it.
    const images = new Map((core.server().boards.find((candidate) => candidate.id === board.id) || { tiles: [] }).tiles.map((tile) => [tile.id, tile.image]));
    const working = copy(board);
    working.tiles.forEach((tile) => { tile.image = images.get(tile.id) || (isNew ? (core.tiles().find((other) => other.id === tile.id) || {}).image : '') || ''; });
    core.setShared({ board: working, dirty: isNew, isNew });
    core.setTiles(working.tiles);
    core.setEditing(true);
  }

  function exitShared() {
    core.setShared(null);
    core.selectBoard(core.active().id);
    core.render();
  }

  /** Leave the shared dashboard; with unsaved changes the administrator is asked first, and the leaving waits. */
  function leave() {
    const shared = core.shared();
    if (!shared) return true;
    if (!shared.dirty) {
      exitShared();
      return true;
    }
    confirmDialog(t, { title: t('web.board.discardTitle'), message: t('web.board.discardMessage', { name: shared.board.name }), confirm: t('web.board.discard'), danger: true }).then((yes) => { if (yes) exitShared(); });
    return false;
  }

  async function save() {
    const shared = core.shared();
    const board = { ...shared.board, tiles: cleanTiles(core.tiles()) };
    const boards = data.boards.some((candidate) => candidate.id === board.id) ? data.boards.map((candidate) => (candidate.id === board.id ? board : candidate)) : [...data.boards, board];
    try {
      data.boards = (await put('boards', { boards })).boards;
    } catch (error) {
      host.notify(error.message, true);
      return;
    }
    core.setShared(null);
    // The administrator's own layout of it would hide what was just saved.
    delete host.layout().boards[board.id];
    host.layout().active = board.id;
    await host.saveLayout();
    await core.reload();
    host.notify(t('web.board.sharedSaved', { name: board.name }));
  }

  const accessFields = (item) => field(t('common.groups'), input('groups', (item.groups || []).join(', '), 'list="admin-groups"'), t('web.board.groupsHint'))
    + field(t('common.users'), input('users', (item.users || []).join(', ')), t('web.board.usersHint'))
    + `<datalist id="admin-groups"><option value="all">${data.groups.map((group) => `<option value="${escapeHtml(group)}">`).join('')}</datalist>`;

  function settingsWindow() {
    const { board } = core.shared();
    openWindow({
      title: t('web.board.dashboardSettings'),
      icon: 'fa-sliders-h',
      body: field(t('common.name'), input('name', board.name, 'maxlength="60"'))
        + field(t('web.board.icon'), input('icon', board.icon || '', 'placeholder="fa-home"'), t('web.board.tabIconHint'))
        + accessFields(board)
        + field(t('web.board.sessionApp'), select('isolated_app', [['', t('web.board.askEachTime')], ...host.apps().filter((app) => app.url_support).map((app) => [app.id, app.name])], board.isolated_app || ''), t('web.board.sessionAppHint'))
        + `<div class="form-group"><label><input type="checkbox" name="locked"${board.locked ? ' checked' : ''}> ${escapeHtml(t('web.board.locked'))}</label><small class="field-hint">${escapeHtml(t('web.board.lockedHint'))}</small></div>`
        + `<div class="form-group"><label><input type="checkbox" name="kiosk"${board.kiosk ? ' checked' : ''}> ${escapeHtml(t('web.board.kiosk'))}</label><small class="field-hint">${escapeHtml(t('web.board.kioskHint'))}</small></div>`,
      confirm: t('common.ok'),
      onConfirm: (win) => {
        const value = (name) => win.querySelector(`[name="${name}"]`).value.trim();
        if (!value('name')) throw new Error(t('web.board.needName'));
        if (value('icon') && !/^(fa[sbrl]? )?fa-[a-z0-9-]+$/.test(value('icon'))) throw new Error(t('web.board.tabIconHint'));
        Object.assign(board, { name: value('name'), icon: value('icon'), groups: list(value('groups')), users: list(value('users')), isolated_app: value('isolated_app'), locked: win.querySelector('[name="locked"]').checked, kiosk: win.querySelector('[name="kiosk"]').checked });
        core.shared().dirty = true;
        core.render();
      },
    });
  }

  /** Every dashboard in the order users get them, to reorder, open for editing, copy, or delete. */
  async function manageWindow() {
    await load();
    const rows = () => (data.boards.length ? data.boards.map((board, index) => `
      <li data-id="${escapeHtml(board.id)}">
        <span class="row-name"><b>${escapeHtml(board.name)}</b>${board.locked ? ' <i class="fas fa-lock"></i>' : ''}<small>${escapeHtml([...board.groups, ...board.users].join(', ') || t('web.board.nobody'))}</small></span>
        <button type="button" class="tool" data-row="up" ${index ? '' : 'disabled'} title="${escapeHtml(t('web.home.moveEarlier'))}"><i class="fas fa-arrow-up"></i></button>
        <button type="button" class="tool" data-row="down" ${index < data.boards.length - 1 ? '' : 'disabled'} title="${escapeHtml(t('web.home.moveLater'))}"><i class="fas fa-arrow-down"></i></button>
        <button type="button" class="tool" data-row="edit" title="${escapeHtml(t('common.edit'))}"><i class="fas fa-pen"></i></button>
        <button type="button" class="tool" data-row="copy" title="${escapeHtml(t('web.board.duplicate'))}"><i class="fas fa-copy"></i></button>
        <button type="button" class="tool" data-row="delete" title="${escapeHtml(t('common.delete'))}"><i class="fas fa-trash"></i></button>
      </li>`).join('') : `<li class="grid-empty">${escapeHtml(t('web.board.noDashboards'))}</li>`);
    const win = openWindow({ title: t('web.board.allDashboards'), icon: 'fa-th-large', body: `<p class="field-hint">${escapeHtml(t('web.board.orderHint'))}</p><ul class="admin-rows">${rows()}</ul>`, wide: true });
    win.querySelector('.admin-rows').addEventListener('click', async (event) => {
      const button = event.target.closest('[data-row]');
      if (!button) return;
      const { id } = button.closest('li').dataset;
      const at = data.boards.findIndex((board) => board.id === id);
      const next = [...data.boards];
      const action = button.dataset.row;
      if (action === 'edit') {
        closeWindow();
        if (leave()) {
          host.layout().active = id;
          await enterShared(id);
        }
        return;
      }
      if (action === 'up' || action === 'down') next.splice(at + (action === 'up' ? -1 : 1), 0, next.splice(at, 1)[0]);
      else if (action === 'copy') next.splice(at + 1, 0, { ...copy(next[at]), id: newId('d'), name: t('web.board.copyOf', { name: next[at].name }) });
      else if (!(await confirmDialog(t, { title: t('web.board.deleteDashboard'), message: t('web.board.deleteDashboardConfirm', { name: next[at].name }), confirm: t('common.delete'), danger: true }))) return;
      else next.splice(at, 1);
      try {
        data.boards = (await put('boards', { boards: next })).boards;
        win.querySelector('.admin-rows').innerHTML = rows();
        if (!core.shared()) await core.reload();
      } catch (error) {
        host.notify(error.message, true);
      }
    });
  }

  // --- The widget library ---------------------------------------------------------

  const area = (name, value, rows = 3, attrs = '') => `<textarea name="${name}" rows="${rows}" spellcheck="false" ${attrs}>${escapeHtml(value || '')}</textarea>`;
  const lines = (value) => String(value || '').split('\n').map((line) => line.trim()).filter(Boolean);
  const headerText = (headers) => Object.entries(headers || {}).map(([name, value]) => `${name}: ${value}`).join('\n');
  const readHeaders = (value) => Object.fromEntries(lines(value).filter((line) => line.includes(':')).map((line) => [line.slice(0, line.indexOf(':')).trim(), line.slice(line.indexOf(':') + 1).trim()]));

  function mappingRows(mappings) {
    const rows = Array.isArray(mappings) && mappings.length ? mappings : [{ field: '', label: '', format: 'text', suffix: '' }];
    return rows.map((row) => `<div class="map-row">
      ${input('map-field', typeof row.field === 'string' ? row.field : JSON.stringify(row.field), `placeholder="${escapeHtml(t('web.board.mapField'))}"`)}
      ${input('map-label', row.label || '', `placeholder="${escapeHtml(t('web.board.label'))}"`)}
      ${select('map-format', FORMATS.map((format) => [format, format]), row.format || 'text')}
      ${input('map-suffix', row.suffix || '', `placeholder="${escapeHtml(t('web.board.mapSuffix'))}" class="narrow"`)}
      <button type="button" class="tool" data-role="map-remove"><i class="fas fa-times"></i></button></div>`).join('');
  }

  function apiFields(config, prefix = '') {
    const dynamic = config.display === 'dynamic-list' || (config.mappings && !Array.isArray(config.mappings));
    const rowsOf = dynamic ? (config.mappings || {}) : {};
    return field(t('web.board.address'), input(`${prefix}url`, config.url || '', 'placeholder="http://service.lan:8080/api/status"'), t('web.board.secretHint', { names: data.secrets.join(', ') || t('common.none') }))
      + `<div class="inline-row">${field(t('web.board.method'), select(`${prefix}method`, [['GET', 'GET'], ['POST', 'POST']], config.method || 'GET'))}
        ${prefix ? '' : field(t('web.board.display'), select('display', [['block', t('web.board.displayBlock')], ['list', t('web.board.displayList')], ['dynamic-list', t('web.board.displayRows')]], dynamic ? 'dynamic-list' : (config.display || 'block')))}</div>`
      + field(t('web.board.headers'), area(`${prefix}headers`, headerText(config.headers), 2, 'placeholder="X-Api-Key: {{secret.NAME}}"'), t('web.board.headersHint'))
      + field(t('web.board.body'), area(`${prefix}body`, config.body, 2))
      + (prefix ? '' : `<div class="display-fields" data-for="values">${field(t('web.board.mappings'), `<div class="map-rows">${mappingRows(dynamic ? [] : config.mappings)}</div><button type="button" class="secondary small" data-role="map-add"><i class="fas fa-plus"></i> ${escapeHtml(t('web.board.addValue'))}</button>`, t('web.board.mappingsHint'))}</div>
        <div class="display-fields" data-for="rows"><div class="inline-row">${field(t('web.board.rowsItems'), input('rows-items', rowsOf.items || '', 'placeholder="records"'))}${field(t('web.board.rowsName'), input('rows-name', rowsOf.name || '', 'placeholder="title"'))}</div>
          <div class="inline-row">${field(t('web.board.rowsValue'), input('rows-label', rowsOf.label || '', 'placeholder="status"'))}${field(t('web.board.rowsLimit'), input('rows-limit', rowsOf.limit || 8, 'inputmode="numeric" class="narrow"'))}</div></div>`);
  }

  function readApi(win, prefix = '') {
    const value = (name) => (win.querySelector(`[name="${name}"]`) ? win.querySelector(`[name="${name}"]`).value.trim() : '');
    const config = { url: value(`${prefix}url`), method: value(`${prefix}method`) || 'GET', headers: readHeaders(value(`${prefix}headers`)), body: value(`${prefix}body`) };
    if (prefix) return config;
    config.display = value('display');
    if (config.display === 'dynamic-list') {
      config.mappings = { items: value('rows-items'), name: value('rows-name'), label: value('rows-label'), limit: Number(value('rows-limit')) || 8 };
    } else {
      config.mappings = [...win.querySelectorAll('.map-row')].map((row) => {
        const part = (name) => row.querySelector(`[name="${name}"]`).value.trim();
        return { field: part('map-field'), label: part('map-label'), format: part('map-format'), suffix: part('map-suffix') };
      }).filter((mapping) => mapping.field);
    }
    return config;
  }

  function typeFields(type, config) {
    if (type === 'clock') return field(t('web.board.timezone'), input('timezone', config.timezone || '', 'placeholder="Europe/Berlin"'), t('web.board.timezoneHint')) + field(t('web.board.label'), input('label', config.label || ''));
    if (type === 'weather') {
      return `<div class="inline-row">${field(t('web.board.latitude'), input('latitude', config.latitude ?? ''))}${field(t('web.board.longitude'), input('longitude', config.longitude ?? ''))}</div>`
        + field(t('web.board.label'), input('label', config.label || ''))
        + field(t('web.board.units'), select('units', [['metric', t('web.board.metric')], ['imperial', t('web.board.imperial')]], config.units || 'metric'));
    }
    if (type === 'stocks') return field(t('web.board.symbols'), input('symbols', (config.symbols || []).join(', '), 'placeholder="AAPL, MSFT, ^GSPC, BTC-USD"'), t('web.board.symbolsHint'));
    if (type === 'feed') return field(t('web.board.feedUrls'), area('urls', (config.urls || []).join('\n'), 3, 'placeholder="https://"'), t('web.board.onePerLine')) + `<button type="button" class="secondary small" data-role="opml"><i class="fas fa-file-import"></i> ${escapeHtml(t('web.board.importOpml'))}</button>` + field(t('web.board.limit'), input('limit', config.limit || 8, 'inputmode="numeric" class="narrow"'));
    if (type === 'calendar') return field(t('web.board.calendarUrls'), area('urls', (config.urls || []).join('\n'), 3, 'placeholder="https://"'), t('web.board.calendarHint')) + field(t('web.board.days'), input('days', config.days || 14, 'inputmode="numeric" class="narrow"'));
    if (type === 'api') return `<button type="button" class="secondary small" data-role="homepage"><i class="fas fa-file-import"></i> ${escapeHtml(t('web.board.fromHomepage'))}</button>${apiFields(config)}`;
    if (type === 'monitor') return field(t('web.board.sites'), area('sites', (config.sites || []).map((site) => `${site.name} | ${site.url}`).join('\n'), 4, 'placeholder="Wiki | https://wiki.lan"'), t('web.board.sitesHint'));
    if (type === 'text') {
      // The times are kept in UTC and typed in the administrator's own zone.
      const local = (iso) => {
        const when = iso ? new Date(iso) : null;
        return when && !Number.isNaN(when.getTime()) ? new Date(when.getTime() - when.getTimezoneOffset() * 60000).toISOString().slice(0, 16) : '';
      };
      return field(t('web.board.notice'), area('text', config.text, 4)) + field(t('web.board.tone'), select('tone', ['neutral', 'info', 'success', 'warning', 'danger'].map((tone) => [tone, t(`web.board.tones.${tone}`)]), config.tone || 'neutral'))
        + `<div class="inline-row">${field(t('web.board.starts'), `<input type="datetime-local" name="starts" value="${local(config.starts)}">`)}${field(t('web.board.ends'), `<input type="datetime-local" name="ends" value="${local(config.ends)}">`)}</div><p class="field-hint">${escapeHtml(t('web.board.scheduleHint'))}</p>`;
    }
    if (type === 'frame') {
      return field(t('web.board.frameHtml'), area('html', config.html, 8, 'class="code"'), t('web.board.frameHint'))
        + `<details${config.source ? ' open' : ''}><summary>${escapeHtml(t('web.board.frameSource'))}</summary><p class="field-hint">${escapeHtml(t('web.board.frameSourceHint'))}</p>${apiFields(config.source || {}, 'source-')}</details>`;
    }
    if (type === 'embed') return field(t('web.board.address'), input('url', config.url || '', 'placeholder="https://"'), t('web.board.embedHint'));
    return '';
  }

  function readType(win, type) {
    const value = (name) => (win.querySelector(`[name="${name}"]`) ? win.querySelector(`[name="${name}"]`).value.trim() : '');
    if (type === 'clock') return { timezone: value('timezone'), label: value('label') };
    if (type === 'weather') return { latitude: Number(value('latitude')), longitude: Number(value('longitude')), label: value('label'), units: value('units') };
    if (type === 'stocks') return { symbols: value('symbols').split(/[\s,]+/).filter(Boolean) };
    if (type === 'feed') return { urls: lines(value('urls')), limit: Number(value('limit')) || 8 };
    if (type === 'calendar') return { urls: lines(value('urls')), days: Number(value('days')) || 14 };
    if (type === 'api') return readApi(win);
    if (type === 'monitor') return { sites: lines(value('sites')).map((line) => ({ name: line.split('|')[0].trim(), url: (line.split('|')[1] || '').trim() })) };
    if (type === 'text') {
      const utc = (name) => (value(name) ? new Date(value(name)).toISOString() : '');
      return { text: value('text'), tone: value('tone'), starts: utc('starts'), ends: utc('ends') };
    }
    if (type === 'frame') return { html: win.querySelector('[name="html"]').value, ...(value('source-url') ? { source: readApi(win, 'source-') } : {}) };
    if (type === 'embed') return { url: value('url') };
    return {};
  }

  function widgetWindow(widget) {
    const item = widget ? copy(widget) : { id: newId('w'), name: '', type: 'feed', config: {}, groups: ['all'], users: [], refresh: 0, w: 3, h: 2 };
    const types = data.types.filter((type) => type !== 'notes');
    const draw = (win) => {
      win.querySelector('.type-fields').innerHTML = typeFields(item.type, item.config);
      const display = win.querySelector('[name="display"]');
      const sync = () => {
        win.querySelectorAll('.display-fields').forEach((block) => { block.hidden = (block.dataset.for === 'rows') !== (display && display.value === 'dynamic-list'); });
      };
      if (display) display.onchange = sync;
      sync();
    };
    const read = (win) => {
      const value = (name) => win.querySelector(`[name="${name}"]`).value.trim();
      return { ...item, name: value('name'), type: item.type, config: readType(win, item.type), groups: list(value('groups')), users: list(value('users')), refresh: Number(value('refresh')) || 0, w: Number(value('w')) || 3, h: Number(value('h')) || 2 };
    };
    const win = openWindow({
      title: t(widget ? 'web.board.editWidget' : 'web.board.addWidget'),
      icon: 'fa-puzzle-piece',
      wide: true,
      body: `<div class="inline-row">${field(t('common.name'), input('name', item.name, 'maxlength="80"'))}${field(t('web.board.type'), select('type', types.map((type) => [type, t(`web.board.types.${type}`)]), item.type))}</div>
        <div class="type-fields"></div>
        <div class="inline-row">${field(t('web.board.refresh'), input('refresh', item.refresh || '', `inputmode="numeric" class="narrow" placeholder="${escapeHtml(t('web.board.automatic'))}"`))}
          ${field(t('web.board.width'), input('w', item.w, 'inputmode="numeric" class="narrow"'))}${field(t('web.board.height'), input('h', item.h, 'inputmode="numeric" class="narrow"'))}</div>
        ${accessFields(item)}
        <div class="form-group"><button type="button" class="secondary" data-role="test"><i class="fas fa-vial"></i> ${escapeHtml(t('web.board.test'))}</button>
          <div class="widget-preview widget-tile" hidden><div class="widget-body"></div></div></div>`,
      onConfirm: async (form) => {
        const made = read(form);
        if (!made.name) throw new Error(t('web.board.needName'));
        const next = data.widgets.some((candidate) => candidate.id === made.id) ? data.widgets.map((candidate) => (candidate.id === made.id ? made : candidate)) : [...data.widgets, made];
        data.widgets = (await put('widgets', { widgets: next })).widgets;
        await core.reload();
        libraryWindow();
        return false;
      },
    });
    win.querySelector('[name="type"]').addEventListener('change', (event) => {
      item.type = event.target.value;
      item.config = {};
      draw(win);
    });
    win.addEventListener('click', async (event) => {
      const role = (event.target.closest('[data-role]') || { dataset: {} }).dataset.role;
      if (role === 'map-add') win.querySelector('.map-rows').insertAdjacentHTML('beforeend', mappingRows([]));
      else if (role === 'map-remove') event.target.closest('.map-row').remove();
      else if (role === 'homepage') homepageWindow(item);
      else if (role === 'opml') {
        const file = await core.pickFile('.opml,.xml,text/xml,text/x-opml');
        const feeds = file ? opmlFeeds(file.text) : [];
        const box = win.querySelector('[name="urls"]');
        if (!feeds.length) host.notify(t('web.board.noFeeds'), true);
        else box.value = [...new Set([...lines(box.value), ...feeds])].slice(0, 10).join('\n');
      }
      else if (role === 'test') {
        const preview = win.querySelector('.widget-preview');
        const body = preview.querySelector('.widget-body');
        preview.hidden = false;
        preview.className = `widget-preview widget-tile type-${item.type}`;
        body.dataset.stamp = '';
        body.innerHTML = '<span class="inline-spinner"></span>';
        try {
          const made = read(win);
          drawWidget(body, made.type, await host.fetch('/api/admin/dashboards/widgets/test', { method: 'POST', body: JSON.stringify({ type: made.type, config: made.config }) }), { t, locale: host.locale });
        } catch (error) {
          drawWidget(body, item.type, { ok: false, error: error.message }, { t, locale: host.locale });
        }
      }
    });
    draw(win);
  }

  /** Take a Homepage `customapi` definition, pasted as YAML, as the widget's configuration. */
  function homepageWindow(item) {
    openWindow({
      title: t('web.board.fromHomepage'),
      icon: 'fa-file-import',
      wide: true,
      body: field(t('web.board.homepageYaml'), area('yaml', '', 12, 'class="code" placeholder="widget:\n  type: customapi\n  url: http://service.lan/api\n  mappings:\n    - field: status\n      label: Status"'), t('web.board.homepageHint')),
      confirm: t('web.board.import'),
      onConfirm: async (win) => {
        const made = await host.fetch('/api/admin/dashboards/widgets/homepage', { method: 'POST', body: JSON.stringify({ text: win.querySelector('[name="yaml"]').value }) });
        widgetWindow({ ...item, type: 'api', config: made.config, refresh: made.refresh });
        return false;
      },
    });
  }

  async function libraryWindow() {
    await load();
    const rows = data.widgets.length ? data.widgets.map((widget) => `
      <li data-id="${escapeHtml(widget.id)}"><i class="fas ${WIDGET_ICONS[widget.type] || 'fa-puzzle-piece'}"></i>
        <span class="row-name"><b>${escapeHtml(widget.name)}</b><small>${escapeHtml(`${t(`web.board.types.${widget.type}`)} · ${[...widget.groups, ...widget.users].join(', ') || t('web.board.nobody')}`)}</small></span>
        <button type="button" class="tool" data-row="edit" title="${escapeHtml(t('common.edit'))}"><i class="fas fa-pen"></i></button>
        <button type="button" class="tool" data-row="delete" title="${escapeHtml(t('common.delete'))}"><i class="fas fa-trash"></i></button></li>`).join('')
      : `<li class="grid-empty">${escapeHtml(t('web.board.noLibrary'))}</li>`;
    const win = openWindow({
      title: t('web.board.widgetLibrary'),
      icon: 'fa-puzzle-piece',
      wide: true,
      body: `<p class="field-hint">${escapeHtml(t('web.board.libraryHint'))}</p><ul class="admin-rows">${rows}</ul>
        <button type="button" class="secondary" data-role="add-widget"><i class="fas fa-plus"></i> ${escapeHtml(t('web.board.addWidget'))}</button>`,
    });
    win.addEventListener('click', async (event) => {
      if (event.target.closest('[data-role="add-widget"]')) return widgetWindow(null);
      const button = event.target.closest('[data-row]');
      if (!button) return null;
      const widget = data.widgets.find((candidate) => candidate.id === button.closest('li').dataset.id);
      if (button.dataset.row === 'edit') return widgetWindow(widget);
      if (!(await confirmDialog(t, { title: t('web.board.deleteWidget'), message: t('web.board.deleteWidgetConfirm', { name: widget.name }), confirm: t('common.delete'), danger: true }))) return null;
      try {
        data.widgets = (await put('widgets', { widgets: data.widgets.filter((candidate) => candidate !== widget) })).widgets;
        await core.reload();
      } catch (error) {
        host.notify(error.message, true);
      }
      return libraryWindow();
    });
  }

  $('admin-scope').addEventListener('click', (event) => {
    const button = event.target.closest('[data-scope]');
    if (!button) return;
    if (button.dataset.scope === 'shared' && !core.shared()) enterShared().catch((error) => host.notify(error.message, true));
    else if (button.dataset.scope === 'mine') leave();
  });
  $('admin-settings').addEventListener('click', settingsWindow);
  $('admin-save').addEventListener('click', save);
  $('admin-manage').addEventListener('click', () => manageWindow().catch((error) => host.notify(error.message, true)));
  $('admin-widgets').addEventListener('click', () => libraryWindow().catch((error) => host.notify(error.message, true)));

  core.attach({ show, leave });
}
