let t;

function applyTranslations(scope, translator) {
  scope.querySelectorAll('[data-i18n]').forEach(el => {
    const key = el.getAttribute('data-i18n');
    el.innerHTML = translator(key);
  });
}

// --- STATE MANAGEMENT ---
let state = {
  config: null,
  currentHome: null,
  currentPath: '/',
  currentPage: 1,
  perPage: 100,
  selectedItems: new Set(),
  homeDirs: [],
  isLoading: false,
  uploadQueue: new Map()
};

// --- DOM ELEMENTS ---
const homeDirNav = document.getElementById('homedir-nav');
const breadcrumbNav = document.getElementById('breadcrumb-nav');
const fileListBody = document.getElementById('file-list-body');
const newFolderBtn = document.getElementById('new-folder-btn');
const uploadFileBtn = document.getElementById('upload-file-btn');
const fileUploadInput = document.getElementById('file-upload-input');
const uploadFolderBtn = document.getElementById('upload-folder-btn');
const folderUploadInput = document.getElementById('folder-upload-input');
const selectAllCheckbox = document.getElementById('select-all-check');
const deleteSelectedBtn = document.getElementById('delete-selected-btn');
const selectionCountSpan = document.getElementById('selection-count');
const paginationControls = document.getElementById('pagination-controls');
const fileManagerFooter = document.getElementById('file-manager-footer');
const newFolderModal = document.getElementById('new-folder-modal');
const newFolderForm = document.getElementById('new-folder-form');
const confirmDeleteModal = document.getElementById('confirm-delete-modal');
const confirmDeleteBtn = document.getElementById('confirm-delete-btn');
const fileContentArea = document.querySelector('.file-content-area');
const dropZoneOverlay = document.getElementById('drop-zone-overlay');
const uploadProgressModal = document.getElementById('upload-progress-modal');
const uploadProgressList = document.getElementById('upload-progress-list');
const uploadModalFooter = document.getElementById('upload-modal-footer');
const closeUploadModalBtn = document.getElementById('close-upload-modal-btn');

// --- UTILS & HELPERS ---

async function secureFetch(url, options = {}) {
  if (!state.config) throw new Error("Configuration not loaded.");
  const jwt = await generateJwtNative(state.config.clientPrivateKey, state.config.username);
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
    }, (response) => {
      if (chrome.runtime.lastError) return reject(new Error(chrome.runtime.lastError.message));
      if (response.success) {
        if ((options.method === 'DELETE' || (options.method === 'POST' && response.data === null)) && response.data === null) {
          resolve({});
        } else {
          resolve(response.data);
        }
      } else {
        reject(new Error(response.error));
      }
    });
  });
}

function formatBytes(bytes, decimals = 2) {
  if (!bytes || bytes === 0) return '0 ' + t('common.bytes');
  const k = 1024;
  const dm = decimals < 0 ? 0 : decimals;
  const sizes = [t('common.bytes'), t('common.kb'), t('common.mb'), t('common.gb'), t('common.tb'), t('common.pb')];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return parseFloat((bytes / Math.pow(k, i)).toFixed(dm)) + ' ' + sizes[i];
}

function formatDate(timestamp) {
  return new Date(timestamp * 1000).toLocaleString(undefined, {
    dateStyle: 'medium',
    timeStyle: 'short'
  });
}

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
  }, 5000);
}

// --- API CALLS ---

async function fetchHomeDirs() {
  try {
    const data = await secureFetch('/api/homedirs', {
      method: 'GET'
    });
    state.homeDirs = data.home_dirs;
    return state.homeDirs;
  } catch (error) {
    displayStatus(t('files.status.homeDirLoadFailed', {
      error: error.message
    }), true);
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
      per_page: state.perPage
    });
    const data = await secureFetch(`/api/files/list/${state.currentHome}?${params.toString()}`, {
      method: 'GET'
    });
    renderFileList(data.items);
    renderPagination(data);
    updateSelectionUI();
  } catch (error) {
    displayStatus(t('files.status.filesLoadFailed', {
      error: error.message
    }), true);
    fileListBody.innerHTML = `<tr class="empty-row"><td colspan="4">${t('files.placeholders.errorLoading')}</td></tr>`;
  } finally {
    setLoading(false);
  }
}

