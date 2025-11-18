let t;
function applyTranslations(scope, translator) {
  scope.querySelectorAll('[data-i18n]').forEach(el => {
    const key = el.getAttribute('data-i18n');
    el.textContent = translator(key);
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
const optionsArea = document.querySelector('.popup-options-area');
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
const uploadStorageHomeDirSelect = document.getElementById('upload-storage-homedir-select');
const uploadStorageBtn = document.getElementById('upload-storage-btn');
const uploadStorageBtnText = document.getElementById('upload-storage-btn-text');
const uploadStorageSpinner = document.getElementById('upload-storage-spinner');
const uploadStorageProgressContainer = document.getElementById('upload-storage-progress-container');
const uploadStorageProgressBar = document.getElementById('upload-storage-progress');
const uploadStorageProgressLabel = document.getElementById('upload-storage-progress-label');
const uploadStorageSuccessContainer = document.getElementById('upload-storage-success-container');
const uploadStorageFooter = document.getElementById('upload-storage-footer');
const uploadFilenameSpan = document.getElementById('upload-filename');
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

async function formatLogoSrc(logoData) {
  if (!logoData) {
    return 'icons/icon128.png';
  }
  if (logoData.startsWith('http')) {
    return logoData;
  }
  if (logoData.startsWith('/api/app_icon/')) {
    try {
      const response = await secureFetch(logoData, { method: 'GET' });
      if (response && response.icon_data_b64) {
        return `data:image/png;base64,${response.icon_data_b64}`;
      }
    } catch (error) {
      console.error(`Failed to fetch secure icon for ${logoData}:`, error);
    }
  }
  return 'icons/icon128.png';
}

async function getSessionTabMap() {
  const result = await chrome.storage.local.get('sessionTabMap');
  return result.sessionTabMap || {};
}

async function saveSessionTabMap(map) {
  await chrome.storage.local.set({ sessionTabMap: map });
}

function getSessionUrlBase(config) {
  if (!config.serverIp || !config.sessionPort) return null;
  return `https://${config.serverIp}:${config.sessionPort}`;
}

async function secureFetch(url, options = {}) {
  const jwt = await generateJwtNative(sealskinConfig.clientPrivateKey, sealskinConfig.username);
  options.headers = {
    ...options.headers,
    'Authorization': `Bearer ${jwt}`
  };

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
          return reject(new Error(chrome.runtime.lastError.message));
        }
        if (response.success) {
          resolve(response.data);
        } else {
          reject(new Error(response.error));
        }
      }
    );
  });
}

function setStatus(message, isError = false) {
  statusDiv.textContent = message;
  statusDiv.style.color = isError ? 'var(--color-danger)' : 'var(--text-muted)';
}

function setContextualStatus() {
  if (isSimpleLaunch) {
    setStatus('');
    return;
  }

  if (sealskinContext.action === 'file' && sealskinContext.filename) {
    const message = t('popup.status.openingFile', {
      filename: sealskinContext.filename
    });
    setStatus(message);
    statusDiv.title = message;
  } else if (sealskinContext.action === 'url' && sealskinContext.targetUrl) {
    const message = t('popup.status.openingUrl', {
      targetUrl: sealskinContext.targetUrl
    });
    setStatus(message);
    statusDiv.title = message;
  } else if (sealskinContext.action === 'server-file' && sealskinContext.filename) {
    const message = t('popup.status.openingServerFile', {
        filename: sealskinContext.filename
    });
    setStatus(message);
    statusDiv.title = message;
  }
}

