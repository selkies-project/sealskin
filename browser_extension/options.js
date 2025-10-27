let t;
function applyTranslations(scope, translator) {
  scope.querySelectorAll('[data-i18n]').forEach(el => {
    const key = el.getAttribute('data-i18n');
    el.innerHTML = translator(key);
  });
  scope.querySelectorAll('[data-i18n-placeholder]').forEach(el => {
    const key = el.getAttribute('data-i18n-placeholder');
    el.placeholder = translator(key);
  });
  scope.querySelectorAll('[data-i18n-title]').forEach(el => {
    const key = el.getAttribute('data-i18n-title');
    el.title = translator(key);
  });
}


async function secureFetch(url, options = {}) {
  if (url.startsWith('/api/admin') || url.startsWith('/api/homedirs') || url.startsWith('/api/sessions')) {
    const jwt = await generateJwtNative(clientPrivateKeyInput.value.trim(), usernameInput.value.trim());
    options.headers = {
      ...options.headers,
      'Authorization': `Bearer ${jwt}`
    };
  }

  return new Promise((resolve, reject) => {
    chrome.runtime.sendMessage({
        type: 'secureFetch',
        payload: {
          url,
          options
        }
      },
      (response) => {
        if (chrome.runtime.lastError) {
          reject(new Error(chrome.runtime.lastError.message));
        } else if (response.success) {
          if ((options.method === 'DELETE' || (options.method === 'POST' && response.data === null)) && response.data === null) {
            resolve({});
          } else {
            resolve(response.data);
          }
        } else {
          reject(new Error(response.error));
        }
      }
    );
  });
}

const simpleConfigView = document.getElementById('simple-config-view');
const advancedConfigView = document.getElementById('advanced-config-view');
const dashboardView = document.getElementById('dashboard-view');
const showAdvancedLink = document.getElementById('show-advanced-link');
const hideAdvancedLink = document.getElementById('hide-advanced-link');
const configFileUpload = document.getElementById('configFileUpload');
const configTextArea = document.getElementById('configTextArea');
const applyConfigBtn = document.getElementById('applyConfig');
const serverIpInput = document.getElementById('serverIp');
const apiPortInput = document.getElementById('apiPort');
const sessionPortInput = document.getElementById('sessionPort');
const usernameInput = document.getElementById('username');
const clientPrivateKeyInput = document.getElementById('clientPrivateKey');
const serverPublicKeyInput = document.getElementById('serverPublicKey');
const searchEngineSelect = document.getElementById('searchEngine');
const saveButton = document.getElementById('save');
const loginButton = document.getElementById('login');
const logoutButton = document.getElementById('logout-button');
const dashboardUsername = document.getElementById('dashboard-username');
const dashboardRole = document.getElementById('dashboard-role');
const dashboardServerIp = document.getElementById('dashboard-server-ip');
const dashboardApiPort = document.getElementById('dashboard-api-port');
const dashboardCpuModel = document.getElementById('dashboard-cpu-model');
const dashboardDiskInfo = document.getElementById('dashboard-disk-info');
const dashboardDiskUsageText = document.getElementById('dashboard-disk-usage-text');
const dashboardDiskUsageBar = document.getElementById('dashboard-disk-usage-bar');
const exportConfigButton = document.getElementById('export-config-button');
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
const availableAppsContainer = document.getElementById('available-apps-container');
const installedAppsTbody = document.querySelector('#installed-apps-table tbody');
const imageUpdateModal = document.getElementById('image-update-modal');
const imageUpdateModalTitle = document.getElementById('image-update-modal-title');
const imageUpdateModalBody = document.getElementById('image-update-modal-body');
const imageUpdateModalFooter = document.getElementById('image-update-modal-footer');
let APP_TEMPLATE_SETTINGS;
let appTemplateTabInitialized = false;

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
  gpus: []
};
const ITEMS_PER_PAGE = 5;
let tableStates = {
  users: {
    currentPage: 1,
    searchTerm: ''
  },
  groups: {
    currentPage: 1,
    searchTerm: ''
  },
  admins: {
    currentPage: 1,
    searchTerm: ''
  },
  installedApps: {
    currentPage: 1,
    searchTerm: ''
  },
  availableApps: {
    currentPage: 1,
    searchTerm: ''
  },
};
let currentAdminManagedUser = null;
let currentAppForUpdateCheck = null;
let installedAppsPollingInterval = null;


function displayStatus(message, isError = false) {
  document.querySelectorAll('.status-toast').forEach(t => t.remove());

  const toast = document.createElement('div');
  toast.className = `status-toast ${isError ? 'error' : 'success'}`;
  toast.innerHTML = `<i class="fas ${isError ? 'fa-exclamation-circle' : 'fa-check-circle'}"></i> ${message}`;

  document.body.appendChild(toast);

  setTimeout(() => toast.classList.add('visible'), 10);

  setTimeout(() => {
    toast.classList.remove('visible');
    toast.addEventListener('transitionend', () => toast.remove());
  }, isError ? 6000 : 3000);
}

function timeAgo(timestamp) {
  const seconds = Math.floor((new Date() - new Date(timestamp * 1000)) / 1000);

  if (seconds < 60) {
    return t('common.justNow');
  }

  const rtf = new Intl.RelativeTimeFormat(navigator.language, {
    style: 'long',
    numeric: 'auto'
  });

  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) {
    return rtf.format(-minutes, 'minute');
  }

  const hours = Math.floor(minutes / 60);
  if (hours < 24) {
    return rtf.format(-hours, 'hour');
  }

  const days = Math.floor(hours / 24);
  return rtf.format(-days, 'day');
}

function formatBytes(bytes, decimals = 2) {
  if (!bytes || bytes === 0) return `0 ${t('common.bytes')}`;
  const k = 1024;
  const dm = decimals < 0 ? 0 : decimals;
  const sizes = [t('common.bytes'), t('common.kb'), t('common.mb'), t('common.gb'), t('common.tb'), t('common.pb')];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return parseFloat((bytes / Math.pow(k, i)).toFixed(dm)) + ' ' + sizes[i];
}

