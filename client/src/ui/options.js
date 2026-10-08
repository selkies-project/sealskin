/**
 * Options / admin dashboard. Served by the server and framed by a shell.
 *
 * The connection settings (server, keys) live in the shell's bundled connect
 * page; this page only shows who is connected and lets the user jump there.
 * In the web app there are none: the page shows who is signed in and signs out.
 */

import { bridge, request } from '../lib/bridge.js';
import { secureFetch, fetchSchema, openPage } from '../lib/api.js';
import { loadTranslator, applyTranslations } from '../lib/i18n.js';
import { browserTimezone } from '../lib/timezone.js';
import { supportedLangs } from '../lib/languages.js';
import { sendForgedSignIn } from '../lib/proxy-check.js';
import { reachableSessionOrigin } from '../lib/session-origin.js';
import { confirmDialog } from '../lib/modal.js';
import {
  announce, escapeHtml, formatBytes, formatDate, timeAgo, formatLogoSrc, hydrateLogos, showToast, tOr,
  addMobileSafeArea, addMobileBackButton, downloadBlob, currentLocale,
} from '../lib/dom.js';

let t;
let info;
let config = {};

const displayStatus = (message, isError = false) => showToast(message, isError);

const dashboardUsername = document.getElementById('dashboard-username');
const dashboardRole = document.getElementById('dashboard-role');
const dashboardServerIp = document.getElementById('dashboard-server-ip');
const dashboardApiPort = document.getElementById('dashboard-api-port');
const dashboardCpuModel = document.getElementById('dashboard-cpu-model');
const dashboardDiskInfo = document.getElementById('dashboard-disk-info');
const dashboardDiskUsageText = document.getElementById('dashboard-disk-usage-text');
const dashboardDiskUsageBar = document.getElementById('dashboard-disk-usage-bar');
const searchEngineDashboardSelect = document.getElementById('searchEngineDashboard');
const changeConnectionButton = document.getElementById('change-connection-button');
const adminNavLinks = document.querySelectorAll('.admin-nav-link');
const adminNavSeparator = document.getElementById('admin-nav-separator');
const serverPublicKeyDisplay = document.getElementById('server-public-key-display');
const addAdminForm = document.getElementById('add-admin-form');
const addUserForm = document.getElementById('add-user-form');
const addGroupForm = document.getElementById('add-group-form');
const homeDirTabButton = document.getElementById('homedir-tab-button');
const addHomeDirForm = document.getElementById('add-homedir-form');
const homeDirsTbody = document.querySelector('#homedirs-table tbody');
const sessionsTabButton = document.getElementById('sessions-tab-button');
const sessionsContainer = document.getElementById('sessions-container');
const refreshSessionsBtn = document.getElementById('refresh-sessions-btn');
const userEditModal = document.getElementById('user-edit-modal');
const userEditForm = document.getElementById('user-edit-form');
const groupEditModal = document.getElementById('group-edit-modal');
const groupEditForm = document.getElementById('group-edit-form');
const userConfigModal = document.getElementById('user-config-modal');
const generatedConfigText = document.getElementById('generatedConfigText');
const copyConfigBtn = document.getElementById('copyConfigBtn');
const downloadConfigBtn = document.getElementById('downloadConfigBtn');
const configModalWarning = document.getElementById('config-modal-warning');
const configModalInfo = document.getElementById('config-modal-info');
const userHomeDirModal = document.getElementById('user-homedir-modal');
const userHomeDirsTbody = document.querySelector('#user-homedirs-table tbody');
const adminAddHomeDirForm = document.getElementById('admin-add-homedir-form');
const appInstallModal = document.getElementById('app-install-modal');
const appInstallForm = document.getElementById('app-install-form');
const appStoreSelect = document.getElementById('app-store-select');
const refreshAppStoreBtn = document.getElementById('refresh-app-store-btn');
const addAppStoreForm = document.getElementById('add-app-store-form');
const addManualAppBtn = document.getElementById('add-manual-app-btn');
const availableAppsContainer = document.getElementById('available-apps-container');
const installedAppsTbody = document.querySelector('#installed-apps-table tbody');
const imageUpdateModal = document.getElementById('image-update-modal');
const imageUpdateModalTitle = document.getElementById('image-update-modal-title');
const imageUpdateModalBody = document.getElementById('image-update-modal-body');
const imageUpdateModalFooter = document.getElementById('image-update-modal-footer');

let APP_TEMPLATE_SETTINGS = [];
let appTemplateTabInitialized = false;
let appLaboratoryTabInitialized = false;
let isLoggedIn = false;
let isAdmin = false;

let adminData = {
  users: [],
  groups: [],
  admins: [],
  api_port: 8000,
  session_port: 8443,
  appStores: [],
  installedApps: [],
  availableApps: [],
  appTemplates: [],
  gpus: [],
  prootCatalogs: [],
  proot_catalogs: [],
  proot_remote: 'linuxserver/proot-apps',
};
const tableStates = {
  users: { currentPage: 1, searchTerm: '' },
  groups: { currentPage: 1, searchTerm: '' },
  admins: { currentPage: 1, searchTerm: '' },
  installedApps: { currentPage: 1, searchTerm: '' },
  availableApps: { currentPage: 1, searchTerm: '' },
  prootCatalogs: { currentPage: 1, searchTerm: '' },
};
let labState = {
  isEditing: false,
  currentApp: null,
  currentSessionId: null,
  base64Icon: '',
  isDirty: false,
};
let currentAdminManagedUser = null;
// Whether the server is one node of several, the administrator's view of them, and the user's.
let clustered = false;
let clusterData = null;
let myCluster = null;
// The node holding each home of the user whose homes an administrator is managing.
let managedHomes = {};
let currentAppForUpdateCheck = null;
let installedAppsPollingInterval = null;
let prootPoll = null;
// The catalog open in the editor: its apps, and the remote whose apps the grid shows.
const prootEditor = { id: null, apps: [], remote: '', remoteApps: [], search: '', icons: {} };

// --- TEMPLATE EDITOR ---

/** The editor's cards in the schema's order, each with its icon and translated label. */
let TEMPLATE_SECTIONS = [];
/** Values of the WebRTC block that switch a session to WebRTC once they differ from the default. */
const WEBRTC_TRIGGERS = ['SELKIES_STUN_HOST', 'SELKIES_TURN_HOST', 'SELKIES_TURN_REST_URI', 'SELKIES_WEBRTC_PUBLIC_IP', 'SELKIES_ENABLE_CLOUDFLARE_TURN'];

/**
 * Turn the server schema into editor definitions with translated labels.
 */
function resolveTemplateSchema(schema) {
  TEMPLATE_SECTIONS = (schema.sections || []).map((section) => ({
    id: section.id,
    icon: section.icon && section.icon.includes(' ') ? section.icon : `fas ${section.icon || 'fa-sliders-h'}`,
    label: tOr(t, `options.appTemplates.sections.${section.id}`, section.id),
  }));
  return (schema.settings || []).map((setting) => {
    const base = `options.appTemplates.settings.${setting.name}`;
    const def = {
      name: setting.name,
      category: setting.category,
      section: setting.section || (TEMPLATE_SECTIONS[0] || {}).id,
      group: setting.group || '',
      type: setting.type,
      default: setting.default === undefined || setting.default === null ? '' : String(setting.default),
      docker: !!setting.docker,
      label: tOr(t, `${base}.label`, setting.label || setting.name),
      description: tOr(t, `${base}.description`, setting.description || ''),
    };
    if (setting.type === 'select') {
      def.options = {};
      (setting.options || []).forEach((opt) => {
        const value = String(opt.value ?? '');
        def.options[value] = opt.label_key
          ? tOr(t, `${base}.options.${opt.label_key}`, opt.label || value)
          : (opt.label ?? value);
      });
    }
    return def;
  });
}

/**
 * One row of a settings form: the label and description on the left, the
 * control on the right. A `switch` row is a checkbox; a `wide` row puts the
 * control under the text instead. `text` is what a filter box matches.
 */
function fieldHtml({ id = '', label, description = '', control, kind = '', text = '' }) {
  const more = description ? `<button type="button" class="field-more" aria-expanded="false">${escapeHtml(t('options.appTemplates.showMore'))}</button>` : '';
  return `<div class="field${kind ? ` ${kind}` : ''}" data-text="${escapeHtml((text || `${label} ${description}`).toLowerCase())}">
    <div class="field-text">
      <label${id ? ` for="${id}"` : ''}>${escapeHtml(label)}</label>
      ${description ? `<p class="description">${escapeHtml(description)}${more}</p>` : ''}
    </div>
    <div class="field-control">${control}</div>
  </div>`;
}

/** One row of the template form, filtered by its label, variable name, and description. */
function templateFieldHtml(setting) {
  const inputId = `template-form-${setting.name}`;
  const def = escapeHtml(setting.default);
  let control;
  switch (setting.type) {
    case 'boolean':
      control = `<input type="checkbox" id="${inputId}" data-name="${setting.name}" ${setting.default === 'true' ? 'checked' : ''}>`;
      break;
    case 'select':
      control = `<select id="${inputId}" data-name="${setting.name}">${Object.entries(setting.options).map(([value, name]) =>
        `<option value="${escapeHtml(value)}" ${value === setting.default ? 'selected' : ''}>${escapeHtml(name)}</option>`).join('')}</select>`;
      break;
    default:
      control = `<input type="text" id="${inputId}" data-name="${setting.name}" value="${def}" placeholder="${def}">`;
  }
  return fieldHtml({
    id: inputId,
    label: setting.label,
    description: setting.description,
    control,
    kind: setting.type === 'boolean' ? 'switch' : '',
    text: `${setting.label} ${setting.name} ${setting.description}`,
  });
}

/** A description is clamped to two lines; a click on the text or its "Show more" shows the whole of it, in any form. */
function bindFieldLists() {
  document.addEventListener('click', (event) => {
    const text = event.target.closest('.field-text');
    if (!text || event.target.closest('label')) return;
    const field = text.closest('.field');
    const more = field.querySelector('.field-more');
    if (!more) return;
    const expanded = field.classList.toggle('expanded');
    more.textContent = t(expanded ? 'options.appTemplates.showLess' : 'options.appTemplates.showMore');
    more.setAttribute('aria-expanded', String(expanded));
  });
  window.addEventListener('resize', () => markClampedDescriptions());
}
bindFieldLists();

/**
 * Mark the rows whose description the two-line clamp cuts, so they offer
 * "Show more" at the end of the second line; a row in a closed card has no
 * height, so those wait for the card to open.
 */
function markClampedDescriptions(scope = document) {
  scope.querySelectorAll('.field:not(.expanded) .field-text .description').forEach((description) => {
    if (!description.clientHeight) return;
    description.closest('.field').classList.toggle('clamped', description.scrollHeight > description.clientHeight + 1);
  });
}

/**
 * Build the form: a collapsible card per section of the schema, the WebRTC
 * block with its headings, and the UI preview inside the appearance card
 * whose toggles it reflects. The first card starts open.
 */
function buildTemplateForm() {
  const host = document.getElementById('template-sections');
  host.innerHTML = '';
  const bySection = new Map(TEMPLATE_SECTIONS.map((section) => [section.id, []]));
  APP_TEMPLATE_SETTINGS.forEach((setting) => {
    (bySection.get(setting.section) || bySection.get(TEMPLATE_SECTIONS[0].id)).push(setting);
  });
  const grid = (settings) => `<div class="field-list">${settings.map(templateFieldHtml).join('')}</div>`;
  const heading = (key, fallback) => `<h5 class="field-group">${escapeHtml(tOr(t, key, fallback))}</h5>`;
  TEMPLATE_SECTIONS.forEach((section, index) => {
    const settings = bySection.get(section.id);
    if (!settings.length) return;
    let body;
    if (section.id === 'webrtc') {
      const groups = [];
      settings.forEach((setting) => {
        let group = groups.find((g) => g.id === setting.group);
        if (!group) groups.push(group = { id: setting.group, settings: [] });
        group.settings.push(setting);
      });
      body = `<p class="description">${escapeHtml(t('options.modals.webrtcDescription'))}</p>`
        + groups.map((g) => heading(`options.appTemplates.groups.${g.id}`, g.id) + grid(g.settings)).join('');
    } else if (section.id === 'container') {
      body = `<p class="description">${escapeHtml(t('options.modals.dockerDescription'))}</p>`
        + `<p id="template-docker-admin-only" class="description" hidden>${escapeHtml(t('options.appTemplates.dockerAdminOnly'))}</p>`
        + grid(settings);
    } else {
      body = grid(settings);
    }
    const details = document.createElement('details');
    details.className = `collapsible-section template-section${section.id === 'webrtc' ? ' template-webrtc' : ''}`;
    details.dataset.section = section.id;
    details.open = index === 0;
    details.innerHTML = `<summary><i class="${escapeHtml(section.icon)}"></i><h4>${escapeHtml(section.label)}</h4><span class="template-badge" hidden></span></summary><div>${body}</div>`;
    host.appendChild(details);
  });
  const appearance = host.querySelector('[data-section="appearance"] > div');
  const preview = document.getElementById('template-preview');
  if (appearance && preview) {
    appearance.prepend(preview);
    preview.hidden = false;
  }
  host.addEventListener('toggle', (event) => { if (event.target.open) markClampedDescriptions(event.target); }, true);
  markClampedDescriptions(host);
  if (!isAdmin) {
    host.querySelectorAll('[data-section="container"] input, [data-section="container"] select').forEach((el) => { el.disabled = true; });
    const note = document.getElementById('template-docker-admin-only');
    if (note) note.hidden = false;
  }
}

/** Whether a field of the form holds something other than the schema's default. */
function templateFieldChanged(setting) {
  const el = document.getElementById(`template-form-${setting.name}`);
  if (!el) return false;
  return (setting.type === 'boolean' ? (el.checked ? 'true' : 'false') : el.value) !== setting.default;
}

/**
 * How the sessions of this template stream, as the base image decides it:
 * WebRTC once the mode says so or a trigger value is set, with dual mode
 * unless it is switched off.
 */
function templateStreaming() {
  const value = (name) => document.getElementById(`template-form-${name}`);
  const mode = value('SELKIES_MODE') ? value('SELKIES_MODE').value : '';
  const triggered = APP_TEMPLATE_SETTINGS.some((setting) => WEBRTC_TRIGGERS.includes(setting.name) && templateFieldChanged(setting)
    && (setting.type === 'boolean' ? value(setting.name).checked : value(setting.name).value));
  if (mode === 'websockets' || (mode !== 'webrtc' && !triggered)) return 'websockets';
  const dual = value('SELKIES_ENABLE_DUAL_MODE');
  return dual && dual.value === 'false' ? 'webrtc' : 'webrtcDual';
}

/**
 * Apply the filter box and the changed-only switch to the fields, and put on
 * each card the count of its changed values; the WebRTC card shows the
 * streaming its values amount to instead.
 */
function updateTemplateSections() {
  const term = document.getElementById('template-filter').value.trim().toLowerCase();
  const changedOnly = document.getElementById('template-changed-only').checked;
  const filtering = Boolean(term) || changedOnly;
  const changed = new Map();
  APP_TEMPLATE_SETTINGS.forEach((setting) => {
    const el = document.getElementById(`template-form-${setting.name}`);
    const field = el && el.closest('.field');
    if (!field) return;
    const isChanged = templateFieldChanged(setting);
    field.classList.toggle('changed', isChanged);
    field.hidden = (term && !field.dataset.text.includes(term)) || (changedOnly && !isChanged);
    if (isChanged) changed.set(setting.section, (changed.get(setting.section) || 0) + 1);
  });
  document.querySelectorAll('#template-sections .template-section').forEach((details) => {
    const { section } = details.dataset;
    const visible = details.querySelectorAll('.field:not([hidden])').length;
    details.hidden = filtering && !visible;
    if (filtering) details.open = visible > 0;
    details.querySelectorAll('.field-list').forEach((g) => {
      const head = g.previousElementSibling;
      if (head && head.classList.contains('field-group')) head.hidden = !g.querySelector('.field:not([hidden])');
    });
    const badge = details.querySelector('.template-badge');
    if (section === 'webrtc') {
      const streaming = templateStreaming();
      badge.textContent = t(`options.appTemplates.streaming.${streaming}`);
      badge.classList.toggle('on', streaming !== 'websockets');
      badge.hidden = false;
    } else {
      const count = changed.get(section) || 0;
      badge.textContent = t('options.appTemplates.changedCount', { count });
      badge.hidden = !count;
    }
  });
  markClampedDescriptions();
}

/** Reload the template list alone, all a template editor who is not an administrator may read. */
async function refreshTemplates() {
  adminData.appTemplates = await secureFetch('/api/admin/apps/templates', { method: 'GET' });
}

function updateTemplatePreview() {
  const getVal = (name, isCheckbox = false) => {
    const el = document.getElementById(`template-form-${name}`);
    if (!el) return isCheckbox ? false : '';
    return isCheckbox ? el.checked : el.value;
  };

  document.getElementById('preview-page-title').textContent = getVal('TITLE') || 'Selkies';

  const showSidebar = getVal('SELKIES_UI_SHOW_SIDEBAR', true);
  const sidebarEl = document.getElementById('preview-sidebar');
  // The real sidebar is 280px wide whatever the window is.
  sidebarEl.style.width = showSidebar ? '280px' : '0';
  sidebarEl.style.padding = showSidebar ? '1rem' : '0';
  sidebarEl.style.borderRight = showSidebar ? '1px solid var(--border-color)' : 'none';

  document.getElementById('preview-title').textContent = getVal('SELKIES_UI_TITLE');
  const toggles = {
    'preview-logo': 'SELKIES_UI_SHOW_LOGO',
    'preview-core-buttons': 'SELKIES_UI_SHOW_CORE_BUTTONS',
    'preview-soft-buttons': 'SELKIES_UI_SIDEBAR_SHOW_SOFT_BUTTONS',
    'preview-video-settings': 'SELKIES_UI_SIDEBAR_SHOW_VIDEO_SETTINGS',
    'preview-screen-settings': 'SELKIES_UI_SIDEBAR_SHOW_SCREEN_SETTINGS',
    'preview-audio-settings': 'SELKIES_UI_SIDEBAR_SHOW_AUDIO_SETTINGS',
    'preview-stats': 'SELKIES_UI_SIDEBAR_SHOW_STATS',
    'preview-clipboard': 'SELKIES_UI_SIDEBAR_SHOW_CLIPBOARD',
    'preview-files': 'SELKIES_UI_SIDEBAR_SHOW_FILES',
    'preview-apps': 'SELKIES_UI_SIDEBAR_SHOW_APPS',
    'preview-sharing': 'SELKIES_UI_SIDEBAR_SHOW_SHARING',
    'preview-gamepads': 'SELKIES_UI_SIDEBAR_SHOW_GAMEPADS',
  };
  Object.entries(toggles).forEach(([id, name]) => {
    document.getElementById(id).style.display = getVal(name, true) ? 'block' : 'none';
  });
  document.getElementById('preview-keyboard-button').style.display = getVal('SELKIES_UI_SIDEBAR_SHOW_KEYBOARD_BUTTON', true) ? 'flex' : 'none';
}

async function saveTemplateProfile() {
  const templateSelect = document.getElementById('template-select');
  const isNew = templateSelect.value === 'new';
  const nameInput = document.getElementById('template-name-input');
  const templateName = isNew ? nameInput.value.trim() : templateSelect.value;

  if (!templateName) {
    displayStatus(t('options.appTemplates.enterName'), true);
    return;
  }

  const settingsBlob = {};
  APP_TEMPLATE_SETTINGS.forEach((setting) => {
    const el = document.getElementById(`template-form-${setting.name}`);
    if (!el) return;
    const value = setting.type === 'boolean' ? (el.checked ? 'true' : 'false') : el.value;
    if (value !== setting.default) settingsBlob[setting.name] = value;
  });

  const payload = { name: templateName, settings: settingsBlob };

  try {
    await secureFetch('/api/admin/apps/templates', { method: 'POST', body: JSON.stringify(payload) });
    displayStatus(t('options.status.templateSaved', { name: payload.name }), false);
    await (isAdmin ? refreshAppData() : refreshTemplates());
    populateTemplateDropdowns();
    templateSelect.value = payload.name;
    nameInput.value = '';
    templateSelect.dispatchEvent(new Event('change'));
  } catch (error) {
    displayStatus(t('options.status.templateSaveFailed', { error: error.message }), true);
  }
}

async function deleteTemplateProfile() {
  const templateSelect = document.getElementById('template-select');
  const templateName = templateSelect.value;

  if (!templateName || templateName === 'new' || templateName === 'Default') {
    displayStatus(t('options.appTemplates.deleteDisabled'), true);
    return;
  }
  const remove = await confirmDialog(t, {
    title: t('options.appTemplates.deleteTitle'),
    message: t('options.appTemplates.confirmDelete', { templateName }),
    confirm: t('common.delete'),
    danger: true,
  });
  if (!remove) return;

  try {
    await secureFetch(`/api/admin/apps/templates/${encodeURIComponent(templateName)}`, { method: 'DELETE' });
    displayStatus(t('options.status.templateDeleted', { templateName }), false);
    await (isAdmin ? refreshAppData() : refreshTemplates());
    populateTemplateDropdowns();
    templateSelect.value = 'new';
    templateSelect.dispatchEvent(new Event('change'));
  } catch (error) {
    displayStatus(t('options.status.templateDeleteFailed', { error: error.message }), true);
  }
}

function loadTemplateIntoForm(templateName) {
  const template = adminData.appTemplates.find((tpl) => tpl.name === templateName);
  const settings = template ? template.settings : {};

  APP_TEMPLATE_SETTINGS.forEach((settingDef) => {
    const el = document.getElementById(`template-form-${settingDef.name}`);
    if (!el) return;
    const value = settings[settingDef.name] ?? settingDef.default;
    if (settingDef.type === 'boolean') {
      el.checked = value === 'true';
    } else {
      el.value = value;
    }
  });
  updateTemplatePreview();
  updateTemplateSections();
}

function populateTemplateDropdowns() {
  const templateSelect = document.getElementById('template-select');
  const currentVal = templateSelect.value;

  templateSelect.innerHTML = `<option value="new">${t('options.appTemplates.createOption')}</option>`;
  adminData.appTemplates.forEach((template) => {
    const option = document.createElement('option');
    option.value = template.name;
    option.textContent = template.name;
    templateSelect.appendChild(option);
  });

  templateSelect.value = [...templateSelect.options].some((o) => o.value === currentVal) ? currentVal : 'new';
}

