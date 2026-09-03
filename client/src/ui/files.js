/**
 * File manager page. Served by the server, framed by a shell; privileged
 * actions (streaming download, native file open, opening the popup) go
 * through the bridge.
 */

import { bridge } from '../lib/bridge.js';
import { secureFetch } from '../lib/api.js';
import { loadTranslator, applyTranslations } from '../lib/i18n.js';
import {
  announce, escapeHtml, formatBytes, timeUntil, formatDate, showToast, downloadBlob,
  addMobileSafeArea, addMobileBackButton,
} from '../lib/dom.js';

let t;
let info;

// --- STATE MANAGEMENT ---
const state = {
  config: null,
  currentHome: null,
  currentPath: '/',
  currentPage: 1,
  perPage: 100,
  selectedItems: new Set(),
  selectedShares: new Set(),
  homeDirs: [],
  currentFileList: [],
  publicShares: [],
  fileToShare: null,
  sharingAllowed: false,
  isLoading: false,
  currentView: 'files',
  uploadQueue: new Map(),
};

// --- DOM ELEMENTS ---
const homeDirNav = document.getElementById('homedir-nav');
const breadcrumbNav = document.getElementById('breadcrumb-nav');
const fileListBody = document.getElementById('file-list-body');
const filesView = document.getElementById('files-view');
const sharesView = document.getElementById('shares-view');
const sharesListBody = document.getElementById('shares-list-body');
const filesSearchInput = document.getElementById('files-search-input');
const sharesSearchInput = document.getElementById('shares-search-input');
const newFolderBtn = document.getElementById('new-folder-btn');
const uploadFileBtn = document.getElementById('upload-file-btn');
const fileUploadInput = document.getElementById('file-upload-input');
const uploadFolderBtn = document.getElementById('upload-folder-btn');
const folderUploadInput = document.getElementById('folder-upload-input');
const selectAllCheckbox = document.getElementById('select-all-check');
const deleteSelectedBtn = document.getElementById('delete-selected-btn');
const deleteSelectedSharesBtn = document.getElementById('delete-selected-shares-btn');
const selectionCountSharesSpan = document.getElementById('selection-count-shares');
const selectAllSharesCheckbox = document.getElementById('select-all-shares-check');
const selectionCountSpan = document.getElementById('selection-count');
const paginationControls = document.getElementById('pagination-controls');
const fileManagerFooter = document.getElementById('file-manager-footer');
const newFolderModal = document.getElementById('new-folder-modal');
const newFolderForm = document.getElementById('new-folder-form');
const confirmDeleteModal = document.getElementById('confirm-delete-modal');
const confirmDeleteBtn = document.getElementById('confirm-delete-btn');
const fileContentArea = document.querySelector('.file-content-area');
const shareFileModal = document.getElementById('share-file-modal');
const shareFileInfo = document.getElementById('share-file-info');
const shareFileForm = document.getElementById('share-file-form');
const dropZoneOverlay = document.getElementById('drop-zone-overlay');
const uploadProgressModal = document.getElementById('upload-progress-modal');
const uploadProgressList = document.getElementById('upload-progress-list');
const uploadModalFooter = document.getElementById('upload-modal-footer');
const closeUploadModalBtn = document.getElementById('close-upload-modal-btn');

const SPECIAL_HOME = '_sealskin_shared_files';

const displayStatus = (message, isError = false) => showToast(message, isError, 5000);

function shareBaseUrl() {
  return `https://${state.config.serverIp}:${state.config.sessionPort}`;
}

// --- API CALLS ---

async function fetchHomeDirs() {
  try {
    const data = await secureFetch('/api/homedirs', { method: 'GET' });
    state.homeDirs = data.home_dirs;
    return state.homeDirs;
  } catch (error) {
    displayStatus(t('files.status.homeDirLoadFailed', { error: error.message }), true);
    return [];
  }
}

async function fetchFiles() {
  if (!state.currentHome || state.isLoading) return;
  setLoading(true);
  try {
    const params = new URLSearchParams({
      path: state.currentPath,
      page: state.currentPage,
      per_page: state.perPage,
    });
    const data = await secureFetch(`/api/files/list/${state.currentHome}?${params.toString()}`, { method: 'GET' });
    state.currentFileList = data.items;
    renderFileList();
    renderPagination(data);
    updateSelectionUI();
  } catch (error) {
    displayStatus(t('files.status.filesLoadFailed', { error: error.message }), true);
    fileListBody.innerHTML = `<tr class="empty-row"><td colspan="4">${t('files.placeholders.errorLoading')}</td></tr>`;
  } finally {
    setLoading(false);
  }
}

