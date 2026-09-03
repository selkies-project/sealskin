/**
 * Installs the mobile polyfill at module-evaluation time so that it exists
 * before `background.js` (imported next by mobile.js) runs its top-level code.
 * The hooks are late-bound: mobile.js fills them in once the host exists.
 */
import { installPolyfill } from './polyfill.js';

export const hooks = {
  openExternal: async (url) => { window.open(url, '_system'); },
  openPopup: () => {},
};

installPolyfill({
  openExternal: (url) => hooks.openExternal(url),
  openPopup: () => hooks.openPopup(),
});
