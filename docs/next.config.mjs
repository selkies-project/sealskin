import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createMDX } from 'fumadocs-mdx/next';

// Empty for a local run. The Docs workflow sets it to the GitHub Pages project
// path ("/sealskin") unless a custom domain is configured; see lib/shared.ts.
const basePath = process.env.NEXT_PUBLIC_BASE_PATH ?? '';

// `next dev` only answers same-origin requests for its assets. To preview
// from another machine, list that origin: SEALSKIN_DOCS_DEV_ORIGINS=host,host2
const allowedDevOrigins = process.env.SEALSKIN_DOCS_DEV_ORIGINS?.split(',').map((s) => s.trim());

const withMDX = createMDX();

/** @type {import('next').NextConfig} */
const config = {
  turbopack: { root: dirname(fileURLToPath(import.meta.url)) },
  // GitHub Pages serves files, not a Node.js server.
  output: 'export',
  basePath,
  // The image optimizer is a server, which a static export does not have.
  images: { unoptimized: true },
  reactStrictMode: true,
  ...(allowedDevOrigins?.length ? { allowedDevOrigins } : {}),
};

export default withMDX(config);
