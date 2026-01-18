let t;
function applyTranslations(scope, translator) {
  scope.querySelectorAll('[data-i18n]').forEach(el => {
    const key = el.getAttribute('data-i18n');
    el.innerHTML = translator(key);
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

  const isMobile = window.Capacitor || (window.parent && window.parent.Capacitor);
  
  const context = {
    action: 'file',
    filename: selectedFile.name
  };

  if (isMobile) {
    context.file = selectedFile;
  } else {
    const objectUrl = URL.createObjectURL(selectedFile);
    context.targetUrl = objectUrl;
  }

  confirmUploadBtn.disabled = true;

  if (isMobile) {
    if (window.parent) {
        window.parent.tempFirefoxContext = context;
        chrome.runtime.sendMessage({ type: 'openPopup' });
        resetForm();
    }
    return;
  }

  let bgPage = null;
  try {
    bgPage = chrome.extension.getBackgroundPage();
  } catch (e) {}

  if (bgPage) {
    bgPage.tempFirefoxContext = context;
    
    try {
        chrome.action.openPopup();
    } catch(e) {
        chrome.runtime.sendMessage({ type: 'openPopup' });
    }
    resetForm();
  } else {
    chrome.storage.local.set({
      'sealskinContext': context
    }, () => {
      chrome.runtime.sendMessage({
        type: 'openPopup'
      });
      resetForm();
    });
  }
}

document.addEventListener('DOMContentLoaded', () => {
  const translator = getTranslator(navigator.language);
  t = translator.t;

  if (window.Capacitor) {
    document.body.classList.add('mobile-scroll-layout');

    const header = document.querySelector('header');
    if (header) {
      header.style.display = 'flex';
      header.style.alignItems = 'center';
      
      const backBtn = document.createElement('button');
      backBtn.className = 'mobile-back-btn';
      backBtn.style.fontSize = '1.5rem'; 
      backBtn.innerHTML = '<i class="fas fa-arrow-left"></i>';
      backBtn.onclick = (e) => {
          e.preventDefault();
          window.history.back();
      };
      header.insertBefore(backBtn, header.firstChild);

      const desc = header.querySelector('[data-i18n="upload.description"]');
      if (desc) {
          desc.style.display = 'none';
      }
    }
  }

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
