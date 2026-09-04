#!/usr/bin/env node
/**
 * SealSkin client build.
 *
 * One source tree, three outputs (see docs/content/development.md, "The client"):
 *   dist/ui         served by the server under /ui/ (hashed, minified)
 *   dist/extension  contents of the browser extension zip (unhashed)
 *   dist/mobile     contents of the Capacitor web dir (unhashed)
 *
 * Entries are discovered from the HTML pages: every local <script src> and
 * <link rel="stylesheet" href> becomes an esbuild entry and the HTML is
 * rewritten to the emitted file name. A <script type="module"> is bundled as
 * ESM, a plain <script> as a classic IIFE bundle.
 *
 * Usage: node build.mjs [--watch] [--target ui|extension|mobile]
 */
import { build } from 'esbuild';
import { createHash } from 'node:crypto';
import { execSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const CLIENT_DIR = path.dirname(fileURLToPath(import.meta.url));
const REPO_DIR = path.resolve(CLIENT_DIR, '..');
const SRC = path.join(CLIENT_DIR, 'src');
const DIST = path.join(CLIENT_DIR, 'dist');
const VENDOR = path.join(CLIENT_DIR, 'vendor');
const ICONS_SRC = path.join(REPO_DIR, 'browser_extension', 'icons');
const MANIFEST_DIR = path.join(REPO_DIR, 'browser_extension');
const MOBILE_NODE_MODULES = path.join(REPO_DIR, 'mobile', 'node_modules');

const VERSION = fs.readFileSync(path.join(REPO_DIR, 'VERSION'), 'utf8').trim();
const BRIDGE_VERSION = 1;
const LANGS = fs.readdirSync(path.join(SRC, 'i18n')).filter((f) => f.endsWith('.json')).map((f) => f.slice(0, -5));

// Translation blocks the shells always bundle whole, on top of the keys the
// build discovers in the shell sources (see collectI18nKeys).
const DEFAULT_SHELL_NAMESPACES = ['shell'];
const DEFAULT_MOBILE_SHARED_PAGES = ['connect.html'];

const args = process.argv.slice(2);
const WATCH = args.includes('--watch');
const ONLY = args.includes('--target') ? args[args.indexOf('--target') + 1] : null;
let strictFailures = 0;

// ---------------------------------------------------------------------------
// helpers

function log(...a) { console.log('[build]', ...a); }
function warn(...a) { console.warn('[build] WARNING:', ...a); }

function readJson(p, fallback) {
  try { return JSON.parse(fs.readFileSync(p, 'utf8')); } catch (e) { return fallback; }
}

function rmrf(p) { fs.rmSync(p, { recursive: true, force: true }); }
function mkdirp(p) { fs.mkdirSync(p, { recursive: true }); }

function copyDir(src, dest) {
  if (!fs.existsSync(src)) { warn(`missing directory ${rel(src)}`); return; }
  mkdirp(dest);
  fs.cpSync(src, dest, { recursive: true });
}

function rel(p) { return path.relative(REPO_DIR, p); }

function hashOf(content) {
  return createHash('sha256').update(content).digest('hex').slice(0, 8).toUpperCase();
}

function isLocalRef(ref) {
  return ref && !/^(https?:)?\/\//i.test(ref) && !/^(data|blob|chrome-extension|moz-extension|capacitor):/i.test(ref) && !ref.startsWith('#');
}

function deepMerge(base, over) {
  if (Array.isArray(over) || typeof over !== 'object' || over === null) return over;
  const out = { ...(typeof base === 'object' && base !== null && !Array.isArray(base) ? base : {}) };
  for (const [k, v] of Object.entries(over)) {
    out[k] = (k in out) ? deepMerge(out[k], v) : v;
  }
  return out;
}

function pick(dict, prefixes) {
  const out = {};
  for (const prefix of prefixes) {
    const parts = prefix.split('.');
    let src = dict;
    let ok = true;
    for (const p of parts) {
      if (src && typeof src === 'object' && p in src) src = src[p]; else { ok = false; break; }
    }
    if (!ok) continue;
    let dst = out;
    for (let i = 0; i < parts.length - 1; i++) {
      dst[parts[i]] = dst[parts[i]] || {};
      dst = dst[parts[i]];
    }
    dst[parts[parts.length - 1]] = src;
  }
  return out;
}

// ---------------------------------------------------------------------------
// i18n

const I18N_SOURCE = Object.fromEntries(LANGS.map((l) => [l, readJson(path.join(SRC, 'i18n', `${l}.json`), {})]));

function mergedLanguage(lang) {
  return deepMerge(I18N_SOURCE.en || {}, I18N_SOURCE[lang]);
}

/**
 * Emit the full dictionary per language for the served UI, minified and
 * content-hashed. Returns the map used for __I18N_FILES__.
 */
function emitI18n(outDir) {
  const files = {};
  mkdirp(path.join(outDir, 'i18n'));
  for (const lang of LANGS) {
    const content = JSON.stringify(mergedLanguage(lang));
    const name = `${lang}.${hashOf(content)}.json`;
    fs.writeFileSync(path.join(outDir, 'i18n', name), content);
    files[lang] = `i18n/${name}`;
  }
  return files;
}

/** Unhashed language file names for the shells; known before bundling. */
function shellI18nFiles() {
  return Object.fromEntries(LANGS.map((lang) => [lang, `i18n/${lang}.json`]));
}

function lookupKey(dict, key) {
  return key.split('.').reduce((obj, k) => (obj && typeof obj === 'object' && k in obj) ? obj[k] : undefined, dict);
}

// A quoted literal shaped like a translation key: dotted, no spaces.
const KEY_LITERAL_RE = /(['"])([a-z][A-Za-z0-9_]*(?:\.[A-Za-z0-9_]+)+)\1/g;
const KEY_ATTR_RE = /data-i18n(?:-placeholder|-title)?\s*=\s*["']([^"']+)["']/g;

/**
 * Translation keys a set of bundled sources reference: `data-i18n*`
 * attributes in HTML and quoted dotted literals in JavaScript that resolve in
 * the English dictionary. Keys built at runtime (template literals) are not
 * found; the blocks they live in are listed in src/shell/i18n-namespaces.json.
 */
function collectI18nKeys(sources) {
  const en = I18N_SOURCE.en || {};
  const keys = new Set();
  for (const file of sources) {
    if (!fs.existsSync(file)) continue;
    const text = fs.readFileSync(file, 'utf8');
    const re = file.endsWith('.html') ? KEY_ATTR_RE : KEY_LITERAL_RE;
    for (const m of text.matchAll(re)) {
      const key = file.endsWith('.html') ? m[1] : m[2];
      if (lookupKey(en, key) !== undefined) keys.add(key);
    }
  }
  return keys;
}

/**
 * Emit the shell subset per language: the keys the bundled shell sources
 * reference plus the whole blocks listed in i18n-namespaces.json, pretty
 * printed so the extension reviewers can read them. The shells carry these
 * files so the connection page reads in the user's language with no server.
 */
function emitShellI18n(outDir, sources) {
  const namespaces = readJson(path.join(SRC, 'shell', 'i18n-namespaces.json'), DEFAULT_SHELL_NAMESPACES);
  const keys = [...new Set([...namespaces, ...collectI18nKeys(sources)])].sort();
  mkdirp(path.join(outDir, 'i18n'));
  for (const lang of LANGS) {
    const dict = pick(mergedLanguage(lang), keys);
    fs.writeFileSync(path.join(outDir, 'i18n', `${lang}.json`), JSON.stringify(dict, null, 2) + '\n');
  }
  log(`shell i18n: ${keys.length} key(s) for ${LANGS.length} language(s)`);
  return keys;
}

// Bare specifier the background script imports the context menu titles from;
// the build aliases it to a module generated from `background.contextMenu`.
const CONTEXT_MENU_MODULE = 'sealskin-i18n/context-menu';
const GENERATED_DIR = path.join(DIST, '.generated');

/**
 * Write the context menu titles per language as a pretty-printed ES module
 * and return the alias map for esbuild. The menus are registered before any
 * page could fetch a language file, so the table is bundled; esbuild keeps
 * the module's formatting, which leaves it readable in the unminified shells.
 */
function generateContextMenuModule() {
  const titles = {};
  for (const lang of LANGS) titles[lang] = lookupKey(mergedLanguage(lang), 'background.contextMenu') || {};
  mkdirp(GENERATED_DIR);
  const file = path.join(GENERATED_DIR, 'context-menu-strings.js');
  fs.writeFileSync(file, `// Generated by build.mjs from background.contextMenu of src/i18n/*.json.\nexport const contextMenuTitles = ${JSON.stringify(titles, null, 2)};\n`);
  return { [CONTEXT_MENU_MODULE]: file };
}

// ---------------------------------------------------------------------------
// HTML discovery / rewriting

const SCRIPT_RE = /<script\b([^>]*)\bsrc\s*=\s*["']([^"']+)["']([^>]*)>/gi;
const LINK_RE = /<link\b([^>]*)\bhref\s*=\s*["']([^"']+)["']([^>]*)>/gi;

function discover(htmlPath) {
  const html = fs.readFileSync(htmlPath, 'utf8');
  const dir = path.dirname(htmlPath);
  const refs = [];
  for (const m of html.matchAll(SCRIPT_RE)) {
    const ref = m[2];
    if (!isLocalRef(ref)) continue;
    const attrs = m[1] + ' ' + m[3];
    refs.push({ kind: 'js', ref, abs: path.resolve(dir, ref), format: /type\s*=\s*["']module["']/i.test(attrs) ? 'esm' : 'iife' });
  }
  for (const m of html.matchAll(LINK_RE)) {
    const attrs = m[1] + ' ' + m[3];
    if (!/rel\s*=\s*["']stylesheet["']/i.test(attrs)) continue;
    const ref = m[2];
    if (!isLocalRef(ref)) continue;
    refs.push({ kind: 'css', ref, abs: path.resolve(dir, ref), format: null });
  }
  return { html, refs };
}

function rewriteHtml(html, htmlOutDir, refMap) {
  const fix = (ref) => {
    const mapped = refMap.get(ref);
    if (!mapped) return ref;
    let r = path.relative(htmlOutDir, mapped).split(path.sep).join('/');
    return r;
  };
  html = html.replace(SCRIPT_RE, (m, a, ref, b) => isLocalRef(ref) ? `<script${a}src="${fix(ref)}"${b}>` : m);
  html = html.replace(LINK_RE, (m, a, ref, b) => isLocalRef(ref) && /rel\s*=\s*["']stylesheet["']/i.test(a + b) ? `<link${a}href="${fix(ref)}"${b}>` : m);
  return html;
}

// ---------------------------------------------------------------------------
// esbuild

async function bundle({ entries, outdir, outbase, hashed, format, minify, define, nodePaths, alias }) {
  if (entries.length === 0) return { outputs: new Map(), inputs: [] };
  const result = await build({
    entryPoints: entries,
    outdir,
    outbase,
    bundle: true,
    format: format === 'iife' ? 'iife' : 'esm',
    splitting: false,
    platform: 'browser',
    target: ['es2020'],
    minify,
    sourcemap: false,
    metafile: true,
    write: true,
    logLevel: 'warning',
    legalComments: 'none',
    entryNames: hashed ? '[dir]/[name].[hash]' : '[dir]/[name]',
    chunkNames: hashed ? 'chunks/[name].[hash]' : 'chunks/[name]',
    assetNames: hashed ? 'assets/[name].[hash]' : 'assets/[name]',
    loader: {
      '.png': 'file', '.svg': 'file', '.woff': 'file', '.woff2': 'file', '.ttf': 'file', '.gif': 'file', '.jpg': 'file', '.json': 'json',
    },
    define,
    nodePaths,
    alias,
    // Keep non-ASCII text as written instead of \u escapes: the shells ship
    // unminified for extension review and their strings should read as text.
    charset: 'utf8',
  });
  const map = new Map();
  for (const [out, meta] of Object.entries(result.metafile.outputs)) {
    if (!meta.entryPoint) continue;
    map.set(path.resolve(CLIENT_DIR, meta.entryPoint), path.resolve(CLIENT_DIR, out));
  }
  const inputs = Object.keys(result.metafile.inputs)
    .filter((f) => !f.includes('node_modules') && !f.includes('/.generated/'))
    .map((f) => path.resolve(CLIENT_DIR, f));
  return { outputs: map, inputs };
}

/**
 * Build one target: discover entries from the given HTML pages, bundle them,
 * emit the rewritten HTML, copy vendor/icons.
 */
async function buildTarget(name, { pages, outdir, outbase, hashed, minify, shell = false, extraEntries = [], nodePaths = [] }) {
  log(`target ${name} -> ${rel(outdir)}`);
  mkdirp(outdir);
  // The served UI gets the full, hashed dictionaries now; a shell gets its
  // subset after bundling, once the bundled sources are known (emitShellI18n).
  const i18nFiles = shell ? shellI18nFiles() : emitI18n(outdir);
  const define = {
    __UI_VERSION__: JSON.stringify(VERSION),
    __BRIDGE_VERSION__: String(BRIDGE_VERSION),
    __I18N_FILES__: JSON.stringify(i18nFiles),
    __SHELL_TARGET__: JSON.stringify(name),
  };
  const alias = generateContextMenuModule();
  const sources = new Set();

  const discovered = [];
  for (const page of pages) {
    if (!fs.existsSync(page)) { warn(`page ${rel(page)} does not exist yet, skipping`); strictFailures++; continue; }
    discovered.push({ page, ...discover(page) });
  }

  // Partition references into: esbuild entries by format, vendor copies.
  const byFormat = { esm: new Set(), iife: new Set(), css: new Set() };
  const vendorRefs = new Map(); // abs -> out abs
  for (const d of discovered) {
    for (const r of d.refs) {
      const vendorIdx = r.ref.replace(/\\/g, '/').indexOf('vendor/');
      if (vendorIdx >= 0) {
        const inside = r.ref.slice(vendorIdx + 'vendor/'.length);
        const srcAbs = path.join(VENDOR, inside);
        if (!fs.existsSync(srcAbs)) { warn(`${rel(d.page)} references missing vendored file ${rel(srcAbs)}`); strictFailures++; continue; }
        r.abs = srcAbs;
        vendorRefs.set(srcAbs, path.join(outdir, 'vendor', inside));
        continue;
      }
      if (!fs.existsSync(r.abs)) { warn(`${rel(d.page)} references missing ${rel(r.abs)}, skipping`); strictFailures++; continue; }
      if (r.kind === 'css') byFormat.css.add(r.abs); else byFormat[r.format].add(r.abs);
    }
  }
  const outMap = new Map();
  const common = { outdir, outbase, hashed, minify, define, nodePaths, alias };
  const record = ({ outputs, inputs }) => {
    for (const [k, v] of outputs) outMap.set(k, v);
    for (const f of inputs) sources.add(f);
  };
  for (const [format, set] of Object.entries(byFormat)) {
    const entries = [...set];
    record(await bundle({ ...common, entries, format: format === 'css' ? 'esm' : format }));
  }
  // Extra entries (no page references them) land flat at the target root.
  for (const e of extraEntries) {
    if (!fs.existsSync(e.abs)) { warn(`extra entry ${rel(e.abs)} does not exist yet, skipping`); strictFailures++; continue; }
    record(await bundle({ ...common, outbase: path.dirname(e.abs), entries: [e.abs], format: e.format }));
  }

  // Vendor: copy the whole vendored package directories that were referenced.
  const vendorPkgs = new Set([...vendorRefs.keys()].map((abs) => path.relative(VENDOR, abs).split(path.sep)[0]));
  for (const pkg of vendorPkgs) {
    copyDir(path.join(VENDOR, pkg, 'css'), path.join(outdir, 'vendor', pkg, 'css'));
    copyDir(path.join(VENDOR, pkg, 'webfonts'), path.join(outdir, 'vendor', pkg, 'webfonts'));
  }
  for (const [abs, out] of vendorRefs) outMap.set(abs, out);

  // Emit HTML.
  for (const d of discovered) {
    sources.add(d.page);
    const outHtml = path.join(outdir, path.relative(outbase, d.page));
    mkdirp(path.dirname(outHtml));
    const refMap = new Map();
    for (const r of d.refs) {
      const out = outMap.get(r.abs);
      if (out) refMap.set(r.ref, out);
    }
    fs.writeFileSync(outHtml, rewriteHtml(d.html, path.dirname(outHtml), refMap));
  }

  copyDir(ICONS_SRC, path.join(outdir, 'icons'));
  return { i18nFiles, outMap, sources };
}

// ---------------------------------------------------------------------------
// targets

function listHtml(dir) {
  if (!fs.existsSync(dir)) return [];
  return fs.readdirSync(dir).filter((f) => f.endsWith('.html')).map((f) => path.join(dir, f));
}

async function buildUi() {
  const outdir = path.join(DIST, 'ui');
  const pages = [...listHtml(path.join(SRC, 'ui')), ...listHtml(path.join(SRC, 'ui', 'room'))];
  await buildTarget('ui', {
    pages, outdir, outbase: path.join(SRC, 'ui'), hashed: true, minify: true,
  });
  fs.writeFileSync(path.join(outdir, 'manifest.json'), JSON.stringify({ version: VERSION, bridge: BRIDGE_VERSION }, null, 2) + '\n');
}

async function buildExtension() {
  const outdir = path.join(DIST, 'extension');
  const pages = listHtml(path.join(SRC, 'shell'));
  const { sources } = await buildTarget('extension', {
    pages, outdir, outbase: path.join(SRC, 'shell'), hashed: false, minify: false, shell: true,
    extraEntries: [{ abs: path.join(SRC, 'shell', 'background.js'), format: 'iife' }],
  });
  emitShellI18n(outdir, sources);
  for (const browser of ['chrome', 'firefox']) {
    const src = path.join(MANIFEST_DIR, `manifest.${browser}.json`);
    const manifest = readJson(src, null);
    if (!manifest) { warn(`missing ${rel(src)}`); strictFailures++; continue; }
    manifest.version = VERSION;
    if (manifest.background && Array.isArray(manifest.background.scripts)) {
      manifest.background.scripts = ['background.js'];
    }
    fs.writeFileSync(path.join(outdir, `manifest.${browser}.json`), JSON.stringify(manifest, null, 2) + '\n');
  }
}

async function buildMobile() {
  const outdir = path.join(DIST, 'mobile');
  const shared = readJson(path.join(SRC, 'shell', 'mobile', 'shared-pages.json'), DEFAULT_MOBILE_SHARED_PAGES);
  const pages = [
    ...listHtml(path.join(SRC, 'shell', 'mobile')),
    ...shared.map((f) => path.join(SRC, 'shell', f)),
  ];
  if (!fs.existsSync(MOBILE_NODE_MODULES)) {
    log('installing mobile node_modules for Capacitor packages');
    try { execSync('npm install --no-audit --no-fund', { cwd: path.join(REPO_DIR, 'mobile'), stdio: 'inherit' }); }
    catch (e) { warn('npm install in mobile/ failed; Capacitor imports will not resolve'); }
  }
  const { sources } = await buildTarget('mobile', {
    pages: listHtml(path.join(SRC, 'shell', 'mobile')), outdir, outbase: path.join(SRC, 'shell', 'mobile'), hashed: false, minify: false, shell: true,
    extraEntries: [{ abs: path.join(SRC, 'shell', 'background.js'), format: 'iife' }],
    nodePaths: [MOBILE_NODE_MODULES],
  });
  if (shared.length) {
    const second = await buildTarget('mobile', {
      pages: shared.map((f) => path.join(SRC, 'shell', f)),
      outdir, outbase: path.join(SRC, 'shell'), hashed: false, minify: false, shell: true,
      nodePaths: [MOBILE_NODE_MODULES],
    });
    for (const f of second.sources) sources.add(f);
  }
  emitShellI18n(outdir, sources);
}

async function buildAll() {
  strictFailures = 0;
  const started = Date.now();
  const targets = { ui: buildUi, extension: buildExtension, mobile: buildMobile };
  if (ONLY && !(ONLY in targets)) {
    console.error(`unknown --target ${ONLY}; expected one of ${Object.keys(targets).join(', ')}`);
    process.exit(2);
  }
  rmrf(ONLY ? path.join(DIST, ONLY) : DIST);
  mkdirp(DIST);
  for (const [name, fn] of Object.entries(targets)) {
    if (ONLY && ONLY !== name) continue;
    await fn();
  }
  log(`done in ${Date.now() - started}ms (version ${VERSION}, bridge ${BRIDGE_VERSION})`);
  if (strictFailures) warn(`${strictFailures} missing input(s); the build is incomplete`);
  return strictFailures;
}

const failures = await buildAll();
if (WATCH) {
  log('watching src/ for changes');
  let timer = null;
  fs.watch(SRC, { recursive: true }, () => {
    clearTimeout(timer);
    timer = setTimeout(() => buildAll().catch((e) => console.error(e)), 150);
  });
} else if (failures && process.env.SEALSKIN_BUILD_STRICT === '1') {
  process.exit(1);
}