async function fetchPublicShares() {
  if (state.isLoading) return;
  setLoading(true, 'shares');
  try {
    const data = await secureFetch('/api/files/shares', { method: 'GET' });
    state.publicShares = data || [];
    renderPublicShares();
  } catch (error) {
    displayStatus(t('files.status.sharesLoadFailed', { error: error.message }), true);
    sharesListBody.innerHTML = `<tr class="empty-row"><td colspan="7">${t('files.placeholders.errorLoading')}</td></tr>`;
  } finally {
    setLoading(false, 'shares');
  }
}

// --- RENDER FUNCTIONS ---

function renderSidebar() {
  homeDirNav.innerHTML = '';

  const normalHomeDirs = state.homeDirs.filter((d) => d !== SPECIAL_HOME).sort();

  const specialBtn = document.createElement('button');
  specialBtn.className = `nav-link special-homedir ${state.currentView === 'files' && state.currentHome === SPECIAL_HOME ? 'active' : ''}`;
  specialBtn.dataset.homedir = SPECIAL_HOME;
  specialBtn.dataset.view = 'files';
  specialBtn.innerHTML = `<i class="fas fa-star fa-fw"></i> <span>${t('files.sidebar.sharedFiles')}</span>`;
  homeDirNav.appendChild(specialBtn);

  normalHomeDirs.forEach((dir) => {
    const button = document.createElement('button');
    button.className = `nav-link ${state.currentView === 'files' && state.currentHome === dir ? 'active' : ''}`;
    button.dataset.homedir = dir;
    button.dataset.view = 'files';
    button.innerHTML = `<i class="fas fa-hdd fa-fw"></i> <span>${escapeHtml(dir)}</span>`;
    homeDirNav.appendChild(button);
  });

  const publicSharesLink = document.querySelector('.nav-link[data-view="shares"]');
  if (publicSharesLink) {
    publicSharesLink.classList.toggle('active', state.currentView === 'shares');
  }
}

function renderBreadcrumbs() {
  const parts = state.currentPath.split('/').filter(Boolean);
  let path = '/';
  breadcrumbNav.innerHTML = `
        <a href="#" class="breadcrumb-item" data-path="/">${t('common.home')}</a>
        ${parts.map((part, index) => {
          path += part + '/';
          const isLast = index === parts.length - 1;
          return `
                <span class="breadcrumb-separator">/</span>
                <a href="#" class="breadcrumb-item ${isLast ? 'active' : ''}" data-path="${escapeHtml(path)}">${escapeHtml(part)}</a>
            `;
        }).join('')}
    `;
}

function renderFileList() {
  const searchTerm = filesSearchInput.value.toLowerCase();
  const items = searchTerm
    ? state.currentFileList.filter((item) => item.name.toLowerCase().includes(searchTerm))
    : state.currentFileList;
  const colSpan = 5;
  if (items.length === 0) {
    fileListBody.innerHTML = `<tr class="empty-row"><td colspan="${colSpan}">${t('files.placeholders.folderEmpty')}</td></tr>`;
    return;
  }
  fileListBody.innerHTML = items.map((item) => {
    const isProtectedPath = state.currentHome !== SPECIAL_HOME
      && (item.path === '/Desktop' || item.path === '/Desktop/files');
    const name = escapeHtml(item.name);
    const path = escapeHtml(item.path);
    let actionButtons = '';
    if (state.currentHome === SPECIAL_HOME && !item.is_dir) {
      actionButtons += `<button class="secondary open-btn" data-filename="${name}"><i class="fas fa-play"></i> ${t('common.open')}</button>`;
    }
    if (!item.is_dir && state.sharingAllowed) {
      actionButtons += `<button class="secondary share-btn" data-path="${path}"><i class="fas fa-share-alt"></i> ${t('common.share')}</button>`;
    }

    return `
        <tr data-path="${path}" data-is-dir="${item.is_dir}" class="${state.selectedItems.has(item.path) ? 'selected' : ''}">
            <td class="col-check"><input type="checkbox" data-path="${path}" ${state.selectedItems.has(item.path) ? 'checked' : ''} ${isProtectedPath ? 'disabled' : ''}></td>
            <td class="col-name">
                <div class="file-item-name">
                    <i class="fas ${item.is_dir ? 'fa-folder' : 'fa-file-alt'}"></i>
                    <span>${name}</span>
                </div>
            </td>
            <td class="col-size">${item.is_dir ? '—' : formatBytes(item.size, t)}</td>
            <td class="col-modified">${formatDate(item.mtime)}</td>
            <td class="col-actions">
                <div class="cell-wrapper">
                    ${actionButtons}
                </div>
            </td>
        </tr>
    `;
  }).join('');
}