function buildTemplateForm() {
  const uiContainer = document.getElementById('template-form-ui');
  const appContainer = document.getElementById('template-form-app');
  const hardeningContainer = document.getElementById('template-form-hardening');
  const generalContainer = document.getElementById('template-form-general');

  [uiContainer, appContainer, hardeningContainer, generalContainer].forEach(c => c.innerHTML = '');

  APP_TEMPLATE_SETTINGS.forEach(setting => {
    let formElementHtml = '';
    const inputId = `template-form-${setting.name}`;

    switch (setting.type) {
      case 'boolean':
        const isChecked = setting.default === 'true';
        formElementHtml = `
                    <div class="form-group">
                        <label for="${inputId}" style="flex-direction: row; align-items: center;">
                            <input type="checkbox" id="${inputId}" data-name="${setting.name}" ${isChecked ? 'checked' : ''}>
                            ${setting.label}
                        </label>
                        <p class="description">${setting.description}</p>
                    </div>`;
        break;
      case 'text':
        formElementHtml = `
                    <div class="form-group">
                        <label for="${inputId}">${setting.label}</label>
                        <input type="text" id="${inputId}" data-name="${setting.name}" value="${setting.default}" placeholder="${setting.default}">
                        <p class="description">${setting.description}</p>
                    </div>`;
        break;
      case 'select':
        const optionsHtml = Object.entries(setting.options).map(([value, text]) =>
          `<option value="${value}" ${value === setting.default ? 'selected' : ''}>${text}</option>`
        ).join('');
        formElementHtml = `
                    <div class="form-group">
                        <label for="${inputId}">${setting.label}</label>
                        <select id="${inputId}" data-name="${setting.name}">${optionsHtml}</select>
                        <p class="description">${setting.description}</p>
                    </div>`;
        break;
    }

    if (setting.category === 'ui') uiContainer.innerHTML += formElementHtml;
    else if (setting.category === 'app') appContainer.innerHTML += formElementHtml;
    else if (setting.category === 'hardening') hardeningContainer.innerHTML += formElementHtml;
    else if (setting.category === 'general') generalContainer.innerHTML += formElementHtml;
  });
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
  sidebarEl.style.width = showSidebar ? '30%' : '0';
  sidebarEl.style.padding = showSidebar ? '1rem' : '0';
  sidebarEl.style.borderRight = showSidebar ? '1px solid var(--border-color)' : 'none';


  document.getElementById('preview-title').textContent = getVal('SELKIES_UI_TITLE');
  document.getElementById('preview-logo').style.display = getVal('SELKIES_UI_SHOW_LOGO', true) ? 'block' : 'none';
  document.getElementById('preview-core-buttons').style.display = getVal('SELKIES_UI_SHOW_CORE_BUTTONS', true) ? 'block' : 'none';
  document.getElementById('preview-soft-buttons').style.display = getVal('SELKIES_UI_SIDEBAR_SHOW_SOFT_BUTTONS', true) ? 'block' : 'none';
  document.getElementById('preview-video-settings').style.display = getVal('SELKIES_UI_SIDEBAR_SHOW_VIDEO_SETTINGS', true) ? 'block' : 'none';
  document.getElementById('preview-screen-settings').style.display = getVal('SELKIES_UI_SIDEBAR_SHOW_SCREEN_SETTINGS', true) ? 'block' : 'none';
  document.getElementById('preview-audio-settings').style.display = getVal('SELKIES_UI_SIDEBAR_SHOW_AUDIO_SETTINGS', true) ? 'block' : 'none';
  document.getElementById('preview-stats').style.display = getVal('SELKIES_UI_SIDEBAR_SHOW_STATS', true) ? 'block' : 'none';
  document.getElementById('preview-clipboard').style.display = getVal('SELKIES_UI_SIDEBAR_SHOW_CLIPBOARD', true) ? 'block' : 'none';
  document.getElementById('preview-files').style.display = getVal('SELKIES_UI_SIDEBAR_SHOW_FILES', true) ? 'block' : 'none';
  document.getElementById('preview-apps').style.display = getVal('SELKIES_UI_SIDEBAR_SHOW_APPS', true) ? 'block' : 'none';
  document.getElementById('preview-sharing').style.display = getVal('SELKIES_UI_SIDEBAR_SHOW_SHARING', true) ? 'block' : 'none';
  document.getElementById('preview-gamepads').style.display = getVal('SELKIES_UI_SIDEBAR_SHOW_GAMEPADS', true) ? 'block' : 'none';

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
  APP_TEMPLATE_SETTINGS.forEach(setting => {
    const el = document.getElementById(`template-form-${setting.name}`);
    if (el) {
      let value;
      if (setting.type === 'boolean') {
        value = el.checked ? 'true' : 'false';
      } else {
        value = el.value;
      }
      if (value !== setting.default) {
        settingsBlob[setting.name] = value;
      }
    }
  });

  const payload = {
    name: templateName,
    settings: settingsBlob
  };

  try {
    await secureFetch('/api/admin/apps/templates', {
      method: 'POST',
      body: JSON.stringify(payload)
    });
    displayStatus(t('options.status.templateSaved', {
      name: payload.name
    }), false);
    await refreshAppData();
    populateTemplateDropdowns();
    templateSelect.value = payload.name;
    nameInput.value = '';
    templateSelect.dispatchEvent(new Event('change'));
  } catch (error) {
    displayStatus(t('options.status.templateSaveFailed', {
      error: error.message
    }), true);
  }
}

async function deleteTemplateProfile() {
  const templateSelect = document.getElementById('template-select');
  const templateName = templateSelect.value;

  if (!templateName || templateName === 'new' || templateName === 'Default') {
    displayStatus(t('options.appTemplates.deleteDisabled'), true);
    return;
  }

  if (!confirm(t('options.appTemplates.confirmDelete', {
      templateName
    }))) {
    return;
  }

  try {
    await secureFetch(`/api/admin/apps/templates/${encodeURIComponent(templateName)}`, {
      method: 'DELETE'
    });
    displayStatus(t('options.status.templateDeleted', {
      templateName
    }), false);
    await refreshAppData();
    populateTemplateDropdowns();
    templateSelect.value = 'new';
    templateSelect.dispatchEvent(new Event('change'));
  } catch (error) {
    displayStatus(t('options.status.templateDeleteFailed', {
      error: error.message
    }), true);
  }
}

function loadTemplateIntoForm(templateName) {
  const template = adminData.appTemplates.find(t => t.name === templateName);
  const settings = template ? template.settings : {};

  APP_TEMPLATE_SETTINGS.forEach(settingDef => {
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
}

function populateTemplateDropdowns() {
  const templateSelect = document.getElementById('template-select');
  const currentVal = templateSelect.value;

  templateSelect.innerHTML = `<option value="new">${t('options.appTemplates.createOption')}</option>`;
  adminData.appTemplates.forEach(template => {
    const option = document.createElement('option');
    option.value = template.name;
    option.textContent = template.name;
    templateSelect.appendChild(option);
  });

  if ([...templateSelect.options].some(o => o.value === currentVal)) {
    templateSelect.value = currentVal;
  } else {
    templateSelect.value = 'new';
  }
}

function initializeAppTemplatesTab() {
  if (appTemplateTabInitialized) return;

  buildTemplateForm();
  updateTemplatePreview();
  populateTemplateDropdowns();

  document.getElementById('app-template-form').addEventListener('input', updateTemplatePreview);
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
      if (isDefault) {
        deleteBtn.style.display = 'none';
      } else {
        deleteBtn.style.display = 'inline-flex';
      }
    } else {
      loadTemplateIntoForm(null);
      deleteBtn.style.display = 'none';
    }
  });

  appTemplateTabInitialized = true;
}

const tableRenderConfig = {
  admins: {
    tbody: document.querySelector('#admins-table tbody'),
    filter: (item, term) => item.username.toLowerCase().includes(term),
    row: item => {
      const shortKey = item.public_key.replace(/-----(BEGIN|END) PUBLIC KEY-----/g, '').replace(/\s/g, '');
      return `
            <tr>
                <td>${item.username}</td>
                <td class="pubkey-cell" title="${item.public_key}">
                    <div class="cell-wrapper">
                        <span class="key-text">${shortKey}</span>
                        <button class="secondary copy-btn" data-pubkey="${item.public_key}"><i class="fas fa-copy"></i></button>
                    </div>
                </td>
                <td class="actions-cell">
                    <div class="cell-wrapper">
                        <button class="secondary" data-adminname="${item.username}">${t('common.homes')}</button>
                        ${item.username !== 'admin' ? `<button class="danger" data-adminname="${item.username}">${t('common.delete')}</button>` : ''}
                    </div>
                </td>
            </tr>`;
    }
  },
  users: {
    tbody: document.querySelector('#users-table tbody'),
    filter: (item, term) => item.username.toLowerCase().includes(term) || (item.settings.group || '').toLowerCase().includes(term),
    row: item => {
      const effectiveSettings = calculateEffectiveSettings(item);
      const homesDisabled = !effectiveSettings.persistent_storage;
      const shortKey = item.public_key.replace(/-----(BEGIN|END) PUBLIC KEY-----/g, '').replace(/\s/g, '');
      return `
                <tr>
                    <td>${item.username}</td>
                    <td>${item.settings.group || t('common.none')}</td>
                    <td class="pubkey-cell" title="${item.public_key}">
                        <div class="cell-wrapper">
                            <span class="key-text">${shortKey}</span>
                            <button class="secondary copy-btn" data-pubkey="${item.public_key}"><i class="fas fa-copy"></i></button>
                        </div>
                    </td>
                    <td class="actions-cell">
                        <div class="cell-wrapper">
                            <button class="secondary" data-username="${item.username}" ${homesDisabled ? `disabled title="${t('options.users.homesDisabledTooltip')}"` : ''}>${t('common.homes')}</button>
                            <button class="warning" data-username="${item.username}">${t('common.edit')}</button>
                            <button class="danger" data-username="${item.username}">${t('common.delete')}</button>
                        </div>
                    </td>
                </tr>`;
    }
  },
  groups: {
    tbody: document.querySelector('#groups-table tbody'),
    filter: (item, term) => item.name.toLowerCase().includes(term),
    row: item => `
                <tr>
                    <td>${item.name}</td>
                    <td class="actions-cell">
                        <button class="warning" data-groupname="${item.name}">${t('common.edit')}</button>
                        <button class="danger" data-groupname="${item.name}">${t('common.delete')}</button>
                    </td>
                </tr>`
  },
  installedApps: {
    tbody: installedAppsTbody,
    filter: (item, term) => item.name.toLowerCase().includes(term) || item.provider_config.image.toLowerCase().includes(term),
    row: item => {
      const sha = item.image_sha ? item.image_sha.substring(0, 12) : t('common.none');
      let versionInfoHtml = '';

      if (item.pull_status === 'pulling') {
        versionInfoHtml = `
                    <div class="spinner-small"></div>
                    <small>${t('options.installedApps.pulling')}</small>
                `;
      } else if (item.auto_update) {
        const checkedAt = item.last_checked_at ? `${t('common.checked')}: ${timeAgo(item.last_checked_at)}` : `${t('common.checked')}: ${t('common.never')}`;
        versionInfoHtml = `
                    <span>${sha}</span>
                    <small>${checkedAt}</small>
                `;
      } else {
        versionInfoHtml = `
                    <div class="sha-and-button">
                        <span>${sha}</span>
                        <button class="secondary check-update-btn" data-appid="${item.id}" ${!item.image_sha ? `disabled title="${t('options.installedApps.notLocal')}"` : ''}>Check</button>
                    </div>
                    <small>&nbsp;</small>
                `;
      }

      return `
            <tr>
                <td>${item.name}</td>
                <td>${item.source}</td>
                <td class="image-version-cell" title="${item.provider_config.image}">
                    ${versionInfoHtml}
                </td>
                <td class="actions-cell">
                    <button class="warning" data-appid="${item.id}">${t('common.edit')}</button>
                    <button class="danger" data-appid="${item.id}">${t('common.delete')}</button>
                </td>
            </tr>`;
    }
  },
};