// --- RENDER FUNCTIONS ---

function renderSidebar() {
  homeDirNav.innerHTML = '';

  const specialHomeDir = '_sealskin_shared_files';
  const sortedHomeDirs = [specialHomeDir, ...state.homeDirs.filter(d => d !== specialHomeDir).sort()];

  sortedHomeDirs.forEach(dir => {
    const isSpecial = dir === specialHomeDir;
    const button = document.createElement('button');
    button.className = `nav-link ${isSpecial ? 'special-homedir' : ''} ${dir === state.currentHome ? 'active' : ''}`;
    button.dataset.homedir = dir;
    button.innerHTML = `
            <i class="fas ${isSpecial ? 'fa-star' : 'fa-hdd'} fa-fw"></i>
            <span>${isSpecial ? t('files.sidebar.sharedFiles') : dir}</span>
        `;
    homeDirNav.appendChild(button);
  });
}


function renderBreadcrumbs() {
  const parts = state.currentPath.split('/').filter(Boolean);
  let path = '/';
  const breadcrumbHtml = `
        <a href="#" class="breadcrumb-item" data-path="/">${t('common.home')}</a>
        ${parts.map((part, index) => {
            path += part + '/';
            const isLast = index === parts.length - 1;
            return `
                <span class="breadcrumb-separator">/</span>
                <a href="#" class="breadcrumb-item ${isLast ? 'active' : ''}" data-path="${path}">${part}</a>
            `;
        }).join('')}
    `;
  breadcrumbNav.innerHTML = breadcrumbHtml;
}

function renderFileList(items) {
  if (items.length === 0) {
    fileListBody.innerHTML = `<tr class="empty-row"><td colspan="4">${t('files.placeholders.folderEmpty')}</td></tr>`;
    return;
  }

  const specialSharedHome = '_sealskin_shared_files';

  fileListBody.innerHTML = items.map(item => {
    const isProtectedPath = state.currentHome !== specialSharedHome &&
      (item.path === '/Desktop' || item.path === '/Desktop/files');
    return `
        <tr data-path="${item.path}" data-is-dir="${item.is_dir}" class="${state.selectedItems.has(item.path) ? 'selected' : ''}">
            <td class="col-check"><input type="checkbox" data-path="${item.path}" ${state.selectedItems.has(item.path) ? 'checked' : ''} ${isProtectedPath ? 'disabled' : ''}></td>
            <td class="col-name">
                <div class="file-item-name">
                    <i class="fas ${item.is_dir ? 'fa-folder' : 'fa-file-alt'}"></i>
                    <span>${item.name}</span>
                </div>
            </td>
            <td class="col-size">${item.is_dir ? '—' : formatBytes(item.size)}</td>
            <td class="col-modified">${formatDate(item.mtime)}</td>
        </tr>
    `
  }).join('');
}

