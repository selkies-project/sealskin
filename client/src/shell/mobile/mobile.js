/**
 * Mobile shell entry (Capacitor outer window).
 *
 * Order matters: the polyfill must exist before the background script runs,
 * and the background must be loaded before the host relays bridge requests to
 * it. ES module imports evaluate in order, so `background.js` is imported
 * after the polyfill has been installed by the top of this module.
 */

import { Browser } from '@capacitor/browser';
import { App } from '@capacitor/app';
import { Filesystem, Directory } from '@capacitor/filesystem';
import write_blob from 'capacitor-blob-writer';
import { FileOpener } from '@capawesome-team/capacitor-file-opener';
import { CapacitorBarcodeScanner, CapacitorBarcodeScannerTypeHint, CapacitorBarcodeScannerCameraDirection } from '@capacitor/barcode-scanner';

// Import order is the load order: polyfill first, then the background
// (E2EE, JWT, pending context) which runs in this window, then the host.
import { hooks } from './polyfill-install.js';
import '../background.js';
import { initHost } from '../host.js';

let hostApi = null;

hooks.openExternal = async (url) => {
  try {
    await Browser.open({ url });
  } catch (e) {
    window.open(url, '_system');
  }
};
hooks.openPopup = () => {
  if (hostApi) hostApi.openPage('popup');
};

/**
 * Write a downloaded blob to the app cache and open it with the system viewer.
 *
 * @param {Blob} blob
 * @param {string} filename
 */
async function saveBlob(blob, filename) {
  await write_blob({
    path: filename,
    directory: Directory.Cache,
    blob,
    recursive: true,
  });
  const uriResult = await Filesystem.getUri({ path: filename, directory: Directory.Cache });
  await FileOpener.openFile({ path: uriResult.uri });
}

/**
 * Read a QR code with the camera, in the plugin's own full-screen scanner.
 *
 * @param {string} instructions Line shown over the camera.
 * @returns {Promise<string>} The code's text; empty when the user closed the scanner.
 */
async function scanCode(instructions) {
  try {
    const result = await CapacitorBarcodeScanner.scanBarcode({
      hint: CapacitorBarcodeScannerTypeHint.QR_CODE,
      scanInstructions: instructions,
      cameraDirection: CapacitorBarcodeScannerCameraDirection.BACK,
    });
    return (result && result.ScanResult) || '';
  } catch (e) {
    // Closing the scanner is not an error worth a message.
    if (/cancel/i.test(e && e.message ? e.message : String(e))) return '';
    throw e;
  }
}

hostApi = initHost({
  saveBlob,
  scanCode,
});

App.addListener('backButton', () => {
  const page = hostApi.currentPage();
  if (page === 'popup' || page === 'connect') {
    App.exitApp();
  } else {
    hostApi.openPage('popup');
  }
});

console.log('SealSkin mobile shell loaded');
