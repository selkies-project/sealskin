/**
 * Pending launch contexts that carry a File.
 *
 * `chrome.runtime.sendMessage` only carries JSON, so a context holding a File
 * (the upload page, mobile file pickers) cannot be handed to the background.
 * Host pages of the same shell share an origin, so the File is parked in
 * IndexedDB here and the JSON part of the context travels through the
 * background as usual with `hasFile: true`. The popup host reunites the two.
 */

const DB_NAME = 'sealskin-shell';
const STORE = 'pending';
const KEY = 'context';

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

/** @param {File|Blob} file */
export function storePendingFile(file) {
  return run('readwrite', (store) => store.put(file, KEY));
}

/** @returns {Promise<File|Blob|undefined>} The stored file, removed on read. */
export async function takePendingFile() {
  const file = await run('readonly', (store) => store.get(KEY));
  if (file !== undefined) {
    await run('readwrite', (store) => store.delete(KEY));
  }
  return file;
}
