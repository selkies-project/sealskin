/**
 * Connection page (bundled in the extension and the mobile app).
 *
 * The only page that must work with no server. Its first card takes an
 * application key the user made in the web app, which names the server after
 * an `@`, pasted or, on a phone, read from the QR code the web app shows beside
 * it; the others take a key file, or its values one by one. Either way it tests the
 * connection through the background and stores the configuration. It is
 * framed by the shell host and talks to it with the same bridge as the served
 * pages, plus the connect-only requests `getConnectConfig`, `saveConfig`, and
 * `clearConfig`.
 *
 * The stored `sealskinConfig` keeps its historical shape:
 * `{serverIp, apiPort, sessionPort, username, clientPrivateKey,
 *   serverPublicKey, searchEngineUrl, userSettings}`; with an application
 * key it holds `applicationKey` and `signIn: 'app_key'` in place of the two
 * key fields, and both ports name the session port. The web app frames no
 * connection page: it signs in with the server's cookie (`ui/app.js`).
 */

import { bridge, request } from '../lib/bridge.js';
import { loadTranslator, applyTranslations } from '../lib/i18n.js';
import { generateRsaKeyPair } from '../lib/crypto-utils.js';
import { confirmDialog } from '../lib/modal.js';
import { apiError } from '../lib/api-error.js';

const DEFAULT_SEARCH_ENGINE = 'https://google.com/search?q=';

const $ = (id) => document.getElementById(id);

const connectedView = $('connected-view');
const keyConfigView = $('key-config-view');
const simpleConfigView = $('simple-config-view');
const advancedConfigView = $('advanced-config-view');
const applicationKeyInput = $('applicationKey');
const scanKeyButton = $('scanKey');
const statusDiv = $('status');
const configFileUpload = $('configFileUpload');
const configTextArea = $('configTextArea');
const serverIpInput = $('serverIp');
const apiPortInput = $('apiPort');
const sessionPortInput = $('sessionPort');
const usernameInput = $('username');
const clientPrivateKeyInput = $('clientPrivateKey');
const serverPublicKeyInput = $('serverPublicKey');

let t = (key) => key;
let info = null;
let currentConfig = null;

function displayStatus(message, isError = false) {
  statusDiv.textContent = message;
  statusDiv.classList.toggle('error', isError);
  statusDiv.hidden = !message;
}

function showView(view) {
  connectedView.hidden = view !== 'connected';
  keyConfigView.hidden = view !== 'key';
  simpleConfigView.hidden = view !== 'simple';
  advancedConfigView.hidden = view !== 'advanced';
}

/** Whether a configuration signs in with an application key rather than a key file. */
const usesKey = (config) => Boolean(config && config.applicationKey);

/**
 * Split a key as the web app hands it out, `ssk_<id>_<secret>@<host>:<port>`,
 * into the key the server takes and the server's address.
 *
 * @param {string} text
 * @returns {{key: string, address: string}}
 */
function splitKey(text) {
  const [key, address = ''] = text.trim().split('@', 2);
  return { key, address };
}

/**
 * Split a server address as typed, `host`, `host:port`, or a URL, into host and port; the port is 443 unless given.
 *
 * @param {string} text
 * @returns {{host: string, port: string}|null}
 */
function parseAddress(text) {
  let value = text.trim().replace(/^[a-z]+:\/\//i, '').replace(/[/?#].*$/, '');
  if (!value) return null;
  let port = '443';
  const bracketed = /^\[([^\]]+)\](?::(\d+))?$/.exec(value);
  if (bracketed) {
    value = bracketed[1];
    if (bracketed[2]) port = bracketed[2];
  } else {
    const at = value.lastIndexOf(':');
    if (at > 0 && value.indexOf(':') === at) {
      port = value.slice(at + 1);
      value = value.slice(0, at);
    }
  }
  if (!value || !/^\d+$/.test(port)) return null;
  return { host: value, port };
}

function fillForm(config) {
  if (!config) return;
  serverIpInput.value = config.serverIp || '';
  apiPortInput.value = config.apiPort || '8000';
  sessionPortInput.value = config.sessionPort || '8443';
  usernameInput.value = config.username || '';
  clientPrivateKeyInput.value = config.clientPrivateKey || '';
  serverPublicKeyInput.value = config.serverPublicKey || '';
}

function formConfig() {
  return {
    serverIp: serverIpInput.value.trim(),
    apiPort: apiPortInput.value.trim(),
    sessionPort: sessionPortInput.value.trim(),
    username: usernameInput.value.trim(),
    clientPrivateKey: clientPrivateKeyInput.value.trim(),
    serverPublicKey: serverPublicKeyInput.value.trim(),
  };
}

