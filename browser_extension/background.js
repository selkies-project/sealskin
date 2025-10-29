importScripts('crypto-utils.js');
importScripts('translations.js');

let session = {
  key: null,
  id: null
};

function arrayBufferToBase64(buffer) {
  let binary = '';
  const bytes = new Uint8Array(buffer);
  for (let i = 0; i < bytes.byteLength; i++) binary += String.fromCharCode(bytes[i]);
  return btoa(binary);
}

async function getSessionTabMap() {
  const result = await chrome.storage.local.get('sessionTabMap');
  return result.sessionTabMap || {};
}

async function saveSessionTabMap(map) {
  await chrome.storage.local.set({ sessionTabMap: map });
}


async function importRsaPublicKey(pem) {
  const buffer = pemToArrayBuffer(pem);
  return crypto.subtle.importKey('spki', buffer, {
      name: 'RSA-PSS',
      hash: 'SHA-256'
    }, true, ['verify'])
    .catch(() => crypto.subtle.importKey('spki', buffer, {
      name: 'RSA-OAEP',
      hash: 'SHA-256'
    }, true, ['encrypt']));
}

async function encryptAesGcm(key, data) {
  const iv = crypto.getRandomValues(new Uint8Array(12));
  const encodedData = new TextEncoder().encode(data);
  const ciphertext = await crypto.subtle.encrypt({
    name: 'AES-GCM',
    iv: iv
  }, key, encodedData);
  return {
    iv: arrayBufferToBase64(iv.buffer),
    ciphertext: arrayBufferToBase64(ciphertext)
  };
}

async function decryptAesGcm(key, iv, ciphertext) {
  try {
    const ivDecoded = atob(iv);
    const ivBuffer = Uint8Array.from(ivDecoded, c => c.charCodeAt(0));
    const ciphertextDecoded = atob(ciphertext);
    const ciphertextBuffer = Uint8Array.from(ciphertextDecoded, c => c.charCodeAt(0));
    const decrypted = await crypto.subtle.decrypt({
      name: 'AES-GCM',
      iv: ivBuffer
    }, key, ciphertextBuffer);
    const decodedText = new TextDecoder().decode(decrypted);
    return decodedText;
  } catch (e) {
    throw e;
  }
}

async function performHandshake(config) {
  console.log('[SealSkin E2EE] Performing handshake...');
  const {
    serverIp,
    apiPort,
    serverPublicKey
  } = config;
  if (!serverIp || !apiPort || !serverPublicKey) throw new Error('Server IP, API Port, or Server Public Key is not configured.');

  const apiUrl = `http://${serverIp}:${apiPort}`;
  const initResponse = await fetch(`${apiUrl}/api/handshake/initiate`, {
    method: 'POST'
  });
  if (!initResponse.ok) throw new Error(`Handshake initiation failed: ${await initResponse.text()}`);
  const {
    nonce,
    signature
  } = await initResponse.json();

  const serverPubKey = await importRsaPublicKey(serverPublicKey);
  const nonceBuffer = Uint8Array.from(atob(nonce), c => c.charCodeAt(0));
  const signatureBuffer = Uint8Array.from(atob(signature), c => c.charCodeAt(0));
  const isValid = await crypto.subtle.verify({
    name: 'RSA-PSS',
    saltLength: 32
  }, serverPubKey, signatureBuffer, nonceBuffer);

  if (!isValid) throw new Error('Handshake failed: Server signature verification failed.');

  const aesKey = await crypto.subtle.generateKey({
    name: 'AES-GCM',
    length: 256
  }, true, ['encrypt', 'decrypt']);
  const exportedKey = await crypto.subtle.exportKey('raw', aesKey);
  const serverEncryptKey = await crypto.subtle.importKey('spki', pemToArrayBuffer(serverPublicKey), {
    name: 'RSA-OAEP',
    hash: 'SHA-256'
  }, false, ['encrypt']);
  const encryptedSessionKey = await crypto.subtle.encrypt({
    name: 'RSA-OAEP'
  }, serverEncryptKey, exportedKey);

  const exchangeResponse = await fetch(`${apiUrl}/api/handshake/exchange`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json'
    },
    body: JSON.stringify({
      encrypted_session_key: arrayBufferToBase64(encryptedSessionKey)
    }),
  });

  if (!exchangeResponse.ok) throw new Error(`Handshake key exchange failed: ${await exchangeResponse.text()}`);
  const {
    session_id
  } = await exchangeResponse.json();
  session = {
    key: aesKey,
    id: session_id
  };
  console.log(`[SealSkin E2EE] Handshake successful. Session: ${session_id}`);
}

