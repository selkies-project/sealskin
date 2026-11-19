#!/usr/bin/env python3
"""Build the browser policy catalogs from the engines' own policy definitions.

Chromium keeps one YAML file per policy under `components/policy/resources/templates`;
Firefox keeps one JSON Schema of every policy. Both are read at a pinned revision and
written in one shape to `server/app/browser_policy_catalog/<engine>.json`, one policy
per line, so that a refresh shows as a diff:

    python3 server/scripts/ingest_browser_policies.py
    python3 server/scripts/ingest_browser_policies.py --chromium <commit> --firefox <commit>
    python3 server/scripts/ingest_browser_policies.py --translations

`--translations` writes nothing but the translations the engines publish of those texts,
to `browser_policy_catalog/l10n/<engine>.<language>.json` for the languages of the web UI.
They are build output, made when the server is packaged and kept out of the repository.
Chromium's come from the `policy_templates_<locale>.xtb` beside its definitions, where a
message is found by the fingerprint of its English text; Firefox's from the
`policies-descriptions.ftl` of its localization repository. A text an engine has not
translated stays English.

A policy of the catalog carries `name`, `group`, `title`, `description`, `schema`, and,
where they apply, `example`, `since`, `recommended` (Chromium: may be a default the user
changes), `restart` (Firefox: read at start only), `deprecated`, and `common` (its place
in the short list the editor shows first). `schema` is JSON Schema cut down to `type`,
`enum` with `x-labels`, `items`, `properties`, `required`, `minimum`, `maximum`, `format`,
and `description`; an empty schema takes any JSON value.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import html
import io
import json
import re
import sys
import tarfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import yaml

CHROMIUM_REVISION = "b760e58eb02c59981c5d4fa06dbed8c16fe5848e"
FIREFOX_REVISION = "d7161e35da7704cff60e48ee86fb7747ffcc93c7"

CHROMIUM_ARCHIVE = (
    "https://chromium.googlesource.com/chromium/src/+archive/{revision}/components/policy/resources/templates.tar.gz"
)
FIREFOX_SCHEMA = (
    "https://raw.githubusercontent.com/mozilla-firefox/firefox/{revision}"
    "/browser/components/enterprisepolicies/schemas/policies-schema.json"
)

CHROMIUM_XTB = (
    "https://chromium.googlesource.com/chromium/src/+/{revision}"
    "/components/policy/resources/policy_templates_{locale}.xtb?format=TEXT"
)
FIREFOX_L10N_REVISION = "fd150ccad0a5326cae85d8ad1febc2d0e10d85a5"
FIREFOX_FTL = (
    "https://raw.githubusercontent.com/mozilla-l10n/firefox-l10n/{revision}"
    "/{locale}/browser/browser/policies/policies-descriptions.ftl"
)
#: The locale each engine publishes for a language of the web UI.
CHROMIUM_LOCALES = {
    "de": "de", "es": "es", "fr": "fr", "it": "it", "ja": "ja", "ko": "ko", "nl": "nl",
    "pt": "pt-BR", "ru": "ru", "th": "th", "tr": "tr", "vi": "vi", "zh": "zh-CN",
}
FIREFOX_LOCALES = {
    "ar": "ar", "da": "da", "de": "de", "es": "es-ES", "fil": "tl", "fr": "fr", "hi": "hi-IN",
    "it": "it", "ja": "ja", "ko": "ko", "nl": "nl", "pt": "pt-BR", "ru": "ru", "th": "th",
    "tr": "tr", "vi": "vi", "zh": "zh-CN",
}

OUTPUT = Path(__file__).resolve().parent.parent / "app" / "browser_policy_catalog"

#: The policies the editor lists before any search, in the order shown.
CHROMIUM_COMMON = (
    "HomepageLocation",
    "HomepageIsNewTabPage",
    "ShowHomeButton",
    "NewTabPageLocation",
    "RestoreOnStartup",
    "RestoreOnStartupURLs",
    "ManagedBookmarks",
    "BookmarkBarEnabled",
    "EditBookmarksEnabled",
    "URLAllowlist",
    "URLBlocklist",
    "ExtensionInstallForcelist",
    "ExtensionInstallAllowlist",
    "ExtensionInstallBlocklist",
    "IncognitoModeAvailability",
    "DeveloperToolsAvailability",
    "BrowserGuestModeEnabled",
    "BrowserAddPersonEnabled",
    "BrowserSignin",
    "SyncDisabled",
    "PasswordManagerEnabled",
    "DownloadRestrictions",
    "DownloadDirectory",
    "PromptForDownloadLocation",
    "PrintingEnabled",
    "DefaultPopupsSetting",
    "DefaultNotificationsSetting",
    "DefaultSearchProviderEnabled",
    "DefaultSearchProviderName",
    "DefaultSearchProviderSearchURL",
    "ProxySettings",
    "DnsOverHttpsMode",
    "CACertificates",
    "ClearBrowsingDataOnExitList",
    "SafeBrowsingProtectionLevel",
    "TranslateEnabled",
    "DefaultBrowserSettingEnabled",
    "MetricsReportingEnabled",
)
FIREFOX_COMMON = (
    "Homepage",
    "ShowHomeButton",
    "NewTabPage",
    "OverrideFirstRunPage",
    "ManagedBookmarks",
    "DisplayBookmarksToolbar",
    "NoDefaultBookmarks",
    "WebsiteFilter",
    "ExtensionSettings",
    "Extensions",
    "InstallAddonsPermission",
    "DisablePrivateBrowsing",
    "DisableDeveloperTools",
    "BlockAboutConfig",
    "DisableFirefoxAccounts",
    "PasswordManagerEnabled",
    "OfferToSaveLogins",
    "AutofillAddressEnabled",
    "AutofillCreditCardEnabled",
    "DisableFormHistory",
    "DownloadDirectory",
    "PromptForDownloadLocation",
    "PrintingEnabled",
    "PopupBlocking",
    "SearchEngines",
    "Proxy",
    "DNSOverHTTPS",
    "Certificates",
    "SanitizeOnShutdown",
    "EnableTrackingProtection",
    "TranslateEnabled",
    "DontCheckDefaultBrowser",
    "DisableAppUpdate",
    "DisableTelemetry",
    "DisableFirefoxStudies",
    "Preferences",
)

SCHEMA_KEYS = ("type", "enum", "items", "properties", "required", "minimum", "maximum", "description")
_PLACEHOLDER = re.compile(r'<ph\b[^>]*?name="([^"]*)"[^>]*>(.*?)</ph>', re.S)
_EXAMPLE = re.compile(r"<ex>(.*?)</ex>", re.S)
_TRANSLATION = re.compile(r'<translation id="(\d+)">(.*?)</translation>', re.S)
_TRANSLATED_PLACEHOLDER = re.compile(r'<ph name="([^"]*)"\s*/>')
_FTL_ENTRY = re.compile(r"^policy-([A-Za-z0-9]+) = (.*(?:\n[ \t]+.*)*)", re.M)


#: Attempts at a URL before its error is raised, and the wait before the second.
FETCH_ATTEMPTS = 6
FETCH_FIRST_WAIT_S = 2.0


def fetch(url: str) -> bytes:
    """Return what `url` serves.

    A rate limit or server error (429, 5xx) and a dropped connection are tried again after
    a wait that doubles each time, or the wait a `Retry-After` header asks for: the
    sources throttle a burst of requests from a shared runner address.
    """
    request = urllib.request.Request(url, headers={"User-Agent": "policy-catalog-ingest"})
    wait = FETCH_FIRST_WAIT_S
    for attempt in range(1, FETCH_ATTEMPTS + 1):
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                return response.read()
        except urllib.error.HTTPError as error:
            if attempt == FETCH_ATTEMPTS or (error.code != 429 and error.code < 500):
                raise
            asked = error.headers.get("Retry-After", "") if error.headers else ""
            delay = float(asked) if asked.isdigit() else wait
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            if attempt == FETCH_ATTEMPTS:
                raise
            delay = wait
        print(f"{url}: attempt {attempt} failed, trying again in {delay:.0f} s", file=sys.stderr)
        time.sleep(delay)
        wait *= 2
    raise AssertionError("unreachable")


def plain(text: str | None) -> str:
    """Return a Chromium message as plain text.

    A `<ph>` placeholder becomes the text it holds, or its `<ex>` example where it holds
    only a `$1` argument.
    """
    return _tidy(_PLACEHOLDER.sub(lambda match: _shown(match.group(2)), text or ""))


def _shown(inner: str) -> str:
    """Return what a placeholder shows: its text, or its example where the text is an argument."""
    example = _EXAMPLE.search(inner)
    shown = _EXAMPLE.sub("", inner).strip()
    if example and re.fullmatch(r"\$\d+", shown):
        return example.group(1)
    return shown


def _tidy(text: str) -> str:
    lines = [line.strip() for line in html.unescape(text).strip().splitlines()]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines))


def message_id(text: str) -> str:
    """Return the id a translation bundle files a Chromium message under.

    It is the fingerprint grit takes of the message with each placeholder as its name:
    the first 64 bits of the MD5, with the sign bit cleared.
    """
    presented = html.unescape(_PLACEHOLDER.sub(lambda match: match.group(1), text))
    return str(int(hashlib.md5(presented.encode("utf-8"), usedforsecurity=False).hexdigest()[:16], 16) & 0x7FFFFFFFFFFFFFFF)


def translated(text: str | None, bundle: dict[str, str]) -> str | None:
    """Return a Chromium message in a bundle's language as plain text, or `None` when it holds none."""
    found = bundle.get(message_id(text)) if text else None
    if found is None:
        return None
    shown = {match.group(1): _shown(match.group(2)) for match in _PLACEHOLDER.finditer(text)}
    return _tidy(_TRANSLATED_PLACEHOLDER.sub(lambda match: shown.get(match.group(1), ""), found))