function parseAndApplyConfig(configText) {
  try {
    const config = JSON.parse(configText);
    // A file exported from a connection with an application key carries the key in place of the key pair.
    if (config && config.application_key) {
      const { key, address } = splitKey(config.application_key);
      applicationKeyInput.value = `${key}@${address || `${config.server_endpoint}:${config.session_port || '443'}`}`;
      showView('key');
      configFileUpload.value = '';
      configTextArea.value = '';
      $('config-file-name').textContent = '';
      return 'key';
    }
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

    displayStatus(t('options.status.configApplied'), false);
    showView('advanced');
    configFileUpload.value = '';
    configTextArea.value = '';
    $('config-file-name').textContent = '';
    return true;
  } catch (error) {
    displayStatus(t('options.status.configApplyFailed', { error: error.message }), true);
    return false;
  }
}

function showConnected(config) {
  $('connected-username').textContent = config.username || '';
  $('connected-server').textContent = `${config.serverIp}:${config.sessionPort || config.apiPort}`;
  $('connected-how').textContent = t(usesKey(config) ? 'options.config.connectedWithKey' : 'options.config.connectedWithKeyFile');
  showView('connected');
}

/**
 * Save the application key, and the server it names, as the configuration and ask the server who the key is.
 *
 * @returns {Promise<boolean>} True when the server took the key.
 */
async function handleKeyLogin() {
  const { key, address: named } = splitKey(applicationKeyInput.value);
  if (!key) {
    displayStatus(t('options.status.keyMissing'), true);
    return false;
  }
  const address = parseAddress(named);
  if (!address) {
    displayStatus(t('options.status.keyNoServer'), true);
    return false;
  }
  displayStatus(t('options.status.loggingIn'));
  const config = {
    serverIp: address.host,
    apiPort: address.port,
    sessionPort: address.port,
    applicationKey: key,
    signIn: 'app_key',
    searchEngineUrl: (currentConfig && currentConfig.searchEngineUrl) || DEFAULT_SEARCH_ENGINE,
  };
  try {
    await request('saveConfig', { config });
    await bridge.storageRemove(['sealskinPendingConfig']);
    const statusData = await bridge.secureFetch('/api/admin/status', { method: 'POST', body: JSON.stringify({}) });
    config.username = statusData.username;
    config.userSettings = { ...statusData.settings, is_admin: statusData.is_admin };
    await request('saveConfig', { config });
    currentConfig = config;
    applicationKeyInput.value = '';
    displayStatus(t(statusData.is_admin ? 'options.status.loggedInAdmin' : 'options.status.loggedInUser', { username: statusData.username }), false);
    showConnected(config);
    // The extension's full page is the options page; the launcher is for its popup.
    await bridge.openPage('options');
    return true;
  } catch (error) {
    const { status, detail } = apiError(error);
    const reason = status === 401 && /^[a-z]+$/.test(detail) ? t(`options.status.keyRefused.${detail}`) : null;
    const known = reason && reason !== `options.status.keyRefused.${detail}`;
    displayStatus(known ? reason : t('options.status.loginFailed', { error: error.message }), true);
    return false;
  }
}

/** Read an application key from the QR code the web app shows and sign in with it. */
async function handleScan() {
  scanKeyButton.disabled = true;
  try {
    const text = await bridge.scanCode(t('options.config.keyScanInstructions'));
    if (!text) return;
    const { key, address } = splitKey(text);
    if (!key || !parseAddress(address)) {
      displayStatus(t('options.status.scanNotKey'), true);
      return;
    }
    applicationKeyInput.value = text.trim();
    await handleKeyLogin();
  } catch (error) {
    displayStatus(t('options.status.scanFailed', { error: error.message }), true);
  } finally {
    scanKeyButton.disabled = false;
  }
}

/**
 * Save the form as the active configuration and test it against the server.
 *
 * @returns {Promise<boolean>} True when `/api/admin/status` answered.
 */
async function handleLogin() {
  displayStatus(t('options.status.loggingIn'));
  const config = {
    ...formConfig(),
    searchEngineUrl: (currentConfig && currentConfig.searchEngineUrl) || DEFAULT_SEARCH_ENGINE,
  };
  try {
    await request('saveConfig', { config });
    await bridge.storageRemove(['sealskinPendingConfig']);
    const statusData = await bridge.secureFetch('/api/admin/status', {
      method: 'POST',
      body: JSON.stringify({}),
    });
    config.userSettings = { ...statusData.settings, is_admin: statusData.is_admin };
    await bridge.updateConfig({ userSettings: config.userSettings });
    currentConfig = config;
    displayStatus(t(statusData.is_admin ? 'options.status.loggedInAdmin' : 'options.status.loggedInUser', {
      username: statusData.username,
    }), false);
    showConnected(config);
    // After a successful login land on the dashboard, not the launcher.
    await bridge.openPage('options');
    return true;
  } catch (error) {
    currentConfig = config;
    displayStatus(t('options.status.loginFailed', { error: error.message }), true);
    return false;
  }
}

function readFileAsText(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = (event) => resolve(event.target.result);
    reader.onerror = (error) => reject(error);
    reader.readAsText(file);
  });
}