function renderPublicShares() {
  const searchTerm = sharesSearchInput.value.toLowerCase();
  const filteredShares = searchTerm
    ? state.publicShares.filter((s) => s.original_filename.toLowerCase().includes(searchTerm) || s.share_id.toLowerCase().includes(searchTerm))
    : state.publicShares;

  if (filteredShares.length === 0) {
    sharesListBody.innerHTML = `<tr class="empty-row"><td colspan="7">${t('files.placeholders.noShares')}</td></tr>`;
    return;
  }

  const baseUrl = shareBaseUrl();

  sharesListBody.innerHTML = filteredShares.map((share) => {
    const fullUrl = escapeHtml(baseUrl + share.url);
    const shareId = escapeHtml(share.share_id);
    return `
            <tr data-share-id="${shareId}" class="${state.selectedShares.has(share.share_id) ? 'selected' : ''}">
                <td class="col-check"><input type="checkbox" data-share-id="${shareId}" ${state.selectedShares.has(share.share_id) ? 'checked' : ''}></td>
                <td class="col-name">
                    <div class="file-item-name">
                        <i class="fas fa-file-alt"></i>
                        <span>${escapeHtml(share.original_filename)}</span>
                    </div>
                </td>
                <td class="col-size">${formatBytes(share.size_bytes, t)}</td>
                <td class="col-date">${formatDate(share.created_at)}</td>
                <td class="col-date">${timeUntil(share.expiry_timestamp, t)}</td>
                <td class="col-password">${share.has_password ? t('common.yes') : t('common.no')}</td>
                <td class="col-url">
                    <button class="secondary copy-url-btn" data-url="${fullUrl}" title="${t('common.copyUrl')}"><i class="fas fa-copy"></i></button>
                </td>
            </tr>
        `;
  }).join('');
}

function renderPagination(data) {
  const { page, per_page, total } = data;
  if (total <= per_page) {
    paginationControls.innerHTML = '';
    fileManagerFooter.style.display = 'none';
    return;
  }
  fileManagerFooter.style.display = 'flex';
  const totalPages = Math.ceil(total / per_page);
  paginationControls.innerHTML = `
        <button class="secondary" data-page="${page - 1}" ${page === 1 ? 'disabled' : ''}>${t('files.pagination.previous')}</button>
        <span class="page-info">${t('files.pagination.pageOf', { page, totalPages })}</span>
        <button class="secondary" data-page="${page + 1}" ${page === totalPages ? 'disabled' : ''}>${t('files.pagination.next')}</button>
    `;
}

function updateSelectionUI() {
  const count = state.selectedItems.size;
  if (count > 0) {
    deleteSelectedBtn.style.display = 'inline-flex';
    selectionCountSpan.textContent = count;
  } else {
    deleteSelectedBtn.style.display = 'none';
  }
  const totalRows = fileListBody.querySelectorAll('tr:not(.empty-row)').length;
  selectAllCheckbox.checked = count > 0 && count === totalRows;
  selectAllCheckbox.indeterminate = count > 0 && count < totalRows;
}

function updateSelectionUIShares() {
  const count = state.selectedShares.size;
  if (count > 0) {
    deleteSelectedSharesBtn.style.display = 'inline-flex';
    selectionCountSharesSpan.textContent = count;
  } else {
    deleteSelectedSharesBtn.style.display = 'none';
  }
  const totalRows = sharesListBody.querySelectorAll('tr:not(.empty-row)').length;
  selectAllSharesCheckbox.checked = count > 0 && count === totalRows;
  selectAllSharesCheckbox.indeterminate = count > 0 && count < totalRows;
}

// --- LOGIC & EVENT HANDLERS ---

function setLoading(isLoading, view = 'files') {
  state.isLoading = isLoading;
  const body = view === 'files' ? fileListBody : sharesListBody;
  const colSpan = view === 'files' ? 5 : 7;
  if (isLoading) {
    body.innerHTML = `<tr><td colspan="${colSpan}"><div class="spinner-container"><div class="spinner-small"></div></div></td></tr>`;
  }
}