async function initializeAppTemplatesTab() {
  if (appTemplateTabInitialized) return;

  if (APP_TEMPLATE_SETTINGS.length === 0) {
    try {
      APP_TEMPLATE_SETTINGS = resolveTemplateSchema(await fetchSchema());
    } catch (error) {
      displayStatus(t('options.status.appDataRefreshFailed', { error: error.message }), true);
      return;
    }
  }

  buildTemplateForm();
  updateTemplatePreview();
  updateTemplateSections();
  populateTemplateDropdowns();

  document.getElementById('app-template-form').addEventListener('input', () => {
    updateTemplatePreview();
    updateTemplateSections();
  });
  document.getElementById('save-template-btn').addEventListener('click', saveTemplateProfile);
  document.getElementById('delete-template-btn').addEventListener('click', deleteTemplateProfile);

  document.getElementById('template-select').addEventListener('change', (e) => {
    const selectedValue = e.target.value;
    const isNew = selectedValue === 'new';
    const isDefault = selectedValue === 'Default';
    const deleteBtn = document.getElementById('delete-template-btn');

    document.getElementById('template-name-group').style.display = isNew ? 'flex' : 'none';

    if (!isNew) {
      loadTemplateIntoForm(selectedValue);
      deleteBtn.style.display = isDefault ? 'none' : 'inline-flex';
    } else {
      loadTemplateIntoForm(null);
      deleteBtn.style.display = 'none';
    }
  });

  appTemplateTabInitialized = true;
}

// --- APP LABORATORY ---

function decodeB64(value, what) {
  if (!value) return '';
  try {
    return atob(value);
  } catch (e) {
    console.error(`Failed to decode ${what}:`, e);
    return '';
  }
}

function initializeAppLaboratoryTab() {
  if (appLaboratoryTabInitialized) return;

  const labAppSelect = document.getElementById('lab-app-select');
  const baseAppSelect = document.getElementById('lab-base-app-select');
  const appNameInput = document.getElementById('lab-app-name');
  const iconUploadInput = document.getElementById('lab-icon-upload');
  const iconPreview = document.getElementById('lab-icon-preview');
  const autostartScriptTextarea = document.getElementById('lab-autostart-script');
  const autostartWaylandScriptTextarea = document.getElementById('lab-autostart-wayland-script');
  const usersInput = document.getElementById('lab-app-users');
  const groupsInput = document.getElementById('lab-app-groups');
  const launchWaylandCheckbox = document.getElementById('lab-launch-wayland');
  const launchBtn = document.getElementById('lab-launch-btn');
  const launchBtnText = document.getElementById('lab-launch-btn-text');
  const spinner = document.getElementById('lab-spinner');
  const sessionFrame = document.getElementById('lab-session-frame');
  const mainPlaceholder = document.getElementById('lab-main-placeholder');
  const updateBtn = document.getElementById('lab-update-btn');

  function resetLabForm() {
    labState = { isEditing: false, currentApp: null, currentSessionId: null, base64Icon: '', isDirty: false };
    appNameInput.value = '';
    appNameInput.disabled = false;
    baseAppSelect.value = '';
    baseAppSelect.disabled = false;
    iconPreview.src = 'icons/icon128.png';
    autostartScriptTextarea.value = '';
    autostartWaylandScriptTextarea.value = '';
    usersInput.value = 'all';
    groupsInput.value = 'all';
    launchWaylandCheckbox.checked = true;
    document.getElementById('lab-form').reset();
    labAppSelect.value = 'new';
    updateBtn.disabled = true;
  }

  async function loadLabData(app) {
    labState.isEditing = true;
    labState.currentApp = app;

    appNameInput.value = app.name;
    appNameInput.disabled = true;
    baseAppSelect.value = app.base_app_id;
    baseAppSelect.disabled = true;
    const iconSrc = await formatLogoSrc(app.logo);
    iconPreview.src = iconSrc;
    labState.base64Icon = iconSrc;
    usersInput.value = app.users.join(',');
    groupsInput.value = app.groups.join(',');

    autostartScriptTextarea.value = decodeB64(app.provider_config?.custom_autostart_script_b64, 'autostart script');
    autostartWaylandScriptTextarea.value = decodeB64(app.provider_config?.custom_autostart_wayland_script_b64, 'wayland autostart script');

    labState.isDirty = false;
    updateBtn.disabled = true;
  }

  labAppSelect.addEventListener('change', async (e) => {
    const appId = e.target.value;
    if (appId === 'new') {
      resetLabForm();
    } else {
      const app = adminData.installedApps.find((a) => a.id === appId);
      if (app) loadLabData(app);
    }
  });

  baseAppSelect.addEventListener('change', async () => {
    if (baseAppSelect.disabled) return;

    const selectedBaseAppId = baseAppSelect.value;
    const baseApp = selectedBaseAppId ? adminData.installedApps.find((app) => app.id === selectedBaseAppId) : null;
    let scriptContent = '';
    let waylandScriptContent = '';

    if (baseApp) {
      scriptContent = decodeB64(baseApp.provider_config?.custom_autostart_script_b64, 'base app autostart script');
      waylandScriptContent = decodeB64(baseApp.provider_config?.custom_autostart_wayland_script_b64, 'base app wayland autostart script');
      const iconSrc = await formatLogoSrc(baseApp.logo);
      iconPreview.src = iconSrc;
      labState.base64Icon = iconSrc;
    } else {
      iconPreview.src = 'icons/icon128.png';
      labState.base64Icon = '';
    }

    autostartScriptTextarea.value = scriptContent;
    autostartWaylandScriptTextarea.value = waylandScriptContent;
  });

  function setLabDirty() {
    if (labState.isEditing) {
      labState.isDirty = true;
      updateBtn.disabled = false;
    }
  }

  autostartScriptTextarea.addEventListener('input', setLabDirty);
  autostartWaylandScriptTextarea.addEventListener('input', setLabDirty);
  usersInput.addEventListener('input', setLabDirty);
  groupsInput.addEventListener('input', setLabDirty);

  iconUploadInput.addEventListener('change', (e) => {
    const file = e.target.files[0];
    if (file && file.type === 'image/png') {
      const reader = new FileReader();
      reader.onload = (event) => {
        labState.base64Icon = event.target.result;
        iconPreview.src = labState.base64Icon;
        setLabDirty();
      };
      reader.readAsDataURL(file);
    }
  });

  async function handleLabUpdate() {
    if (!labState.isEditing || !labState.currentApp || !labState.isDirty) return false;

    displayStatus('Updating application settings...');
    updateBtn.disabled = true;

    try {
      const payload = { ...labState.currentApp, provider_config: { ...(labState.currentApp.provider_config || {}) } };
      if (labState.base64Icon.startsWith('data:image')) {
        payload.logo = labState.base64Icon.split(',')[1];
      } else {
        delete payload.logo;
      }
      payload.users = usersInput.value.split(',').map((s) => s.trim()).filter(Boolean);
      payload.groups = groupsInput.value.split(',').map((s) => s.trim()).filter(Boolean);
      payload.provider_config.custom_autostart_script_b64 = btoa(autostartScriptTextarea.value);
      payload.provider_config.custom_autostart_wayland_script_b64 = btoa(autostartWaylandScriptTextarea.value);

      const updatedApp = await secureFetch(`/api/admin/apps/installed/${labState.currentApp.id}`, {
        method: 'PUT',
        body: JSON.stringify(payload),
      });

      const appIndex = adminData.installedApps.findIndex((a) => a.id === updatedApp.id);
      if (appIndex > -1) adminData.installedApps[appIndex] = updatedApp;
      labState.currentApp = updatedApp;
      const iconSrc = await formatLogoSrc(updatedApp.logo);
      iconPreview.src = iconSrc;
      labState.base64Icon = iconSrc;
      labState.isDirty = false;
      displayStatus(t('options.status.appSaved', { name: updatedApp.name, action: t('options.status.appSaveActions.updated') }));
      return true;
    } catch (error) {
      displayStatus(t('options.status.appSaveFailed', { error: error.message }), true);
      return false;
    } finally {
      updateBtn.disabled = !labState.isDirty;
    }
  }

  async function handleLabLaunch() {
    if (labState.currentSessionId) {
      try {
        if (labState.isDirty) {
          const success = await handleLabUpdate();
          if (!success && !await confirmDialog(t, {
            title: t('options.appLaboratory.unsavedTitle'),
            message: t('options.appLaboratory.unsavedClose'),
            confirm: t('common.close'),
            danger: true,
          })) return;
        }
        displayStatus(t('options.status.closingSession'));
        if (info.shell === 'web') await bridge.closeSession(labState.currentSessionId);
        else await secureFetch(`/api/admin/sessions/${labState.currentSessionId}`, { method: 'DELETE' });
        labState.currentSessionId = null;
        sessionFrame.src = 'about:blank';
        sessionFrame.style.display = 'none';
        mainPlaceholder.style.display = 'block';
        launchBtnText.textContent = t('options.appLaboratory.launchButton');
        spinner.style.display = 'none';
        launchBtn.disabled = false;
        displayStatus(t('options.status.sessionClosed'));
        await refreshAppData();
        if (labState.currentApp) {
          const appName = labState.currentApp.name;
          await openTab('InstalledApps');
          const searchInput = document.getElementById('installedApps-search');
          searchInput.value = appName;
          searchInput.dataset.transient = 'true';
          searchInput.dispatchEvent(new Event('input'));
        }
        resetLabForm();
      } catch (error) {
        displayStatus(t('options.status.sessionCloseFailed', { error: error.message }), true);
      }
      return;
    }

    const isCreatingNew = labAppSelect.value === 'new';
    if (!baseAppSelect.value || !appNameInput.value.trim()) {
      displayStatus(t('options.appLaboratory.formInvalid'), true);
      return;
    }

    launchBtn.disabled = true;
    spinner.style.display = 'inline-block';
    launchBtnText.textContent = t('options.appLaboratory.savingAndLaunching');
    // A web sign-in's session is served on an origin of its own, so there it gets a tab instead of a frame in this page.
    if (info.shell === 'web') bridge.reserveTab();

    try {
      let appToLaunch;

      if (isCreatingNew) {
        const payload = {
          name: appNameInput.value,
          base_app_id: baseAppSelect.value,
          logo: labState.base64Icon.startsWith('data:image') ? labState.base64Icon.split(',')[1] : labState.base64Icon,
          custom_autostart_script_b64: btoa(autostartScriptTextarea.value),
          custom_autostart_wayland_script_b64: btoa(autostartWaylandScriptTextarea.value),
          users: usersInput.value.split(',').map((s) => s.trim()).filter(Boolean),
          groups: groupsInput.value.split(',').map((s) => s.trim()).filter(Boolean),
        };
        appToLaunch = await secureFetch('/api/admin/apps/meta', { method: 'POST', body: JSON.stringify(payload) });
        displayStatus(t('options.status.appCreated', { name: appToLaunch.name }));
        populateLabDropdowns();
        labAppSelect.value = appToLaunch.id;
        labAppSelect.dispatchEvent(new Event('change'));
      } else {
        if (labState.isDirty) {
          displayStatus('Saving application settings before launch...');
          const success = await handleLabUpdate();
          if (!success) throw new Error('Failed to save app settings before launching.');
        }
        appToLaunch = labState.currentApp;
      }

      const launchPayload = {
        application_id: appToLaunch.id,
        wayland_mode: launchWaylandCheckbox.checked,
        timezone: browserTimezone(),
      };
      const launchResponse = await secureFetch('/api/admin/launch/meta_customize', { method: 'POST', body: JSON.stringify(launchPayload) }, { timeout: 0 });

      const sessionUrlBase = `https://${config.serverIp}:${config.sessionPort}`;
      const frameUrl = `${sessionUrlBase}${launchResponse.session_url}&embedded=true`;

      labState.currentSessionId = launchResponse.session_url.substring(1).split('/?')[0];
      if (info.shell === 'web') {
        await bridge.openSession(labState.currentSessionId, launchResponse.session_url);
      } else {
        sessionFrame.src = frameUrl;
        sessionFrame.style.display = 'block';
        mainPlaceholder.style.display = 'none';
      }

      launchBtnText.textContent = t('options.appLaboratory.closeButton');
    } catch (error) {
      if (info.shell === 'web') bridge.reserveTab(false);
      const reason = error.message === 'noSessionOrigin' ? t('popup.status.noSessionOrigin') : error.message;
      displayStatus(t('options.status.launchFailed', { error: reason }), true);
      launchBtnText.textContent = t('options.appLaboratory.launchButton');
    } finally {
      spinner.style.display = 'none';
      launchBtn.disabled = false;
    }
  }

  launchBtn.addEventListener('click', handleLabLaunch);
  updateBtn.addEventListener('click', handleLabUpdate);

  populateLabDropdowns();
  resetLabForm();
  appLaboratoryTabInitialized = true;
}

function populateLabDropdowns() {
  const labAppSelect = document.getElementById('lab-app-select');
  const baseAppSelect = document.getElementById('lab-base-app-select');

  if (!labAppSelect || !adminData.installedApps) return;

  const currentLabApp = labAppSelect.value;
  labAppSelect.innerHTML = `<option value="new">${t('options.appLaboratory.createNew')}</option>`;
  adminData.installedApps.filter((app) => app.is_meta_app).forEach((app) => {
    labAppSelect.add(new Option(app.name, app.id));
  });
  if ([...labAppSelect.options].some((o) => o.value === currentLabApp)) {
    labAppSelect.value = currentLabApp;
  } else {
    labAppSelect.value = 'new';
    if (labState.isEditing) labAppSelect.dispatchEvent(new Event('change'));
  }

  const currentBaseApp = baseAppSelect.value;
  baseAppSelect.innerHTML = `<option value="">${t('options.appLaboratory.selectBase')}</option>`;
  adminData.installedApps.filter((app) => !app.is_meta_app).forEach((app) => {
    baseAppSelect.add(new Option(app.name, app.id));
  });
  if ([...baseAppSelect.options].some((o) => o.value === currentBaseApp)) {
    baseAppSelect.value = currentBaseApp;
  }
}

// --- APP LABORATORY (WEB APP) ---

const LAB_POLL_MS = 15000;
const wlab = (name) => document.getElementById(`wlab-${name}`);
// The administrator's open customization session as the server reports it, the meta-app in the form, and its icon.
let labSession = null;
let labApp = null;
let labIcon = '';
let labPoll = null;
let labLaunching = false;

function labGpus() {
  const base = adminData.installedApps.find((app) => app.id === (labApp ? labApp.base_app_id : wlab('base-app-select').value));
  const config = (base && base.provider_config) || {};
  return (adminData.gpus || []).filter((gpu) => (gpu.driver === 'nvidia' ? config.nvidia_support : config.dri3_support));
}

function renderLabGpus() {
  const gpus = labGpus();
  const current = wlab('gpu').value;
  wlab('gpu').innerHTML = `<option value="">${escapeHtml(t('popup.launchView.noGpu'))}</option>`
    + gpus.map((gpu) => `<option value="${escapeHtml(gpu.device)}">${escapeHtml(`${gpu.device.split('/').pop()} (${gpu.driver})`)}</option>`).join('');
  if (gpus.some((gpu) => gpu.device === current)) wlab('gpu').value = current;
  wlab('gpu-group').style.display = gpus.length ? '' : 'none';
}

/** Put a meta-app into the form to edit, or clear the form for a new one. */
async function loadLabApp(app) {
  labApp = app || null;
  const config = (app && app.provider_config) || {};
  wlab('app-select').value = app ? app.id : 'new';
  wlab('base-app-select').value = app ? app.base_app_id || '' : '';
  wlab('app-name').value = app ? app.name : '';
  wlab('app-users').value = app ? app.users.join(',') : 'all';
  wlab('app-groups').value = app ? app.groups.join(',') : 'all';
  wlab('autostart-script').value = decodeB64(config.custom_autostart_script_b64, 'autostart script');
  wlab('autostart-wayland-script').value = decodeB64(config.custom_autostart_wayland_script_b64, 'wayland autostart script');
  labIcon = app ? await formatLogoSrc(app.logo) : '';
  wlab('icon-preview').src = labIcon || 'icons/icon128.png';
  renderLabGpus();
  renderLabState();
}

function populateWebLab() {
  const metas = adminData.installedApps.filter((app) => app.is_meta_app);
  const bases = adminData.installedApps.filter((app) => !app.is_meta_app);
  const names = new Map(adminData.installedApps.map((app) => [app.id, app.name]));
  wlab('app-select').innerHTML = `<option value="new">${escapeHtml(t('options.appLaboratory.createNew'))}</option>`
    + metas.map((app) => `<option value="${escapeHtml(app.id)}">${escapeHtml(app.name)}</option>`).join('');
  wlab('app-select').value = labApp && metas.some((app) => app.id === labApp.id) ? labApp.id : 'new';
  const base = wlab('base-app-select').value;
  wlab('base-app-select').innerHTML = `<option value="">${escapeHtml(t('options.appLaboratory.selectBase'))}</option>`
    + bases.map((app) => `<option value="${escapeHtml(app.id)}">${escapeHtml(app.name)}</option>`).join('');
  wlab('base-app-select').value = labApp ? labApp.base_app_id || '' : base;
  document.querySelector('#wlab-apps-table tbody').innerHTML = metas.length ? metas.map((app) => `
            <tr>
                <td>${escapeHtml(app.name)}</td>
                <td>${escapeHtml(names.get(app.base_app_id) || t('common.na'))}</td>
                <td class="actions-cell"><button type="button" class="warning" data-appid="${escapeHtml(app.id)}">${t('common.edit')}</button></td>
            </tr>`).join('')
    : `<tr class="empty-row"><td colspan="3" style="text-align:center; padding: 2rem;">${t('options.appLaboratory.listNone')}</td></tr>`;
}

/** Show the open session, and hold the form while there is one: it describes the app being customized. */
function renderLabState() {
  const open = Boolean(labSession) || labClosing;
  wlab('open-panel').style.display = open ? 'block' : 'none';
  if (open && labSession) {
    wlab('open-name').textContent = labSession.app_name;
    wlab('open-started').textContent = [t('options.appLaboratory.openStarted', { when: timeAgo(labSession.created_at, t) }), labSession.node].filter(Boolean).join(' · ');
    formatLogoSrc(labSession.app_logo).then((src) => { wlab('open-logo').src = src; });
  }
  wlab('form').querySelectorAll('input, select, textarea, button').forEach((control) => { control.disabled = open || labLaunching; });
  wlab('icon-upload').closest('div').querySelector('label').classList.toggle('disabled', open || labLaunching);
  if (!open && !labLaunching) {
    wlab('app-name').disabled = Boolean(labApp);
    wlab('base-app-select').disabled = Boolean(labApp);
  }
  wlab('busy-note').textContent = open ? t('options.appLaboratory.busy', { name: labSession.app_name }) : '';
  wlab('busy-note').style.display = open ? 'block' : 'none';
  document.querySelectorAll('#wlab-apps-table button').forEach((button) => { button.disabled = open || labLaunching; });
}

/** Ask the server for the open customization session, which only it keeps track of. */
async function refreshLab() {
  try {
    const { session } = await secureFetch('/api/admin/lab', { method: 'GET' });
    const changed = (session && session.session_id) !== (labSession && labSession.session_id);
    labSession = session || null;
    if (changed && labSession) {
      const app = adminData.installedApps.find((a) => a.id === labSession.app_id);
      if (app) await loadLabApp(app);
    }
  } catch (error) {
    console.warn('Could not read the App Laboratory session:', error);
  }
  renderLabState();
}

/**
 * Save the form as a meta-app: a new one, or the one being edited.
 *
 * @returns {Promise<object>} The saved application.
 */
async function saveLabApp() {
  const users = splitList(wlab('app-users').value);
  const groups = splitList(wlab('app-groups').value);
  const scripts = {
    custom_autostart_script_b64: btoa(wlab('autostart-script').value),
    custom_autostart_wayland_script_b64: btoa(wlab('autostart-wayland-script').value),
  };
  let saved;
  if (labApp) {
    const payload = { ...labApp, users, groups, provider_config: { ...(labApp.provider_config || {}), ...scripts } };
    if (labIcon.startsWith('data:image')) payload.logo = labIcon.split(',')[1];
    else delete payload.logo;
    saved = await secureFetch(`/api/admin/apps/installed/${labApp.id}`, { method: 'PUT', body: JSON.stringify(payload) });
  } else {
    if (!wlab('base-app-select').value || !wlab('app-name').value.trim()) throw new Error(t('options.appLaboratory.formInvalid'));
    saved = await secureFetch('/api/admin/apps/meta', {
      method: 'POST',
      body: JSON.stringify({
        name: wlab('app-name').value.trim(),
        base_app_id: wlab('base-app-select').value,
        logo: labIcon.startsWith('data:image') ? labIcon.split(',')[1] : labIcon,
        users,
        groups,
        ...scripts,
      }),
    });
  }
  labApp = saved;
  await refreshAppData();
  await loadLabApp(adminData.installedApps.find((app) => app.id === saved.id) || saved);
  return saved;
}

/** Save the meta-app and open its customization session, in a tab that shows the launch's progress. */
async function launchLab() {
  const id = crypto.randomUUID();
  const name = wlab('app-name').value.trim();
  try {
    await request('reserveTab', { reserve: true, launch: { id, app: name, logo: (labApp && labApp.logo) || '', room: false } });
  } catch (error) {
    const reason = { noSessionOrigin: t('popup.status.noSessionOrigin'), popupBlocked: t('web.home.popupBlocked') }[error.message] || error.message;
    displayStatus(escapeHtml(reason), true);
    return;
  }
  // The launching page asks for news when it loads, which may be after a failure.
  const channel = new BroadcastChannel(`sealskin-launch-${id}`);
  let news = null;
  const tell = (message) => {
    news = message;
    channel.postMessage(message);
  };
  channel.onmessage = (event) => { if (event.data && event.data.hello && news) channel.postMessage(news); };
  labLaunching = true;
  wlab('spinner').style.display = 'inline-block';
  renderLabState();
  try {
    const app = await saveLabApp();
    tell({ posted: true });
    const data = await secureFetch('/api/admin/launch/meta_customize', {
      method: 'POST',
      body: JSON.stringify({
        launch_id: id,
        application_id: app.id,
        language: wlab('language').value,
        timezone: browserTimezone(),
        selected_gpu: wlab('gpu').value || null,
        wayland_mode: wlab('launch-wayland').checked,
      }),
    }, { timeout: 0 });
    await request('openSession', { sessionId: data.session_id, sessionUrl: data.session_url, launchId: id });
    channel.close();
  } catch (error) {
    tell({ error: error.message });
    setTimeout(() => channel.close(), 120000);
    displayStatus(t('options.status.launchFailed', { error: escapeHtml(error.message) }), true);
  }
  labLaunching = false;
  wlab('spinner').style.display = 'none';
  await refreshLab();
}

// Whether the open session is being closed, so the panel keeps showing that through refreshes.
let labClosing = false;

/** Show what closing the session is doing: a stage, the saved template, or a failure to retry. */
function showLabClosing(text, failed = false) {
  wlab('open-actions').style.display = labClosing || failed ? 'none' : '';
  wlab('closing').style.display = labClosing || failed ? '' : 'none';
  wlab('closing-spinner').style.display = labClosing && !failed ? '' : 'none';
  wlab('closing-status').textContent = text;
  wlab('closing-status').classList.toggle('error', failed);
  wlab('close-retry').style.display = failed ? '' : 'none';
}

/**
 * Close the customization session and keep its home directory as the
 * template, following the server's progress alongside the request.
 */
