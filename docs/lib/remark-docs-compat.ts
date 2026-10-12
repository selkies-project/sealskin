import type { Root } from 'mdast';
import type { Transformer } from 'unified';
import { visit } from 'unist-util-visit';
import { withBasePath } from './shared';

const PROTOCOL = /^[a-z][a-z0-9+.-]*:/i;

// A relative `src` in an inline HTML image, as in the landing page's logo.
const HTML_IMG_SRC = /(<img\b[^>]*?\bsrc=")(assets\/[^"]*)"/g;

/**
 * Reconciles the Markdown dialect the pages are written in with this site's.
 *
 * Pages are authored for GitHub first, so nothing here pushes a change back
 * into the Markdown: whatever GitHub renders correctly is what the files keep
 * saying.
 *
 * - A sibling page is linked as `start.md`. Fumadocs resolves such a link to
 *   its page URL only once it is explicitly relative.
 * - Images are written relative to the Markdown file. Every page but the
 *   landing page is served one path segment deep, so the same relative URL
 *   would miss; sync-assets.mjs mirrors content/assets to the site root and
 *   these are pointed at it. An inline HTML image is given its address
 *   under the base path, which also holds in the page's Markdown copy.
 */
export function remarkDocsCompat(): Transformer<Root, Root> {
  return (tree) => {
    visit(tree, 'link', (node) => {
      if (PROTOCOL.test(node.url) || node.url.startsWith('/')) return;
      if (node.url.startsWith('./') || node.url.startsWith('../')) return;
      if (/\.mdx?($|#)/.test(node.url)) node.url = `./${node.url}`;
    });
    visit(tree, 'image', (node) => {
      if (node.url.startsWith('assets/')) node.url = `/${node.url}`;
    });
    visit(tree, 'html', (node) => {
      node.value = node.value.replace(HTML_IMG_SRC, (_, head, src) => `${head}${withBasePath(`/${src}`)}"`);
    });
    visit(tree, 'code', (node) => {
      if (node.lang === 'Dockerfile') node.lang = 'dockerfile';
      if (node.lang === 'caddyfile' || node.lang === 'Caddyfile') node.lang = 'nginx';
    });
  };
}
