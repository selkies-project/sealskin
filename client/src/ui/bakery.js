/**
 * The Bakery tab of the admin page: recipes that build a layer on an
 * application's image.
 *
 * The list shows every recipe with the state of this node's image. The
 * editor is a bench of two panes: on the left the steps of a guided recipe,
 * each a small form, and on the right the Dockerfile the server renders from
 * them as they change (`POST /api/admin/bakery/preview/<app>`), each step's
 * lines lit while the step is open or pointed at. The same answer says what
 * the steps offer on the image (`offers`): the applications, font sets,
 * toolchains, and package sets of its distribution, and the kinds it hides. Developer mode swaps the
 * right pane for the text itself, and the left for the recipe's files and a
 * snippet builder that renders one step and puts it at the cursor.
 *
 * A build of the draft (`POST /api/admin/bakery/builds/<app>`) changes
 * nothing users run; its output is followed line by line and its image can
 * be opened in a trial session. Saving keeps the recipe and goes back to the
 * list, where the bake is followed to its end.
 */

import { request } from '../lib/bridge.js';
import { secureFetch, uploadInChunks } from '../lib/api.js';
import { postLaunch } from '../lib/launch-answer.js';
import { confirmDialog } from '../lib/modal.js';
import { browserTimezone } from '../lib/timezone.js';
import { escapeHtml, formatBytes, formatLogoSrc, timeAgo, showToast } from '../lib/dom.js';

const $ = (id) => document.getElementById(id);
const copy = (value) => JSON.parse(JSON.stringify(value));
const newId = () => crypto.randomUUID().replace(/-/g, '').slice(0, 8);
// Every kind of step a recipe may hold. The menus offer the ones in a group; the rest are a developer's own lines.
const KINDS = [
  { kind: 'packages', icon: 'fa-cubes', group: 'software' },
  { kind: 'app', icon: 'fa-store', group: 'software' },
  { kind: 'ppa', icon: 'fa-box-archive', group: 'software' },
  { kind: 'download', icon: 'fa-cloud-arrow-down', group: 'software' },
  { kind: 'devkit', icon: 'fa-laptop-code', group: 'software' },
  { kind: 'docker', icon: 'fa-layer-group', group: 'software' },
  { kind: 'desktop', icon: 'fa-table-cells-large', group: 'desktop' },
  { kind: 'launch', icon: 'fa-play', group: 'desktop' },
  { kind: 'fonts', icon: 'fa-font', group: 'desktop' },
  { kind: 'files', icon: 'fa-file-import', group: 'system' },
  { kind: 'certificates', icon: 'fa-certificate', group: 'system' },
  { kind: 'env', icon: 'fa-sliders', group: 'system' },
  { kind: 'proxy', icon: 'fa-network-wired', group: 'system' },
  { kind: 'startup', icon: 'fa-power-off', group: 'system' },
  { kind: 'run', icon: 'fa-terminal' },
  { kind: 'raw', icon: 'fa-code' },
];
const GROUPS = ['software', 'desktop', 'system'];
// What a new step of each kind starts with, beside the fields every step has.
const OPTIONS = {
  app: { app: '', menu: true, default: true },
  ppa: { ppas: [] },
  desktop: { name: '', command: '', icon: '', category: 'Utility', sandbox: false, terminal: false, menu: true },
  fonts: { packs: [], eula: false },
  proxy: { proxy: '', no_proxy: '', pip: '', npm: '' },
  docker: { version: '', compose: true, buildx: true },
  devkit: { kit: '' },
  launch: { placement: 'replace' },
  startup: { mode: 'once', name: '' },
};
const INSTRUCTION = /^(\s*)(FROM|RUN|COPY|ADD|ENV|ARG|LABEL|USER|WORKDIR|EXPOSE|VOLUME|ENTRYPOINT|CMD|SHELL|HEALTHCHECK|STOPSIGNAL|ONBUILD)\b/i;

let t;
let deps;
let wired = false;
// What the server said last: the recipes, whether this node builds, and the families.
let data = { available: true, recipes: [], families: {} };
// The recipe in the editor, the base image of its application, and its last rendering.
let draft = null;
let base = null;
let preview = null;
let offers = null;
let openStep = null;
// The step a developer fills in to insert, and the build the panel follows.
let snippet = null;
let build = null;
let previewTimer = null;
let previewRun = 0;
let buildTimer = null;
let listTimer = null;
let searchTimer = null;

const tk = (key, vars) => t(`options.bakery.${key}`, vars);
const appsList = () => deps.apps();
const appOf = (id) => appsList().find((app) => app.id === id);
/** The application a draft renders and builds against: its own, or the one a new application is built on. */
const targetApp = () => (draft ? draft.app_id || draft.base_app_id : '');

function blankStep(kind) {
  return {
    id: newId(), kind, note: '', enabled: true, packages: [], files: [], dest: kind === 'files' ? '/usr/local/bin/' : '',
    executable: false, extract: false, url: '', sha256: '', action: 'save', env: kind === 'env' ? [{ name: '', value: '' }] : [], script: '', text: '',
    options: copy(OPTIONS[kind] || {}),
  };
}

function recipeBody() {
  return {
    mode: draft.mode, family: draft.family, steps: draft.steps, dockerfile: draft.dockerfile, files: draft.files, enabled: draft.enabled,
  };
}

// --- The list ----------------------------------------------------------------

/** A recipe's image on this node, and under it on every other node that answered. */
function statusCell(recipe) {
  const elsewhere = (data.others || []).map((other) => {
    const status = other.status[recipe.app_id] || { state: 'pending' };
    const label = { building: 'stateBuilding', error: 'stateError', ready: 'stateReady' }[status.state] || 'statePending';
    return `<div class="bk-sub bk-node${status.state === 'error' ? ' error' : ''}" title="${escapeHtml(status.error || '')}">${escapeHtml(other.node)} · ${escapeHtml(tk(label))}</div>`;
  }).join('');
  return `${ownStatus(recipe)}${elsewhere}`;
}

function ownStatus(recipe) {
  const { status } = recipe;
  if (!recipe.enabled) return `<span class="pill">${escapeHtml(tk('statePaused'))}</span>`;
  if (status.state === 'building') return `<span class="proot-state"><span class="spinner-small"></span>${escapeHtml(tk('stateBuilding'))}</span>`;
  if (status.state === 'error') {
    const kept = status.image ? ` ${tk('stateErrorKept')}` : '';
    return `<span class="pill off" title="${escapeHtml(status.error)}">${escapeHtml(tk('stateError'))}</span><span class="bk-sub">${escapeHtml(kept)}</span>`;
  }
  if (status.state === 'ready') return `<span class="pill active">${escapeHtml(tk('stateReady'))}</span><span class="bk-sub"> ${escapeHtml(timeAgo(status.built_at, t))}</span>`;
  return `<span class="pill">${escapeHtml(tk('statePending'))}</span>`;
}

/** What a recipe's application is built on: the application a meta-app was made from, and the image. */
function builtOn(recipe) {
  const app = appOf(recipe.app_id);
  const from = app && app.base_app_id ? appOf(app.base_app_id) : null;
  return from ? tk('listBuiltOn', { name: from.name, image: recipe.image }) : recipe.image;
}