async function closeLab() {
  if (labClosing) return;
  labClosing = true;
  wlab('close-btn').disabled = true;
  const progressId = crypto.randomUUID();
  const stages = { stopping: 'options.appLaboratory.closingStopping', saving: 'options.appLaboratory.closingSaving' };
  showLabClosing(t(stages.stopping));
  let failure = null;
  const poll = setInterval(async () => {
    try {
      const progress = await secureFetch(`/api/launch/progress/${progressId}`, { method: 'GET' });
      if (progress.stage === 'failed') failure = progress.error || t('options.appLaboratory.closingFailed');
      else if (stages[progress.stage]) showLabClosing(t(stages[progress.stage]));
    } catch (e) { /* not known to the server yet */ }
  }, 500);
  let kept = null;
  try {
    kept = await secureFetch(`/api/admin/lab?progress_id=${progressId}`, { method: 'DELETE' });
  } catch (error) {
    failure = error.message;
  }
  clearInterval(poll);
  labClosing = false;
  wlab('close-btn').disabled = false;
  if (failure) {
    showLabClosing(failure, true);
    return;
  }
  const saved = t('options.appLaboratory.closingSaved', { files: kept.files || 0, size: formatBytes(kept.bytes || 0, t) });
  showLabClosing(saved);
  displayStatus(escapeHtml(saved));
  await refreshLab();
  await refreshAppData();
  showLabClosing('');
}

async function openWebLab() {
  if (!wlab('language').options.length) {
    wlab('language').innerHTML = Object.entries(supportedLangs)
      .map(([name, value]) => `<option value="${escapeHtml(value)}">${escapeHtml(name)}</option>`).join('');
    const [lang, region = ''] = currentLocale().split('-');
    const wanted = `${lang.toLowerCase()}_${region.toUpperCase()}.UTF-8`;
    wlab('language').value = Object.values(supportedLangs).includes(wanted) ? wanted : 'en_US.UTF-8';
  }
  if (adminData.installedApps.length === 0) await refreshAppData();
  populateWebLab();
  renderLabGpus();
  await refreshLab();
  labPoll = setInterval(() => { if (!document.hidden && !labLaunching) refreshLab(); }, LAB_POLL_MS);
}

function bindWebLabEvents() {
  wlab('app-select').addEventListener('change', (e) => loadLabApp(adminData.installedApps.find((app) => app.id === e.target.value)));
  wlab('base-app-select').addEventListener('change', async () => {
    const base = adminData.installedApps.find((app) => app.id === wlab('base-app-select').value);
    const config = (base && base.provider_config) || {};
    wlab('autostart-script').value = decodeB64(config.custom_autostart_script_b64, 'base app autostart script');
    wlab('autostart-wayland-script').value = decodeB64(config.custom_autostart_wayland_script_b64, 'base app wayland autostart script');
    labIcon = base ? await formatLogoSrc(base.logo) : '';
    wlab('icon-preview').src = labIcon || 'icons/icon128.png';
    renderLabGpus();
  });
  wlab('icon-upload').addEventListener('change', (e) => {
    const file = e.target.files[0];
    if (!file || file.type !== 'image/png') return;
    const reader = new FileReader();
    reader.onload = () => {
      labIcon = reader.result;
      wlab('icon-preview').src = labIcon;
    };
    reader.readAsDataURL(file);
  });
  wlab('update-btn').addEventListener('click', async () => {
    try {
      const saved = await saveLabApp();
      displayStatus(t('options.status.appSaved', { name: escapeHtml(saved.name), action: t('options.status.appSaveActions.updated') }));
    } catch (error) {
      displayStatus(t('options.status.appSaveFailed', { error: escapeHtml(error.message) }), true);
    }
  });
  wlab('launch-btn').addEventListener('click', launchLab);
  wlab('reopen-btn').addEventListener('click', async () => {
    try {
      await bridge.focusSession(labSession);
    } catch (error) {
      displayStatus(escapeHtml(error.message === 'noSessionOrigin' ? t('popup.status.noSessionOrigin') : error.message), true);
    }
  });
  wlab('close-btn').addEventListener('click', async () => {
    const close = await confirmDialog(t, {
      title: t('options.appLaboratory.closeTitle'),
      message: t('options.appLaboratory.confirmClose', { name: labSession.app_name }),
      confirm: t('options.appLaboratory.closeAndSave'),
    });
    if (close) closeLab();
  });
  wlab('close-retry').addEventListener('click', closeLab);
  document.querySelector('#wlab-apps-table tbody').addEventListener('click', (e) => {
    const button = e.target.closest('button[data-appid]');
    if (!button) return;
    loadLabApp(adminData.installedApps.find((app) => app.id === button.dataset.appid));
    wlab('form').scrollIntoView({ block: 'start' });
  });
}

// --- CLUSTER ---

const nodeLabel = (node) => node.name || node.id;

/**
 * Ask which node to move a home directory to.
 *
 * @param {string} title
 * @param {Array<object>} nodes Nodes to offer, as the cluster endpoints list them.
 * @returns {Promise<string>} The chosen node's id; never settles when the dialog is closed.
 */
function pickNode(title, nodes) {
  const modal = document.getElementById('node-pick-modal');
  const select = document.getElementById('node-pick-select');
  document.getElementById('node-pick-title').textContent = title;
  select.innerHTML = nodes.map((node) => {
    const offline = node.alive === false ? ` (${t('options.cluster.offline')})` : '';
    return `<option value="${escapeHtml(node.id)}">${escapeHtml(`${nodeLabel(node)} · ${node.pool}${offline}`)}</option>`;
  }).join('');
  modal.style.display = 'block';
  return new Promise((resolve) => {
    document.getElementById('node-pick-form').onsubmit = (event) => {
      event.preventDefault();
      modal.style.display = 'none';
      resolve(select.value);
    };
  });
}

/**
 * Move a home directory to a node the user picks.
 *
 * @param {string} url The move endpoint of the home.
 * @param {string} homeName
 * @param {Array<object>} nodes Nodes to offer.
 * @param {function} reload Called once the move ends, either way.
 */
async function moveHome(url, homeName, nodes, reload) {
  if (!nodes.length) {
    displayStatus(t('options.home.noOtherNode'), true);
    return;
  }
  const node = await pickNode(t('options.home.moveTitle', { homeName }), nodes);
  try {
    displayStatus(t('options.status.homedirMoving', { homeName }));
    await secureFetch(url, { method: 'POST', body: JSON.stringify({ node }) }, { timeout: 0 });
    displayStatus(t('options.status.homedirMoved', { homeName }));
  } catch (error) {
    displayStatus(t('options.status.homedirMoveFailed', { error: error.message }), true);
  }
  await reload();
}

function renderClusterNodes() {
  const tbody = document.querySelector('#cluster-nodes-table tbody');
  const pools = Object.keys(clusterData.pools).sort();
  tbody.innerHTML = clusterData.nodes.map((node) => {
    const id = escapeHtml(node.id);
    let state = node.alive ? t('options.cluster.online') : t('options.cluster.offline');
    if (!node.approved) state = t('options.cluster.waiting');
    const notes = [];
    if (!node.alive && node.last_seen) notes.push(t('options.cluster.lastSeen', { when: timeAgo(node.last_seen, t) }));
    if (node.alive && !node.store_reachable) notes.push(t('options.cluster.storeUnreachableNode'));
    if (!node.alive && node.error) notes.push(node.error);
    if (node.roles && node.roles.length) notes.push(node.roles.join(', '));
    const load = typeof node.load === 'number' ? node.load.toFixed(2) : t('common.na');
    const capacity = [];
    if (node.cpus) capacity.push(t('options.cluster.cpus', { count: node.cpus }));
    if (node.gpus.length) capacity.push(t('options.cluster.gpus', { count: node.gpus.length }));
    const sessions = `${node.sessions}${node.max_sessions > 0 ? ` / ${node.max_sessions}` : ''}`;
    const gpuSessions = node.gpus.length || node.gpu_sessions
      ? `<small>${escapeHtml(t('options.cluster.gpuSessions', { used: node.gpu_sessions, slots: node.gpu_slots > 0 ? node.gpu_slots : '∞' }))}</small>`
      : '';
    const approve = node.approved
      ? (node.self ? '' : `<button class="warning" data-action="suspend" data-node="${id}">${t('options.cluster.suspend')}</button>`)
      : `<button class="primary" data-action="approve" data-node="${id}">${t('options.cluster.approve')}</button>`;
    return `
            <tr>
                <td>
                    <div>${escapeHtml(nodeLabel(node))}${node.self ? ` <small>(${escapeHtml(t('options.cluster.thisNode'))})</small>` : ''}</div>
                    <small title="${id}">${escapeHtml(node.public_url || node.address || '')}</small>
                </td>
                <td>
                    <select data-node="${id}" style="min-width: 8rem;">
                        ${[...new Set([...pools, node.pool])].map((pool) => `<option value="${escapeHtml(pool)}"${pool === node.pool ? ' selected' : ''}>${escapeHtml(pool)}</option>`).join('')}
                    </select>
                </td>
                <td><div>${escapeHtml(state)}</div><small>${escapeHtml(notes.join(' · '))}</small></td>
                <td><div>${escapeHtml(load)}</div><small>${escapeHtml(capacity.join(' · '))}</small></td>
                <td><div>${escapeHtml(sessions)}</div>${gpuSessions}</td>
                <td>${escapeHtml(node.version || t('common.na'))}</td>
                <td class="actions-cell">
                    <div class="cell-wrapper">
                        ${approve}
                        ${node.self ? '' : `<button class="danger" data-action="remove" data-node="${id}">${t('options.cluster.remove')}</button>`}
                    </div>
                </td>
            </tr>`;
  }).join('');
}

function renderClusterPools() {
  const tbody = document.querySelector('#cluster-pools-table tbody');
  const names = Object.keys(clusterData.pools).sort();
  tbody.innerHTML = names.map((name) => {
    const pool = clusterData.pools[name];
    const nodes = clusterData.nodes.filter((node) => node.pool === name).length;
    const cost = pool.gpu_cost === undefined || pool.gpu_cost === null
      ? String(pool.cost)
      : t('options.cluster.poolCostWithGpu', { cost: pool.cost, gpuCost: pool.gpu_cost });
    return `
            <tr>
                <td><div>${escapeHtml(name)}</div><small>${escapeHtml(pool.description || '')}</small></td>
                <td>${escapeHtml(pool.domain || t('common.none'))}</td>
                <td>${escapeHtml(t(pool.restricted ? 'options.cluster.poolRestricted' : 'options.cluster.poolOpen'))}</td>
                <td>${escapeHtml(cost)}</td>
                <td>${nodes}</td>
                <td class="actions-cell">
                    <div class="cell-wrapper">
                        <button class="warning" data-pool="${escapeHtml(name)}">${t('common.edit')}</button>
                        <button class="danger" data-pool="${escapeHtml(name)}">${t('common.delete')}</button>
                    </div>
                </td>
            </tr>`;
  }).join('');

  const joinPool = document.getElementById('cluster-join-pool');
  const chosen = joinPool.value;
  joinPool.innerHTML = names.map((name) => `<option value="${escapeHtml(name)}">${escapeHtml(name)}</option>`).join('');
  if (names.includes(chosen)) joinPool.value = chosen;
}

/** Put a pool into the pool form to edit, or clear the form for a new one. */
function editPool(name) {
  const pool = name ? clusterData.pools[name] : { cost: 1 };
  const nameInput = document.getElementById('cluster-pool-name');
  nameInput.value = name || '';
  nameInput.readOnly = Boolean(name);
  document.getElementById('cluster-pool-form-title').textContent = name ? t('options.cluster.poolEdit', { name }) : t('options.cluster.poolNew');
  document.getElementById('cluster-pool-description').value = pool.description || '';
  document.getElementById('cluster-pool-domain').value = pool.domain || '';
  document.getElementById('cluster-pool-cost').value = pool.cost;
  document.getElementById('cluster-pool-gpu-cost').value = pool.gpu_cost ?? '';
  document.getElementById('cluster-pool-users').value = (pool.users || []).join(', ');
  document.getElementById('cluster-pool-groups').value = (pool.groups || []).join(', ');
  document.getElementById('cluster-pool-restricted').checked = Boolean(pool.restricted);
  document.getElementById('cluster-pool-stop').checked = Boolean(pool.stop_when_spent);
}

function renderClusterStore() {
  const { store } = clusterData;
  document.getElementById('cluster-store-kind').textContent = tOr(t, `options.cluster.storeKinds.${store.kind}`, store.kind);
  document.getElementById('cluster-store-detail').textContent = store.detail || '';
  document.getElementById('cluster-store-detail-row').style.display = store.detail ? 'block' : 'none';
  document.getElementById('cluster-store-reachable').textContent = t(store.reachable ? 'options.cluster.reachable' : 'options.cluster.unreachable');
  document.getElementById('cluster-public-url').textContent = clusterData.public_url || t('common.na');
  document.getElementById('cluster-session-domain').textContent = !clusterData.session_isolation ? t('options.cluster.sessionDomainOff')
    : clusterData.session_domain || t('options.cluster.sessionDomainUnset');
}

async function refreshClusterUsage() {
  const tbody = document.querySelector('#cluster-usage-table tbody');
  const period = document.getElementById('cluster-usage-period').value;
  try {
    const { hours } = await secureFetch(`/api/admin/cluster/usage?period=${period}`, { method: 'GET' });
    const rows = Object.entries(hours).sort((a, b) => b[1] - a[1]);
    tbody.innerHTML = rows.length
      ? rows.map(([username, used]) => `<tr><td>${escapeHtml(username)}</td><td>${escapeHtml(used)}</td></tr>`).join('')
      : `<tr class="empty-row"><td colspan="2" style="text-align:center; padding: 2rem;">${t('options.cluster.usageNone')}</td></tr>`;
  } catch (error) {
    tbody.innerHTML = `<tr class="empty-row"><td colspan="2" style="text-align:center; padding: 2rem;">${escapeHtml(error.message)}</td></tr>`;
  }
}

async function refreshCluster() {
  try {
    clusterData = await secureFetch('/api/admin/cluster', { method: 'GET' });
  } catch (error) {
    displayStatus(t('options.status.clusterLoadFailed', { error: error.message }), true);
    return;
  }
  renderClusterNodes();
  renderClusterPools();
  renderClusterStore();
  renderClusterSettings();
  if (info.shell === 'web') renderSignIn();
  await refreshClusterUsage();
}

/** Run a cluster write, then show the cluster as it is now, which is also what a conflict asks for. */
async function clusterWrite(url, options, done) {
  try {
    const result = await secureFetch(url, options);
    displayStatus(done);
    await refreshCluster();
    return result;
  } catch (error) {
    displayStatus(t('options.status.clusterWriteFailed', { error: error.message }), true);
    if (error.status === 409) await refreshCluster();
    return null;
  }
}

function bindClusterEvents() {
  document.getElementById('cluster-refresh-btn').addEventListener('click', refreshCluster);

  const nodes = document.querySelector('#cluster-nodes-table tbody');
  nodes.addEventListener('change', (e) => {
    const select = e.target.closest('select[data-node]');
    if (!select) return;
    clusterWrite(`/api/admin/cluster/nodes/${select.dataset.node}`, { method: 'PUT', body: JSON.stringify({ pool: select.value }) }, t('options.status.nodeUpdated'));
  });
  nodes.addEventListener('click', async (e) => {
    const button = e.target.closest('button[data-action]');
    if (!button) return;
    const url = `/api/admin/cluster/nodes/${button.dataset.node}`;
    const node = clusterData.nodes.find((n) => n.id === button.dataset.node);
    const { action } = button.dataset;
    if (action === 'remove') {
      const remove = await confirmDialog(t, {
        title: t('options.cluster.removeTitle'),
        message: t('options.cluster.confirmRemove', { name: nodeLabel(node) }),
        confirm: t('common.remove'),
        danger: true,
      });
      if (remove) clusterWrite(url, { method: 'DELETE' }, t('options.status.nodeRemoved'));
    } else {
      clusterWrite(url, { method: 'PUT', body: JSON.stringify({ approved: action === 'approve' }) }, t('options.status.nodeUpdated'));
    }
  });

  document.getElementById('cluster-join-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const pool = document.getElementById('cluster-join-pool').value;
    try {
      const issued = await secureFetch('/api/admin/cluster/join_codes', { method: 'POST', body: JSON.stringify(pool ? { pool } : {}) });
      document.getElementById('cluster-join-env').value = `SEALSKIN_JOIN_URL=${issued.join_url}\nSEALSKIN_JOIN_CODE=${issued.code}`;
      document.getElementById('cluster-join-expires').textContent = t('options.cluster.joinExpires', { when: formatDate(issued.expires) });
      document.getElementById('cluster-join-result').style.display = 'block';
    } catch (error) {
      document.getElementById('cluster-join-result').style.display = 'none';
      displayStatus(error.message, true);
    }
  });
  document.getElementById('cluster-join-copy').addEventListener('click', () => navigator.clipboard.writeText(document.getElementById('cluster-join-env').value)
    .then(() => displayStatus(t('options.status.copySuccess')), () => displayStatus(t('options.status.copyFailed'), true)));

  document.querySelector('#cluster-pools-table tbody').addEventListener('click', async (e) => {
    const button = e.target.closest('button[data-pool]');
    if (!button) return;
    const name = button.dataset.pool;
    if (button.classList.contains('warning')) {
      editPool(name);
      document.getElementById('cluster-pool-form').scrollIntoView({ block: 'nearest' });
      return;
    }
    const remove = await confirmDialog(t, {
      title: t('options.cluster.deletePoolTitle'),
      message: t('options.cluster.confirmDeletePool', { name }),
      confirm: t('common.delete'),
      danger: true,
    });
    if (remove) clusterWrite(`/api/admin/cluster/pools/${encodeURIComponent(name)}`, { method: 'DELETE' }, t('options.status.poolDeleted', { name }));
  });
  document.getElementById('cluster-pool-reset').addEventListener('click', () => editPool(null));
  document.getElementById('cluster-pool-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const name = document.getElementById('cluster-pool-name').value.trim();
    const number = (id) => parseFloat(document.getElementById(id).value);
    const gpuCost = number('cluster-pool-gpu-cost');
    const body = {
      description: document.getElementById('cluster-pool-description').value.trim(),
      domain: document.getElementById('cluster-pool-domain').value.trim(),
      restricted: document.getElementById('cluster-pool-restricted').checked,
      users: splitList(document.getElementById('cluster-pool-users').value),
      groups: splitList(document.getElementById('cluster-pool-groups').value),
      cost: Number.isFinite(number('cluster-pool-cost')) ? number('cluster-pool-cost') : 1,
      gpu_cost: Number.isFinite(gpuCost) ? gpuCost : null,
      stop_when_spent: document.getElementById('cluster-pool-stop').checked,
    };
    const saved = await clusterWrite(`/api/admin/cluster/pools/${encodeURIComponent(name)}`, { method: 'PUT', body: JSON.stringify(body) }, t('options.status.poolSaved', { name }));
    if (saved) editPool(null);
  });

  document.getElementById('cluster-settings-form').addEventListener('submit', (e) => {
    e.preventDefault();
    const body = changedSettings(e.target.dataset.settings.split(','));
    if (!Object.keys(body).length) {
      displayStatus(t('options.status.nothingChanged'));
      return;
    }
    clusterWrite('/api/admin/cluster/settings', { method: 'PUT', body: JSON.stringify(body) }, t('options.status.settingsSaved'));
  });

  document.getElementById('cluster-usage-period').addEventListener('change', refreshClusterUsage);
}

// The settings an administrator may write for every node, and the control each takes.
const CLUSTER_SETTINGS = {
  oidc_issuer: 'text',
  oidc_client_id: 'text',
  oidc_client_secret: 'secret',
  oidc_scopes: 'text',
  saml_metadata_url: 'text',
  saml_username_attribute: 'text',
  saml_groups_attribute: 'text',
  sso_username_claim: 'text',
  sso_groups_claim: 'text',
  sso_admin_group: 'text',
  sso_max_age_seconds: 'hours',
  sso_force_login: 'bool',
  sso_create_users: 'bool',
  sso_hold_new_users: 'bool',
  proxy_auth_user_header: 'text',
  proxy_auth_groups_header: 'text',
  proxy_auth_logout_url: 'text',
  web_session_seconds: 'hours',
  files_sync: 'sync',
};
// What the web app's Sign In section shows, card by card; the other shells keep one form in the Cluster section.
const SIGNIN_CARDS = {
  oidc: ['oidc_issuer', 'oidc_client_id', 'oidc_client_secret', 'oidc_scopes'],
  saml: ['saml_metadata_url', 'saml_username_attribute', 'saml_groups_attribute'],
  proxy: ['proxy_auth_user_header', 'proxy_auth_groups_header', 'proxy_auth_logout_url'],
  who: ['sso_username_claim', 'sso_groups_claim', 'sso_admin_group', 'sso_max_age_seconds', 'web_session_seconds', 'sso_create_users', 'sso_hold_new_users', 'sso_force_login'],
};
// Issuer URL shapes of common OpenID Connect providers; a preset fills the hints and stores nothing.
const OIDC_PRESETS = {
  authelia: 'https://<host>',
  authentik: 'https://<host>/application/o/<slug>/',
  keycloak: 'https://<host>/realms/<realm>',
  pocketid: 'https://<host>',
  tinyauth: 'https://<host>',
  entra: 'https://login.microsoftonline.com/<tenant>/v2.0',
  google: 'https://accounts.google.com',
  okta: 'https://<org>.okta.com',
  other: '',
};

// The card whose save is being shown: its controls take the saved values, not what was typed.
let savedSignInCard = null;

const hoursOf = (secondsValue) => String(Math.round((Number(secondsValue) / 3600) * 100) / 100);
// A setting's value as its control shows it.
const shownSetting = (name) => {
  const value = clusterData.settings[name];
  return CLUSTER_SETTINGS[name] === 'hours' ? hoursOf(value) : String(value ?? '');
};

/** The row of one cluster setting, saying whether its value is written for the cluster or a node's own. */
function settingField(name) {
  const kind = CLUSTER_SETTINGS[name];
  const id = `cluster-setting-${name}`;
  const written = clusterData.written_settings.includes(name);
  const hint = t(written ? 'options.cluster.settingWritten' : 'options.cluster.settingFromEnvironment');
  let control;
  if (kind === 'bool' || kind === 'sync') {
    const choices = kind === 'bool'
      ? [['true', t('options.groups.on')], ['false', t('options.groups.off')]]
      : ['auto', 'on', 'off'].map((choice) => [choice, choice]);
    const current = written ? String(clusterData.settings[name]) : '';
    control = `<select id="${id}">
                <option value="">${escapeHtml(t('options.cluster.settingInherit', { value: String(clusterData.settings[name]) }))}</option>
                ${choices.map(([choice, label]) => `<option value="${choice}"${choice === current ? ' selected' : ''}>${escapeHtml(label)}</option>`).join('')}
            </select>`;
  } else if (kind === 'secret') {
    const set = clusterData.secret_set[name];
    control = `<input type="text" id="${id}" class="masked" autocomplete="off" autocapitalize="off" spellcheck="false" placeholder="${escapeHtml(t(set ? 'options.cluster.secretSet' : 'options.cluster.secretUnset'))}">
            ${written ? `<label class="field-aside"><input type="checkbox" id="${id}-clear"> <span>${escapeHtml(t('options.cluster.secretClear'))}</span></label>` : ''}`;
  } else {
    control = `<input type="${kind === 'hours' ? 'number' : 'text'}"${kind === 'hours' ? ' min="0" step="any"' : ''} id="${id}" value="${escapeHtml(shownSetting(name))}">`;
  }
  return fieldHtml({ id, label: t(`options.cluster.settings.${name}`), description: hint, control, kind: written ? 'setting-written' : '' });
}

