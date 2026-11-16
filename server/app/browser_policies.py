"""Browser policies: named sets of browser settings an application's sessions start under.

A policy holds a Chromium-family part and a Firefox-family part, each a map of the
engine's own policy names to values. The names and the shape of their values come from
the catalogs under `browser_policy_catalog/`, which `server/scripts/ingest_browser_policies.py`
builds from the engines' policy definitions; a name the catalog lacks (a policy only one
fork reads, or one newer than the catalog) is kept as the JSON it was given. The script
also writes the engines' own translations of the titles and descriptions, to
`browser_policy_catalog/l10n/` when the server is packaged; a catalog is served in a
language that has them and in English otherwise.

The policies are one shared record, `browser_policies.yml`. An installed application names
at most one. At launch the policy is written as the files each browser reads, under
`browser_policy_files_path/<id>` on this node, and every file is mounted read-only at
each place of `CHROMIUM_DIRECTORIES` and `FIREFOX_FILES`: a Chromium-family browser reads
every file of its `managed` and `recommended` directories, so `sealskin.json` sits beside
an image's own; a Firefox-family browser reads one `policies.json`, so the mounted file
stands in place of the image's. A part left empty mounts nothing. A file is replaced by
rename, so a running session keeps the policy it started with.
"""

from __future__ import annotations

import copy
import functools
import hashlib
import json
import logging
import os
import shutil
import uuid
from typing import Any

from pydantic import ValidationError

from . import persistence
from .fsutil import safe_join
from .models import BrowserPolicy
from .settings import settings
from .state import state

logger = logging.getLogger(__name__)

ENGINES = ("chromium", "firefox")
CATALOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "browser_policy_catalog")

#: Name of the file written into a Chromium policy directory.
CHROMIUM_FILE = "sealskin.json"
#: Where each Chromium-family browser reads policies, read from the binaries of the images.
CHROMIUM_DIRECTORIES: tuple[tuple[str, str], ...] = (
    ("Chromium, ungoogled-chromium, Helium", "/etc/chromium/policies"),
    ("Google Chrome", "/etc/opt/chrome/policies"),
    ("Brave", "/etc/brave/policies"),
    ("Microsoft Edge", "/etc/opt/edge/policies"),
    ("Opera", "/etc/opt/opera/policies"),
    ("Vivaldi", "/etc/vivaldi/policies"),
)
#: Where each Firefox-family browser reads its one policies file: the system file where the
#: build reads one, and the `distribution` file of the installs the images hold.
FIREFOX_FILES: tuple[tuple[str, str], ...] = (
    ("Firefox", "/etc/firefox/policies/policies.json"),
    ("Firefox ESR", "/etc/firefox-esr/policies/policies.json"),
    ("LibreWolf", "/etc/librewolf/policies/policies.json"),
    ("LibreWolf", "/usr/share/librewolf/distribution/policies.json"),
    ("Zen", "/etc/zen/policies/policies.json"),
    ("Zen", "/opt/zen/distribution/policies.json"),
    ("Mullvad Browser", "/app/Browser/distribution/policies.json"),
)

#: Languages of the web UI a catalog's translation may be packaged in.
LANGUAGES = frozenset(
    {"ar", "da", "de", "es", "fil", "fr", "hi", "it", "ja", "ko", "nl", "pt", "ru", "th", "tr", "vi", "zh"}
)
#: Policies that carry a credential, which the shared record does not hold.
REFUSED: dict[str, frozenset[str]] = {
    "chromium": frozenset({"CloudManagementEnrollmentToken"}),
    "firefox": frozenset(),
}
#: Most bytes one engine's part of a policy takes as JSON.
MAX_PART_BYTES = 1024 * 1024
MAX_NAME_LENGTH = 128


class PolicyError(ValueError):
    """A browser policy holds what an engine would not read, or what is not kept."""


class MissingPolicy(LookupError):
    """An application names a browser policy this node does not have."""


@functools.cache
def catalog(engine: str) -> dict[str, Any]:
    """Return an engine's catalog: its source, its groups, and its policies.

    Raises:
        KeyError: For an engine other than those of `ENGINES`.
    """
    if engine not in ENGINES:
        raise KeyError(engine)
    with open(os.path.join(CATALOG_PATH, f"{engine}.json"), encoding="utf-8") as handle:
        return json.load(handle)