function renderList() {
  $('bk-unavailable').style.display = data.available ? 'none' : '';
  $('bk-new').disabled = !data.available;
  const body = document.querySelector('#bk-table tbody');
  if (!data.recipes.length) {
    body.innerHTML = `<tr class="empty-row"><td colspan="4" style="text-align:center; padding: 2rem;">${escapeHtml(tk('none'))}</td></tr>`;
    return;
  }
  body.innerHTML = data.recipes.map((recipe) => {
    const what = recipe.mode === 'dockerfile' ? tk('listDockerfile') : tk('listSteps', { count: recipe.steps.filter((step) => step.enabled).length });
    return `
      <tr data-app="${escapeHtml(recipe.app_id)}">
        <td><strong>${escapeHtml(recipe.app_name || recipe.app_id)}</strong><div class="bk-sub">${escapeHtml(builtOn(recipe))}</div></td>
        <td>${escapeHtml(what)}</td>
        <td>${statusCell(recipe)}</td>
        <td class="actions-cell">
          <button type="button" class="warning" data-act="edit">${escapeHtml(t('common.edit'))}</button>
          <button type="button" class="secondary" data-act="log" title="${escapeHtml(tk('logTitle'))}"><i class="fas fa-terminal"></i></button>
          <button type="button" class="secondary" data-act="rebuild" title="${escapeHtml(tk('rebuildTitle'))}"><i class="fas fa-rotate"></i></button>
          <button type="button" class="danger" data-act="delete" title="${escapeHtml(t('common.delete'))}"><i class="fas fa-trash"></i></button>
        </td>
      </tr>`;
  }).join('');
}

async function refresh() {
  try {
    data = await secureFetch('/api/admin/bakery', { method: 'GET' });
  } catch (error) {
    showToast(error.message, true);
    return;
  }
  renderList();
  clearTimeout(listTimer);
  const visible = $('Bakery').classList.contains('active');
  if (visible && data.recipes.some((recipe) => recipe.status.state === 'building')) listTimer = setTimeout(refresh, 4000);
}

async function onListClick(event) {
  const button = event.target.closest('button[data-act]');
  if (!button) return;
  const recipe = data.recipes.find((item) => item.app_id === button.closest('tr').dataset.app);
  if (!recipe) return;
  if (button.dataset.act === 'edit') {
    await openEditor(recipe);
  } else if (button.dataset.act === 'log') {
    await showRecipeBuild(recipe);
  } else if (button.dataset.act === 'rebuild') {
    try {
      const started = await secureFetch(`/api/admin/bakery/recipes/${recipe.app_id}/build?fresh=true`, { method: 'POST' });
      await refresh();
      watchBuild(started, { list: true, name: recipe.app_name || recipe.app_id, image: recipe.image });
    } catch (error) {
      showToast(error.message, true);
    }
  } else if (button.dataset.act === 'delete') {
    const app = appOf(recipe.app_id);
    const sure = await confirmDialog(t, {
      title: tk('deleteTitle'), message: tk(app && app.is_meta_app ? 'confirmDeleteApp' : 'confirmDelete', { name: recipe.app_name || recipe.app_id }), confirm: t('common.delete'), danger: true,
    });
    if (!sure) return;
    try {
      await secureFetch(`/api/admin/bakery/recipes/${recipe.app_id}`, { method: 'DELETE' });
      await deps.refreshApps();
      if (build && build.list && build.name === (recipe.app_name || recipe.app_id)) $('bk-panel-close').click();
      await refresh();
    } catch (error) {
      showToast(error.message, true);
    }
  }
}

// --- The editor: the bar ---------------------------------------------------------

function fillAppSelects() {
  const apps = appsList();
  const own = appOf(draft.app_id);
  const shown = draft.existing ? ((own && own.base_app_id) || draft.app_id) : draft.base_app_id;
  // A new recipe builds on an application as its store gives it, never on one the laboratory or the Bakery made.
  const bases = apps.filter((app) => !app.is_meta_app || (draft.existing && app.id === shown));
  $('bk-base-app').innerHTML = `<option value="">${escapeHtml(tk('pickBase'))}</option>${bases.map((app) => `<option value="${escapeHtml(app.id)}">${escapeHtml(app.name)}</option>`).join('')}`;
  $('bk-base-app').value = shown || '';
  $('bk-base-app').disabled = draft.existing;
  $('bk-family').innerHTML = `<option value="">${escapeHtml(tk('familyAuto'))}</option>${Object.entries(data.families).map(([key, manager]) => `<option value="${key}">${key} (${manager})</option>`).join('')}`;
  $('bk-family').value = draft.family;
}

function renderBar() {
  const own = appOf(draft.app_id);
  const fresh = !draft.existing;
  // A name is the new application's, or a baked meta-app's own; an application baked in place keeps its name.
  const named = fresh || Boolean(draft.existing && own && own.is_meta_app);
  $('bk-name').disabled = !named;
  const name = draft.name ?? (own ? own.name : '');
  if ($('bk-name').value !== name) $('bk-name').value = name;
  $('bk-icon-field').style.display = named ? '' : 'none';
  const shown = draft.existing ? own : appOf(draft.base_app_id);
  if (draft.icon) $('bk-icon-preview').src = draft.icon;
  else if (shown && shown.logo) formatLogoSrc(shown.logo).then((src) => { if (!draft || !draft.icon) $('bk-icon-preview').src = src; });
  else $('bk-icon-preview').src = 'icons/icon128.png';
  $('bk-mode').querySelectorAll('button').forEach((button) => button.classList.toggle('active', button.dataset.mode === draft.mode));
  const target = targetApp();
  let text = tk('baseNone');
  if (target && base && base.image) {
    const distro = base.distribution || tk('baseUnknown');
    text = [distro, base.manager, base.image].filter(Boolean).join(' · ');
  } else if (target) {
    text = tk('baseReading');
  }
  $('bk-base').textContent = text;
  $('bk-base').classList.toggle('warn', Boolean(target && base && base.image && !base.family && !draft.family));
  $('bk-save').disabled = $('bk-try').disabled = !target;
}

async function loadBase() {
  const target = targetApp();
  base = null;
  offers = null;
  renderMenus();
  renderBar();
  if (!target) return;
  try {
    const found = await secureFetch(`/api/admin/bakery/base/${target}`, { method: 'GET' });
    if (target === targetApp()) base = found;
  } catch (error) {
    showToast(error.message, true);
  }
  renderBar();
  schedulePreview(0);
}

// --- The editor: steps -------------------------------------------------------------

const opt = (step, name, fallback = '') => (step.options || {})[name] ?? fallback;
/** The list a row of chips edits: the step's packages, or a list among its options. */
function listOf(step, list) {
  if (list === 'packages') return step.packages;
  step.options = step.options || {};
  if (!Array.isArray(step.options[list])) step.options[list] = [];
  return step.options[list];
}
const firstLine = (value) => value.split('\n').find((line) => line.trim()) || '';

