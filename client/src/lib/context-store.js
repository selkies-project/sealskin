/**
 * Pending launch contexts that carry a File.
 *
 * `chrome.runtime.sendMessage` only carries JSON, so a context holding a File
 * (the upload page, mobile file pickers) cannot be handed to the background.
 * Host pages of the same shell share an origin, so the File is parked in
 * IndexedDB here and the JSON part of the context travels through the
 * background as usual with `hasFile: true`. The popup host reunites the two.
 * The web app's service worker parks what the share target received here too.
 */

const DB_NAME = 'sealskin-shell';
const STORE = 'pending';
const KEY = 'context';
const SHARED_KEY = 'shared';

function openDb() {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(DB_NAME, 1);
    req.onupgradeneeded = () => {
      req.result.createObjectStore(STORE);
    };
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}

function run(mode, fn) {
  return openDb().then((db) => new Promise((resolve, reject) => {
    const tx = db.transaction(STORE, mode);
    const store = tx.objectStore(STORE);
    const req = fn(store);
    tx.oncomplete = () => { db.close(); resolve(req && req.result); };
    tx.onerror = () => { db.close(); reject(tx.error); };
    tx.onabort = () => { db.close(); reject(tx.error); };
  }));
}

async function take(key) {
  const value = await run('readonly', (store) => store.get(key));
  if (value !== undefined) {
    await run('readwrite', (store) => store.delete(key));
  }
  return value;
}

/** @param {File|Blob} file */
export async function storePendingFile(file) {
  try {
    await run('readwrite', (store) => store.put(file, KEY));
  } catch (e) {
    // WebKit keeps no Blob in IndexedDB in a private session; the bytes still go in.
    const bytes = await file.arrayBuffer();
    await run('readwrite', (store) => store.put({ name: file.name, type: file.type, bytes }, KEY));
  }
}

/** @returns {Promise<File|Blob|undefined>} The stored file, removed on read. */
export async function takePendingFile() {
  const value = await take(KEY);
  return value && value.bytes ? new File([value.bytes], value.name, { type: value.type }) : value;
}

/** @param {object} context A launch context the web app picks up when it opens. */
export function storeShared(context) {
  return run('readwrite', (store) => store.put(context, SHARED_KEY));
}

/** @returns {Promise<object|undefined>} The shared context, removed on read. */
export function takeShared() {
  return take(SHARED_KEY);
}