function renderTable(dataType) {
  const state = tableStates[dataType];
  const config = tableRenderConfig[dataType];
  const paginationEl = document.getElementById(`${dataType}-pagination`);

  const searchTerm = state.searchTerm.toLowerCase();
  const sourceData = (dataType === 'installedApps' ? adminData.installedApps : adminData[dataType]) || [];

  const filteredData = searchTerm ?
    sourceData.filter(item => config.filter(item, searchTerm)) :
    sourceData;

  const itemsPerPage = dataType === 'installedApps' ? 10 : 5;
  const totalPages = Math.max(1, Math.ceil(filteredData.length / itemsPerPage));
  state.currentPage = Math.min(state.currentPage, totalPages);

  const startIndex = (state.currentPage - 1) * itemsPerPage;
  const endIndex = startIndex + itemsPerPage;
  const paginatedData = filteredData.slice(startIndex, endIndex);

  if (paginatedData.length > 0) {
    config.tbody.innerHTML = paginatedData.map(config.row).join('');
  } else {
    const colspan = config.tbody.closest('table').querySelector('thead th').length;
    const placeholderKey = `options.placeholders.no${dataType.charAt(0).toUpperCase() + dataType.slice(1)}`;
    config.tbody.innerHTML = `<tr class="empty-row"><td colspan="${colspan}" style="text-align:center; padding: 2rem;">${t(placeholderKey)}</td></tr>`;
  }

  paginationEl.innerHTML = `
        <button class="secondary" data-page="prev" ${state.currentPage === 1 ? 'disabled' : ''}>&laquo; ${t('common.previous')}</button>
        <span class="page-info">${t('common.page')} ${state.currentPage} ${t('common.of')} ${totalPages}</span>
        <button class="secondary" data-page="next" ${state.currentPage === totalPages ? 'disabled' : ''}>${t('common.next')} &raquo;</button>
    `;
}

function getSettingsFromForm(formPrefix) {
  return {
    active: document.getElementById(`${formPrefix}Active`).checked,
    group: document.getElementById(`${formPrefix}Group`)?.value || 'none',
    persistent_storage: document.getElementById(`${formPrefix}PersistentStorage`).checked,
    harden_container: document.getElementById(`${formPrefix}HardenContainer`).checked,
    harden_openbox: document.getElementById(`${formPrefix}HardenOpenbox`).checked,
    gpu: document.getElementById(`${formPrefix}Gpu`).checked,
    storage_limit: -1,
    session_limit: parseInt(document.getElementById(`${formPrefix}SessionLimit`).value, 10),
  };
}

function populateSettingsForm(formPrefix, settings) {
  document.getElementById(`${formPrefix}Active`).checked = settings.active;
  if (document.getElementById(`${formPrefix}Group`)) {
    document.getElementById(`${formPrefix}Group`).value = settings.group;
  }
  document.getElementById(`${formPrefix}PersistentStorage`).checked = settings.persistent_storage;
  document.getElementById(`${formPrefix}HardenContainer`).checked = settings.harden_container;
  document.getElementById(`${formPrefix}HardenOpenbox`).checked = settings.harden_openbox;
  document.getElementById(`${formPrefix}Gpu`).checked = settings.gpu;
  document.getElementById(`${formPrefix}SessionLimit`).value = settings.session_limit;
}

function calculateEffectiveSettings(user) {
  if (!user || !user.settings) return {};
  const baseSettings = {
    ...user.settings
  };
  const group = adminData.groups.find(g => g.name === baseSettings.group);
  if (group && group.settings) {
    const effective = {
      ...baseSettings,
      ...group.settings
    };
    return effective;
  }
  return baseSettings;
}

function populateGroupDropdowns() {
  const groupSelects = document.querySelectorAll('#newUserGroup, #editUserGroup');
  groupSelects.forEach(select => {
    const currentVal = select.value;
    select.innerHTML = `<option value="none">${t('common.none')}</option>`;
    adminData.groups.forEach(group => {
      select.insertAdjacentHTML('beforeend', `<option value="${group.name}">${group.name}</option>`);
    });
    select.value = currentVal;
  });
}

function saveConfig() {
  const config = {
    serverIp: serverIpInput.value.trim(),
    apiPort: apiPortInput.value.trim(),
    sessionPort: sessionPortInput.value.trim(),
    username: usernameInput.value.trim(),
    clientPrivateKey: clientPrivateKeyInput.value.trim(),
    serverPublicKey: serverPublicKeyInput.value.trim(),
    searchEngineUrl: searchEngineSelect.value,
  };
  chrome.storage.local.set({
    sealskinConfig: config
  }, () => {
    displayStatus(t('options.status.configSaved'), false);
  });
}

async function loadConfig() {
  const {
    sealskinConfig
  } = await chrome.storage.local.get('sealskinConfig');
  if (sealskinConfig) {
    serverIpInput.value = sealskinConfig.serverIp || '';
    apiPortInput.value = sealskinConfig.apiPort || '8000';
    sessionPortInput.value = sealskinConfig.sessionPort || '8443';
    usernameInput.value = sealskinConfig.username || '';
    clientPrivateKeyInput.value = sealskinConfig.clientPrivateKey || '';
    serverPublicKeyInput.value = sealskinConfig.serverPublicKey || '';
    searchEngineSelect.value = sealskinConfig.searchEngineUrl || 'https://google.com/search?q=';
  }
}

function parseAndApplyConfig(configText) {
  try {
    const config = JSON.parse(configText);
    const requiredKeys = ['server_endpoint', 'api_port', 'session_port', 'username', 'private_key', 'server_public_key'];
    for (const key of requiredKeys) {
      if (!(key in config)) {
        throw new Error(`Missing required key in configuration: ${key}`);
      }
    }

    serverIpInput.value = config.server_endpoint;
    apiPortInput.value = config.api_port;
    sessionPortInput.value = config.session_port;
    usernameInput.value = config.username;
    clientPrivateKeyInput.value = config.private_key;
    serverPublicKeyInput.value = config.server_public_key;

    saveConfig();
    displayStatus(t('options.status.configApplied'), false);

    simpleConfigView.style.display = 'none';
    advancedConfigView.style.display = 'block';

    configFileUpload.value = '';
    configTextArea.value = '';

  } catch (error) {
    displayStatus(t('options.status.configApplyFailed', {
      error: error.message
    }), true);
  }
}

function showUserConfigModal(user, privateKey, isNewUser = false) {
  const config = {
    server_endpoint: serverIpInput.value.trim(),
    api_port: adminData.api_port || apiPortInput.value.trim(),
    session_port: adminData.session_port || sessionPortInput.value.trim(),
    username: user.username,
    private_key: privateKey,
    server_public_key: adminData.server_public_key || serverPublicKeyInput.value.trim()
  };

  const configJson = JSON.stringify(config, null, 2);
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
    gpuList.innerHTML = gpus.map(gpu => `<li>${gpu.device.split('/').pop()} (${gpu.driver})</li>`).join('');
    gpuInfoContainer.style.display = 'block';
  } else {
    gpuInfoContainer.style.display = 'none';
  }
}