function summary(step) {
  const also = (...parts) => parts.flat().filter(Boolean).join(', ');
  switch (step.kind) {
    case 'packages': return step.packages.join(', ');
    case 'app': return ((offers && offers.apps[opt(step, 'app')]) || {}).name || opt(step, 'app');
    case 'ppa': return also(listOf(step, 'ppas'), step.packages);
    case 'files': return step.files.length ? `${step.files.join(', ')} → ${step.dest}` : '';
    case 'download': return step.url;
    case 'desktop': return opt(step, 'name') && opt(step, 'command') ? `${opt(step, 'name')} → ${opt(step, 'command')}` : '';
    case 'certificates': return step.files.join(', ');
    case 'fonts': return also(listOf(step, 'packs').map((name) => tk(`fontPacks.${name}`)), step.files);
    case 'env': return step.env.filter((item) => item.name).map((item) => `${item.name}=${item.value}`).join(', ');
    case 'proxy': return also(opt(step, 'proxy'), opt(step, 'pip'), opt(step, 'npm'));
    case 'docker': return opt(step, 'version') || tk('latest');
    case 'devkit': return opt(step, 'kit') ? also(tk(`devkits.${opt(step, 'kit')}`), step.packages) : '';
    case 'launch': case 'startup': case 'run': return firstLine(step.script);
    default: return firstLine(step.text);
  }
}

const field = (label, control, help = '') => `<label class="bk-field"><span>${escapeHtml(label)}</span>${control}${help ? `<small>${escapeHtml(help)}</small>` : ''}</label>`;
// A field whose control is more than one input, which a label would tie to the first.
const block = (label, control, help = '') => `<div class="bk-field"><span>${escapeHtml(label)}</span>${control}${help ? `<small>${escapeHtml(help)}</small>` : ''}</div>`;
const check = (name, label, value) => `<label class="bk-check"><input type="checkbox" data-field="${name}" ${value ? 'checked' : ''}> ${escapeHtml(label)}</label>`;
const text = (name, value, placeholder = '') => `<input type="text" data-field="${name}" value="${escapeHtml(value)}" placeholder="${escapeHtml(placeholder)}" spellcheck="false">`;
const chip = (label, attr) => `<span class="chip">${escapeHtml(label)}<button type="button" ${attr} title="${escapeHtml(t('common.remove'))}">&times;</button></span>`;
const optText = (step, name, placeholder = '') => `<input type="text" data-opt="${name}" value="${escapeHtml(opt(step, name))}" placeholder="${escapeHtml(placeholder)}" spellcheck="false">`;
const optCheck = (step, name, label, fallback = false) => `<label class="bk-check"><input type="checkbox" data-opt="${name}" ${opt(step, name, fallback) ? 'checked' : ''}> ${escapeHtml(label)}</label>`;
const optSelect = (step, name, choices, blank = '') => `<select data-opt="${name}" data-redraw>${blank ? `<option value="">${escapeHtml(blank)}</option>` : ''}${choices.map(([value, label]) => `<option value="${escapeHtml(value)}" ${opt(step, name) === value ? 'selected' : ''}>${escapeHtml(label)}</option>`).join('')}</select>`;
const scriptArea = (step, placeholder) => `<textarea data-field="script" rows="6" spellcheck="false" placeholder="${escapeHtml(placeholder)}">${escapeHtml(step.script)}</textarea>`;

/** A list edited as chips; `search` looks what is typed up among the image's packages. */
function chips(step, list, placeholder, search = false) {
  const items = listOf(step, list).map((name) => chip(name, `data-drop-chip="${list}" data-value="${escapeHtml(name)}"`)).join('');
  return `
    <div class="bk-chips">${items}
      <input type="text" class="bk-package-input" data-chip-input="${list}" ${search ? 'data-search' : ''} placeholder="${escapeHtml(placeholder)}" spellcheck="false" autocomplete="off"></div>
    ${search ? '<div class="bk-results" data-results></div>' : ''}`;
}

function filesForm(step, single = false) {
  const spare = draft.files.filter((file) => !step.files.includes(file.name));
  return `
    <div class="bk-chips">${step.files.map((name) => chip(name, `data-drop-file="${escapeHtml(name)}"`)).join('') || `<span class="bk-sub">${escapeHtml(tk('noFiles'))}</span>`}</div>
    <div class="bk-row">
      <label class="button secondary"><i class="fas fa-upload"></i> ${escapeHtml(tk('upload'))}<input type="file" ${single ? '' : 'multiple'} data-upload hidden></label>
      ${spare.length ? `<select data-pick-file><option value="">${escapeHtml(tk('pickFile'))}</option>${spare.map((file) => `<option>${escapeHtml(file.name)}</option>`).join('')}</select>` : ''}
    </div>`;
}

