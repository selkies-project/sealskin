/**
 * The Browser Policies tab of the admin page: named sets of browser settings
 * that installed applications start their sessions under.
 *
 * The list shows every policy with the applications that name it. The editor
 * has a tab per browser engine, drawn from the engine's catalog
 * (`GET /api/admin/browser-policies/catalog/<engine>`): the catalog's common
 * policies first, then whatever else the policy sets, and a search and a
 * group filter over every policy the engine reads. A row is a policy; ticking
 * it sets the policy and shows a control made from its schema: a select, a
 * number, a text, a list with a line per value, fields for an object's
 * properties, and JSON for anything deeper. The JSON view shows an engine's
 * part as the file a browser reads and takes a pasted one. A deprecated
 * policy is listed only while the policy sets it or once asked for.
 */

import { secureFetch } from '../lib/api.js';
import { confirmDialog } from '../lib/modal.js';
import { escapeHtml, showToast } from '../lib/dom.js';

const $ = (id) => document.getElementById(id);
const copy = (value) => JSON.parse(JSON.stringify(value));
const ENGINES = ['chromium', 'firefox'];
const MAX_RESULTS = 60;

let t;
let deps;
let wired = false;
// What the server said last: the policies and where each engine's file is mounted.
let data = { policies: [], paths: {} };
// Each engine's catalog once fetched, with its policies by name.
const catalogs = {};
// The policy in the editor and what of it is shown.
let draft = null;
let dirty = false;
let engine = 'chromium';
let query = '';
let group = '';
let onlySet = false;
let showDeprecated = false;
let jsonMode = false;
// JSON boxes holding text that does not parse, which holds a save back.
const invalid = new Set();
let searchTimer = null;

const tk = (key, vars) => t(`options.browserPolicies.${key}`, vars);

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  Object.entries(attrs).forEach(([key, value]) => {
    if (value === false || value === null || value === undefined) return;
    if (key === 'class') node.className = value;
    else if (key === 'text') node.textContent = value;
    else if (key.startsWith('on')) node.addEventListener(key.slice(2), value);
    else node.setAttribute(key, value === true ? '' : value);
  });
  children.flat().forEach((child) => {
    if (child !== null && child !== undefined && child !== false) node.append(child);
  });
  return node;
}

const isObject = (value) => value !== null && typeof value === 'object' && !Array.isArray(value);
const count = (part) => Object.keys(part || {}).length;

// --- The draft: an engine's enforced part, and Chromium's defaults ----------------

function levelOf(kind, name) {
  return kind === 'chromium' && name in draft.chromium_recommended ? 'recommended' : 'managed';
}

function partOf(kind, level) {
  if (kind === 'firefox') return draft.firefox;
  return level === 'recommended' ? draft.chromium_recommended : draft.chromium;
}

const isSet = (kind, name) => name in partOf(kind, 'managed') || (kind === 'chromium' && name in draft.chromium_recommended);
const valueOf = (kind, name) => partOf(kind, levelOf(kind, name))[name];
const setCount = (kind) => count(partOf(kind, 'managed')) + (kind === 'chromium' ? count(draft.chromium_recommended) : 0);

function setValue(kind, name, value) {
  partOf(kind, levelOf(kind, name))[name] = value;
  touched();
}

function unset(kind, name) {
  delete partOf(kind, 'managed')[name];
  if (kind === 'chromium') delete draft.chromium_recommended[name];
  touched();
}

function touched() {
  dirty = true;
  paintTabs();
}

/** What a policy holds when it is first ticked. */
function initial(definition) {
  const schema = definition.schema || {};
  if (schema.enum) return schema.enum[0];
  if (schema.type === 'boolean') return true;
  if (schema.type === 'integer' || schema.type === 'number') return schema.minimum ?? 0;
  if (schema.type === 'string') return '';
  if (schema.type === 'array') return [];
  if (schema.type === 'object' && schema.properties) return {};
  return definition.example !== undefined ? copy(definition.example) : {};
}

// --- The list --------------------------------------------------------------------

async function refresh() {
  try {
    data = await secureFetch('/api/admin/browser-policies', { method: 'GET' });
  } catch (error) {
    showToast(error.message, true);
    return;
  }
  deps.setPolicies(data.policies.map(({ id, name }) => ({ id, name })));
  renderList();
}