async function ensureSession() {
  if (!session.key || !session.id) {
    const {
      sealskinConfig
    } = await chrome.storage.local.get('sealskinConfig');
    if (!sealskinConfig) throw new Error('Extension is not configured.');
    await performHandshake(sealskinConfig);
  }
  return session;
}

async function secureFetchInBackground(url, options = {}) {
  for (let attempt = 0; attempt < 2; attempt++) {
    try {
      const { sealskinConfig } = await chrome.storage.local.get('sealskinConfig');
      if (!sealskinConfig || !sealskinConfig.serverIp || !sealskinConfig.apiPort) {
        throw new Error('Extension is not configured.');
      }

      if (url.startsWith('/api/admin') || url.startsWith('/api/homedirs') || url.startsWith('/api/sessions')) {
          const jwt = await generateJwtNative(sealskinConfig.clientPrivateKey, sealskinConfig.username);
          options.headers = {
              ...options.headers,
              'Authorization': `Bearer ${jwt}`
          };
      }

      const apiUrl = `http://${sealskinConfig.serverIp}:${sealskinConfig.apiPort}`;
      const fullUrl = `${apiUrl}${url}`;

      const currentSession = await ensureSession();
      const headers = { ...options.headers, 'X-Session-ID': currentSession.id };
      let body = options.body;

      if (body) {
        const encryptedPayload = await encryptAesGcm(currentSession.key, body);
        body = JSON.stringify(encryptedPayload);
        headers['Content-Type'] = 'application/json';
      }

      const response = await fetch(fullUrl, { ...options, headers, body });

      if (response.status === 204 || response.status === 200 && response.headers.get('Content-Length') === '0') {
        return null;
      }

      const responseText = await response.text();

      if (!response.ok) {
        throw new Error(`HTTP error! status: ${response.status} - ${responseText}`);
      }

      const encryptedResponse = JSON.parse(responseText);
      const decryptedData = await decryptAesGcm(currentSession.key, encryptedResponse.iv, encryptedResponse.ciphertext);

      return JSON.parse(decryptedData);

    } catch (error) {
      console.log(`[SealSkin SecureFetchInBackground Attempt ${attempt + 1}]`, error);
      const isSessionError = error.message.includes('atob') ||
        error.message.includes('decryption') ||
        error.message.includes('HTTP error! status: 400');

      if (isSessionError && attempt === 0) {
        console.log('[SealSkin] Detected session error, resetting session and retrying...');
        session = { key: null, id: null };
      } else {
        throw error;
      }
    }
  }
}

function getSessionUrlBase(config) {
    if (!config.serverIp || !config.sessionPort) return null;
    return `https://${config.serverIp}:${config.sessionPort}`;
}

chrome.runtime.onMessage.addListener((request, sender, sendResponse) => {
  if (request.type === 'secureFetch') {
    (async () => {
      try {
        const data = await secureFetchInBackground(request.payload.url, request.payload.options);
        sendResponse({ success: true, data });
      } catch (error) {
        sendResponse({ success: false, error: error.message });
      }
    })();
    return true;
  }

  if (request.type === 'openPopup') {
    chrome.action.openPopup();
    return false;
  }

  if (request.type === 'createTabAndTrack') {
    (async () => {
      const { sessionId, session_url } = request.payload;
      const { sealskinConfig } = await chrome.storage.local.get('sealskinConfig');
      const fullUrl = `${getSessionUrlBase(sealskinConfig)}${session_url}`;
      const newTab = await chrome.tabs.create({ url: fullUrl });
      const map = await getSessionTabMap();
      map[sessionId] = newTab.id;
      await saveSessionTabMap(map);
    })();
    return false;
  }

  if (request.type === 'focusOrCreateTab') {
    (async () => {
      const { session } = request.payload;
      const map = await getSessionTabMap();
      const tabId = map[session.session_id];

      if (tabId) {
        try {
          const tab = await chrome.tabs.get(tabId);
          await chrome.tabs.update(tab.id, { active: true });
          await chrome.windows.update(tab.windowId, { focused: true });
          return;
        } catch (e) {
          console.log(`Tab ${tabId} not found, will create a new one.`);
        }
      }

      const { sealskinConfig } = await chrome.storage.local.get('sealskinConfig');
      const fullUrl = `${getSessionUrlBase(sealskinConfig)}${session.session_url}`;
      const newTab = await chrome.tabs.create({ url: fullUrl });
      map[session.session_id] = newTab.id;
      await saveSessionTabMap(map);
    })();
    return false;
  }

  if (request.type === 'closeSession') {
    (async () => {
      const { sessionId } = request.payload;
      try {
        const map = await getSessionTabMap();
        const tabId = map[sessionId];
        if (tabId) {
          try {
            await chrome.tabs.remove(tabId);
          } catch (e) { /* Tab already closed */ }
        }
        await secureFetchInBackground(`/api/sessions/${sessionId}`, { method: 'DELETE' });
        delete map[sessionId];
        await saveSessionTabMap(map);
        sendResponse({ success: true });
      } catch (error) {
        console.error(`[BG] FAILED to close session ${sessionId}. Error:`, error);
        sendResponse({ success: false, error: error.message });
      }
    })();
    return true;
  }
});