def chromium_schema(schema: Any, shared: dict[str, Any], open_refs: tuple[str, ...] = ()) -> Any:
    """Return a Chromium policy schema with its `$ref`s inlined and only the catalog's keys.

    A schema that refers to itself ends in an empty schema at the second level.
    """
    if isinstance(schema, list):
        return [chromium_schema(item, shared, open_refs) for item in schema]
    if not isinstance(schema, dict):
        return schema
    ref = schema.get("$ref")
    if ref is not None:
        if ref in open_refs or ref not in shared:
            return {}
        return chromium_schema(shared[ref], shared, (*open_refs, ref))
    result: dict[str, Any] = {}
    for key in SCHEMA_KEYS:
        if key not in schema:
            continue
        value = schema[key]
        if key == "properties":
            result[key] = {name: chromium_schema(sub, shared, open_refs) for name, sub in value.items()}
        elif key == "items":
            result[key] = chromium_schema(value, shared, open_refs)
        else:
            result[key] = value
    return result


def linux_since(supported_on: list[str]) -> str | None:
    """Return the version a policy reached Linux desktop in, or `None` when it is not read there."""
    for entry in supported_on:
        platform, _, versions = entry.partition(":")
        if platform not in ("chrome.*", "chrome.linux"):
            continue
        since, _, until = versions.partition("-")
        if not until:
            return since
    return None