function stepForm(step) {
  const k = (name) => tk(`fields.${name}`);
  const offered = offers || { bundles: {}, fonts: {}, devkits: {}, apps: {}, categories: [] };
  let body = '';
  if (step.kind === 'packages') {
    const bundles = Object.keys(offered.bundles).map((name) => `<button type="button" class="secondary" data-bundle="${name}">${escapeHtml(tk(`bundles.${name}`))}</button>`).join('');
    body = chips(step, 'packages', tk('packagePlaceholder'), true)
      + (bundles ? `<div class="bk-row bk-bundles"><span class="bk-sub">${escapeHtml(tk('bundles.label'))}</span>${bundles}</div>` : '');
  } else if (step.kind === 'app') {
    const chosen = offered.apps[opt(step, 'app')];
    body = field(k('app'), optSelect(step, 'app', Object.entries(offered.apps).map(([id, app]) => [id, app.name]), tk('pickApp')))
      + `<div class="bk-row">${optCheck(step, 'menu', k('menu'), true)}${chosen && chosen.browser ? optCheck(step, 'default', k('default'), true) : ''}</div>`
      + `<small class="bk-sub">${escapeHtml(tk('menuHelp'))}</small>`;
  } else if (step.kind === 'ppa') {
    body = block(k('ppas'), chips(step, 'ppas', 'owner/name'))
      + block(k('packages'), chips(step, 'packages', tk('packagePlaceholder'), true));
  } else if (step.kind === 'files') {
    body = filesForm(step)
      + field(k('dest'), text('dest', step.dest, '/usr/local/bin/'), tk('destHelp'))
      + `<div class="bk-row">${check('executable', k('executable'), step.executable)}${check('extract', k('extract'), step.extract)}</div>`;
  } else if (step.kind === 'download') {
    const actions = ['save', 'extract', 'appimage', 'install'].map((action) => `<option value="${action}" ${step.action === action ? 'selected' : ''}>${escapeHtml(tk(`actions.${action}`))}</option>`).join('');
    const place = { save: '/usr/local/bin/tool', extract: '/opt/tool/', appimage: '/opt/application' }[step.action];
    body = field(k('url'), text('url', step.url, 'https://'))
      + field(k('action'), `<select data-field="action" data-redraw>${actions}</select>`, step.action === 'appimage' ? tk('appimageHelp') : '')
      + (step.action === 'install' ? '' : field(k('dest'), text('dest', step.dest, place)))
      + field(k('sha256'), text('sha256', step.sha256), tk('sha256Help'))
      + (step.action === 'save' ? check('executable', k('executable'), step.executable) : '');
  } else if (step.kind === 'desktop') {
    body = field(k('name'), optText(step, 'name'))
      + field(k('command'), optText(step, 'command', '/opt/application/AppRun'))
      + block(k('icon'), filesForm(step, true) + (step.files.length ? '' : optText(step, 'icon', tk('iconPlaceholder'))))
      + field(k('category'), optSelect(step, 'category', offered.categories.map((name) => [name, tk(`categories.${name}`)])))
      + `<div class="bk-checks">${optCheck(step, 'sandbox', k('sandbox'))}${optCheck(step, 'terminal', k('terminal'))}${optCheck(step, 'menu', k('menu'), true)}</div>`
      + `<small class="bk-sub">${escapeHtml(tk('menuHelp'))}</small>`;
  } else if (step.kind === 'certificates') {
    body = filesForm(step);
  } else if (step.kind === 'fonts') {
    const chosen = listOf(step, 'packs');
    const packs = Object.keys(offered.fonts).map((name) => `<label class="bk-check"><input type="checkbox" data-member="packs" value="${name}" ${chosen.includes(name) ? 'checked' : ''}> ${escapeHtml(tk(`fontPacks.${name}`))}</label>`).join('');
    const license = chosen.some((name) => offered.fonts[name]) ? optCheck(step, 'eula', k('eula')) : '';
    body = (packs ? block(k('packs'), `<div class="bk-checks">${packs}${license}</div>`) : '') + block(k('fontFiles'), filesForm(step));
  } else if (step.kind === 'env') {
    body = step.env.map((item, index) => `
      <div class="bk-row bk-env" data-env="${index}">
        <input type="text" data-env-field="name" value="${escapeHtml(item.name)}" placeholder="NAME" spellcheck="false">
        <input type="text" data-env-field="value" value="${escapeHtml(item.value)}" placeholder="${escapeHtml(k('value'))}" spellcheck="false">
        <button type="button" class="secondary" data-drop-env="${index}" title="${escapeHtml(t('common.remove'))}">&times;</button>
      </div>`).join('') + `<button type="button" class="secondary" data-add-env><i class="fas fa-plus"></i> ${escapeHtml(tk('addVariable'))}</button>`;
  } else if (step.kind === 'proxy') {
    body = field(k('proxy'), optText(step, 'proxy', 'http://proxy.example.com:3128'))
      + field(k('no_proxy'), optText(step, 'no_proxy', 'intranet.example.com, 10.0.0.0/8'), tk('noProxyHelp'))
      + field(k('pip'), optText(step, 'pip', 'https://'))
      + field(k('npm'), optText(step, 'npm', 'https://'));
  } else if (step.kind === 'docker') {
    body = field(k('version'), optText(step, 'version', tk('latest')), tk('versionHelp'))
      + `<div class="bk-row">${optCheck(step, 'compose', 'Compose', true)}${optCheck(step, 'buildx', 'Buildx', true)}</div>`;
  } else if (step.kind === 'devkit') {
    const kit = opt(step, 'kit');
    body = field(k('kit'), optSelect(step, 'kit', Object.keys(offered.devkits).map((name) => [name, tk(`devkits.${name}`)]), tk('pickKit')))
      + (offered.devkits[kit] ? block(tk(`devkitPackages.${kit}`), chips(step, 'packages', tk('devkitPlaceholder')), tk('devkitHelp')) : '');
  } else if (step.kind === 'launch') {
    body = field(k('placement'), optSelect(step, 'placement', ['replace', 'before'].map((name) => [name, tk(`placements.${name}`)])))
      + field(k('script'), scriptArea(step, 'application --flag'), tk('launchHelp'));
  } else if (step.kind === 'startup') {
    body = field(k('mode'), optSelect(step, 'mode', ['once', 'service'].map((name) => [name, tk(`startModes.${name}`)])))
      + field(k('scriptName'), optText(step, 'name', 'prepare'))
      + field(k('script'), scriptArea(step, 'mkdir -p /data/cache'), tk(`startHelp.${opt(step, 'mode', 'once')}`));
  } else if (step.kind === 'run') {
    body = field(k('script'), scriptArea(step, "sed -i 's/old/new/' /etc/example.conf"), tk('scriptHelp'));
  } else {
    body = field(k('text'), `<textarea data-field="text" rows="6" spellcheck="false" placeholder="LABEL maintainer=&quot;me&quot;">${escapeHtml(step.text)}</textarea>`, tk('rawHelp'));
  }
  const note = step.kind === 'raw' ? '' : field(k('note'), text('note', step.note, tk(`kinds.${step.kind}.title`)));
  return `<p class="bk-kind-help">${escapeHtml(tk(`kinds.${step.kind}.help`))}</p>${body}${note}`;
}

function stepProblem(step) {
  const found = preview && preview.problems.find((problem) => problem.step === step.id);
  return found ? found.message : '';
}

function stepCard(step, index) {
  const kind = KINDS.find((item) => item.kind === step.kind);
  const open = step.id === openStep;
  const problem = stepProblem(step);
  return `
    <div class="bk-step${open ? ' open' : ''}${step.enabled ? '' : ' off'}${problem ? ' bad' : ''}${summary(step) ? '' : ' todo'}" data-id="${step.id}">
      <div class="bk-step-head" data-toggle>
        <i class="fas ${kind.icon} fa-fw"></i>
        <div class="bk-step-title"><strong>${escapeHtml(step.note || tk(`kinds.${step.kind}.title`))}</strong><span data-summary>${escapeHtml(summary(step))}</span></div>
        <div class="bk-step-tools">
          <button type="button" data-move="-1" ${index === 0 ? 'disabled' : ''} title="${escapeHtml(tk('moveUp'))}"><i class="fas fa-arrow-up"></i></button>
          <button type="button" data-move="1" ${index === draft.steps.length - 1 ? 'disabled' : ''} title="${escapeHtml(tk('moveDown'))}"><i class="fas fa-arrow-down"></i></button>
          <button type="button" data-enable title="${escapeHtml(tk(step.enabled ? 'skipStep' : 'useStep'))}"><i class="fas ${step.enabled ? 'fa-eye' : 'fa-eye-slash'}"></i></button>
          <button type="button" data-remove title="${escapeHtml(t('common.remove'))}"><i class="fas fa-trash"></i></button>
        </div>
      </div>
      <p class="bk-step-error" data-error ${problem ? '' : 'hidden'}>${escapeHtml(problem)}</p>
      ${open ? `<div class="bk-step-body">${stepForm(step)}</div>` : ''}
    </div>`;
}

function renderSteps() {
  $('bk-steps').innerHTML = draft.steps.length
    ? draft.steps.map(stepCard).join('')
    : `<p class="bk-empty">${escapeHtml(tk('noSteps'))}</p>`;
}

function renderMenus() {
  // Until the image is read, the steps of one distribution alone stay out of the menu.
  const hidden = offers ? offers.hidden : ['ppa'];
  const menu = GROUPS.map((group) => `<h5 class="bk-group">${escapeHtml(tk(`groups.${group}`))}</h5>${KINDS.filter((item) => item.group === group && !hidden.includes(item.kind)).map(({ kind, icon }) => `
    <button type="button" class="bk-kind" data-kind="${kind}">
      <i class="fas ${icon} fa-fw"></i><strong>${escapeHtml(tk(`kinds.${kind}.title`))}</strong><span>${escapeHtml(tk(`kinds.${kind}.short`))}</span>
    </button>`).join('')}`).join('');
  $('bk-add').innerHTML = menu;
  $('bk-snippets').innerHTML = menu;
}

/** The step a control of the left pane belongs to: a card's, or the snippet being filled in. */
function stepOf(element) {
  if (element.closest('#bk-snippet')) return snippet;
  const card = element.closest('.bk-step');
  return card ? draft.steps.find((step) => step.id === card.dataset.id) : null;
}

