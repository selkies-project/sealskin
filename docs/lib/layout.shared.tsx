import type { BaseLayoutProps } from 'fumadocs-ui/layouts/shared';
import Image from 'next/image';
// Imported rather than referenced by URL: a bare path would be emitted without
// the GitHub Pages base path and 404 in production.
import icon from '../content/assets/icon.png';
import { projectUrl, repoUrl, siteName } from './shared';

export function baseOptions(): BaseLayoutProps {
  return {
    nav: {
      title: (
        <>
          <Image src={icon} alt="" width={24} height={24} aria-hidden />
          <span className="font-semibold">{siteName}</span>
        </>
      ),
    },
    links: [{ text: 'sealskin.app', url: projectUrl, external: true }],
    githubUrl: repoUrl,
  };
}
