/**
 * Launcher popup. Served by the server and framed by the extension popup or
 * the mobile app; every privileged operation goes through the bridge.
 */

import { bridge } from '../lib/bridge.js';
import { secureFetch, getContextBlob, uploadInChunks } from '../lib/api.js';
import { loadTranslator, applyTranslations } from '../lib/i18n.js';
import { supportedLangs } from '../lib/languages.js';
import { browserTimezone } from '../lib/timezone.js';
import { announce, escapeHtml, formatLogoSrc, hydrateLogos, timeAgo, currentLocale } from '../lib/dom.js';

let t;
let info;

const spinner = document.getElementById('spinner');
const launchBtn = document.getElementById('launch-btn');
const launchBtnText = document.getElementById('launch-btn-text');
const statusDiv = document.getElementById('status');
const appGridContainer = document.getElementById('app-grid-container');
const appSearchInput = document.getElementById('app-search');
const gpuFormGroup = document.getElementById('gpu-form-group');
const gpuSelect = document.getElementById('gpuSelect');
const homeDirFormGroup = document.getElementById('homedir-form-group');
const homeDirSelect = document.getElementById('homeDirectory');
const languageSelect = document.getElementById('language');
const saveOptionsCheckbox = document.getElementById('saveOptions');
const saveOptionsLabel = document.getElementById('saveOptionsLabel');
const openFileContainer = document.getElementById('open-file-container');
const openFileCheckbox = document.getElementById('openFileOnLaunch');
const waylandModeCheckbox = document.getElementById('waylandMode');
const sessionsTabBtn = document.getElementById('sessions-tab-btn');
const launchTabBtn = document.getElementById('launch-tab-btn');
const manageFilesBtn = document.getElementById('manage-files-btn');
const uploadFilesTabBtn = document.getElementById('upload-files-tab-btn');
const uploadStorageTabBtn = document.getElementById('upload-storage-tab-btn');
const sessionsView = document.getElementById('sessions-view');
const launchView = document.getElementById('launch-view');
const uploadStorageView = document.getElementById('upload-storage-view');
const sessionsListContainer = document.getElementById('sessions-list-container');
const uploadProgressContainer = document.getElementById('upload-progress-container');
const uploadProgressBar = document.getElementById('upload-progress');
const progressLabel = document.getElementById('progress-label');
const uploadStorageBtn = document.getElementById('upload-storage-btn');
const uploadStorageBtnText = document.getElementById('upload-storage-btn-text');
const uploadStorageSpinner = document.getElementById('upload-storage-spinner');
const uploadStorageProgressContainer = document.getElementById('upload-storage-progress-container');
const uploadStorageProgressBar = document.getElementById('upload-storage-progress');
const uploadStorageProgressLabel = document.getElementById('upload-storage-progress-label');
const uploadStorageSuccessContainer = document.getElementById('upload-storage-success-container');
const uploadStorageFooter = document.getElementById('upload-storage-footer');
const uploadStorageDescription = document.getElementById('upload-storage-description');

let sealskinContext = {};
let sealskinConfig = {};
let userSettings = {};
let availableApps = [];
let availableGpus = [];
let homeDirs = [];
let activeSessions = [];
let isSimpleLaunch = false;
let selectedAppId = null;
let launchProfileKey = 'workflow_profile_simple';

const isMobile = () => info && info.shell === 'mobile';

function setStatus(message, isError = false) {
  statusDiv.textContent = message;
  statusDiv.style.color = isError ? 'var(--color-danger-text)' : 'var(--text-muted)';
}

function setContextualStatus() {
  if (isSimpleLaunch) {
    setStatus('');
    return;
  }
  let message = null;
  if (sealskinContext.action === 'file' && sealskinContext.filename) {
    message = t('popup.status.openingFile', { filename: sealskinContext.filename });
  } else if (sealskinContext.action === 'url' && sealskinContext.targetUrl) {
    message = t('popup.status.openingUrl', { targetUrl: sealskinContext.targetUrl });
  } else if (sealskinContext.action === 'server-file' && sealskinContext.filename) {
    message = t('popup.status.openingServerFile', { filename: sealskinContext.filename });
  }
  if (message) {
    setStatus(message);
    statusDiv.title = message;
  }
}