function switchView(view, homeDir = null) {
  state.currentView = view;

  filesView.classList.remove('active');
  sharesView.classList.remove('active');

  if (view === 'files') {
    state.currentHome = homeDir || state.currentHome;
    filesView.classList.add('active');
    if (sharesSearchInput.value) sharesSearchInput.value = '';
    changeDirectory('/');
  } else if (view === 'shares') {
    sharesView.classList.add('active');
    fetchPublicShares();
  }
  renderSidebar();
}

function changeDirectory(path) {
  if (path.startsWith('/Desktop/files')) {
    state.currentHome = SPECIAL_HOME;
    renderSidebar();
    const subPath = path.replace('/Desktop/files', '');
    state.currentPath = subPath || '/';
  } else {
    state.currentPath = path;
  }
  state.currentPage = 1;
  state.selectedItems.clear();
  if (filesSearchInput.value) state.fileSearchTerm = '';
  renderBreadcrumbs();
  fetchFiles();
}

function handleItemClick(e) {
  const row = e.target.closest('tr');
  if (!row || row.classList.contains('empty-row')) return;

  const path = row.dataset.path;
  const isDir = row.dataset.isDir === 'true';
  const checkbox = row.querySelector('input[type="checkbox"]');

  if (e.target.closest('.open-btn')) {
    const filename = e.target.closest('.open-btn').dataset.filename;
    bridge.setContext({ action: 'server-file', filename }, true).catch((err) => {
      displayStatus(`Error: ${err.message}`, true);
    });
    return;
  }

  if (e.target.closest('.share-btn')) {
    const filename = row.querySelector('.file-item-name span').textContent;
    state.fileToShare = { home: state.currentHome, path };
    shareFileInfo.innerHTML = t('files.modals.share.sharingFile', { filename: escapeHtml(filename) });
    shareFileModal.style.display = 'block';
    return;
  }

  if (e.target.tagName === 'INPUT' && e.target.type === 'checkbox') return;

  if (e.target.closest('.file-item-name')) {
    if (isDir) {
      changeDirectory(path);
    } else {
      downloadFile(state.currentHome, path);
    }
  } else if (!checkbox.disabled) {
    checkbox.checked = !checkbox.checked;
    checkbox.dispatchEvent(new Event('change', { bubbles: true }));
  }
}

/** Fetch every chunk of a file through the API and assemble a Blob. */
async function fetchFileAsBlob(home, path) {
  let chunkIndex = 0;
  let isLastChunk = false;
  const chunks = [];
  while (!isLastChunk) {
    const params = new URLSearchParams({ path, chunk_index: chunkIndex });
    const response = await secureFetch(`/api/files/download/chunk/${home}?${params.toString()}`, { method: 'GET' });
    if (response.chunk_data_b64) {
      const binaryString = atob(response.chunk_data_b64);
      const bytes = new Uint8Array(binaryString.length);
      for (let i = 0; i < binaryString.length; i++) bytes[i] = binaryString.charCodeAt(i);
      chunks.push(bytes);
    }
    isLastChunk = response.is_last_chunk;
    chunkIndex++;
  }
  return new Blob(chunks, { type: 'application/octet-stream' });
}

async function downloadFile(home, path) {
  const filename = path.split('/').pop();
  const caps = info.capabilities || {};

  if (caps.streamDownload) {
    // Chrome extension: the service worker streams the chunks straight into a download.
    try {
      await bridge.downloadFile(home, path, filename);
    } catch (error) {
      displayStatus(t('files.status.downloadFailed', { error: error.message }), true);
    }
    return;
  }

  displayStatus(t('files.status.downloading', { filename }) || `Downloading ${filename}...`);
  try {
    const blob = await fetchFileAsBlob(home, path);
    if (caps.nativeFileOpen) {
      await bridge.saveBlob(blob, filename);
      displayStatus(t('files.status.downloadSuccess') || 'File opened');
      return;
    }
    downloadBlob(blob, filename);
    displayStatus(t('files.status.downloadSuccess') || 'Download complete');
  } catch (error) {
    displayStatus(t('files.status.downloadFailed', { error: error.message }), true);
  }
}