function renderList() {
  const tbody = document.querySelector('#bp-table tbody');
  if (!data.policies.length) {
    tbody.innerHTML = `<tr><td colspan="5" class="bp-empty">${escapeHtml(tk('none'))}</td></tr>`;
    return;
  }
  tbody.innerHTML = data.policies.map((policy) => {
    const used = policy.used_by || [];
    return `
      <tr data-id="${escapeHtml(policy.id)}">
        <td>${escapeHtml(policy.name)}</td>
        <td>${escapeHtml(tk('policyCount', { count: count(policy.chromium) + count(policy.chromium_recommended) }))}</td>
        <td>${escapeHtml(tk('policyCount', { count: count(policy.firefox) }))}</td>
        <td>${used.length ? escapeHtml(used.join(', ')) : `<span class="bp-muted">${escapeHtml(tk('unused'))}</span>`}</td>
        <td class="actions-cell">
          <button class="warning" data-act="edit">${escapeHtml(t('common.edit'))}</button>
          <button class="secondary" data-act="copy">${escapeHtml(tk('duplicate'))}</button>
          <button class="danger" data-act="delete" ${used.length ? `disabled title="${escapeHtml(tk('inUse', { names: used.join(', ') }))}"` : ''}>${used.length ? '<i class="fas fa-lock"></i> ' : ''}${escapeHtml(t('common.delete'))}</button>
        </td>
      </tr>`;
  }).join('');
}

async function onListClick(event) {
  const button = event.target.closest('button[data-act]');
  if (!button) return;
  const policy = data.policies.find((item) => item.id === button.closest('tr').dataset.id);
  if (!policy) return;
  if (button.dataset.act === 'edit') {
    await openEditor(policy);
  } else if (button.dataset.act === 'copy') {
    await openEditor({ ...policy, id: '', name: tk('copyOf', { name: policy.name }) }, true);
  } else if (button.dataset.act === 'delete') {
    const sure = await confirmDialog(t, {
      title: tk('deleteTitle'), message: tk('confirmDelete', { name: policy.name }), confirm: t('common.delete'), danger: true,
    });
    if (!sure) return;
    try {
      await secureFetch(`/api/admin/browser-policies/${policy.id}`, { method: 'DELETE' });
    } catch (error) {
      showToast(error.message, true);
    }
    await refresh();
  }
}

// --- The editor ------------------------------------------------------------------

async function loadCatalogs() {
  await Promise.all(ENGINES.filter((kind) => !catalogs[kind]).map(async (kind) => {
    const catalog = await secureFetch(`/api/admin/browser-policies/catalog/${kind}?lang=${encodeURIComponent(deps.locale || '')}`, { method: 'GET' });
    catalog.byName = {};
    catalog.policies.forEach((policy) => {
      policy.hay = `${policy.name}\n${policy.title}\n${policy.description}`.toLowerCase();
      catalog.byName[policy.name] = policy;
    });
    catalog.common = catalog.policies.filter((policy) => policy.common).sort((a, b) => a.common - b.common);
    catalogs[kind] = catalog;
  }));
}

async function openEditor(policy, changed = false) {
  try {
    await loadCatalogs();
  } catch (error) {
    showToast(error.message, true);
    return;
  }
  draft = {
    id: policy.id || '',
    name: policy.name || '',
    chromium: copy(policy.chromium || {}),
    chromium_recommended: copy(policy.chromium_recommended || {}),
    firefox: copy(policy.firefox || {}),
  };
  dirty = changed;
  engine = 'chromium';
  query = '';
  group = '';
  onlySet = false;
  jsonMode = false;
  $('bp-list').style.display = 'none';
  $('bp-editor').style.display = '';
  $('bp-editor-title').textContent = draft.id ? tk('editPolicy', { name: draft.name }) : tk('newPolicy');
  $('bp-name').value = draft.name;
  $('bp-search').value = '';
  $('bp-only-set').checked = false;
  $('bp-deprecated').checked = false;
  showDeprecated = false;
  paint();
}

