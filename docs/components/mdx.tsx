import defaultMdxComponents from 'fumadocs-ui/mdx';
import * as PythonComponents from 'fumadocs-python/components';
import type { MDXComponents } from 'mdx/types';
import type { ImgHTMLAttributes } from 'react';

/*
 * A plain <img>. The default routes every image through next/image, which
 * needs the dimensions of every remote image at build time; the badge row on
 * the landing page points at shields.io and GitHub, and a badge that does not
 * exist yet (a workflow not yet pushed) would fail the whole page.
 */
function Image(props: ImgHTMLAttributes<HTMLImageElement>) {
  // eslint-disable-next-line @next/next/no-img-element
  return <img loading="lazy" decoding="async" {...props} />;
}

export function getMDXComponents(components?: MDXComponents) {
  return {
    ...defaultMdxComponents,
    // PyFunction/PyAttribute/... and Tabs, used by the generated pages under
    // content/reference (see scripts/generate-python-docs.mjs).
    ...PythonComponents,
    img: Image,
    ...components,
  } satisfies MDXComponents;
}

export const useMDXComponents = getMDXComponents;

declare global {
  type MDXProvidedComponents = ReturnType<typeof getMDXComponents>;
}