def chromium_catalog(revision: str) -> dict[str, Any]:
    """Read the Chromium policy definitions at `revision` into a catalog."""
    archive = tarfile.open(fileobj=io.BytesIO(fetch(CHROMIUM_ARCHIVE.format(revision=revision))))
    files = {member.name.removeprefix("./"): member for member in archive.getmembers() if member.isfile()}

    def load(name: str) -> Any:
        return yaml.safe_load(archive.extractfile(files[name]).read())

    shared = load("common_schemas.yaml")
    groups: dict[str, str] = {}
    group_messages: dict[str, str] = {}
    messages: dict[str, dict[str, Any]] = {}
    policies: list[dict[str, Any]] = []
    for path in sorted(files):
        parts = path.split("/")
        if len(parts) != 3 or parts[0] != "policy_definitions" or not parts[2].endswith(".yaml"):
            continue
        group, filename = parts[1], parts[2]
        if filename == ".group.details.yaml":
            group_messages[group] = load(path).get("caption") or ""
            groups[group] = plain(group_messages[group]) or group
            continue
        if filename.startswith(".") or filename == "policy_atomic_groups.yaml":
            continue
        definition = load(path)
        since = linux_since(definition.get("supported_on") or [])
        if since is None or definition.get("device_only"):
            continue
        schema = chromium_schema(definition.get("schema") or {}, shared)
        items = [item for item in definition.get("items") or [] if item.get("value") is not None]
        labelled = bool(items) and definition.get("type") in ("int-enum", "string-enum", "string-enum-list")
        if labelled:
            target = schema.get("items", schema) if definition["type"] == "string-enum-list" else schema
            target["enum"] = [item["value"] for item in items]
            target["x-labels"] = [plain(item.get("caption")) or str(item["value"]) for item in items]
        messages[filename.removesuffix(".yaml")] = {
            "title": definition.get("caption"),
            "description": definition.get("desc"),
            "labels": [item.get("caption") for item in items] if labelled else [],
        }
        policy: dict[str, Any] = {
            "name": filename.removesuffix(".yaml"),
            "group": group,
            "title": plain(definition.get("caption")),
            "description": plain(definition.get("desc")),
            "schema": schema,
            "since": since,
        }
        if definition.get("example_value") is not None:
            policy["example"] = definition["example_value"]
        if (definition.get("features") or {}).get("can_be_recommended"):
            policy["recommended"] = True
        if definition.get("deprecated"):
            policy["deprecated"] = True
        policies.append(policy)
    used = {policy["group"] for policy in policies}
    return {
        "engine": "chromium",
        "source": "https://chromium.googlesource.com/chromium/src/+/main/components/policy/resources/templates",
        "revision": revision,
        "groups": [{"id": group, "title": groups.get(group, group)} for group in sorted(used)],
        "policies": policies,
        "messages": {"policies": messages, "groups": {group: group_messages.get(group, "") for group in used}},
    }


