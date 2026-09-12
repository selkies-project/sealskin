# SealSkin documentation site

The [Fumadocs](https://fumadocs.dev) site the `Docs` workflow publishes to
GitHub Pages. It renders the Markdown under [`content/`](content) and nothing
else; the pages stay plain Markdown so they read, review and edit straight
from GitHub.

```bash
npm install
npm run dev              # http://localhost:3000
npm run build            # static site in out/
npm run build:versions   # the published layout: every version in out/, see below
npm run check-links      # every link and anchor in out/ must resolve
npm run generate:api     # regenerate content/reference from the server docstrings
npm run generate:settings  # regenerate content/settings.md from server/app/settings.py
```

Everything is prerendered: GitHub Pages serves files, so the export carries
its own search index and there is no server at runtime. Each page is written
both as `page.html` and as `page/index.html`, so `/page` and `/page/` both
resolve.

The published site is versioned the way Read the Docs lays a site out:
`scripts/build-versions.mjs` builds one export per release tag that carries
this directory and one for `main`, and assembles them as `out/<version>/`.
The newest release is built as `latest`, and its own version segment, the
site root and every page address without a version redirect into it.
`out/versions.json` lists the versions, and the sidebar's version dropdown
switches between them. Every
version is rendered by the tooling in this directory over that version's own
`content/` and server source, so a fix here reaches every version when the
site is next published.

`NEXT_PUBLIC_BASE_PATH` is the path the site is served from. It is empty for
a local run; the workflow sets it to `/sealskin` for the GitHub Pages project
path, or leaves it empty when `public/CNAME` names a custom domain. A
versioned build appends the version's segment to it and passes the version
index as `NEXT_PUBLIC_DOCS_VERSIONS`.

Writing a page is covered in
[Development](content/development.md#this-documentation).