function showView(viewName) {
  [launchView, sessionsView, uploadStorageView].forEach((v) => v.classList.remove('active'));
  [launchTabBtn, sessionsTabBtn, uploadStorageTabBtn].forEach((b) => b.classList.remove('active'));

  if (viewName === 'sessions') {
    sessionsView.classList.add('active');
    sessionsTabBtn.classList.add('active');
  } else if (viewName === 'upload-storage') {
    uploadStorageView.classList.add('active');
    uploadStorageTabBtn.classList.add('active');
  } else {
    launchView.classList.add('active');
    launchTabBtn.classList.add('active');
  }
}

function renderActiveSessions(isFileContext) {
  sessionsListContainer.innerHTML = '';
  if (activeSessions.length === 0) {
    sessionsListContainer.innerHTML = `<p style="text-align: center; color: var(--text-muted); padding: 2rem 0;">${t('popup.sessionsView.noSessions')}</p>`;
    return;
  }

  activeSessions.forEach((session, index) => {
    const card = document.createElement('div');
    card.className = 'session-card';
    card.style.setProperty('--i', String(index));
    card.dataset.sessionId = session.session_id;

    const actionButtonText = isFileContext ? t('common.sendFile') : t('common.reopen');
    const actionButtonClass = isFileContext ? 'secondary' : 'primary';
    const roomIcon = session.is_collaboration ? '<i class="fas fa-users"></i>' : '';

    let contextHtml = '';
    if (session.launch_context) {
      const icon = session.launch_context.type === 'url' ? 'fa-link' : 'fa-file-alt';
      const value = escapeHtml(session.launch_context.value);
      contextHtml = `<div class="session-card-info-action" title="${value}"><i class="fas ${icon}"></i> ${value}</div>`;
    }

    card.innerHTML = `
            <img data-logo-src="${escapeHtml(session.app_logo)}" src="icons/icon128.png" class="session-card-logo">
            <div class="session-card-info">
                <div class="session-card-info-name">${escapeHtml(session.app_name)} ${roomIcon}</div>
                <div class="session-card-info-time">Started ${timeAgo(session.created_at, t)}</div>
                ${contextHtml}
            </div>
            <div class="session-card-actions">
                <button class="${actionButtonClass} reopen-btn-text" data-action="reopen">${actionButtonText}</button>
                <button class="danger" data-action="close" title="${t('common.stop')} session"><i class="fas fa-times"></i></button>
            </div>
        `;
    sessionsListContainer.appendChild(card);
  });

  hydrateLogos(sessionsListContainer);
}

function renderAppCards(apps, defaultAppId = null) {
  appGridContainer.innerHTML = '';

  let recommendedApps = [];
  let otherApps = [];
  const fileExtension = (sealskinContext.action === 'file' && sealskinContext.filename)
    ? sealskinContext.filename.split('.').pop().toLowerCase()
    : null;

  if (isSimpleLaunch) {
    otherApps = [...apps];
  } else if (sealskinContext.action === 'url') {
    apps.forEach((app) => {
      if (app.url_support) recommendedApps.push(app);
      else otherApps.push(app);
    });
  } else if (fileExtension) {
    apps.forEach((app) => {
      if (app.extensions.includes(fileExtension)) recommendedApps.push(app);
      else otherApps.push(app);
    });
  } else {
    otherApps = [...apps];
  }

  if (defaultAppId) {
    const moveAppToFront = (arr) => {
      const idx = arr.findIndex((app) => app.id === defaultAppId);
      if (idx > -1) {
        const [defaultApp] = arr.splice(idx, 1);
        arr.unshift(defaultApp);
      }
    };
    moveAppToFront(recommendedApps);
    moveAppToFront(otherApps);
  }

  let cardIndex = 0;
  const createCard = (app) => {
    const card = document.createElement('div');
    card.className = 'app-card-popup';
    card.dataset.appid = app.id;
    card.style.setProperty('--i', String(cardIndex++));
    card.innerHTML = `
            <img data-logo-src="${escapeHtml(app.logo)}" src="icons/icon128.png" alt="${escapeHtml(app.name)} logo">
            <span>${escapeHtml(app.name)}</span>
        `;
    card.addEventListener('click', () => handleAppSelection(app.id));
    formatLogoSrc(app.logo).then((src) => {
      const img = card.querySelector('img');
      if (img) img.src = src;
    });
    return card;
  };

  if (recommendedApps.length > 0) {
    const recommendedHeader = document.createElement('div');
    recommendedHeader.className = 'app-section-header';
    recommendedHeader.textContent = 'Recommended';
    appGridContainer.appendChild(recommendedHeader);

    const grid = document.createElement('div');
    grid.className = 'app-grid';
    recommendedApps.forEach((app) => grid.appendChild(createCard(app)));
    appGridContainer.appendChild(grid);
  }

  if (otherApps.length > 0) {
    if (recommendedApps.length > 0) {
      const allAppsHeader = document.createElement('div');
      allAppsHeader.className = 'app-section-header';
      allAppsHeader.style.marginTop = '1rem';
      allAppsHeader.textContent = 'All Apps';
      appGridContainer.appendChild(allAppsHeader);
    }
    const grid = document.createElement('div');
    grid.className = 'app-grid';
    otherApps.forEach((app) => grid.appendChild(createCard(app)));
    appGridContainer.appendChild(grid);
  }

  if (apps.length === 0) {
    appGridContainer.innerHTML = `<p style="text-align: center; color: var(--text-muted);">${t('popup.status.noAppsAvailable')}</p>`;
  }

  if (defaultAppId) {
    handleAppSelection(defaultAppId);
  } else if (recommendedApps.length > 0) {
    handleAppSelection(recommendedApps[0].id);
  } else if (otherApps.length > 0) {
    handleAppSelection(otherApps[0].id);
  }
}