def chromium_translation(catalog: dict[str, Any], locale: str) -> dict[str, Any]:
    """Return the texts of a Chromium catalog that the bundle of `locale` translates."""
    raw = base64.b64decode(fetch(CHROMIUM_XTB.format(revision=catalog["revision"], locale=locale))).decode("utf-8")
    bundle = dict(_TRANSLATION.findall(raw))
    policies: dict[str, dict[str, Any]] = {}
    for policy in catalog["policies"]:
        source = catalog["messages"]["policies"][policy["name"]]
        entry: dict[str, Any] = {}
        for key in ("title", "description"):
            text = translated(source[key], bundle)
            if text:
                entry[key] = text
        labels = [translated(label, bundle) for label in source["labels"]]
        if any(labels):
            english = policy["schema"].get("items", policy["schema"]).get("x-labels") or policy["schema"]["x-labels"]
            entry["labels"] = [label or english[index] for index, label in enumerate(labels)]
        if entry:
            policies[policy["name"]] = entry
    groups = {group: text for group, source in catalog["messages"]["groups"].items() if (text := translated(source, bundle))}
    return {"revision": catalog["revision"], "locale": locale, "groups": groups, "policies": policies}


def firefox_translation(catalog: dict[str, Any], locale: str, revision: str) -> dict[str, Any]:
    """Return the descriptions of a Firefox catalog that the localization of `locale` holds.

    A message id is `policy-<name>` with a number after it once its text was rewritten; the
    highest number is the current text.
    """
    raw = fetch(FIREFOX_FTL.format(revision=revision, locale=locale)).decode("utf-8")
    names = {policy["name"] for policy in catalog["policies"]}
    found: dict[str, tuple[int, str]] = {}
    for match in _FTL_ENTRY.finditer(raw):
        key, text = match.group(1), " ".join(line.strip() for line in match.group(2).splitlines())
        name, version = key, 0
        if key not in names:
            stem = key.rstrip("0123456789")
            name, version = stem, int(key[len(stem):] or 0)
        if name in names and version >= found.get(name, (-1, ""))[0]:
            found[name] = (version, re.sub(r"\{\s*-[a-z-]*brand[a-z-]*\s*\}", "Firefox", text))
    policies = {name: {"description": text} for name, (_version, text) in sorted(found.items()) if "{" not in text}
    return {"revision": revision, "locale": locale, "groups": {}, "policies": policies}


def firefox_schema(schema: Any, definitions: dict[str, Any]) -> Any:
    """Return a Firefox policy schema in the catalog's subset.

    A `$ref` to a URL definition becomes a string of format `url`, a `oneOf` of constants an
    `enum` with labels, and a value of several types or of free property names an empty or
    open schema.
    """
    if not isinstance(schema, dict):
        return {}
    ref = schema.get("$ref")
    if ref is not None:
        return {"type": "string", "format": "url"} if ref.rsplit("/", 1)[-1] in definitions else {}
    kind = schema.get("type")
    if isinstance(kind, list):
        return {}
    result: dict[str, Any] = {}
    if kind is not None:
        result["type"] = "number" if kind == "number" else kind
    choices = schema.get("oneOf")
    if isinstance(choices, list) and choices and all(isinstance(choice, dict) and "const" in choice for choice in choices):
        result["enum"] = [choice["const"] for choice in choices]
        result["x-labels"] = [choice.get("title") or str(choice["const"]) for choice in choices]
    elif isinstance(schema.get("enum"), list):
        result["enum"] = schema["enum"]
    if kind == "object":
        if "patternProperties" in schema or not schema.get("properties"):
            return {"type": "object"}
        result["properties"] = {name: firefox_schema(sub, definitions) for name, sub in schema["properties"].items()}
        if schema.get("required"):
            result["required"] = schema["required"]
    if kind == "array":
        result["items"] = firefox_schema(schema.get("items") or {}, definitions)
    for key in ("minimum", "maximum"):
        if key in schema:
            result[key] = schema[key]
    return result


