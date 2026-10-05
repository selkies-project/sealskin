/**
 * Web app launching page. The web app opens it in a tab at the click that
 * launches a session, so the tab shows what the launch waits on instead of a
 * blank page: `?id=<launch id>&app=<name>&logo=<url>&room=<0|1>` and, where
 * the web app already knows it, `&suffix=<session-origin suffix>`.
 *
 * It polls the launch's progress and, once the session is ready, takes this
 * tab to the session's own origin. The pages that started the launch talk to
 * it over a BroadcastChannel named by the launch id: the web app posts the
 * suffix, and the home page a failure the server never saw. Left without a
 * suffix, it probes for one itself.
 */

import { loadTranslator } from '../lib/i18n.js';
import { probeSessionOrigin } from '../lib/session-origin.js';

const POLL_MS = 600;
const POLL_SLOW_MS = 1500;
const SLOW_AFTER_MS = 30000;
// A launch the server has not heard of this long after it was sent did not arrive.
const UNSEEN_MS = 20000;
// How long the web app has to hand over the suffix before this page probes.
const SUFFIX_WAIT_MS = 3000;

const params = new URLSearchParams(location.search);
const id = params.get('id') || '';
const $ = (name) => document.getElementById(name);
const started = Date.now();
const seconds = () => Math.round((Date.now() - started) / 1000);

let t = (key) => key;
let suffix = params.get('suffix') || null;
let ownProbe = null;
let done = false;
// When the launch request last showed life: this page loading, an upload chunk, the request leaving.
let lastSign = Date.now();
let stage = null;
// The stages reached, in order, with the second each began at.
const steps = [];

function stageSentence(name, detail = {}) {
  const key = `web.launch.stages.${name}`;
  const text = t(key, { node: detail.node || '' });
  return text === key ? name : text;
}

function renderSteps() {
  $('steps').replaceChildren(...steps.map((step, i) => {
    const item = document.createElement('li');
    const label = document.createElement('span');
    const at = document.createElement('span');
    label.textContent = step.text;
    at.textContent = t('web.launch.seconds', { count: i + 1 < steps.length ? steps[i + 1].at - step.at : seconds() - step.at });
    if (i === steps.length - 1 && !done) item.className = 'current';
    item.append(label, at);
    return item;
  }));
}

function show(name, detail = {}, text = stageSentence(name, detail)) {
  $('stage').textContent = text;
  $('hint').hidden = name !== 'image';
  $('hint').textContent = name === 'image' ? t('web.launch.imageHint', { image: detail.image || '' }) : '';
  $('node').hidden = !detail.node;
  if (detail.node) $('node').textContent = t('web.launch.onNode', { node: detail.node });
  if (name !== stage) {
    stage = name;
    steps.push({ text, at: seconds() });
  } else {
    steps[steps.length - 1].text = text;
  }
  renderSteps();
}

function fail(message) {
  if (done) return;
  done = true;
  $('spinner').hidden = true;
  $('stage').textContent = t('web.launch.failed');
  $('hint').hidden = true;
  $('error').textContent = message === 'noSessionOrigin' ? t('popup.status.noSessionOrigin') : message;
  $('error').hidden = false;
  $('close').hidden = false;
  renderSteps();
}

/** The session-origin suffix: the web app's, or this page's own probe when none comes. */
async function sessionSuffix() {
  if (suffix) return suffix;
  await new Promise((resolve) => setTimeout(resolve, SUFFIX_WAIT_MS));
  if (suffix) return suffix;
  if (!ownProbe) {
    ownProbe = (async () => {
      const response = await fetch('/api/admin/status', {
        method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' }, body: '{}',
      });
      const domain = response.ok ? (await response.json()).session_domain : '';
      return probeSessionOrigin(location.hostname, location.port || '443', domain || '');
    })().catch(() => null);
  }
  return suffix || ownProbe;
}

async function enter(progress) {
  show('ready');
  const found = await sessionSuffix();
  if (!found) {
    fail('noSessionOrigin');
    return;
  }
  done = true;
  const port = location.port ? `:${location.port}` : '';
  location.replace(`https://${progress.session_id}.${found}${port}${progress.session_url}`);
}

async function poll() {
  if (done) return;
  let wait = Date.now() - started > SLOW_AFTER_MS ? POLL_SLOW_MS : POLL_MS;
  try {
    const response = await fetch(`/api/launch/progress/${encodeURIComponent(id)}`, { credentials: 'same-origin', cache: 'no-store' });
    if (response.status === 401) {
      fail(t('web.signIn.ended'));
    } else if (response.status === 404) {
      if (stage === null || stage === 'requesting') show('requesting');
      // Not final: a request held up on the way still shows up here.
      if (Date.now() - lastSign > UNSEEN_MS) $('stage').textContent = t('web.launch.unseen');
    } else if (response.ok) {
      const progress = await response.json();
      lastSign = Date.now();
      if (progress.stage === 'failed') fail(progress.error || t('web.launch.failed'));
      else if (progress.stage === 'ready') await enter(progress);
      else show(progress.stage, progress.detail || {});
    }
  } catch (e) {
    wait = POLL_SLOW_MS;
  }
  if (!done) setTimeout(poll, wait);
}

async function showLogo(logo) {
  if (/^https?:/.test(logo)) {
    $('app-logo').src = logo;
  } else if (logo.startsWith('/api/app_icon/')) {
    // An installed icon comes as JSON behind the sign-in.
    const response = await fetch(logo, { credentials: 'same-origin' });
    const icon = response.ok ? await response.json() : {};
    if (icon.icon_data_b64) $('app-logo').src = `data:image/png;base64,${icon.icon_data_b64}`;
  }
}

async function start() {
  t = await loadTranslator(navigator.language);
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
  showLogo(params.get('logo') || '').catch(() => {});
  setInterval(() => {
    if (done) return;
    $('elapsed').textContent = t('web.launch.seconds', { count: seconds() });
    renderSteps();
  }, 1000);

  if (!id) {
    fail(t('web.launch.failed'));
    return;
  }
  const channel = new BroadcastChannel(`sealskin-launch-${id}`);
  channel.onmessage = ({ data }) => {
    if (!data) return;
    if (data.suffix) suffix = data.suffix;
    if (data.uploading) {
      lastSign = Date.now();
      if (stage === null || stage === 'uploading' || stage === 'requesting') show('uploading', {}, t('web.launch.uploading', data.uploading));
    }
    if (data.posted) {
      lastSign = Date.now();
      if (stage === 'uploading') show('requesting');
    }
    if (data.error) fail(data.error);
  };
  channel.postMessage({ hello: true });
  show('requesting');
  poll();
}

start();
