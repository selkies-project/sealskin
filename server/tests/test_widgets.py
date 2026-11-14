"""Widgets: the documents they parse, the values they pick, and the secrets they fill in."""

import datetime
import json

import httpx
import pytest

from app import netguard, widgets
from app.settings import settings

RSS = b"""<?xml version="1.0"?><!DOCTYPE rss [<!ENTITY x "boom">]>
<rss version="2.0"><channel><title>News</title>
<item><title>First &amp; foremost</title><link>https://example.org/1</link><pubDate>Mon, 02 Nov 2026 10:00:00 GMT</pubDate></item>
<item><title>Trap</title><link>javascript:alert(1)</link><pubDate>Tue, 03 Nov 2026 10:00:00 GMT</pubDate></item>
</channel></rss>"""
ATOM = b"""<feed xmlns="http://www.w3.org/2005/Atom"><title>Releases</title>
<entry><title>v2 <b>out</b></title><link rel="self" href="https://example.org/self"/><link href="https://example.org/v2"/><updated>2026-11-04T08:00:00Z</updated></entry></feed>"""
JSON_FEED = json.dumps({"title": "Blog", "items": [{"title": "Post", "url": "https://example.org/post", "date_published": "2026-11-01T00:00:00+00:00"}]}).encode()
NOW = datetime.datetime(2026, 11, 14, 12, 0, tzinfo=datetime.UTC).timestamp()
ICS = b"""BEGIN:VCALENDAR
BEGIN:VEVENT
SUMMARY:Stand-up\\, daily
DTSTART;TZID=Europe/Berlin:20260701T093000
DTEND;TZID=Europe/Berlin:20260701T094500
RRULE:FREQ=WEEKLY;BYDAY=MO,WE
END:VEVENT
BEGIN:VEVENT
SUMMARY:Maintenance
  window
DTSTART;VALUE=DATE:20261116
LOCATION:Rack 4
END:VEVENT
BEGIN:VEVENT
SUMMARY:Long gone
DTSTART:20200101T000000Z
END:VEVENT
END:VCALENDAR
"""


def test_feeds_of_the_three_kinds_are_read_and_a_script_link_is_dropped():
    rss = widgets.parse_feed(RSS)
    assert [(e["title"], e["link"], e["source"]) for e in rss] == [("First & foremost", "https://example.org/1", "News"), ("Trap", "", "News")]
    assert rss[0]["published"] == datetime.datetime(2026, 11, 2, 10, tzinfo=datetime.UTC).timestamp()
    assert widgets.parse_feed(ATOM)[0] | {"published": 0} == {"title": "v2 out", "link": "https://example.org/v2", "source": "Releases", "published": 0}
    assert widgets.parse_feed(JSON_FEED)[0]["link"] == "https://example.org/post"
    with pytest.raises(widgets.WidgetError):
        widgets.parse_feed(b"<html><body>not a feed</body></html>")


def test_a_calendar_gives_the_coming_events_and_repeats_the_repeating():
    events = widgets.parse_calendar(ICS, 7, NOW)
    titles = [(e["title"], datetime.datetime.fromtimestamp(e["start"], datetime.UTC).strftime("%m-%d %H:%M"), e["all_day"]) for e in sorted(events, key=lambda e: e["start"])]
    assert titles == [("Maintenance window", "11-16 00:00", True), ("Stand-up, daily", "11-16 08:30", False), ("Stand-up, daily", "11-18 08:30", False)]
    assert [e["location"] for e in events if e["all_day"]] == ["Rack 4"]
    with pytest.raises(widgets.WidgetError):
        widgets.parse_calendar(b"hello", 7)


def test_values_are_picked_by_path_and_never_sent_as_structures():
    answer = {"a": {"b": [{"c": 5}, {"c": 7}]}, "state": "up", "big": {"x": 1}}
    assert widgets.pick(answer, "a.b.1.c") == 7 and widgets.pick(answer, {"a": {"b": "0"}}) == {"c": 5}
    assert widgets.pick(answer, "a.missing.c") is None and widgets.pick(answer, "a.b.9") is None
    assert widgets._value(5, {"scale": 0.5}) == 2.5
    assert widgets._value("up", {"remap": [{"value": "up", "to": "Online"}, {"any": True, "to": "Offline"}]}) == "Online"
    assert widgets._value([1, 2, 3], {"format": "size"}) == 3 and isinstance(widgets._value({"x": 1}, {}), str)