function redraw(step) {
  if (step === snippet) renderSnippet();
  else renderSteps();
  schedulePreview();
}

function onStepInput(event) {
  const step = stepOf(event.target);
  if (!step) return;
  const control = event.target;
  if (control.dataset.field) {
    step[control.dataset.field] = control.type === 'checkbox' ? control.checked : control.value;
    if ('redraw' in control.dataset) return redraw(step);
  } else if (control.dataset.opt) {
    step.options = step.options || {};
    step.options[control.dataset.opt] = control.type === 'checkbox' ? control.checked : control.value;
    if ('redraw' in control.dataset) return redraw(step);
  } else if (control.dataset.member) {
    const list = listOf(step, control.dataset.member);
    if (control.checked) list.push(control.value);
    else list.splice(list.indexOf(control.value), 1);
    return redraw(step);
  } else if (control.dataset.envField) {
    step.env[Number(control.closest('[data-env]').dataset.env)][control.dataset.envField] = control.value;
  } else if ('search' in control.dataset) {
    return searchPackages(step, control);
  } else {
    return undefined;
  }
  const card = control.closest('#bk-steps .bk-step');
  if (card) {
    card.querySelector('[data-summary]').textContent = summary(step);
    if (control.dataset.field === 'note') card.querySelector('.bk-step-title strong').textContent = step.note || tk(`kinds.${step.kind}.title`);
  }
  return schedulePreview();
}

function addChips(step, list, value) {
  const items = listOf(step, list);
  value.split(/[\s,]+/).map((name) => name.trim()).filter(Boolean).forEach((name) => { if (!items.includes(name)) items.push(name); });
  redraw(step);
  const scope = step === snippet ? $('bk-snippet') : document.querySelector(`.bk-step[data-id="${step.id}"]`);
  const input = scope && scope.querySelector(`[data-chip-input="${list}"]`);
  if (input) input.focus();
}

function onStepKey(event) {
  const list = event.target.dataset.chipInput;
  if (!list) return;
  const step = stepOf(event.target);
  if (['Enter', ',', ' '].includes(event.key) && event.target.value.trim()) {
    event.preventDefault();
    addChips(step, list, event.target.value);
  } else if (event.key === 'Backspace' && !event.target.value && listOf(step, list).length) {
    listOf(step, list).pop();
    addChips(step, list, '');
  }
}

function showResults(control, html) {
  const box = control.closest('.bk-step-body, #bk-snippet-form').querySelector('[data-results]');
  if (box) box.innerHTML = html;
}

function searchPackages(step, control) {
  clearTimeout(searchTimer);
  const query = control.value.trim();
  if (query.length < 2 || !targetApp()) return showResults(control, '');
  searchTimer = setTimeout(async () => {
    let found;
    try {
      found = await secureFetch(`/api/admin/bakery/packages/${targetApp()}?q=${encodeURIComponent(query)}&family=${encodeURIComponent(draft.family)}`, { method: 'GET' });
    } catch (error) {
      return showResults(control, `<p class="bk-sub">${escapeHtml(error.message)}</p>`);
    }
    if (!control.isConnected || control.value.trim() !== query) return undefined;
    if (found.state === 'indexing') {
      showResults(control, `<p class="bk-sub"><span class="spinner-small"></span> ${escapeHtml(tk('indexing'))}</p>`);
      searchTimer = setTimeout(() => searchPackages(step, control), 3000);
      return undefined;
    }
    if (found.state !== 'ready') return showResults(control, `<p class="bk-sub">${escapeHtml(found.error || tk('noIndex'))}</p>`);
    const rows = found.results.filter((item) => !step.packages.includes(item.name)).map((item) => `
      <button type="button" data-add-chip="${escapeHtml(item.name)}"><strong>${escapeHtml(item.name)}</strong><span>${escapeHtml(item.description)}</span></button>`).join('');
    return showResults(control, rows || `<p class="bk-sub">${escapeHtml(tk('noPackages', { query }))}</p>`);
  }, 250);
  return undefined;
}

async function uploadFiles(step, files) {
  for (const file of files) {
    try {
      const { uploadId, totalChunks } = await uploadInChunks(file, file.name);
      const kept = await secureFetch('/api/admin/bakery/files', {
        method: 'POST', body: JSON.stringify({ upload_id: uploadId, total_chunks: totalChunks, filename: file.name }),
      });
      draft.files = draft.files.filter((item) => item.name !== kept.name).concat(kept);
      // A desktop entry has one icon.
      if (step && step.kind === 'desktop') step.files = [kept.name];
      else if (step && !step.files.includes(kept.name)) step.files.push(kept.name);
    } catch (error) {
      showToast(tk('uploadFailed', { name: file.name, error: error.message }), true);
    }
  }
  if (step) redraw(step);
  renderFiles();
  schedulePreview();
}

async function onStepChange(event) {
  const control = event.target;
  const step = stepOf(control);
  if ('upload' in control.dataset && control.files.length) {
    await uploadFiles(step, [...control.files]);
  } else if (step && 'pickFile' in control.dataset && control.value) {
    if (step.kind === 'desktop') step.files = [];
    step.files.push(control.value);
    redraw(step);
  }
}

function onStepClick(event) {
  const target = event.target.closest('button, [data-toggle]');
  if (!target) return;
  const step = stepOf(target);
  if (!step) return;
  const index = draft.steps.indexOf(step);
  if (target.dataset.move) {
    draft.steps.splice(index, 1);
    draft.steps.splice(index + Number(target.dataset.move), 0, step);
  } else if ('enable' in target.dataset) {
    step.enabled = !step.enabled;
  } else if ('remove' in target.dataset) {
    draft.steps.splice(index, 1);
  } else if (target.dataset.dropChip) {
    const list = listOf(step, target.dataset.dropChip);
    list.splice(list.indexOf(target.dataset.value), 1);
  } else if (target.dataset.addChip) {
    return addChips(step, 'packages', target.dataset.addChip);
  } else if (target.dataset.bundle) {
    return addChips(step, 'packages', ((offers && offers.bundles[target.dataset.bundle]) || []).join(' '));
  } else if (target.dataset.dropFile) {
    step.files = step.files.filter((name) => name !== target.dataset.dropFile);
  } else if (target.dataset.dropEnv) {
    step.env.splice(Number(target.dataset.dropEnv), 1);
  } else if ('addEnv' in target.dataset) {
    step.env.push({ name: '', value: '' });
  } else if ('toggle' in target.dataset && step !== snippet) {
    openStep = openStep === step.id ? null : step.id;
    renderSteps();
    return paintCode();
  } else {
    return undefined;
  }
  return redraw(step);
}

function addStep(kind) {
  const step = blankStep(kind);
  draft.steps.push(step);
  openStep = step.id;
  renderSteps();
  schedulePreview();
  const card = document.querySelector(`.bk-step[data-id="${step.id}"]`);
  if (card) card.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
}

// --- The editor: developer mode ------------------------------------------------------

