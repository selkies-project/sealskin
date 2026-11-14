/**
 * Drawing a dashboard widget into its tile from what the server read for
 * it (`/api/dashboard/data`). Everything the data holds is a stranger's
 * text: it is escaped, and a link is used only when it is a web address.
 *
 * A frame widget is the administrator's own HTML, written into a sandboxed
 * page (`widget-frame.html`) that has an origin of its own; an embed is
 * another site in a frame, which keeps its own origin unless that origin is
 * this page's.
 */

import { escapeHtml, formatBytes, timeAgo } from './dom.js';

export const WIDGET_ICONS = {
  clock: 'fa-clock', notes: 'fa-sticky-note', weather: 'fa-cloud-sun', stocks: 'fa-chart-line', feed: 'fa-rss', calendar: 'fa-calendar-alt',
  api: 'fa-chart-bar', monitor: 'fa-heartbeat', text: 'fa-bullhorn', frame: 'fa-code', embed: 'fa-window-maximize',
};
const FRAME_SANDBOX = 'allow-scripts allow-forms allow-popups allow-popups-to-escape-sandbox';
const webLink = (value) => (/^https?:\/\/\S+$/i.test(String(value || '')) ? String(value) : '');
const linked = (label, href) => (webLink(href)
  ? `<a href="${escapeHtml(href)}" target="_blank" rel="noopener noreferrer">${escapeHtml(label)}</a>`
  : `<span>${escapeHtml(label)}</span>`);

/** The Font Awesome icon and the translation key of a WMO weather code. */
function sky(code, day = true) {
  if (code === 0) return [day ? 'fa-sun' : 'fa-moon', 'clear'];
  if (code <= 2) return [day ? 'fa-cloud-sun' : 'fa-cloud-moon', 'partly'];
  if (code === 3) return ['fa-cloud', 'cloudy'];
  if (code <= 48) return ['fa-smog', 'fog'];
  if (code <= 57) return ['fa-cloud-rain', 'drizzle'];
  if (code <= 67) return ['fa-cloud-showers-heavy', 'rain'];
  if (code <= 77) return ['fa-snowflake', 'snow'];
  if (code <= 82) return ['fa-cloud-showers-heavy', 'showers'];
  if (code <= 86) return ['fa-snowflake', 'snow'];
  return ['fa-bolt', 'storm'];
}

const round = (value) => (typeof value === 'number' ? Math.round(value) : '–');

/**
 * A forecast in three parts, of which the tile's size decides how much
 * shows (the stylesheet's container queries): now, the coming hours, and
 * the coming days. The times are the place's own, read as written.
 */
function weather(data, t, locale) {
  const unit = data.units === 'imperial' ? '°F' : '°C';
  const [icon, name] = sky(data.code, data.day);
  const today = (data.days || [])[0] || {};
  const clock = (stamp) => {
    const [hour, minute] = String(stamp || '').slice(11, 16).split(':').map(Number);
    return Number.isFinite(hour) ? new Date(2000, 0, 1, hour, minute || 0).toLocaleTimeString(locale, { hour: 'numeric', ...(minute ? { minute: '2-digit' } : {}) }) : '';
  };
  const rain = (chance) => (typeof chance === 'number' && chance >= 20 ? `<small class="rain"><i class="fas fa-tint"></i>${Math.round(chance)}%</small>` : '<small class="rain"></small>');
  const hours = (data.hours || []).slice(1, 19).map((hour) => `<li><span>${escapeHtml(clock(hour.time))}</span><i class="fas ${sky(hour.code, hour.day)[0]}"></i><b>${round(hour.temperature)}°</b>${rain(hour.rain)}</li>`).join('');
  const lows = (data.days || []).map((day) => day.min).filter((value) => typeof value === 'number');
  const highs = (data.days || []).map((day) => day.max).filter((value) => typeof value === 'number');
  const [floor, span] = [Math.min(...lows), Math.max(...highs) - Math.min(...lows) || 1];
  const days = (data.days || []).slice(1, 7).map((day) => {
    const when = new Date(`${day.date}T12:00:00`);
    const from = typeof day.min === 'number' ? ((day.min - floor) / span) * 100 : 0;
    const to = typeof day.max === 'number' ? ((day.max - floor) / span) * 100 : 100;
    return `<li><span>${escapeHtml(when.toLocaleDateString(locale, { weekday: 'short' }))}</span><i class="fas ${sky(day.code)[0]}"></i>${rain(day.rain)}
      <span class="low">${round(day.min)}°</span><span class="range"><span style="left:${from.toFixed(0)}%;right:${(100 - to).toFixed(0)}%"></span></span><span class="high">${round(day.max)}°</span></li>`;
  }).join('');
  const sun = today.sunrise && today.sunset ? `<small class="sun"><i class="fas fa-sun"></i> ${escapeHtml(clock(today.sunrise))} – ${escapeHtml(clock(today.sunset))}</small>` : '';
  return `
    <div class="w-weather">
      <div class="now"><i class="fas ${icon}"></i><strong>${round(data.temperature)}${unit}</strong>
        <div class="facts"><span>${escapeHtml(t(`web.board.sky.${name}`))}${typeof today.max === 'number' ? ` <b>${round(today.max)}° / ${round(today.min)}°</b>` : ''}</span>
          <small>${escapeHtml(t('web.board.weatherFacts', { feels: round(data.feels), humidity: round(data.humidity), wind: round(data.wind), speed: data.units === 'imperial' ? 'mph' : 'km/h' }))}</small>${sun}</div></div>
      <ul class="hours">${hours}</ul>
      <ul class="days">${days}</ul>
    </div>`;
}

