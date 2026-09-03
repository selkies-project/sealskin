/**
 * Stand-alone file picker. The chosen File is handed to the shell as a launch
 * context (structured clone carries the File) and the shell opens the popup.
 */

import { bridge } from '../lib/bridge.js';
import { loadTranslator, applyTranslations } from '../lib/i18n.js';
import { announce, addMobileBackButton } from '../lib/dom.js';

let t;
let selectedFile = null;

const dropZone = document.getElementById('file-drop-zone');
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

async function triggerUpload() {
  if (!selectedFile) return;
  confirmUploadBtn.disabled = true;
  try {
    await bridge.setContext({ action: 'file', filename: selectedFile.name, file: selectedFile }, true);
    resetForm();
  } catch (error) {
    console.error('Failed to hand the file to the shell:', error);
    confirmUploadBtn.disabled = false;
  }
}

async function init() {
  const info = await announce();
  t = await loadTranslator(info.locale);

  if (info.shell === 'mobile') {
    document.body.classList.add('mobile-scroll-layout');
    const header = document.querySelector('header');
    if (header) {
      const backBtn = addMobileBackButton(header, () => window.history.back());
      backBtn.style.fontSize = '1.5rem';
      const desc = header.querySelector('[data-i18n="upload.description"]');
      if (desc) desc.style.display = 'none';
    }
  }

  applyTranslations(document.body, t, { html: true });

  uploadPrompt.addEventListener('click', () => fileInput.click());

  fileInput.addEventListener('change', (e) => {
    if (e.target.files.length > 0) processFile(e.target.files[0]);
    e.target.value = '';
  });

  ['dragenter', 'dragover', 'dragleave', 'drop'].forEach((eventName) => {
    dropZone.addEventListener(eventName, (e) => {
      e.preventDefault();
      e.stopPropagation();
    });
  });
  ['dragenter', 'dragover'].forEach((eventName) => {
    dropZone.addEventListener(eventName, () => {
      if (uploadPrompt.style.display !== 'none') dropZone.classList.add('dragover');
    });
  });
  ['dragleave', 'drop'].forEach((eventName) => {
    dropZone.addEventListener(eventName, () => dropZone.classList.remove('dragover'));
  });
  dropZone.addEventListener('drop', (e) => {
    if (uploadPrompt.style.display !== 'none' && e.dataTransfer.files.length > 0) {
      processFile(e.dataTransfer.files[0]);
    }
  });

  confirmUploadBtn.addEventListener('click', triggerUpload);
}

init();