async function closeEditor() {
  if (dirty) {
    const sure = await confirmDialog(t, { title: tk('discardTitle'), message: tk('confirmDiscard'), confirm: tk('discard'), danger: true });
    if (!sure) return;
  }
  draft = null;
  $('bp-editor').style.display = 'none';
  $('bp-list').style.display = '';
  await refresh();
}

async function save() {
  if (invalid.size) {
    showToast(tk('fixJson'), true);
    return;
  }
  const body = {
    name: $('bp-name').value.trim(),
    chromium: draft.chromium,
    chromium_recommended: draft.chromium_recommended,
    firefox: draft.firefox,
  };
  if (!body.name) {
    showToast(tk('nameRequired'), true);
    $('bp-name').focus();
    return;
  }
  try {
    await secureFetch(draft.id ? `/api/admin/browser-policies/${draft.id}` : '/api/admin/browser-policies', {
      method: draft.id ? 'PUT' : 'POST', body: JSON.stringify(body),
    });
  } catch (error) {
    showToast(error.message, true);
    return;
  }
  showToast(tk('saved', { name: body.name }));
  dirty = false;
  await closeEditor();
}

function paintTabs() {
  document.querySelectorAll('#bp-engines button').forEach((button) => {
    const kind = button.dataset.engine;
    button.classList.toggle('active', kind === engine);
    button.querySelector('.bp-tab-count').textContent = setCount(kind) || '';
  });
}

function paint() {
  const catalog = catalogs[engine];
  paintTabs();
  const select = $('bp-group');
  select.innerHTML = '';
  select.add(new Option(tk('allGroups'), ''));
  catalog.groups.forEach((item) => select.add(new Option(item.title, item.id)));
  select.value = group;
  $('bp-search').placeholder = tk('searchPlaceholder', { count: catalog.policies.length });
  $('bp-json').classList.toggle('active', jsonMode);
  document.querySelector('#bp-editor .bp-toolbar').classList.toggle('bp-json-on', jsonMode);

  const paths = $('bp-paths');
  paths.innerHTML = '';
  paths.append(
    el('summary', { text: tk('whereApplied') }),
    el('p', { class: 'description', text: tk(engine === 'firefox' ? 'pathsFirefox' : 'pathsChromium') }),
    el('ul', {}, (data.paths[engine] || []).map((item) => el('li', {}, `${item.browser} `, el('code', { text: item.path })))),
  );
  paintBody();
}

/** A deprecated policy stays out of the lists unless the policy sets it or it was asked for. */
const hidden = (policy) => policy.deprecated && !showDeprecated && !isSet(engine, policy.name);

function matches() {
  const catalog = catalogs[engine];
  const words = query.toLowerCase().split(/\s+/).filter(Boolean);
  const found = catalog.policies.filter((policy) => {
    if (group && policy.group !== group) return false;
    if (hidden(policy)) return false;
    return words.every((word) => policy.hay.includes(word));
  });
  const first = words[0] || '';
  const rank = (policy) => (policy.name.toLowerCase().includes(first) ? 0 : policy.title.toLowerCase().includes(first) ? 1 : 2);
  return first ? found.sort((a, b) => rank(a) - rank(b)) : found;
}

/** The policies the part holds that the catalog lacks, as definitions that take any JSON. */
function uncatalogued() {
  const catalog = catalogs[engine];
  const names = [...Object.keys(partOf(engine, 'managed')), ...(engine === 'chromium' ? Object.keys(draft.chromium_recommended) : [])];
  return names.filter((name) => !catalog.byName[name]).sort().map((name) => ({ name, title: name, description: tk('uncataloguedHelp'), schema: {}, unknown: true }));
}

function paintBody() {
  const body = $('bp-body');
  invalid.clear();
  body.innerHTML = '';
  if (jsonMode) {
    body.append(jsonView());
    return;
  }
  const catalog = catalogs[engine];
  const keep = (list) => (onlySet ? list.filter((policy) => isSet(engine, policy.name)) : list);
  const section = (title, list, note) => {
    if (!list.length && !note) return;
    body.append(el('h4', { class: 'bp-section', text: title }));
    if (note) body.append(el('p', { class: 'description', text: note }));
    list.forEach((policy) => body.append(row(policy)));
  };
  if (query || group) {
    const found = keep(matches());
    section(tk('results', { count: found.length }), found.slice(0, MAX_RESULTS), found.length ? '' : tk('noResults'));
    if (found.length > MAX_RESULTS) body.append(el('p', { class: 'description', text: tk('moreResults', { count: found.length - MAX_RESULTS }) }));
    return;
  }
  const others = [
    ...catalog.policies.filter((policy) => !policy.common && isSet(engine, policy.name)),
    ...uncatalogued(),
  ];
  section(tk('common'), keep(catalog.common.filter((policy) => !hidden(policy))), tk('commonHelp'));
  section(tk('others', { count: others.length }), others, others.length ? '' : tk('othersHelp', { count: catalog.policies.length }));
}

