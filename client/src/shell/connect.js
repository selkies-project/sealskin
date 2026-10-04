/**
 * Connection page (bundled in every shell).
 *
 * The only page that must work with no server: it collects the server
 * address, ports, username, and keys, tests the connection through the
 * background, and stores the configuration. It is framed by the shell host
 * and talks to it with the same bridge as the served pages, plus the
 * connect-only requests `getConnectConfig`, `saveConfig`, and `clearConfig`.
 *
 * The stored `sealskinConfig` keeps its historical shape:
 * `{serverIp, apiPort, sessionPort, username, clientPrivateKey,
 *   serverPublicKey, searchEngineUrl, userSettings}`. The web app also asks
 * for the passphrase that seals the private key in the browser, and offers
 * the server's identity provider sign-ins, whose configurations carry the
 * `keyId` and `signIn` the web app gives them and no key to edit or export.
 */

import { bridge, request } from '../lib/bridge.js';
import { loadTranslator, applyTranslations } from '../lib/i18n.js';
import { MIN_PASSPHRASE, generateRsaKeyPair } from '../lib/crypto-utils.js';

const DEFAULT_SEARCH_ENGINE = 'https://google.com/search?q=';

const $ = (id) => document.getElementById(id);

const connectedView = $('connected-view');
const simpleConfigView = $('simple-config-view');
const advancedConfigView = $('advanced-config-view');
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
let signInOffered = false;

function displayStatus(message, isError = false) {
  statusDiv.textContent = message;
  statusDiv.classList.toggle('error', isError);
  statusDiv.hidden = !message;
}

function showView(view) {
  connectedView.hidden = view !== 'connected';
  simpleConfigView.hidden = view !== 'simple';
  advancedConfigView.hidden = view !== 'advanced';
  $('passphrase-view').hidden = view === 'connected' || !info || info.shell !== 'web';
  $('sign-in-view').hidden = view === 'connected' || !signInOffered;
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
  $('edit-connection').hidden = Boolean(config.signIn);
  $('export-config-button').hidden = Boolean(config.signIn);
  showView('connected');
}

/** Offer the identity provider sign-ins the server that serves the web app configures. */
async function offerSignIn() {
  if (!info || info.shell !== 'web') return;
  try {
    const offered = await (await fetch('/api/auth/config')).json();
    for (const via of ['oidc', 'saml']) {
      $(`sign-in-${via}`).hidden = !offered[via];
      $(`sign-in-${via}`).addEventListener('click', () => request('signIn', { via }));
    }
    signInOffered = Boolean(offered.oidc || offered.saml);
  } catch (e) {
    signInOffered = false;
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
  if (info.shell === 'web') Object.assign(config, webSealing());
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
    displayStatus(sealingError(error) || t('options.status.loginFailed', { error: error.message }), true);
    return false;
  }
}

/** The web app's reasons for not sealing a key, as the status line shows them. */
function sealingError(error) {
  const key = {
    passphraseRequired: 'options.status.passphraseRequired',
    passphraseTooShort: 'options.status.passphraseTooShort',
    wrongPassphrase: 'web.wrongPassphrase',
  }[error.message];
  return key ? t(key, { count: MIN_PASSPHRASE }) : null;
}

/** How the web app keeps the key: sealed under the passphrase, or in the tab alone. */
function webSealing() {
  return { passphrase: $('passphrase').value, remember: $('remember').checked };
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
  const source = currentConfig && currentConfig.clientPrivateKey ? currentConfig : form;
  if (!source.username || !source.clientPrivateKey) {
    displayStatus(t('options.status.generateConfigFailed'), true);
    return;
  }
  const exported = {
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

  const { config, pendingConfig, remember, signInError } = await request('getConnectConfig');
  currentConfig = config;
  await offerSignIn();
  if (signInError) {
    const key = `web.signIn.${signInError}`;
    displayStatus(t(key) === key ? t('web.signIn.failed') : t(key), true);
  }
  $('remember').checked = remember !== false;
  $('passphrase-group').hidden = !$('remember').checked;
  $('remember').addEventListener('change', () => { $('passphrase-group').hidden = !$('remember').checked; });
  fillForm(pendingConfig || config);

  if (config && config.serverIp && config.username && config.clientPrivateKey && !pendingConfig) {
    showConnected(config);
  } else if (pendingConfig) {
    showView('advanced');
  } else {
    showView('simple');
  }

  $('open-app').addEventListener('click', () => bridge.openPage('options'));
  $('edit-connection').addEventListener('click', () => showView('advanced'));
  $('export-config-button').addEventListener('click', exportConfig);
  $('logout-button').addEventListener('click', async () => {
    if (!confirm(t('options.dashboard.confirmLogout'))) return;
    await request('clearConfig');
    currentConfig = null;
    fillForm({ apiPort: '8000', sessionPort: '8443' });
    displayStatus(t('options.status.logoutSuccess'), false);
    showView('simple');
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
      if (parseAndApplyConfig(configText)) {
        await handleLogin();
      }
    } catch (e) {
      displayStatus(t('options.status.fileReadError'), true);
    }
  });

  $('save').addEventListener('click', async () => {
    const pending = formConfig();
    if (info.shell === 'web') Object.assign(pending, webSealing());
    try {
      await bridge.storageSet({ sealskinPendingConfig: pending });
      displayStatus(t('options.status.pendingConfigSaved'), false);
    } catch (error) {
      displayStatus(sealingError(error) || error.message, true);
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
    showView('simple');
  });
});