function renderPagination(data) {
  const {
    page,
    per_page,
    total
  } = data;
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

// --- LOGIC & EVENT HANDLERS ---

function setLoading(isLoading) {
  state.isLoading = isLoading;
  if (isLoading) {
    fileListBody.innerHTML = `<tr><td colspan="4"><div class="spinner-container"><div class="spinner-small"></div></div></td></tr>`;
  }
}

function changeDirectory(path) {
  if (path.startsWith('/Desktop/files')) {
    state.currentHome = '_sealskin_shared_files';
    renderSidebar();
    const subPath = path.replace('/Desktop/files', '');
    state.currentPath = subPath || '/';
  } else {
    state.currentPath = path;
  }
  state.currentPage = 1;
  state.selectedItems.clear();
  renderBreadcrumbs();
  fetchFiles();
}

function handleItemClick(e) {
  const row = e.target.closest('tr');
  if (!row || row.classList.contains('empty-row')) return;

  const path = row.dataset.path;
  const isDir = row.dataset.isDir === 'true';
  const checkbox = row.querySelector('input[type="checkbox"]');

  if (e.target.tagName === 'INPUT' && e.target.type === 'checkbox') {
    return;
  }

  if (e.target.closest('.file-item-name')) {
    if (isDir) {
      changeDirectory(path);
    } else {
      downloadFile(state.currentHome, path);
    }
  } else {
    if (!checkbox.disabled) {
      checkbox.checked = !checkbox.checked;
      checkbox.dispatchEvent(new Event('change', {
        bubbles: true
      }));
    }
  }
}

async function downloadFile(home, path) {
  const filename = path.split('/').pop();
  const params = new URLSearchParams({
    home: home,
    path: path,
    filename: filename
  });
  const downloadUrl = chrome.runtime.getURL(`/download-stream?${params.toString()}`);
  const a = document.createElement('a');
  a.href = downloadUrl;
  document.body.appendChild(a);
  a.click();
  a.remove();
}

async function handleDeleteSelected() {
  confirmDeleteModal.style.display = 'none';

  const specialSharedHome = '_sealskin_shared_files';
  let pathsToDelete = Array.from(state.selectedItems);

  if (state.currentHome !== specialSharedHome) {
    const protectedPaths = ['/Desktop', '/Desktop/files'];
    pathsToDelete = pathsToDelete.filter(path => !protectedPaths.includes(path));
  }

  if (pathsToDelete.length === 0) {
    if (state.selectedItems.size > 0) {
      displayStatus(t('files.status.deleteProtectedError'), true);
    }
    state.selectedItems.clear();
    updateSelectionUI();
    return;
  }

  displayStatus(t('files.status.deletingItems', {
    count: pathsToDelete.length
  }));
  try {
    await secureFetch(`/api/files/delete/${state.currentHome}`, {
      method: 'POST',
      body: JSON.stringify({
        paths: pathsToDelete
      })
    });
    displayStatus(t('files.status.deleteSuccess'));
    state.selectedItems.clear();
    fetchFiles();
  } catch (error) {
    displayStatus(t('files.status.deleteFailed', {
      error: error.message
    }), true);
  }
}

// --- UPLOAD LOGIC ---
const CHUNK_SIZE = 2 * 1024 * 1024;

async function uploadFile(file, home, path, displayName) {
  const uploadId = `${file.name}-${file.size}-${Date.now()}`;
  state.uploadQueue.set(uploadId, {
    file,
    displayName: displayName || file.name,
    status: 'pending',
    progress: 0
  });
  updateUploadModal();

  try {
    updateUploadStatus(uploadId, 'initiating', 0);
    const {
      upload_id: serverUploadId
    } = await secureFetch('/api/upload/initiate', {
      method: 'POST',
      body: JSON.stringify({
        filename: file.name,
        total_size: file.size
      })
    });

    const totalChunks = Math.ceil(file.size / CHUNK_SIZE);
    for (let i = 0; i < totalChunks; i++) {
      const start = i * CHUNK_SIZE;
      const end = Math.min(start + CHUNK_SIZE, file.size);
      const chunk = file.slice(start, end);
      const chunkDataB64 = await new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(reader.result.split(',')[1]);
        reader.onerror = reject;
        reader.readAsDataURL(chunk);
      });

      await secureFetch('/api/upload/chunk', {
        method: 'POST',
        body: JSON.stringify({
          upload_id: serverUploadId,
          chunk_index: i,
          chunk_data_b64: chunkDataB64
        })
      });
      const progress = totalChunks > 1 ? Math.round(((i + 1) / totalChunks) * 95) : 95;
      updateUploadStatus(uploadId, `uploading`, progress);
    }

    updateUploadStatus(uploadId, 'finalizing', 98);
    await secureFetch(`/api/files/upload_to_dir/${home}`, {
      method: 'POST',
      body: JSON.stringify({
        path: path,
        filename: file.name,
        upload_id: serverUploadId,
        total_chunks: totalChunks
      })
    });

    updateUploadStatus(uploadId, 'completed', 100);
  } catch (error) {
    console.error("Upload error:", error);
    updateUploadStatus(uploadId, `error`, state.uploadQueue.get(uploadId)?.progress || 0);
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
  const statusOrder = {
    'pending': 0,
    'initiating': 1,
    'uploading': 2,
    'finalizing': 3,
    'completed': 4,
    'error': 5
  };
  uploads.sort(([, a], [, b]) => statusOrder[a.status] - statusOrder[b.status]);

  uploadProgressList.innerHTML = uploads.map(([id, upload]) => {
    const statusClass = upload.status === 'error' ? 'error' : (upload.status === 'completed' ? 'success' : '');
    return `
            <div class="upload-item" data-upload-id="${id}">
                <div class="upload-info">
                    <span class="filename" title="${upload.displayName}">${upload.displayName}</span>
                    <span class="status ${statusClass}">${t(`files.uploadStatus.${upload.status}`)}</span>
                </div>
                <progress value="${upload.progress}" max="100"></progress>
            </div>
        `;
  }).join('');

  checkAllUploadsDone();
}

