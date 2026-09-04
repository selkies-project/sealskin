// Mirrors content/assets into public/ so the images the Markdown references
// resolve on the site. The files stay under content/ because that is where
// GitHub renders and edits them; Next.js only serves what is under public/.
import { cp, rm } from 'node:fs/promises';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const docs = dirname(dirname(fileURLToPath(import.meta.url)));
const from = join(docs, 'content', 'assets');
const to = join(docs, 'public', 'assets');

await rm(to, { recursive: true, force: true });
await cp(from, to, { recursive: true });
console.log(`sync-assets: ${from} -> ${to}`);