function handleAppSelection(appId) {
  selectedAppId = appId;
  let selectedCard = null;
  document.querySelectorAll('.app-card-popup').forEach((card) => {
    const isSelected = card.dataset.appid === appId;
    card.classList.toggle('selected', isSelected);
    if (isSelected) selectedCard = card;
  });
  if (selectedCard) {
    const img = selectedCard.querySelector('img');
    if (img && img.src && !img.src.endsWith('icons/icon128.png')) {
      setBackdrop(img.src);
    } else if (img) {
      img.addEventListener('load', () => {
        if (selectedAppId === appId) setBackdrop(img.src);
      }, { once: true });
    }
  }
  updateDynamicForms();
}

/**
 * Blurred backdrop drawn from the selected app's icon. Two image layers are
 * cross-faded so a change never pops.
 */
let backdropLayers = null;
let activeBackdropLayer = 0;
function setBackdrop(src) {
  if (!backdropLayers) {
    const container = document.querySelector('.popup-container');
    if (!container) return;
    const backdrop = document.createElement('div');
    backdrop.className = 'popup-backdrop';
    backdrop.setAttribute('aria-hidden', 'true');
    backdropLayers = [document.createElement('img'), document.createElement('img')];
    backdropLayers.forEach((img) => { img.alt = ''; backdrop.appendChild(img); });
    container.insertBefore(backdrop, container.firstChild);
  }
  const current = backdropLayers[activeBackdropLayer];
  if (current.classList.contains('show') && current.src === src) return;
  const next = backdropLayers[1 - activeBackdropLayer];
  next.src = src;
  const reveal = () => {
    next.classList.add('show');
    current.classList.remove('show');
    activeBackdropLayer = 1 - activeBackdropLayer;
  };
  if (next.complete) reveal();
  else next.addEventListener('load', reveal, { once: true });
}

