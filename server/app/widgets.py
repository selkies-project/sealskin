"""Dashboard widgets: what each type is configured with, and the data the server reads for it.

A widget is a tile of the home page that shows something live. Its type
decides what it reads:

* `clock` and `notes` read nothing; the page draws them.
* `weather` reads a forecast from the Open-Meteo API at `weather_url`.
* `feed` reads RSS, Atom, or JSON Feed documents and `calendar` reads
  iCalendar files, both merged into one list.
* `api` reads a JSON API and picks values out of the answer by `mappings`,
  the form the Homepage dashboard's `customapi` widget takes, so such a
  definition can be pasted in.
* `stocks` reads the price of each symbol and its last month of closes from
  the chart API at `stocks_url`.
* `monitor` asks a list of addresses whether they answer.
* `text` is an administrator's notice, which may have a time it starts
  showing and a time it stops.
* `frame` is an administrator's own HTML, CSS, and script, and `embed`
  another site's page; the page shows both in a sandboxed frame.

An administrator defines widgets in the library (`app.dashboards`), with any
address the node can reach, and may write `{{secret.NAME}}` where a
credential belongs: the node fills it from the environment variable
`SEALSKIN_WIDGET_SECRET_<NAME>` or the file `<NAME>` under
`widget_secrets_path`, so no record of the store holds a credential and no
client is sent one. A user may put a widget of the types in `USER_TYPES` on
their own page; the addresses a user gives are fetched through
`netguard.fetch(public_only=True)`.

What an address answered is a stranger's text: every link taken from a feed
is kept only when it is `http` or `https`, and the page escapes the rest.
Answers are cached per configuration for the widget's `refresh` seconds, so
a page that many users watch costs one fetch.
"""

from __future__ import annotations

import asyncio
import datetime
import hashlib
import json
import logging
import math
import os
import re
import time
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import quote
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx
import yaml
from lxml import etree
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from . import netguard
from .settings import settings

logger = logging.getLogger(__name__)

#: Every widget type.
TYPES = ("clock", "notes", "weather", "stocks", "feed", "calendar", "api", "monitor", "text", "frame", "embed")
#: The types a user may configure on their own page.
USER_TYPES = ("clock", "notes", "weather", "stocks", "feed", "calendar")
#: The user types that fetch an address the user gave, which the `dashboard_feeds` setting allows.
USER_FETCH_TYPES = ("feed", "calendar")
#: Seconds an answer is kept, by type, unless the widget says otherwise.
REFRESH = {"weather": 900, "stocks": 300, "feed": 900, "calendar": 900, "api": 60, "monitor": 60, "frame": 60, "reach": 60, "text": 60}
MIN_REFRESH = 15
#: Seconds a failure is kept before the address is tried again.
ERROR_SECONDS = 30
CACHE_ENTRIES = 512
SECRET_ENV = "SEALSKIN_WIDGET_SECRET_"
_SECRET = re.compile(r"\{\{\s*secret\.([A-Za-z0-9_]{1,64})\s*\}\}")
_WEB = re.compile(r"^https?://\S+$")
FORMATS = ("text", "number", "float", "percent", "bytes", "bitrate", "size", "date", "relativeDate", "duration")

#: Answers by configuration: when each stops being fresh, and the answer.
_cache: dict[str, tuple[float, dict[str, Any]]] = {}
_locks: dict[str, asyncio.Lock] = {}


class WidgetError(Exception):
    """A widget could not be read; the message is shown on its tile."""


def _instant(value: str) -> datetime.datetime | None:
    """Return the moment an ISO 8601 time names, in UTC; one without an offset is taken as UTC."""
    try:
        when = datetime.datetime.fromisoformat(value)
    except ValueError:
        return None
    return (when if when.tzinfo else when.replace(tzinfo=datetime.UTC)).astimezone(datetime.UTC)


class _Config(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)


def _web(value: str) -> str:
    """Return an `http` or `https` address trimmed, or raise `ValueError`; a calendar's `webcal` address is its `https` one."""
    value = re.sub(r"^webcals?://", "https://", value.strip(), flags=re.IGNORECASE)
    if not _WEB.fullmatch(value) or len(value) > 2000:
        raise ValueError("An address is http or https.")
    return value


class ClockConfig(_Config):
    """A clock: `timezone` is an IANA name, empty for the browser's own."""

    timezone: str = Field(default="", max_length=64)
    label: str = Field(default="", max_length=60)
    seconds: bool = False

    @field_validator("timezone")
    @classmethod
    def _known_zone(cls, value: str) -> str:
        """Take a time zone the server knows."""
        if value:
            try:
                ZoneInfo(value)
            except (ZoneInfoNotFoundError, ValueError) as exc:
                raise ValueError("Unknown time zone.") from exc
        return value