@functools.cache
def localized(engine: str, language: str) -> dict[str, Any]:
    """Return an engine's catalog with the titles and descriptions the engine publishes in a language.

    Args:
        engine: One of `ENGINES`.
        language: A locale of the web UI, as `de` or `pt-BR`; its first part picks the translation.

    Returns:
        The catalog in that language where a translation is packaged, with the texts it lacks
        left in English; the English catalog otherwise.

    Raises:
        KeyError: For an engine other than those of `ENGINES`.
    """
    english = catalog(engine)
    code = language.replace("_", "-").split("-")[0].lower()
    if code not in LANGUAGES:
        return english
    try:
        with open(os.path.join(CATALOG_PATH, "l10n", f"{engine}.{code}.json"), encoding="utf-8") as handle:
            translation = json.load(handle)
    except (OSError, ValueError):
        return english
    result = copy.deepcopy(english)
    result["language"] = code
    for group in result["groups"]:
        group["title"] = translation["groups"].get(group["id"], group["title"])
    for policy in result["policies"]:
        texts = translation["policies"].get(policy["name"]) or {}
        policy["title"] = texts.get("title", policy["title"])
        policy["description"] = texts.get("description", policy["description"])
        holder = policy["schema"].get("items") if "x-labels" not in policy["schema"] else policy["schema"]
        labels = texts.get("labels")
        if labels and isinstance(holder, dict) and len(labels) == len(holder.get("x-labels") or []):
            holder["x-labels"] = labels
    return result


@functools.cache
def _definitions(engine: str) -> dict[str, dict[str, Any]]:
    return {policy["name"]: policy for policy in catalog(engine)["policies"]}


def paths() -> dict[str, list[dict[str, str]]]:
    """Return where each engine's files are mounted, with the browsers that read each place."""
    return {
        "chromium": [
            {"browser": browser, "path": f"{directory}/managed/{CHROMIUM_FILE}"}
            for browser, directory in CHROMIUM_DIRECTORIES
        ],
        "firefox": [{"browser": browser, "path": path} for browser, path in FIREFOX_FILES],
    }


# --- Validation --------------------------------------------------------------


def _matches(kind: str, value: Any) -> bool:
    if kind == "boolean":
        return isinstance(value, bool)
    if kind == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if kind == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if kind == "string":
        return isinstance(value, str)
    if kind == "array":
        return isinstance(value, list)
    if kind == "object":
        return isinstance(value, dict)
    return True


def check_value(schema: dict[str, Any], value: Any, where: str) -> None:
    """Hold a value to a catalog schema.

    Types, choices, bounds, and required properties are checked; a property the schema does
    not name passes, as a browser newer than the catalog may read it.

    Raises:
        PolicyError: Naming the place in the value that does not fit.
    """
    kind = schema.get("type")
    if kind and not _matches(kind, value):
        raise PolicyError(f"{where} takes a value of type {kind}.")
    choices = schema.get("enum")
    if choices and value not in choices:
        raise PolicyError(f"{where} takes one of: {', '.join(str(choice) for choice in choices)}.")
    if kind in ("integer", "number"):
        if "minimum" in schema and value < schema["minimum"]:
            raise PolicyError(f"{where} is at least {schema['minimum']}.")
        if "maximum" in schema and value > schema["maximum"]:
            raise PolicyError(f"{where} is at most {schema['maximum']}.")
    if kind == "array" and isinstance(schema.get("items"), dict):
        for index, item in enumerate(value):
            check_value(schema["items"], item, f"{where}[{index}]")
    if kind == "object":
        properties = schema.get("properties") or {}
        for name in schema.get("required") or []:
            if name not in value:
                raise PolicyError(f"{where} needs '{name}'.")
        for name, item in value.items():
            if name in properties:
                check_value(properties[name], item, f"{where}.{name}")


def _check_part(engine: str, part: dict[str, Any], recommended: bool = False) -> None:
    definitions = _definitions(engine)
    for name, value in part.items():
        if not isinstance(name, str) or not 0 < len(name) <= MAX_NAME_LENGTH:
            raise PolicyError("A policy name is a string of 1 to 128 characters.")
        if name in REFUSED[engine]:
            raise PolicyError(f"{name} carries a credential and is not kept in a browser policy.")
        definition = definitions.get(name)
        if definition is None:
            continue
        if recommended and not definition.get("recommended"):
            raise PolicyError(f"{name} cannot be a default the user changes; set it as enforced.")
        check_value(definition["schema"], value, name)
    if len(json.dumps(part)) > MAX_PART_BYTES:
        raise PolicyError("The policies of one browser family take at most 1 MiB.")


def validate(policy: BrowserPolicy) -> None:
    """Hold a policy to the catalogs.

    Raises:
        PolicyError: For a value its policy does not take, a refused policy, a policy both
            enforced and recommended, or a part over the size limit.
    """
    _check_part("chromium", policy.chromium)
    _check_part("chromium", policy.chromium_recommended, recommended=True)
    _check_part("firefox", policy.firefox)
    both = sorted(set(policy.chromium) & set(policy.chromium_recommended))
    if both:
        raise PolicyError(f"{', '.join(both)} is set both as enforced and as a default; keep one.")


# --- Records -----------------------------------------------------------------