function renderFiles() {
  $('bk-files').innerHTML = draft.files.length ? draft.files.map((file) => `
    <div class="bk-file">
      <code>files/${escapeHtml(file.name)}</code><span class="bk-sub">${escapeHtml(formatBytes(file.size, t))}</span>
      <button type="button" class="secondary" data-forget-file="${escapeHtml(file.name)}" title="${escapeHtml(t('common.remove'))}">&times;</button>
    </div>`).join('') : `<p class="bk-sub">${escapeHtml(tk('noFiles'))}</p>`;
}

function renderSnippet() {
  $('bk-snippet').style.display = snippet ? '' : 'none';
  if (!snippet) return;
  $('bk-snippet-title').textContent = tk(`kinds.${snippet.kind}.title`);
  $('bk-snippet-form').innerHTML = stepForm(snippet);
}

async function insertSnippet() {
  if (!snippet || !targetApp()) return;
  let rendered;
  try {
    rendered = await secureFetch(`/api/admin/bakery/preview/${targetApp()}`, {
      method: 'POST', body: JSON.stringify({ mode: 'guided', family: draft.family, steps: [snippet], files: draft.files }),
    });
  } catch (error) {
    showToast(error.message, true);
    return;
  }
  const block = rendered.blocks[0];
  if (!block) {
    showToast((rendered.problems[0] || {}).message || tk('snippetEmpty'), true);
    return;
  }
  const lines = rendered.dockerfile.split('\n').slice(block.start - 1, block.end).join('\n');
  const area = $('bk-text');
  const at = area.selectionStart;
  const before = area.value.slice(0, at);
  const lead = before && !before.endsWith('\n\n') ? (before.endsWith('\n') ? '\n' : '\n\n') : '';
  area.setRangeText(`${lead}${lines}\n`, at, area.selectionEnd, 'end');
  draft.dockerfile = area.value;
  paintGutter();
  snippet = null;
  renderSnippet();
  area.focus();
  schedulePreview();
}

async function setMode(mode) {
  if (mode === draft.mode) return;
  if (mode === 'dockerfile') {
    if (!draft.dockerfile.trim()) draft.dockerfile = (preview && preview.portable) || 'ARG BASE_IMAGE\nFROM ${BASE_IMAGE}\n';
  } else if (draft.dockerfile.trim() && !(await confirmDialog(t, { title: tk('toGuidedTitle'), message: tk('toGuided'), confirm: tk('toGuidedConfirm') }))) {
    return;
  }
  draft.mode = mode;
  renderEditor();
  schedulePreview(0);
}

// --- The editor: the Dockerfile ---------------------------------------------------------

function paintCode() {
  $('bk-code').dataset.empty = tk('baseNone');
  if (!preview) {
    $('bk-code').innerHTML = '';
    return;
  }
  const lines = preview.dockerfile.replace(/\n$/, '').split('\n');
  const owner = new Map();
  ((preview && preview.blocks) || []).forEach((block) => {
    for (let line = block.start; line <= block.end; line += 1) owner.set(line, block.step);
  });
  $('bk-code').innerHTML = lines.map((line, index) => {
    const step = owner.get(index + 1) || '';
    const tinted = line.trimStart().startsWith('#')
      ? `<span class="c">${escapeHtml(line)}</span>`
      : escapeHtml(line).replace(INSTRUCTION, '$1<span class="k">$2</span>');
    return `<div class="bk-ln${step && step === openStep ? ' hot' : ''}" data-step="${step}"><span class="bk-no">${index + 1}</span><span class="bk-tx">${tinted || ' '}</span></div>`;
  }).join('');
}

/** Number the lines of the text a developer writes, marking the ones the server has something to say about. */
function paintGutter() {
  const marks = new Map(((preview && draft.mode === 'dockerfile' && preview.problems) || []).filter((problem) => problem.line).map((problem) => [problem.line, problem.level]));
  const count = $('bk-text').value.split('\n').length;
  $('bk-gutter').innerHTML = Array.from({ length: count }, (_, index) => `<span class="${marks.get(index + 1) || ''}">${index + 1}</span>`).join('\n');
  $('bk-gutter').scrollTop = $('bk-text').scrollTop;
}

function paintProblems() {
  const problems = [...((preview && preview.problems) || [])].sort((a, b) => (a.line || 0) - (b.line || 0));
  paintGutter();
  $('bk-problems').innerHTML = problems.filter((problem) => !problem.step).map((problem) => `
    <li class="${problem.level}" ${problem.line ? `data-line="${problem.line}"` : ''}>
      <i class="fas ${problem.level === 'error' ? 'fa-circle-xmark' : 'fa-triangle-exclamation'}"></i>
      ${problem.line ? `<strong>${escapeHtml(tk('line', { line: problem.line }))}</strong> ` : ''}${escapeHtml(problem.message)}
    </li>`).join('');
  document.querySelectorAll('#bk-steps .bk-step').forEach((card) => {
    const step = draft.steps.find((item) => item.id === card.dataset.id);
    const message = step ? stepProblem(step) : '';
    card.classList.toggle('bad', Boolean(message));
    card.classList.toggle('todo', Boolean(step) && !summary(step));
    const note = card.querySelector('[data-error]');
    note.hidden = !message;
    note.textContent = message;
  });
}

function schedulePreview(delay = 350) {
  clearTimeout(previewTimer);
  previewTimer = setTimeout(runPreview, delay);
}

async function runPreview() {
  const target = targetApp();
  if (!draft || !target) {
    preview = null;
    paintCode();
    paintProblems();
    return;
  }
  const run = ++previewRun;
  let rendered;
  try {
    rendered = await secureFetch(`/api/admin/bakery/preview/${target}`, { method: 'POST', body: JSON.stringify(recipeBody()) });
  } catch (error) {
    if (run === previewRun) $('bk-problems').innerHTML = `<li class="error"><i class="fas fa-circle-xmark"></i> ${escapeHtml(error.message)}</li>`;
    return;
  }
  if (run !== previewRun || !draft) return;
  preview = rendered;
  // The image, or the family chosen for it, decides what the steps offer.
  if (JSON.stringify(rendered.offers) !== JSON.stringify(offers)) {
    offers = rendered.offers;
    renderMenus();
    if (!document.activeElement || !document.activeElement.closest('#bk-steps, #bk-snippet-form')) {
      renderSteps();
      renderSnippet();
    }
  }
  if (draft.mode === 'guided') paintCode();
  paintProblems();
}

function onCodeHover(event) {
  const line = event.target.closest('.bk-ln');
  const step = event.type === 'mouseover' && line ? line.dataset.step : '';
  document.querySelectorAll('#bk-steps .bk-step').forEach((card) => card.classList.toggle('lit', Boolean(step) && card.dataset.id === step));
}

function onStepHover(event) {
  const card = event.type === 'mouseover' ? event.target.closest('.bk-step') : null;
  const id = card ? card.dataset.id : openStep;
  document.querySelectorAll('#bk-code .bk-ln').forEach((line) => line.classList.toggle('hot', Boolean(id) && line.dataset.step === id));
}

function selectLine(number) {
  const area = $('bk-text');
  const lines = area.value.split('\n');
  const start = lines.slice(0, number - 1).reduce((sum, line) => sum + line.length + 1, 0);
  area.focus();
  area.setSelectionRange(start, start + (lines[number - 1] || '').length);
}

// --- The editor: builds ------------------------------------------------------------------