class NotesConfig(_Config):
    """Notes the user types, which the browser keeps."""


class WeatherConfig(_Config):
    """A forecast for one place."""

    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    label: str = Field(default="", max_length=80)
    units: str = Field(default="metric", pattern=r"^(metric|imperial)$")


class StocksConfig(_Config):
    """Ticker symbols as the chart API names them: `AAPL`, `^GSPC`, `BTC-USD`, `EURUSD=X`."""

    symbols: list[str] = Field(min_length=1, max_length=12)

    @field_validator("symbols")
    @classmethod
    def _tickers(cls, value: list[str]) -> list[str]:
        """Take symbols of letters, digits, and the marks tickers use, upper case, each once."""
        symbols = list(dict.fromkeys(symbol.strip().upper() for symbol in value if symbol.strip()))
        if not symbols or not all(re.fullmatch(r"[A-Z0-9.^=-]{1,15}", symbol) for symbol in symbols):
            raise ValueError("A symbol is letters, digits, and . ^ = - alone, such as AAPL or ^GSPC.")
        return symbols


class FeedConfig(_Config):
    """RSS, Atom, or JSON Feed documents shown as one list, newest first."""

    urls: list[str] = Field(min_length=1, max_length=10)
    limit: int = Field(default=8, ge=1, le=30)

    _urls = field_validator("urls")(lambda cls, value: [_web(url) for url in value])


class CalendarConfig(_Config):
    """iCalendar files shown as one list of the events of the next `days`."""

    urls: list[str] = Field(min_length=1, max_length=5)
    days: int = Field(default=14, ge=1, le=90)
    limit: int = Field(default=8, ge=1, le=30)

    _urls = field_validator("urls")(lambda cls, value: [_web(url) for url in value])


class Mapping(_Config):
    """One value picked out of an API's answer.

    Attributes:
        field: The path of the value: `key`, `path.to.key`, `list.0.key`, or the same as nested objects.
        label: What the value is called on the tile.
        format: How the page writes it (see `FORMATS`).
        scale: A factor the number is multiplied by.
        prefix: Text before the value.
        suffix: Text after it.
        remap: Values replaced by words: `{value, to}` entries, and `{any: true, to}` for the rest.
    """

    field: str | dict[str, Any]
    label: str = Field(default="", max_length=60)
    format: str = Field(default="text", pattern="^(" + "|".join(FORMATS) + ")$")
    scale: float | None = None
    prefix: str = Field(default="", max_length=20)
    suffix: str = Field(default="", max_length=20)
    remap: list[dict[str, Any]] = Field(default_factory=list, max_length=20)


class ListMapping(_Config):
    """The rows of a `dynamic-list`: `items` is the path of the array, `name` and `label` the fields of each row."""

    items: str = Field(default="", max_length=200)
    name: str = Field(max_length=200)
    label: str = Field(default="", max_length=200)
    limit: int = Field(default=8, ge=1, le=30)
    format: str = Field(default="text", pattern="^(" + "|".join(FORMATS) + ")$")
    target: str = Field(default="", max_length=2000)


class ApiConfig(_Config):
    """A JSON API and the values to show from it, in the form of Homepage's `customapi`."""

    url: str = Field(max_length=2000)
    method: str = Field(default="GET", pattern=r"^(GET|POST)$")
    headers: dict[str, str] = Field(default_factory=dict, max_length=20)
    body: str = Field(default="", max_length=10000, alias="requestBody")
    display: str = Field(default="block", pattern=r"^(block|list|dynamic-list)$")
    mappings: list[Mapping] | ListMapping = Field(default_factory=list)

    @field_validator("url")
    @classmethod
    def _address(cls, value: str) -> str:
        """Take an `http` or `https` address, which may carry a secret."""
        value = value.strip()
        if not re.fullmatch(r"https?://\S+", _SECRET.sub("x", value)):
            raise ValueError("An address is http or https.")
        return value

    @field_validator("body", mode="before")
    @classmethod
    def _text_body(cls, value: Any) -> Any:
        """Write a body given as an object out as JSON."""
        return json.dumps(value) if isinstance(value, dict | list) else value

    @field_validator("mappings")
    @classmethod
    def _few(cls, value: Any) -> Any:
        """Keep a tile to a dozen values."""
        if isinstance(value, list) and len(value) > 12:
            raise ValueError("A widget shows at most 12 values.")
        return value


class Site(_Config):
    """An address a monitor asks."""

    name: str = Field(min_length=1, max_length=60)
    url: str = Field(max_length=2000)

    _url = field_validator("url")(lambda cls, value: _web(value))


class MonitorConfig(_Config):
    """Addresses asked whether they answer; an answer below 400 is up."""

    sites: list[Site] = Field(min_length=1, max_length=20)


