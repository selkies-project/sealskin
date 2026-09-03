#!/usr/bin/env node
/**
 * SealSkin client build.
 *
 * One source tree, three outputs (see docs/architecture.md section 3):
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

const DEFAULT_SHELL_NAMESPACES = ['common', 'connect', 'options.config', 'options.status', 'options.dashboard', 'background'];
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

/**
 * Emit one JSON per language, merged over English, optionally filtered to
 * namespaces, optionally hashed. Returns the map used for __I18N_FILES__.
 */
function emitI18n(outDir, { hashed, namespaces = null }) {
  const files = {};
  mkdirp(path.join(outDir, 'i18n'));
  const en = I18N_SOURCE.en || {};
  for (const lang of LANGS) {
    let dict = deepMerge(en, I18N_SOURCE[lang]);
    if (namespaces) dict = pick(dict, namespaces);
    const content = JSON.stringify(dict);
    const name = hashed ? `${lang}.${hashOf(content)}.json` : `${lang}.json`;
    fs.writeFileSync(path.join(outDir, 'i18n', name), content);
    files[lang] = `i18n/${name}`;
  }
  return files;
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

async function bundle({ entries, outdir, outbase, hashed, format, minify, define, nodePaths }) {
  if (entries.length === 0) return new Map();
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
  });
  const map = new Map();
  for (const [out, meta] of Object.entries(result.metafile.outputs)) {
    if (!meta.entryPoint) continue;
    map.set(path.resolve(CLIENT_DIR, meta.entryPoint), path.resolve(CLIENT_DIR, out));
  }
  return map;
}

/**
 * Build one target: discover entries from the given HTML pages, bundle them,
 * emit the rewritten HTML, copy vendor/icons.
 */
async function buildTarget(name, { pages, outdir, outbase, hashed, minify, i18n, extraEntries = [], nodePaths = [] }) {
  log(`target ${name} -> ${rel(outdir)}`);
  mkdirp(outdir);
  const i18nFiles = emitI18n(outdir, i18n);
  const define = {
    __UI_VERSION__: JSON.stringify(VERSION),
    __BRIDGE_VERSION__: String(BRIDGE_VERSION),
    __I18N_FILES__: JSON.stringify(i18nFiles),
    __SHELL_TARGET__: JSON.stringify(name),
  };

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
  const common = { outdir, outbase, hashed, minify, define, nodePaths };
  for (const [format, set] of Object.entries(byFormat)) {
    const entries = [...set];
    const m = await bundle({ ...common, entries, format: format === 'css' ? 'esm' : format });
    for (const [k, v] of m) outMap.set(k, v);
  }
  // Extra entries (no page references them) land flat at the target root.
  for (const e of extraEntries) {
    if (!fs.existsSync(e.abs)) { warn(`extra entry ${rel(e.abs)} does not exist yet, skipping`); strictFailures++; continue; }
    const m = await bundle({ ...common, outbase: path.dirname(e.abs), entries: [e.abs], format: e.format });
    for (const [k, v] of m) outMap.set(k, v);
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
  return { i18nFiles, outMap };
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
    i18n: { hashed: true },
  });
  fs.writeFileSync(path.join(outdir, 'manifest.json'), JSON.stringify({ version: VERSION, bridge: BRIDGE_VERSION }, null, 2) + '\n');
}

function shellNamespaces() {
  return readJson(path.join(SRC, 'shell', 'i18n-namespaces.json'), DEFAULT_SHELL_NAMESPACES);
}

async function buildExtension() {
  const outdir = path.join(DIST, 'extension');
  const pages = listHtml(path.join(SRC, 'shell'));
  await buildTarget('extension', {
    pages, outdir, outbase: path.join(SRC, 'shell'), hashed: false, minify: false,
    i18n: { hashed: false, namespaces: shellNamespaces() },
    extraEntries: [{ abs: path.join(SRC, 'shell', 'background.js'), format: 'iife' }],
  });
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
  const { outMap } = await buildTarget('mobile', {
    pages: listHtml(path.join(SRC, 'shell', 'mobile')), outdir, outbase: path.join(SRC, 'shell', 'mobile'), hashed: false, minify: false,
    i18n: { hashed: false, namespaces: shellNamespaces() },
    extraEntries: [{ abs: path.join(SRC, 'shell', 'background.js'), format: 'iife' }],
    nodePaths: [MOBILE_NODE_MODULES],
  });
  if (shared.length) {
    await buildTarget('mobile', {
      pages: shared.map((f) => path.join(SRC, 'shell', f)),
      outdir, outbase: path.join(SRC, 'shell'), hashed: false, minify: false,
      i18n: { hashed: false, namespaces: shellNamespaces() },
      nodePaths: [MOBILE_NODE_MODULES],
    });
  }
  void outMap;
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