/** @returns {object} What the controls of `names` change; an empty string hands a setting back to the nodes. */
function changedSettings(names) {
  const written = new Set(clusterData.written_settings);
  const body = {};
  names.forEach((name) => {
    const kind = CLUSTER_SETTINGS[name];
    const input = document.getElementById(`cluster-setting-${name}`);
    if (!input) return;
    const value = input.value.trim();
    if (kind === 'secret') {
      const clear = document.getElementById(`cluster-setting-${name}-clear`);
      if (value) body[name] = value;
      else if (clear && clear.checked) body[name] = '';
    } else if (kind === 'bool' || kind === 'sync') {
      const current = written.has(name) ? String(clusterData.settings[name]) : '';
      if (value !== current) body[name] = kind === 'bool' && value ? value === 'true' : value;
    } else if (value !== shownSetting(name)) {
      body[name] = kind === 'hours' && value ? Math.round(Number(value) * 3600) : value;
    }
  });
  return body;
}

function renderClusterSettings() {
  // The web app has a section for signing in; elsewhere every setting stays here.
  const names = info.shell === 'web' ? ['files_sync'] : Object.keys(CLUSTER_SETTINGS);
  document.getElementById('cluster-settings-form').dataset.settings = names.join(',');
  document.getElementById('cluster-settings-fields').innerHTML = names.map(settingField).join('');
  markClampedDescriptions(document.getElementById('cluster-settings-fields'));
}

// The headers and logout page of the proxies' identity providers; a preset fills the fields and stores nothing until saved.
const PROXY_PRESETS = {
  authelia: { user: 'Remote-User', groups: 'Remote-Groups', logout: 'https://<auth host>/logout' },
  authentik: { user: 'X-authentik-username', groups: 'X-authentik-groups', logout: 'https://<this host>/outpost.goauthentik.io/sign_out' },
  tinyauth: { user: 'Remote-User', groups: 'Remote-Groups', logout: 'https://<tinyauth host>/logout' },
  oauth2proxy: { user: 'X-Auth-Request-Preferred-Username', groups: 'X-Auth-Request-Groups', logout: 'https://<this host>/oauth2/sign_out' },
};

function signInPill(state) {
  return `<span class="pill ${state}">${escapeHtml(t(`options.signin.pill.${state}`))}</span>`;
}

function copyRows(rows) {
  return `<div class="copy-rows">${rows.filter(([, value]) => value).map(([label, value]) => `
            <div class="copy-row">
                <span>${escapeHtml(t(label))}</span>
                <code>${escapeHtml(value)}</code>
                <button type="button" class="secondary" data-copy="${escapeHtml(value)}" title="${escapeHtml(t('common.copy'))}"><i class="fas fa-copy"></i></button>
            </div>`).join('')}</div>`;
}

function signInCard(kind, title, state, body, test) {
  return `
    <form class="card signin-card" data-card="${kind}">
        <div class="card-header"><h3>${escapeHtml(t(title))}</h3>${signInPill(state)}</div>
        ${body}
        ${SIGNIN_CARDS[kind] ? `<div class="button-group">
            <button type="submit" class="primary"><i class="fas fa-save"></i> ${escapeHtml(t('common.save'))}</button>
            ${test ? `<button type="button" class="secondary" data-test="${kind}"><i class="fas fa-plug"></i> ${escapeHtml(t('options.signin.test'))}</button>` : ''}
        </div>
        <div class="signin-result" id="signin-result-${kind}"></div>` : ''}
    </form>`;
}

/** What the node's check of the reverse proxy found, as the line under the proxy card's fields. */
function proxyCheckLine(check) {
  if (!check || check.state === 'off') return '';
  if (check.unchecked) return `<p class="status-message error">${escapeHtml(t('options.signin.proxyCheck.unchecked'))}</p>`;
  if (check.state === 'guarded') return `<div class="signin-ok"><p><i class="fas fa-check-circle"></i> ${escapeHtml(t('options.signin.proxyCheck.guarded'))}</p></div>`;
  return `<p class="status-message error">${escapeHtml(t(`options.signin.proxyCheck.${check.state}`))} ${escapeHtml(check.detail || '')}</p>`;
}

/** The proxy card's pill: active only while the proxy's header is believed. */
function proxyState(signin, settings) {
  if (!settings.proxy_auth_user_header) return 'none';
  const check = signin.proxy_check || {};
  return signin.trusted_proxies && (check.unchecked || check.state === 'guarded') ? 'active' : 'off';
}

/** Draw the Sign In section: one card per way of signing in, each saved on its own. */
function renderSignIn() {
  const container = document.getElementById('signin-cards');
  const { signin = { enabled: {}, urls: {}, trusted_proxies: '' }, settings } = clusterData;
  const { enabled, urls } = signin;
  // What is typed into a card and not saved survives another card's save.
  const typed = new Map([...container.querySelectorAll('input[id], select[id]')]
    .filter((control) => !control.classList.contains('masked') && control.closest('.signin-card').dataset.card !== savedSignInCard)
    .map((control) => [control.id, control.type === 'checkbox' ? control.checked : control.value]));
  const fields = (kind) => `<div class="field-list single">${SIGNIN_CARDS[kind].map(settingField).join('')}</div>`;
  const stateOf = (kind, configured) => (enabled[kind] ? 'active' : configured ? 'off' : 'none');
  const sentence = (key) => `<p class="description">${escapeHtml(t(key))}</p>`;

  const presets = Object.keys(OIDC_PRESETS).map((preset) => `<option value="${preset}">${escapeHtml(t(`options.signin.presets.${preset}.name`))}</option>`).join('');
  const oidc = `
        <div class="field-list single">
            <div class="field">
                <div class="field-text"><label for="signin-oidc-preset">${escapeHtml(t('options.signin.provider'))}</label><p class="description" id="signin-oidc-hint">${escapeHtml(t('options.signin.providerHelp'))}</p></div>
                <div class="field-control"><select id="signin-oidc-preset"><option value="">${escapeHtml(t('options.signin.providerChoose'))}</option>${presets}</select></div>
            </div>
        </div>
        ${fields('oidc')}
        <h5 class="field-group">${escapeHtml(t('options.signin.register'))}</h5>
        ${copyRows([['options.signin.oidcRedirect', urls.oidc_redirect], ['options.signin.oidcBackchannel', urls.oidc_backchannel_logout], ['options.signin.oidcFrontchannel', urls.oidc_frontchannel_logout]])}`;
  const saml = `
        ${fields('saml')}
        <h5 class="field-group">${escapeHtml(t('options.signin.register'))}</h5>
        ${copyRows([['options.signin.samlEntity', urls.saml_entity_id], ['options.signin.samlAcs', urls.saml_acs], ['options.signin.samlSlo', urls.saml_slo]])}
        ${urls.saml_entity_id ? `<p><a href="${escapeHtml(urls.saml_entity_id)}" target="_blank" rel="noopener noreferrer">${escapeHtml(t('options.signin.samlMetadata'))}</a></p>` : ''}`;
  const untrusted = settings.proxy_auth_user_header && !signin.trusted_proxies;
  const proxyPresets = Object.keys(PROXY_PRESETS).map((preset) => `<option value="${preset}">${escapeHtml(t(`options.signin.proxyPresets.${preset}`))}</option>`).join('');
  const proxy = `
        ${sentence('options.signin.proxyHelp')}
        <div class="field-list single">
            <div class="field">
                <div class="field-text"><label for="signin-proxy-preset">${escapeHtml(t('options.signin.provider'))}</label></div>
                <div class="field-control"><select id="signin-proxy-preset"><option value="">${escapeHtml(t('options.signin.proxyPresetChoose'))}</option>${proxyPresets}</select></div>
            </div>
        </div>
        ${fields('proxy')}
        <p><span>${escapeHtml(t('options.signin.trustedProxies'))}</span> <code>${escapeHtml(signin.trusted_proxies || t('common.none'))}</code></p>
        ${sentence('options.signin.trustedProxiesHelp')}
        ${untrusted ? `<p class="status-message error">${escapeHtml(t('options.signin.noTrustedProxy'))}</p>` : proxyCheckLine(signin.proxy_check)}`;
  const who = `${sentence('options.signin.whoHelp')}${fields('who')}`;

  container.innerHTML = [
    reachCard(signin),
    signInCard('oidc', 'options.signin.oidcTitle', stateOf('oidc', settings.oidc_issuer), oidc, true),
    signInCard('saml', 'options.signin.samlTitle', stateOf('saml', settings.saml_metadata_url), saml, true),
    signInCard('proxy', 'options.signin.proxyTitle', proxyState(signin, settings), proxy, true),
    `<form class="card signin-card" data-card="who">
        <div class="card-header"><h3>${escapeHtml(t('options.signin.whoTitle'))}</h3></div>
        ${who}
        <div class="button-group"><button type="submit" class="primary"><i class="fas fa-save"></i> ${escapeHtml(t('common.save'))}</button></div>
    </form>`,
    signInCard('root', 'options.signin.rootTitle', enabled.root === false ? 'off' : 'active', sentence('options.signin.rootHelp'), false),
    signInCard('key', 'options.signin.keyTitle', enabled.key ? 'active' : 'off', sentence(enabled.key ? 'options.signin.keyOn' : 'options.signin.keyOff'), false),
  ].join('');

  typed.forEach((value, id) => {
    const control = document.getElementById(id);
    if (!control) return;
    if (control.type === 'checkbox') control.checked = value;
    else control.value = value;
  });
  applyOidcPreset();
  testSessionNames();
  markClampedDescriptions(container);
}

/** How browsers reach the server, as the server and this browser each see it: what a reverse proxy has to get right. */
function reachCard(signin) {
  const arrival = signin.arrival || {};
  const row = (label, value, note = '', bad = false) => `
        <div class="copy-row">
            <span>${escapeHtml(t(label))}</span>
            <code>${escapeHtml(value || t('common.none'))}</code>
        </div>${note ? `<p class="description${bad ? ' reach-bad' : ''}">${escapeHtml(note)}</p>` : ''}`;
  const publicUrl = clusterData.public_url || '';
  const elsewhere = info.shell === 'web' && publicUrl && publicUrl.replace(/\/$/, '') !== location.origin;
  // Without isolation a session is a path of the web app, and no name has to answer for it.
  const shared = !clusterData.session_isolation;
  const sessionRows = shared
    ? row('options.signin.reach.sessionNames', `${(publicUrl || location.origin).replace(/\/$/, '')}/<session id>/`, t('options.signin.reach.sessionNamesShared'))
    : `${row('options.signin.reach.sessionDomain', clusterData.session_domain || t('options.cluster.sessionDomainUnset'))}
            <div class="copy-row">
                <span>${escapeHtml(t('options.signin.reach.sessionNames'))}</span>
                <code id="reach-session-names">${escapeHtml(t('options.signin.testing'))}</code>
            </div>
            <p class="description" id="reach-session-note" hidden></p>`;
  const via = !arrival.remote ? ''
    : arrival.trusted ? t('options.signin.reach.viaTrusted')
      : t(signin.trusted_proxies ? 'options.signin.reach.viaOther' : 'options.signin.reach.viaDirect');
  return `
    <div class="card signin-card" data-card="reach">
        <div class="card-header"><h3>${escapeHtml(t('options.signin.reach.title'))}</h3></div>
        ${`<p class="description">${escapeHtml(t('options.signin.reach.help'))}</p>`}
        <div class="copy-rows">
            ${row('options.signin.reach.publicUrl', publicUrl, elsewhere ? t('options.signin.reach.publicUrlDiffers', { origin: location.origin }) : '', elsewhere)}
            ${sessionRows}
            ${row('options.signin.reach.trustedProxies', signin.trusted_proxies)}
            ${row('options.signin.reach.arrivedFrom', arrival.remote, via, Boolean(arrival.remote) && !arrival.trusted && Boolean(signin.trusted_proxies))}
            ${row('options.signin.reach.yourAddress', arrival.client)}
            ${row('options.signin.reach.httpPort', signin.http_port ? String(signin.http_port) : t('options.groups.off'))}
        </div>
    </div>`;
}

/** With session isolation, ask a made-up session name for its answer, as a launch does, and say in the card whether one came. */
async function testSessionNames() {
  const target = document.getElementById('reach-session-names');
  if (!target || info.shell !== 'web') {
    if (target) target.closest('.copy-row').hidden = true;
    return;
  }
  const named = clusterData.session_domain || '';
  const found = await reachableSessionOrigin(location.hostname, location.port || '443', named);
  const note = document.getElementById('reach-session-note');
  if (!document.body.contains(target)) return;
  if (found) {
    target.textContent = `<session id>.${found}${location.port ? `:${location.port}` : ''}`;
    if (named && named !== found) {
      note.textContent = t('options.signin.reach.sessionNamesOther', { domain: named });
      note.className = 'description reach-bad';
      note.hidden = false;
    }
    return;
  }
  // A proxy that signs users in on the session names turns this test away and lets a real session through.
  target.textContent = t('options.signin.reach.sessionNamesNone');
  note.textContent = t(named ? 'options.signin.reach.sessionNamesGuarded' : 'options.signin.reach.sessionNamesHelp', { domain: named });
  note.className = named ? 'description' : 'description reach-bad';
  note.hidden = false;
}

function applyProxyPreset() {
  const preset = PROXY_PRESETS[document.getElementById('signin-proxy-preset').value];
  if (!preset) return;
  document.getElementById('cluster-setting-proxy_auth_user_header').value = preset.user;
  document.getElementById('cluster-setting-proxy_auth_groups_header').value = preset.groups;
  document.getElementById('cluster-setting-proxy_auth_logout_url').placeholder = preset.logout;
}

function applyOidcPreset() {
  const preset = document.getElementById('signin-oidc-preset').value;
  const issuer = document.getElementById('cluster-setting-oidc_issuer');
  issuer.placeholder = OIDC_PRESETS[preset] || '';
  document.getElementById('signin-oidc-hint').textContent = t(preset ? `options.signin.presets.${preset}.hint` : 'options.signin.providerHelp');
}

/** Write a card's changed settings; the saved card then shows what the cluster holds. */
async function saveSignInCard(kind) {
  const body = changedSettings(SIGNIN_CARDS[kind]);
  if (!Object.keys(body).length) return true;
  savedSignInCard = kind;
  const saved = await clusterWrite('/api/admin/cluster/settings', { method: 'PUT', body: JSON.stringify(body) }, t('options.status.settingsSaved'));
  savedSignInCard = null;
  return Boolean(saved);
}

/** Test the saved settings of a provider, saving the card first when it was changed. */
async function testSignIn(kind) {
  if (!(await saveSignInCard(kind))) return;
  const result = () => document.getElementById(`signin-result-${kind}`);
  result().innerHTML = `<p class="description"><span class="spinner-small" style="display: inline-block;"></span> ${escapeHtml(t('options.signin.testing'))}</p>`;
  let answer;
  try {
    // The server counts, in its test, the forged headers that reach it from this browser now.
    if (kind === 'proxy' && info.shell === 'web') await sendForgedSignIn();
    answer = await secureFetch('/api/admin/cluster/signin/test', { method: 'POST', body: JSON.stringify({ kind }) });
  } catch (error) {
    answer = { ok: false, error: error.message };
  }
  if (kind === 'proxy') {
    // The card's own line says what the check found.
    result().innerHTML = '';
    if (clusterData.signin) clusterData.signin.proxy_check = { ...clusterData.signin.proxy_check, ...answer };
    if (answer.state) renderSignIn();
    else result().innerHTML = `<p class="status-message error">${escapeHtml(answer.error || t('options.signin.testFailed'))}</p>`;
    return;
  }
  if (!answer.ok) {
    result().innerHTML = `<p class="status-message error">${escapeHtml(answer.error || t('options.signin.testFailed'))}</p>`;
    return;
  }
  const yesNo = (value) => t(value ? 'common.yes' : 'common.no');
  const groupsClaim = clusterData.settings.sso_groups_claim;
  const lines = kind === 'oidc' ? [
    ['options.signin.resultIssuer', answer.issuer],
    ['options.signin.resultAuthorization', answer.authorization_endpoint],
    ['options.signin.resultBackchannel', yesNo(answer.backchannel_logout)],
    ['options.signin.resultScopes', (answer.scopes || []).join(' ')],
    ['options.signin.resultGroupsClaim', t((answer.claims || []).includes(groupsClaim) ? 'options.signin.claimListed' : 'options.signin.claimNotListed', { claim: groupsClaim })],
  ] : [
    ['options.signin.resultEntity', answer.entity_id],
    ['options.signin.resultSso', answer.sso_url],
    ['options.signin.resultSlo', yesNo(answer.single_logout)],
    ['options.signin.resultCertificates', Array.isArray(answer.certificates) ? answer.certificates.length : answer.certificates],
  ];
  result().innerHTML = `<div class="signin-ok"><p><i class="fas fa-check-circle"></i> ${escapeHtml(t('options.signin.testOk'))}</p>${lines
    .map(([label, value]) => `<p><span>${escapeHtml(t(label))}</span> <strong>${escapeHtml(value ?? '')}</strong></p>`).join('')}</div>`;
}

function bindSignInEvents() {
  const container = document.getElementById('signin-cards');
  container.addEventListener('submit', (e) => {
    e.preventDefault();
    saveSignInCard(e.target.dataset.card);
  });
  container.addEventListener('click', (e) => {
    const copy = e.target.closest('button[data-copy]');
    if (copy) {
      navigator.clipboard.writeText(copy.dataset.copy)
        .then(() => displayStatus(t('options.status.copySuccess')), () => displayStatus(t('options.status.copyFailed'), true));
    }
    const test = e.target.closest('button[data-test]');
    if (test) testSignIn(test.dataset.test);
  });
  container.addEventListener('change', (e) => {
    if (e.target.id === 'signin-oidc-preset') applyOidcPreset();
    if (e.target.id === 'signin-proxy-preset') applyProxyPreset();
  });
}

// --- AUDIT LOG ---

// What every audit event carries, shown in columns of their own.
const AUDIT_COLUMNS = ['time', 'node', 'event', 'user'];
const AUDIT_EXPORT_PAGE = 5000;
const AUDIT_SEARCH_DELAY_MS = 300;
let auditOffset = 0;

const utcDay = (daysAgo = 0) => new Date(Date.now() - daysAgo * 86400000).toISOString().slice(0, 10);
const auditDetails = (entry, separator) => Object.entries(entry)
  .filter(([key, value]) => !AUDIT_COLUMNS.includes(key) && value !== null && value !== '')
  .map(([key, value]) => `${key}=${typeof value === 'object' ? JSON.stringify(value) : value}`)
  .join(separator);

/** The search and range of the audit controls as query parameters, and a name for the range. */
function auditQuery() {
  const range = document.getElementById('audit-range').value || 'today';
  const query = new URLSearchParams();
  let label = utcDay();
  if (range.startsWith('day:')) {
    label = range.slice(4);
    query.set('day', label);
  } else if (range.startsWith('last:')) {
    const days = Number(range.slice(5));
    query.set('since', utcDay(days - 1));
    label = `last-${days}-days`;
  }
  const words = document.getElementById('audit-search').value.trim();
  if (words) query.set('q', words);
  return { query, label };
}

function renderAuditRanges(days) {
  const select = document.getElementById('audit-range');
  const current = select.value || 'today';
  const listed = [...new Set([...(days || []), ...(current.startsWith('day:') ? [current.slice(4)] : [])])].sort().reverse();
  select.innerHTML = `
        <option value="today">${escapeHtml(t('options.audit.today'))}</option>
        <option value="last:7">${escapeHtml(t('options.audit.last7'))}</option>
        <option value="last:30">${escapeHtml(t('options.audit.last30'))}</option>
        ${listed.length ? `<optgroup label="${escapeHtml(t('options.audit.oneDay'))}">${listed.map((day) => `<option value="day:${escapeHtml(day)}">${escapeHtml(day)}</option>`).join('')}</optgroup>` : ''}`;
  select.value = current;
}

async function refreshAudit() {
  const tbody = document.querySelector('#audit-table tbody');
  const pager = document.getElementById('audit-pagination');
  const limit = Number(document.getElementById('audit-page-size').value);
  const message = (text) => `<tr class="empty-row"><td colspan="5" style="text-align:center; padding: 2rem;">${escapeHtml(text)}</td></tr>`;
  const { query } = auditQuery();
  query.set('offset', auditOffset);
  query.set('limit', limit);
  try {
    const { total, events, days } = await secureFetch(`/api/admin/cluster/audit?${query}`, { method: 'GET' });
    renderAuditRanges(days);
    if (clustered && !clusterData) clusterData = await secureFetch('/api/admin/cluster', { method: 'GET' }).catch(() => null);
    const nodeNames = new Map(((clusterData && clusterData.nodes) || []).map((node) => [node.id, nodeLabel(node)]));
    tbody.innerHTML = events.length ? events.map((entry) => {
      const at = new Date(entry.time);
      return `
            <tr>
                <td title="${escapeHtml(entry.time)}">${escapeHtml(Number.isNaN(at.getTime()) ? entry.time : at.toLocaleString())}</td>
                <td>${escapeHtml(nodeNames.get(entry.node) || entry.node || '')}</td>
                <td>${escapeHtml(entry.event)}</td>
                <td>${escapeHtml(entry.user || '')}</td>
                <td class="audit-details">${escapeHtml(auditDetails(entry, ' '))}</td>
            </tr>`;
    }).join('') : message(t('options.audit.none'));
    pager.innerHTML = `
        <button class="secondary" data-page="prev" ${auditOffset === 0 ? 'disabled' : ''}>&laquo; ${t('common.previous')}</button>
        <span class="page-info">${escapeHtml(t('options.audit.shown', { from: total ? auditOffset + 1 : 0, to: auditOffset + events.length, total }))}</span>
        <button class="secondary" data-page="next" ${auditOffset + events.length >= total ? 'disabled' : ''}>${t('common.next')} &raquo;</button>`;
  } catch (error) {
    tbody.innerHTML = message(error.message);
    pager.innerHTML = '';
  }
}

// A cell a spreadsheet would run as a formula is kept as text.
const csvCell = (value) => {
  const text = String(value ?? '');
  return `"${(/^[=+\-@\t\r]/.test(text) ? `'${text}` : text).replace(/"/g, '""')}"`;
};

/** Download every event of the current search and range, a page at a time, as CSV or JSON. */
async function exportAudit(format, button) {
  const { query, label } = auditQuery();
  button.disabled = true;
  try {
    const all = [];
    for (let total = Infinity; all.length < total;) {
      query.set('offset', all.length);
      query.set('limit', AUDIT_EXPORT_PAGE);
      const page = await secureFetch(`/api/admin/cluster/audit?${query}`, { method: 'GET' });
      total = page.total;
      if (!page.events.length) break;
      all.push(...page.events);
    }
    const text = format === 'json'
      ? JSON.stringify(all, null, 2)
      : [[...AUDIT_COLUMNS, 'details'].map(csvCell).join(','),
        ...all.map((entry) => [...AUDIT_COLUMNS.map((column) => entry[column]), auditDetails(entry, '; ')].map(csvCell).join(','))].join('\r\n');
    const blob = new Blob([text], { type: format === 'json' ? 'application/json' : 'text/csv' });
    const filename = `sealskin-audit-${label}.${format}`;
    // The mobile app saves through its native file plugin; a browser downloads.
    if (info.capabilities && info.capabilities.nativeFileOpen) await bridge.saveBlob(blob, filename);
    else downloadBlob(blob, filename);
    displayStatus(t('options.audit.exported', { count: all.length }));
  } catch (error) {
    displayStatus(escapeHtml(error.message), true);
  }
  button.disabled = false;
}