class TextConfig(_Config):
    """A notice in plain text; the page turns its addresses into links.

    Attributes:
        text: The notice.
        tone: How it is colored.
        starts: When it starts showing, an ISO 8601 time; empty for at once.
        ends: When it stops showing; empty for never.
    """

    text: str = Field(min_length=1, max_length=4000)
    tone: str = Field(default="neutral", pattern=r"^(neutral|info|success|warning|danger)$")
    starts: str = Field(default="", max_length=40)
    ends: str = Field(default="", max_length=40)

    @field_validator("starts", "ends")
    @classmethod
    def _time(cls, value: str) -> str:
        """Take a time `_instant` reads, written back in UTC."""
        value = value.strip()
        if not value:
            return ""
        when = _instant(value)
        if when is None:
            raise ValueError("A time is written as 2026-11-14T22:00 or with an offset.")
        return when.isoformat()


class FrameConfig(_Config):
    """An administrator's own page, shown in a sandboxed frame; `source` is a JSON API whose answer the frame is handed."""

    html: str = Field(min_length=1, max_length=200_000)
    source: ApiConfig | None = None


class EmbedConfig(_Config):
    """Another site's page in a frame."""

    url: str

    _url = field_validator("url")(lambda cls, value: _web(value))


CONFIGS: dict[str, type[_Config]] = {
    "clock": ClockConfig,
    "notes": NotesConfig,
    "weather": WeatherConfig,
    "stocks": StocksConfig,
    "feed": FeedConfig,
    "calendar": CalendarConfig,
    "api": ApiConfig,
    "monitor": MonitorConfig,
    "text": TextConfig,
    "frame": FrameConfig,
    "embed": EmbedConfig,
}


def validate(kind: str, config: Any) -> dict[str, Any]:
    """Return a widget's configuration as its type keeps it.

    Raises:
        ValueError: For an unknown type or a configuration the type does not take.
    """
    model = CONFIGS.get(kind)
    if model is None:
        raise ValueError(f"A widget is one of: {', '.join(TYPES)}.")
    try:
        return model.model_validate(config or {}).model_dump(exclude_none=True)
    except ValidationError as exc:
        first = exc.errors()[0]
        where = ".".join(str(part) for part in first["loc"])
        raise ValueError(f"{where}: {first['msg']}" if where else first["msg"]) from exc


def from_homepage(text: str) -> dict[str, Any]:
    """Return an `api` widget from a Homepage `customapi` definition pasted as YAML.

    The definition may be the widget alone or sit anywhere inside a service
    entry; the first mapping with a `url` and `mappings`, or of the type
    `customapi`, is taken.

    Returns:
        `{config, refresh}`: the widget's configuration and its refresh in seconds.

    Raises:
        ValueError: For text that is not YAML, holds no such definition, or holds one the type does not take.
    """
    try:
        document = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ValueError("The text is not YAML.") from exc

    def find(node: Any, depth: int = 0) -> dict[str, Any] | None:
        if depth > 8:
            return None
        if isinstance(node, dict):
            if node.get("url") and (node.get("type") == "customapi" or "mappings" in node):
                return node
            node = list(node.values())
        if isinstance(node, list):
            return next((found for found in (find(child, depth + 1) for child in node) if found), None)
        return None

    found = find(document)
    if not found:
        raise ValueError("The text holds no customapi widget: a mapping with a url and mappings.")
    headers = found.get("headers") if isinstance(found.get("headers"), dict) else {}
    config = validate(
        "api",
        {**found, "url": str(found["url"]), "headers": {str(name): str(value) for name, value in headers.items()}},
    )
    try:
        refresh = max(0, int(float(found.get("refreshInterval") or 0) / 1000))
    except (TypeError, ValueError):
        refresh = 0
    return {"config": config, "refresh": min(refresh, 86400)}


# --- Secrets ------------------------------------------------------------------


def secret_names() -> list[str]:
    """Return the names of the secrets this node holds, never their values."""
    names = {name[len(SECRET_ENV) :] for name in os.environ if name.startswith(SECRET_ENV) and name[len(SECRET_ENV) :]}
    try:
        names.update(name for name in os.listdir(settings.widget_secrets_path) if re.fullmatch(r"[A-Za-z0-9_]{1,64}", name))
    except OSError:
        pass
    return sorted(names)


def _secret(name: str) -> str:
    """Return a secret of this node, or raise `WidgetError` where it holds none by that name."""
    value = os.environ.get(SECRET_ENV + name)
    if value is None:
        try:
            with open(os.path.join(settings.widget_secrets_path, name), encoding="utf-8") as handle:
                value = handle.read().strip()
        except OSError:
            value = None
    if value is None:
        raise WidgetError(f"This node holds no secret named {name}.")
    return value