function row(definition) {
  const kind = engine;
  const set = isSet(kind, definition.name);
  const badges = [];
  if (definition.unknown) badges.push(el('span', { class: 'pill off', text: tk('uncatalogued') }));
  if (definition.deprecated) badges.push(el('span', { class: 'pill off', text: tk('deprecated') }));
  if (definition.since) badges.push(el('span', { class: 'pill', text: tk(kind === 'firefox' ? 'sinceFirefox' : 'sinceChromium', { version: definition.since }) }));
  if (definition.restart) badges.push(el('span', { class: 'pill', text: tk('restart') }));
  const description = el('p', {
    class: 'bp-desc',
    text: definition.description,
    title: tk('showAll'),
    onclick: (event) => event.currentTarget.classList.toggle('open'),
  });
  const box = el('input', {
    type: 'checkbox',
    checked: set,
    'aria-label': definition.name,
    onchange: (event) => {
      if (event.target.checked) setValue(kind, definition.name, initial(definition));
      else unset(kind, definition.name);
      node.replaceWith(row(definition));
    },
  });
  box.checked = set;
  const named = definition.title && definition.title !== definition.name;
  const head = el('div', { class: 'bp-row-head' },
    el('label', { class: 'bp-set' }, box),
    el('div', { class: 'bp-row-text' },
      el('div', { class: 'bp-row-title' }, named ? el('strong', { text: definition.title }) : null, el('code', { text: definition.name }), badges),
      description,
    ),
  );
  const node = el('div', { class: `bp-row${set ? ' set' : ''}` }, head);
  if (!set) return node;

  const bodyNode = el('div', { class: 'bp-row-body' });
  const draw = () => {
    bodyNode.innerHTML = '';
    bodyNode.append(control(definition.schema || {}, valueOf(kind, definition.name), (value) => setValue(kind, definition.name, value), false, 0, definition.name));
    const extras = el('div', { class: 'bp-extras' });
    if (kind === 'chromium' && (definition.recommended || levelOf(kind, definition.name) === 'recommended')) {
      const soft = el('input', {
        type: 'checkbox',
        onchange: (event) => {
          const value = valueOf(kind, definition.name);
          unset(kind, definition.name);
          partOf(kind, event.target.checked ? 'recommended' : 'managed')[definition.name] = value;
          touched();
        },
      });
      soft.checked = levelOf(kind, definition.name) === 'recommended';
      extras.append(el('label', { class: 'bk-check', title: tk('userMayChangeHelp') }, soft, tk('userMayChange')));
    }
    if (definition.example !== undefined && !['boolean', 'integer', 'number'].includes((definition.schema || {}).type) && !(definition.schema || {}).enum) {
      extras.append(el('button', {
        type: 'button',
        class: 'secondary',
        text: tk('useExample'),
        onclick: () => {
          setValue(kind, definition.name, copy(definition.example));
          draw();
        },
      }));
    }
    if (extras.children.length) bodyNode.append(extras);
  };
  draw();
  node.append(bodyNode);
  return node;
}

// --- Controls, made from a schema ------------------------------------------------

/**
 * Make the control of a value.
 *
 * @param {object} schema The catalog's schema of the value.
 * @param {*} value What it holds.
 * @param {function} change Called with the new value; `undefined` leaves an optional value out.
 * @param {boolean} optional A property of an object, which an empty control leaves out.
 * @param {number} depth How many objects the control is inside.
 * @param {string} key What names the control among the JSON boxes.
 */