/** A month of closes as a line over a faint fill, in the color of the day's change; it stretches to the room it is given. */
function sparkline(points, up) {
  if (!points || points.length < 2) return '';
  const low = Math.min(...points);
  const span = Math.max(...points) - low || 1;
  const path = points.map((point, index) => `${((index / (points.length - 1)) * 100).toFixed(1)},${(28 - ((point - low) / span) * 26).toFixed(1)}`).join(' ');
  return `<svg class="spark ${up ? 'up' : 'down'}" viewBox="0 0 100 30" preserveAspectRatio="none" aria-hidden="true"><polygon points="0,30 ${path} 100,30"/><polyline points="${path}"/></svg>`;
}

function stocks(data, t, locale) {
  const money = (value) => (typeof value === 'number' ? value.toLocaleString(locale, { minimumFractionDigits: 2, maximumFractionDigits: value < 10 ? 4 : 2 }) : '–');
  return `<ul class="w-rows w-stocks">${data.quotes.map((quote) => {
    if (!quote.ok) return `<li><div class="q"><span class="sym"><b>${escapeHtml(quote.symbol)}</b><small>${escapeHtml(t('web.board.noQuote'))}</small></span></div></li>`;
    const up = (quote.change || 0) >= 0;
    const percent = typeof quote.percent === 'number' ? `${up ? '+' : ''}${quote.percent.toFixed(2)}%` : '';
    return `<li title="${escapeHtml(`${quote.name} · ${quote.currency}`)}"><div class="q"><span class="sym"><b>${escapeHtml(quote.symbol)}</b><small>${escapeHtml(quote.name)}</small></span>
      ${sparkline(quote.points, up)}<span class="val"><b>${escapeHtml(money(quote.price))}</b><small class="${up ? 'up' : 'down'}">${escapeHtml(percent)}</small></span></div></li>`;
  }).join('')}</ul>`;
}

function feed(data, t) {
  if (!data.items.length) return `<p class="w-empty">${escapeHtml(t('web.board.feedEmpty'))}</p>`;
  return `<ul class="w-list">${data.items.map((item) => `
    <li>${linked(item.title, item.link)}<small>${escapeHtml([item.source, item.published ? timeAgo(item.published, t) : ''].filter(Boolean).join(' · '))}</small></li>`).join('')}</ul>`;
}

function calendar(data, t, locale) {
  if (!data.events.length) return `<p class="w-empty">${escapeHtml(t('web.board.calendarEmpty'))}</p>`;
  return `<ul class="w-list w-events">${data.events.map((event) => {
    const start = new Date(event.start * 1000);
    const day = start.toLocaleDateString(locale, { weekday: 'short', month: 'short', day: 'numeric', ...(event.all_day ? { timeZone: 'UTC' } : {}) });
    const time = event.all_day ? t('web.board.allDay') : start.toLocaleTimeString(locale, { hour: 'numeric', minute: '2-digit' });
    return `<li><span class="when"><b>${escapeHtml(day)}</b><small>${escapeHtml(time)}</small></span><span><span>${escapeHtml(event.title || t('web.board.untitled'))}</span>${event.location ? `<small>${escapeHtml(event.location)}</small>` : ''}</span></li>`;
  }).join('')}</ul>`;
}