def fill(text: str) -> str:
    """Return `text` with every `{{secret.NAME}}` replaced by the node's secret."""
    return _SECRET.sub(lambda match: _secret(match.group(1)), text)


# --- Reading ------------------------------------------------------------------


async def _get(url: str, public_only: bool, **options: Any) -> netguard.Fetched:
    """Fetch an address for a widget, a refusal becoming the widget's error."""
    try:
        return await netguard.fetch(url, public_only=public_only, **options)
    except netguard.Refused as exc:
        raise WidgetError(str(exc)) from exc


def _json(fetched: netguard.Fetched) -> Any:
    """Return the JSON an address answered, or raise `WidgetError`."""
    if fetched.status >= 400:
        raise WidgetError(f"The address answered {fetched.status}.")
    try:
        return json.loads(fetched.content)
    except ValueError as exc:
        raise WidgetError("The address did not answer with JSON.") from exc


def _link(value: Any) -> str:
    """Return a link from a stranger's document when it is a web address, else nothing."""
    text = str(value or "").strip()
    return text if _WEB.fullmatch(text) and len(text) <= 2000 else ""


def _text(value: Any, limit: int = 300) -> str:
    """Return a stranger's text on one line, shortened."""
    return " ".join(str(value or "").split())[:limit]


def _stamp(value: Any) -> float | None:
    """Return the Unix time a feed's date names, in the RFC 822 or the ISO 8601 form."""
    text = str(value or "").strip()
    if not text:
        return None
    try:
        when = parsedate_to_datetime(text)
    except (TypeError, ValueError):
        try:
            when = datetime.datetime.fromisoformat(text)
        except ValueError:
            return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=datetime.UTC)
    return when.timestamp()


def _name(element: Any) -> str:
    """Return an XML element's name without its namespace; empty for a comment."""
    return etree.QName(element).localname.lower() if isinstance(element.tag, str) else ""


def _child(element: Any, *names: str) -> Any:
    """Return the first child of an element with one of `names`."""
    return next((child for child in element if _name(child) in names), None)


def parse_feed(content: bytes) -> list[dict[str, Any]]:
    """Return the entries of an RSS, Atom, or JSON Feed document as `{title, link, published, source}`.

    Raises:
        WidgetError: For a document that is none of the three.
    """
    head = content.lstrip()[:1]
    if head == b"{":
        try:
            feed = json.loads(content)
        except ValueError as exc:
            raise WidgetError("The feed is not valid JSON.") from exc
        source = _text(feed.get("title"), 80)
        return [
            {
                "title": _text(item.get("title") or item.get("content_text")),
                "link": _link(item.get("url") or item.get("external_url")),
                "published": _stamp(item.get("date_published")),
                "source": source,
            }
            for item in feed.get("items") or []
            if isinstance(item, dict)
        ]
    parser = etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False, huge_tree=False, recover=True)
    try:
        root = etree.fromstring(content, parser)
    except (etree.XMLSyntaxError, ValueError) as exc:
        raise WidgetError("The feed is not RSS, Atom, or JSON Feed.") from exc
    if root is None:
        raise WidgetError("The feed is not RSS, Atom, or JSON Feed.")
    entries: list[dict[str, Any]] = []
    if _name(root) == "feed":
        title = _child(root, "title")
        source = _text(title.text if title is not None else "", 80)
        for entry in (child for child in root if _name(child) == "entry"):
            links = [child for child in entry if _name(child) == "link"]
            chosen = next((link for link in links if link.get("rel", "alternate") == "alternate"), links[0] if links else None)
            name, when = _child(entry, "title"), _child(entry, "published", "updated")
            entries.append(
                {
                    "title": _text("".join(name.itertext()) if name is not None else ""),
                    "link": _link(chosen.get("href") if chosen is not None else ""),
                    "published": _stamp(when.text if when is not None else ""),
                    "source": source,
                }
            )
        return entries
    channel = _child(root, "channel")
    if channel is None:
        raise WidgetError("The feed is not RSS, Atom, or JSON Feed.")
    title = _child(channel, "title")
    source = _text(title.text if title is not None else "", 80)
    # RSS 1.0 keeps its items beside the channel, RSS 2.0 inside it.
    for item in [child for parent in (channel, root) for child in parent if _name(child) == "item"]:
        name, link, when = _child(item, "title"), _child(item, "link"), _child(item, "pubdate", "date")
        entries.append(
            {
                "title": _text("".join(name.itertext()) if name is not None else ""),
                "link": _link(link.text if link is not None else ""),
                "published": _stamp(when.text if when is not None else ""),
                "source": source,
            }
        )
    return entries