function updateDynamicForms() {
  if (!selectedAppId) return;
  const selectedApp = availableApps.find((app) => app.id === selectedAppId);
  if (!selectedApp) return;

  const userHasGpu = userSettings.gpu;
  const appHasGpu = selectedApp.nvidia_support || selectedApp.dri3_support;
  if (userHasGpu && appHasGpu && availableGpus.length > 0) {
    const currentGpuVal = gpuSelect.value;
    gpuSelect.innerHTML = `<option value="none">${t('popup.launchView.noGpu')}</option>`;
    availableGpus.forEach((gpu) => {
      const isNvidia = gpu.driver === 'nvidia';
      if ((isNvidia && selectedApp.nvidia_support) || (!isNvidia && selectedApp.dri3_support)) {
        const option = document.createElement('option');
        option.value = gpu.device;
        option.textContent = `${gpu.device.split('/').pop()} (${gpu.driver})`;
        gpuSelect.appendChild(option);
      }
    });
    if ([...gpuSelect.options].some((o) => o.value === currentGpuVal)) {
      gpuSelect.value = currentGpuVal;
    }
    gpuFormGroup.classList.remove('hidden');
  } else {
    gpuFormGroup.classList.add('hidden');
  }

  const userHasStorage = userSettings.persistent_storage;
  const appHasStorage = selectedApp.home_directories;
  if (userHasStorage && appHasStorage) {
    homeDirFormGroup.classList.remove('hidden');
    if (selectedApp.is_meta_app) {
      const currentVal = homeDirSelect.value;
      homeDirSelect.innerHTML = `
          <option value="auto">${t('popup.launchView.autoHome')}</option>
          <option value="cleanroom">${t('popup.launchView.cleanroom')}</option>
      `;
      homeDirSelect.value = (currentVal === 'auto' || currentVal === 'cleanroom') ? currentVal : 'auto';
    } else if (homeDirSelect.options.length < 3 && homeDirs.length > 0) {
      populateHomeDirDropdown();
    }
  } else {
    homeDirFormGroup.classList.add('hidden');
  }
}

function populateLanguageDropdown() {
  const browserLang = currentLocale();
  languageSelect.innerHTML = '';

  const defaultOption = document.createElement('option');
  defaultOption.value = 'en_US.UTF-8';
  defaultOption.textContent = t('popup.languages.default', { locale: 'en_US' });
  languageSelect.appendChild(defaultOption);

  for (const [displayName, value] of Object.entries(supportedLangs)) {
    const option = document.createElement('option');
    option.value = value;
    option.textContent = displayName;
    languageSelect.appendChild(option);
  }

  const langCode = browserLang.split('-')[0].toLowerCase();
  const regionCode = (browserLang.split('-')[1] || '').toUpperCase();
  const values = Object.values(supportedLangs);

  const exactMatchKey = `${langCode}_${regionCode}.UTF-8`;
  if (values.includes(exactMatchKey)) {
    languageSelect.value = exactMatchKey;
    return;
  }

  const primaryRegionMap = {
    es: 'ES', fr: 'FR', pt: 'BR', de: 'DE', it: 'IT', ru: 'RU', ja: 'JP', ko: 'KR', th: 'TH',
    zh: regionCode === 'TW' || regionCode === 'HK' ? regionCode : 'CN',
  };
  const primaryRegion = primaryRegionMap[langCode];
  if (primaryRegion) {
    const primaryMatchKey = `${langCode}_${primaryRegion}.UTF-8`;
    if (values.includes(primaryMatchKey)) {
      languageSelect.value = primaryMatchKey;
      return;
    }
  }

  const firstAvailable = values.find((val) => val.startsWith(`${langCode}_`));
  languageSelect.value = firstAvailable || 'en_US.UTF-8';
}

function populateHomeDirDropdown() {
  homeDirSelect.innerHTML = `
    <option value="auto">${t('popup.launchView.autoHome')}</option>
    <option value="cleanroom">${t('popup.launchView.cleanroom')}</option>
  `;
  const optionsHtml = homeDirs
    .filter((dir) => dir !== '_sealskin_shared_files' && !dir.startsWith('auto-'))
    .map((dir) => `<option value="${escapeHtml(dir)}">${escapeHtml(dir)}</option>`)
    .join('');
  homeDirSelect.insertAdjacentHTML('beforeend', optionsHtml);
}

async function reopenOrFocusSession(session) {
  try {
    await bridge.focusSession(session);
  } catch (error) {
    console.error('Failed to focus session:', error);
  }
  bridge.close();
}

async function closeSession(sessionId) {
  const card = sessionsListContainer.querySelector(`[data-session-id="${sessionId}"]`);
  if (!card) return;

  const closeButton = card.querySelector('[data-action="close"]');
  closeButton.disabled = true;
  closeButton.innerHTML = '<i class="fas fa-spinner fa-spin"></i>';

  try {
    await bridge.closeSession(sessionId);
    card.remove();
    activeSessions = activeSessions.filter((s) => s.session_id !== sessionId);
    if (activeSessions.length === 0) {
      renderActiveSessions(sealskinContext.action === 'file');
    }
  } catch (error) {
    console.error('Failed to close session:', error);
    setStatus(t('popup.status.errorClosingSession', { message: error.message || 'Unknown error' }), true);
    closeButton.disabled = false;
    closeButton.innerHTML = '<i class="fas fa-times"></i>';
  }
}