function control(schema, value, change, optional, depth, key) {
  if (schema.enum) return choice(schema.enum, schema['x-labels'], value, change, optional);
  if (schema.type === 'boolean') return choice([true, false], [tk('enabled'), tk('disabled')], value, change, optional);
  if (schema.type === 'integer' || schema.type === 'number') {
    return el('input', {
      type: 'number',
      value: value ?? '',
      min: schema.minimum,
      max: schema.maximum,
      step: schema.type === 'integer' ? 1 : 'any',
      oninput: (event) => {
        const text = event.target.value;
        if (text === '') change(optional ? undefined : 0);
        else change(Number(text));
      },
    });
  }
  if (schema.type === 'string') {
    return el('input', {
      type: 'text',
      value: typeof value === 'string' ? value : '',
      spellcheck: 'false',
      placeholder: schema.format === 'url' ? 'https://' : null,
      oninput: (event) => change(optional && !event.target.value ? undefined : event.target.value),
    });
  }
  const items = schema.items || {};
  if (schema.type === 'array' && items.enum) return checks(items, Array.isArray(value) ? value : [], change, optional);
  if (schema.type === 'array' && items.type === 'string') {
    const lines = Array.isArray(value) ? value.filter((item) => typeof item === 'string') : [];
    const area = el('textarea', {
      rows: Math.min(10, Math.max(3, lines.length + 1)),
      spellcheck: 'false',
      placeholder: tk('onePerLine'),
      oninput: (event) => {
        const list = event.target.value.split('\n').map((line) => line.trim()).filter(Boolean);
        change(optional && !list.length ? undefined : list);
      },
    });
    area.value = lines.join('\n');
    return area;
  }
  if (schema.type === 'object' && schema.properties && depth < 2 && (value === undefined || isObject(value))) {
    return fields(schema, isObject(value) ? value : {}, change, optional, depth, key);
  }
  return json(value, change, optional, key);
}

function choice(values, labels, value, change, optional) {
  const select = el('select', { onchange: (event) => change(event.target.value === '' ? undefined : JSON.parse(event.target.value)) });
  if (optional) select.add(new Option(tk('notSet'), ''));
  values.forEach((item, index) => {
    const label = labels && labels[index] && String(labels[index]) !== String(item) ? `${labels[index]} (${item})` : String((labels && labels[index]) || item);
    select.add(new Option(label, JSON.stringify(item)));
  });
  const held = JSON.stringify(value);
  if (value !== undefined && !values.some((item) => JSON.stringify(item) === held)) select.add(new Option(String(value), held));
  select.value = value === undefined ? '' : held;
  return select;
}

function checks(items, value, change, optional) {
  const picked = new Set(value);
  return el('div', { class: 'bp-checks' }, items.enum.map((item, index) => {
    const box = el('input', {
      type: 'checkbox',
      onchange: (event) => {
        if (event.target.checked) picked.add(item);
        else picked.delete(item);
        const list = items.enum.filter((entry) => picked.has(entry));
        change(optional && !list.length ? undefined : list);
      },
    });
    box.checked = picked.has(item);
    return el('label', { class: 'bk-check' }, box, String((items['x-labels'] || [])[index] || item));
  }));
}

function fields(schema, value, change, optional, depth, key) {
  const held = { ...value };
  const report = () => change(optional && !Object.keys(held).length ? undefined : { ...held });
  const required = new Set(schema.required || []);
  const known = Object.entries(schema.properties).map(([name, sub]) => el('div', { class: 'bp-field' },
    el('div', { class: 'bp-field-name' }, el('code', { text: name }), required.has(name) ? el('em', { class: 'bk-required', text: ' *' }) : null,
      sub.description ? el('small', { text: sub.description }) : null),
    control(sub, held[name], (next) => {
      if (next === undefined) delete held[name];
      else held[name] = next;
      report();
    }, true, depth + 1, `${key}.${name}`),
  ));
  // Properties the schema does not name stay in the value untouched.
  const extra = Object.keys(held).filter((name) => !(name in schema.properties));
  const note = extra.length ? el('p', { class: 'description', text: tk('extraProperties', { names: extra.join(', ') }) }) : null;
  return el('div', { class: 'bp-fields' }, known, note);
}