async def _gather(urls: list[str], public_only: bool, parse: Any) -> tuple[list[Any], list[str]]:
    """Fetch and parse every address, returning what was read and the failures."""

    async def one(url: str) -> list[Any]:
        fetched = await _get(fill(url) if not public_only else url, public_only)
        if fetched.status >= 400:
            raise WidgetError(f"The address answered {fetched.status}.")
        return parse(fetched.content)

    results = await asyncio.gather(*(one(url) for url in urls), return_exceptions=True)
    read: list[Any] = []
    failed: list[str] = []
    for result in results:
        if isinstance(result, WidgetError):
            failed.append(str(result))
        elif isinstance(result, BaseException):
            raise result
        else:
            read.extend(result)
    if failed and len(failed) == len(urls):
        raise WidgetError(failed[0])
    return read, failed


async def _feed(config: dict[str, Any], public_only: bool) -> dict[str, Any]:
    entries, failed = await _gather(config["urls"], public_only, parse_feed)
    entries = [entry for entry in entries if entry["title"]]
    entries.sort(key=lambda entry: entry["published"] or 0, reverse=True)
    return {"items": entries[: config["limit"]], "failed": len(failed)}


# --- Calendars ----------------------------------------------------------------

_WEEKDAYS = ("MO", "TU", "WE", "TH", "FR", "SA", "SU")
_STEP = {"DAILY": datetime.timedelta(days=1), "WEEKLY": datetime.timedelta(weeks=1)}
#: Occurrences of one repeating event looked at before giving up.
_MAX_STEPS = 2000


def _moment(value: str, params: dict[str, str]) -> tuple[datetime.datetime, bool] | None:
    """Return the time an iCalendar value names, in its own zone, and whether it is a whole day."""
    value = value.strip()
    try:
        if len(value) == 8:
            return datetime.datetime.strptime(value, "%Y%m%d").replace(tzinfo=datetime.UTC), True
        when = datetime.datetime.strptime(value.rstrip("Z"), "%Y%m%dT%H%M%S")
    except ValueError:
        return None
    zone: datetime.tzinfo = datetime.UTC
    if not value.endswith("Z") and params.get("TZID"):
        try:
            zone = ZoneInfo(params["TZID"])
        except (ZoneInfoNotFoundError, ValueError):
            zone = datetime.UTC
    return when.replace(tzinfo=zone), False