def firefox_catalog(revision: str) -> dict[str, Any]:
    """Read Firefox's policy schema at `revision` into a catalog."""
    document = json.loads(fetch(FIREFOX_SCHEMA.format(revision=revision)))
    definitions = document.get("definitions") or {}
    policies: list[dict[str, Any]] = []
    for name, definition in sorted(document["properties"].items()):
        policy: dict[str, Any] = {
            "name": name,
            "group": definition.get("x-category") or "Miscellaneous",
            "title": name,
            "description": (definition.get("description") or "").strip(),
            "schema": firefox_schema(definition, definitions),
        }
        since = ((definition.get("x-compatibility") or {}).get("firefox") or {}).get("version_added")
        if since:
            policy["since"] = str(since)
        examples = definition.get("examples") or []
        if examples:
            policy["example"] = examples[-1]
        if definition.get("x-restart-required"):
            policy["restart"] = True
        if definition.get("deprecated"):
            policy["deprecated"] = True
        policies.append(policy)
    used = sorted({policy["group"] for policy in policies})
    return {
        "engine": "firefox",
        "source": "https://github.com/mozilla-firefox/firefox/blob/main/browser/components/enterprisepolicies/schemas/policies-schema.json",
        "revision": revision,
        "groups": [{"id": group, "title": group} for group in used],
        "policies": policies,
    }


def mark_common(catalog: dict[str, Any], names: tuple[str, ...]) -> None:
    """Number the policies of the short list, and stop on a name the catalog lost or one deprecated."""
    by_name = {policy["name"]: policy for policy in catalog["policies"]}
    missing = [name for name in names if name not in by_name]
    if missing:
        sys.exit(f"{catalog['engine']}: the common list names policies the source no longer has: {', '.join(missing)}")
    stale = [name for name in names if by_name[name].get("deprecated")]
    if stale:
        sys.exit(f"{catalog['engine']}: the common list names deprecated policies: {', '.join(stale)}")
    for place, name in enumerate(names, start=1):
        by_name[name]["common"] = place


def write(catalog: dict[str, Any]) -> Path:
    """Write a catalog with one policy per line."""
    head = {key: value for key, value in catalog.items() if key not in ("policies", "messages")}
    lines = [json.dumps(policy, ensure_ascii=False, sort_keys=True, separators=(",", ":")) for policy in catalog["policies"]]
    body = json.dumps(head, ensure_ascii=False, indent=1)[:-2] + ',\n "policies": [\n' + ",\n".join(lines) + "\n ]\n}\n"
    OUTPUT.mkdir(parents=True, exist_ok=True)
    path = OUTPUT / f"{catalog['engine']}.json"
    path.write_text(body, encoding="utf-8")
    return path


def write_translations(chromium: dict[str, Any], firefox: dict[str, Any], firefox_l10n: str) -> None:
    """Write every language's translations of both catalogs."""
    folder = OUTPUT / "l10n"
    folder.mkdir(parents=True, exist_ok=True)
    jobs = [("chromium", language, locale) for language, locale in CHROMIUM_LOCALES.items()]
    jobs += [("firefox", language, locale) for language, locale in FIREFOX_LOCALES.items()]
    for engine, language, locale in jobs:
        if engine == "chromium":
            translation = chromium_translation(chromium, locale)
        else:
            translation = firefox_translation(firefox, locale, firefox_l10n)
        path = folder / f"{engine}.{language}.json"
        path.write_text(json.dumps(translation, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        print(f"{path}: {len(translation['policies'])} policies in {locale}")


def main() -> None:
    """Fetch both sources and write both catalogs, or their translations."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--chromium", default=CHROMIUM_REVISION, help="commit of chromium/src")
    parser.add_argument("--firefox", default=FIREFOX_REVISION, help="commit of mozilla-firefox/firefox")
    parser.add_argument("--firefox-l10n", default=FIREFOX_L10N_REVISION, help="commit of mozilla-l10n/firefox-l10n")
    parser.add_argument("--translations", action="store_true", help="write the translations, not the catalogs")
    arguments = parser.parse_args()
    chromium, firefox = chromium_catalog(arguments.chromium), firefox_catalog(arguments.firefox)
    if arguments.translations:
        write_translations(chromium, firefox, arguments.firefox_l10n)
        return
    for catalog, common in ((chromium, CHROMIUM_COMMON), (firefox, FIREFOX_COMMON)):
        mark_common(catalog, common)
        path = write(catalog)
        print(f"{path}: {len(catalog['policies'])} policies at {catalog['revision']}")


if __name__ == "__main__":
    main()