function showView(viewName) {
  [launchView, sessionsView, uploadStorageView].forEach(v => v.classList.remove('active'));
  [launchTabBtn, sessionsTabBtn, uploadStorageTabBtn].forEach(b => b.classList.remove('active'));

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


function renderActiveSessions(isFileContext) {
  sessionsListContainer.innerHTML = '';
  if (activeSessions.length === 0) {
    sessionsListContainer.innerHTML = `<p style="text-align: center; color: var(--text-muted); padding: 2rem 0;">${t('popup.sessionsView.noSessions')}</p>`;
    return;
  }

  activeSessions.forEach(session => {
    const card = document.createElement('div');
    card.className = 'session-card';
    card.dataset.sessionId = session.session_id;

    const actionButtonText = isFileContext ? t('common.sendFile') : t('common.reopen');
    const actionButtonClass = isFileContext ? 'secondary' : 'primary';
    const roomIcon = session.is_collaboration ? `<i class="fas fa-users"></i>` : '';

    let contextHtml = '';
    if (session.launch_context) {
        const icon = session.launch_context.type === 'url' ? 'fa-link' : 'fa-file-alt';
        contextHtml = `<div class="session-card-info-action" title="${session.launch_context.value}"><i class="fas ${icon}"></i> ${session.launch_context.value}</div>`;
    }

    card.innerHTML = `
            <img data-logo-src="${session.app_logo}" src="icons/icon128.png" class="session-card-logo">
            <div class="session-card-info">
                <div class="session-card-info-name">${session.app_name} ${roomIcon}</div>
                <div class="session-card-info-time">Started ${timeAgo(session.created_at)}</div>
                ${contextHtml}
            </div>
            <div class="session-card-actions">
                <button class="${actionButtonClass} reopen-btn-text" data-action="reopen">${actionButtonText}</button>
                <button class="danger" data-action="close" title="${t('common.stop')} session"><i class="fas fa-times"></i></button>
            </div>
        `;
    sessionsListContainer.appendChild(card);
  });

  sessionsListContainer.querySelectorAll('img[data-logo-src]').forEach(async (img) => {
      const src = await formatLogoSrc(img.dataset.logoSrc);
      if (src) img.src = src;
  });
}

function renderAppCards(apps, defaultAppId = null) {
  appGridContainer.innerHTML = '';

  let recommendedApps = [];
  let otherApps = [];
  const fileExtension = (sealskinContext.action === 'file' && sealskinContext.filename) ?
    sealskinContext.filename.split('.').pop().toLowerCase() :
    null;

  if (isSimpleLaunch) {
    otherApps = [...apps];
  } else if (sealskinContext.action === 'url') {
    apps.forEach(app => {
      if (app.url_support) recommendedApps.push(app);
      else otherApps.push(app);
    });
  } else if (fileExtension) {
    apps.forEach(app => {
      if (app.extensions.includes(fileExtension)) recommendedApps.push(app);
      else otherApps.push(app);
    });
  } else {
    otherApps = [...apps];
  }

  if (defaultAppId) {
    const moveAppToFront = (arr) => {
      const defaultAppIndex = arr.findIndex(app => app.id === defaultAppId);
      if (defaultAppIndex > -1) {
        const [defaultApp] = arr.splice(defaultAppIndex, 1);
        arr.unshift(defaultApp);
      }
    };
    moveAppToFront(recommendedApps);
    moveAppToFront(otherApps);
  }

  const createCard = (app) => {
    const card = document.createElement('div');
    card.className = 'app-card-popup';
    card.dataset.appid = app.id;
    card.innerHTML = `
            <img data-logo-src="${app.logo}" src="icons/icon128.png" alt="${app.name} logo">
            <span>${app.name}</span>
        `;
    card.addEventListener('click', () => handleAppSelection(app.id));
    formatLogoSrc(app.logo).then(src => {
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
    recommendedApps.forEach(app => grid.appendChild(createCard(app)));
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
    otherApps.forEach(app => grid.appendChild(createCard(app)));
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
  document.querySelectorAll('.app-card-popup').forEach(card => {
    card.classList.toggle('selected', card.dataset.appid === appId);
  });
  updateDynamicForms();
}

function updateDynamicForms() {
  if (!selectedAppId) return;
  const selectedApp = availableApps.find(app => app.id === selectedAppId);
  if (!selectedApp) return;

  const userHasGpu = userSettings.gpu;
  const appHasGpu = selectedApp.nvidia_support || selectedApp.dri3_support;
  if (userHasGpu && appHasGpu && availableGpus.length > 0) {
    const currentGpuVal = gpuSelect.value;
    gpuSelect.innerHTML = `<option value="none">${t('popup.launchView.noGpu')}</option>`;
    availableGpus.forEach(gpu => {
      const isNvidia = gpu.driver === 'nvidia';
      if ((isNvidia && selectedApp.nvidia_support) || (!isNvidia && selectedApp.dri3_support)) {
        const option = document.createElement('option');
        option.value = gpu.device;
        option.textContent = `${gpu.device.split('/').pop()} (${gpu.driver})`;
        gpuSelect.appendChild(option);
      }
    });
    if ([...gpuSelect.options].some(o => o.value === currentGpuVal)) {
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
      if (currentVal === 'auto' || currentVal === 'cleanroom') {
        homeDirSelect.value = currentVal;
      } else {
        homeDirSelect.value = 'auto';
      }
    } else {
      if (homeDirSelect.options.length < 3 && homeDirs.length > 0) {
        populateHomeDirDropdown();
      }
    }
  } else {
    homeDirFormGroup.classList.add('hidden');
  }
}

function populateLanguageDropdown() {
  const browserLang = navigator.language;
  languageSelect.innerHTML = '';

  const defaultOption = document.createElement('option');
  defaultOption.value = 'en_US.UTF-8';
  defaultOption.textContent = t('popup.languages.default', {
    locale: 'en_US'
  });
  languageSelect.appendChild(defaultOption);

  for (const [displayName, value] of Object.entries(supportedLangs)) {
    const option = document.createElement('option');
    option.value = value;
    option.textContent = displayName;
    languageSelect.appendChild(option);
  }

  const langCode = browserLang.split('-')[0].toLowerCase();
  const regionCode = (browserLang.split('-')[1] || '').toUpperCase();

  const exactMatchKey = `${langCode}_${regionCode}.UTF-8`;
  if (Object.values(supportedLangs).includes(exactMatchKey)) {
    languageSelect.value = exactMatchKey;
    return;
  }

  const primaryRegionMap = {
    es: 'ES',
    fr: 'FR',
    pt: 'BR',
    de: 'DE',
    it: 'IT',
    ru: 'RU',
    ja: 'JP',
    ko: 'KR',
    th: 'TH',
    zh: regionCode === 'TW' || regionCode === 'HK' ? regionCode : 'CN'
  };
  const primaryRegion = primaryRegionMap[langCode];
  if (primaryRegion) {
    const primaryMatchKey = `${langCode}_${primaryRegion}.UTF-8`;
    if (Object.values(supportedLangs).includes(primaryMatchKey)) {
      languageSelect.value = primaryMatchKey;
      return;
    }
  }

  const langPrefix = `${langCode}_`;
  const firstAvailable = Object.values(supportedLangs).find(val => val.startsWith(langPrefix));
  if (firstAvailable) {
    languageSelect.value = firstAvailable;
    return;
  }

  languageSelect.value = "en_US.UTF-8";
}

function populateHomeDirDropdown() {
  homeDirSelect.innerHTML = `
    <option value="auto">${t('popup.launchView.autoHome')}</option>
    <option value="cleanroom">${t('popup.launchView.cleanroom')}</option>
  `;
  const optionsHtml = homeDirs
    .filter(dir => dir !== '_sealskin_shared_files' && !dir.startsWith('auto-'))
    .map(dir => `<option value="${dir}">${dir}</option>`)
    .join('');
  homeDirSelect.insertAdjacentHTML('beforeend', optionsHtml);
}

async function reopenOrFocusSession(session) {
  chrome.runtime.sendMessage({ type: 'focusOrCreateTab', payload: { session } });
  window.close();
}

async function closeSession(sessionId) {
  const card = sessionsListContainer.querySelector(`[data-session-id="${sessionId}"]`);
  if (!card) return;

  const closeButton = card.querySelector('[data-action="close"]');

  closeButton.disabled = true;
  closeButton.innerHTML = `<i class="fas fa-spinner fa-spin"></i>`;

  chrome.runtime.sendMessage({ type: 'closeSession', payload: { sessionId } }, (response) => {

    if (chrome.runtime.lastError || !response || !response.success) {
      console.error('Failed to close session in background:', chrome.runtime.lastError || response.error);
      setStatus(t('popup.status.errorClosingSession', { message: chrome.runtime.lastError?.message || response?.error || 'Unknown error' }), true);

      closeButton.disabled = false;
      closeButton.innerHTML = `<i class="fas fa-times"></i>`;

    } else {
      card.remove();
      activeSessions = activeSessions.filter(s => s.session_id !== sessionId);

      if (activeSessions.length === 0) {
        renderActiveSessions(sealskinContext.action === 'file');
      }
    }
  });
}

const CHUNK_SIZE = 1 * 1024 * 1024;

function readBlobAsBase64(blob) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result.split(',')[1]);
    reader.onerror = (error) => reject(error);
    reader.readAsDataURL(blob);
  });
}