function bindAuditEvents() {
  const reload = () => {
    auditOffset = 0;
    refreshAudit();
  };
  let timer = null;
  document.getElementById('audit-search').addEventListener('input', () => {
    clearTimeout(timer);
    timer = setTimeout(reload, AUDIT_SEARCH_DELAY_MS);
  });
  document.getElementById('audit-range').addEventListener('change', reload);
  document.getElementById('audit-page-size').addEventListener('change', reload);
  document.getElementById('audit-refresh').addEventListener('click', refreshAudit);
  document.getElementById('audit-pagination').addEventListener('click', (e) => {
    const button = e.target.closest('button[data-page]');
    if (!button) return;
    const size = Number(document.getElementById('audit-page-size').value);
    auditOffset = Math.max(0, auditOffset + (button.dataset.page === 'next' ? size : -size));
    refreshAudit();
  });
  document.querySelectorAll('.audit-export').forEach((button) => button.addEventListener('click', () => exportAudit(button.dataset.format, button)));
}

// --- TABLES ---

const shortKey = (pem) => escapeHtml(pem.replace(/-----(BEGIN|END) PUBLIC KEY-----/g, '').replace(/\s/g, ''));

const tableRenderConfig = {
  admins: {
    tbody: document.querySelector('#admins-table tbody'),
    filter: (item, term) => item.username.toLowerCase().includes(term),
    row: (item) => {
      const username = escapeHtml(item.username);
      const pubkey = escapeHtml(item.public_key);
      return `
            <tr>
                <td>${username}</td>
                <td class="pubkey-cell" title="${pubkey}">
                    <div class="cell-wrapper">
                        <span class="key-text">${shortKey(item.public_key)}</span>
                        <button class="secondary copy-btn" data-pubkey="${pubkey}"><i class="fas fa-copy"></i></button>
                    </div>
                </td>
                <td class="actions-cell">
                    <div class="cell-wrapper">
                        <button class="secondary" data-adminname="${username}">${t('common.homes')}</button>
                        ${item.username !== 'admin' ? `<button class="danger" data-adminname="${username}">${t('common.delete')}</button>` : ''}
                    </div>
                </td>
            </tr>`;
    },
  },
  users: {
    tbody: document.querySelector('#users-table tbody'),
    filter: (item, term) => item.username.toLowerCase().includes(term) || groupsOf(item.settings).some((group) => group.toLowerCase().includes(term)),
    row: (item) => {
      const effectiveSettings = calculateEffectiveSettings(item);
      const homesDisabled = !effectiveSettings.persistent_storage;
      const username = escapeHtml(item.username);
      const pubkey = escapeHtml(item.public_key);
      // The server says who still waits: a held sign-in no group, admin group, or approval has let in.
      const waiting = item.held === true;
      const marks = (item.admin ? ` <span class="pill active">${escapeHtml(t('options.users.admin'))}</span>` : '')
        + (waiting ? ` <span class="pill off">${escapeHtml(t('options.users.awaitingGroup'))}</span>` : '');
      return `
                <tr>
                    <td>${username}</td>
                    <td>${escapeHtml(groupsOf(item.settings).join(', ')) || t('common.none')}${marks}</td>
                    <td class="pubkey-cell" title="${pubkey}">
                        <div class="cell-wrapper">
                            <span class="key-text">${item.public_key ? shortKey(item.public_key) : t('options.users.signInOnly')}</span>
                            ${item.public_key ? `<button class="secondary copy-btn" data-pubkey="${pubkey}"><i class="fas fa-copy"></i></button>` : ''}
                        </div>
                    </td>
                    <td class="actions-cell">
                        <div class="cell-wrapper">
                            ${waiting ? `<button class="primary" data-action="approve" data-username="${username}">${t('options.users.approve')}</button>` : ''}
                            <button class="secondary" data-username="${username}" ${homesDisabled ? `disabled title="${t('options.users.homesDisabledTooltip')}"` : ''}>${t('common.homes')}</button>
                            <button class="warning" data-username="${username}">${t('common.edit')}</button>
                            <button class="danger" data-username="${username}">${t('common.delete')}</button>
                        </div>
                    </td>
                </tr>`;
    },
  },
  groups: {
    tbody: document.querySelector('#groups-table tbody'),
    filter: (item, term) => item.name.toLowerCase().includes(term),
    row: (item) => `
                <tr>
                    <td>${escapeHtml(item.name)}</td>
                    <td class="actions-cell">
                        <button class="warning" data-groupname="${escapeHtml(item.name)}">${t('common.edit')}</button>
                        <button class="danger" data-groupname="${escapeHtml(item.name)}">${t('common.delete')}</button>
                    </td>
                </tr>`,
  },
  prootCatalogs: {
    tbody: document.querySelector('#prootCatalogs-table tbody'),
    filter: (item, term) => item.name.toLowerCase().includes(term),
    row: (item) => {
      const id = escapeHtml(item.id);
      const auto = item.auto_update ? ` <small>(${t('options.prootApps.autoUpdateOn')})</small>` : '';
      return `
                <tr>
                    <td>${escapeHtml(item.name)}</td>
                    <td>${item.apps.length}${auto}</td>
                    <td>${prootStateHtml(item)}</td>
                    <td class="actions-cell">
                        <div class="cell-wrapper">
                            <button class="secondary" data-catalogid="${id}" data-action="update" title="${escapeHtml(t('options.prootApps.updateTitle'))}" ${item.state === 'syncing' ? 'disabled' : ''}>${t('options.prootApps.update')}</button>
                            <button class="warning" data-catalogid="${id}" data-action="edit">${t('common.edit')}</button>
                            <button class="danger" data-catalogid="${id}" data-action="delete">${t('common.delete')}</button>
                        </div>
                    </td>
                </tr>`;
    },
  },
  installedApps: {
    tbody: installedAppsTbody,
    filter: (item, term) => item.name.toLowerCase().includes(term) || item.provider_config.image.toLowerCase().includes(term),
    row: (item) => {
      const sha = item.image_sha ? item.image_sha.substring(0, 12) : t('common.none');
      let versionInfoHtml = '';

      if (item.pull_status === 'pulling') {
        versionInfoHtml = `
                    <div class="spinner-small"></div>
                    <small>${t('options.installedApps.pulling')}</small>
                `;
      } else if (item.auto_update) {
        const checkedAt = item.last_checked_at ? `${t('common.checked')}: ${timeAgo(item.last_checked_at, t)}` : `${t('common.checked')}: ${t('common.never')}`;
        versionInfoHtml = `
                    <span>${sha}</span>
                    <small>${checkedAt}</small>
                `;
      } else {
        versionInfoHtml = `
                    <div class="sha-and-button">
                        <span>${sha}</span>
                        ${!item.is_meta_app
                          ? `<button class="secondary check-update-btn" data-appid="${item.id}" ${!item.image_sha ? `disabled title="${t('options.installedApps.notLocal')}"` : ''}>Check</button>`
                          : ''}
                    </div>
                    <small>&nbsp;</small>
                `;
      }

      const labIcon = item.is_meta_app ? `<i class="fas fa-flask" style="color: var(--color-warning-text);" title="${t('options.installedApps.isLaboratory')}"></i>` : '';

      return `
            <tr>
                <td><div style="display: flex; align-items: center; gap: 0.5rem;"><span>${escapeHtml(item.name)}</span>${labIcon}</div></td>
                <td>${escapeHtml(item.source)}</td>
                <td class="image-version-cell" title="${escapeHtml(item.provider_config.image)}">
                    ${versionInfoHtml}
                </td>
                <td class="actions-cell">
                    <button class="warning" data-appid="${item.id}">${t('common.edit')}</button>
                    <button class="danger" data-appid="${item.id}">${t('common.delete')}</button>
                </td>
            </tr>`;
    },
  },
};

function renderTable(dataType) {
  const state = tableStates[dataType];
  const cfg = tableRenderConfig[dataType];
  const paginationEl = document.getElementById(`${dataType}-pagination`);

  const searchTerm = state.searchTerm.toLowerCase();
  const sourceData = adminData[dataType] || [];
  const filteredData = searchTerm ? sourceData.filter((item) => cfg.filter(item, searchTerm)) : sourceData;

  const itemsPerPage = dataType === 'installedApps' ? 10 : 5;
  const totalPages = Math.max(1, Math.ceil(filteredData.length / itemsPerPage));
  state.currentPage = Math.min(state.currentPage, totalPages);

  const startIndex = (state.currentPage - 1) * itemsPerPage;
  const paginatedData = filteredData.slice(startIndex, startIndex + itemsPerPage);

  if (paginatedData.length > 0) {
    cfg.tbody.innerHTML = paginatedData.map(cfg.row).join('');
  } else {
    const colspan = cfg.tbody.closest('table').querySelectorAll('thead th').length;
    const placeholderKey = `options.placeholders.no${dataType.charAt(0).toUpperCase() + dataType.slice(1)}`;
    cfg.tbody.innerHTML = `<tr class="empty-row"><td colspan="${colspan}" style="text-align:center; padding: 2rem;">${t(placeholderKey)}</td></tr>`;
  }

  paginationEl.innerHTML = `
        <button class="secondary" data-page="prev" ${state.currentPage === 1 ? 'disabled' : ''}>&laquo; ${t('common.previous')}</button>
        <span class="page-info">${t('common.page')} ${state.currentPage} ${t('common.of')} ${totalPages}</span>
        <button class="secondary" data-page="next" ${state.currentPage === totalPages ? 'disabled' : ''}>${t('common.next')} &raquo;</button>
    `;
}

// --- USER AND GROUP SETTINGS ---

// Each switch with its label and the value that wins where a user's groups disagree.
const SETTING_SWITCHES = [
  ['active', 'options.users.activeAccount', false],
  ['admin', 'options.users.admin', false],
  ['persistent_storage', 'options.users.allowStorage', false],
  ['public_sharing', 'options.users.allowPublicSharing', false],
  ['gpu', 'options.users.allowGpu', false],
  ['gpu_share', 'options.users.gpuShare', false],
  ['home_migration', 'options.users.homeMigration', false],
  ['edit_templates', 'options.users.allowEditTemplates', false],
  ['harden_container', 'options.users.hardenContainer', true],
  ['harden_openbox', 'options.users.hardenWm', true],
];
// Each limit with its label and whether the server takes a whole number.
const SETTING_LIMITS = [
  ['session_limit', 'options.users.sessionLimitLabel', true],
  ['storage_limit', 'options.users.storageLimit', true],
  ['session_cpus', 'options.users.sessionCpus', false],
  ['session_memory_mb', 'options.users.sessionMemory', true],
  ['session_hours', 'options.users.sessionHours', false],
  ['allowance_hours', 'options.users.allowanceHours', false],
];
const SETTING_LISTS = [
  ['pools', 'options.users.pools'],
  ['pools_denied', 'options.users.poolsDenied'],
  ['sso_groups', 'options.groups.ssoGroups'],
];
const PERIODS = ['day', 'week', 'month'];
// What the server gives a user whose record leaves a setting out.
const USER_DEFAULTS = {
  active: true, admin: false, persistent_storage: true, public_sharing: false, gpu: true, gpu_share: true,
  home_migration: false, edit_templates: false, harden_container: false, harden_openbox: false,
  session_limit: -1, storage_limit: -1, session_cpus: -1, session_memory_mb: -1, session_hours: -1,
  allowance_hours: -1, allowance_period: 'month', groups: [], pools: [], pools_denied: [], proot_catalog: null,
};
// The new-user form starts without a GPU, as it always has.
const NEW_USER_SETTINGS = { ...USER_DEFAULTS, gpu: false };

const splitList = (value) => value.split(',').map((s) => s.trim()).filter(Boolean);
const field = (prefix, name) => document.getElementById(`${prefix}-${name}`);

/** The groups a user's settings name; records written before `groups` name one in `group`. */
function groupsOf(settings) {
  if (settings.groups && settings.groups.length) return settings.groups;
  return settings.group && settings.group !== 'none' ? [settings.group] : [];
}

/**
 * Build a settings form into `#<prefix>Settings`. A user's form holds a value
 * for every setting; a group's leaves each one unset until it is chosen.
 *
 * @param {string} prefix `newUser`, `editUser`, `newGroup`, or `editGroup`.
 * @param {'user'|'group'} kind
 */
function buildSettingsForm(prefix, kind) {
  const isGroup = kind === 'group';
  const notSet = escapeHtml(t('options.groups.notSet'));
  const id = (name) => `${prefix}-${name}`;
  const row = (name, label, control, extra = {}) => fieldHtml({ id: id(name), label: t(label), control, ...extra });

  const limits = SETTING_LIMITS.map(([name, label, whole]) => row(name, label,
    `<input type="number" id="${id(name)}" step="${whole ? '1' : 'any'}" ${isGroup ? `placeholder="${notSet}"` : `value="${NEW_USER_SETTINGS[name]}"`}>`)).join('');
  const period = row('allowance_period', 'options.users.allowancePeriod', `<select id="${id('allowance_period')}">
                ${isGroup ? `<option value="" selected>${notSet}</option>` : ''}
                ${PERIODS.map((p) => `<option value="${p}"${!isGroup && p === NEW_USER_SETTINGS.allowance_period ? ' selected' : ''}>${escapeHtml(t(`options.periods.${p}`))}</option>`).join('')}
            </select>`);
  const lists = SETTING_LISTS.filter(([name]) => isGroup || name !== 'sso_groups').map(([name, label]) => row(name, label, `<input type="text" id="${id(name)}">`)).join('');
  const catalog = row('proot_catalog', 'options.users.prootCatalog',
    `<select id="${id('proot_catalog')}"><option value="">${isGroup ? notSet : escapeHtml(t('options.users.prootCatalogNone'))}</option></select>`,
    { description: t('options.users.prootCatalogHelp') });
  const switches = SETTING_SWITCHES.map(([name, label, restricting]) => (isGroup
    ? row(name, label, `<select id="${id(name)}">
                <option value="" selected>${notSet}</option>
                <option value="true">${escapeHtml(t(restricting ? 'options.groups.on' : 'options.groups.allow'))}</option>
                <option value="false">${escapeHtml(t(restricting ? 'options.groups.off' : 'options.groups.deny'))}</option>
            </select>`)
    : row(name, label, `<input type="checkbox" id="${id(name)}"${NEW_USER_SETTINGS[name] ? ' checked' : ''}>`, { kind: 'switch' }))).join('');
  const groups = isGroup ? '' : `<div class="field-list single">${fieldHtml({
    id: id('groups'),
    label: t('common.groups'),
    description: t('options.users.groupsHelp'),
    control: `<select id="${id('groups')}" multiple size="4"></select><p class="description" id="${id('provider_groups')}" style="display: none;"></p>`,
  })}</div>`;

  const host = document.getElementById(`${prefix}Settings`);
  host.innerHTML = `
    <h5 class="field-group">${escapeHtml(t(isGroup ? 'options.groups.overrideTitle' : 'options.users.settingsTitle'))}</h5>
    <p class="description">${escapeHtml(t(isGroup ? 'options.groups.overrideHelp' : 'options.users.limitsHelp'))}</p>
    ${groups}
    <div class="field-list">${limits}${period}${lists}${catalog}</div>
    <h5 class="field-group">${escapeHtml(t(isGroup ? 'options.groups.permissionsTitle' : 'options.users.permissionsTitle'))}</h5>
    <div class="field-list">${switches}</div>`;
  markClampedDescriptions(host);
}

/** @returns {object} The user settings a form holds; a blank limit sets none. */
function readUserSettings(prefix) {
  const settings = {};
  SETTING_SWITCHES.forEach(([name]) => { settings[name] = field(prefix, name).checked; });
  SETTING_LIMITS.forEach(([name, , whole]) => {
    const value = parseFloat(field(prefix, name).value);
    settings[name] = Number.isFinite(value) ? (whole ? Math.trunc(value) : value) : -1;
  });
  settings.allowance_period = field(prefix, 'allowance_period').value;
  settings.groups = [...field(prefix, 'groups').selectedOptions].map((option) => option.value);
  settings.group = settings.groups[0] || 'none';
  settings.pools = splitList(field(prefix, 'pools').value);
  settings.pools_denied = splitList(field(prefix, 'pools_denied').value);
  settings.proot_catalog = field(prefix, 'proot_catalog').value || null;
  return settings;
}

function fillUserSettings(prefix, stored) {
  const settings = { ...USER_DEFAULTS, ...stored };
  SETTING_SWITCHES.forEach(([name]) => { field(prefix, name).checked = Boolean(settings[name]); });
  SETTING_LIMITS.forEach(([name]) => { field(prefix, name).value = settings[name]; });
  field(prefix, 'allowance_period').value = settings.allowance_period;
  const groups = groupsOf(settings);
  [...field(prefix, 'groups').options].forEach((option) => { option.selected = groups.includes(option.value); });
  field(prefix, 'pools').value = (settings.pools || []).join(', ');
  field(prefix, 'pools_denied').value = (settings.pools_denied || []).join(', ');
  field(prefix, 'proot_catalog').value = settings.proot_catalog || '';
  const provided = field(prefix, 'provider_groups');
  const named = settings.provider_groups || [];
  provided.textContent = t('options.users.providerGroups', { groups: named.join(', ') });
  provided.style.display = named.length ? 'block' : 'none';
}

/** @returns {object} The group settings a form holds; null for each one the group leaves alone. */
function readGroupSettings(prefix) {
  const settings = {};
  SETTING_SWITCHES.forEach(([name]) => {
    const { value } = field(prefix, name);
    settings[name] = value === '' ? null : value === 'true';
  });
  SETTING_LIMITS.forEach(([name, , whole]) => {
    const value = parseFloat(field(prefix, name).value);
    settings[name] = Number.isFinite(value) ? (whole ? Math.trunc(value) : value) : null;
  });
  settings.allowance_period = field(prefix, 'allowance_period').value || null;
  SETTING_LISTS.forEach(([name]) => { settings[name] = splitList(field(prefix, name).value); });
  settings.proot_catalog = field(prefix, 'proot_catalog').value || null;
  return settings;
}

function fillGroupSettings(prefix, settings) {
  SETTING_SWITCHES.forEach(([name]) => { field(prefix, name).value = typeof settings[name] === 'boolean' ? String(settings[name]) : ''; });
  SETTING_LIMITS.forEach(([name]) => { field(prefix, name).value = typeof settings[name] === 'number' ? settings[name] : ''; });
  field(prefix, 'allowance_period').value = settings.allowance_period || '';
  SETTING_LISTS.forEach(([name]) => { field(prefix, name).value = (settings[name] || []).join(', '); });
  field(prefix, 'proot_catalog').value = settings.proot_catalog || '';
}

/**
 * The settings a user runs under, as the server works them out: a switch
 * takes its restricting value when any of the user's groups gives it that, a
 * limit the smallest any group sets, and what no group sets stays the user's.
 */
function calculateEffectiveSettings(user) {
  if (!user || !user.settings) return {};
  const base = { ...USER_DEFAULTS, ...user.settings };
  const known = new Map(adminData.groups.map((group) => [group.name, group.settings || {}]));
  const brought = new Set(base.provider_groups || []);
  const names = groupsOf(base).filter((name) => known.has(name));
  known.forEach((settings, name) => {
    if (!names.includes(name) && (brought.has(name) || (settings.sso_groups || []).some((g) => brought.has(g)))) names.push(name);
  });
  const members = names.map((name) => known.get(name));
  const effective = { ...base, groups: names, group: names[0] || 'none' };
  SETTING_SWITCHES.forEach(([key, , restricting]) => {
    const chosen = members.filter((g) => typeof g[key] === 'boolean').map((g) => g[key]);
    if (chosen.length) effective[key] = chosen.includes(restricting) ? restricting : !restricting;
  });
  SETTING_LIMITS.forEach(([key]) => {
    const chosen = members.map((g) => g[key]).filter((value) => typeof value === 'number' && value >= 0);
    if (!chosen.length) return;
    effective[key] = Math.min(...chosen);
    if (key === 'allowance_hours') {
      effective.allowance_period = members.find((g) => g[key] === effective[key]).allowance_period || base.allowance_period;
    }
  });
  effective.pools = [...new Set([...members.flatMap((g) => g.pools || []), ...(base.pools || [])])].sort();
  effective.pools_denied = [...new Set([...members.flatMap((g) => g.pools_denied || []), ...(base.pools_denied || [])])].sort();
  // A catalog is a choice: the user's own, else the first group's that makes one.
  effective.proot_catalog = base.proot_catalog || (members.find((g) => g.proot_catalog) || {}).proot_catalog || null;
  return effective;
}

function populateCatalogDropdowns() {
  document.querySelectorAll('#newUser-proot_catalog, #editUser-proot_catalog, #newGroup-proot_catalog, #editGroup-proot_catalog').forEach((select) => {
    const chosen = select.value;
    const first = select.options[0];
    select.innerHTML = '';
    select.add(first);
    (adminData.proot_catalogs || []).forEach((catalog) => select.add(new Option(catalog.name, catalog.id)));
    select.value = chosen;
    if (select.value !== chosen) select.value = '';
  });
}

function populateGroupDropdowns() {
  document.querySelectorAll('#newUser-groups, #editUser-groups').forEach((select) => {
    const chosen = [...select.selectedOptions].map((option) => option.value);
    select.innerHTML = adminData.groups
      .map((group) => `<option value="${escapeHtml(group.name)}"${chosen.includes(group.name) ? ' selected' : ''}>${escapeHtml(group.name)}</option>`)
      .join('');
  });
}

/** Report a failed write; where another node changed the record first, show what it is now. */
async function writeFailed(error, message, reload) {
  displayStatus(message, true);
  if (error.status === 409) await reload();
}

function showUserConfigModal(user, privateKey, isNewUser = false) {
  const configJson = JSON.stringify({
    server_endpoint: config.serverIp,
    api_port: adminData.api_port || config.apiPort,
    session_port: adminData.session_port || config.sessionPort,
    username: user.username,
    private_key: privateKey,
    server_public_key: adminData.server_public_key || '',
  }, null, 2);
  generatedConfigText.value = configJson;
  downloadConfigBtn.dataset.username = user.username;

  if (configModalWarning && configModalInfo) {
    configModalWarning.style.display = isNewUser ? 'block' : 'none';
    configModalInfo.style.display = isNewUser ? 'none' : 'block';
  }
  userConfigModal.style.display = 'block';
}

