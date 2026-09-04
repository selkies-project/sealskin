import { loader } from 'fumadocs-core/source';
import { getSlugs } from 'fumadocs-core/source/plugins/slugs';
import { metaSchema, pageSchema } from 'fumadocs-core/source/schema';
import { defineDocs } from 'fumadocs-mdx/macro';

const docs = defineDocs({
  // The pages are plain Markdown under docs/content so they read on GitHub
  // as well as on the site.
  dir: 'content',
  docs: { schema: pageSchema },
  meta: { schema: metaSchema },
});

export const source = loader({
  // Pages sit at the site root rather than under a /docs prefix.
  baseUrl: '/',
  source: docs.toFumadocsSource(),
  slugs(file) {
    const segments = getSlugs(file.path);
    // content/README.md is the landing page. Keeping that name is what makes
    // GitHub render it when someone browses the content directory.
    if (segments.at(-1) === 'README') segments.pop();
    return segments;
  },
});