async function exportConfig() {
  const form = formConfig();
  const source = usesKey(currentConfig) || (currentConfig && currentConfig.clientPrivateKey) ? currentConfig : form;
  if (!source.username || !(source.clientPrivateKey || source.applicationKey)) {
    displayStatus(t('options.status.generateConfigFailed'), true);
    return;
  }
  const exported = usesKey(source)
    ? {
      server_endpoint: source.serverIp,
      session_port: source.sessionPort,
      username: source.username,
      application_key: `${source.applicationKey}@${source.serverIp}:${source.sessionPort}`,
    }
    : {
      server_endpoint: source.serverIp,
      api_port: source.apiPort,
      session_port: source.sessionPort,
      username: source.username,
      private_key: source.clientPrivateKey,
      server_public_key: source.serverPublicKey,
    };
  const blob = new Blob([JSON.stringify(exported, null, 2)], { type: 'application/json' });
  const filename = `${source.username}-sealskin-config.json`;
  if (info && info.capabilities && info.capabilities.nativeFileOpen) {
    try {
      await bridge.saveBlob(blob, filename);
    } catch (e) {
      displayStatus(e.message, true);
    }
    return;
  }
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

async function init() {
  info = await bridge.hello();
  document.documentElement.classList.add(`shell-${info.shell}`);
  t = await loadTranslator(info.locale || navigator.language);
  applyTranslations(document.body, t);

  const { config, pendingConfig } = await request('getConnectConfig');
  currentConfig = config;
  fillForm(pendingConfig || config);

  if (config && config.serverIp && config.username && (config.clientPrivateKey || usesKey(config)) && !pendingConfig) {
    showConnected(config);
  } else if (pendingConfig) {
    showView('advanced');
  } else {
    showView('key');
  }

  $('open-app').addEventListener('click', () => bridge.openPage('options'));
  $('edit-connection').addEventListener('click', () => showView(usesKey(currentConfig) ? 'key' : 'advanced'));
  $('connectKey').addEventListener('click', handleKeyLogin);
  scanKeyButton.hidden = !(info.capabilities && info.capabilities.scanCode);
  scanKeyButton.addEventListener('click', handleScan);
  applicationKeyInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') {
      e.preventDefault();
      handleKeyLogin();
    }
  });
  $('show-keyfile-link').addEventListener('click', (e) => { e.preventDefault(); showView('simple'); });
  ['show-key-link', 'show-key-link-2'].forEach((id) => $(id).addEventListener('click', (e) => { e.preventDefault(); showView('key'); }));
  $('export-config-button').addEventListener('click', exportConfig);
  $('logout-button').addEventListener('click', async () => {
    const logout = await confirmDialog(t, {
      title: t('options.dashboard.logoutTitle'),
      message: t('options.dashboard.confirmLogout'),
      confirm: t('options.dashboard.logout'),
      danger: true,
    });
    if (!logout) return;
    await request('clearConfig');
    currentConfig = null;
    fillForm({ apiPort: '8000', sessionPort: '8443' });
    applicationKeyInput.value = '';
    displayStatus(t('options.status.logoutSuccess'), false);
    showView('key');
  });

  $('show-advanced-link').addEventListener('click', (e) => { e.preventDefault(); showView('advanced'); });
  $('hide-advanced-link').addEventListener('click', (e) => { e.preventDefault(); showView('simple'); });

  configFileUpload.addEventListener('change', () => {
    $('config-file-name').textContent = configFileUpload.files.length > 0 ? configFileUpload.files[0].name : '';
  });

  $('applyConfig').addEventListener('click', async () => {
    try {
      const text = configTextArea.value.trim();
      let configText;
      if (text) {
        configText = text;
      } else if (configFileUpload.files.length > 0) {
        configText = await readFileAsText(configFileUpload.files[0]);
      } else {
        displayStatus(t('options.status.noConfig'), true);
        return;
      }
      const applied = parseAndApplyConfig(configText);
      if (applied === 'key') await handleKeyLogin();
      else if (applied) await handleLogin();
    } catch (e) {
      displayStatus(t('options.status.fileReadError'), true);
    }
  });

  $('save').addEventListener('click', async () => {
    try {
      await bridge.storageSet({ sealskinPendingConfig: formConfig() });
      displayStatus(t('options.status.pendingConfigSaved'), false);
    } catch (error) {
      displayStatus(error.message, true);
    }
  });

  $('login').addEventListener('click', handleLogin);

  $('generateKeyBtn').addEventListener('click', async () => {
    try {
      const keyPair = await generateRsaKeyPair();
      clientPrivateKeyInput.value = keyPair.privateKey;
      $('generatedPubKey').value = keyPair.publicKey;
      $('pubKeyDisplay').hidden = false;
      displayStatus(t('options.status.keyGenerated'));
    } catch (error) {
      displayStatus(t('options.status.keyGenFailed', { error: error.message }), true);
    }
  });

  $('copyPubKeyBtn').addEventListener('click', () => {
    navigator.clipboard.writeText($('generatedPubKey').value).then(
      () => displayStatus(t('options.status.publicKeyCopied')),
      () => displayStatus(t('options.status.keyCopyFailed'), true),
    );
  });
}

document.addEventListener('DOMContentLoaded', () => {
  init().catch((error) => {
    console.error(error);
    displayStatus(error.message, true);
    showView('key');
  });
});
