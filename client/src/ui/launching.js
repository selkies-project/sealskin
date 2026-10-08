/**
 * Web app launching page. The web app opens it in a tab at the click that
 * launches a session in a tab of its own, so the tab shows what the launch
 * waits on instead of a blank page: `?id=<launch id>&app=<name>&room=<0|1>`.
 *
 * It polls the launch's progress and, once the session is ready, takes this
 * tab to the session: on this origin, or, with session isolation, on the
 * session's own origin. The pages that started the launch talk to it over a
 * BroadcastChannel named by the launch id: the web app posts the app's logo
 * and where sessions open (the session-origin suffix, or that they open on
 * this origin), and the home page a failure the server never saw. Left
 * without an answer, it asks the server itself. Neither the logo nor the
 * suffix is ever taken from this page's address, where a link made
 * elsewhere could name an image to load or a host to send the session, and
 * the access token its URL carries, to.
 */

import { loadTranslator } from '../lib/i18n.js';
import { createLaunchView } from '../lib/launch-view.js';
import { probeSessionOrigin } from '../lib/session-origin.js';

const POLL_MS = 600;
const POLL_SLOW_MS = 1500;
const SLOW_AFTER_MS = 30000;
// A launch the server has not heard of this long after it was sent did not arrive.
const UNSEEN_MS = 20000;
// How long the web app has to say where sessions open before this page asks the server.
const SUFFIX_WAIT_MS = 3000;

const params = new URLSearchParams(location.search);
const id = params.get('id') || '';
const $ = (name) => document.getElementById(name);
const started = Date.now();

let t = (key) => key;
let view = null;
// Where sessions open: `{suffix}` for their own origins, `{shared: true}` for this one, null until known.
let origin = null;
let logoShown = false;
let ownProbe = null;
// When the launch request last showed life: this page loading, an upload chunk, the request leaving.
let lastSign = Date.now();

function fail(message) {
  view.fail(message === 'noSessionOrigin' ? t('popup.status.noSessionOrigin') : message);
}

/** Where sessions open: as the web app said, or as the server says when no word comes. */
async function sessionOrigin() {
  if (origin) return origin;
  await new Promise((resolve) => setTimeout(resolve, SUFFIX_WAIT_MS));
  if (origin) return origin;
  if (!ownProbe) {
    ownProbe = (async () => {
      const response = await fetch('/api/admin/status', {
        method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' }, body: '{}',
      });
      const status = response.ok ? await response.json() : {};
      if (!status.session_isolation) return { shared: true };
      const suffix = await probeSessionOrigin(location.hostname, location.port || '443', status.session_domain || '');
      return suffix ? { suffix } : null;
    })().catch(() => null);
  }
  return origin || ownProbe;
}

async function enter(progress) {
  view.show('ready');
  const found = await sessionOrigin();
  if (!found) {
    fail('noSessionOrigin');
    return;
  }
  view.finish();
  if (found.shared) {
    location.replace(progress.session_url);
    return;
  }
  const port = location.port ? `:${location.port}` : '';
  location.replace(`https://${progress.session_id}.${found.suffix}${port}${progress.session_url}`);
}

async function poll() {
  if (view.done()) return;
  let wait = Date.now() - started > SLOW_AFTER_MS ? POLL_SLOW_MS : POLL_MS;
  try {
    const response = await fetch(`/api/launch/progress/${encodeURIComponent(id)}`, { credentials: 'same-origin', cache: 'no-store' });
    if (response.status === 401) {
      fail(t('web.signIn.ended'));
    } else if (response.status === 404) {
      if (view.stage() === null || view.stage() === 'requesting') view.show('requesting');
      // Not final: a request held up on the way still shows up here.
      if (Date.now() - lastSign > UNSEEN_MS) $('stage').textContent = t('web.launch.unseen');
    } else if (response.ok) {
      const progress = await response.json();
      lastSign = Date.now();
      if (progress.stage === 'failed') fail(progress.error || t('web.launch.failed'));
      else if (progress.stage === 'ready') await enter(progress);
      else view.show(progress.stage, progress.detail || {});
    }
  } catch (e) {
    wait = POLL_SLOW_MS;
  }
  if (!view.done()) setTimeout(poll, wait);
}

async function start() {
  t = await loadTranslator(navigator.language);
  view = createLaunchView(t, $);
  const app = params.get('app') || 'SealSkin';
  document.title = t('web.launch.title', { app });
  $('app-name').textContent = app;
  $('close').textContent = t('common.close');
  $('close-hint').textContent = t('web.launch.closeTab');
  $('close').addEventListener('click', () => {
    window.close();
    // A browser that refuses leaves the page here.
    setTimeout(() => { $('close-hint').hidden = false; }, 300);
  });
  setInterval(() => view.tick(), 1000);

  if (!id) {
    fail(t('web.launch.failed'));
    return;
  }
  const channel = new BroadcastChannel(`sealskin-launch-${id}`);
  channel.onmessage = ({ data }) => {
    if (!data) return;
    if (typeof data.logo === 'string' && !logoShown) {
      logoShown = true;
      view.showLogo(data.logo).catch(() => {});
    }
    if (data.suffix) origin = { suffix: data.suffix };
    if (data.shared) origin = { shared: true };
    if (data.uploading) {
      lastSign = Date.now();
      const stage = view.stage();
      if (stage === null || stage === 'uploading' || stage === 'requesting') view.show('uploading', {}, t('web.launch.uploading', data.uploading));
    }
    if (data.posted) {
      lastSign = Date.now();
      if (view.stage() === 'uploading') view.show('requesting');
    }
    if (data.error) fail(data.error);
  };
  channel.postMessage({ hello: true });
  view.show('requesting');
  poll();
}

start();