/** The elements a build is shown in: the list's panel for a saved recipe, the editor's for a trial. */
function panel() {
  return build && build.list
    ? { box: $('bk-panel'), spinner: $('bk-panel-spinner'), state: $('bk-panel-state'), log: $('bk-panel-log') }
    : { box: $('bk-build'), spinner: $('bk-build-spinner'), state: $('bk-build-state'), log: $('bk-log') };
}

function paintBuild() {
  $('bk-build').style.display = build && !build.list ? '' : 'none';
  $('bk-panel').style.display = build && build.list ? '' : 'none';
  if (!build) return;
  const { spinner, state } = panel();
  const running = build.state === 'queued' || build.state === 'building';
  spinner.style.display = running ? '' : 'none';
  const vars = { seconds: Math.round(build.elapsed || 0), name: build.name || '' };
  if (build.list) {
    const label = { queued: 'buildQueued', building: 'listBuilding', ready: 'listBaked', failed: 'listFailed' }[build.state] || 'listBuilding';
    state.textContent = tk(label, vars);
    $('bk-panel-note').textContent = tk('rebakeNote', { image: build.image || '' });
    $('bk-panel-done').style.display = build.state === 'ready' ? '' : 'none';
  } else {
    const label = { queued: 'buildQueued', building: 'buildRunning', ready: 'buildReady', failed: 'buildFailed' }[build.state] || 'buildRunning';
    state.textContent = tk(label, vars);
    $('bk-trial').style.display = build.state === 'ready' && build.id ? '' : 'none';
  }
  state.className = build.state === 'failed' ? 'error' : '';
}

function appendLog(lines) {
  if (!lines.length) return;
  const { log } = panel();
  const pinned = log.scrollTop + log.clientHeight >= log.scrollHeight - 24;
  log.append(document.createTextNode(`${lines.join('\n')}\n`));
  if (pinned) log.scrollTop = log.scrollHeight;
}

async function followBuild() {
  clearTimeout(buildTimer);
  if (!build || !build.id) return;
  const { id } = build;
  try {
    const news = await secureFetch(`/api/admin/bakery/builds/${id}?after=${build.next}`, { method: 'GET' });
    if (!build || build.id !== id) return;
    appendLog(news.lines);
    Object.assign(build, { state: news.state, next: news.next, elapsed: news.elapsed, error: news.error });
  } catch (error) {
    if (build && build.id === id) Object.assign(build, { state: 'failed', error: error.message });
  }
  paintBuild();
  if (build.state === 'queued' || build.state === 'building') buildTimer = setTimeout(followBuild, 1000);
  else if (build.list) refresh();
}

function watchBuild(started, extra = {}) {
  build = { id: started.id, state: started.state, next: 0, elapsed: 0, ...extra };
  const { box, log } = panel();
  log.textContent = '';
  paintBuild();
  box.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
  followBuild();
}

/**
 * Show a saved recipe's build under the list: the one running, followed as it goes, else the output of its last.
 *
 * @param {object} recipe The recipe as the server gives it.
 */
async function showRecipeBuild(recipe) {
  clearTimeout(buildTimer);
  const about = { list: true, name: recipe.app_name || recipe.app_id, image: recipe.image };
  if (recipe.status.build) {
    watchBuild({ id: recipe.status.build, state: 'building' }, about);
    return;
  }
  build = { id: '', state: recipe.status.state === 'error' ? 'failed' : 'ready', next: 0, elapsed: 0, ...about };
  $('bk-panel-log').textContent = '';
  paintBuild();
  try {
    const { lines } = await secureFetch(`/api/admin/bakery/recipes/${recipe.app_id}/log`, { method: 'GET' });
    if (build && build.list && build.name === about.name) appendLog(lines);
  } catch (error) { /* the state line stands without the output */ }
  $('bk-panel').scrollIntoView({ block: 'nearest', behavior: 'smooth' });
}

async function tryBuild() {
  if (draft.mode === 'dockerfile') draft.dockerfile = $('bk-text').value;
  try {
    watchBuild(await secureFetch(`/api/admin/bakery/builds/${targetApp()}`, { method: 'POST', body: JSON.stringify(recipeBody()) }));
  } catch (error) {
    showToast(error.message, true);
  }
}

/** Open a session of the application on the image the followed build made, in a tab that shows the launch. */
async function launchTrial() {
  const id = crypto.randomUUID();
  const app = appOf(targetApp()) || {};
  try {
    await request('reserveTab', { reserve: true, launch: { id, app: app.name || '', logo: app.logo || '', room: false } });
  } catch (error) {
    const reason = { noSessionOrigin: t('popup.status.noSessionOrigin'), popupBlocked: t('web.home.popupBlocked') }[error.message] || error.message;
    showToast(reason, true);
    return;
  }
  const channel = new BroadcastChannel(`sealskin-launch-${id}`);
  let news = null;
  const tell = (message) => {
    news = message;
    channel.postMessage(message);
  };
  channel.onmessage = (event) => { if (event.data && event.data.hello && news) channel.postMessage(news); };
  $('bk-trial').disabled = true;
  try {
    tell({ posted: true });
    const session = await postLaunch('/api/admin/bakery/try', { launch_id: id, build_id: build.id, timezone: browserTimezone(), wayland_mode: true });
    await request('openSession', { sessionId: session.session_id, sessionUrl: session.session_url, launchId: id });
    channel.close();
  } catch (error) {
    tell({ error: error.message });
    setTimeout(() => channel.close(), 120000);
    showToast(t('options.status.launchFailed', { error: error.message }), true);
  }
  $('bk-trial').disabled = false;
}

async function save() {
  if (draft.mode === 'dockerfile') draft.dockerfile = $('bk-text').value;
  else draft.files = draft.files.filter((file) => draft.steps.some((step) => step.files.includes(file.name)));
  $('bk-save').disabled = true;
  try {
    const name = (draft.name || '').trim();
    const own = appOf(draft.app_id);
    let saved;
    if (!draft.existing) {
      if (!name || !draft.base_app_id) {
        $('bk-name').focus();
        throw new Error(tk('newAppInvalid'));
      }
      // The application and its recipe are made together, or not at all.
      saved = await secureFetch('/api/admin/bakery/apps', {
        method: 'POST', body: JSON.stringify({ ...recipeBody(), name, base_app_id: draft.base_app_id, logo: draft.icon ? draft.icon.split(',')[1] : '' }),
      });
    } else {
      if (own && own.is_meta_app && ((name && name !== own.name) || draft.icon)) {
        const changes = {};
        if (name && name !== own.name) changes.name = name;
        if (draft.icon) changes.logo = draft.icon.split(',')[1];
        await secureFetch(`/api/admin/apps/installed/${draft.app_id}`, { method: 'PATCH', body: JSON.stringify(changes) });
      }
      saved = await secureFetch(`/api/admin/bakery/recipes/${draft.app_id}`, { method: 'PUT', body: JSON.stringify(recipeBody()) });
    }
    await deps.refreshApps();
    await closeEditor();
    await showRecipeBuild(saved);
  } catch (error) {
    showToast(error.message, true);
  }
  $('bk-save').disabled = false;
}

// --- The editor: open and close ---------------------------------------------------------

