/**
 * Builds the Developer Reference pages under content/reference from the
 * Google-style docstrings and type hints of the server package (server/app).
 *
 * The pages are build output, not source: content/reference is gitignored and
 * this script runs from the predev/prebuild npm hooks, so a local build and
 * the Docs workflow both generate them fresh from the code.
 *
 * Extraction is done by fumapy-generate (griffe, static analysis; the server's
 * dependencies are never imported). It runs from a private venv in
 * docs/.venv-docs that this script bootstraps on first use with python3;
 * delete that directory to force a clean bootstrap, e.g. after upgrading the
 * fumadocs-python npm package. Passing a JSON path as the first argument skips
 * extraction and converts that file instead.
 */
import { spawnSync } from 'node:child_process';
import { existsSync, mkdirSync } from 'node:fs';
import * as fs from 'node:fs/promises';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import * as Python from 'fumadocs-python';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const repoRoot = join(root, '..');
const serverRoot = join(repoRoot, 'server');
const outDir = join(root, 'content', 'reference');
const cache = join(root, '.cache');
const venv = join(root, '.venv-docs');
const venvBin = join(venv, process.platform === 'win32' ? 'Scripts' : 'bin');

// The package is named `app` inside server/; the reference is published under
// /reference/<module> without that generic prefix.
const PACKAGE = 'app';

function run(cmd, args, opts = {}) {
  const res = spawnSync(cmd, args, { stdio: 'inherit', ...opts });
  if (res.status !== 0) {
    throw new Error(`${cmd} ${args.join(' ')} exited with ${res.status ?? res.error}`);
  }
}

function extract() {
  const fumapy = join(venvBin, 'fumapy-generate');
  if (!existsSync(fumapy)) {
    run('python3', ['-m', 'venv', venv]);
    run(join(venvBin, 'pip'), ['install', '--quiet', join(root, 'node_modules', 'fumadocs-python')]);
  }
  // griffe resolves the package from sys.path; PYTHONPATH makes server/ visible
  // without installing the server into the venv.
  mkdirSync(cache, { recursive: true });
  run(fumapy, [PACKAGE, '--dir', cache], {
    cwd: serverRoot,
    env: { ...process.env, PYTHONPATH: serverRoot },
  });
  return join(cache, `${PACKAGE}.json`);
}

let jsonPath = process.argv[2];
if (!jsonPath) {
  try {
    jsonPath = extract();
  } catch (err) {
    // A site contributor without a working python3 can still build the rest
    // of the site against a previously generated reference; a from-scratch
    // build has nothing to fall back on.
    if (existsSync(join(outDir, 'index.mdx'))) {
      console.warn(`generate-python-docs: extraction failed (${err.message}); reusing the existing content/reference`);
      process.exit(0);
    }
    console.error(`generate-python-docs: extraction failed and no previous content/reference exists.\n${err.message}\npython3 with the venv module is required to build the Developer Reference.`);
    process.exit(1);
  }
}

const pkg = JSON.parse(await fs.readFile(jsonPath, 'utf8'));

const files = Python.convert(pkg, { baseUrl: '/reference', groupBy: 'none' });

// The package is called `app`, which says nothing on a sidebar or a tab.
for (const file of files) {
  if (file.path === 'index.mdx') file.title = 'Server Reference';
}

await fs.rm(outDir, { recursive: true, force: true });
await Python.write(files, outDir);

// The folder's sidebar entry: the package page first, then every module in
// alphabetical order (the extractor's order is arbitrary).
const moduleNames = Object.keys(pkg.modules ?? {}).sort();
await fs.writeFile(
  join(outDir, 'meta.json'),
  JSON.stringify({ title: 'Server Reference', pages: ['index', ...moduleNames] }, null, 2) + '\n',
);

console.log(`generate-python-docs: wrote ${files.length} pages to ${outDir}`);
