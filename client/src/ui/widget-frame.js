/**
 * The page a dashboard's frame widget is written into. The server serves it
 * sandboxed (`WIDGET_FRAME_CSP`): an origin of its own, with no cookie, no
 * storage, and no handle on the page around it. The home page posts the
 * administrator's HTML here once, and this page becomes it.
 *
 * The widget's script reads `window.sealskin`: `data` is the answer of the
 * widget's `source`, `theme` the page's colors, and `onData(callback)` is
 * called with each later answer.
 */
(() => {
  const PREAMBLE = (state) => `<script>(() => {
    const listeners = [];
    const state = ${JSON.stringify(state).replace(/</g, '\\u003c')};
    window.sealskin = { data: state.data, theme: state.theme, onData(callback) { listeners.push(callback); } };
    window.addEventListener('message', (event) => {
      if (event.source !== window.parent || !event.data || event.data.type !== 'sealskin-widget-data') return;
      window.sealskin.data = event.data.data;
      listeners.forEach((callback) => callback(event.data.data));
    });
  })();</script>`;
  const BASE = `<style>html{color-scheme:dark}body{margin:0;padding:12px;font:14px/1.45 Inter,system-ui,sans-serif;color:#eaf0f8;background:transparent}a{color:inherit}</style>`;

  function take(event) {
    if (event.source !== window.parent || !event.data || event.data.type !== 'sealskin-widget') return;
    window.removeEventListener('message', take);
    const { html, data, theme } = event.data;
    document.open();
    document.write(PREAMBLE({ data, theme }) + BASE + String(html || ''));
    document.close();
  }
  window.addEventListener('message', take);
  window.parent.postMessage({ type: 'sealskin-widget-ready' }, '*');
})();