async function uploadFileInChunks(fileBlob, filename) {
  const {
    upload_id
  } = await secureFetch('/api/upload/initiate', {
    method: 'POST',
    body: JSON.stringify({
      filename: filename,
      total_size: fileBlob.size
    })
  });

  uploadProgressContainer.style.display = 'block';

  const totalChunks = Math.ceil(fileBlob.size / CHUNK_SIZE);
  for (let i = 0; i < totalChunks; i++) {
    const start = i * CHUNK_SIZE;
    const end = Math.min(start + CHUNK_SIZE, fileBlob.size);
    const chunk = fileBlob.slice(start, end);

    const chunkDataB64 = await readBlobAsBase64(chunk);

    progressLabel.textContent = t('popup.uploadStorageView.uploadingChunk', {
      current: i + 1,
      total: totalChunks
    });

    await secureFetch('/api/upload/chunk', {
      method: 'POST',
      body: JSON.stringify({
        upload_id: upload_id,
        chunk_index: i,
        chunk_data_b64: chunkDataB64
      })
    });

    uploadProgressBar.value = ((i + 1) / totalChunks) * 100;
  }

  progressLabel.textContent = t('popup.uploadStorageView.uploadComplete');

  return {
    uploadId: upload_id,
    totalChunks: totalChunks
  };
}