function renderGpuInfo(gpus) {
  const gpuInfoContainer = document.getElementById('dashboard-gpu-info');
  const gpuList = document.getElementById('dashboard-gpu-list');

  if (gpus && gpus.length > 0) {
    gpuList.innerHTML = gpus.map((gpu) => `<li>${escapeHtml(gpu.device.split('/').pop())} (${escapeHtml(gpu.driver)})</li>`).join('');
    gpuInfoContainer.style.display = 'block';
  } else {
    gpuInfoContainer.style.display = 'none';
  }
}

async function refreshAdminData() {
  try {
    const data = await secureFetch('/api/admin/data', { method: 'POST', body: JSON.stringify({}) });
    adminData = { ...adminData, ...data };
    serverPublicKeyDisplay.value = data.server_public_key;
    renderTable('admins');
    renderTable('users');
    renderTable('groups');
    populateGroupDropdowns();
    populateCatalogDropdowns();
    renderGpuInfo(adminData.gpus);
  } catch (error) {
    displayStatus(t('options.status.adminDataRefreshFailed', { error: error.message }), true);
  }
}

function setAdminNavVisibility(visible) {
  const display = visible ? 'flex' : 'none';
  adminNavLinks.forEach((link) => { link.style.display = display; });
  adminNavSeparator.style.display = visible ? 'block' : 'none';
  // The Sign In section is the web app's; the other shells keep those settings in the Cluster section.
  if (info.shell !== 'web') document.querySelector('.nav-link[data-tabname="SignIn"]').style.display = 'none';
  if (visible && info.platform === 'ios') {
    const lab = document.querySelector('.nav-link[data-tabname="AppLaboratory"]');
    if (lab) lab.style.display = 'none';
  }
}

/**
 * Load the dashboard for the connected user. Replaces the former login flow:
 * the shell already holds the credentials and performs the handshake.
 */
/**
 * Show a warning on the dashboard when the server's TLS certificate is
 * expired or expires within two weeks. Browsers only report "Failed to
 * fetch" once it has expired, so this is the early notice.
 */
function renderCertWarning(expiresAt) {
  const el = document.getElementById('dashboard-cert-warning');
  if (!el) return;
  if (!expiresAt) {
    el.style.display = 'none';
    return;
  }
  const days = Math.floor((expiresAt * 1000 - Date.now()) / 86400000);
  if (days < 0) {
    el.textContent = tOr(t, 'options.dashboard.certExpired', 'The server TLS certificate has expired. HTTPS connections will fail until it is renewed.');
  } else if (days <= 14) {
    el.textContent = tOr(t, 'options.dashboard.certExpiring', 'The server TLS certificate expires in {days} day(s). Renew it to avoid connection failures.', { days });
  } else {
    el.style.display = 'none';
    return;
  }
  el.style.display = 'block';
}

async function loadDashboard() {
  displayStatus(t('options.status.loggingIn'));
  dashboardServerIp.textContent = config.serverIp || '';
  dashboardApiPort.textContent = config.sessionPort || '';
  searchEngineDashboardSelect.value = config.searchEngineUrl || 'https://google.com/search?q=';
  try {
    const statusData = await secureFetch('/api/admin/status', { method: 'POST', body: JSON.stringify({}) });
    isLoggedIn = true;
    isAdmin = !!statusData.is_admin;

    const userSettings = { ...statusData.settings, is_admin: statusData.is_admin };
    bridge.updateConfig({ userSettings }).catch((e) => console.warn('Could not persist user settings:', e));
    config.userSettings = userSettings;

    dashboardUsername.textContent = statusData.username;
    dashboardRole.textContent = isAdmin ? t('options.dashboard.roleAdmin') : t('options.dashboard.roleUser');
    dashboardCpuModel.textContent = statusData.cpu_model || t('common.na');
    clustered = Boolean(statusData.clustered);
    document.getElementById('dashboard-via').textContent = tOr(t, `options.via.${statusData.via}`, statusData.via || '');
    document.getElementById('dashboard-via-row').style.display = statusData.via ? 'block' : 'none';
    const { allowance } = statusData;
    if (allowance) {
      document.getElementById('dashboard-allowance').textContent = t('options.dashboard.allowanceUsed', {
        used: Number(allowance.used).toFixed(1), hours: allowance.hours, period: t(`options.periods.${allowance.period}`).toLowerCase(),
      });
    }
    document.getElementById('dashboard-allowance-row').style.display = allowance ? 'block' : 'none';
    renderCertWarning(statusData.proxy_cert_expires_at);
    if (statusData.disk_total && statusData.disk_used) {
      dashboardDiskUsageText.textContent = `${formatBytes(statusData.disk_used, t)} / ${formatBytes(statusData.disk_total, t)}`;
      dashboardDiskUsageBar.value = (statusData.disk_used / statusData.disk_total) * 100;
      dashboardDiskInfo.style.display = 'block';
    } else {
      dashboardDiskInfo.style.display = 'none';
    }

    sessionsTabButton.style.display = 'flex';

    if (statusData.settings.gpu) {
      renderGpuInfo(statusData.gpus);
    } else {
      document.getElementById('dashboard-gpu-info').style.display = 'none';
    }

    if (isAdmin) {
      displayStatus(t('options.status.loggedInAdmin', { username: statusData.username }), false);
      setAdminNavVisibility(true);
      homeDirTabButton.style.display = 'flex';
      await refreshAdminData();
      await refreshAppData();
    } else {
      displayStatus(t('options.status.loggedInUser', { username: statusData.username }), false);
      setAdminNavVisibility(false);
      homeDirTabButton.style.display = statusData.settings.persistent_storage ? 'flex' : 'none';
      if (statusData.settings.edit_templates) {
        await refreshTemplates();
        document.querySelector('.nav-link[data-tabname="AppTemplates"]').style.display = 'flex';
        adminNavSeparator.style.display = 'block';
      }
    }
    return true;
  } catch (error) {
    console.error('Login failed:', error);
    displayStatus(t('options.status.loginFailed', { error: error.message }), true);
    setAdminNavVisibility(false);
    homeDirTabButton.style.display = 'none';
    sessionsTabButton.style.display = 'none';
    return false;
  }
}

async function refreshHomeDirs() {
  try {
    const [data, mine] = await Promise.all([
      secureFetch('/api/homedirs', { method: 'GET' }),
      clustered ? secureFetch('/api/cluster', { method: 'GET' }).catch(() => null) : null,
    ]);
    myCluster = mine && mine.clustered ? mine : null;
    renderHomeDirsTable(data.home_dirs);
  } catch (error) {
    displayStatus(t('options.status.homedirLoadFailed', { error: error.message }), true);
    homeDirsTbody.innerHTML = `<tr><td colspan="3" class="empty-row" style="text-align:center;">${t('options.placeholders.errorLoading')}</td></tr>`;
  }
}

function renderHomeDirsTable(dirs) {
  const filteredDirs = dirs ? dirs.filter((dir) => dir !== '_sealskin_shared_files') : [];
  document.getElementById('homedirs-node-header').style.display = myCluster ? '' : 'none';
  // In a cluster, the node holding each home, and the way to another for who may move one.
  const nodeCell = (dir) => {
    if (!myCluster) return '';
    const holder = myCluster.nodes.find((node) => node.id === myCluster.homes[dir]);
    return `<td>${escapeHtml(holder ? nodeLabel(holder) : myCluster.homes[dir] || t('common.na'))}</td>`;
  };
  const move = (dir) => (myCluster && myCluster.can_move_homes
    ? `<button class="secondary move-btn" data-homedir-name="${escapeHtml(dir)}">${t('options.home.move')}</button>`
    : '');
  if (filteredDirs.length > 0) {
    homeDirsTbody.innerHTML = filteredDirs.map((dir) => `
            <tr>
                <td>${escapeHtml(dir)}</td>
                ${nodeCell(dir)}
                <td class="actions-cell">
                    <div class="cell-wrapper">
                        <button class="secondary manage-btn" data-homedir-name="${escapeHtml(dir)}">${t('common.manage')}</button>
                        ${move(dir)}
                        <button class="danger" data-homedir-name="${escapeHtml(dir)}">${t('common.delete')}</button>
                    </div>
                </td>
            </tr>
        `).join('');
  } else {
    homeDirsTbody.innerHTML = `<tr class="empty-row"><td colspan="3" style="text-align:center; padding: 2rem;">${t('options.placeholders.noHomeDirs')}</td></tr>`;
  }
}

async function refreshAdminUserHomeDirs(username, isAdminUser = false) {
  currentAdminManagedUser = { username, isAdmin: isAdminUser };
  document.getElementById('homedir-list-title').textContent = isAdminUser
    ? t('options.modals.dirsForAdmin', { username })
    : t('options.modals.dirsForUser', { username });
  try {
    const path = isAdminUser ? 'admins' : 'users';
    const [data, held] = await Promise.all([
      secureFetch(`/api/admin/${path}/${username}/homedirs`, { method: 'GET' }),
      clustered ? secureFetch(`/api/admin/cluster/users/${encodeURIComponent(username)}/homes`, { method: 'GET' }).catch(() => null) : null,
    ]);
    managedHomes = (held && held.homes) || {};
    if (clustered && !clusterData) clusterData = await secureFetch('/api/admin/cluster', { method: 'GET' }).catch(() => null);
    renderAdminUserHomeDirsTable(data.home_dirs);
  } catch (error) {
    displayStatus(t('options.status.homedirLoadFailed', { error: error.message }), true);
    userHomeDirsTbody.innerHTML = `<tr><td colspan="3" class="empty-row" style="text-align:center;">${t('options.placeholders.errorLoading')}</td></tr>`;
  }
}

function renderAdminUserHomeDirsTable(dirs) {
  document.getElementById('user-homedirs-node-header').style.display = clustered ? '' : 'none';
  const nodeCell = (dir) => {
    if (!clustered) return '';
    const holder = clusterData && clusterData.nodes.find((node) => node.id === managedHomes[dir]);
    return `<td>${escapeHtml(holder ? nodeLabel(holder) : managedHomes[dir] || t('common.na'))}</td>`;
  };
  if (dirs && dirs.length > 0) {
    userHomeDirsTbody.innerHTML = dirs.map((dir) => `
            <tr>
                <td>${escapeHtml(dir)}</td>
                ${nodeCell(dir)}
                <td class="actions-cell">
                    <div class="cell-wrapper">
                        ${clustered ? `<button class="secondary move-btn" data-homedir-name="${escapeHtml(dir)}">${t('options.home.move')}</button>` : ''}
                        <button class="danger" data-homedir-name="${escapeHtml(dir)}">${t('common.delete')}</button>
                    </div>
                </td>
            </tr>
        `).join('');
  } else {
    userHomeDirsTbody.innerHTML = `<tr class="empty-row"><td colspan="3" style="text-align:center; padding: 2rem;">${t('options.placeholders.noHomeDirs')}</td></tr>`;
  }
}

async function refreshSessions() {
  const endpoint = isAdmin ? '/api/admin/sessions' : '/api/sessions';
  try {
    const data = await secureFetch(endpoint, { method: 'GET' });
    if (isAdmin) renderAdminSessions(data);
    else renderUserSessions(data);
  } catch (error) {
    displayStatus(t('options.status.sessionsLoadFailed', { error: error.message }), true);
    sessionsContainer.innerHTML = `<p style="text-align: center; color: var(--text-muted);">${t('options.placeholders.errorLoading')}</p>`;
  }
}

function sessionRowHtml(s) {
  let contextHtml = '';
  if (s.launch_context) {
    const icon = s.launch_context.type === 'url' ? 'fa-link' : 'fa-file-alt';
    const value = escapeHtml(s.launch_context.value);
    contextHtml = `<div class="session-context" title="${value}"><i class="fas ${icon}"></i> ${value}</div>`;
  }
  return `
                        <tr>
                            <td>
                                <div style="display: flex; align-items: center; gap: 1rem;">
                                    <img data-logo-src="${escapeHtml(s.app_logo)}" src="icons/icon128.png" alt="${escapeHtml(s.app_name)}" style="width: 32px; height: 32px; object-fit: contain;">
                                    <div>
                                        <span>${escapeHtml(s.app_name)}</span>
                                        ${contextHtml}
                                    </div>
                                </div>
                            </td>
                            <td>${timeAgo(s.created_at, t)}</td>
                            <td class="actions-cell">
                                <button class="secondary stop-session-btn" data-session-id="${escapeHtml(s.session_id)}">${t('common.stop')}</button>
                            </td>
                        </tr>
                    `;
}

function sessionsTableHtml(sessions, tableAttr) {
  return `
        <div class="table-container">
            <table ${tableAttr}>
                <thead>
                    <tr>
                        <th>${t('options.sessions.application')}</th>
                        <th>${t('options.sessions.started')}</th>
                        <th class="actions-cell">${t('common.actions')}</th>
                    </tr>
                </thead>
                <tbody>
                    ${sessions.map(sessionRowHtml).join('')}
                </tbody>
            </table>
        </div>
    `;
}

function renderUserSessions(sessions) {
  if (!sessions || sessions.length === 0) {
    sessionsContainer.innerHTML = `<p style="text-align: center; color: var(--text-muted); padding: 2rem;">${t('options.sessions.noSessionsUser')}</p>`;
    return;
  }
  sessionsContainer.innerHTML = sessionsTableHtml(sessions, 'id="user-sessions-table"');
  hydrateLogos(sessionsContainer);
}

function renderAdminSessions(usersWithSessions) {
  if (!usersWithSessions || usersWithSessions.length === 0) {
    sessionsContainer.innerHTML = `<p style="text-align: center; color: var(--text-muted); padding: 2rem;">${t('options.sessions.noSessionsAdmin')}</p>`;
    return;
  }

  sessionsContainer.innerHTML = usersWithSessions.map((userData) => `
        <details class="collapsible-section" open>
            <summary><h4>${t('options.sessions.sessionsFor', { username: escapeHtml(userData.username), count: userData.sessions.length })}</h4></summary>
            <div>
                ${sessionsTableHtml(userData.sessions, 'class="admin-sessions-table"')}
            </div>
        </details>
    `).join('');
  hydrateLogos(sessionsContainer);
}

async function refreshAppData() {
  try {
    const [stores, installed, templates] = await Promise.all([
      secureFetch('/api/admin/apps/stores', { method: 'GET' }),
      secureFetch('/api/admin/apps/installed', { method: 'GET' }),
      secureFetch('/api/admin/apps/templates', { method: 'GET' }),
    ]);
    adminData.appStores = stores;
    adminData.installedApps = installed;
    adminData.appTemplates = templates;

    renderAppStoreSelect();
    renderTable('installedApps');

    if (appLaboratoryTabInitialized) populateLabDropdowns();
    if (info.shell === 'web') populateWebLab();

    const selectedStoreUrl = appStoreSelect.value;
    if (selectedStoreUrl) await fetchAndRenderAvailableApps(selectedStoreUrl);
  } catch (error) {
    displayStatus(t('options.status.appDataRefreshFailed', { error: error.message }), true);
  }
}

async function refreshInstalledApps() {
  try {
    const installed = await secureFetch('/api/admin/apps/installed', { method: 'GET' });
    adminData.installedApps = installed;
    renderTable('installedApps');

    const isStillPulling = installed.some((app) => app.pull_status === 'pulling');
    if (!isStillPulling && installedAppsPollingInterval) {
      clearInterval(installedAppsPollingInterval);
      installedAppsPollingInterval = null;
    }
  } catch (error) {
    console.error('Failed to poll installed apps:', error);
    if (installedAppsPollingInterval) {
      clearInterval(installedAppsPollingInterval);
      installedAppsPollingInterval = null;
    }
  }
}

function renderAppStoreSelect() {
  const currentVal = appStoreSelect.value;
  appStoreSelect.innerHTML = '';
  adminData.appStores.forEach((store) => {
    appStoreSelect.add(new Option(store.name, store.url));
  });
  if ([...appStoreSelect.options].some((o) => o.value === currentVal)) {
    appStoreSelect.value = currentVal;
  }
}

async function fetchAndRenderAvailableApps(storeUrl) {
  try {
    displayStatus(t('options.status.fetchingApps'));
    const selectedStoreName = appStoreSelect.options[appStoreSelect.selectedIndex].text;
    const apiUrl = `/api/admin/apps/available?url=${encodeURIComponent(storeUrl)}&store_name=${encodeURIComponent(selectedStoreName)}`;
    const apps = await secureFetch(apiUrl, { method: 'GET' });
    adminData.availableApps = apps;
    document.getElementById('available-apps-title').textContent = t('options.appStore.availableFrom', { storeName: selectedStoreName });
    renderAvailableAppsGrid();
  } catch (error) {
    displayStatus(t('options.status.fetchAppsFailed', { error: error.message }), true);
    availableAppsContainer.innerHTML = `<p style="text-align: center; color: var(--text-muted);">${t('options.appStore.couldNotLoad')}</p>`;
  }
}

function renderAvailableAppsGrid() {
  const paginationEl = document.getElementById('available-apps-pagination');
  const searchTerm = tableStates.availableApps.searchTerm.toLowerCase();

  const filteredData = searchTerm
    ? adminData.availableApps.filter((app) => app.name.toLowerCase().includes(searchTerm) || app.id.toLowerCase().includes(searchTerm))
    : adminData.availableApps;

  if (filteredData.length > 0) {
    availableAppsContainer.innerHTML = filteredData.map((app) => `
            <div class="app-card" data-appid="${escapeHtml(app.id)}" title="${t('options.modals.installAppTitle', { appName: escapeHtml(app.name) })}">
                <img data-logo-src="${escapeHtml(app.logo)}" src="icons/icon128.png" alt="${escapeHtml(app.name)} logo" class="app-card-logo">
                <div class="app-card-name">${escapeHtml(app.name)}</div>
            </div>
        `).join('');
  } else {
    availableAppsContainer.innerHTML = `<p style="text-align: center; color: var(--text-muted); grid-column: 1 / -1;">${t('options.appStore.noAppsFound')}</p>`;
  }

  paginationEl.innerHTML = '';
  hydrateLogos(availableAppsContainer);
}

// --- PROOT APPS CATALOGS ---

/** The state of this node's copy of a catalog, as a cell. */
function prootStateHtml(item) {
  const size = item.size ? ` · ${formatBytes(item.size, t, 1)}` : '';
  if (item.state === 'syncing') {
    return `<span class="proot-state"><div class="spinner-small"></div> ${t('options.prootApps.stateSyncing', { done: item.done, total: item.total })}${item.current ? ` (${escapeHtml(item.current)})` : ''}</span>`;
  }
  if (item.state === 'ready') {
    const ago = item.synced_at ? ` · ${t('options.prootApps.syncedAgo', { ago: timeAgo(item.synced_at, t) })}` : '';
    return `<span class="proot-state" title="${escapeHtml(item.message || '')}"><i class="fas fa-check-circle" style="color: var(--color-success);"></i> ${t('options.prootApps.stateReady')}${size}${ago}</span>`;
  }
  if (item.state === 'error') {
    return `<span class="proot-state" title="${escapeHtml(item.message || '')}"><i class="fas fa-exclamation-circle" style="color: var(--color-danger);"></i> ${t('options.prootApps.stateError')}${size}<br><small>${escapeHtml(item.message || '')}</small></span>`;
  }
  return `<span class="proot-state">${t('options.prootApps.statePending')}</span>`;
}

/** Load the catalogs with this node's state of each, and poll while one is syncing. */
async function refreshProotCatalogs() {
  try {
    adminData.prootCatalogs = await secureFetch('/api/admin/proot/catalogs', { method: 'GET' });
    adminData.proot_catalogs = adminData.prootCatalogs.map(({ id, name }) => ({ id, name }));
    renderTable('prootCatalogs');
    populateCatalogDropdowns();
    const syncing = adminData.prootCatalogs.some((c) => c.state === 'syncing');
    if (syncing && !prootPoll) prootPoll = setInterval(refreshProotCatalogs, 3000);
    if (!syncing && prootPoll) { clearInterval(prootPoll); prootPoll = null; }
  } catch (error) {
    clearInterval(prootPoll);
    prootPoll = null;
    displayStatus(t('options.status.catalogsRefreshFailed', { error: error.message }), true);
  }
}

const prootKey = (remote, name) => `${remote.toLowerCase()}:${name}`;
const prootSelected = (remote, name) => prootEditor.apps.some((a) => prootKey(a.remote, a.name) === prootKey(remote, name));

/** Open the editor on a catalog, or empty for a new one. */
async function openProotEditor(catalog) {
  prootEditor.id = catalog ? catalog.id : null;
  prootEditor.apps = catalog ? catalog.apps.map((a) => ({ remote: a.remote, name: a.name })) : [];
  document.getElementById('proot-editor-title').textContent = catalog
    ? t('options.prootApps.editCatalog', { name: catalog.name })
    : t('options.prootApps.newCatalog');
  document.getElementById('proot-catalog-name').value = catalog ? catalog.name : '';
  document.getElementById('proot-catalog-auto-update').checked = catalog ? catalog.auto_update : true;
  document.getElementById('proot-remote-search').value = '';
  prootEditor.search = '';
  const remote = (catalog && catalog.apps[0] && catalog.apps[0].remote) || adminData.proot_remote;
  document.getElementById('proot-remote').value = remote;
  document.getElementById('proot-editor').style.display = 'block';
  renderProotSelected();
  await loadProotRemote(remote, false);
  document.getElementById('proot-editor').scrollIntoView({ behavior: 'smooth', block: 'start' });
}

function closeProotEditor() {
  document.getElementById('proot-editor').style.display = 'none';
  prootEditor.id = null;
  prootEditor.apps = [];
}

/** Fetch the apps a remote publishes and show them in the grid. */
async function loadProotRemote(remote, refresh) {
  const grid = document.getElementById('proot-remote-grid');
  grid.innerHTML = '<div class="spinner-small"></div>';
  try {
    const apps = await secureFetch(`/api/admin/proot/remote?remote=${encodeURIComponent(remote)}&refresh=${refresh ? 'true' : 'false'}`, { method: 'GET' });
    prootEditor.remote = remote;
    prootEditor.remoteApps = apps;
    apps.forEach((app) => { if (app.icon) prootEditor.icons[prootKey(remote, app.name)] = app.icon; });
    renderProotRemoteGrid();
    renderProotSelected();
  } catch (error) {
    prootEditor.remoteApps = [];
    grid.innerHTML = `<p style="text-align: center; color: var(--text-muted); grid-column: 1 / -1;">${escapeHtml(t('options.prootApps.couldNotLoad', { remote, error: error.message }))}</p>`;
  }
}

function renderProotRemoteGrid() {
  const grid = document.getElementById('proot-remote-grid');
  const term = prootEditor.search.toLowerCase();
  const apps = prootEditor.remoteApps.filter((app) => !term || app.name.toLowerCase().includes(term) || app.full_name.toLowerCase().includes(term));
  if (!apps.length) {
    grid.innerHTML = `<p style="text-align: center; color: var(--text-muted); grid-column: 1 / -1;">${t('options.prootApps.noApps')}</p>`;
    return;
  }
  grid.innerHTML = apps.map((app, i) => {
    const classes = ['app-card-popup'];
    if (prootSelected(prootEditor.remote, app.name)) classes.push('selected');
    if (app.disabled) classes.push('disabled');
    const title = app.disabled ? t('options.prootApps.disabledHint') : app.description;
    return `
      <div class="${classes.join(' ')}" style="--i: ${i}" data-name="${escapeHtml(app.name)}" title="${escapeHtml(title)}">
        <img src="${escapeHtml(app.icon || 'icons/icon128.png')}" alt="">
        <span>${escapeHtml(app.full_name || app.name)}</span>
      </div>`;
  }).join('');
}