/**
 * Upload the context's file with progress shown in the given elements.
 */
async function uploadContextFile(progressBar, label) {
  const fileBlob = await getContextBlob(sealskinContext);
  const filename = sealskinContext.filename || 'uploaded.file';
  const result = await uploadInChunks(fileBlob, filename, {
    onProgress: (done, total) => {
      label.textContent = t('popup.uploadStorageView.uploadingChunk', { current: done, total });
      progressBar.value = (done / total) * 100;
    },
  });
  return { ...result, filename };
}

async function handleSendFileToSession(sessionId) {
  setStatus(t('popup.status.preparingFile'));
  try {
    uploadProgressContainer.style.display = 'block';
    const { uploadId, totalChunks, filename } = await uploadContextFile(uploadProgressBar, progressLabel);
    progressLabel.textContent = t('popup.uploadStorageView.uploadComplete');
    setStatus(t('popup.status.sendingFile'));
    await secureFetch(`/api/sessions/${sessionId}/send_file`, {
      method: 'POST',
      body: JSON.stringify({ filename, upload_id: uploadId, total_chunks: totalChunks }),
    });

    const session = activeSessions.find((s) => s.session_id === sessionId);
    reopenOrFocusSession(session);
  } catch (error) {
    setStatus(t('popup.status.errorSendingFile', { message: error.message }), true);
  } finally {
    uploadProgressContainer.style.display = 'none';
  }
}

async function handleUploadToStorage() {
  uploadStorageBtn.disabled = true;
  uploadStorageSpinner.style.display = 'block';
  uploadStorageBtnText.textContent = t('popup.uploadStorageView.uploadingButton');
  uploadStorageSuccessContainer.style.display = 'none';

  uploadStorageProgressContainer.style.display = 'block';
  uploadStorageProgressBar.value = 0;
  uploadStorageProgressLabel.textContent = t('popup.uploadStorageView.preparing');

  try {
    const { uploadId, totalChunks, filename } = await uploadContextFile(uploadStorageProgressBar, uploadStorageProgressLabel);
    uploadStorageProgressLabel.textContent = t('popup.uploadStorageView.finalizing');

    await secureFetch('/api/upload/to_storage', {
      method: 'POST',
      body: JSON.stringify({
        filename,
        upload_id: uploadId,
        total_chunks: totalChunks,
        home_name: '_sealskin_shared_files',
      }),
    });

    uploadStorageProgressContainer.style.display = 'none';
    uploadStorageFooter.style.display = 'none';
    uploadStorageSuccessContainer.style.display = 'block';
    setStatus(t('popup.status.uploadSuccess'));
  } catch (error) {
    setStatus(t('popup.status.error', { message: error.message }), true);
    uploadStorageBtn.disabled = false;
    uploadStorageSpinner.style.display = 'none';
    uploadStorageBtnText.textContent = t('popup.uploadStorageView.uploadButton');
    uploadStorageProgressContainer.style.display = 'none';
  }
}

