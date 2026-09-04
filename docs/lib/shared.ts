export const siteName = 'SealSkin';

export const siteDescription =
  'Self-hosted browser isolation and remote application streaming. Open any link, file or download in a containerized desktop application streamed to your browser or phone.';

// Pages live in docs/content, so a contributor can edit them straight from
// GitHub without touching the site.
export const gitConfig = {
  user: 'selkies-project',
  repo: 'sealskin',
  branch: 'main',
  dir: 'docs/content',
};

export const repoUrl = `https://github.com/${gitConfig.user}/${gitConfig.repo}`;

export const projectUrl = 'https://sealskin.app';

export const discordUrl = 'https://discord.com/invite/linuxserver';

// The Docs workflow publishes to GitHub Pages under the repository name unless
// docs/public/CNAME names a custom domain; it sets both variables accordingly.
// next.config.mjs applies the base path to routed URLs, so anything assembled
// by hand here has to add it back.
export const basePath = process.env.NEXT_PUBLIC_BASE_PATH ?? '';

export const siteUrl =
  process.env.NEXT_PUBLIC_SITE_URL ?? `https://${gitConfig.user}.github.io${basePath}`;

export function withBasePath(path: string): string {
  return path.startsWith('/') ? `${basePath}${path}` : path;
}

/**
 * The published address of a page. Pages resolve with or without a trailing
 * slash, so one spelling is named as canonical and it is this one.
 */
export function pageUrl(url: string): string {
  return `${siteUrl}${url === '/' ? '/' : url}`;
}