// --- Context Menu and Download Logic ---
chrome.runtime.onInstalled.addListener(() => {
  const baseLang = chrome.i18n.getUILanguage().split('-')[0];
  const translator = getTranslator(baseLang);
  const t = translator.t;

  chrome.contextMenus.create({
    id: 'sealskin-open-url',
    title: t('background.contextMenu.openUrl'),
    contexts: ['link']
  });
  chrome.contextMenus.create({
    id: 'sealskin-open-file',
    title: t('background.contextMenu.openFile'),
    contexts: ['link']
  });
  chrome.contextMenus.create({
    id: 'sealskin-send-media',
    title: t('background.contextMenu.sendMedia'),
    contexts: ['image', 'video', 'audio']
  });
  chrome.contextMenus.create({
    id: 'sealskin-search-selection',
    title: t('background.contextMenu.searchText'),
    contexts: ['selection']
  });
  chrome.contextMenus.create({
    id: 'sealskin-intercept-next-download',
    title: t('background.contextMenu.sendDownload'),
    contexts: ['page', 'selection']
  });
});

chrome.contextMenus.onClicked.addListener(async (info, tab) => {
  const {
    menuItemId,
    linkUrl,
    srcUrl,
    selectionText
  } = info;
  if (menuItemId === 'sealskin-intercept-next-download') {
    chrome.storage.local.set({
      'interceptNextDownload': {
        active: true,
        timestamp: Date.now()
      }
    });
    chrome.action.setBadgeText({
      text: '...'
    });
    return;
  }

  let context = null;

  const getFilenameFromUrl = (url) => {
    try {
      const pathname = new URL(url).pathname;
      return pathname.substring(pathname.lastIndexOf('/') + 1) || 'file_from_url';
    } catch (e) {
      console.warn('Could not parse URL to get filename:', url);
      return 'unknown_file';
    }
  };

  if (menuItemId === 'sealskin-open-url') {
    context = {
      action: 'url',
      targetUrl: linkUrl
    };
  } else if (menuItemId === 'sealskin-open-file') {
    context = {
      action: 'file',
      targetUrl: linkUrl,
      filename: getFilenameFromUrl(linkUrl)
    };
  } else if (menuItemId === 'sealskin-send-media') {
    context = {
      action: 'file',
      targetUrl: srcUrl,
      filename: getFilenameFromUrl(srcUrl)
    };
  } else if (menuItemId === 'sealskin-search-selection') {
    const data = await chrome.storage.local.get('sealskinConfig');
    const searchEngineBaseUrl = data.sealskinConfig?.searchEngineUrl || 'https://google.com/search?q=';
    context = {
      action: 'url',
      targetUrl: `${searchEngineBaseUrl}${encodeURIComponent(selectionText)}`
    };
  }

  if (context) {
    chrome.storage.local.set({
      'sealskinContext': context
    }, () => chrome.action.openPopup());
  }
});

chrome.downloads.onDeterminingFilename.addListener((downloadItem, suggest) => {
  (async () => {
    const data = await chrome.storage.local.get('interceptNextDownload');
    const interceptConfig = data.interceptNextDownload;
    if (interceptConfig && interceptConfig.active && (Date.now() - interceptConfig.timestamp < 60000)) {
      await chrome.storage.local.remove('interceptNextDownload');
      chrome.action.setBadgeText({
        text: ''
      });
      await chrome.downloads.cancel(downloadItem.id);
      await chrome.storage.local.set({
        'sealskinContext': {
          action: 'file',
          targetUrl: downloadItem.url,
          filename: downloadItem.filename
        }
      });
      chrome.action.openPopup();
    } else suggest();
  })();
  return true;
});
