let t;
function applyTranslations(scope, translator) {
  scope.querySelectorAll('[data-i18n]').forEach(el => {
    const key = el.getAttribute('data-i18n');
    el.innerHTML = translator(key); // Use innerHTML to support simple tags
  });
}

let selectedFile = null;

const dropZone = document.getElementById('file-drop-zone');
const selectFileBtn = document.getElementById('select-file-btn');
const fileInput = document.getElementById('file-input');
const uploadPrompt = document.getElementById('upload-prompt');
const uploadConfirm = document.getElementById('upload-confirm');
const selectedFilenameEl = document.getElementById('selected-filename');
const confirmUploadBtn = document.getElementById('confirm-upload-btn');

function resetForm() {
  selectedFile = null;
  uploadConfirm.style.display = 'none';
  uploadPrompt.style.display = 'block';
  dropZone.style.cursor = 'pointer';
  confirmUploadBtn.disabled = false;
}

function processFile(file) {
  if (!file) return;

  selectedFile = file;
  selectedFilenameEl.textContent = file.name;

  uploadPrompt.style.display = 'none';
  uploadConfirm.style.display = 'block';

  dropZone.style.cursor = 'default';
}

function triggerUpload() {
  if (!selectedFile) return;

  const objectUrl = URL.createObjectURL(selectedFile);

  const context = {
    action: 'file',
    targetUrl: objectUrl,
    filename: selectedFile.name
  };

  confirmUploadBtn.disabled = true;

  chrome.storage.local.set({
    'sealskinContext': context
  }, () => {
    chrome.runtime.sendMessage({
      type: 'openPopup'
    });
    resetForm();
  });
}

document.addEventListener('DOMContentLoaded', () => {
  const translator = getTranslator(navigator.language);
  t = translator.t;
  applyTranslations(document.body, t);

  const currentTheme = localStorage.getItem('theme');
  if (currentTheme) {
    document.documentElement.setAttribute('data-theme', currentTheme);
  } else {
    const prefersDark = window.matchMedia('(prefers-color-scheme: dark)').matches;
    document.documentElement.setAttribute('data-theme', prefersDark ? 'dark' : 'light');
  }

  uploadPrompt.addEventListener('click', () => {
    fileInput.click();
  });

  fileInput.addEventListener('change', (e) => {
    if (e.target.files.length > 0) {
      processFile(e.target.files[0]);
    }
    e.target.value = '';
  });

  ['dragenter', 'dragover', 'dragleave', 'drop'].forEach(eventName => {
    dropZone.addEventListener(eventName, (e) => {
      e.preventDefault();
      e.stopPropagation();
    });
  });

  ['dragenter', 'dragover'].forEach(eventName => {
    dropZone.addEventListener(eventName, () => {
      if (uploadPrompt.style.display !== 'none') {
        dropZone.classList.add('dragover');
      }
    });
  });

  ['dragleave', 'drop'].forEach(eventName => {
    dropZone.addEventListener(eventName, () => dropZone.classList.remove('dragover'));
  });

  dropZone.addEventListener('drop', (e) => {
    if (uploadPrompt.style.display !== 'none' && e.dataTransfer.files.length > 0) {
      processFile(e.dataTransfer.files[0]);
    }
  });

  confirmUploadBtn.addEventListener('click', triggerUpload);
});