function renderEditor() {
  const guided = draft.mode === 'guided';
  fillAppSelects();
  renderBar();
  $('bk-guided').style.display = guided ? '' : 'none';
  $('bk-dev').style.display = guided ? 'none' : '';
  $('bk-code').style.display = guided ? '' : 'none';
  $('bk-edit').style.display = guided ? 'none' : '';
  $('bk-text').value = draft.dockerfile;
  renderSteps();
  renderFiles();
  renderSnippet();
  paintCode();
  paintProblems();
  paintBuild();
}

async function openEditor(recipe) {
  draft = recipe
    ? { ...copy({ mode: recipe.mode, family: recipe.family, steps: recipe.steps, dockerfile: recipe.dockerfile, files: recipe.files, enabled: recipe.enabled }), app_id: recipe.app_id, existing: true }
    : { app_id: '', base_app_id: '', name: '', existing: false, mode: 'guided', family: '', steps: [], dockerfile: '', files: [], enabled: true };
  preview = null;
  snippet = null;
  build = null;
  openStep = null;
  clearTimeout(buildTimer);
  $('bk-name').value = '';
  $('bk-log').textContent = '';
  $('bk-list').style.display = 'none';
  $('bk-editor').style.display = '';
  renderEditor();
  await loadBase();
}

async function closeEditor() {
  draft = null;
  build = null;
  clearTimeout(buildTimer);
  clearTimeout(previewTimer);
  paintBuild();
  $('bk-editor').style.display = 'none';
  $('bk-list').style.display = '';
  await refresh();
}

/** Empty the recipe in the editor: its steps, its Dockerfile, and its files. The application, its name, and its icon stay. */
async function clearRecipe() {
  const sure = await confirmDialog(t, { title: tk('clearTitle'), message: tk('confirmClear'), confirm: tk('clear'), danger: true });
  if (!sure) return;
  draft.steps = [];
  draft.files = [];
  draft.family = '';
  draft.dockerfile = draft.mode === 'dockerfile' ? 'ARG BASE_IMAGE\nFROM ${BASE_IMAGE}\n' : '';
  openStep = null;
  snippet = null;
  build = null;
  clearTimeout(buildTimer);
  renderEditor();
  schedulePreview(0);
}

function wire() {
  wired = true;
  renderMenus();
  $('bk-new').addEventListener('click', () => openEditor(null));
  document.querySelector('#bk-table tbody').addEventListener('click', onListClick);
  $('bk-close').addEventListener('click', closeEditor);
  $('bk-save').addEventListener('click', save);
  $('bk-try').addEventListener('click', tryBuild);
  $('bk-trial').addEventListener('click', launchTrial);
  $('bk-base-app').addEventListener('change', (event) => {
    draft.base_app_id = event.target.value;
    loadBase();
  });
  $('bk-name').addEventListener('input', (event) => { draft.name = event.target.value; });
  $('bk-icon-upload').addEventListener('change', (event) => {
    const [file] = event.target.files;
    event.target.value = '';
    if (!file) return;
    const reader = new FileReader();
    reader.onload = () => {
      draft.icon = reader.result;
      renderBar();
    };
    reader.readAsDataURL(file);
  });
  $('bk-clear').addEventListener('click', clearRecipe);
  $('bk-panel-close').addEventListener('click', () => {
    build = null;
    clearTimeout(buildTimer);
    paintBuild();
  });
  $('bk-panel-done').addEventListener('click', () => $('bk-panel-close').click());
  $('bk-family').addEventListener('change', (event) => {
    draft.family = event.target.value;
    renderBar();
    schedulePreview(0);
  });
  $('bk-mode').addEventListener('click', (event) => {
    const button = event.target.closest('button[data-mode]');
    if (button) setMode(button.dataset.mode);
  });
  $('bk-add').addEventListener('click', (event) => {
    const button = event.target.closest('button[data-kind]');
    if (button) addStep(button.dataset.kind);
  });
  $('bk-snippets').addEventListener('click', (event) => {
    const button = event.target.closest('button[data-kind]');
    if (!button) return;
    snippet = blankStep(button.dataset.kind);
    renderSnippet();
  });
  $('bk-snippet-insert').addEventListener('click', insertSnippet);
  $('bk-snippet-cancel').addEventListener('click', () => {
    snippet = null;
    renderSnippet();
  });
  for (const pane of [$('bk-steps'), $('bk-snippet-form')]) {
    pane.addEventListener('input', onStepInput);
    pane.addEventListener('change', onStepChange);
    pane.addEventListener('click', onStepClick);
    pane.addEventListener('keydown', onStepKey);
  }
  $('bk-steps').addEventListener('mouseover', onStepHover);
  $('bk-steps').addEventListener('mouseout', onStepHover);
  $('bk-code').addEventListener('mouseover', onCodeHover);
  $('bk-code').addEventListener('mouseout', onCodeHover);
  $('bk-code').addEventListener('click', (event) => {
    const line = event.target.closest('.bk-ln');
    if (!line || !line.dataset.step) return;
    openStep = line.dataset.step;
    renderSteps();
    paintCode();
    const card = document.querySelector(`.bk-step[data-id="${openStep}"]`);
    if (card) card.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
  });
  $('bk-text').addEventListener('input', (event) => {
    draft.dockerfile = event.target.value;
    paintGutter();
    schedulePreview(600);
  });
  $('bk-text').addEventListener('scroll', (event) => { $('bk-gutter').scrollTop = event.target.scrollTop; });
  $('bk-text').addEventListener('keydown', (event) => {
    if (event.key !== 'Tab' || event.shiftKey) return;
    event.preventDefault();
    event.target.setRangeText('  ', event.target.selectionStart, event.target.selectionEnd, 'end');
    draft.dockerfile = event.target.value;
  });
  $('bk-problems').addEventListener('click', (event) => {
    const item = event.target.closest('li[data-line]');
    if (item && draft.mode === 'dockerfile') selectLine(Number(item.dataset.line));
  });
  $('bk-copy').addEventListener('click', async () => {
    await navigator.clipboard.writeText(draft.mode === 'guided' ? (preview ? preview.dockerfile : '') : $('bk-text').value);
    showToast(tk('copied'));
  });
  $('bk-dev-upload').addEventListener('change', async (event) => {
    await uploadFiles(null, [...event.target.files]);
    event.target.value = '';
  });
  $('bk-files').addEventListener('click', (event) => {
    const button = event.target.closest('button[data-forget-file]');
    if (!button) return;
    const name = button.dataset.forgetFile;
    draft.files = draft.files.filter((file) => file.name !== name);
    draft.steps.forEach((step) => { step.files = step.files.filter((item) => item !== name); });
    renderFiles();
    schedulePreview();
  });
}

/**
 * Show the Bakery tab: the list, read again from the server.
 *
 * @param {object} options
 * @param {function} options.t Translator.
 * @param {function(): object[]} options.apps The installed applications as the admin page holds them.
 * @param {function(): Promise<void>} options.refreshApps Reads them again, after one was made here.
 */
export async function openBakery(options) {
  t = options.t;
  deps = options;
  if (!wired) wire();
  if (!appsList().length) await deps.refreshApps();
  if (!draft) await refresh();
  paintBuild();
}

/**
 * Open a baked application's recipe in the editor; the tab is shown first (`openBakery`).
 *
 * @param {string} appId The baked application.
 */
export async function editBaked(appId) {
  await refresh();
  const recipe = data.recipes.find((item) => item.app_id === appId);
  if (recipe) await openEditor(recipe);
}