/** Write a picked value the way its mapping asks. */
function formatValue(value, format, locale, t) {
  if (value === null || value === undefined || value === '') return '–';
  const number = Number(value);
  const numeric = typeof value !== 'boolean' && Number.isFinite(number);
  if (format === 'number' && numeric) return Math.round(number).toLocaleString(locale);
  if (format === 'float' && numeric) return number.toLocaleString(locale, { maximumFractionDigits: 2 });
  if (format === 'size' && numeric) return number.toLocaleString(locale);
  if (format === 'percent' && numeric) return `${number.toLocaleString(locale, { maximumFractionDigits: 1 })}%`;
  if (format === 'bytes' && numeric) return formatBytes(number, t);
  if (format === 'bitrate' && numeric) return `${formatBytes(number / 8, t)}/s`;
  if (format === 'duration' && numeric) {
    const hours = Math.floor(number / 3600);
    const minutes = Math.floor((number % 3600) / 60);
    return hours ? `${hours}h ${minutes}m` : minutes ? `${minutes}m ${Math.floor(number % 60)}s` : `${Math.floor(number)}s`;
  }
  if (format === 'date' || format === 'relativeDate') {
    const when = new Date(numeric ? (number < 1e11 ? number * 1000 : number) : value);
    if (!Number.isNaN(when.getTime())) return format === 'date' ? when.toLocaleString(locale, { dateStyle: 'medium', timeStyle: 'short' }) : timeAgo(when.getTime() / 1000, t);
  }
  return String(value);
}

function api(data, t, locale) {
  if (data.display === 'dynamic-list') {
    if (!data.rows.length) return `<p class="w-empty">${escapeHtml(t('web.board.apiEmpty'))}</p>`;
    return `<ul class="w-rows">${data.rows.map((row) => `<li>${linked(row.name, row.link)}<b>${row.value === null ? '' : escapeHtml(formatValue(row.value, data.format, locale, t))}</b></li>`).join('')}</ul>`;
  }
  if (!data.fields.length) return `<p class="w-empty">${escapeHtml(t('web.board.apiNoMappings'))}</p>`;
  const cell = (field) => `<span>${escapeHtml(field.prefix || '')}${escapeHtml(formatValue(field.value, field.format, locale, t))}${escapeHtml(field.suffix || '')}</span>`;
  return data.display === 'list'
    ? `<ul class="w-rows">${data.fields.map((field) => `<li><span>${escapeHtml(field.label)}</span><b>${cell(field)}</b></li>`).join('')}</ul>`
    : `<div class="w-stats">${data.fields.map((field) => `<div><strong>${cell(field)}</strong><small>${escapeHtml(field.label)}</small></div>`).join('')}</div>`;
}

function monitor(data, t) {
  return `<ul class="w-rows w-sites">${data.sites.map((site) => `
    <li><span><i class="dot ${site.up ? 'up' : 'down'}"></i>${escapeHtml(site.name)}</span><b>${site.up ? `${site.ms} ms` : escapeHtml(site.status ? String(site.status) : t('web.board.down'))}</b></li>`).join('')}</ul>`;
}

/** An administrator's notice: plain text, its web addresses as links. */
function notice(data) {
  const html = escapeHtml(data.text).replace(/https?:\/\/[^\s<]+[^\s<.,;:!?)\]]/g, (url) => `<a href="${url}" target="_blank" rel="noopener noreferrer">${url}</a>`);
  return `<div class="w-text tone-${escapeHtml(data.tone || 'neutral')}">${html}</div>`;
}