def test_a_configuration_is_held_to_its_type():
    assert widgets.validate("feed", {"urls": [" https://example.org/rss "]}) == {"urls": ["https://example.org/rss"], "limit": 8}
    assert widgets.validate("api", {"url": "https://x/{{secret.KEY}}", "requestBody": {"a": 1}})["body"] == '{"a": 1}'
    for kind, config in (("feed", {"urls": ["file:///etc/passwd"]}), ("embed", {"url": "javascript:alert(1)"}), ("weather", {"latitude": 200, "longitude": 0}), ("nope", {}), ("clock", {"timezone": "Mars/Base"})):
        with pytest.raises(ValueError):
            widgets.validate(kind, config)


async def test_an_api_widget_fills_its_secret_on_the_node_and_shows_mapped_values(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "widget_secrets_path", str(tmp_path))
    (tmp_path / "TOKEN").write_text("s3cret\n")
    monkeypatch.setenv("SEALSKIN_WIDGET_SECRET_OTHER", "env")
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={"queue": {"size": 3}, "state": "ok", "items": [{"n": "a", "v": 1}, {"n": "b", "v": 2}]})

    monkeypatch.setattr(netguard, "_transport", httpx.MockTransport(handler))
    widgets.reset()
    assert widgets.secret_names() == ["OTHER", "TOKEN"]
    config = widgets.validate("api", {"url": "http://10.0.0.5/api", "headers": {"X-Api-Key": "{{secret.TOKEN}}"}, "mappings": [{"field": "queue.size", "label": "Queued", "format": "number"}, {"field": "state"}]})
    answer = await widgets.data("api", config)
    assert seen[0].headers["x-api-key"] == "s3cret" and "s3cret" not in json.dumps(answer)
    assert [(f["label"], f["value"]) for f in answer["data"]["fields"]] == [("Queued", 3), ("state", "ok")]
    await widgets.data("api", config)
    assert len(seen) == 1
    rows = widgets.validate("api", {"url": "http://10.0.0.5/api", "display": "dynamic-list", "mappings": {"items": "items", "name": "n", "label": "v", "limit": 1}})
    assert (await widgets.data("api", rows))["data"]["rows"] == [{"name": "a", "value": 1, "link": ""}]
    missing = widgets.validate("api", {"url": "http://10.0.0.5/{{secret.ABSENT}}"})
    failed = await widgets.data("api", missing)
    assert failed["ok"] is False and "ABSENT" in failed["error"]


def test_a_homepage_customapi_definition_becomes_an_api_widget():
    pasted = """
- Media:
    - Sonarr:
        href: http://sonarr.lan
        widget:
          type: customapi
          url: http://sonarr.lan/api/v3/queue
          refreshInterval: 30000
          headers:
            X-Api-Key: "{{secret.SONARR}}"
          mappings:
            - field: totalRecords
              label: Queue
              format: number
"""
    made = widgets.from_homepage(pasted)
    assert made["refresh"] == 30 and made["config"]["headers"] == {"X-Api-Key": "{{secret.SONARR}}"}
    assert made["config"]["mappings"][0] | {"remap": []} == {"field": "totalRecords", "label": "Queue", "format": "number", "prefix": "", "suffix": "", "remap": []}
    for text in ("- just: a list", ": not yaml :", "widget: {type: customapi, url: 'ftp://x', mappings: []}"):
        with pytest.raises(ValueError):
            widgets.from_homepage(text)