async function refreshAdminData() {
  try {
    const data = await secureFetch('/api/admin/data', {
      method: 'POST',
      body: JSON.stringify({})
    });
    adminData = {
      ...adminData,
      ...data
    };
    serverPublicKeyDisplay.value = data.server_public_key;
    renderTable('admins');
    renderTable('users');
    renderTable('groups');
    populateGroupDropdowns();
    renderGpuInfo(adminData.gpus);
  } catch (error) {
    displayStatus(t('options.status.adminDataRefreshFailed', {
      error: error.message
    }), true);
  }
}

function setAdminNavVisibility(visible) {
  const display = visible ? 'flex' : 'none';
  adminNavLinks.forEach(link => link.style.display = display);
  adminNavSeparator.style.display = visible ? 'block' : 'none';
}

async function handleLogin() {
  displayStatus(t('options.status.loggingIn'));
  try {
    const statusData = await secureFetch('/api/admin/status', {
      method: 'POST',
      body: JSON.stringify({})
    });

    simpleConfigView.style.display = 'none';
    advancedConfigView.style.display = 'none';
    dashboardView.style.display = 'block';

    dashboardUsername.textContent = statusData.username;
    dashboardRole.textContent = statusData.is_admin ? t('options.dashboard.roleAdmin') : t('options.dashboard.roleUser');
    dashboardServerIp.textContent = serverIpInput.value.trim();
    dashboardApiPort.textContent = apiPortInput.value.trim();

    dashboardCpuModel.textContent = statusData.cpu_model || t('common.na');
    if (statusData.disk_total && statusData.disk_used) {
      const usageText = `${formatBytes(statusData.disk_used)} / ${formatBytes(statusData.disk_total)}`;
      dashboardDiskUsageText.textContent = usageText;
      const percentUsed = (statusData.disk_used / statusData.disk_total) * 100;
      dashboardDiskUsageBar.value = percentUsed;
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

    if (statusData.is_admin) {
      displayStatus(t('options.status.loggedInAdmin', {
        username: statusData.username
      }), false);
      setAdminNavVisibility(true);
      homeDirTabButton.style.display = 'flex';
      await refreshAdminData();
    } else {
      displayStatus(t('options.status.loggedInUser', {
        username: statusData.username
      }), false);
      setAdminNavVisibility(false);
      if (statusData.settings.persistent_storage) {
        homeDirTabButton.style.display = 'flex';
      } else {
        homeDirTabButton.style.display = 'none';
      }
    }
    return true;
  } catch (error) {
    console.error('Login failed:', error);
    displayStatus(t('options.status.loginFailed', {
      error: error.message
    }), true);
    setAdminNavVisibility(false);
    homeDirTabButton.style.display = 'none';
    sessionsTabButton.style.display = 'none';

    dashboardView.style.display = 'none';
    simpleConfigView.style.display = 'none';
    advancedConfigView.style.display = 'block';

    return false;
  }
}

async function refreshHomeDirs() {
  try {
    const data = await secureFetch('/api/homedirs', {
      method: 'GET'
    });
    renderHomeDirsTable(data.home_dirs);
  } catch (error) {
    displayStatus(t('options.status.homedirLoadFailed', {
      error: error.message
    }), true);
    homeDirsTbody.innerHTML = `<tr><td colspan="2" class="empty-row" style="text-align:center;">${t('options.placeholders.errorLoading')}</td></tr>`;
  }
}

function renderHomeDirsTable(dirs) {
  if (dirs && dirs.length > 0) {
    homeDirsTbody.innerHTML = dirs.map(dir => `
            <tr>
                <td>${dir}</td>
                <td class="actions-cell">
                    <button class="danger" data-homedir-name="${dir}">${t('common.delete')}</button>
                </td>
            </tr>
        `).join('');
  } else {
    homeDirsTbody.innerHTML = `<tr class="empty-row"><td colspan="2" style="text-align:center; padding: 2rem;">${t('options.placeholders.noHomeDirs')}</td></tr>`;
  }
}

async function refreshAdminUserHomeDirs(username, isAdmin = false) {
  currentAdminManagedUser = {
    username,
    isAdmin
  };
  const userType = isAdmin ? t('common.admin').toLowerCase() : t('common.user').toLowerCase();
  document.getElementById('homedir-list-title').textContent = isAdmin ? t('options.modals.dirsForAdmin', {
    username
  }) : t('options.modals.dirsForUser', {
    username
  });
  try {
    const path = isAdmin ? 'admins' : 'users';
    const data = await secureFetch(`/api/admin/${path}/${username}/homedirs`, {
      method: 'GET'
    });
    renderAdminUserHomeDirsTable(data.home_dirs);
  } catch (error) {
    displayStatus(t('options.status.homedirLoadFailed', {
      error: error.message
    }), true);
    userHomeDirsTbody.innerHTML = `<tr><td colspan="2" class="empty-row" style="text-align:center;">${t('options.placeholders.errorLoading')}</td></tr>`;
  }
}

function renderAdminUserHomeDirsTable(dirs) {
  if (dirs && dirs.length > 0) {
    userHomeDirsTbody.innerHTML = dirs.map(dir => `
            <tr>
                <td>${dir}</td>
                <td class="actions-cell">
                    <button class="danger" data-homedir-name="${dir}">${t('common.delete')}</button>
                </td>
            </tr>
        `).join('');
  } else {
    userHomeDirsTbody.innerHTML = `<tr class="empty-row"><td colspan="2" style="text-align:center; padding: 2rem;">${t('options.placeholders.noHomeDirs')}</td></tr>`;
  }
}

async function refreshSessions() {
  const isAdmin = dashboardRole.textContent === t('options.dashboard.roleAdmin');
  const endpoint = isAdmin ? '/api/admin/sessions' : '/api/sessions';

  try {
    const data = await secureFetch(endpoint, {
      method: 'GET'
    });
    if (isAdmin) {
      renderAdminSessions(data);
    } else {
      renderUserSessions(data);
    }
  } catch (error) {
    displayStatus(t('options.status.sessionsLoadFailed', {
      error: error.message
    }), true);
    sessionsContainer.innerHTML = `<p style="text-align: center; color: var(--text-muted);">${t('options.placeholders.errorLoading')}</p>`;
  }
}

function renderUserSessions(sessions) {
  if (!sessions || sessions.length === 0) {
    sessionsContainer.innerHTML = `<p style="text-align: center; color: var(--text-muted); padding: 2rem;">${t('options.sessions.noSessionsUser')}</p>`;
    return;
  }

  sessionsContainer.innerHTML = `
        <div class="table-container">
            <table id="user-sessions-table">
                <thead>
                    <tr>
                        <th>${t('options.sessions.application')}</th>
                        <th>${t('options.sessions.started')}</th>
                        <th class="actions-cell">${t('common.actions')}</th>
                    </tr>
                </thead>
                <tbody>
                    ${sessions.map(s => `
                        <tr>
                            <td>
                                <div style="display: flex; align-items: center; gap: 1rem;">
                                    <img src="${s.app_logo}" alt="${s.app_name}" style="width: 32px; height: 32px; object-fit: contain;">
                                    <span>${s.app_name}</span>
                                </div>
                            </td>
                            <td>${timeAgo(s.created_at)}</td>
                            <td class="actions-cell">
                                <button class="danger stop-session-btn" data-session-id="${s.session_id}">${t('common.stop')}</button>
                            </td>
                        </tr>
                    `).join('')}
                </tbody>
            </table>
        </div>
    `;
}

function renderAdminSessions(usersWithSessions) {
  if (!usersWithSessions || usersWithSessions.length === 0) {
    sessionsContainer.innerHTML = `<p style="text-align: center; color: var(--text-muted); padding: 2rem;">${t('options.sessions.noSessionsAdmin')}</p>`;
    return;
  }

  sessionsContainer.innerHTML = usersWithSessions.map(userData => `
        <details class="collapsible-section" open>
            <summary><h4>${t('options.sessions.sessionsFor', { username: userData.username, count: userData.sessions.length })}</h4></summary>
            <div>
                <div class="table-container">
                    <table class="admin-sessions-table">
                        <thead>
                            <tr>
                                <th>${t('options.sessions.application')}</th>
                                <th>${t('options.sessions.started')}</th>
                                <th class="actions-cell">${t('common.actions')}</th>
                            </tr>
                        </thead>
                        <tbody>
                            ${userData.sessions.map(s => `
                                <tr>
                                    <td>
                                        <div style="display: flex; align-items: center; gap: 1rem;">
                                            <img src="${s.app_logo}" alt="${s.app_name}" style="width: 32px; height: 32px; object-fit: contain;">
                                            <span>${s.app_name}</span>
                                        </div>
                                    </td>
                                    <td>${timeAgo(s.created_at)}</td>
                                    <td class="actions-cell">
                                        <button class="danger stop-session-btn" data-session-id="${s.session_id}">${t('common.stop')}</button>
                                    </td>
                                </tr>
                            `).join('')}
                        </tbody>
                    </table>
                </div>
            </div>
        </details>
    `).join('');
}

async function refreshAppData() {
  try {
    const [stores, installed, templates] = await Promise.all([
      secureFetch('/api/admin/apps/stores', {
        method: 'GET'
      }),
      secureFetch('/api/admin/apps/installed', {
        method: 'GET'
      }),
      secureFetch('/api/admin/apps/templates', {
        method: 'GET'
      })
    ]);
    adminData.appStores = stores;
    adminData.installedApps = installed;
    adminData.appTemplates = templates;

    renderAppStoreSelect();
    renderTable('installedApps');

    const selectedStoreUrl = appStoreSelect.value;
    if (selectedStoreUrl) {
      await fetchAndRenderAvailableApps(selectedStoreUrl);
    }
  } catch (error) {
    displayStatus(t('options.status.appDataRefreshFailed', {
      error: error.message
    }), true);
  }
}

async function refreshInstalledApps() {
  try {
    const installed = await secureFetch('/api/admin/apps/installed', {
      method: 'GET'
    });
    adminData.installedApps = installed;
    renderTable('installedApps');

    const isStillPulling = installed.some(app => app.pull_status === 'pulling');
    if (!isStillPulling && installedAppsPollingInterval) {
      clearInterval(installedAppsPollingInterval);
      installedAppsPollingInterval = null;
    }
  } catch (error) {
    console.error("Failed to poll installed apps:", error);
    if (installedAppsPollingInterval) {
      clearInterval(installedAppsPollingInterval);
      installedAppsPollingInterval = null;
    }
  }
}

function renderAppStoreSelect() {
  const currentVal = appStoreSelect.value;
  appStoreSelect.innerHTML = '';
  adminData.appStores.forEach(store => {
    appStoreSelect.add(new Option(store.name, store.url));
  });
  if ([...appStoreSelect.options].some(o => o.value === currentVal)) {
    appStoreSelect.value = currentVal;
  }
}

async function fetchAndRenderAvailableApps(storeUrl) {
  try {
    displayStatus(t('options.status.fetchingApps'));
    const apps = await secureFetch(`/api/admin/apps/available?url=${encodeURIComponent(storeUrl)}`, {
      method: 'GET'
    });
    adminData.availableApps = apps;
    document.getElementById('available-apps-title').textContent = t('options.appStore.availableFrom', {
      storeName: appStoreSelect.options[appStoreSelect.selectedIndex].text
    });
    renderAvailableAppsGrid();
  } catch (error) {
    displayStatus(t('options.status.fetchAppsFailed', {
      error: error.message
    }), true);
    availableAppsContainer.innerHTML = `<p style="text-align: center; color: var(--text-muted);">${t('options.appStore.couldNotLoad')}</p>`;
  }
}

function renderAvailableAppsGrid() {
  const paginationEl = document.getElementById('available-apps-pagination');
  const searchTerm = tableStates.availableApps.searchTerm.toLowerCase();

  const filteredData = searchTerm ?
    adminData.availableApps.filter(app => app.name.toLowerCase().includes(searchTerm) || app.id.toLowerCase().includes(searchTerm)) :
    adminData.availableApps;

  if (filteredData.length > 0) {
    availableAppsContainer.innerHTML = filteredData.map(app => `
            <div class="app-card" data-appid="${app.id}" title="${t('options.modals.installAppTitle', { appName: app.name })}">
                <img src="${app.logo}" alt="${app.name} logo" class="app-card-logo">
                <div class="app-card-name">${app.name}</div>
            </div>
        `).join('');
  } else {
    availableAppsContainer.innerHTML = `<p style="text-align: center; color: var(--text-muted); grid-column: 1 / -1;">${t('options.appStore.noAppsFound')}</p>`;
  }

  paginationEl.innerHTML = '';
}

function showInstallModal(appData, existingInstall = null) {
  const isEditing = !!existingInstall;
  document.getElementById('app-install-modal-title').textContent = isEditing ? t('options.modals.editAppTitle', {
    appName: existingInstall.name
  }) : t('options.modals.installAppTitle', {
    appName: appData.name
  });

  document.getElementById('install-app-id').value = isEditing ? existingInstall.id : '';
  document.getElementById('install-source-app-id').value = isEditing ? existingInstall.source_app_id : appData.id;
  document.getElementById('install-source-name').value = appStoreSelect.options[appStoreSelect.selectedIndex].text;
  document.getElementById('install-app-name').value = isEditing ? existingInstall.name : appData.name;
  document.getElementById('install-app-image').value = isEditing ? existingInstall.provider_config.image : appData.provider_config.image;

  document.getElementById('install-gpu-support').checked = isEditing ? (existingInstall.provider_config.nvidia_support || existingInstall.provider_config.dri3_support) : (appData.provider_config.nvidia_support || appData.provider_config.dri3_support);
  document.getElementById('install-home-support').checked = isEditing ? existingInstall.home_directories : true;
  document.getElementById('install-url-support').checked = isEditing ? existingInstall.provider_config.url_support : appData.provider_config.url_support;
  document.getElementById('install-open-support').checked = isEditing ? existingInstall.provider_config.open_support : appData.provider_config.open_support;
  document.getElementById('install-auto-update').checked = isEditing ? existingInstall.auto_update : true;

  document.getElementById('install-app-users').value = isEditing ? existingInstall.users.join(',') : 'all';
  document.getElementById('install-app-groups').value = isEditing ? existingInstall.groups.join(',') : 'all';

  const templateSelect = document.getElementById('install-app-template');
  templateSelect.innerHTML = '';
  if (adminData.appTemplates.length > 0) {
    adminData.appTemplates.forEach(template => {
      const option = document.createElement('option');
      option.value = template.name;
      option.textContent = template.name;
      templateSelect.appendChild(option);
    });
  } else {
    templateSelect.innerHTML = `<option value="">No templates available</option>`;
  }
  templateSelect.disabled = adminData.appTemplates.length === 0;

  if (isEditing) {
    templateSelect.value = existingInstall.app_template || (adminData.appTemplates.length > 0 ? adminData.appTemplates[0].name : '');
  } else if (adminData.appTemplates.length > 0) {
    const defaultTemplate = adminData.appTemplates.find(t => t.name === 'Default');
    templateSelect.value = defaultTemplate ? defaultTemplate.name : adminData.appTemplates[0].name;
  }

  appInstallModal.style.display = 'block';
}

function showImageUpdateModal(app) {
  currentAppForUpdateCheck = app;
  imageUpdateModalTitle.textContent = t('options.modals.updateStatusTitle', {
    appName: app.name
  });
  imageUpdateModalBody.innerHTML = `<div class="spinner"></div><p>${t('options.modals.checkingUpdates')}</p>`;
  imageUpdateModal.style.display = 'block';

  secureFetch(`/api/admin/apps/installed/${app.id}/check_update`, {
      method: 'POST'
    })
    .then(data => {
      const currentSha = data.current_sha ? data.current_sha.substring(0, 12) : t('common.na');
      if (data.update_available) {
        imageUpdateModalBody.innerHTML = `
                    <p><i class="fas fa-arrow-alt-circle-up" style="color: var(--color-success);"></i> ${t('options.modals.updateAvailable')}</p>
                    <p>${t('options.modals.yourVersion', { sha: `<span class="sha-display">${currentSha}</span>` })}</p>
                    <p>${t('options.modals.latestAvailable')}</p>
                `;
        imageUpdateModalFooter.innerHTML = `
                    <button class="primary" id="pull-latest-image-btn">${t('options.modals.pullLatest')}</button>
                `;
      } else {
        imageUpdateModalBody.innerHTML = `
                    <p><i class="fas fa-check-circle" style="color: var(--color-success);"></i> ${t('options.modals.upToDate')}</p>
                    <p>${t('options.modals.currentVersion', { sha: `<span class="sha-display">${currentSha}</span>` })}</p>
                `;
      }
    })
    .catch(error => {
      imageUpdateModalBody.innerHTML = `
                <p><i class="fas fa-exclamation-circle" style="color: var(--color-danger);"></i> ${t('options.modals.errorChecking')}</p>
                <p style="color: var(--text-muted); font-size: 0.9em;">${error.message}</p>
            `;
    });
}

async function handlePullLatestImage() {
  if (!currentAppForUpdateCheck) return;

  imageUpdateModalBody.innerHTML = `<div class="spinner"></div><p>${t('options.modals.pullingLatest')}</p>`;
  imageUpdateModalFooter.innerHTML = '';

  try {
    const data = await secureFetch(`/api/admin/apps/installed/${currentAppForUpdateCheck.id}/pull_latest`, {
      method: 'POST'
    });
    const newSha = data.new_sha ? data.new_sha.substring(0, 12) : t('common.na');
    imageUpdateModalBody.innerHTML = `
            <p><i class="fas fa-check-circle" style="color: var(--color-success);"></i> ${t('options.modals.pullComplete')}</p>
            <p>${t('options.modals.newVersion', { sha: `<span class="sha-display">${newSha}</span>` })}</p>
        `;
    await refreshAppData();
  } catch (error) {
    imageUpdateModalBody.innerHTML = `
            <p><i class="fas fa-exclamation-circle" style="color: var(--color-danger);"></i> ${t('options.modals.errorPulling')}</p>
            <p style="color: var(--text-muted); font-size: 0.9em;">${error.message}</p>
        `;
  } finally {
    imageUpdateModalFooter.innerHTML = `<button class="primary close-button" data-modal-id="image-update-modal">${t('common.close')}</button>`;
  }
}

async function renderPinnedBehaviorTable() {
  const tbody = document.querySelector('#pinned-behavior-table tbody');
  try {
    const allItems = await chrome.storage.local.get(null);
    const pinnedItems = Object.entries(allItems).filter(([key]) => key.startsWith('workflow_profile_'));

    if (pinnedItems.length === 0) {
      tbody.innerHTML = `<tr class="empty-row"><td colspan="3" style="text-align:center; padding: 2rem;">${t('options.placeholders.noPinned')}</td></tr>`;
      return;
    }

    const {
      sealskinConfig
    } = await chrome.storage.local.get('sealskinConfig');
    if (sealskinConfig && sealskinConfig.username) {
      const jwt = await generateJwtNative(clientPrivateKeyInput.value.trim(), usernameInput.value.trim());
      const appsData = await new Promise((resolve, reject) => {
        chrome.runtime.sendMessage({
          type: 'secureFetch',
          payload: {
            url: '/api/applications',
            options: {
              method: 'POST',
              body: JSON.stringify({}),
              headers: {
                'Authorization': `Bearer ${jwt}`
              }
            }
          }
        }, response => {
          if (chrome.runtime.lastError) reject(new Error(chrome.runtime.lastError.message));
          else if (response.success) resolve(response.data);
          else reject(new Error(response.error));
        });
      });
      const appNameMap = new Map((appsData || []).map(app => [app.id, app.name]));

      tbody.innerHTML = pinnedItems.map(([key, value]) => {
        let triggerText = '';
        if (key === 'workflow_profile_simple') triggerText = t('options.pinned.triggerSimple');
        else if (key === 'workflow_profile_url') triggerText = t('options.pinned.triggerUrl');
        else triggerText = t('options.pinned.triggerFile', {
          fileType: key.replace('workflow_profile_.', '')
        });

        const appName = appNameMap.get(value.appId) || t('options.pinned.unknownApp', {
          appId: value.appId.substring(0, 8)
        });

        return `
                    <tr>
                        <td>${triggerText}</td>
                        <td>${appName}</td>
                        <td class="actions-cell">
                            <button class="danger" data-storage-key="${key}">${t('common.delete')}</button>
                        </td>
                    </tr>
                `;
      }).join('');
    } else {
      tbody.innerHTML = `<tr class="empty-row"><td colspan="3" style="text-align:center;">Login to view app names.</td></tr>`;
    }
  } catch (error) {
    tbody.innerHTML = `<tr class="empty-row"><td colspan="3" style="text-align:center;">Error loading pinned behaviors.</td></tr>`;
    console.error("Error rendering pinned behaviors:", error);
  }
}

async function openTab(tabName) {
  if (installedAppsPollingInterval) {
    clearInterval(installedAppsPollingInterval);
    installedAppsPollingInterval = null;
  }

  document.querySelectorAll('.tab-content').forEach(tab => tab.classList.remove('active'));
  document.querySelectorAll('.nav-link').forEach(link => link.classList.remove('active'));
  document.getElementById(tabName).classList.add('active');
  document.querySelector(`.nav-link[data-tabname="${tabName}"]`).classList.add('active');

  if (tabName === 'Home' && usernameInput.value) {
    await refreshHomeDirs();
  } else if (tabName === 'Sessions' && usernameInput.value) {
    await refreshSessions();
  } else if (tabName === 'InstalledApps') {
    if (adminData.installedApps.length === 0) {
      await refreshAppData();
    }
    if (adminData.installedApps.some(app => app.pull_status === 'pulling')) {
      installedAppsPollingInterval = setInterval(refreshInstalledApps, 3000);
    }
  } else if (tabName === 'AppStore') {
    if (adminData.appStores.length === 0) {
      await refreshAppData();
    }
  } else if (tabName === 'PinnedBehavior') {
    await renderPinnedBehaviorTable();
  } else if (tabName === 'AppTemplates') {
    initializeAppTemplatesTab();
  }
}

document.addEventListener('DOMContentLoaded', async () => {
  const translator = getTranslator(navigator.language);
  t = translator.t;
  APP_TEMPLATE_SETTINGS = getAppTemplateSettings(t);
  applyTranslations(document, t);

  const howToList = document.getElementById('how-to-list');
  if (howToList) {
    howToList.innerHTML = t('options.dashboard.howToList').map(item => `<li>${item}</li>`).join('');
  }

  const themeToggle = document.getElementById('theme-toggle');
  const prefersDark = window.matchMedia('(prefers-color-scheme: dark)');
  const currentTheme = localStorage.getItem('theme');

  const setTheme = (theme) => {
    document.documentElement.setAttribute('data-theme', theme);
    themeToggle.checked = theme === 'dark';
  };

  if (currentTheme) setTheme(currentTheme);
  else setTheme(prefersDark.matches ? 'dark' : 'light');

  themeToggle.addEventListener('change', () => {
    const newTheme = themeToggle.checked ? 'dark' : 'light';
    localStorage.setItem('theme', newTheme);
    setTheme(newTheme);
  });

  document.querySelectorAll('.nav-link').forEach(button => {
    button.addEventListener('click', (event) => {
      openTab(event.currentTarget.dataset.tabname);
    });
  });

  await loadConfig();
  await openTab('Config');

  if (usernameInput.value && clientPrivateKeyInput.value) {
    if (await handleLogin()) {
      const statusData = await secureFetch('/api/admin/status', {
        method: 'POST',
        body: JSON.stringify({})
      });
      if (statusData.is_admin) await refreshAppData();
    }
  } else {
    dashboardView.style.display = 'none';
    simpleConfigView.style.display = 'block';
    advancedConfigView.style.display = 'none';
  }

  showAdvancedLink.addEventListener('click', (e) => {
    e.preventDefault();
    simpleConfigView.style.display = 'none';
    advancedConfigView.style.display = 'block';
  });
  hideAdvancedLink.addEventListener('click', (e) => {
    e.preventDefault();
    simpleConfigView.style.display = 'block';
    advancedConfigView.style.display = 'none';
  });

  applyConfigBtn.addEventListener('click', () => {
    const text = configTextArea.value.trim();
    if (text) parseAndApplyConfig(text);
    else if (configFileUpload.files.length > 0) {
      const reader = new FileReader();
      reader.onload = (event) => parseAndApplyConfig(event.target.result);
      reader.onerror = () => displayStatus(t('options.status.fileReadError'), true);
      reader.readAsText(configFileUpload.files[0]);
    } else displayStatus(t('options.status.noConfig'), true);
  });

  saveButton.addEventListener('click', saveConfig);
  loginButton.addEventListener('click', handleLogin);
  logoutButton.addEventListener('click', () => {
    if (confirm(t('options.dashboard.confirmLogout'))) {
      chrome.storage.local.remove('sealskinConfig', () => {
        serverIpInput.value = '';
        apiPortInput.value = '8000';
        sessionPortInput.value = '8443';
        usernameInput.value = '';
        clientPrivateKeyInput.value = '';
        serverPublicKeyInput.value = '';
        dashboardView.style.display = 'none';
        simpleConfigView.style.display = 'block';
        advancedConfigView.style.display = 'none';
        setAdminNavVisibility(false);
        homeDirTabButton.style.display = 'none';
        sessionsTabButton.style.display = 'none';
        displayStatus(t('options.status.logoutSuccess'), false);
      });
    }
  });

  exportConfigButton.addEventListener('click', () => {
    const user = {
      username: usernameInput.value.trim()
    };
    const privateKey = clientPrivateKeyInput.value.trim();
    if (user.username && privateKey) showUserConfigModal(user, privateKey, false);
    else displayStatus(t('options.status.generateConfigFailed'), true);
  });

  document.querySelectorAll('.close-button').forEach(btn => btn.addEventListener('click', () => document.getElementById(btn.dataset.modalId).style.display = 'none'));
  window.addEventListener('click', (event) => {
    if (event.target.classList.contains('modal')) event.target.style.display = 'none';
  });

  copyConfigBtn.addEventListener('click', () => navigator.clipboard.writeText(generatedConfigText.value).then(() => displayStatus(t('options.status.copySuccess')), () => displayStatus(t('options.status.copyFailed'), true)));
  downloadConfigBtn.addEventListener('click', () => {
    const username = downloadConfigBtn.dataset.username || 'user';
    const blob = new Blob([generatedConfigText.value], {
      type: 'application/json'
    });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `${username}-sealskin-config.json`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  });

  ['users', 'groups', 'admins', 'installedApps'].forEach(dataType => {
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
        const action = button.dataset.page;
        if (action === 'prev') tableStates[dataType].currentPage--;
        if (action === 'next') tableStates[dataType].currentPage++;
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
      public_key: document.getElementById('newAdminPublicKey').value.trim() || null
    };
    if (!payload.username) return displayStatus(t('common.username') + ' is required.', true);
    try {
      displayStatus(t('options.status.creatingAdmin'));
      const response = await secureFetch('/api/admin/admins', {
        method: 'POST',
        body: JSON.stringify(payload)
      });
      displayStatus(t('options.status.adminCreated', {
        username: response.user.username
      }), false);
      if (response.private_key) showUserConfigModal(response.user, response.private_key, true);
      addAdminForm.reset();
      await refreshAdminData();
    } catch (error) {
      displayStatus(t('options.status.adminCreateFailed', {
        error: error.message
      }), true);
    }
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
      if (confirm(t('options.admins.confirmDelete', {
          username
        }))) {
        try {
          await secureFetch(`/api/admin/admins/${username}`, {
            method: 'DELETE'
          });
          displayStatus(t('options.status.adminDeleted', {
            username
          }));
          await refreshAdminData();
        } catch (error) {
          displayStatus(t('options.status.adminDeleteFailed', {
            error: error.message
          }), true);
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
      public_key: document.getElementById('newUserPublicKey').value.trim() || null,
      settings: getSettingsFromForm('newUser')
    };
    if (!payload.username) return displayStatus(t('common.username') + ' is required.', true);
    try {
      displayStatus(t('options.status.creatingUser'));
      const response = await secureFetch('/api/admin/users', {
        method: 'POST',
        body: JSON.stringify(payload)
      });
      displayStatus(t('options.status.userCreated', {
        username: response.user.username
      }), false);
      if (response.private_key) showUserConfigModal(response.user, response.private_key, true);
      addUserForm.reset();
      await refreshAdminData();
    } catch (error) {
      displayStatus(t('options.status.userCreateFailed', {
        error: error.message
      }), true);
    }
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
    if (button.classList.contains('danger')) {
      if (confirm(t('options.users.confirmDelete', {
          username
        }))) {
        try {
          await secureFetch(`/api/admin/users/${username}`, {
            method: 'DELETE'
          });
          displayStatus(t('options.status.userDeleted', {
            username
          }));
          await refreshAdminData();
        } catch (error) {
          displayStatus(t('options.status.userDeleteFailed', {
            error: error.message
          }), true);
        }
      }
    } else if (button.classList.contains('warning')) {
      const user = adminData.users.find(u => u.username === username);
      if (!user) return;
      document.getElementById('user-edit-title').textContent = t('options.modals.editUserTitle', {
        username
      });
      document.getElementById('editUsername').value = username;
      populateSettingsForm('editUser', user.settings);
      const effectiveSettingsPre = document.getElementById('effective-settings-pre');
      const updateEffectiveSettingsDisplay = () => {
        const currentFormSettings = getSettingsFromForm('editUser');
        const pseudoUser = {
          settings: currentFormSettings
        };
        effectiveSettingsPre.textContent = JSON.stringify(calculateEffectiveSettings(pseudoUser), null, 2);
      };
      userEditForm.oninput = updateEffectiveSettingsDisplay;
      updateEffectiveSettingsDisplay();
      userEditModal.style.display = 'block';
    } else if (button.classList.contains('secondary')) {
      await refreshAdminUserHomeDirs(username, false);
      userHomeDirModal.style.display = 'block';
    }
  });

  userEditForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    const username = document.getElementById('editUsername').value;
    const payload = {
      settings: getSettingsFromForm('editUser')
    };
    try {
      await secureFetch(`/api/admin/users/${username}`, {
        method: 'PUT',
        body: JSON.stringify(payload)
      });
      displayStatus(t('options.status.userUpdated', {
        username
      }));
      userEditModal.style.display = 'none';
      await refreshAdminData();
    } catch (error) {
      displayStatus(t('options.status.userUpdateFailed', {
        error: error.message
      }), true);
    }
  });

  addGroupForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    const groupName = document.getElementById('newGroupName').value.trim();
    if (!groupName) return displayStatus(t('common.group') + ' name is required.', true);
    const payload = {
      name: groupName,
      settings: getSettingsFromForm('newGroup')
    };
    delete payload.settings.group;
    try {
      await secureFetch('/api/admin/groups', {
        method: 'POST',
        body: JSON.stringify(payload)
      });
      displayStatus(t('options.status.groupCreated', {
        groupName
      }));
      addGroupForm.reset();
      await refreshAdminData();
    } catch (error) {
      displayStatus(t('options.status.groupCreateFailed', {
        error: error.message
      }), true);
    }
  });

  tableRenderConfig.groups.tbody.addEventListener('click', async (e) => {
    const button = e.target.closest('button');
    if (!button) return;
    const groupName = button.dataset.groupname;
    if (!groupName) return;
    if (button.classList.contains('danger')) {
      if (confirm(t('options.groups.confirmDelete', {
          groupName
        }))) {
        try {
          await secureFetch(`/api/admin/groups/${groupName}`, {
            method: 'DELETE'
          });
          displayStatus(t('options.status.groupDeleted', {
            groupName
          }));
          await refreshAdminData();
        } catch (error) {
          displayStatus(t('options.status.groupDeleteFailed', {
            error: error.message
          }), true);
        }
      }
    } else if (button.classList.contains('warning')) {
      const group = adminData.groups.find(g => g.name === groupName);
      if (!group) return;
      document.getElementById('group-edit-title').textContent = t('options.modals.editGroupTitle', {
        groupName
      });
      document.getElementById('editGroupName').value = groupName;
      const formSettings = {
        ...getSettingsFromForm('editGroup'),
        ...group.settings
      };
      populateSettingsForm('editGroup', formSettings);
      groupEditModal.style.display = 'block';
    }
  });

  groupEditForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    const groupName = document.getElementById('editGroupName').value;
    const payload = {
      settings: getSettingsFromForm('editGroup')
    };
    delete payload.settings.group;
    try {
      await secureFetch(`/api/admin/groups/${groupName}`, {
        method: 'PUT',
        body: JSON.stringify(payload)
      });
      displayStatus(t('options.status.groupUpdated', {
        groupName
      }));
      groupEditModal.style.display = 'none';
      await refreshAdminData();
    } catch (error) {
      displayStatus(t('options.status.groupUpdateFailed', {
        error: error.message
      }), true);
    }
  });

  addHomeDirForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    const homeNameInput = document.getElementById('newHomeDirName');
    const homeName = homeNameInput.value.trim();
    if (!homeName) return;
    try {
      await secureFetch('/api/homedirs', {
        method: 'POST',
        body: JSON.stringify({
          home_name: homeName
        })
      });
      displayStatus(t('options.status.homedirCreated', {
        homeName
      }));
      homeNameInput.value = '';
      await refreshHomeDirs();
    } catch (error) {
      displayStatus(t('options.status.homedirCreateFailed', {
        error: error.message
      }), true);
    }
  });

  homeDirsTbody.addEventListener('click', async (e) => {
    const button = e.target.closest('button.danger');
    if (!button) return;
    const homeName = button.dataset.homedirName;
    if (confirm(t('options.home.confirmDelete', {
        homeName
      }))) {
      try {
        await secureFetch(`/api/homedirs/${homeName}`, {
          method: 'DELETE'
        });
        displayStatus(t('options.status.homedirDeleted', {
          homeName
        }));
        await refreshHomeDirs();
      } catch (error) {
        displayStatus(t('options.status.homedirDeleteFailed', {
          error: error.message
        }), true);
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
      const {
        username,
        isAdmin
      } = currentAdminManagedUser;
      const path = isAdmin ? 'admins' : 'users';
      await secureFetch(`/api/admin/${path}/${username}/homedirs`, {
        method: 'POST',
        body: JSON.stringify({
          home_name: homeName
        })
      });
      displayStatus(t('options.status.homedirCreatedFor', {
        homeName,
        username
      }));
      homeNameInput.value = '';
      await refreshAdminUserHomeDirs(username, isAdmin);
    } catch (error) {
      displayStatus(t('options.status.homedirCreateFailed', {
        error: error.message
      }), true);
    }
  });

  userHomeDirsTbody.addEventListener('click', async (e) => {
    const button = e.target.closest('button.danger');
    if (!button || !currentAdminManagedUser) return;
    const homeName = button.dataset.homedirName;
    const {
      username,
      isAdmin
    } = currentAdminManagedUser;
    if (confirm(t('options.modals.confirmDeleteDir', {
        homeName,
        username
      }))) {
      try {
        const path = isAdmin ? 'admins' : 'users';
        await secureFetch(`/api/admin/${path}/${username}/homedirs/${homeName}`, {
          method: 'DELETE'
        });
        displayStatus(t('options.status.homedirDeletedFor', {
          homeName,
          username
        }));
        await refreshAdminUserHomeDirs(username, isAdmin);
      } catch (error) {
        displayStatus(t('options.status.homedirDeleteFailed', {
          error: error.message
        }), true);
      }
    }
  });

  refreshSessionsBtn.addEventListener('click', refreshSessions);
  sessionsContainer.addEventListener('click', async (e) => {
    const button = e.target.closest('button.stop-session-btn');
    if (!button) return;

    const sessionId = button.dataset.sessionId;
    const isAdmin = dashboardRole.textContent === t('options.dashboard.roleAdmin');
    const endpoint = isAdmin ? `/api/admin/sessions/${sessionId}` : `/api/sessions/${sessionId}`;

    if (confirm(t('options.sessions.confirmStop'))) {
      button.disabled = true;
      button.innerHTML = `<i class="fas fa-spinner fa-spin"></i>`;
      try {
        await secureFetch(endpoint, {
          method: 'DELETE'
        });
        displayStatus(t('options.status.sessionStopped'));
        await refreshSessions();
      } catch (error) {
        displayStatus(t('options.status.sessionStopError', {
          error: error.message
        }), true);
        await refreshSessions();
      }
    }
  });

  appStoreSelect.addEventListener('change', (e) => fetchAndRenderAvailableApps(e.target.value));

  refreshAppStoreBtn.addEventListener('click', () => {
    const selectedStoreUrl = appStoreSelect.value;
    if (selectedStoreUrl) {
      fetchAndRenderAvailableApps(selectedStoreUrl);
    } else {
      displayStatus('Please select an app store to refresh.', true);
    }
  });

  addAppStoreForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    const name = document.getElementById('new-app-store-name').value.trim();
    const url = document.getElementById('new-app-store-url').value.trim();
    if (!name || !url) return;
    try {
      await secureFetch('/api/admin/apps/stores', {
        method: 'POST',
        body: JSON.stringify({
          name,
          url
        })
      });
      displayStatus(t('options.status.appStoreAdded', {
        name
      }));
      addAppStoreForm.reset();
      await refreshAppData();
    } catch (error) {
      displayStatus(t('options.status.appStoreAddFailed', {
        error: error.message
      }), true);
    }
  });

  availableAppsContainer.addEventListener('click', (e) => {
    const card = e.target.closest('.app-card[data-appid]');
    if (!card) return;
    const appId = card.dataset.appid;
    const appData = adminData.availableApps.find(app => app.id === appId);
    if (appData) showInstallModal(appData);
  });

  appInstallForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    const isEditing = !!document.getElementById('install-app-id').value;
    const app_id = isEditing ? document.getElementById('install-app-id').value : crypto.randomUUID();

    const gpu = document.getElementById('install-gpu-support').checked;
    const sourceAppId = document.getElementById('install-source-app-id').value;
    const sourceApp = adminData.availableApps.find(a => a.id === sourceAppId);

    const payload = {
      id: app_id,
      name: document.getElementById('install-app-name').value,
      source: document.getElementById('install-source-name').value,
      source_app_id: sourceAppId,
      users: document.getElementById('install-app-users').value.split(',').map(s => s.trim()).filter(Boolean),
      groups: document.getElementById('install-app-groups').value.split(',').map(s => s.trim()).filter(Boolean),
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
        env: []
      }
    };

    try {
      const method = isEditing ? 'PUT' : 'POST';
      const url = isEditing ? `/api/admin/apps/installed/${app_id}` : '/api/admin/apps/installed';
      await secureFetch(url, {
        method,
        body: JSON.stringify(payload)
      });
      const action = isEditing ? t('options.status.appSaveActions.updated') : t('options.status.appSaveActions.installed');
      displayStatus(t('options.status.appSaved', {
        name: payload.name,
        action
      }));
      appInstallModal.style.display = 'none';
      await refreshAppData();
      await openTab('InstalledApps');
    } catch (error) {
      displayStatus(t('options.status.appSaveFailed', {
        error: error.message
      }), true);
    }
  });

  installedAppsTbody.addEventListener('click', async (e) => {
    const button = e.target.closest('button[data-appid]');
    if (!button) return;
    const appId = button.dataset.appid;
    const app = adminData.installedApps.find(a => a.id === appId);
    if (!app) return;

    if (button.classList.contains('danger')) {
      if (confirm(t('options.installedApps.confirmDelete', {
          appName: app.name
        }))) {
        try {
          await secureFetch(`/api/admin/apps/installed/${appId}`, {
            method: 'DELETE'
          });
          displayStatus(t('options.status.appDeleted', {
            name: app.name
          }));
          await refreshAppData();
        } catch (error) {
          displayStatus(t('options.status.appDeleteFailed', {
            error: error.message
          }), true);
        }
      }
    } else if (button.classList.contains('warning')) {
      const sourceApp = adminData.availableApps.find(a => a.id === app.source_app_id);
      const appDataForModal = sourceApp || {
        id: app.source_app_id,
        name: app.name,
        logo: app.logo,
        url: app.url,
        provider: app.provider,
        provider_config: app.provider_config
      };
      showInstallModal(appDataForModal, app);
    } else if (button.classList.contains('check-update-btn')) {
      showImageUpdateModal(app);
    }
  });

  imageUpdateModalFooter.addEventListener('click', e => {
    if (e.target.id === 'pull-latest-image-btn') {
      handlePullLatestImage();
    }
  });

  document.querySelector('#pinned-behavior-table tbody').addEventListener('click', async (e) => {
    const button = e.target.closest('button.danger');
    if (!button) return;
    const key = button.dataset.storageKey;
    const prettyKey = key.replace('workflow_profile_', '');
    if (confirm(`Are you sure you want to remove the pinned behavior for "${prettyKey}"?`)) {
      await chrome.storage.local.remove(key);
      displayStatus(t('options.status.pinRemoved'));
      await renderPinnedBehaviorTable();
    }
  });
});