function clock(body, data, locale) {
  const zone = data.timezone || undefined;
  const paint = () => {
    const now = new Date();
    let time;
    let date;
    try {
      time = now.toLocaleTimeString(locale, { hour: 'numeric', minute: '2-digit', ...(data.seconds ? { second: '2-digit' } : {}), timeZone: zone });
      date = now.toLocaleDateString(locale, { weekday: 'long', month: 'long', day: 'numeric', timeZone: zone });
    } catch (error) {
      time = now.toLocaleTimeString(locale);
      date = now.toLocaleDateString(locale);
    }
    // Unlabeled, a clock of another zone says its city: the last part of the zone's name.
    const place = data.label || (zone ? zone.split('/').pop().replace(/_/g, ' ') : '');
    body.innerHTML = `<div class="w-clock"><strong>${escapeHtml(time)}</strong><span>${escapeHtml(date)}</span>${place ? `<small>${escapeHtml(place)}</small>` : ''}</div>`;
  };
  paint();
  const timer = setInterval(() => (body.isConnected ? paint() : clearInterval(timer)), data.seconds ? 1000 : 15000);
}

/** The administrator's page in its sandbox: written once, then handed each new answer. */
function frame(body, data, theme) {
  let holder = body.querySelector('iframe.w-frame');
  if (holder && holder.dataset.html === data.html && holder.contentWindow) {
    holder.contentWindow.postMessage({ type: 'sealskin-widget-data', data: data.data }, '*');
    return;
  }
  body.innerHTML = '';
  holder = document.createElement('iframe');
  holder.className = 'w-frame';
  holder.setAttribute('sandbox', FRAME_SANDBOX);
  holder.setAttribute('referrerpolicy', 'no-referrer');
  holder.setAttribute('allow', '');
  holder.dataset.html = data.html;
  const ready = (event) => {
    // A frame taken off the page before it loaded has no window left to answer.
    if (!holder.isConnected) {
      window.removeEventListener('message', ready);
      return;
    }
    if (event.source !== holder.contentWindow || !event.data || event.data.type !== 'sealskin-widget-ready') return;
    window.removeEventListener('message', ready);
    holder.contentWindow.postMessage({ type: 'sealskin-widget', html: data.html, data: data.data, theme }, '*');
  };
  window.addEventListener('message', ready);
  holder.src = 'widget-frame.html';
  body.append(holder);
}

function embed(body, data) {
  const url = webLink(data.url);
  const holder = body.querySelector('iframe.w-frame');
  if (!url || (holder && holder.dataset.url === url)) return;
  body.innerHTML = '';
  const page = document.createElement('iframe');
  page.className = 'w-frame';
  // Another site keeps its own origin, so its sign-in works; a page of this origin gets none, and so no way to this page.
  const foreign = new URL(url, location.href).origin !== location.origin;
  page.setAttribute('sandbox', `${FRAME_SANDBOX}${foreign ? ' allow-same-origin' : ''}`);
  page.setAttribute('referrerpolicy', 'no-referrer');
  page.setAttribute('loading', 'lazy');
  page.dataset.url = url;
  page.src = url;
  body.append(page);
}

/**
 * Draw a widget's answer into its tile's body.
 *
 * @param {HTMLElement} body
 * @param {string} type The widget's type.
 * @param {{ok: boolean, data?: object, error?: string}} answer
 * @param {object} context
 * @param {function} context.t Translator.
 * @param {string} context.locale
 * @param {object} [context.theme] Colors handed to a frame widget.
 */
export function drawWidget(body, type, answer, { t, locale, theme = {} }) {
  if (!answer || !answer.ok) {
    body.innerHTML = `<p class="w-error"><i class="fas fa-exclamation-triangle"></i> ${escapeHtml((answer && answer.error) || t('web.board.widgetFailed'))}</p>`;
    return;
  }
  const { data } = answer;
  if (type === 'clock') clock(body, data, locale);
  else if (type === 'frame') frame(body, data, theme);
  else if (type === 'embed') embed(body, data);
  else if (type === 'weather') body.innerHTML = weather(data, t, locale);
  else if (type === 'stocks') body.innerHTML = stocks(data, t, locale);
  else if (type === 'feed') body.innerHTML = feed(data, t);
  else if (type === 'calendar') body.innerHTML = calendar(data, t, locale);
  else if (type === 'api') body.innerHTML = api(data, t, locale);
  else if (type === 'monitor') body.innerHTML = monitor(data, t);
  else if (type === 'text') body.innerHTML = notice(data);
}