async def test_stocks_give_each_symbols_price_change_and_closes(monkeypatch):
    def handler(request):
        if "NOPE" in request.url.path:
            return httpx.Response(404, json={"chart": {"result": None}})
        assert request.url.path.endswith(("/^GSPC", "/AAPL")) and request.url.params["range"] == "1mo"
        return httpx.Response(200, json={"chart": {"result": [{"meta": {"shortName": "Apple <b>", "currency": "USD", "regularMarketPrice": 110.0}, "indicators": {"quote": [{"close": [90.0, None, 100.0, 109.99999237]}]}}]}})

    monkeypatch.setattr(netguard, "_transport", httpx.MockTransport(handler))
    widgets.reset()
    config = widgets.validate("stocks", {"symbols": [" aapl ", "^gspc", "AAPL", "nope"]})
    assert config == {"symbols": ["AAPL", "^GSPC", "NOPE"]}
    quotes = (await widgets.data("stocks", config))["data"]["quotes"]
    assert quotes[0] | {"points": 0} == {"symbol": "AAPL", "ok": True, "name": "Apple <b>", "currency": "USD", "price": 110.0, "change": 10.0, "percent": 10.0, "points": 0}
    assert quotes[0]["points"] == [90.0, 100.0, 110.0]  # the source's closes are single precision and quotes[2] == {"symbol": "NOPE", "ok": False}
    for symbols in ([], ["AAPL/../x"], ["a b"], ["x" * 20]):
        with pytest.raises(ValueError):
            widgets.validate("stocks", {"symbols": symbols})
    assert widgets.validate("calendar", {"urls": ["webcal://example.org/cal.ics"]})["urls"] == ["https://example.org/cal.ics"]


async def test_weather_gives_now_the_coming_hours_and_the_coming_days(monkeypatch):
    seen = []

    def handler(request):
        seen.append(request.url)
        return httpx.Response(200, json={
            "current": {"temperature_2m": 11.8, "apparent_temperature": 9.3, "relative_humidity_2m": 80, "weather_code": 61, "wind_speed_10m": 14.4, "is_day": 0},
            "hourly": {"time": ["2026-11-14T11:00", "2026-11-14T12:00"], "temperature_2m": [11.5, 12.0], "weather_code": [61, 3], "precipitation_probability": [13, None], "is_day": [1, 0]},
            "daily": {"time": ["2026-11-14"], "weather_code": [61], "temperature_2m_max": [12.7], "temperature_2m_min": [8.1], "precipitation_probability_max": [91], "sunrise": ["2026-11-14T07:13"], "sunset": ["2026-11-14T16:33"]},
        })

    monkeypatch.setattr(netguard, "_transport", httpx.MockTransport(handler))
    widgets.reset()
    shown = (await widgets.data("weather", widgets.validate("weather", {"latitude": 47.6, "longitude": -122.33, "label": "Seattle", "units": "imperial"})))["data"]
    assert seen[0].params["forecast_hours"] == "24" and seen[0].params["temperature_unit"] == "fahrenheit" and seen[0].host == "api.open-meteo.com"
    assert (shown["temperature"], shown["day"], shown["label"]) == (11.8, False, "Seattle")
    assert shown["hours"] == [{"time": "2026-11-14T11:00", "temperature": 11.5, "code": 61, "rain": 13, "day": True}, {"time": "2026-11-14T12:00", "temperature": 12.0, "code": 3, "rain": None, "day": False}]
    assert shown["days"] == [{"date": "2026-11-14", "code": 61, "max": 12.7, "min": 8.1, "rain": 91, "sunrise": "2026-11-14T07:13", "sunset": "2026-11-14T16:33"}]
    monkeypatch.setattr(netguard, "_transport", httpx.MockTransport(lambda request: httpx.Response(200, json={"current": "?", "hourly": [], "daily": None})))
    widgets.reset()
    assert (await widgets.data("weather", widgets.validate("weather", {"latitude": 1, "longitude": 1})))["data"]["hours"] == []


async def test_a_notice_shows_between_its_times():
    widgets.reset()
    config = widgets.validate("text", {"text": "Patch window", "starts": "2020-01-01T10:00+02:00", "ends": ""})
    assert config["starts"] == "2020-01-01T08:00:00+00:00" and (await widgets.data("text", config))["data"]["active"] is True
    for window in ({"starts": "2999-01-01T00:00"}, {"ends": "2020-01-01T00:00"}):
        assert (await widgets.data("text", widgets.validate("text", {"text": "x", **window})))["data"]["active"] is False
    with pytest.raises(ValueError):
        widgets.validate("text", {"text": "x", "starts": "next tuesday"})
    with pytest.raises(ValueError):
        widgets.validate("usage", {})