function updateUploadStatus(uploadId, status, progress) {
  if (state.uploadQueue.has(uploadId)) {
    const upload = state.uploadQueue.get(uploadId);
    upload.status = status;
    upload.progress = progress;

    const itemEl = uploadProgressList.querySelector(`[data-upload-id="${uploadId}"]`);
    if (itemEl) {
      itemEl.querySelector('.status').textContent = t(`files.uploadStatus.${status}`);
      itemEl.querySelector('progress').value = progress;
      const statusClass = status === 'error' ? 'error' : (status === 'completed' ? 'success' : '');
      itemEl.querySelector('.status').className = `status ${statusClass}`;
    } else {
      updateUploadModal();
    }
  }
}

function checkAllUploadsDone() {
  const allDone = [...state.uploadQueue.values()].every(u => u.status === 'completed' || u.status === 'error');
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

  displayStatus(t('files.status.preparingUpload', {
    count: files.length
  }));

  const creationPromises = new Map();
  const uploadTasks = [];

  const ensureTrailingSlash = (path) => path.endsWith('/') ? path : path + '/';

  for (const file of files) {
    const task = (async () => {
      const pathParts = file.webkitRelativePath.split('/');
      const fileName = pathParts.pop();
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
              body: JSON.stringify({
                path: parentForThisPart,
                folder_name: part
              })
            }).catch(e => {
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
    displayStatus(t('files.status.uploadPrepFailed', {
      error: error.message
    }), true);
  }
}

// --- INITIALIZATION ---

async function init() {
  const translator = getTranslator(navigator.language);
  t = translator.t;
  applyTranslations(document.body, t);
  document.title = t('files.title');
  document.getElementById('new-folder-name').title = t('files.modals.newFolder.nameTitle');


  const currentTheme = localStorage.getItem('theme');
  document.documentElement.setAttribute('data-theme', currentTheme || (window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'));

  const {
    sealskinConfig
  } = await chrome.storage.local.get('sealskinConfig');
  if (!sealskinConfig) {
    document.body.innerHTML = `<h1>${t('files.status.notConfigured')}</h1>`;
    return;
  }
  state.config = sealskinConfig;

  const homeDirs = await fetchHomeDirs();
  if (homeDirs.length > 0 || state.config.username === 'admin') {
    const urlParams = new URLSearchParams(window.location.search);
    const homeFromUrl = urlParams.get('home');
    state.currentHome = homeDirs.includes(homeFromUrl) ? homeFromUrl : '_sealskin_shared_files';

    renderSidebar();
    changeDirectory('/');
  } else {
    renderSidebar();
    fileListBody.innerHTML = `<tr class="empty-row"><td colspan="4">${t('files.placeholders.noHomeDirs')}</td></tr>`;
  }

  // Event Listeners
  homeDirNav.addEventListener('click', e => {
    const button = e.target.closest('.nav-link');
    if (button && button.dataset.homedir !== state.currentHome) {
      state.currentHome = button.dataset.homedir;
      renderSidebar();
      changeDirectory('/');
    }
  });

  breadcrumbNav.addEventListener('click', e => {
    e.preventDefault();
    const link = e.target.closest('.breadcrumb-item');
    if (link && !link.classList.contains('active')) {
      changeDirectory(link.dataset.path);
    }
  });

  fileListBody.addEventListener('click', handleItemClick);
  fileListBody.addEventListener('change', e => {
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

  selectAllCheckbox.addEventListener('change', e => {
    const isChecked = e.target.checked;

    document.querySelectorAll('#file-list-body input[type="checkbox"]:not(:disabled)').forEach(checkbox => {
      if (checkbox.checked !== isChecked) {
        checkbox.checked = isChecked;
        checkbox.dispatchEvent(new Event('change', {
          bubbles: true
        }));
      }
    });
  });

  paginationControls.addEventListener('click', e => {
    const button = e.target.closest('button');
    if (button && !button.disabled) {
      state.currentPage = parseInt(button.dataset.page, 10);
      fetchFiles();
    }
  });

  newFolderBtn.addEventListener('click', () => newFolderModal.style.display = 'block');
  newFolderForm.addEventListener('submit', async e => {
    e.preventDefault();
    const folderName = document.getElementById('new-folder-name').value;
    try {
      await secureFetch(`/api/files/create_folder/${state.currentHome}`, {
        method: 'POST',
        body: JSON.stringify({
          path: state.currentPath,
          folder_name: folderName
        })
      });
      displayStatus(t('files.status.folderCreated', {
        folderName
      }));
      fetchFiles();
    } catch (error) {
      displayStatus(t('files.status.folderCreateFailed', {
        error: error.message
      }));
    }
    newFolderModal.style.display = 'none';
    newFolderForm.reset();
  });

  deleteSelectedBtn.addEventListener('click', () => {
    const message = t('files.modals.confirmDelete.message', {
      count: state.selectedItems.size
    });
    document.getElementById('confirm-delete-message').textContent = message;
    confirmDeleteModal.style.display = 'block';
  });
  confirmDeleteBtn.addEventListener('click', handleDeleteSelected);

  uploadFileBtn.addEventListener('click', () => fileUploadInput.click());
  fileUploadInput.addEventListener('change', e => {
    state.uploadQueue.clear();
    for (const file of e.target.files) {
      uploadFile(file, state.currentHome, state.currentPath);
    }
    e.target.value = '';
  });

  uploadFolderBtn.addEventListener('click', () => folderUploadInput.click());
  folderUploadInput.addEventListener('change', e => {
    handleFolderUpload(e.target.files);
    e.target.value = '';
  });

  ['dragenter', 'dragover', 'dragleave', 'drop'].forEach(eventName => {
    fileContentArea.addEventListener(eventName, e => {
      e.preventDefault();
      e.stopPropagation();
    });
  });
  ['dragenter', 'dragover'].forEach(eventName => fileContentArea.addEventListener(eventName, () => dropZoneOverlay.classList.add('visible')));
  ['dragleave', 'drop'].forEach(eventName => fileContentArea.addEventListener(eventName, () => dropZoneOverlay.classList.remove('visible')));

  fileContentArea.addEventListener('drop', async (e) => {
    const items = e.dataTransfer.items;
    if (!items) return;

    const getFile = (entry) => new Promise((resolve, reject) => entry.file(resolve, reject));

    const traverseFileTree = async (entry, path) => {
      path = path || "";
      let files = [];

      if (entry.isFile) {
        const file = await getFile(entry);
        Object.defineProperty(file, 'webkitRelativePath', {
          value: path + file.name
        });
        files.push(file);
      } else if (entry.isDirectory) {
        const dirReader = entry.createReader();
        const readAllEntries = () => new Promise(async (resolve, reject) => {
          let allEntries = [];
          const readBatch = () => {
            dirReader.readEntries(async (entries) => {
              if (entries.length > 0) {
                allEntries.push(...entries);
                readBatch();
              } else {
                for (const subEntry of allEntries) {
                  files.push(...await traverseFileTree(subEntry, path + entry.name + "/"));
                }
                resolve();
              }
            }, reject);
          };
          readBatch();
        });
        await readAllEntries();
      }
      return files;
    };

    const allFiles = (await Promise.all(
      Array.from(items).map(item => traverseFileTree(item.webkitGetAsEntry()))
    )).flat();

    if (allFiles.length > 0) {
      handleFolderUpload(allFiles);
    }
  });

  document.querySelectorAll('.close-button').forEach(btn => {
    btn.addEventListener('click', () => document.getElementById(btn.dataset.modalId).style.display = 'none');
  });

  closeUploadModalBtn.addEventListener('click', () => {
    uploadProgressModal.style.display = 'none';
    state.uploadQueue.clear();
  });
}

document.addEventListener('DOMContentLoaded', init);
