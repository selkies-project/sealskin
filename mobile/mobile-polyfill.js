if (window.Capacitor) {
  window.chrome = window.chrome || {};

  const getTabUrl = (id) => localStorage.getItem(`sealskin_tab_${id}`);
  const setTabUrl = (id, url) => localStorage.setItem(`sealskin_tab_${id}`, url);
  const removeTabUrl = (id) => localStorage.removeItem(`sealskin_tab_${id}`);

  const mockListener = {
    addListener: () => {},
    removeListener: () => {},
    hasListener: () => false
  };

  const mockAsync = (defaultReturn = {}) => {
    return (arg1, arg2, cb) => {
      const callback = typeof arg1 === 'function' ? arg1 : (typeof arg2 === 'function' ? arg2 : cb);
      if (callback) callback(defaultReturn);
      return Promise.resolve(defaultReturn);
    };
  };

  window.chrome.runtime = {
    id: 'sealskin-mobile',
    lastError: null,
    getURL: (path) => path,
    getManifest: () => ({ version: '1.0.0', manifest_version: 3 }),
    
    sendMessage: (message, options, callback) => {
      const cb = typeof options === 'function' ? options : callback;
      
      let handler = window.handleMessage;
      
      if (!handler && window.parent && window.parent.handleMessage) {
        handler = window.parent.handleMessage;
      }

      if (handler) {
        const sender = { id: 'mobile-shell', url: window.location.href };
        
        handler(message, sender, (response) => {
           if (window.chrome.runtime.lastError || (window.parent.chrome && window.parent.chrome.runtime.lastError)) {
               if (cb) cb(); 
               return;
           }
           const safeResponse = response || { success: false, error: "No response from background" };
           if (cb) cb(safeResponse);
        });
      } else {
        console.error("Polyfill: No background listener found in current window or parent.");
        const err = { message: "Background script not listening" };
        window.chrome.runtime.lastError = err;
        if(window.parent && window.parent.chrome) window.parent.chrome.runtime.lastError = err;
        
        if (cb) cb();
        
        window.chrome.runtime.lastError = null;
        if(window.parent && window.parent.chrome) window.parent.chrome.runtime.lastError = null;
      }
    },

    openOptionsPage: () => {
      const appFrame = document.getElementById('app-frame') || (window.parent ? window.parent.document.getElementById('app-frame') : null);
      if (appFrame) appFrame.src = 'options.html';
    },
    reload: () => { window.location.reload(); },
    connect: () => ({
      onMessage: mockListener,
      postMessage: () => {},
      disconnect: () => {}
    }),
    onMessage: {
      addListener: (cb) => { window.handleMessage = cb; },
      removeListener: () => { window.handleMessage = null; },
      hasListener: () => !!window.handleMessage
    },
    onInstalled: mockListener,
    onStartup: mockListener,
    onSuspend: mockListener,
    onConnect: mockListener
  };

  const actionMock = {
    openPopup: () => {
        const appFrame = document.getElementById('app-frame') || (window.parent ? window.parent.document.getElementById('app-frame') : null);
        if (appFrame) appFrame.src = 'popup.html';
    },
    setBadgeText: () => {},
    setTitle: () => {},
    setIcon: () => {},
    enable: () => {},
    disable: () => {},
    onClicked: mockListener
  };
  window.chrome.action = actionMock;
  window.chrome.browserAction = actionMock;

  const storageListeners = new Set();
  window.addEventListener('storage', (e) => {
    if (e.storageArea === localStorage && e.key) {
      const changes = {
        [e.key]: {
          oldValue: e.oldValue ? JSON.parse(e.oldValue) : undefined,
          newValue: e.newValue ? JSON.parse(e.newValue) : undefined
        }
      };
      storageListeners.forEach(cb => cb(changes, 'local'));
    }
  });
  const storageArea = {
    get: (keys, cb) => {
      let res = {};
      if (keys === null) {
        res = {...localStorage};
        Object.keys(res).forEach(k => {
            try { res[k] = JSON.parse(res[k]); } catch(e) {}
        });
      } else {
        const k = Array.isArray(keys) ? keys : [keys];
        k.forEach(key => {
          const val = localStorage.getItem(key);
          try { res[key] = JSON.parse(val); } catch(e) { res[key] = val; }
        });
      }
      if (cb) cb(res);
      return Promise.resolve(res);
    },
    set: (items, cb) => {
      const changes = {};
      for (const k in items) {
        const oldValueStr = localStorage.getItem(k);
        const oldValue = oldValueStr ? JSON.parse(oldValueStr) : undefined;
        localStorage.setItem(k, JSON.stringify(items[k]));
        changes[k] = { oldValue, newValue: items[k] };
      }
      storageListeners.forEach(listener => listener(changes, 'local'));
      if (cb) cb();
      return Promise.resolve();
    },
    remove: (keys, cb) => {
      const k = Array.isArray(keys) ? keys : [keys];
      const changes = {};
      k.forEach(key => {
        const oldValueStr = localStorage.getItem(key);
        if(oldValueStr) {
            changes[key] = { oldValue: JSON.parse(oldValueStr), newValue: undefined };
        }
        localStorage.removeItem(key);
      });
      if (Object.keys(changes).length > 0) {
          storageListeners.forEach(listener => listener(changes, 'local'));
      }
      if (cb) cb();
      return Promise.resolve();
    },
    clear: (cb) => {
      localStorage.clear();
      if (cb) cb();
      return Promise.resolve();
    }
  };
  window.chrome.storage = {
    local: storageArea,
    sync: storageArea,
    managed: storageArea,
    onChanged: {
      addListener: (cb) => storageListeners.add(cb),
      removeListener: (cb) => storageListeners.delete(cb),
      hasListener: (cb) => storageListeners.has(cb)
    }
  };

  window.chrome.tabs = {
    query: mockAsync([{id: 1, url: window.location.href}]),
    create: (props) => {
      const url = props.url || '';
      const isExternal = url.match(/^[a-z]+:\/\//i) && !url.startsWith('file://') && !url.startsWith('capacitor://') && !url.startsWith('http://localhost');
      
      if (!isExternal) {
          const appFrame = document.getElementById('app-frame') || (window.parent ? window.parent.document.getElementById('app-frame') : null);
          if (appFrame) {
              appFrame.src = url;
              return Promise.resolve({id: 2});
          }
      }
      
      window.open(url, '_system');
      
      const tabId = Date.now();
      setTabUrl(tabId, url);
      
      return Promise.resolve({id: tabId});
    },
    update: (id, props) => {
      if(props.url) {
          window.open(props.url, '_system');
          return Promise.resolve({});
      }
      
      const url = getTabUrl(id);
      if (url) {
          window.open(url, '_system');
          return Promise.resolve({id: id, windowId: 1});
      }
      
      return Promise.reject(new Error("Tab URL not found in mobile storage"));
    },
    remove: (id, cb) => {
      removeTabUrl(id);
      if (cb) cb();
      return Promise.resolve();
    },
    getCurrent: mockAsync({id: 1}),
    sendMessage: (tabId, msg, cb) => { if(cb) cb(); },
    onUpdated: mockListener,
    onActivated: mockListener,
    onCreated: mockListener,
    onRemoved: mockListener,
    onSelectionChanged: mockListener
  };

  window.chrome.windows = {
    getAll: mockAsync([]),
    getCurrent: mockAsync({}),
    create: mockAsync({}),
    update: mockAsync({}),
    onFocusChanged: mockListener
  };

  window.chrome.webRequest = {
    onBeforeRequest: mockListener,
    onBeforeSendHeaders: mockListener,
    onSendHeaders: mockListener,
    onHeadersReceived: mockListener,
    onAuthRequired: mockListener,
    onResponseStarted: mockListener,
    onBeforeRedirect: mockListener,
    onCompleted: mockListener,
    onErrorOccurred: mockListener
  };

  window.chrome.webNavigation = {
    onBeforeNavigate: mockListener,
    onCommitted: mockListener,
    onDOMContentLoaded: mockListener,
    onCompleted: mockListener,
    onHistoryStateUpdated: mockListener
  };

  window.chrome.cookies = {
    get: mockAsync(null),
    getAll: mockAsync([]),
    set: mockAsync(null),
    remove: mockAsync(null),
    onChanged: mockListener
  };

  window.chrome.commands = { onCommand: mockListener };
  window.chrome.contextMenus = { create: () => {}, removeAll: () => {}, onClicked: mockListener };
  window.chrome.i18n = { getUILanguage: () => 'en-US', getMessage: (m) => m };
  window.chrome.extension = { getURL: (p) => p, getBackgroundPage: () => window.parent || window };
  window.chrome.management = { getAll: mockAsync([]) };
  window.chrome.idle = { queryState: mockAsync('active'), onStateChanged: mockListener };
  window.chrome.notifications = { create: mockAsync(), clear: mockAsync(), onClicked: mockListener };
  const link = document.createElement('link');
  link.rel = 'stylesheet';
  link.href = 'mobile-overrides.css';
  document.head.appendChild(link);
}
