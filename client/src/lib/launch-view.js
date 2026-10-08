/**
 * What a launch is waiting on, drawn on a page that has the launching
 * layout: the stage line, its hint, the node, the elapsed seconds, and the
 * list of stages reached. The web app's launching page and an application's
 * address page both show it.
 *
 * The page provides the elements by id: `stage`, `hint`, `node`, `elapsed`,
 * `steps`, `spinner`, `error`, `close`, and `app-logo`.
 */

/**
 * @param {function} t The translator.
 * @param {function(string): HTMLElement} $ Element lookup by id.
 * @returns {{show: function, fail: function, tick: function, showLogo: function, done: function(): boolean}}
 */
export function createLaunchView(t, $) {
  const started = Date.now();
  const seconds = () => Math.round((Date.now() - started) / 1000);
  let stage = null;
  let done = false;
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

  /**
   * Show the stage a launch reached.
   *
   * @param {string} name The stage, as the server names it.
   * @param {object} [detail] The stage's detail: `node`, `image`.
   * @param {string} [text] The sentence to show in place of the stage's own.
   */
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

  /**
   * End the wait with what went wrong.
   *
   * @param {string} message
   */
  function fail(message) {
    if (done) return;
    done = true;
    $('spinner').hidden = true;
    $('stage').textContent = t('web.launch.failed');
    $('hint').hidden = true;
    $('error').textContent = message;
    $('error').hidden = false;
    $('close').hidden = false;
    renderSteps();
  }

  /** Bring the elapsed seconds up to date; once a second. */
  function tick() {
    if (done) return;
    $('elapsed').textContent = t('web.launch.seconds', { count: seconds() });
    renderSteps();
  }

  /**
   * Show the application's logo: a store's by its address, an uploaded one through the API.
   *
   * @param {string} logo
   */
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

  return {
    show,
    fail,
    tick,
    showLogo,
    done: () => done,
    finish: () => { done = true; },
    stage: () => stage,
  };
}
