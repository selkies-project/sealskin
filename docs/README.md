# SealSkin documentation site

The [Fumadocs](https://fumadocs.dev) site the `Docs` workflow publishes to
GitHub Pages. It renders the Markdown under [`content/`](content) and nothing
else; the pages stay plain Markdown so they read, review and edit straight
from GitHub.

```bash
npm install
npm run dev              # http://localhost:3000
npm run build            # static site in out/
npm run check-links      # every link and anchor in out/ must resolve
npm run generate:api     # regenerate content/reference from the server docstrings
npm run generate:settings  # regenerate content/settings.md from server/app/settings.py
```

Everything is prerendered: GitHub Pages serves files, so the export carries
its own search index and there is no server at runtime. Each page is written
both as `page.html` and as `page/index.html`, so `/page` and `/page/` both
resolve.

`NEXT_PUBLIC_BASE_PATH` is the path the site is served from. It is empty for
a local run; the workflow sets it to `/sealskin` for the GitHub Pages project
path, or leaves it empty when `public/CNAME` names a custom domain.

Writing a page is covered in
[Development](content/development.md#this-documentation).