async function handleSendFileToSession(sessionId) {
  setStatus(t('popup.status.preparingFile'));
  try {
    const fileResponse = await fetch(sealskinContext.targetUrl);
    if (!fileResponse.ok) throw new Error(t('popup.status.fetchDataFailed', {
      statusText: fileResponse.statusText
    }));
    const fileBlob = await fileResponse.blob();
    const filename = sealskinContext.filename || 'uploaded.file';

    const {
      uploadId,
      totalChunks
    } = await uploadFileInChunks(fileBlob, filename);

    setStatus(t('popup.status.sendingFile'));
    await secureFetch(`/api/sessions/${sessionId}/send_file`, {
      method: 'POST',
      body: JSON.stringify({
        filename,
        upload_id: uploadId,
        total_chunks: totalChunks
      }),
    });

    const session = activeSessions.find(s => s.session_id === sessionId);
    reopenOrFocusSession(session);

  } catch (error) {
    setStatus(t('popup.status.errorSendingFile', {
      message: error.message
    }), true);
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
    const fileResponse = await fetch(sealskinContext.targetUrl);
    if (!fileResponse.ok) throw new Error(t('popup.status.fetchDataFailed', {
      statusText: fileResponse.statusText
    }));
    const fileBlob = await fileResponse.blob();
    const filename = sealskinContext.filename || 'uploaded.file';
    const homeName = '_sealskin_shared_files';

    const {
      upload_id
    } = await secureFetch('/api/upload/initiate', {
      method: 'POST',
      body: JSON.stringify({
        filename: filename,
        total_size: fileBlob.size
      })
    });

    const totalChunks = Math.ceil(fileBlob.size / CHUNK_SIZE);
    for (let i = 0; i < totalChunks; i++) {
      const start = i * CHUNK_SIZE;
      const end = Math.min(start + CHUNK_SIZE, fileBlob.size);
      const chunk = fileBlob.slice(start, end);
      const chunkDataB64 = await readBlobAsBase64(chunk);

      uploadStorageProgressLabel.textContent = t('popup.uploadStorageView.uploadingChunk', {
        current: i + 1,
        total: totalChunks
      });

      await secureFetch('/api/upload/chunk', {
        method: 'POST',
        body: JSON.stringify({
          upload_id: upload_id,
          chunk_index: i,
          chunk_data_b64: chunkDataB64
        })
      });

      uploadStorageProgressBar.value = ((i + 1) / totalChunks) * 100;
    }

    uploadStorageProgressLabel.textContent = t('popup.uploadStorageView.finalizing');

    await secureFetch('/api/upload/to_storage', {
      method: 'POST',
      body: JSON.stringify({
        filename: filename,
        upload_id: upload_id,
        total_chunks: totalChunks,
        home_name: homeName
      })
    });

    uploadStorageProgressContainer.style.display = 'none';
    uploadStorageFooter.style.display = 'none';
    uploadStorageSuccessContainer.style.display = 'block';
    setStatus(t('popup.status.uploadSuccess'));

  } catch (error) {
    setStatus(t('popup.status.error', {
      message: error.message
    }), true);
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

  const selectedHomeDirValue = homeDirFormGroup.classList.contains('hidden') ?
    'cleanroom' :
    homeDirSelect.value;

  const selectedGpuValue = gpuFormGroup.classList.contains('hidden') ?
    null :
    (gpuSelect.value === 'none' ? null : gpuSelect.value);

  const collaborationMode = document.getElementById('collaborationMode').checked;

  if (isSimpleLaunch) {
    const simpleLaunchOptions = {
      appId: selectedAppId,
      homeDir: selectedHomeDirValue,
      language: languageSelect.value,
      gpu: selectedGpuValue,
    };
    await chrome.storage.local.set({ 'simple_launch_profile': simpleLaunchOptions });
  } else if (saveOptionsCheckbox.checked) {
    const pinnedProfile = {
      appId: selectedAppId,
      homeDir: selectedHomeDirValue,
      language: languageSelect.value,
      gpu: selectedGpuValue,
      openFileOnLaunch: openFileCheckbox.checked,
    };
    await chrome.storage.local.set({ [launchProfileKey]: pinnedProfile });
  }

  try {
    let finalHomeName = selectedHomeDirValue;
    if (selectedHomeDirValue === 'auto' && !homeDirFormGroup.classList.contains('hidden')) {
        const selectedApp = availableApps.find(app => app.id === selectedAppId);
        if (!selectedApp) throw new Error("Selected app not found for auto home generation.");
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
    let payload = {
      application_id: selectedAppId,
      home_name: finalHomeName,
      language: languageSelect.value,
      selected_gpu: selectedGpuValue,
      launch_in_room_mode: collaborationMode,
    };

    if (isSimpleLaunch) {
      setStatus(t('popup.status.preparingSession'));
      endpoint = '/api/launch/simple';
    } else if (sealskinContext.action === 'url') {
      setStatus(t('popup.status.preparingSession'));
      endpoint = '/api/launch/url';
      payload.url = sealskinContext.targetUrl;
    } else if (sealskinContext.action === 'file') {
      setStatus(t('popup.status.fetchingData'));
      launchBtnText.textContent = t('popup.launchView.uploadingButton');

      const fileResponse = await fetch(sealskinContext.targetUrl);
      if (!fileResponse.ok) throw new Error(t('popup.status.fetchDataFailed', {
        statusText: fileResponse.statusText
      }));
      const fileBlob = await fileResponse.blob();
      const filename = sealskinContext.filename || 'uploaded.file';

      const {
        uploadId,
        totalChunks
      } = await uploadFileInChunks(fileBlob, filename);

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

    const data = await secureFetch(endpoint, {
      method: 'POST',
      body: JSON.stringify(payload),
    });

    const sessionId = data.session_id;
    chrome.runtime.sendMessage({
      type: 'createTabAndTrack',
      payload: { sessionId: sessionId, session_url: data.session_url }
    });

    window.close();

  } catch (error) {
    spinner.style.display = 'none';
    setStatus(t('popup.status.error', {
      message: error.message
    }), true);
    launchBtnText.textContent = t('popup.launchView.launchButton');
    launchBtn.disabled = false;
    uploadProgressContainer.style.display = 'none';
  }
}

document.addEventListener('DOMContentLoaded', async () => {
  const translator = getTranslator(navigator.language);
  t = translator.t;
  applyTranslations(document.body, t);

  document.getElementById('options-gear-btn').addEventListener('click', () => {
      chrome.runtime.openOptionsPage();
  });

  const themeData = await chrome.storage.local.get('theme');
  document.documentElement.setAttribute('data-theme', themeData.theme || 'dark');

  try {
    const configData = await chrome.storage.local.get('sealskinConfig');
    if (!configData.sealskinConfig?.serverIp || !configData.sealskinConfig?.username || !configData.sealskinConfig?.clientPrivateKey) {
      setStatus(t('popup.status.unconfigured'), true);
      return;
    }
    sealskinConfig = configData.sealskinConfig;

    const contextData = await chrome.storage.local.get('sealskinContext');
    if (contextData.sealskinContext) {
      sealskinContext = contextData.sealskinContext;
      chrome.storage.local.remove('sealskinContext');
      isSimpleLaunch = false;
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
      const data = await chrome.storage.local.get('simple_launch_profile');
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
        saveOptionsLabel.textContent = t('popup.launchView.saveOptionsForFile', {
          extension
        });
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
        const data = await chrome.storage.local.get(launchProfileKey);
        savedProfile = data[launchProfileKey];
    }

    const [statusData, appsData, sessionsData] = await Promise.all([
      secureFetch('/api/admin/status', {
        method: 'POST',
        body: JSON.stringify({})
      }),
      secureFetch('/api/applications', {
        method: 'POST',
        body: JSON.stringify({})
      }),
      secureFetch('/api/sessions', {
        method: 'GET'
      })
    ]);

    userSettings = statusData.settings;
    availableGpus = statusData.gpus || [];
    availableApps = appsData;
    activeSessions = sessionsData;
    const sessionTabMap = await getSessionTabMap();
    const activeSessionIds = new Set(activeSessions.map(s => s.session_id));
    let mapWasChanged = false;
    for (const storedSessionId in sessionTabMap) {
      if (!activeSessionIds.has(storedSessionId)) {
        delete sessionTabMap[storedSessionId];
        mapWasChanged = true;
      }
    }
    if (mapWasChanged) await saveSessionTabMap(sessionTabMap);

    if (activeSessions.length === 0) {
      sessionsTabBtn.style.display = 'none';
    }

    populateLanguageDropdown();
    if (userSettings.persistent_storage) {
      const homeDirsData = await secureFetch('/api/homedirs', {
        method: 'GET'
      });
      homeDirs = homeDirsData.home_dirs || [];
      populateHomeDirDropdown();
    }

    if (userSettings.persistent_storage && isSimpleLaunch) {
        manageFilesBtn.style.display = 'flex';
    }

    renderAppCards(availableApps, savedProfile?.appId);

    if (savedProfile) {
      if ([...homeDirSelect.options].some(o => o.value === savedProfile.homeDir)) homeDirSelect.value = savedProfile.homeDir;
      if ([...languageSelect.options].some(o => o.value === savedProfile.language)) languageSelect.value = savedProfile.language;
      if (savedProfile.gpu) {
        setTimeout(() => {
          if ([...gpuSelect.options].some(o => o.value === savedProfile.gpu)) gpuSelect.value = savedProfile.gpu;
        }, 50);
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
      uploadFilesTabBtn.style.display = 'none';
      if (userSettings.persistent_storage) {
        uploadStorageTabBtn.style.display = 'flex';
        const filename = sealskinContext.filename;
        if (filename) {
          uploadStorageDescription.innerHTML = t('popup.uploadStorageView.description', {
            filename
          });
        } else {
          uploadStorageDescription.innerHTML = t('popup.uploadStorageView.descriptionFallback');
        }
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
    setStatus(t('popup.status.error', {
      message: error.message
    }), true);
    showView('launch');
    launchBtn.disabled = true;
  }
});

sessionsTabBtn.addEventListener('click', () => showView('sessions'));
launchTabBtn.addEventListener('click', () => showView('launch'));
manageFilesBtn.addEventListener('click', () => {
    chrome.tabs.create({ url: 'files.html' });
    window.close();
});

uploadFilesTabBtn.addEventListener('click', () => {
  chrome.tabs.create({
    url: 'upload.html'
  });
  window.close();
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
      const session = activeSessions.find(s => s.session_id === sessionId);
      if (session) reopenOrFocusSession(session);
    }
  } else if (action === 'close') {
    button.disabled = true;
    button.innerHTML = `<i class="fas fa-spinner fa-spin"></i>`;
    closeSession(sessionId);
  }
});

appSearchInput.addEventListener('input', (e) => {
  const searchTerm = e.target.value.toLowerCase();
  document.querySelectorAll('.app-card-popup').forEach(card => {
    const appName = card.querySelector('span').textContent.toLowerCase();
    card.style.display = appName.includes(searchTerm) ? 'flex' : 'none';
  });
});