def load() -> None:
    """Read the shared record into `state.browser_policies` and drop the files of policies it lost."""
    records = persistence.read_yaml(settings.browser_policies_path, []) or []
    loaded: dict[str, BrowserPolicy] = {}
    for item in records if isinstance(records, list) else []:
        try:
            policy = BrowserPolicy(**item)
        except (ValidationError, TypeError) as exc:
            logger.warning("Skipping a browser policy record that does not validate: %s", exc)
            continue
        loaded[policy.id] = policy
    state.browser_policies = loaded
    try:
        folders = os.listdir(settings.browser_policy_files_path)
    except OSError:
        return
    for folder in folders:
        if folder not in loaded:
            _forget_files(folder)


def _forget_files(policy_id: str) -> None:
    """Remove this node's files of a policy; a running session keeps the ones it mounted."""
    try:
        shutil.rmtree(safe_join(settings.browser_policy_files_path, policy_id), ignore_errors=True)
    except ValueError:
        pass


async def _save() -> None:
    await persistence.write_yaml(
        settings.browser_policies_path, [policy.model_dump() for policy in state.browser_policies.values()]
    )


def new_id() -> str:
    """Return an identifier for a new policy."""
    return uuid.uuid4().hex


async def put(policy: BrowserPolicy) -> BrowserPolicy:
    """Keep a policy, new or changed.

    Raises:
        PolicyError: When `validate` refuses it, or another policy has its name.
        store.Conflict: When another node changed the record meanwhile; nothing is kept.
        store.StoreUnavailable: When the shared record cannot be written; nothing is kept.
    """
    validate(policy)
    taken = any(
        other.id != policy.id and other.name.casefold() == policy.name.casefold()
        for other in state.browser_policies.values()
    )
    if taken:
        raise PolicyError(f"Another browser policy is named '{policy.name}'.")
    previous = state.browser_policies.get(policy.id)
    state.browser_policies[policy.id] = policy
    try:
        await _save()
    except BaseException:
        # The record was not written: this node goes on with what the others have.
        if previous is None:
            del state.browser_policies[policy.id]
        else:
            state.browser_policies[policy.id] = previous
        raise
    return policy


def used_by(policy_id: str) -> list[str]:
    """Return the names of the installed applications that name a policy."""
    return sorted(app.name for app in state.installed_apps.values() if app.browser_policy == policy_id)


async def delete(policy_id: str) -> None:
    """Remove a policy and this node's files of it.

    Raises:
        KeyError: When there is no such policy.
        PolicyError: While an installed application names it.
    """
    if policy_id not in state.browser_policies:
        raise KeyError(policy_id)
    holders = used_by(policy_id)
    if holders:
        raise PolicyError(f"{', '.join(holders)} use this browser policy. Assign them another first.")
    removed = state.browser_policies.pop(policy_id)
    try:
        await _save()
    except BaseException:
        state.browser_policies[policy_id] = removed
        raise
    _forget_files(policy_id)


# --- Files -------------------------------------------------------------------


def render(policy: BrowserPolicy) -> dict[str, bytes]:
    """Return the files of a policy, keyed by the path a container reads each at."""

    def encoded(document: dict[str, Any]) -> bytes:
        return (json.dumps(document, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")

    files: dict[str, bytes] = {}
    for level, part in (("managed", policy.chromium), ("recommended", policy.chromium_recommended)):
        if part:
            for _browser, directory in CHROMIUM_DIRECTORIES:
                files[f"{directory}/{level}/{CHROMIUM_FILE}"] = encoded(part)
    if policy.firefox:
        for _browser, path in FIREFOX_FILES:
            files[path] = encoded({"policies": policy.firefox})
    return files


def _write(path: str, data: bytes) -> None:
    """Write a file by rename unless it already holds `data`, so a mounted copy stays as it was."""
    try:
        with open(path, "rb") as handle:
            if handle.read() == data:
                return
    except OSError:
        pass
    temporary = f"{path}.tmp-{uuid.uuid4().hex}"
    with open(temporary, "wb") as handle:
        handle.write(data)
    os.chmod(temporary, 0o644)
    os.replace(temporary, path)


def mounts_for(policy_id: str | None) -> dict[str, dict[str, str]]:
    """Write a policy's files on this node and return the volumes that mount them read-only.

    Each place a browser reads gets a file of its own, as a launch's volumes are keyed by
    the path on the node.

    Raises:
        MissingPolicy: When the policy an application names is not among this node's.
    """
    if not policy_id:
        return {}
    policy = state.browser_policies.get(policy_id)
    if policy is None:
        raise MissingPolicy(policy_id)
    folder = safe_join(settings.browser_policy_files_path, policy.id)
    os.makedirs(folder, exist_ok=True, mode=0o755)
    volumes: dict[str, dict[str, str]] = {}
    for container_path, data in render(policy).items():
        name = hashlib.sha256(container_path.encode("utf-8")).hexdigest()[:16] + ".json"
        path = os.path.join(folder, name)
        _write(path, data)
        volumes[path] = {"bind": container_path, "mode": "ro"}
    return volumes