async function handleLaunch() {
  launchBtn.disabled = true;
  spinner.style.display = 'block';
  launchBtnText.textContent = t('popup.launchView.launchingButton');

  if (!selectedAppId) {
    setStatus(t('popup.status.noAppSelected'), true);
    launchBtn.disabled = false;
    spinner.style.display = 'none';
    launchBtnText.textContent = t('popup.launchView.launchButton');
    return;
  }

  const selectedHomeDirValue = homeDirFormGroup.classList.contains('hidden') ? 'cleanroom' : homeDirSelect.value;
  const selectedGpuValue = gpuFormGroup.classList.contains('hidden')
    ? null
    : (gpuSelect.value === 'none' ? null : gpuSelect.value);

  const collaborationMode = document.getElementById('collaborationMode').checked;
  const waylandMode = waylandModeCheckbox.checked;

  const profile = {
    appId: selectedAppId,
    homeDir: selectedHomeDirValue,
    language: languageSelect.value,
    gpu: selectedGpuValue,
    waylandMode,
  };
  if (isSimpleLaunch) {
    await bridge.storageSet({ simple_launch_profile: profile });
  } else if (saveOptionsCheckbox.checked) {
    await bridge.storageSet({ [launchProfileKey]: { ...profile, openFileOnLaunch: openFileCheckbox.checked } });
  }

  try {
    let finalHomeName = selectedHomeDirValue;
    if (selectedHomeDirValue === 'auto' && !homeDirFormGroup.classList.contains('hidden')) {
      const selectedApp = availableApps.find((app) => app.id === selectedAppId);
      if (!selectedApp) throw new Error('Selected app not found for auto home generation.');
      const appNameSanitized = selectedApp.name.toLowerCase().replace(/[\s_]+/g, '-').replace(/[^a-z0-9-]/g, '');
      const autoHomeName = `auto-${appNameSanitized}`;
      if (!homeDirs.includes(autoHomeName) && !selectedApp.is_meta_app) {
        setStatus(t('popup.status.creatingAutoHome'));
        await secureFetch('/api/homedirs', { method: 'POST', body: JSON.stringify({ home_name: autoHomeName }) });
        homeDirs.push(autoHomeName);
      }
      finalHomeName = autoHomeName;
    }

    let endpoint;
    const payload = {
      application_id: selectedAppId,
      home_name: finalHomeName,
      language: languageSelect.value,
      timezone: browserTimezone(),
      selected_gpu: selectedGpuValue,
      launch_in_room_mode: collaborationMode,
      wayland_mode: waylandMode,
    };

    if (isSimpleLaunch || !sealskinContext.action) {
      setStatus(t('popup.status.preparingSession'));
      endpoint = '/api/launch/simple';
    } else if (sealskinContext.action === 'url') {
      setStatus(t('popup.status.preparingSession'));
      endpoint = '/api/launch/url';
      payload.url = sealskinContext.targetUrl;
    } else if (sealskinContext.action === 'file') {
      setStatus(t('popup.status.fetchingData'));
      launchBtnText.textContent = t('popup.launchView.uploadingButton');

      uploadProgressContainer.style.display = 'block';
      const { uploadId, totalChunks, filename } = await uploadContextFile(uploadProgressBar, progressLabel);
      progressLabel.textContent = t('popup.uploadStorageView.uploadComplete');

      endpoint = '/api/launch/file';
      payload.filename = filename;
      payload.upload_id = uploadId;
      payload.total_chunks = totalChunks;
      payload.open_file_on_launch = openFileCheckbox.checked;

      setStatus(t('popup.uploadStorageView.finalizing'));
      launchBtnText.textContent = t('popup.launchView.launchingButton');
    } else if (sealskinContext.action === 'server-file') {
      setStatus(t('popup.status.preparingSession'));
      endpoint = '/api/launch/file_path';
      payload.filename = sealskinContext.filename;
      if (finalHomeName === 'cleanroom') {
        throw new Error("Cannot open a server-side file in 'Cleanroom' mode. Please select a persistent storage directory.");
      }
    } else {
      throw new Error(t('popup.status.unknownAction'));
    }

    const data = await secureFetch(endpoint, { method: 'POST', body: JSON.stringify(payload) });

    await bridge.openSession(data.session_id, data.session_url);

    if (isMobile()) {
      window.location.reload();
    } else {
      bridge.close();
    }
  } catch (error) {
    spinner.style.display = 'none';
    setStatus(t('popup.status.error', { message: error.message }), true);
    launchBtnText.textContent = t('popup.launchView.launchButton');
    launchBtn.disabled = false;
    uploadProgressContainer.style.display = 'none';
  }
}