function renderProotSelected() {
  const box = document.getElementById('proot-selected');
  if (!prootEditor.apps.length) {
    box.innerHTML = `<span class="description">${t('options.prootApps.noneSelected')}</span>`;
    return;
  }
  box.innerHTML = prootEditor.apps.map((app) => {
    const icon = prootEditor.icons[prootKey(app.remote, app.name)];
    const other = app.remote.toLowerCase() !== adminData.proot_remote.toLowerCase()
      ? ` <small>${escapeHtml(t('options.prootApps.fromRemote', { remote: app.remote }))}</small>` : '';
    return `<span class="chip">${icon ? `<img src="${escapeHtml(icon)}" alt="">` : ''}${escapeHtml(app.name)}${other}<button type="button" data-remote="${escapeHtml(app.remote)}" data-name="${escapeHtml(app.name)}" title="${t('common.delete')}">&times;</button></span>`;
  }).join('');
}

function toggleProotApp(remote, name) {
  const selected = !prootSelected(remote, name);
  if (selected) {
    prootEditor.apps.push({ remote, name });
  } else {
    prootEditor.apps = prootEditor.apps.filter((a) => prootKey(a.remote, a.name) !== prootKey(remote, name));
  }
  renderProotSelected();
  // Flip the one card rather than rebuild the grid, which would animate every card again.
  if (remote.toLowerCase() === prootEditor.remote.toLowerCase()) {
    const card = [...document.querySelectorAll('#proot-remote-grid .app-card-popup[data-name]')].find((c) => c.dataset.name === name);
    if (card) card.classList.toggle('selected', selected);
  }
}

async function saveProotCatalog() {
  const name = document.getElementById('proot-catalog-name').value.trim();
  if (!name) return document.getElementById('proot-catalog-name').reportValidity();
  const payload = { name, auto_update: document.getElementById('proot-catalog-auto-update').checked, apps: prootEditor.apps };
  try {
    await secureFetch(prootEditor.id ? `/api/admin/proot/catalogs/${prootEditor.id}` : '/api/admin/proot/catalogs', {
      method: prootEditor.id ? 'PUT' : 'POST',
      body: JSON.stringify(payload),
    });
    displayStatus(t('options.status.catalogSaved', { name }));
    closeProotEditor();
    await refreshProotCatalogs();
  } catch (error) {
    await writeFailed(error, t('options.status.catalogSaveFailed', { error: error.message }), refreshProotCatalogs);
  }
  return undefined;
}

function showInstallModal(appData, existingInstall = null, isManual = false) {
  const isEditing = !!existingInstall;
  document.getElementById('manual-install-note').style.display = isManual ? 'block' : 'none';
  document.getElementById('app-install-modal-title').textContent = isEditing
    ? t('options.modals.editAppTitle', { appName: existingInstall.name })
    : t('options.modals.installAppTitle', { appName: appData.name });

  document.getElementById('install-app-id').value = isEditing ? existingInstall.id : '';
  document.getElementById('install-source-app-id').value = isEditing ? existingInstall.source_app_id : appData.id;
  document.getElementById('install-source-name').value = isManual ? 'manual' : appStoreSelect.options[appStoreSelect.selectedIndex].text;
  document.getElementById('install-app-name').value = isEditing ? existingInstall.name : appData.name;
  document.getElementById('install-app-image').value = isEditing ? existingInstall.provider_config.image : appData.provider_config.image;

  document.getElementById('install-gpu-support').checked = isEditing
    ? (existingInstall.provider_config.nvidia_support || existingInstall.provider_config.dri3_support)
    : (appData.provider_config.nvidia_support || appData.provider_config.dri3_support);
  document.getElementById('install-home-support').checked = isEditing ? existingInstall.home_directories : true;
  document.getElementById('install-url-support').checked = isEditing ? existingInstall.provider_config.url_support : appData.provider_config.url_support;
  document.getElementById('install-open-support').checked = isEditing ? existingInstall.provider_config.open_support : appData.provider_config.open_support;
  document.getElementById('install-auto-update').checked = isEditing ? existingInstall.auto_update : true;

  document.getElementById('install-app-users').value = isEditing ? existingInstall.users.join(',') : 'all';
  document.getElementById('install-app-groups').value = isEditing ? existingInstall.groups.join(',') : 'all';

  const templateSelect = document.getElementById('install-app-template');
  templateSelect.innerHTML = '';
  if (adminData.appTemplates.length > 0) {
    adminData.appTemplates.forEach((template) => {
      const option = document.createElement('option');
      option.value = template.name;
      option.textContent = template.name;
      templateSelect.appendChild(option);
    });
  } else {
    templateSelect.innerHTML = '<option value="">No templates available</option>';
  }
  templateSelect.disabled = adminData.appTemplates.length === 0;

  if (isEditing) {
    templateSelect.value = existingInstall.app_template || (adminData.appTemplates.length > 0 ? adminData.appTemplates[0].name : '');
  } else if (adminData.appTemplates.length > 0) {
    const defaultTemplate = adminData.appTemplates.find((tpl) => tpl.name === 'Default');
    templateSelect.value = defaultTemplate ? defaultTemplate.name : adminData.appTemplates[0].name;
  }

  const autostartTextArea = document.getElementById('install-autostart-script');
  const autostartWaylandTextArea = document.getElementById('install-autostart-wayland-script');
  const placeholder = 'program ${SEALSKIN_FILE:+"$SEALSKIN_FILE"} ${SEALSKIN_URL:+"$SEALSKIN_URL"}';
  autostartTextArea.placeholder = placeholder;
  autostartWaylandTextArea.placeholder = placeholder;

  const source = isEditing ? existingInstall.provider_config : appData.provider_config;
  autostartTextArea.value = decodeB64(source.custom_autostart_script_b64, 'autostart script');
  autostartWaylandTextArea.value = decodeB64(source.custom_autostart_wayland_script_b64, 'wayland autostart script');

  appInstallModal.style.display = 'block';
  markClampedDescriptions(appInstallModal);
}

function showImageUpdateModal(app) {
  currentAppForUpdateCheck = app;
  imageUpdateModalTitle.textContent = t('options.modals.updateStatusTitle', { appName: app.name });
  imageUpdateModalBody.innerHTML = `<div class="spinner"></div><p>${t('options.modals.checkingUpdates')}</p>`;
  imageUpdateModal.style.display = 'block';

  secureFetch(`/api/admin/apps/installed/${app.id}/check_update`, { method: 'POST' })
    .then((data) => {
      const currentSha = data.current_sha ? data.current_sha.substring(0, 12) : t('common.na');
      if (data.update_available) {
        imageUpdateModalBody.innerHTML = `
                    <p><i class="fas fa-arrow-alt-circle-up" style="color: var(--color-success-text);"></i> ${t('options.modals.updateAvailable')}</p>
                    <p>${t('options.modals.yourVersion', { sha: `<span class="sha-display">${currentSha}</span>` })}</p>
                    <p>${t('options.modals.latestAvailable')}</p>
                `;
        imageUpdateModalFooter.innerHTML = `
                    <button class="primary" id="pull-latest-image-btn">${t('options.modals.pullLatest')}</button>
                `;
      } else {
        imageUpdateModalBody.innerHTML = `
                    <p><i class="fas fa-check-circle" style="color: var(--color-success-text);"></i> ${t('options.modals.upToDate')}</p>
                    <p>${t('options.modals.currentVersion', { sha: `<span class="sha-display">${currentSha}</span>` })}</p>
                `;
      }
    })
    .catch((error) => {
      imageUpdateModalBody.innerHTML = `
                <p><i class="fas fa-exclamation-circle" style="color: var(--color-danger-text);"></i> ${t('options.modals.errorChecking')}</p>
                <p style="color: var(--text-muted); font-size: 0.9em;">${escapeHtml(error.message)}</p>
            `;
    });
}

async function handlePullLatestImage() {
  if (!currentAppForUpdateCheck) return;

  imageUpdateModalBody.innerHTML = `<div class="spinner"></div><p>${t('options.modals.pullingLatest')}</p>`;
  imageUpdateModalFooter.innerHTML = '';

  try {
    const data = await secureFetch(`/api/admin/apps/installed/${currentAppForUpdateCheck.id}/pull_latest`, { method: 'POST' });
    const newSha = data.new_sha ? data.new_sha.substring(0, 12) : t('common.na');
    imageUpdateModalBody.innerHTML = `
            <p><i class="fas fa-check-circle" style="color: var(--color-success-text);"></i> ${t('options.modals.pullComplete')}</p>
            <p>${t('options.modals.newVersion', { sha: `<span class="sha-display">${newSha}</span>` })}</p>
        `;
    await refreshAppData();
  } catch (error) {
    imageUpdateModalBody.innerHTML = `
            <p><i class="fas fa-exclamation-circle" style="color: var(--color-danger-text);"></i> ${t('options.modals.errorPulling')}</p>
            <p style="color: var(--text-muted); font-size: 0.9em;">${escapeHtml(error.message)}</p>
        `;
  } finally {
    imageUpdateModalFooter.innerHTML = `<button class="primary close-button" data-modal-id="image-update-modal">${t('common.close')}</button>`;
  }
}

async function renderPinnedBehaviorTable() {
  const tbody = document.querySelector('#pinned-behavior-table tbody');
  try {
    const allItems = await bridge.storageGet(null);
    const pinnedItems = Object.entries(allItems || {}).filter(([key]) => key.startsWith('workflow_profile_'));

    if (pinnedItems.length === 0) {
      tbody.innerHTML = `<tr class="empty-row"><td colspan="3" style="text-align:center; padding: 2rem;">${t('options.placeholders.noPinned')}</td></tr>`;
      return;
    }

    if (!isLoggedIn) {
      tbody.innerHTML = '<tr class="empty-row"><td colspan="3" style="text-align:center;">Login to view app names.</td></tr>';
      return;
    }

    const appsData = await secureFetch('/api/applications', { method: 'POST', body: JSON.stringify({}) });
    const appNameMap = new Map((appsData || []).map((app) => [app.id, app.name]));

    tbody.innerHTML = pinnedItems.map(([key, value]) => {
      let triggerText = '';
      if (key === 'workflow_profile_simple') triggerText = t('options.pinned.triggerSimple');
      else if (key === 'workflow_profile_url') triggerText = t('options.pinned.triggerUrl');
      else triggerText = t('options.pinned.triggerFile', { fileType: key.replace('workflow_profile_.', '') });

      const appName = appNameMap.get(value.appId) || t('options.pinned.unknownApp', { appId: String(value.appId || '').substring(0, 8) });

      return `
                    <tr>
                        <td>${escapeHtml(triggerText)}</td>
                        <td>${escapeHtml(appName)}</td>
                        <td class="actions-cell">
                            <button class="danger" data-storage-key="${escapeHtml(key)}">${t('common.delete')}</button>
                        </td>
                    </tr>
                `;
    }).join('');
  } catch (error) {
    tbody.innerHTML = '<tr class="empty-row"><td colspan="3" style="text-align:center;">Error loading pinned behaviors.</td></tr>';
    console.error('Error rendering pinned behaviors:', error);
  }
}

async function openTab(tabName) {
  if (installedAppsPollingInterval) {
    clearInterval(installedAppsPollingInterval);
    installedAppsPollingInterval = null;
  }
  clearInterval(labPoll);
  clearInterval(prootPoll);
  prootPoll = null;

  const oldActiveTab = document.querySelector('.tab-content.active');
  if (oldActiveTab && oldActiveTab.id === 'InstalledApps' && tabName !== 'InstalledApps') {
    const searchInput = document.getElementById('installedApps-search');
    if (searchInput.dataset.transient) {
      searchInput.value = '';
      searchInput.dispatchEvent(new Event('input'));
      delete searchInput.dataset.transient;
    }
  }

  document.querySelectorAll('.tab-content').forEach((tab) => tab.classList.remove('active'));
  document.querySelectorAll('.nav-link').forEach((link) => link.classList.remove('active'));
  document.getElementById(tabName).classList.add('active');
  document.querySelector(`.nav-link[data-tabname="${tabName}"]`).classList.add('active');
  markClampedDescriptions(document.getElementById(tabName));

  if (tabName === 'Home' && isLoggedIn) {
    await refreshHomeDirs();
  } else if (tabName === 'Sessions' && isLoggedIn) {
    await refreshSessions();
  } else if (tabName === 'InstalledApps') {
    if (adminData.installedApps.length === 0) await refreshAppData();
    if (adminData.installedApps.some((app) => app.pull_status === 'pulling')) {
      installedAppsPollingInterval = setInterval(refreshInstalledApps, 3000);
    }
  } else if (tabName === 'AppStore') {
    if (adminData.appStores.length === 0) await refreshAppData();
  } else if (tabName === 'ProotApps') {
    await refreshProotCatalogs();
  } else if (tabName === 'PinnedBehavior') {
    await renderPinnedBehaviorTable();
  } else if (tabName === 'AppTemplates') {
    await initializeAppTemplatesTab();
  } else if (tabName === 'AppLaboratory') {
    initializeAppLaboratoryTab();
  } else if (tabName === 'WebLaboratory') {
    await openWebLab();
  } else if (tabName === 'Cluster' || tabName === 'SignIn') {
    await refreshCluster();
  } else if (tabName === 'Audit') {
    await refreshAudit();
  }
}

function applyMobileLayout() {
  const searchSelect = document.getElementById('searchEngineDashboard');
  const formGroup = searchSelect?.closest('.form-group');
  if (formGroup && formGroup.parentElement) formGroup.parentElement.style.display = 'none';

  const safeAreaPad = addMobileSafeArea();
  const optionsContainer = document.querySelector('.options-container');
  if (optionsContainer) {
    optionsContainer.style.height = `calc(100vh - ${safeAreaPad.style.paddingTop})`;
  }
}

/**
 * The pick bookmarklet, serialized into its link and run in the page it is
 * clicked on. The next click on a link opens that link here; on an image,
 * video, or audio, or a Shift-click on a link, the page fetches the file with
 * its own cookies and hands it to `receive.html`. What the page cannot fetch
 * opens as a link, and what has no web address sends the page itself. Chrome
 * and Firefox keep clicks on a media element's own controls from the page, so
 * each audio or video that shows them is covered by a shield until the click.
 *
 * @param {string} app The web app's address.
 * @param {string} hint The banner shown until the click.
 */
function pickForSealSkin(app, hint) {
  const banner = document.createElement('div');
  const shields = new Map([...document.querySelectorAll('audio[controls], video[controls]')].map((media) => {
    const shield = document.createElement('div');
    const box = media.getBoundingClientRect();
    shield.style.cssText = `position:absolute;left:${box.left + scrollX}px;top:${box.top + scrollY}px;width:${box.width}px;height:${box.height}px;z-index:2147483646;cursor:pointer`;
    return [shield, media];
  }));
  const stop = () => {
    banner.remove();
    shields.forEach((media, shield) => shield.remove());
    removeEventListener('click', onClick, true);
    removeEventListener('keydown', onKey, true);
  };
  const onKey = (event) => { if (event.key === 'Escape') stop(); };
  const onClick = (event) => {
    const media = shields.get(event.target) || (event.target.closest && event.target.closest('img, video, audio'));
    const link = event.target.closest && event.target.closest('a[href]');
    if (event.target !== banner && !media && !link) return;
    event.preventDefault();
    event.stopImmediatePropagation();
    stop();
    if (!media && !link) return;
    const target = event.shiftKey && link ? link : media || link;
    const url = target === link ? link.href : media.currentSrc || media.src;
    const web = /^https?:/.test(url);
    const address = `${app}?url=${encodeURIComponent(web ? url : location.href)}`;
    if (target === link && !event.shiftKey && !link.hasAttribute('download')) {
      open(address);
      return;
    }
    const tab = open(`${app}receive.html#${encodeURIComponent(address)}`);
    const ready = new Promise((resolve) => {
      addEventListener('message', function listen(message) {
        if (message.source !== tab || message.data !== 'sealskin-receive') return;
        removeEventListener('message', listen);
        resolve();
      });
    });
    fetch(url).then(async (response) => {
      if (!response.ok) throw new Error(response.statusText);
      const header = response.headers.get('content-disposition') || '';
      const encoded = /filename\*=UTF-8''([^;]+)/i.exec(header);
      const plain = /filename="?([^";]+)/i.exec(header);
      const name = (encoded && decodeURIComponent(encoded[1])) || (plain && plain[1]) || (target === link && link.download)
        || (web && decodeURIComponent(new URL(url).pathname.split('/').pop())) || 'file';
      const blob = await response.blob();
      await ready;
      tab.postMessage({ file: new File([blob], name, { type: blob.type }) }, new URL(app).origin);
    }).catch(() => { tab.location = address; });
  };
  banner.textContent = hint;
  banner.style.cssText = 'position:fixed;top:0;left:0;right:0;z-index:2147483647;padding:12px;text-align:center;font:15px/1.4 system-ui,sans-serif;color:#fff;background:#a82a69;cursor:pointer';
  document.body.append(banner, ...shields.keys());
  addEventListener('click', onClick, true);
  addEventListener('keydown', onKey, true);
}

/**
 * The web app's stand-ins for the context menu: a bookmarklet that opens the
 * current page (or searches the selection) here, one that picks a link or a
 * file on the page, and `web+sealskin:` links.
 */
function applyWebLayout() {
  document.getElementById('web-card').style.display = '';
  // No connection to change and no key to give a user: the server signs both in.
  changeConnectionButton.style.display = 'none';
  document.getElementById('sign-out-button').style.display = '';
  document.getElementById('newUserKeyGroup').style.display = 'none';
  // The web app's laboratory opens its session in a tab; the other shells keep the framed one.
  document.querySelector('.nav-link[data-tabname="AppLaboratory"]').dataset.tabname = 'WebLaboratory';
  const app = new URL('./', location.href).href;
  const bookmarklet = document.getElementById('web-bookmarklet');
  // A javascript: URL is percent-decoded before it runs, so a `%` in the address must survive that.
  const appLiteral = JSON.stringify(app).replace(/%/g, '%25');
  bookmarklet.href = `javascript:(()=>{const s=String(getSelection()).trim();window.open(${appLiteral}+(s?'?q='+encodeURIComponent(s):'?url='+encodeURIComponent(location.href)))})()`;
  const pick = document.getElementById('web-pick');
  pick.href = `javascript:(${encodeURIComponent(pickForSealSkin)})(${encodeURIComponent(JSON.stringify(app))},${encodeURIComponent(JSON.stringify(t('options.web.pickHint')))})`;
  [bookmarklet, pick].forEach((link) => link.addEventListener('click', (event) => event.preventDefault()));
  document.getElementById('web-protocol').hidden = typeof navigator.registerProtocolHandler !== 'function';
  document.getElementById('web-protocol-button').addEventListener('click', () => navigator.registerProtocolHandler('web+sealskin', `${app}?url=%s`));
}