function json(value, change, optional, key) {
  const area = el('textarea', {
    class: 'bp-json',
    rows: 6,
    spellcheck: 'false',
    placeholder: tk('jsonPlaceholder'),
    oninput: (event) => {
      const text = event.target.value.trim();
      if (!text && optional) {
        invalid.delete(key);
        event.target.classList.remove('invalid');
        change(undefined);
        return;
      }
      try {
        change(JSON.parse(text));
        invalid.delete(key);
        event.target.classList.remove('invalid');
      } catch {
        invalid.add(key);
        event.target.classList.add('invalid');
      }
    },
  });
  area.value = value === undefined ? '' : JSON.stringify(value, null, 2);
  area.rows = Math.min(16, Math.max(4, area.value.split('\n').length + 1));
  return area;
}

// --- The JSON view: an engine's part as the file a browser reads -------------------

function jsonView() {
  const boxes = engine === 'firefox'
    ? [{ label: tk('jsonFirefox'), read: () => ({ policies: draft.firefox }), write: (value) => { draft.firefox = isObject(value.policies) ? value.policies : value; } }]
    : [
      { label: tk('jsonManaged'), read: () => draft.chromium, write: (value) => { draft.chromium = value; } },
      { label: tk('jsonRecommended'), read: () => draft.chromium_recommended, write: (value) => { draft.chromium_recommended = value; } },
    ];
  const areas = boxes.map((box) => {
    const area = el('textarea', { class: 'bp-json', rows: 14, spellcheck: 'false' });
    area.value = JSON.stringify(box.read(), null, 2);
    return area;
  });
  const apply = el('button', {
    type: 'button',
    class: 'primary',
    text: tk('applyJson'),
    onclick: () => {
      const parsed = [];
      for (const area of areas) {
        try {
          const value = JSON.parse(area.value.trim() || '{}');
          if (!isObject(value)) throw new Error('not an object');
          parsed.push(value);
          area.classList.remove('invalid');
        } catch {
          area.classList.add('invalid');
          showToast(tk('jsonInvalid'), true);
          return;
        }
      }
      boxes.forEach((box, index) => box.write(parsed[index]));
      if (engine === 'chromium') Object.keys(draft.chromium_recommended).forEach((name) => delete draft.chromium[name]);
      jsonMode = false;
      touched();
      paint();
    },
  });
  return el('div', { class: 'bp-json-view' },
    el('p', { class: 'description', text: tk('jsonHelp') }),
    boxes.map((box, index) => el('label', { class: 'bk-field' }, el('span', { text: box.label }), areas[index])),
    el('div', { class: 'button-group' }, apply),
  );
}

// --- Wiring ----------------------------------------------------------------------

function wire() {
  wired = true;
  $('bp-new').addEventListener('click', () => openEditor({}));
  document.querySelector('#bp-table tbody').addEventListener('click', onListClick);
  $('bp-cancel').addEventListener('click', closeEditor);
  $('bp-save').addEventListener('click', save);
  $('bp-name').addEventListener('input', () => { dirty = true; });
  $('bp-engines').addEventListener('click', (event) => {
    const button = event.target.closest('button[data-engine]');
    if (!button || button.dataset.engine === engine) return;
    if (invalid.size) {
      showToast(tk('fixJson'), true);
      return;
    }
    engine = button.dataset.engine;
    group = '';
    paint();
  });
  $('bp-search').addEventListener('input', (event) => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => {
      query = event.target.value.trim();
      paintBody();
    }, 150);
  });
  $('bp-group').addEventListener('change', (event) => {
    group = event.target.value;
    paintBody();
  });
  $('bp-only-set').addEventListener('change', (event) => {
    onlySet = event.target.checked;
    paintBody();
  });
  $('bp-deprecated').addEventListener('change', (event) => {
    showDeprecated = event.target.checked;
    paintBody();
  });
  $('bp-json').addEventListener('click', () => {
    if (invalid.size) {
      showToast(tk('fixJson'), true);
      return;
    }
    jsonMode = !jsonMode;
    paint();
  });
}

/**
 * Show the tab.
 *
 * @param {{t: function, locale: string, setPolicies: function}} options The translator, the
 *   locale the catalogs are asked for in, and what takes the policies' names for the
 *   installed application form.
 */
export async function openBrowserPolicies(options) {
  t = options.t;
  deps = options;
  if (!wired) wire();
  if (!draft) await refresh();
}