function applyMobileLayout() {
  const container = document.querySelector('.popup-container');
  const tabs = document.querySelector('.popup-tabs');
  if (container && tabs) {
    const header = document.createElement('div');
    header.className = 'mobile-app-header';
    header.style.paddingTop = 'max(40px, env(safe-area-inset-top))';
    header.style.paddingBottom = '10px';
    header.style.display = 'flex';
    header.style.justifyContent = 'space-between';
    header.style.alignItems = 'center';
    header.innerHTML = `
        <div style="display: flex; align-items: center;">
            <img src="icons/icon128.png" alt="SealSkin" style="height: 32px; margin-right: 10px;">
            <h1>SealSkin</h1>
        </div>
        <button id="mobile-refresh-btn" style="background: none; border: none; color: inherit; font-size: 1.2rem; cursor: pointer; padding: 0 10px;">
            <i class="fas fa-sync-alt"></i>
        </button>
    `;
    container.insertBefore(header, tabs);
    document.getElementById('mobile-refresh-btn').addEventListener('click', () => {
      window.location.reload();
    });
  }

  const footer = document.querySelector('.popup-footer');
  if (footer) {
    const optionsContainer = document.createElement('div');
    optionsContainer.className = 'mobile-footer-options-container';
    const saveOptions = document.getElementById('save-options-container');
    const checkboxes = document.querySelectorAll('.popup-checkbox-item');
    if (saveOptions) optionsContainer.appendChild(saveOptions);
    checkboxes.forEach((cb) => optionsContainer.appendChild(cb));
    const btnGroup = document.querySelector('.button-group-popup');
    if (btnGroup) {
      footer.insertBefore(optionsContainer, btnGroup);
    }
  }
}

async function init() {
  info = await announce();
  t = await loadTranslator(info.locale);

  if (isMobile()) applyMobileLayout();

  applyTranslations(document.body, t);
  document.getElementById('options-gear-btn').addEventListener('click', () => {
    bridge.openPage('options');
    if (!isMobile()) bridge.close();
  });

  try {
    sealskinConfig = info.config || {};
    if (!sealskinConfig.serverIp || !sealskinConfig.username) {
      if (isMobile()) {
        bridge.openPage('connect');
        return;
      }
      setStatus(t('popup.status.unconfigured'), true);
      return;
    }

    const pendingContext = await bridge.getContext();
    if (pendingContext && pendingContext.action) {
      sealskinContext = pendingContext;
      isSimpleLaunch = false;
      if (sealskinContext.action === 'search') {
        const searchEngineBaseUrl = sealskinConfig.searchEngineUrl || 'https://google.com/search?q=';
        sealskinContext.action = 'url';
        sealskinContext.targetUrl = `${searchEngineBaseUrl}${encodeURIComponent(sealskinContext.selectionText)}`;
      }
    } else {
      isSimpleLaunch = true;
    }

    const hasValidExtension = (filename = '') => {
      if (!filename) return false;
      const parts = filename.split('.');
      return parts.length > 1 && parts[parts.length - 1].length > 0;
    };

    const saveOptionsContainer = document.getElementById('save-options-container');
    let savedProfile = null;

    if (isSimpleLaunch) {
      const data = await bridge.storageGet(['simple_launch_profile']);
      savedProfile = data.simple_launch_profile;
      saveOptionsContainer.style.display = 'none';
    } else if (sealskinContext.action === 'url') {
      launchProfileKey = 'workflow_profile_url';
      saveOptionsLabel.textContent = t('popup.launchView.saveOptionsForUrl');
      saveOptionsContainer.style.display = 'block';
    } else if (sealskinContext.action === 'file' || sealskinContext.action === 'server-file') {
      const filename = sealskinContext.filename || '';
      if (hasValidExtension(filename)) {
        const extension = filename.split('.').pop().toLowerCase();
        launchProfileKey = `workflow_profile_.${extension}`;
        saveOptionsLabel.textContent = t('popup.launchView.saveOptionsForFile', { extension });
        saveOptionsContainer.style.display = 'block';
        openFileContainer.style.display = 'flex';
      } else {
        launchProfileKey = 'workflow_profile_simple';
        saveOptionsContainer.style.display = 'none';
      }
    } else {
      saveOptionsContainer.style.display = 'none';
    }

    if (!isSimpleLaunch && saveOptionsContainer.style.display === 'block') {
      const data = await bridge.storageGet([launchProfileKey]);
      savedProfile = data[launchProfileKey];
    }

    const [statusData, appsData, sessionsData] = await Promise.all([
      secureFetch('/api/admin/status', { method: 'POST', body: JSON.stringify({}) }),
      secureFetch('/api/applications', { method: 'POST', body: JSON.stringify({}) }),
      secureFetch('/api/sessions', { method: 'GET' }),
    ]);

    userSettings = statusData.settings;
    availableGpus = statusData.gpus || [];
    availableApps = appsData;
    activeSessions = sessionsData;

    if (activeSessions.length === 0 && !isMobile()) {
      sessionsTabBtn.style.display = 'none';
    }

    populateLanguageDropdown();
    if (userSettings.persistent_storage) {
      const homeDirsData = await secureFetch('/api/homedirs', { method: 'GET' });
      homeDirs = homeDirsData.home_dirs || [];
      populateHomeDirDropdown();
    }

    if (userSettings.persistent_storage && (isSimpleLaunch || isMobile())) {
      manageFilesBtn.style.display = 'flex';
    }

    renderAppCards(availableApps, savedProfile?.appId);

    if (savedProfile) {
      if ([...homeDirSelect.options].some((o) => o.value === savedProfile.homeDir)) homeDirSelect.value = savedProfile.homeDir;
      if ([...languageSelect.options].some((o) => o.value === savedProfile.language)) languageSelect.value = savedProfile.language;
      if (savedProfile.gpu) {
        setTimeout(() => {
          if ([...gpuSelect.options].some((o) => o.value === savedProfile.gpu)) gpuSelect.value = savedProfile.gpu;
        }, 50);
      }
      if (savedProfile.waylandMode !== undefined) {
        waylandModeCheckbox.checked = savedProfile.waylandMode;
      }
      if (!isSimpleLaunch) {
        openFileCheckbox.checked = savedProfile.openFileOnLaunch !== false;
        saveOptionsCheckbox.checked = true;
      }
    }

    if (availableApps.length > 0) {
      launchBtn.disabled = false;
    } else {
      setStatus(t('popup.status.noAppsAvailable'), true);
    }

    const isFileContext = sealskinContext.action === 'file';

    if (isFileContext) {
      if (!isMobile()) uploadFilesTabBtn.style.display = 'none';
      if (userSettings.persistent_storage) {
        uploadStorageTabBtn.style.display = 'flex';
        const filename = sealskinContext.filename;
        uploadStorageDescription.innerHTML = filename
          ? t('popup.uploadStorageView.description', { filename: escapeHtml(filename) })
          : t('popup.uploadStorageView.descriptionFallback');
        uploadStorageBtn.disabled = false;
      }
    } else {
      uploadStorageTabBtn.style.display = 'none';
    }

    renderActiveSessions(isFileContext);

    if (activeSessions.length > 0 && isSimpleLaunch) {
      showView('sessions');
    } else {
      showView('launch');
    }

    setContextualStatus();
  } catch (error) {
    setStatus(t('popup.status.error', { message: error.message }), true);
    showView('launch');
    launchBtn.disabled = true;
  }
}