function bindEvents() {
  document.querySelectorAll('.nav-link').forEach((button) => {
    button.addEventListener('click', (event) => openTab(event.currentTarget.dataset.tabname));
  });

  changeConnectionButton.addEventListener('click', () => bridge.openPage('connect'));
  document.getElementById('sign-out-button').addEventListener('click', async () => {
    await secureFetch('/api/auth/signout', { method: 'POST', body: '{}' }).catch((e) => console.warn('Sign-out failed:', e));
    // The web app asks the server who is signed in and shows its sign-in.
    bridge.openPage('connect');
  });

  searchEngineDashboardSelect.addEventListener('change', async () => {
    try {
      await bridge.updateConfig({ searchEngineUrl: searchEngineDashboardSelect.value });
      config.searchEngineUrl = searchEngineDashboardSelect.value;
      displayStatus(t('options.status.settingsSaved'), false);
    } catch (error) {
      displayStatus(error.message, true);
    }
  });

  document.querySelectorAll('.close-button').forEach((btn) => btn.addEventListener('click', () => {
    document.getElementById(btn.dataset.modalId).style.display = 'none';
  }));
  window.addEventListener('click', (event) => {
    if (event.target.classList.contains('modal')) event.target.style.display = 'none';
  });

  copyConfigBtn.addEventListener('click', () => navigator.clipboard.writeText(generatedConfigText.value)
    .then(() => displayStatus(t('options.status.copySuccess')), () => displayStatus(t('options.status.copyFailed'), true)));
  downloadConfigBtn.addEventListener('click', () => {
    const username = downloadConfigBtn.dataset.username || 'user';
    downloadBlob(new Blob([generatedConfigText.value], { type: 'application/json' }), `${username}-sealskin-config.json`);
  });

  ['users', 'groups', 'admins', 'installedApps', 'prootCatalogs'].forEach((dataType) => {
    const searchInput = document.getElementById(`${dataType}-search`);
    if (searchInput) {
      searchInput.addEventListener('input', (e) => {
        tableStates[dataType].searchTerm = e.target.value;
        tableStates[dataType].currentPage = 1;
        renderTable(dataType);
      });
    }
    const pagination = document.getElementById(`${dataType}-pagination`);
    if (pagination) {
      pagination.addEventListener('click', (e) => {
        const button = e.target.closest('button');
        if (!button) return;
        if (button.dataset.page === 'prev') tableStates[dataType].currentPage--;
        if (button.dataset.page === 'next') tableStates[dataType].currentPage++;
        renderTable(dataType);
      });
    }
  });

  document.getElementById('available-apps-search').addEventListener('input', (e) => {
    tableStates.availableApps.searchTerm = e.target.value;
    renderAvailableAppsGrid();
  });

  addAdminForm.addEventListener('submit', async (event) => {
    event.preventDefault();
    const payload = {
      username: document.getElementById('newAdminUsername').value.trim(),
      public_key: document.getElementById('newAdminPublicKey').value.trim() || null,
    };
    if (!payload.username) return displayStatus(t('common.username') + ' is required.', true);
    try {
      displayStatus(t('options.status.creatingAdmin'));
      const response = await secureFetch('/api/admin/admins', { method: 'POST', body: JSON.stringify(payload) });
      displayStatus(t('options.status.adminCreated', { username: response.user.username }), false);
      if (response.private_key) showUserConfigModal(response.user, response.private_key, true);
      addAdminForm.reset();
      await refreshAdminData();
    } catch (error) {
      displayStatus(t('options.status.adminCreateFailed', { error: error.message }), true);
    }
    return undefined;
  });

  tableRenderConfig.admins.tbody.addEventListener('click', async (e) => {
    const button = e.target.closest('button');
    if (!button) return;
    if (button.classList.contains('copy-btn')) {
      navigator.clipboard.writeText(button.dataset.pubkey)
        .then(() => displayStatus(t('options.status.publicKeyCopied')))
        .catch(() => displayStatus(t('options.status.keyCopyFailed'), true));
      return;
    }
    const username = button.dataset.adminname;
    if (!username) return;
    if (button.classList.contains('danger')) {
      const remove = await confirmDialog(t, {
        title: t('options.admins.deleteTitle'),
        message: t('options.admins.confirmDelete', { username }),
        confirm: t('common.delete'),
        danger: true,
      });
      if (remove) {
        try {
          await secureFetch(`/api/admin/admins/${username}`, { method: 'DELETE' });
          displayStatus(t('options.status.adminDeleted', { username }));
          await refreshAdminData();
        } catch (error) {
          displayStatus(t('options.status.adminDeleteFailed', { error: error.message }), true);
        }
      }
    } else if (button.classList.contains('secondary')) {
      await refreshAdminUserHomeDirs(username, true);
      userHomeDirModal.style.display = 'block';
    }
  });

  addUserForm.addEventListener('submit', async (event) => {
    event.preventDefault();
    const payload = {
      username: document.getElementById('newUsername').value.trim(),
      // A user of the web app signs in through the server; null has the server generate a key file.
      public_key: info.shell === 'web' ? '' : document.getElementById('newUserPublicKey').value.trim() || null,
      settings: readUserSettings('newUser'),
    };
    if (!payload.username) return displayStatus(t('common.username') + ' is required.', true);
    try {
      displayStatus(t('options.status.creatingUser'));
      const response = await secureFetch('/api/admin/users', { method: 'POST', body: JSON.stringify(payload) });
      displayStatus(t('options.status.userCreated', { username: response.user.username }), false);
      if (response.private_key) showUserConfigModal(response.user, response.private_key, true);
      addUserForm.reset();
      await refreshAdminData();
    } catch (error) {
      await writeFailed(error, t('options.status.userCreateFailed', { error: error.message }), refreshAdminData);
    }
    return undefined;
  });

  tableRenderConfig.users.tbody.addEventListener('click', async (e) => {
    const button = e.target.closest('button');
    if (!button || button.disabled) return;
    if (button.classList.contains('copy-btn')) {
      navigator.clipboard.writeText(button.dataset.pubkey)
        .then(() => displayStatus(t('options.status.publicKeyCopied')))
        .catch(() => displayStatus(t('options.status.keyCopyFailed'), true));
      return;
    }
    const username = button.dataset.username;
    if (!username) return;
    if (button.dataset.action === 'approve') {
      try {
        await secureFetch(`/api/admin/users/${username}/approve`, { method: 'POST' });
        displayStatus(t('options.status.userApproved', { username }));
        await refreshAdminData();
      } catch (error) {
        await writeFailed(error, t('options.status.userApproveFailed', { error: error.message }), refreshAdminData);
      }
    } else if (button.classList.contains('danger')) {
      const remove = await confirmDialog(t, {
        title: t('options.users.deleteTitle'),
        message: t('options.users.confirmDelete', { username }),
        confirm: t('common.delete'),
        danger: true,
      });
      if (remove) {
        try {
          await secureFetch(`/api/admin/users/${username}`, { method: 'DELETE' });
          displayStatus(t('options.status.userDeleted', { username }));
          await refreshAdminData();
        } catch (error) {
          await writeFailed(error, t('options.status.userDeleteFailed', { error: error.message }), refreshAdminData);
        }
      }
    } else if (button.classList.contains('warning')) {
      const user = adminData.users.find((u) => u.username === username);
      if (!user) return;
      document.getElementById('user-edit-title').textContent = t('options.modals.editUserTitle', { username });
      document.getElementById('editUsername').value = username;
      fillUserSettings('editUser', user.settings);
      const effectiveSettingsPre = document.getElementById('effective-settings-pre');
      const updateEffectiveSettingsDisplay = () => {
        const settings = { ...readUserSettings('editUser'), provider_groups: user.settings.provider_groups || [] };
        effectiveSettingsPre.textContent = JSON.stringify(calculateEffectiveSettings({ settings }), null, 2);
      };
      userEditForm.oninput = updateEffectiveSettingsDisplay;
      updateEffectiveSettingsDisplay();
      userEditModal.style.display = 'block';
      markClampedDescriptions(userEditModal);
    } else if (button.classList.contains('secondary')) {
      await refreshAdminUserHomeDirs(username, false);
      userHomeDirModal.style.display = 'block';
    }
  });

  userEditForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    const username = document.getElementById('editUsername').value;
    try {
      await secureFetch(`/api/admin/users/${username}`, {
        method: 'PUT',
        body: JSON.stringify({ settings: readUserSettings('editUser') }),
      });
      displayStatus(t('options.status.userUpdated', { username }));
      userEditModal.style.display = 'none';
      await refreshAdminData();
    } catch (error) {
      await writeFailed(error, t('options.status.userUpdateFailed', { error: error.message }), async () => {
        userEditModal.style.display = 'none';
        await refreshAdminData();
      });
    }
  });

  addGroupForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    const groupName = document.getElementById('newGroupName').value.trim();
    if (!groupName) return displayStatus(t('common.group') + ' name is required.', true);
    const payload = { name: groupName, settings: readGroupSettings('newGroup') };
    try {
      await secureFetch('/api/admin/groups', { method: 'POST', body: JSON.stringify(payload) });
      displayStatus(t('options.status.groupCreated', { groupName }));
      addGroupForm.reset();
      await refreshAdminData();
    } catch (error) {
      await writeFailed(error, t('options.status.groupCreateFailed', { error: error.message }), refreshAdminData);
    }
    return undefined;
  });

  tableRenderConfig.groups.tbody.addEventListener('click', async (e) => {
    const button = e.target.closest('button');
    if (!button) return;
    const groupName = button.dataset.groupname;
    if (!groupName) return;
    if (button.classList.contains('danger')) {
      const remove = await confirmDialog(t, {
        title: t('options.groups.deleteTitle'),
        message: t('options.groups.confirmDelete', { groupName }),
        confirm: t('common.delete'),
        danger: true,
      });
      if (remove) {
        try {
          await secureFetch(`/api/admin/groups/${groupName}`, { method: 'DELETE' });
          displayStatus(t('options.status.groupDeleted', { groupName }));
          await refreshAdminData();
        } catch (error) {
          await writeFailed(error, t('options.status.groupDeleteFailed', { error: error.message }), refreshAdminData);
        }
      }
    } else if (button.classList.contains('warning')) {
      const group = adminData.groups.find((g) => g.name === groupName);
      if (!group) return;
      document.getElementById('group-edit-title').textContent = t('options.modals.editGroupTitle', { groupName });
      document.getElementById('editGroupName').value = groupName;
      fillGroupSettings('editGroup', group.settings || {});
      groupEditModal.style.display = 'block';
      markClampedDescriptions(groupEditModal);
    }
  });

  groupEditForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    const groupName = document.getElementById('editGroupName').value;
    const payload = { settings: readGroupSettings('editGroup') };
    try {
      await secureFetch(`/api/admin/groups/${groupName}`, { method: 'PUT', body: JSON.stringify(payload) });
      displayStatus(t('options.status.groupUpdated', { groupName }));
      groupEditModal.style.display = 'none';
      await refreshAdminData();
    } catch (error) {
      await writeFailed(error, t('options.status.groupUpdateFailed', { error: error.message }), async () => {
        groupEditModal.style.display = 'none';
        await refreshAdminData();
      });
    }
  });

  addHomeDirForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    const homeNameInput = document.getElementById('newHomeDirName');
    const homeName = homeNameInput.value.trim();
    if (!homeName) return;
    try {
      await secureFetch('/api/homedirs', { method: 'POST', body: JSON.stringify({ home_name: homeName }) });
      displayStatus(t('options.status.homedirCreated', { homeName }));
      homeNameInput.value = '';
      await refreshHomeDirs();
    } catch (error) {
      displayStatus(t('options.status.homedirCreateFailed', { error: error.message }), true);
    }
  });

  homeDirsTbody.addEventListener('click', async (e) => {
    const button = e.target.closest('button');
    if (!button) return;
    const homeName = button.dataset.homedirName;

    if (button.classList.contains('manage-btn')) {
      openPage('files', { home: homeName });
    } else if (button.classList.contains('move-btn')) {
      const elsewhere = myCluster.nodes.filter((node) => node.id !== myCluster.homes[homeName]);
      await moveHome(`/api/cluster/homedirs/${encodeURIComponent(homeName)}/move`, homeName, elsewhere, refreshHomeDirs);
    } else if (button.classList.contains('danger')) {
      const remove = await confirmDialog(t, {
        title: t('options.home.deleteTitle'),
        message: t('options.home.confirmDelete', { homeName }),
        confirm: t('common.delete'),
        danger: true,
      });
      if (remove) {
        try {
          await secureFetch(`/api/homedirs/${homeName}`, { method: 'DELETE' });
          displayStatus(t('options.status.homedirDeleted', { homeName }));
          await refreshHomeDirs();
        } catch (error) {
          displayStatus(t('options.status.homedirDeleteFailed', { error: error.message }), true);
        }
      }
    }
  });

  adminAddHomeDirForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    if (!currentAdminManagedUser) return;
    const homeNameInput = document.getElementById('adminNewHomeDirName');
    const homeName = homeNameInput.value.trim();
    if (!homeName) return;
    try {
      const { username, isAdmin: isAdminUser } = currentAdminManagedUser;
      const path = isAdminUser ? 'admins' : 'users';
      await secureFetch(`/api/admin/${path}/${username}/homedirs`, { method: 'POST', body: JSON.stringify({ home_name: homeName }) });
      displayStatus(t('options.status.homedirCreatedFor', { homeName, username }));
      homeNameInput.value = '';
      await refreshAdminUserHomeDirs(username, isAdminUser);
    } catch (error) {
      displayStatus(t('options.status.homedirCreateFailed', { error: error.message }), true);
    }
  });

  userHomeDirsTbody.addEventListener('click', async (e) => {
    const button = e.target.closest('button');
    if (!button || !currentAdminManagedUser) return;
    const homeName = button.dataset.homedirName;
    const { username, isAdmin: isAdminUser } = currentAdminManagedUser;
    if (button.classList.contains('move-btn')) {
      if (!clusterData) await refreshCluster();
      const nodes = clusterData ? clusterData.nodes.filter((node) => node.approved && node.id !== managedHomes[homeName]) : [];
      const url = `/api/admin/cluster/users/${encodeURIComponent(username)}/homedirs/${encodeURIComponent(homeName)}/move`;
      await moveHome(url, homeName, nodes, () => refreshAdminUserHomeDirs(username, isAdminUser));
    } else if (await confirmDialog(t, {
      title: t('options.home.deleteTitle'),
      message: t('options.modals.confirmDeleteDir', { homeName, username }),
      confirm: t('common.delete'),
      danger: true,
    })) {
      try {
        const path = isAdminUser ? 'admins' : 'users';
        await secureFetch(`/api/admin/${path}/${username}/homedirs/${homeName}`, { method: 'DELETE' });
        displayStatus(t('options.status.homedirDeletedFor', { homeName, username }));
        await refreshAdminUserHomeDirs(username, isAdminUser);
      } catch (error) {
        displayStatus(t('options.status.homedirDeleteFailed', { error: error.message }), true);
      }
    }
  });

  refreshSessionsBtn.addEventListener('click', refreshSessions);
  sessionsContainer.addEventListener('click', async (e) => {
    const button = e.target.closest('button.stop-session-btn');
    if (!button) return;

    const sessionId = button.dataset.sessionId;
    const endpoint = isAdmin ? `/api/admin/sessions/${sessionId}` : `/api/sessions/${sessionId}`;

    button.disabled = true;
    button.innerHTML = '<i class="fas fa-spinner fa-spin"></i>';
    try {
      await secureFetch(endpoint, { method: 'DELETE' });
      displayStatus(t('options.status.sessionStopped'));
    } catch (error) {
      displayStatus(t('options.status.sessionStopError', { error: error.message }), true);
    }
    await refreshSessions();
  });

  appStoreSelect.addEventListener('change', (e) => fetchAndRenderAvailableApps(e.target.value));

  refreshAppStoreBtn.addEventListener('click', () => {
    const selectedStoreUrl = appStoreSelect.value;
    if (selectedStoreUrl) fetchAndRenderAvailableApps(selectedStoreUrl);
    else displayStatus('Please select an app store to refresh.', true);
  });

  addAppStoreForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    const name = document.getElementById('new-app-store-name').value.trim();
    const url = document.getElementById('new-app-store-url').value.trim();
    if (!name || !url) return;
    try {
      await secureFetch('/api/admin/apps/stores', { method: 'POST', body: JSON.stringify({ name, url }) });
      displayStatus(t('options.status.appStoreAdded', { name }));
      addAppStoreForm.reset();
      await refreshAppData();
    } catch (error) {
      displayStatus(t('options.status.appStoreAddFailed', { error: error.message }), true);
    }
  });

  addManualAppBtn.addEventListener('click', () => {
    showInstallModal({
      id: '',
      name: 'My Custom App',
      logo: '',
      provider_config: {
        image: 'image/name:tag',
        nvidia_support: true,
        dri3_support: true,
        url_support: true,
        open_support: true,
        autostart: false,
        custom_autostart_script_b64: null,
        extensions: [],
      },
    }, null, true);
  });

  availableAppsContainer.addEventListener('click', (e) => {
    const card = e.target.closest('.app-card[data-appid]');
    if (!card) return;
    const appData = adminData.availableApps.find((app) => app.id === card.dataset.appid);
    if (appData) showInstallModal(appData);
  });

  document.getElementById('proot-new-catalog-btn').addEventListener('click', () => openProotEditor(null));
  document.getElementById('proot-cancel-btn').addEventListener('click', closeProotEditor);
  document.getElementById('proot-save-btn').addEventListener('click', saveProotCatalog);
  document.getElementById('proot-editor-form').addEventListener('submit', (e) => { e.preventDefault(); saveProotCatalog(); });
  document.getElementById('proot-remote-form').addEventListener('submit', (e) => {
    e.preventDefault();
    loadProotRemote(document.getElementById('proot-remote').value.trim(), false);
  });
  document.getElementById('proot-remote-refresh').addEventListener('click', () => {
    loadProotRemote(document.getElementById('proot-remote').value.trim(), true);
  });
  document.getElementById('proot-remote-search').addEventListener('input', (e) => {
    prootEditor.search = e.target.value;
    renderProotRemoteGrid();
  });
  document.getElementById('proot-remote-grid').addEventListener('click', (e) => {
    const card = e.target.closest('.app-card-popup[data-name]');
    if (card) toggleProotApp(prootEditor.remote, card.dataset.name);
  });
  document.getElementById('proot-selected').addEventListener('click', (e) => {
    const button = e.target.closest('button[data-name]');
    if (button) toggleProotApp(button.dataset.remote, button.dataset.name);
  });
  tableRenderConfig.prootCatalogs.tbody.addEventListener('click', async (e) => {
    const button = e.target.closest('button[data-catalogid]');
    if (!button || button.disabled) return;
    const catalog = adminData.prootCatalogs.find((c) => c.id === button.dataset.catalogid);
    if (!catalog) return;
    if (button.dataset.action === 'edit') {
      await openProotEditor(catalog);
    } else if (button.dataset.action === 'update') {
      try {
        await secureFetch(`/api/admin/proot/catalogs/${catalog.id}/update`, { method: 'POST', body: JSON.stringify({}) });
        displayStatus(t('options.status.catalogUpdateStarted', { name: catalog.name }));
        await refreshProotCatalogs();
      } catch (error) {
        await writeFailed(error, t('options.status.catalogUpdateFailed', { error: error.message }), refreshProotCatalogs);
      }
    } else if (button.dataset.action === 'delete') {
      const remove = await confirmDialog(t, {
        title: t('options.prootApps.deleteTitle'),
        message: t('options.prootApps.confirmDelete', { name: catalog.name }),
        confirm: t('common.delete'),
        danger: true,
      });
      if (!remove) return;
      try {
        await secureFetch(`/api/admin/proot/catalogs/${catalog.id}`, { method: 'DELETE' });
        displayStatus(t('options.status.catalogDeleted', { name: catalog.name }));
        if (prootEditor.id === catalog.id) closeProotEditor();
        await refreshProotCatalogs();
      } catch (error) {
        await writeFailed(error, t('options.status.catalogDeleteFailed', { error: error.message }), refreshProotCatalogs);
      }
    }
  });

  appInstallForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    const isEditing = !!document.getElementById('install-app-id').value;
    const appId = isEditing ? document.getElementById('install-app-id').value : crypto.randomUUID();

    const gpu = document.getElementById('install-gpu-support').checked;
    const sourceAppId = document.getElementById('install-source-app-id').value;
    const sourceApp = adminData.availableApps.find((a) => a.id === sourceAppId);
    const autostartValue = document.getElementById('install-autostart-script').value;
    const autostartWaylandValue = document.getElementById('install-autostart-wayland-script').value;

    const payload = {
      id: appId,
      name: document.getElementById('install-app-name').value,
      source: document.getElementById('install-source-name').value,
      source_app_id: sourceAppId,
      users: document.getElementById('install-app-users').value.split(',').map((s) => s.trim()).filter(Boolean),
      groups: document.getElementById('install-app-groups').value.split(',').map((s) => s.trim()).filter(Boolean),
      home_directories: document.getElementById('install-home-support').checked,
      auto_update: document.getElementById('install-auto-update').checked,
      app_template: document.getElementById('install-app-template').value,
      logo: sourceApp?.logo || '',
      url: sourceApp?.url || '',
      provider: sourceApp?.provider || 'docker',
      provider_config: {
        image: document.getElementById('install-app-image').value,
        port: sourceApp?.provider_config.port || 3000,
        type: sourceApp?.provider_config.type || 'app',
        extensions: sourceApp?.provider_config.extensions || [],
        nvidia_support: gpu,
        dri3_support: gpu,
        url_support: document.getElementById('install-url-support').checked,
        open_support: document.getElementById('install-open-support').checked,
        autostart: sourceApp?.provider_config.autostart || false,
        env: [],
      },
    };

    if (autostartValue) {
      payload.provider_config.custom_autostart_script_b64 = btoa(autostartValue);
    } else if (isEditing) {
      payload.provider_config.custom_autostart_script_b64 = '';
    }
    if (autostartWaylandValue) {
      payload.provider_config.custom_autostart_wayland_script_b64 = btoa(autostartWaylandValue);
    } else if (isEditing) {
      payload.provider_config.custom_autostart_wayland_script_b64 = '';
    }

    try {
      const method = isEditing ? 'PUT' : 'POST';
      const url = isEditing ? `/api/admin/apps/installed/${appId}` : '/api/admin/apps/installed';
      await secureFetch(url, { method, body: JSON.stringify(payload) });
      const action = isEditing ? t('options.status.appSaveActions.updated') : t('options.status.appSaveActions.installed');
      displayStatus(t('options.status.appSaved', { name: payload.name, action }));
      appInstallModal.style.display = 'none';
      await refreshAppData();
      await openTab('InstalledApps');
      const searchInput = document.getElementById('installedApps-search');
      searchInput.value = payload.name;
      searchInput.dataset.transient = 'true';
      searchInput.dispatchEvent(new Event('input'));
    } catch (error) {
      displayStatus(t('options.status.appSaveFailed', { error: error.message }), true);
    }
  });

  installedAppsTbody.addEventListener('click', async (e) => {
    const button = e.target.closest('button[data-appid]');
    if (!button) return;
    const appId = button.dataset.appid;
    const app = adminData.installedApps.find((a) => a.id === appId);
    if (!app) return;

    if (button.classList.contains('danger')) {
      const remove = await confirmDialog(t, {
        title: t('options.installedApps.deleteTitle'),
        message: t('options.installedApps.confirmDelete', { appName: app.name }),
        confirm: t('common.delete'),
        danger: true,
      });
      if (remove) {
        try {
          await secureFetch(`/api/admin/apps/installed/${appId}`, { method: 'DELETE' });
          displayStatus(t('options.status.appDeleted', { name: app.name }));
          await refreshAppData();
        } catch (error) {
          displayStatus(t('options.status.appDeleteFailed', { error: error.message }), true);
        }
      }
    } else if (button.classList.contains('warning')) {
      if (app.is_meta_app) {
        if (info.shell === 'web') {
          await openTab('WebLaboratory');
          if (!labSession) await loadLabApp(app);
        } else {
          await openTab('AppLaboratory');
          const labAppSelect = document.getElementById('lab-app-select');
          labAppSelect.value = app.id;
          labAppSelect.dispatchEvent(new Event('change'));
        }
      } else {
        const sourceApp = adminData.availableApps.find((a) => a.id === app.source_app_id);
        const appDataForModal = sourceApp || {
          id: app.source_app_id,
          name: app.name,
          logo: app.logo,
          url: app.url,
          provider: app.provider,
          provider_config: app.provider_config,
        };
        showInstallModal(appDataForModal, app);
      }
    } else if (button.classList.contains('check-update-btn')) {
      showImageUpdateModal(app);
    }
  });

  imageUpdateModalFooter.addEventListener('click', (e) => {
    if (e.target.id === 'pull-latest-image-btn') handlePullLatestImage();
  });

  document.querySelector('#pinned-behavior-table tbody').addEventListener('click', async (e) => {
    const button = e.target.closest('button.danger');
    if (!button) return;
    const key = button.dataset.storageKey;
    const prettyKey = key.replace('workflow_profile_', '');
    const remove = await confirmDialog(t, {
      title: t('options.pinned.removeTitle'),
      message: t('options.pinned.confirmRemove', { name: prettyKey }),
      confirm: t('common.remove'),
      danger: true,
    });
    if (remove) {
      await bridge.storageRemove([key]);
      displayStatus(t('options.status.pinRemoved'));
      await renderPinnedBehaviorTable();
    }
  });

  bindClusterEvents();
  bindSignInEvents();
  bindAuditEvents();
  bindWebLabEvents();
}

async function init() {
  info = await announce();
  t = await loadTranslator(info.locale);
  config = info.config || {};

  applyTranslations(document, t, { html: true });
  document.getElementById('change-connection-label').textContent = tOr(t, 'options.dashboard.changeConnection', 'Change connection');

  const howToList = document.getElementById('how-to-list');
  if (howToList) {
    const items = t('options.dashboard.howToList');
    howToList.innerHTML = Array.isArray(items) ? items.map((item) => `<li>${item}</li>`).join('') : '';
  }

  if (info.shell !== 'extension') {
    const header = document.querySelector('.sidebar-header');
    // The web app's rail is always there to leave by.
    if (header && info.shell === 'mobile') addMobileBackButton(header, () => bridge.openPage('popup'));
    document.getElementById('how-to-card').style.display = 'none';
  }
  if (info.shell === 'mobile') applyMobileLayout();
  if (info.shell === 'web') applyWebLayout();

  ['newUser', 'editUser'].forEach((prefix) => buildSettingsForm(prefix, 'user'));
  ['newGroup', 'editGroup'].forEach((prefix) => buildSettingsForm(prefix, 'group'));
  bindEvents();

  if (!config.serverIp || !config.username) {
    displayStatus(t('options.status.loginFailed', { error: 'not configured' }), true);
    bridge.openPage('connect');
    return;
  }

  await openTab('Config');
  const loaded = await loadDashboard();
  // A page that sends an administrator here may name the section to show.
  const section = new URLSearchParams(location.search).get('section');
  if (loaded && isAdmin && section && document.querySelector(`.nav-link[data-tabname="${CSS.escape(section)}"]`)) await openTab(section);
}

init();
