import { createRelativeLink } from 'fumadocs-ui/mdx';
import {
  DocsBody,
  DocsDescription,
  DocsPage,
  DocsTitle,
  EditOnGitHub,
} from 'fumadocs-ui/layouts/docs/page';
import type { Metadata } from 'next';
import { notFound } from 'next/navigation';
import { getMDXComponents } from '@/components/mdx';
import { gitConfig, pageUrl, siteName } from '@/lib/shared';
import { source } from '@/lib/source';

const isLanding = (url: string) => url === '/';

// Every page is known at build time; anything else (a stray /favicon.ico in
// dev, a mistyped URL) is a 404 rather than an error under output: export.
export const dynamicParams = false;

/** The page's Markdown source, open in GitHub's web editor. */
function editUrl(path: string) {
  const { user, repo, branch, dir } = gitConfig;
  return `https://github.com/${user}/${repo}/edit/${branch}/${dir}/${path}`;
}

export default async function Page(props: PageProps<'/[[...slug]]'>) {
  const params = await props.params;
  const page = source.getPage(params.slug);
  if (!page) notFound();

  const MDX = page.data.body;
  // The Developer Reference is generated from the code, so its "source" is
  // the Python module rather than a Markdown file.
  const generated = page.path.startsWith('reference/');
  const edit = generated ? undefined : <EditOnGitHub href={editUrl(page.path)} />;

  return (
    <DocsPage
      toc={page.data.toc}
      full={page.data.full}
      tableOfContent={{ footer: edit }}
      tableOfContentPopover={{ footer: edit }}
    >
      {/* The landing page opens with the logo and its own lead paragraph. */}
      {!isLanding(page.url) && (
        <div className="mb-2 flex flex-col gap-4 border-b border-fd-border pb-6">
          <DocsTitle>{page.data.title}</DocsTitle>
          <DocsDescription className="mb-0">{page.data.description}</DocsDescription>
        </div>
      )}
      <DocsBody>
        <MDX
          components={getMDXComponents({
            // Lets a page keep linking to `start.md`, which is what GitHub
            // resolves when the same file is read there.
            a: createRelativeLink(source, page),
          })}
        />
      </DocsBody>
    </DocsPage>
  );
}

export function generateStaticParams() {
  return source.generateParams();
}

export async function generateMetadata(
  props: PageProps<'/[[...slug]]'>,
): Promise<Metadata> {
  const params = await props.params;
  const page = source.getPage(params.slug);
  if (!page) notFound();

  return {
    title: isLanding(page.url) ? { absolute: siteName } : page.data.title,
    description: page.data.description,
    alternates: { canonical: pageUrl(page.url) },
  };
}