async function handleDeleteSelected() {
  confirmDeleteModal.style.display = 'none';

  let pathsToDelete = Array.from(state.selectedItems);
  if (state.currentHome !== SPECIAL_HOME) {
    const protectedPaths = ['/Desktop', '/Desktop/files'];
    pathsToDelete = pathsToDelete.filter((path) => !protectedPaths.includes(path));
  }

  if (pathsToDelete.length === 0) {
    if (state.selectedItems.size > 0) displayStatus(t('files.status.deleteProtectedError'), true);
    state.selectedItems.clear();
    updateSelectionUI();
    return;
  }

  displayStatus(t('files.status.deletingItems', { count: pathsToDelete.length }));
  try {
    await secureFetch(`/api/files/delete/${state.currentHome}`, {
      method: 'POST',
      body: JSON.stringify({ paths: pathsToDelete }),
    });
    displayStatus(t('files.status.deleteSuccess'));
    state.selectedItems.clear();
    fetchFiles();
  } catch (error) {
    displayStatus(t('files.status.deleteFailed', { error: error.message }), true);
  }
}

async function handleShareFormSubmit(e) {
  e.preventDefault();
  if (!state.fileToShare) return;

  const password = document.getElementById('share-password').value;
  const expiry = document.getElementById('share-expiry').value;

  const payload = { home_dir: state.fileToShare.home, path: state.fileToShare.path };
  if (password) payload.password = password;
  if (expiry) payload.expiry_hours = parseInt(expiry, 10);

  try {
    const response = await secureFetch('/api/files/share', { method: 'POST', body: JSON.stringify(payload) });
    navigator.clipboard.writeText(`${shareBaseUrl()}${response.url}`);
    displayStatus(t('files.status.shareCreated'));
    switchView('shares');
    sharesSearchInput.value = response.share_id;
    sharesSearchInput.dispatchEvent(new Event('input'));
  } catch (error) {
    displayStatus(t('files.status.shareCreateFailed', { error: error.message }), true);
  } finally {
    shareFileModal.style.display = 'none';
    shareFileForm.reset();
  }
}

async function handleDeleteSelectedShares() {
  const shareIdsToDelete = Array.from(state.selectedShares);
  displayStatus(t('files.status.deletingItems', { count: shareIdsToDelete.length }));
  try {
    await Promise.all(shareIdsToDelete.map((shareId) => secureFetch(`/api/files/share/${shareId}`, { method: 'DELETE' })));
    displayStatus(t('files.status.deleteSuccess'));
    state.selectedShares.clear();
    updateSelectionUIShares();
    await fetchPublicShares();
  } catch (error) {
    displayStatus(t('files.status.deleteFailed', { error: error.message }), true);
  }
}

// --- UPLOAD LOGIC ---
const CHUNK_SIZE = 2 * 1024 * 1024;

async function uploadFile(file, home, path, displayName) {
  const uploadId = `${file.name}-${file.size}-${Date.now()}`;
  state.uploadQueue.set(uploadId, { file, displayName: displayName || file.name, status: 'pending', progress: 0 });
  updateUploadModal();

  try {
    updateUploadStatus(uploadId, 'initiating', 0);
    const { upload_id: serverUploadId } = await secureFetch('/api/upload/initiate', {
      method: 'POST',
      body: JSON.stringify({ filename: file.name, total_size: file.size }),
    });

    const totalChunks = Math.ceil(file.size / CHUNK_SIZE);
    for (let i = 0; i < totalChunks; i++) {
      const chunk = file.slice(i * CHUNK_SIZE, Math.min((i + 1) * CHUNK_SIZE, file.size));
      const chunkDataB64 = await new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(reader.result.split(',')[1]);
        reader.onerror = reject;
        reader.readAsDataURL(chunk);
      });

      await secureFetch('/api/upload/chunk', {
        method: 'POST',
        body: JSON.stringify({ upload_id: serverUploadId, chunk_index: i, chunk_data_b64: chunkDataB64 }),
      });
      const progress = totalChunks > 1 ? Math.round(((i + 1) / totalChunks) * 95) : 95;
      updateUploadStatus(uploadId, 'uploading', progress);
    }

    updateUploadStatus(uploadId, 'finalizing', 98);
    await secureFetch(`/api/files/upload_to_dir/${home}`, {
      method: 'POST',
      body: JSON.stringify({ path, filename: file.name, upload_id: serverUploadId, total_chunks: totalChunks }),
    });

    updateUploadStatus(uploadId, 'completed', 100);
  } catch (error) {
    console.error('Upload error:', error);
    updateUploadStatus(uploadId, 'error', state.uploadQueue.get(uploadId)?.progress || 0);
  } finally {
    checkAllUploadsDone();
  }
}