def _add_months(when: datetime.datetime, months: int) -> datetime.datetime | None:
    """Return `when` moved by whole months, or `None` where that month has no such day."""
    index = when.year * 12 + when.month - 1 + months
    try:
        return when.replace(year=index // 12, month=index % 12 + 1)
    except ValueError:
        return None


def _occurrences(start: datetime.datetime, rule: str, first: datetime.datetime, last: datetime.datetime) -> list[datetime.datetime]:
    """Return the starts of a repeating event between `first` and `last`.

    `FREQ` with `INTERVAL`, `COUNT`, `UNTIL`, and, for a weekly event,
    `BYDAY` are read; the rest of the rule is ignored. The event repeats at
    its hour in its own time zone, across a change of the clocks.
    """
    parts = dict(part.split("=", 1) for part in rule.split(";") if "=" in part)
    freq = parts.get("FREQ", "")
    try:
        interval = max(1, int(parts.get("INTERVAL", "1")))
        count = int(parts["COUNT"]) if "COUNT" in parts else None
    except ValueError:
        return []
    until = _moment(parts["UNTIL"], {}) if "UNTIL" in parts else None
    stop = min(last, until[0] + (datetime.timedelta(days=1) if until[1] else datetime.timedelta())) if until else last
    days = [_WEEKDAYS.index(day[-2:]) for day in parts.get("BYDAY", "").split(",") if day[-2:] in _WEEKDAYS]
    found: list[datetime.datetime] = []
    seen = 0
    for step in range(_MAX_STEPS):
        if freq in _STEP:
            base = start + _STEP[freq] * interval * step
        elif freq in ("MONTHLY", "YEARLY"):
            base = _add_months(start, step * interval * (12 if freq == "YEARLY" else 1))
        else:
            return found
        if base is None:
            continue
        if freq == "WEEKLY" and days:
            week = base - datetime.timedelta(days=base.weekday())
            moments = sorted(week + datetime.timedelta(days=day) for day in days)
            moments = [moment for moment in moments if moment >= start]
        else:
            moments = [base]
        for moment in moments:
            seen += 1
            if moment > stop or (count is not None and seen > count):
                return found
            if moment >= first:
                found.append(moment)
        if base > stop:
            return found
    return found


def parse_calendar(content: bytes, days: int, now: float | None = None) -> list[dict[str, Any]]:
    """Return the events of an iCalendar file that start, or still run, within the next `days`.

    Each is `{title, start, end, all_day, location}` with Unix times.

    Raises:
        WidgetError: For a file that is not an iCalendar.
    """
    text = content.decode("utf-8", "replace")
    if "BEGIN:VCALENDAR" not in text:
        raise WidgetError("The calendar is not an iCalendar file.")
    lines: list[str] = []
    for raw in text.splitlines():
        if raw[:1] in (" ", "\t") and lines:
            lines[-1] += raw[1:]
        else:
            lines.append(raw)
    first = datetime.datetime.fromtimestamp(now if now is not None else time.time(), datetime.UTC)
    last = first + datetime.timedelta(days=days)
    events: list[dict[str, Any]] = []
    event: dict[str, Any] | None = None
    for line in lines:
        if line == "BEGIN:VEVENT":
            event = {}
        elif line == "END:VEVENT" and event is not None:
            start = event.get("DTSTART")
            if start:
                end = event.get("DTEND")
                length = (end[0] - start[0]) if end else datetime.timedelta(days=1 if start[1] else 0)
                rule = event.get("RRULE")
                starts = _occurrences(start[0], rule, first - length, last) if rule else [start[0]]
                for begin in starts:
                    if begin <= last and begin + length >= first:
                        events.append(
                            {
                                "title": _text(event.get("SUMMARY"), 200),
                                "start": begin.timestamp(),
                                "end": (begin + length).timestamp(),
                                "all_day": start[1],
                                "location": _text(event.get("LOCATION"), 120),
                            }
                        )
            event = None
        elif event is not None and ":" in line:
            head, value = line.split(":", 1)
            name, *rest = head.split(";")
            params = dict(part.split("=", 1) for part in rest if "=" in part)
            name = name.upper()
            if name in ("DTSTART", "DTEND"):
                event[name] = _moment(value, params)
            elif name == "RRULE":
                event[name] = value
            elif name in ("SUMMARY", "LOCATION"):
                event[name] = value.replace("\\,", ",").replace("\\;", ";").replace("\\n", " ").replace("\\N", " ").replace("\\\\", "\\")
    return events


async def _calendar(config: dict[str, Any], public_only: bool) -> dict[str, Any]:
    events, failed = await _gather(config["urls"], public_only, lambda content: parse_calendar(content, config["days"]))
    events.sort(key=lambda event: event["start"])
    return {"events": events[: config["limit"]], "failed": len(failed)}


# --- Weather, APIs, monitors --------------------------------------------------


def _columns(table: dict[str, Any], names: dict[str, str]) -> list[dict[str, Any]]:
    """Return the rows of an Open-Meteo table of columns, each column under the name given for it."""
    lists = {name: table.get(column) if isinstance(table.get(column), list) else [] for name, column in names.items()}
    return [{name: values[row] if row < len(values) else None for name, values in lists.items()} for row in range(len(lists[next(iter(names))]))]


async def _weather(config: dict[str, Any]) -> dict[str, Any]:
    imperial = config["units"] == "imperial"
    query = (
        f"?latitude={config['latitude']:.4f}&longitude={config['longitude']:.4f}"
        "&current=temperature_2m,apparent_temperature,relative_humidity_2m,weather_code,wind_speed_10m,is_day"
        "&hourly=temperature_2m,weather_code,precipitation_probability,is_day&forecast_hours=24"
        "&daily=weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max,sunrise,sunset"
        "&timezone=auto&forecast_days=7" + ("&temperature_unit=fahrenheit&wind_speed_unit=mph" if imperial else "")
    )
    answer = _json(await _get(settings.weather_url + query, False))
    current = answer.get("current") if isinstance(answer.get("current"), dict) else {}
    days = _columns(
        answer.get("daily") if isinstance(answer.get("daily"), dict) else {},
        {"date": "time", "code": "weather_code", "max": "temperature_2m_max", "min": "temperature_2m_min", "rain": "precipitation_probability_max", "sunrise": "sunrise", "sunset": "sunset"},
    )
    hours = _columns(
        answer.get("hourly") if isinstance(answer.get("hourly"), dict) else {},
        {"time": "time", "temperature": "temperature_2m", "code": "weather_code", "rain": "precipitation_probability", "day": "is_day"},
    )
    return {
        "label": config["label"],
        "units": config["units"],
        "temperature": current.get("temperature_2m"),
        "feels": current.get("apparent_temperature"),
        "humidity": current.get("relative_humidity_2m"),
        "wind": current.get("wind_speed_10m"),
        "code": current.get("weather_code"),
        "day": bool(current.get("is_day", 1)),
        # The times are the place's own, as written: the page shows them without moving them to the viewer's zone.
        "hours": [{**hour, "time": _text(hour["time"], 20), "day": bool(hour["day"] if hour["day"] is not None else 1)} for hour in hours[:24]],
        "days": [{**day, **{name: _text(day[name], 20) for name in ("date", "sunrise", "sunset")}} for day in days[:7]],
    }


async def _stocks(config: dict[str, Any]) -> dict[str, Any]:
    async def read(symbol: str) -> dict[str, Any]:
        address = settings.stocks_url.replace("{symbol}", quote(symbol, safe="")) + "?interval=1d&range=1mo"
        try:
            result = _json(await _get(address, False))["chart"]["result"][0]
            meta = result["meta"]
            closes = [close for close in result["indicators"]["quote"][0]["close"] if isinstance(close, int | float)]
            price = meta.get("regularMarketPrice", closes[-1] if closes else None)
            if not isinstance(price, int | float):
                raise KeyError("price")
        except (WidgetError, KeyError, IndexError, TypeError, AttributeError):
            return {"symbol": symbol, "ok": False}
        # The last close is today's once the market has traded; the change is against the one before it.
        before = closes[:-1] if closes and math.isclose(closes[-1], price, rel_tol=1e-4) else closes
        previous = before[-1] if before else meta.get("chartPreviousClose")
        change = price - previous if isinstance(previous, int | float) else None
        return {
            "symbol": symbol,
            "ok": True,
            "name": _text(meta.get("shortName") or meta.get("longName") or symbol, 60),
            "currency": _text(meta.get("currency"), 8),
            "price": price,
            "change": change,
            "percent": change / previous * 100 if change is not None and previous else None,
            "points": [round(close, 4) for close in closes[-30:]],
        }

    return {"quotes": list(await asyncio.gather(*(read(symbol) for symbol in config["symbols"])))}


async def places(name: str) -> list[dict[str, Any]]:
    """Return the places the geocoding API at `geocoding_url` knows by `name`, for a weather widget's form."""
    name = name.strip()[:80]
    if len(name) < 2:
        return []
    query = httpx.URL(settings.geocoding_url, params={"name": name, "count": "6", "format": "json"})
    fetched = await _get(str(query), False)
    return [
        {
            "label": ", ".join(str(part) for part in (place.get("name"), place.get("admin1"), place.get("country")) if part),
            "latitude": place.get("latitude"),
            "longitude": place.get("longitude"),
        }
        for place in (_json(fetched).get("results") or [])
        if isinstance(place.get("latitude"), int | float) and isinstance(place.get("longitude"), int | float)
    ]


def pick(document: Any, path: str | dict[str, Any]) -> Any:
    """Return the value at `path` of a JSON document: dotted keys and list indexes, or the same as nested objects."""
    while isinstance(path, dict):
        if len(path) != 1:
            return None
        key, path = next(iter(path.items()))
        document = _step(document, str(key))
    for key in str(path).split(".") if path not in ("", None) else []:
        document = _step(document, key)
    return document


def _step(document: Any, key: str) -> Any:
    if isinstance(document, dict):
        return document.get(key)
    if isinstance(document, list) and key.lstrip("-").isdigit() and -len(document) <= int(key) < len(document):
        return document[int(key)]
    return None


def _value(raw: Any, mapping: dict[str, Any]) -> Any:
    """Return a picked value as the page is sent it: remapped, scaled, and never a structure."""
    for entry in mapping.get("remap") or []:
        if entry.get("any") or str(entry.get("value")) == str(raw):
            return _text(entry.get("to"), 200)
    if mapping.get("format") == "size":
        return len(raw) if isinstance(raw, list | dict | str) else 0
    if isinstance(raw, dict | list):
        return _text(json.dumps(raw), 200)
    if mapping.get("scale") is not None and isinstance(raw, int | float) and not isinstance(raw, bool):
        return raw * mapping["scale"]
    return raw if isinstance(raw, int | float | bool) or raw is None else _text(raw, 500)


async def _answer(config: dict[str, Any]) -> Any:
    """Return the JSON an API widget's address answers."""
    headers = {name: fill(value) for name, value in config["headers"].items()}
    body = fill(config["body"]) if config["body"] else None
    if body and not any(name.lower() == "content-type" for name in headers):
        headers["Content-Type"] = "application/json"
    return _json(await _get(fill(config["url"]), False, method=config["method"], headers=headers, body=body))


async def _api(config: dict[str, Any]) -> dict[str, Any]:
    answer = await _answer(config)
    mappings = config["mappings"]
    if isinstance(mappings, dict):
        rows = pick(answer, mappings["items"]) if mappings["items"] else answer
        if isinstance(rows, dict):
            rows = list(rows.values())
        rows = rows if isinstance(rows, list) else []
        return {
            "display": "dynamic-list",
            "format": mappings["format"],
            "rows": [
                {
                    "name": _text(pick(row, mappings["name"]), 200),
                    "value": _value(pick(row, mappings["label"]), mappings) if mappings["label"] else None,
                    "link": _link(pick(row, mappings["target"])) if mappings["target"] else "",
                }
                for row in rows[: mappings["limit"]]
            ],
        }
    return {
        "display": config["display"],
        "fields": [
            {
                "label": mapping["label"] or (mapping["field"] if isinstance(mapping["field"], str) else ""),
                "value": _value(pick(answer, mapping["field"]), mapping),
                "format": mapping["format"],
                "prefix": mapping["prefix"],
                "suffix": mapping["suffix"],
            }
            for mapping in mappings
        ],
    }


async def _monitor(config: dict[str, Any]) -> dict[str, Any]:
    async def ask(site: dict[str, str]) -> dict[str, Any]:
        try:
            fetched = await netguard.fetch(site["url"], public_only=False, max_bytes=5_000_000, timeout=6.0)
        except netguard.Refused:
            return {"name": site["name"], "up": False, "status": 0, "ms": None}
        return {"name": site["name"], "up": fetched.status < 400, "status": fetched.status, "ms": round(fetched.seconds * 1000)}

    return {"sites": list(await asyncio.gather(*(ask(site) for site in config["sites"])))}


async def reach(url: str) -> dict[str, Any]:
    """Tell whether an address answers this node, and how fast; any answer short of a server error counts."""
    try:
        fetched = await netguard.fetch(url, public_only=False, max_bytes=5_000_000, timeout=6.0)
    except netguard.Refused:
        return {"up": False, "status": 0, "ms": None}
    return {"up": fetched.status < 500, "status": fetched.status, "ms": round(fetched.seconds * 1000)}


async def _frame(config: dict[str, Any]) -> dict[str, Any]:
    data = None
    if config.get("source"):
        data = await _answer(config["source"])
    return {"html": config["html"], "data": data}


async def _read(kind: str, config: dict[str, Any], public_only: bool) -> dict[str, Any]:
    """Read what a widget of `kind` shows."""
    if kind == "weather":
        return await _weather(config)
    if kind == "stocks":
        return await _stocks(config)
    if kind == "feed":
        return await _feed(config, public_only)
    if kind == "calendar":
        return await _calendar(config, public_only)
    if kind == "api":
        return await _api(config)
    if kind == "monitor":
        return await _monitor(config)
    if kind == "frame":
        return await _frame(config)
    if kind == "reach":
        return await reach(config["url"])
    if kind == "text":
        # A notice outside its time is told to the page as such, which shows it to no one but who edits the dashboard.
        now = datetime.datetime.now(datetime.UTC)
        starts, ends = _instant(config.get("starts") or ""), _instant(config.get("ends") or "")
        return {**config, "active": not ((starts and now < starts) or (ends and now >= ends))}
    # The rest is drawn from the configuration alone.
    return dict(config)


async def data(kind: str, config: dict[str, Any], *, refresh: int = 0, public_only: bool = False, fresh: bool = False) -> dict[str, Any]:
    """Return what a widget shows, from the cache while it is fresh.

    Args:
        kind: The widget's type.
        config: Its configuration, as `validate` returned it.
        refresh: Seconds the answer is kept; the type's own (see `REFRESH`) for 0.
        public_only: The addresses are a user's: fetch nothing that is not of the public internet.
        fresh: Read again whatever the cache holds.

    Returns:
        `{ok: True, data, at}`, or `{ok: False, error, at}` where the widget could not be read.
    """
    seconds = max(MIN_REFRESH, refresh or REFRESH.get(kind, 3600))
    key = hashlib.sha256(json.dumps([kind, config, public_only], sort_keys=True, default=str).encode()).hexdigest()
    held = _cache.get(key)
    if held and held[0] > time.time() and not fresh:
        return held[1]
    lock = _locks.setdefault(key, asyncio.Lock())
    async with lock:
        held = _cache.get(key)
        if held and held[0] > time.time() and not fresh:
            return held[1]
        try:
            answer = {"ok": True, "data": await _read(kind, config, public_only), "at": time.time()}
            keep = seconds
        except WidgetError as exc:
            answer = {"ok": False, "error": str(exc), "at": time.time()}
            keep = min(seconds, ERROR_SECONDS)
        except Exception:  # noqa: BLE001 - a stranger's document must not fail the whole page
            logger.exception("Widget of type %s could not be read", kind)
            answer = {"ok": False, "error": "The widget could not be read.", "at": time.time()}
            keep = min(seconds, ERROR_SECONDS)
        if len(_cache) >= CACHE_ENTRIES:
            now = time.time()
            for stale in [name for name, (until, _) in _cache.items() if until <= now] or list(_cache)[: CACHE_ENTRIES // 4]:
                _cache.pop(stale, None)
                _locks.pop(stale, None)
        _cache[key] = (time.time() + keep, answer)
        return answer


def reset() -> None:
    """Forget every answer, for a test or after the library changed."""
    _cache.clear()
    _locks.clear()
