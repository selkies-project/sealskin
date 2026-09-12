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
// docs/public/CNAME names a custom domain; it sets both variables accordingly,
// and build-versions.mjs appends the version segment to the base path.
// next.config.mjs applies the base path to routed URLs, so anything assembled
// by hand here has to add it back.
export const basePath = process.env.NEXT_PUBLIC_BASE_PATH ?? '';

export function withBasePath(path: string): string {
  return path.startsWith('/') ? `${basePath}${path}` : path;
}

/** One version of the site, as scripts/build-versions.mjs lists it. */
export interface DocsVersion {
  /** The path segment the version is served under, which is also its name. */
  version: string;
  kind: 'release' | 'branch';
  /** Further segments serving the same build, such as `latest`. */
  aliases: string[];
}

/** The version index of a versioned site. */
export interface DocsVersions {
  /** The segment the site root redirects to. */
  default: string;
  versions: DocsVersion[];
}

/**
 * The version index when this build is one version of a versioned site, else
 * undefined. build-versions.mjs hands it to every version it builds, each
 * served one segment below the site root, so the base path's last segment
 * names the version this build is.
 */
export const docsVersions: DocsVersions | undefined = process.env.NEXT_PUBLIC_DOCS_VERSIONS
  ? JSON.parse(process.env.NEXT_PUBLIC_DOCS_VERSIONS)
  : undefined;

const segmentAt = basePath.lastIndexOf('/');

/** The path the versioned site is served from, above the version segments. */
export const siteRootPath = docsVersions ? basePath.slice(0, segmentAt) : basePath;

/** The segment this build is served under: a version or one of its aliases. */
export const versionSegment = docsVersions ? basePath.slice(segmentAt + 1) : undefined;

export const currentVersion =
  versionSegment === undefined
    ? undefined
    : docsVersions?.versions.find(
        (v) => v.version === versionSegment || v.aliases.includes(versionSegment),
      );

/** The site-absolute address of `path` in the version served under `segment`. */
export function versionPath(segment: string, path = '/'): string {
  return `${siteRootPath}/${segment}${path}`;
}

/**
 * The segment a version is served under: its alias when it has one, since
 * the version's own segment then only redirects there.
 */
export function servedSegment({ version, aliases }: DocsVersion): string {
  return aliases[0] ?? version;
}

export const siteUrl =
  process.env.NEXT_PUBLIC_SITE_URL ?? `https://${gitConfig.user}.github.io${siteRootPath}`;

/**
 * The published address of a page. Pages resolve with or without a trailing
 * slash, so one spelling is named as canonical and it is this one. On a
 * versioned site every version's copy of a page names the default version's,
 * so search results lead to the current release rather than to whichever
 * version was crawled.
 */
export function pageUrl(url: string): string {
  return docsVersions ? `${siteUrl}/${docsVersions.default}${url}` : `${siteUrl}${url}`;
}