function updateUploadModal() {
  if (state.uploadQueue.size === 0) {
    uploadProgressModal.style.display = 'none';
    return;
  }
  uploadProgressModal.style.display = 'block';

  const uploads = Array.from(state.uploadQueue.entries());
  const statusOrder = { pending: 0, initiating: 1, uploading: 2, finalizing: 3, completed: 4, error: 5 };
  uploads.sort(([, a], [, b]) => statusOrder[a.status] - statusOrder[b.status]);

  uploadProgressList.innerHTML = uploads.map(([id, upload]) => {
    const statusClass = upload.status === 'error' ? 'error' : (upload.status === 'completed' ? 'success' : '');
    const name = escapeHtml(upload.displayName);
    return `
            <div class="upload-item" data-upload-id="${escapeHtml(id)}">
                <div class="upload-info">
                    <span class="filename" title="${name}">${name}</span>
                    <span class="status ${statusClass}">${t(`files.uploadStatus.${upload.status}`)}</span>
                </div>
                <progress value="${upload.progress}" max="100"></progress>
            </div>
        `;
  }).join('');

  checkAllUploadsDone();
}

function updateUploadStatus(uploadId, status, progress) {
  if (!state.uploadQueue.has(uploadId)) return;
  const upload = state.uploadQueue.get(uploadId);
  upload.status = status;
  upload.progress = progress;

  const itemEl = uploadProgressList.querySelector(`[data-upload-id="${CSS.escape(uploadId)}"]`);
  if (itemEl) {
    itemEl.querySelector('.status').textContent = t(`files.uploadStatus.${status}`);
    itemEl.querySelector('progress').value = progress;
    const statusClass = status === 'error' ? 'error' : (status === 'completed' ? 'success' : '');
    itemEl.querySelector('.status').className = `status ${statusClass}`;
  } else {
    updateUploadModal();
  }
}

function checkAllUploadsDone() {
  const allDone = [...state.uploadQueue.values()].every((u) => u.status === 'completed' || u.status === 'error');
  if (allDone) {
    uploadModalFooter.style.display = 'flex';
    fetchFiles();
  } else {
    uploadModalFooter.style.display = 'none';
  }
}

async function handleFolderUpload(files) {
  if (files.length === 0) return;
  state.uploadQueue.clear();

  displayStatus(t('files.status.preparingUpload', { count: files.length }));

  const creationPromises = new Map();
  const uploadTasks = [];
  const ensureTrailingSlash = (path) => (path.endsWith('/') ? path : path + '/');

  for (const file of files) {
    const task = (async () => {
      const pathParts = file.webkitRelativePath.split('/');
      pathParts.pop();
      const parentPath = pathParts.join('/');

      const normalizedBasePath = ensureTrailingSlash(state.currentPath);
      const serverPathForFile = normalizedBasePath + (parentPath ? parentPath + '/' : '');

      if (parentPath) {
        let cumulativePath = normalizedBasePath;
        for (const part of pathParts) {
          const parentForThisPart = cumulativePath;
          cumulativePath += part + '/';
          const simplePathKey = cumulativePath.replace(normalizedBasePath, '');
          if (!creationPromises.has(simplePathKey)) {
            const promise = secureFetch(`/api/files/create_folder/${state.currentHome}`, {
              method: 'POST',
              body: JSON.stringify({ path: parentForThisPart, folder_name: part }),
            }).catch((e) => {
              if (!e.message.includes('already exists')) throw e;
            });
            creationPromises.set(simplePathKey, promise);
          }
          await creationPromises.get(simplePathKey);
        }
      }

      uploadFile(file, state.currentHome, serverPathForFile, file.webkitRelativePath);
    })();
    uploadTasks.push(task);
  }

  try {
    await Promise.all(uploadTasks);
  } catch (error) {
    displayStatus(t('files.status.uploadPrepFailed', { error: error.message }), true);
  }
}

// --- INITIALIZATION ---

