---
title: Dashboards
description: Lay the home page out for your users, with links, live widgets, and pages of their own on top.
---

# Dashboards

The home page of the web app is a dashboard: one grid, twelve cells wide,
where every tile sits where it was put. A tile is an application, a link, a
bundle of links, a widget that shows something live, or a heading. An
administrator lays dashboards out for groups of users; a user rearranges
the one they are given and adds to it, and those changes stay in their
browser.

With no dashboard defined, everyone sees the built-in page: the brand's
[home page links](administration.md#branding) and the applications. The
running sessions are not part of a dashboard: they always show under the
search box, above it.

## Arranging a page

The pencil beside the search box opens edit mode. In it:

* A tile is dragged to any cell of the grid and resized by its corner, up to
  the grid's twelve cells wide and eight high. Whatever it lands on moves
  down to make room; nothing else moves.
* **Add a tile** offers the kinds below and puts the tile in the first free
  place.
* The catalog of applications is a band the width of the page, dragged to
  another row by its grip or taken off. A page without it still shows the
  catalog while something is searched for.
* A heading's text comes in three sizes, and a row that holds only
  headings is just as high as their text.
* **More** closes the empty rows, imports a browser's bookmarks file as link
  tiles under a heading per folder, exports the page as a file and imports
  one as a page of your own, resets the page to the dashboard as the
  administrator made it, or resets everything the browser keeps for the
  home page: arrangements, pages of your own, the look, the background, and
  hidden applications.
* **New page** beside the tabs makes a page that is yours alone.

Starring an application in its launch window puts a tile of it on the page.
An application's tile carries a mark while a session of it runs, and a tile
two rows high or more shows that session's desktop, which a click opens.
A window too narrow for twelve cells shows the tiles in reading order on
eight or four, where they are not rearranged.

When an administrator later changes the dashboard, a user's page takes the
change: a new tile appears where the administrator put it, or in the first
free place from there, a removed one disappears, and the user's own
placement, sizes, and tiles stay.

### Appearance

**Appearance** in edit mode sets the page's background and the look of its
tiles: solid cards, glass over the background, or none; compact, normal, or
roomy rows; a centered page or the whole window; names on application and
link tiles or icons alone; and an accent color. A user's choices are kept
in their browser over the dashboard's. On the shared side of an
administrator's edit mode the same panel sets the dashboard's look for
everyone it is for.

### Icons

A link's or a bundle's icon is one of:

* a [Font Awesome](https://fontawesome.com/search?ic=free-collection) class:
  `fa-book`, `fab fa-github`;
* a name from an icon pack, which the server fetches and serves itself:
  `di-plex` ([dashboard-icons](https://dashboardicons.com)), `sh-jellyfin`
  ([selfh.st icons](https://selfh.st/icons/)), `si-github`
  ([Simple Icons](https://simpleicons.org)), `mdi-home`
  ([Material Design Icons](https://pictogrammers.com/library/mdi/));
* a picture you upload: a user's is scaled down and kept in their browser,
  an administrator's is kept on the server beside the dashboards;
* the site's own icon, which the wand button has the server read from the
  link's address; a user's site is read only on the public internet.

| Tile | What it does |
| --- | --- |
| **Application** | Opens the application's launch options; the play button launches at once. |
| **Link** | Opens an address in a new tab, in the page's own tab, or in a session of an application: one named on the tile, or one the user picks. |
| **Link bundle** | Opens several web addresses at once, each in a tab. The browser may ask once to allow pop-ups. |
| **Widget** | Shows live data; see [Widgets](#widgets). |
| **Heading** | A line of text in one of three sizes, to group the tiles under it. |

## Links

A link is `http` or `https`, or one of the schemes
[`SEALSKIN_DASHBOARD_LINK_SCHEMES`](settings.md) lists (`mailto`, `tel`,
`ssh`, `sftp`, `rdp`, and `vnc` by default), which the user's device hands to
the application registered for it. `javascript`, `data`, `file`, and the
other schemes a browser runs inside the page are refused whatever the
setting lists, for an administrator as for a user. A link opened in a
session or in the page's own tab is a web address.

In an administrator's link, `{username}` becomes the signed-in user's name,
percent-encoded: `https://tickets.example.org/u/{username}` or
`ssh://{username}@bastion.example.org`.

## Reaching internal services

A session's browser runs inside the server's network. A link set to open
**In a session** therefore reaches an address that only that network can,
from wherever the user is, with nothing exposed to the internet but
SealSkin itself.

* An address that looks internal (a private address, a bare host name, a
  name under `.lan`, `.local`, `.internal`, and the like) typed into a new
  link picks **In a session** by itself, and its tile carries a network
  mark. Importing bookmarks offers the same choice for the whole file.
* A dashboard's settings name the **application for links opened in a
  session**, so a click opens the address at once; a link may name its own.
  With one such application installed it is used without asking. A session
  of that application you already have running is handed the address, which
  it opens beside what it has open, and is brought forward: one browser for
  the links, not one each. Where it takes none, as after its application was
  closed, a new session starts.
* A link may **leave through** a VPN profile, for a service on another
  site's network. The launch checks the user's access to the profile as any
  launch does, and a user without it cannot open the link.
* On a shared dashboard a link may **show whether the address answers**: a
  dot on the tile, checked from the server once a minute. Only the users
  the dashboard is for are told, and only for links an administrator marked;
  a user's own links are never checked from the server.

What a user may reach this way is what their sessions may reach: the
applications they are given, the network those run on, and the
[VPN profiles](vpn.md) open to them. Turn off **Allow Own Links** to keep
the links to the ones an administrator placed. The dashboard's own tiles
are the administrator's to change; a user moves them, sizes them, or takes
them off their page.

## Shared dashboards

An administrator's edit mode has a second side, **Shared dashboard**. There
the page shows the dashboard as kept on the server, and every edit goes to
it until **Save for everyone**. The first save of the built-in page, or of a
page of the administrator's own, makes a new dashboard.

**Dashboard settings** hold its name and tab icon, the groups and users it
is for (`all` is everyone, `default` users in no group), and two switches.
**Locked**: users see the dashboard as it is and cannot rearrange it or add
to it. **Kiosk**: its users see this dashboard and nothing else, with no
search box, no catalog beyond the one placed on it, no other dashboards or
pages of their own, and no edit mode; an administrator keeps the pencil.
Running sessions still show on top. Kiosk is what the page offers, not what
a user may launch, which each application's own access still decides.
**All dashboards** lists every one to reorder, copy, and delete. A user gets
every dashboard that names them, in that order, as tabs.

Three user and group settings decide what users may do; see
[Users](administration.md#users). They shape what the page
offers. A page a user rearranges is seen by that user alone, so the server
enforces only what reaches it: whose widgets it reads, and whether it
fetches a feed a user gave.

The dashboards are the shared objects `dashboards/boards.yml` and
`dashboards/widgets.yml`, which can be written by hand, with the uploaded
icons under `dashboards/icons/`; an entry that does not validate is left
out and logged.

## Widgets

A widget is defined once in the **Widget library**, with the groups and
users that may see it, then placed on any dashboard. **Test** reads it
without saving and shows what its tile will.

| Type | Shows |
| --- | --- |
| **Clock** | The time in a time zone; a new one starts in the browser's own. Down to one cell, where it shows the time alone. |
| **Stocks** | Price, the day's change, and a month's line for ticker symbols (`AAPL`, `^GSPC`, `BTC-USD`), read from the chart API at `SEALSKIN_STOCKS_URL`, Yahoo Finance's by default. |
| **Weather** | Now and the next days for a place, from the [Open-Meteo](https://open-meteo.com) API (`SEALSKIN_WEATHER_URL` points at a host of your own). |
| **Feed** | The newest entries of RSS, Atom, or JSON Feed documents. **Import an OPML file** fills the addresses from a feed reader's export. |
| **Calendar** | The coming events of iCalendar (`.ics`) addresses, `https` or `webcal`: Google Calendar's "Secret address in iCal format", Outlook's published ICS link, Nextcloud's subscription link. |
| **API values** | Values picked from a JSON API by path, side by side, in a list, or a row per item. |
| **Site monitor** | Whether each address answers, and how fast. |
| **Notice** | A message in one of five tones, always or between a time it starts showing and a time it stops. |
| **Custom frame** | Your own HTML, CSS, and script. |
| **Embedded page** | Another site in a frame. |
| **Notes** | Text the user types, kept in their browser. |

The server reads every widget's data, so an address may be one only the
server reaches, and the answer is cached for the widget's refresh time.

### API values

The form takes an address, a method, headers, a body, and the values to
show, each a path into the answer (`queue.size`, `items.0.name`) with a label
and a format: `text`, `number`, `float`, `percent`, `bytes`, `bitrate`,
`size`, `date`, `relativeDate`, or `duration`. This is the shape of the
[Homepage](https://gethomepage.dev) dashboard's `customapi` widget, and
**Paste a Homepage customapi widget** takes such a definition as YAML.

### Secrets

Write `{{secret.NAME}}` in an address, a header, or a body where a credential
belongs. The node fills it in from the file `NAME` under
`SEALSKIN_WIDGET_SECRETS_PATH`, or from the environment variable
`SEALSKIN_WIDGET_SECRET_NAME`. The shared records hold only the name, and no
browser is sent the value. In a cluster each node needs the secret.

### Custom frames

A custom frame runs in a sandboxed frame with an origin of its own: it has
no cookie, no storage, and no way to the page around it, and it may load and
run anything. Give it a data source, a JSON API the server reads with
secrets filled in, and its script finds the answer in `window.sealskin.data`
and each later one through `window.sealskin.onData(callback)`.

```html
<ul id="jobs"></ul>
<script>
  const draw = (data) => {
    document.getElementById('jobs').textContent = data.records.map((job) => job.title).join(', ');
  };
  draw(window.sealskin.data);
  window.sealskin.onData(draw);
</script>
```

An embedded page keeps its own origin, so its sign-in works; a site that
forbids framing shows nothing.

### A user's own widgets

On a page they may change, a user adds a clock, notes, a weather tile for a
place they search for, stocks, and, where **Allow Own Feeds and Calendars** is on,
feeds and calendars by address. The server fetches such an address only
when it resolves to the public internet, with no credential, and never an
address inside your network.