sessionsTabBtn.addEventListener('click', () => showView('sessions'));
launchTabBtn.addEventListener('click', () => showView('launch'));
manageFilesBtn.addEventListener('click', () => {
  bridge.openPage('files');
  if (!isMobile()) bridge.close();
});
uploadFilesTabBtn.addEventListener('click', () => {
  bridge.openPage('upload');
  if (!isMobile()) bridge.close();
});
uploadStorageTabBtn.addEventListener('click', () => showView('upload-storage'));
uploadStorageBtn.addEventListener('click', handleUploadToStorage);
launchBtn.addEventListener('click', handleLaunch);

sessionsListContainer.addEventListener('click', (e) => {
  const button = e.target.closest('button');
  if (!button) return;
  const card = button.closest('.session-card');
  if (!card) return;

  const sessionId = card.dataset.sessionId;
  const action = button.dataset.action;

  if (action === 'reopen') {
    if (sealskinContext.action === 'file') {
      handleSendFileToSession(sessionId);
    } else {
      const session = activeSessions.find((s) => s.session_id === sessionId);
      if (session) reopenOrFocusSession(session);
    }
  } else if (action === 'close') {
    closeSession(sessionId);
  }
});

appSearchInput.addEventListener('input', (e) => {
  const searchTerm = e.target.value.toLowerCase();
  document.querySelectorAll('.app-card-popup').forEach((card) => {
    const appName = card.querySelector('span').textContent.toLowerCase();
    card.style.display = appName.includes(searchTerm) ? 'flex' : 'none';
  });
});

init();