async function init() {
  info = await announce();
  t = await loadTranslator(info.locale);

  if (info.shell === 'mobile') {
    addMobileSafeArea();
    const header = document.querySelector('.sidebar-header');
    if (header) addMobileBackButton(header, () => window.history.back());
  }

  applyTranslations(document.body, t, { html: true });
  document.title = t('files.title');
  document.getElementById('new-folder-name').title = t('files.modals.newFolder.nameTitle');

  const config = info.config;
  if (!config || !config.serverIp) {
    document.body.innerHTML = `<h1>${t('files.status.notConfigured')}</h1>`;
    return;
  }
  state.config = config;

  state.sharingAllowed = config.userSettings?.is_admin || config.userSettings?.public_sharing === true;

  const actionsHeader = document.querySelector('#file-list-table .col-actions');
  if (actionsHeader) {
    actionsHeader.style.display = state.sharingAllowed ? 'table-cell' : 'none';
  }

  if (!state.sharingAllowed) {
    const sharesNavLink = document.querySelector('.nav-link[data-view="shares"]');
    const navContainer = sharesNavLink?.closest('.sidebar-nav');
    if (navContainer) {
      navContainer.style.display = 'none';
      const separator = navContainer.previousElementSibling;
      if (separator && separator.classList.contains('nav-separator')) separator.style.display = 'none';
    }
  }

  const homeDirs = await fetchHomeDirs();
  if (homeDirs.length > 0 || config.username === 'admin') {
    const urlParams = new URLSearchParams(window.location.search);
    const homeFromUrl = urlParams.get('home');
    state.currentHome = homeDirs.includes(homeFromUrl) ? homeFromUrl : (homeDirs.includes('Desktop') ? 'Desktop' : homeDirs[0]);
    switchView('files', state.currentHome);
  } else {
    renderSidebar();
    fileListBody.innerHTML = `<tr class="empty-row"><td colspan="4">${t('files.placeholders.noHomeDirs')}</td></tr>`;
  }

  // Event Listeners
  document.getElementById('homedir-sidebar').addEventListener('click', (e) => {
    const button = e.target.closest('.nav-link');
    if (!button) return;
    if (button.dataset.view === 'files') {
      if (button.dataset.homedir !== state.currentHome || state.currentView !== 'files') {
        switchView('files', button.dataset.homedir);
      }
    } else if (button.dataset.view === 'shares') {
      switchView('shares');
    }
  });

  filesSearchInput.addEventListener('input', renderFileList);

  breadcrumbNav.addEventListener('click', (e) => {
    e.preventDefault();
    const link = e.target.closest('.breadcrumb-item');
    if (link && !link.classList.contains('active')) changeDirectory(link.dataset.path);
  });

  fileListBody.addEventListener('click', handleItemClick);
  fileListBody.addEventListener('change', (e) => {
    if (e.target.type === 'checkbox') {
      const path = e.target.dataset.path;
      const row = e.target.closest('tr');
      if (e.target.checked) {
        state.selectedItems.add(path);
        row.classList.add('selected');
      } else {
        state.selectedItems.delete(path);
        row.classList.remove('selected');
      }
      updateSelectionUI();
    }
  });

  selectAllCheckbox.addEventListener('change', (e) => {
    const isChecked = e.target.checked;
    document.querySelectorAll('#file-list-body input[type="checkbox"]:not(:disabled)').forEach((checkbox) => {
      if (checkbox.checked !== isChecked) {
        checkbox.checked = isChecked;
        checkbox.dispatchEvent(new Event('change', { bubbles: true }));
      }
    });
  });

  sharesListBody.addEventListener('change', (e) => {
    if (e.target.type === 'checkbox') {
      const shareId = e.target.dataset.shareId;
      const row = e.target.closest('tr');
      if (e.target.checked) {
        state.selectedShares.add(shareId);
        row.classList.add('selected');
      } else {
        state.selectedShares.delete(shareId);
        row.classList.remove('selected');
      }
      updateSelectionUIShares();
    }
  });

  sharesListBody.addEventListener('click', (e) => {
    const row = e.target.closest('tr');
    if (!row || row.classList.contains('empty-row')) return;
    const copyBtn = e.target.closest('.copy-url-btn');
    if (copyBtn) {
      navigator.clipboard.writeText(copyBtn.dataset.url);
      displayStatus(t('files.status.urlCopied'));
      return;
    }
    const checkbox = row.querySelector('input[type="checkbox"]');
    if (e.target.tagName !== 'INPUT' && e.target.tagName !== 'BUTTON' && !e.target.closest('button')) {
      checkbox.checked = !checkbox.checked;
      checkbox.dispatchEvent(new Event('change', { bubbles: true }));
    }
  });

  selectAllSharesCheckbox.addEventListener('change', (e) => {
    const isChecked = e.target.checked;
    document.querySelectorAll('#shares-list-body input[type="checkbox"]').forEach((checkbox) => {
      checkbox.checked = isChecked;
      checkbox.dispatchEvent(new Event('change', { bubbles: true }));
    });
  });

  paginationControls.addEventListener('click', (e) => {
    const button = e.target.closest('button');
    if (button && !button.disabled) {
      state.currentPage = parseInt(button.dataset.page, 10);
      fetchFiles();
    }
  });

  newFolderBtn.addEventListener('click', () => { newFolderModal.style.display = 'block'; });
  newFolderForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    const folderName = document.getElementById('new-folder-name').value;
    try {
      await secureFetch(`/api/files/create_folder/${state.currentHome}`, {
        method: 'POST',
        body: JSON.stringify({ path: state.currentPath, folder_name: folderName }),
      });
      displayStatus(t('files.status.folderCreated', { folderName }));
      fetchFiles();
    } catch (error) {
      displayStatus(t('files.status.folderCreateFailed', { error: error.message }), true);
    }
    newFolderModal.style.display = 'none';
    newFolderForm.reset();
  });

  deleteSelectedBtn.addEventListener('click', () => {
    document.getElementById('confirm-delete-message').textContent = t('files.modals.confirmDelete.message', { count: state.selectedItems.size });
    confirmDeleteModal.style.display = 'block';
  });
  confirmDeleteBtn.addEventListener('click', handleDeleteSelected);

  deleteSelectedSharesBtn.addEventListener('click', () => {
    if (confirm(t('files.modals.confirmDelete.message', { count: state.selectedShares.size }))) {
      handleDeleteSelectedShares();
    }
  });

  uploadFileBtn.addEventListener('click', () => fileUploadInput.click());
  fileUploadInput.addEventListener('change', (e) => {
    state.uploadQueue.clear();
    for (const file of e.target.files) uploadFile(file, state.currentHome, state.currentPath);
    e.target.value = '';
  });

  uploadFolderBtn.addEventListener('click', () => folderUploadInput.click());
  folderUploadInput.addEventListener('change', (e) => {
    handleFolderUpload(e.target.files);
    e.target.value = '';
  });

  shareFileForm.addEventListener('submit', handleShareFormSubmit);

  ['dragenter', 'dragover', 'dragleave', 'drop'].forEach((eventName) => {
    fileContentArea.addEventListener(eventName, (e) => {
      e.preventDefault();
      e.stopPropagation();
    });
  });
  ['dragenter', 'dragover'].forEach((eventName) => fileContentArea.addEventListener(eventName, () => dropZoneOverlay.classList.add('visible')));
  ['dragleave', 'drop'].forEach((eventName) => fileContentArea.addEventListener(eventName, () => dropZoneOverlay.classList.remove('visible')));

  fileContentArea.addEventListener('drop', async (e) => {
    const items = e.dataTransfer.items;
    if (!items) return;

    const getFile = (entry) => new Promise((resolve, reject) => entry.file(resolve, reject));

    const traverseFileTree = async (entry, path = '') => {
      const files = [];
      if (entry.isFile) {
        const file = await getFile(entry);
        Object.defineProperty(file, 'webkitRelativePath', { value: path + file.name });
        files.push(file);
      } else if (entry.isDirectory) {
        const dirReader = entry.createReader();
        const allEntries = [];
        const readBatch = () => new Promise((resolve, reject) => {
          dirReader.readEntries((entries) => {
            if (entries.length > 0) {
              allEntries.push(...entries);
              readBatch().then(resolve, reject);
            } else {
              resolve();
            }
          }, reject);
        });
        await readBatch();
        for (const subEntry of allEntries) {
          files.push(...await traverseFileTree(subEntry, path + entry.name + '/'));
        }
      }
      return files;
    };

    const allFiles = (await Promise.all(
      Array.from(items).map((item) => traverseFileTree(item.webkitGetAsEntry())),
    )).flat();

    if (allFiles.length > 0) handleFolderUpload(allFiles);
  });

  document.querySelectorAll('.close-button').forEach((btn) => {
    btn.addEventListener('click', () => { document.getElementById(btn.dataset.modalId).style.display = 'none'; });
  });

  closeUploadModalBtn.addEventListener('click', () => {
    uploadProgressModal.style.display = 'none';
    state.uploadQueue.clear();
  });
  sharesSearchInput.addEventListener('input', renderPublicShares);
}

init();
