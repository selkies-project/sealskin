"""The Bakery: recipes that build a layer of an administrator's own on an application's image.

A recipe belongs to one installed application and is the shared record
`bakery/recipes.yml`; the files it copies into the image are the shared
objects `bakery/files/<sha256>`, named by their content. A `guided` recipe is
an ordered list of steps (`BakeStep`), each rendered to the Dockerfile lines a
linuxserver image is built with: one `RUN` per step that installs with the
distribution's package manager and removes what the install left behind
(`FAMILIES`). The applications, font sets, toolchains, and package sets a
step offers by name are the tables of `bakekits`, each per family. The
distribution is read from the image's own `os-release`. The
`FROM` line of a guided recipe is the application's image and nothing else. A
`dockerfile` recipe is the administrator's own text, built as written: the
application's image reaches it as the build argument `BASE_IMAGE`, so stages
may come before it, and `lint` says what a session would not survive without
refusing it.

Every node builds its own images, with the backend's builder
(`BaseProvider.build_image`), from the image it already pulled; a build pulls
nothing of the application and is handed no registry credential. An image is
tagged `sealskin.local/bake/<application id>:<key>`, the key a hash of the
base image's id, the Dockerfile, and the files, so a build of what is already
there is found, not repeated, and a base image that changed is a new key: the
image pull calls `base_pulled`, which bakes again. Sessions run the image of
the recipe as it is now once that has built, and the last one that built
until then (`image_for`); an application with no image baked yet waits for
its first build at launch, and a recipe that fails to build leaves the last
image in use.

A recipe and its application are one thing: the Bakery makes a baked
application, a meta-app of an installed one, together with its recipe, and
either goes with the other. A meta-app the App Laboratory makes from a baked
application has no recipe of its own and runs the baked image. An
application is not deleted while another is built on it this way
(`held_by`).

In a cluster the recipes and files are read by every node, each of which
builds its own image when the record changes; a baked application's empty
home template is made on whichever node launches it (`ensure_template`).

What a node built and why a build failed (`node_state_path/bakery`) are its
own; the package lists the step editor searches are read from a throwaway
container of the image and kept there too.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import logging
import os
import re
import shlex
import tarfile
import time
import uuid
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit
from xml.sax.saxutils import escape as xml_escape
from xml.sax.saxutils import quoteattr

import yaml
from fastapi import HTTPException
from pydantic import ValidationError

from . import bakekits, persistence, progress, registries
from .fsutil import safe_join
from .models import BakeFile, BakeRecipe, BakeStep, InstalledApp
from .settings import settings
from .state import state

logger = logging.getLogger(__name__)

RECIPES_NAME = "recipes.yml"
FILES_DIR = "files"
#: The repository every baked image is a tag of; no registry answers to the name.
IMAGE_PREFIX = "sealskin.local/bake"
#: Image labels: the application a bake is for, and the server that built it.
BAKE_LABEL = "sealskin.app/bake"
NODE_LABEL = "sealskin.app/bake-node"
#: The build argument a `dockerfile` recipe takes the application's image by.
BASE_ARG = "BASE_IMAGE"
#: Where a build finds the recipe's files, relative to its context.
CONTEXT_FILES = "files"

MAX_FILE_BYTES = 64 * 1024 * 1024
MAX_FILES = 64
MAX_DOCKERFILE_BYTES = 256 * 1024
MAX_LOG_LINES = 20000
#: Seconds a finished build is remembered, and its image kept when no recipe names it.
KEEP_BUILD_SECONDS = 3600
#: Seconds between two rounds of `prune`, and how long a stored file may go unnamed by any recipe before one removes it.
PRUNE_SECONDS = 3600
UNUSED_FILE_SECONDS = 24 * 3600
#: Seconds a package list may take to read from an image.
INDEX_TIMEOUT = 300
#: Seconds a package list is kept on disk.
INDEX_KEEP_SECONDS = 30 * 24 * 3600
SEARCH_RESULTS = 60

_FILE_NAME = re.compile(r"^[^/\\\x00-\x1f]{1,128}$")
_PACKAGE = re.compile(r"^[A-Za-z0-9@][A-Za-z0-9+._:@=~-]*$")
_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
_PLAIN_PATH = re.compile(r"^[A-Za-z0-9._/+@%:,=-]+$")
_PPA = re.compile(r"^[a-z0-9][a-z0-9.+-]*/[a-z0-9][a-z0-9.+-]*$")
#: Line endings after which a shell command goes on, so no `&&` joins the next line to it.
_OPEN_ENDINGS = ("&&", "||", "|", ";", "then", "do", "else", "{", "(")
_CLOSERS = ("fi", "done", "esac", "}", "else", "elif", ")")


class RecipeError(ValueError):
    """A recipe that cannot be rendered or built as it stands."""


class StepError(ValueError):
    """A step whose fields do not make an instruction."""


@dataclass(frozen=True)
class Family:
    """How the images of one distribution family install and clean up.

    Attributes:
        key: The family's name in records and answers.
        manager: The package manager, for the editor.
        refresh: Commands run before an install.
        install: The install command, the package names following it.
        install_file: The command that installs a package file by its path.
        cleanup: Commands run after an install.
        leftovers: Paths removed after an install.
        cert_dir: Where a trusted certificate is put.
        cert_command: The command that takes the certificates of `cert_dir` up.
        index: A shell script that prints the installable packages.
    """

    key: str
    manager: str
    refresh: tuple[str, ...]
    install: tuple[str, ...]
    install_file: tuple[str, ...]
    cleanup: tuple[str, ...]
    leftovers: tuple[str, ...]
    cert_dir: str
    cert_command: str
    index: str


FAMILIES: dict[str, Family] = {
    "alpine": Family(
        key="alpine",
        manager="apk",
        refresh=(),
        install=("apk add --no-cache",),
        install_file=("apk add --no-cache --allow-untrusted",),
        cleanup=(),
        leftovers=("/tmp/*",),
        cert_dir="/usr/local/share/ca-certificates",
        cert_command="update-ca-certificates",
        index="apk update >/dev/null 2>&1; apk search -v",
    ),
    "arch": Family(
        key="arch",
        manager="pacman",
        refresh=(),
        install=("pacman -Sy --noconfirm --needed",),
        install_file=("pacman -U --noconfirm",),
        cleanup=(),
        leftovers=("/tmp/*", "/var/cache/pacman/pkg/*", "/var/lib/pacman/sync/*"),
        cert_dir="/etc/ca-certificates/trust-source/anchors",
        cert_command="update-ca-trust",
        index="pacman -Sy >/dev/null 2>&1; pacman -Ss",
    ),
    "debian": Family(
        key="debian",
        manager="apt",
        refresh=("apt-get update",),
        install=("DEBIAN_FRONTEND=noninteractive", "apt-get install -y --no-install-recommends"),
        install_file=("DEBIAN_FRONTEND=noninteractive", "apt-get install -y --no-install-recommends"),
        cleanup=("apt-get autoclean",),
        leftovers=("/var/lib/apt/lists/*", "/var/tmp/*", "/tmp/*"),
        cert_dir="/usr/local/share/ca-certificates",
        cert_command="update-ca-certificates",
        index="apt-get update >/dev/null 2>&1; apt-cache search .",
    ),
    "fedora": Family(
        key="fedora",
        manager="dnf",
        refresh=(),
        install=("dnf install -y --setopt=install_weak_deps=False --best",),
        install_file=("dnf install -y --setopt=install_weak_deps=False",),
        cleanup=("dnf autoremove -y", "dnf clean all"),
        leftovers=("/tmp/*",),
        cert_dir="/etc/pki/ca-trust/source/anchors",
        cert_command="update-ca-trust",
        index="dnf -q repoquery --qf '%{name} - %{summary}\\n' 2>/dev/null",
    ),
}

#: The `ID` and `ID_LIKE` words of `os-release` that name each family.
_FAMILY_WORDS = {
    "alpine": ("alpine",),
    "arch": ("arch", "archlinux", "manjaro", "endeavouros", "cachyos"),
    "debian": ("debian", "ubuntu"),
    "fedora": ("fedora", "rhel", "centos", "rocky", "almalinux"),
}


def family_of(release: dict[str, str]) -> str:
    """Return the family an `os-release` names, or `""` for one no step knows how to install on."""
    words = [release.get("ID", "").lower(), *release.get("ID_LIKE", "").lower().split()]
    for word in words:
        for family, names in _FAMILY_WORDS.items():
            if word in names:
                return family
    return ""


def parse_os_release(text: str) -> dict[str, str]:
    """Return the fields of an `os-release` file."""
    fields: dict[str, str] = {}
    for line in text.splitlines():
        name, found, value = line.strip().partition("=")
        if not found or name.startswith("#"):
            continue
        try:
            parts = shlex.split(value)
        except ValueError:
            parts = [value.strip("\"'")]
        fields[name] = parts[0] if parts else ""
    return fields


# --- Records -----------------------------------------------------------------


def _dir() -> str:
    """Return the directory of the shared records and files."""
    return settings.bakery_path


def _file_path(digest: str) -> str:
    """Return where a recipe file of a digest is kept.

    Raises:
        ValueError: For anything but a SHA-256 in hex.
    """
    if not _SHA256.fullmatch(digest):
        raise ValueError("A recipe file is named by its SHA-256.")
    return os.path.join(_dir(), FILES_DIR, digest)


def _state_path(name: str) -> str:
    """Return a file of this node's own bakery state."""
    return safe_join(settings.node_state_path, "bakery", name)


def load() -> None:
    """Read the shared recipes into `state.bake_recipes`."""
    document = persistence.read_yaml(os.path.join(_dir(), RECIPES_NAME), {}) or {}
    records = document.get("recipes") if isinstance(document, dict) else None
    loaded: dict[str, BakeRecipe] = {}
    for item in records if isinstance(records, list) else []:
        try:
            recipe = BakeRecipe(**item)
        except (ValidationError, TypeError) as exc:
            logger.warning("Skipping a Bakery recipe that does not validate: %s", exc)
            continue
        loaded[recipe.app_id] = recipe
    state.bake_recipes = loaded


async def save() -> None:
    """Write `state.bake_recipes` to the shared record."""
    await persistence.write_yaml(
        os.path.join(_dir(), RECIPES_NAME),
        {"recipes": [recipe.model_dump(exclude_defaults=True) | {"app_id": recipe.app_id} for recipe in state.bake_recipes.values()]},
    )


def recipe_for(app: InstalledApp) -> BakeRecipe | None:
    """Return the recipe an application's sessions run the image of.

    The application's own, else that of the application a meta-app was made
    from while both run the same image.
    """
    own = state.bake_recipes.get(app.id)
    if own is not None:
        return own
    base = state.installed_apps.get(app.base_app_id or "")
    if base is not None and base.provider_config.image == app.provider_config.image:
        return state.bake_recipes.get(base.id)
    return None


def held_by(app_id: str) -> list[str]:
    """Return the names of the applications that keep one from being deleted.

    The baked applications built on it, and the meta-apps that run a baked
    application's image: each is deleted before what it is built on.
    """
    baked = app_id in state.bake_recipes
    return sorted(
        app.name for app in state.installed_apps.values() if app.base_app_id == app_id and (baked or app.id in state.bake_recipes)
    )


def ensure_template(app: InstalledApp) -> bool:
    """Make a baked application's home template on this node when it has none, and say whether it is there.

    The template of a baked application is the empty one it was made with,
    on the node that made it; any node can stand it up again. A laboratory
    meta-app's template holds what an administrator put there and is not
    made up.
    """
    if app.id not in state.bake_recipes or not app.home_template_name:
        return False
    try:
        template = safe_join(settings.home_templates_path, app.home_template_name)
    except ValueError:
        return False
    if not os.path.isdir(template):
        os.makedirs(os.path.join(template, "Desktop", "files"), exist_ok=True, mode=0o755)
        os.chmod(template, 0o700)
    return True


def status_of(app_id: str) -> dict[str, Any]:
    """Return this node's state of a recipe's image, loading it on first use."""
    if app_id not in state.bake_status:
        try:
            stored = persistence.read_yaml(_state_path(f"{app_id}.yml"), {}) or {}
        except (yaml.YAMLError, OSError, ValueError):
            stored = {}
        status = dict({"state": "pending", "image": "", "key": "", "base": "", "built_at": 0.0, "error": ""}, **stored)
        if status["state"] == "building":
            status["state"] = "ready" if status["image"] else "pending"
        state.bake_status[app_id] = status
    return state.bake_status[app_id]


def _keep_status(app_id: str) -> None:
    """Write this node's state of a recipe's image."""
    try:
        persistence.write_yaml_sync(_state_path(f"{app_id}.yml"), state.bake_status[app_id])
    except (OSError, ValueError) as exc:
        logger.warning("Could not keep the Bakery state of '%s': %s", app_id, exc)


def save_file(name: str, data: bytes) -> BakeFile:
    """Keep an uploaded file for recipes to copy into their images.

    Raises:
        ValueError: For a name no build context can hold, or a file too large.
    """
    name = os.path.basename(name.strip())
    if not _FILE_NAME.fullmatch(name) or name in (".", ".."):
        raise ValueError("A file's name is at most 128 characters, with no slash.")
    if len(data) > MAX_FILE_BYTES:
        raise ValueError(f"A recipe file is at most {MAX_FILE_BYTES // (1024 * 1024)} MiB; download larger ones in a step.")
    digest = hashlib.sha256(data).hexdigest()
    path = _file_path(digest)
    if not persistence.exists(path):
        persistence.write_bytes(path, data)
    return BakeFile(name=name, hash=digest, size=len(data))


def _prune_files() -> None:
    """Remove the files this node has seen no recipe name for `UNUSED_FILE_SECONDS`.

    A file uploaded for a draft, on any node, is in the store before the
    recipe that names it, so a file is only dropped once it has stayed
    unnamed that long. A node that may not write the shared records leaves
    them alone.
    """
    from . import cluster

    if cluster.is_clustered() and "frontend" not in cluster.roles():
        return
    wanted = {item.hash for recipe in state.bake_recipes.values() for item in recipe.files}
    folder = os.path.join(_dir(), FILES_DIR)
    present = {name for name in persistence.list_names(folder) if _SHA256.fullmatch(name)}
    now = time.time()
    for name in [name for name in _unused if name not in present or name in wanted]:
        del _unused[name]
    for name in present - wanted:
        if now - _unused.setdefault(name, now) >= UNUSED_FILE_SECONDS:
            persistence.remove(os.path.join(folder, name))
            del _unused[name]


def validate(recipe: BakeRecipe) -> None:
    """Check what a recipe names before it is kept.

    Raises:
        RecipeError: For an unknown application, a file that was never uploaded, or text too long.
    """
    if recipe.app_id not in state.installed_apps:
        raise RecipeError("The recipe's application is not installed.")
    if recipe.family and recipe.family not in FAMILIES:
        raise RecipeError(f"'{recipe.family}' is not a distribution family: {', '.join(FAMILIES)}.")
    if len(recipe.dockerfile.encode()) > MAX_DOCKERFILE_BYTES:
        raise RecipeError("The Dockerfile is too long.")
    if len(recipe.files) > MAX_FILES:
        raise RecipeError(f"A recipe holds at most {MAX_FILES} files.")
    names: set[str] = set()
    for item in recipe.files:
        if not _FILE_NAME.fullmatch(item.name) or item.name in (".", "..") or item.name in names:
            raise RecipeError(f"'{item.name}' is not a file name, or is there twice.")
        names.add(item.name)
        try:
            known = persistence.exists(_file_path(item.hash))
        except ValueError as exc:
            raise RecipeError(str(exc)) from exc
        if not known:
            raise RecipeError(f"The file '{item.name}' is not on the server; upload it again.")


# --- Rendering -----------------------------------------------------------------


def _banner(text: str) -> str:
    """Return the `echo` line a step announces itself with in the build log."""
    return f'echo "**** {re.sub(r"[^A-Za-z0-9 ._:/@+,()-]", "", text).strip() or "step"} ****"'


def _run(commands: list[str]) -> list[str]:
    """Return a `RUN` instruction of commands joined by `&&`, one per line.

    A command may hold several lines, each already ending as it has to.
    """
    lines = ["RUN \\"]
    for index, command in enumerate(commands):
        last = index == len(commands) - 1
        lines.append(f"  {command}" + ("" if last else " && \\"))
    return lines


def _listed(head: str, items: list[str]) -> str:
    """Return a command with its arguments one per line, as the linuxserver images write them."""
    return " \\\n    ".join([head, *items])


def _remove(paths: tuple[str, ...] | list[str]) -> str:
    return _listed("rm -rf", list(paths))


def _copy(instruction: str, sources: list[str], dest: str) -> str:
    """Return a `COPY` or `ADD` line, in JSON form where a path holds more than plain characters."""
    parts = [*sources, dest]
    if all(_PLAIN_PATH.fullmatch(part) for part in parts):
        return f"{instruction} {' '.join(parts)}"
    return f"{instruction} {json.dumps(parts)}"


def _dest(step: BakeStep, folder: bool = False) -> str:
    """Return a step's destination, an absolute path inside the image.

    Raises:
        StepError: When it is not one.
    """
    dest = step.dest.strip()
    if not dest.startswith("/") or "\n" in dest or "\x00" in dest:
        raise StepError("The destination is an absolute path inside the image, such as /usr/local/bin/.")
    if folder and not dest.endswith("/"):
        dest += "/"
    return dest


def _context_files(step: BakeStep, recipe: BakeRecipe) -> list[str]:
    """Return a step's files as paths in the build context.

    Raises:
        StepError: When the step names none, or one the recipe does not hold.
    """
    known = {item.name for item in recipe.files}
    if not step.files:
        raise StepError("Add at least one file.")
    for name in step.files:
        if name not in known:
            raise StepError(f"The file '{name}' is not among the recipe's files.")
    return [f"{CONTEXT_FILES}/{name}" for name in step.files]


def _family(family: str, what: str) -> Family:
    """Return the family a step renders for.

    Raises:
        StepError: When the base image's distribution is not one of `FAMILIES`.
    """
    found = FAMILIES.get(family)
    if found is None:
        raise StepError(
            f"The distribution of the base image is not known, so {what} has no command to render. "
            "Choose the distribution family, or write the commands in a Run step."
        )
    return found


def _install(family: Family, command: tuple[str, ...], items: list[str]) -> str:
    """Return an install command with its packages one per line."""
    return " \\\n  ".join([*command[:-1], _listed(command[-1], items)])


def _cleanup(family: Family, extra: tuple[str, ...] = ()) -> list[str]:
    return [_banner("cleanup"), *family.cleanup, _remove([*extra, *family.leftovers])]


def _render_packages(step: BakeStep, family: str, recipe: BakeRecipe, distro: str = "") -> list[str]:
    packages = _packages_of(step)
    if not packages:
        raise StepError("Add at least one package.")
    found = _family(family, "a package install")
    return _run(
        [
            _banner(step.note or "install packages"),
            *found.refresh,
            _install(found, found.install, packages),
            *_cleanup(found),
        ]
    )


def _render_files(step: BakeStep, family: str, recipe: BakeRecipe, distro: str = "") -> list[str]:
    sources = _context_files(step, recipe)
    several = len(sources) > 1 or step.extract
    dest = _dest(step, folder=several)
    lines = [f"# {step.note}"] if step.note else []
    if step.extract:
        return lines + [_copy("ADD", [source], dest) for source in sources]
    lines.append(_copy("COPY", sources, dest))
    if step.executable:
        placed = [dest + name for name in step.files] if dest.endswith("/") else [dest]
        lines.extend(_run([_listed("chmod +x", [shlex.quote(path) for path in placed])]))
    return lines


def _render_download(step: BakeStep, family: str, recipe: BakeRecipe, distro: str = "") -> list[str]:
    url = step.url.strip()
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.netloc or any(c.isspace() for c in url):
        raise StepError("The address is an http or https URL.")
    checksum = step.sha256.strip().lower()
    if checksum and not _SHA256.fullmatch(checksum):
        raise StepError("The checksum is a SHA-256: 64 hexadecimal characters.")
    name = os.path.basename(parts.path) or "download"
    if not _PLAIN_PATH.fullmatch(name):
        name = "download"
    if step.action == "save":
        target = _dest(step)
        if target.endswith("/"):
            target += name
        folder = os.path.dirname(target) or "/"
    elif step.action == "appimage":
        target = "/tmp/app.AppImage"
        folder = _dest(step).rstrip("/")
        if folder in ("", "/config") or folder.count("/") < 2:
            raise StepError("An AppImage is unpacked into a folder of its own, such as /opt/application.")
    else:
        target = f"/tmp/{name}"
        folder = _dest(step, folder=True) if step.action == "extract" else ""
    quoted = shlex.quote(target)
    commands = [_banner(step.note or f"download {name}")]
    if folder and step.action != "appimage":
        commands.append(f"mkdir -p {shlex.quote(folder)}")
    commands.append(_listed("curl -fsSL -o", [quoted, shlex.quote(url)]))
    if checksum:
        commands.append(f'echo "{checksum}  {target}" | sha256sum -c -')
    if step.action == "extract":
        commands.append(_listed("tar xf", [quoted, f"-C {shlex.quote(folder)}"]))
        commands.append(_remove(["/tmp/*"]))
    elif step.action == "appimage":
        place = shlex.quote(folder)
        commands.extend(
            [
                f"chmod +x {quoted}",
                "cd /tmp",
                f"{quoted} --appimage-extract >/dev/null",
                f"mkdir -p {shlex.quote(os.path.dirname(folder))}",
                f"rm -rf {place}",
                f"mv squashfs-root {place}",
                f"chmod -R a+rX {place}",
                _remove(["/tmp/*"]),
            ]
        )
    elif step.action == "install":
        found = _family(family, "a package file install")
        commands.extend([*found.refresh, _install(found, found.install_file, [quoted]), *_cleanup(found)])
    elif step.executable:
        commands.append(f"chmod +x {quoted}")
    return _run(commands)


def _render_certificates(step: BakeStep, family: str, recipe: BakeRecipe, distro: str = "") -> list[str]:
    sources = _context_files(step, recipe)
    found = _family(family, "trusting a certificate")
    lines = [f"# {step.note}"] if step.note else []
    for source, name in zip(sources, step.files, strict=True):
        lines.append(_copy("COPY", [source], f"{found.cert_dir}/{os.path.splitext(name)[0]}.crt"))
    return lines + _run([_banner("trust certificates"), found.cert_command])


def _option(step: BakeStep, name: str, limit: int = 512) -> str:
    """Return a text option of a step, trimmed.

    Raises:
        StepError: For text of several lines, or longer than `limit`.
    """
    value = step.options.get(name, "")
    value = value.strip() if isinstance(value, str) else ""
    if len(value) > limit or any(char in value for char in "\n\r\x00"):
        raise StepError("A field is one line of text.")
    return value


def _flag(step: BakeStep, name: str, default: bool = False) -> bool:
    value = step.options.get(name, default)
    return value if isinstance(value, bool) else default


def _items(step: BakeStep, name: str) -> list[str]:
    """Return a list option of a step, its entries trimmed, the empty and the repeated ones dropped."""
    value = step.options.get(name, [])
    found: list[str] = []
    for item in value if isinstance(value, list) else []:
        item = item.strip()
        if any(char in item for char in "\n\r\x00"):
            raise StepError("A list entry is one line of text.")
        if item and item not in found:
            found.append(item)
    return found


def _web_address(value: str, what: str) -> str:
    """Return an `http` or `https` address as given.

    Raises:
        StepError: For anything else.
    """
    parts = urlsplit(value)
    if parts.scheme not in ("http", "https") or not parts.netloc or any(char.isspace() for char in value):
        raise StepError(f"{what} is an http or https URL.")
    return value


def _slug(text: str, fallback: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:48] or fallback


def _write(lines: list[str], path: str) -> str:
    """Return the command that writes lines of text to a file of the image."""
    return _listed("printf '%s\\n'", [shlex.quote(line) for line in lines]) + f" \\\n    > {shlex.quote(path)}"


def _script_lines(step: BakeStep) -> list[str]:
    """Return a step's script as the lines of a file.

    Raises:
        StepError: For an empty script.
    """
    lines = step.script.replace("\r\n", "\n").strip("\n").split("\n")
    if not any(line.strip() for line in lines):
        raise StepError("Write at least one command.")
    return [line.rstrip() for line in lines]


def _env_lines(pairs: list[tuple[str, str]]) -> str:
    """Return one `ENV` instruction of variables, their values quoted."""
    quoted = []
    for name, value in pairs:
        value = value.replace("\\", "\\\\").replace('"', '\\"').replace("$", "\\$")
        quoted.append(f'{name}="{value}"')
    return " \\\n    ".join([f"ENV {quoted[0]}", *quoted[1:]])


def _menu_item(label: str, command: str, icon: str = "") -> str:
    """Return the command that lists a program in the right-click menu the image's sessions start with."""
    attributes = f"label={quoteattr(label)}" + (f" icon={quoteattr(icon)}" if icon else "")
    item = f'<item {attributes}><action name="Execute"><command>{xml_escape(command)}</command></action></item>'
    script = "/<\\/menu>/i " + item.replace("\\", "\\\\")
    return (
        'for menu in /defaults/menu.xml /defaults/menu_wayland.xml; do '
        f'if [ -f "$menu" ]; then sed -i {shlex.quote(script)} "$menu"; fi; done'
    )


def _packages_of(step: BakeStep) -> list[str]:
    """Return a step's package names, sorted.

    Raises:
        StepError: For a name no package manager takes.
    """
    packages = sorted({name.strip() for name in step.packages if name.strip()})
    for name in packages:
        if not _PACKAGE.fullmatch(name):
            raise StepError(f"'{name}' is not a package name.")
    return packages


def _render_fonts(step: BakeStep, family: str, recipe: BakeRecipe, distro: str = "") -> list[str]:
    chosen = _items(step, "packs")
    if not chosen and not step.files:
        raise StepError("Choose a set of fonts, or upload font files.")
    lines = [f"# {step.note}"] if step.note else []
    if step.files:
        lines.append(_copy("COPY", _context_files(step, recipe), "/usr/share/fonts/sealskin/"))
    commands = [_banner(step.note or "install fonts")]
    if chosen:
        found = _family(family, "a font install")
        packages: list[str] = []
        before: list[str] = []
        after: list[str] = []
        reinstall = False
        for name in chosen:
            pack = bakekits.FONT_PACKS.get(name)
            if pack is None or family not in pack.packages:
                raise StepError(f"The fonts '{name}' are not packaged for this distribution.")
            if pack.eula and not _flag(step, "eula"):
                raise StepError("These fonts come under a license of their own; accept it in the step to install them.")
            packages.extend(pack.packages[family])
            before.extend(pack.before.get(family, ()))
            after.extend(pack.after.get(family, ()))
            reinstall = reinstall or pack.reinstall
        install = found.install
        if reinstall and family == "debian":
            install = (*install[:-1], install[-1] + " --reinstall")
        commands.extend([*before, *found.refresh, _install(found, install, sorted(set(packages))), *after])
    commands.append("if command -v fc-cache >/dev/null; then fc-cache -f; fi")
    if chosen:
        commands.extend(_cleanup(FAMILIES[family]))
    return lines + _run(commands)


def _render_env(step: BakeStep, family: str, recipe: BakeRecipe, distro: str = "") -> list[str]:
    if not step.env:
        raise StepError("Add at least one variable.")
    for item in step.env:
        if not _ENV_NAME.fullmatch(item.name):
            raise StepError(f"'{item.name}' is not a variable name.")
        if "\n" in item.value:
            raise StepError(f"The value of {item.name} is one line.")
    lines = [f"# {step.note}"] if step.note else []
    return lines + [_env_lines([(item.name, item.value) for item in step.env])]


def _render_proxy(step: BakeStep, family: str, recipe: BakeRecipe, distro: str = "") -> list[str]:
    proxy = _option(step, "proxy")
    pairs: list[tuple[str, str]] = []
    if proxy:
        _web_address(proxy, "The proxy")
        # Selkies and the session's own services answer on the loopback, which no proxy reaches.
        skipped = ",".join(dict.fromkeys(["localhost", "127.0.0.1", *re.split(r"[\s,]+", _option(step, "no_proxy"))]))
        skipped = skipped.strip(",")
        for name in ("http_proxy", "https_proxy"):
            pairs.extend([(name, proxy), (name.upper(), proxy)])
        pairs.extend([("no_proxy", skipped), ("NO_PROXY", skipped)])
    for option, name, what in (("pip", "PIP_INDEX_URL", "The Python index"), ("npm", "NPM_CONFIG_REGISTRY", "The npm registry")):
        value = _option(step, option)
        if value:
            pairs.append((name, _web_address(value, what)))
    if not pairs:
        raise StepError("Give the proxy's address, or a mirror's.")
    lines = [f"# {step.note}"] if step.note else []
    return lines + [_env_lines(pairs)]


def _repository(repository: bakekits.Repository, name: str) -> list[str]:
    """Return the commands that add a vendor's APT repository under a name."""
    commands = [
        f"mkdir -p {os.path.dirname(repository.key_file)}",
        _listed("curl -fsSL -o", [repository.key_file, shlex.quote(repository.key_url)]),
        f"echo {shlex.quote(repository.source.format(key=repository.key_file))} > /etc/apt/sources.list.d/{name}.list",
    ]
    if repository.pin:
        commands.append(
            _write(["Package: *", f"Pin: origin {repository.pin}", "Pin-Priority: 1000"], f"/etc/apt/preferences.d/{name}")
        )
    return commands


def _archives(found: Family, archives: list[str]) -> list[str]:
    """Return the commands that add personal package archives to an Ubuntu image."""
    return [
        *found.refresh,
        _install(found, found.install, ["software-properties-common"]),
        *[f"add-apt-repository -y ppa:{archive}" for archive in archives],
    ]


def _render_ppa(step: BakeStep, family: str, recipe: BakeRecipe, distro: str = "") -> list[str]:
    if distro not in bakekits.PPA_DISTROS:
        raise StepError("A personal package archive installs on an Ubuntu image only.")
    archives = [item.removeprefix("ppa:") for item in _items(step, "ppas")]
    if not archives:
        raise StepError("Name at least one archive, as owner/name.")
    for archive in archives:
        if not _PPA.fullmatch(archive):
            raise StepError(f"'{archive}' is not an archive: write it as owner/name.")
    packages = _packages_of(step)
    if not packages:
        raise StepError("Add at least one package to install from the archive.")
    found = FAMILIES["debian"]
    return _run(
        [
            _banner(step.note or "install from a package archive"),
            *_archives(found, archives),
            *found.refresh,
            _install(found, found.install, packages),
            *_cleanup(found),
        ]
    )


def _render_app(step: BakeStep, family: str, recipe: BakeRecipe, distro: str = "") -> list[str]:
    app = bakekits.APPS.get(_option(step, "app"))
    if app is None:
        raise StepError("Choose an application.")
    found = _family(family, "an application install")
    if not app.offered(family, distro):
        raise StepError(f"{app.name} is not offered on this distribution.")
    commands = [_banner(step.note or f"install {app.name}")]
    if family == "debian" and distro in app.repository:
        commands.extend(_repository(app.repository[distro], _slug(app.name, "vendor")))
    elif family == "debian" and distro in app.ppa:
        commands.extend(_archives(found, [app.ppa[distro]]))
    # The entries the install adds are found by what was there before it, whatever the vendor names them.
    commands.append("ls /usr/share/applications > /tmp/entries 2>/dev/null || true")
    commands.extend([*found.refresh, _install(found, found.install, list(app.packages[family]))])
    command = app.commands.get(family, app.command)
    default = app.browser and _flag(step, "default", True)
    if app.sandbox or default:
        commands.append(
            'ENTRIES=$(cd /usr/share/applications && for entry in *.desktop; do grep -qxF "${entry}" /tmp/entries || '
            f"grep -lE '^Exec=[^ ]*{command}' \"${{entry}}\"; done || true)"
        )
    if app.sandbox:
        # A browser engine's own sandbox needs what a container's seccomp profile withholds.
        commands.append(
            'for entry in ${ENTRIES}; do sed -i -E \'s|^(Exec=[^ ]+)|\\1 --no-sandbox|\' "/usr/share/applications/${entry}"; done'
        )
        command += " --no-sandbox"
    if default:
        commands.extend(
            [
                "mkdir -p /etc/xdg",
                'if [ -n "${ENTRIES}" ]; then '
                "printf '[Default Applications]\\nx-scheme-handler/http=%s\\nx-scheme-handler/https=%s\\ntext/html=%s\\n' "
                "$(for kind in 1 2 3; do echo ${ENTRIES} | cut -d' ' -f1; done) > /etc/xdg/mimeapps.list; fi",
            ]
        )
    if _flag(step, "menu", True):
        commands.append(_menu_item(app.name, command))
    return _run([*commands, *_cleanup(found)])


def _render_desktop(step: BakeStep, family: str, recipe: BakeRecipe, distro: str = "") -> list[str]:
    name = _option(step, "name", 80)
    command = _option(step, "command")
    if not name or not command:
        raise StepError("A desktop entry has a name and the command it starts.")
    category = _option(step, "category") or bakekits.DESKTOP_CATEGORIES[0]
    if category not in bakekits.DESKTOP_CATEGORIES:
        raise StepError(f"'{category}' is not a menu category.")
    slug = _slug(name, step.id)
    lines = [f"# {step.note}"] if step.note else []
    icon = _option(step, "icon")
    if step.files:
        source = _context_files(step, recipe)[0]
        icon = f"/usr/share/pixmaps/sealskin-{slug}{os.path.splitext(step.files[0])[1].lower()}"
        lines.append(_copy("COPY", [source], icon))
    if _flag(step, "sandbox"):
        command += " --no-sandbox"
    entry = ["[Desktop Entry]", "Type=Application", f"Name={name}", f"Exec={command}"]
    if icon:
        entry.append(f"Icon={icon}")
    entry.extend([f"Categories={category};", f"Terminal={'true' if _flag(step, 'terminal') else 'false'}"])
    commands = [
        _banner(step.note or f"desktop entry for {name}"),
        "mkdir -p /usr/share/applications",
        _write(entry, f"/usr/share/applications/sealskin-{slug}.desktop"),
    ]
    if _flag(step, "menu", True):
        commands.append(_menu_item(name, command, icon if icon.startswith("/") else ""))
    return lines + _run(commands)


def _render_docker(step: BakeStep, family: str, recipe: BakeRecipe, distro: str = "") -> list[str]:
    version = _option(step, "version")
    if version and not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise StepError("A Docker version is three numbers, such as 28.5.1; leave it empty for the latest.")
    static = "https://download.docker.com/linux/static/stable/${ARCH}"
    latest = (
        f'$(curl -fsSL "{static}/" | grep -oE \'docker-[0-9]+\\.[0-9]+\\.[0-9]+\\.tgz\' '
        "| sort -uV | tail -n1 | sed 's/^docker-//; s/\\.tgz$//')"
    )
    plugins = "/usr/local/lib/docker/cli-plugins"
    commands = [
        _banner(step.note or "install docker"),
        "ARCH=$(uname -m)",
        f"DOCKER_RELEASE={version or latest}",
        'echo "docker ${DOCKER_RELEASE}"',
        f'curl -fsSL --retry 5 "{static}/docker-${{DOCKER_RELEASE}}.tgz" \\\n    | tar -xzf - -C /usr/local/bin --strip-components=1',
    ]
    wanted = [name for name in ("buildx", "compose") if _flag(step, name, True)]
    if wanted:
        commands.append(f"mkdir -p {plugins}")
    if "buildx" in wanted:
        commands.extend(
            [
                'case "${ARCH}" in aarch64) GOARCH=arm64 ;; *) GOARCH=amd64 ;; esac',
                "BUILDX_RELEASE=$(curl -fsSLI -o /dev/null -w '%{url_effective}' https://github.com/docker/buildx/releases/latest | sed 's|.*/||')",
                _listed(
                    "curl -fsSL --retry 5 -o",
                    [
                        f"{plugins}/docker-buildx",
                        '"https://github.com/docker/buildx/releases/download/${BUILDX_RELEASE}/buildx-${BUILDX_RELEASE}.linux-${GOARCH}"',
                    ],
                ),
            ]
        )
    if "compose" in wanted:
        commands.append(
            _listed(
                "curl -fsSL --retry 5 -o",
                [
                    f"{plugins}/docker-compose",
                    '"https://github.com/docker/compose/releases/latest/download/docker-compose-linux-${ARCH}"',
                ],
            )
        )
    if wanted:
        commands.append(f"chmod 755 {plugins}/*")
    commands.extend(["(getent group docker >/dev/null || groupadd docker)", "usermod -aG docker abc"])
    return _run(commands)


def _render_devkit(step: BakeStep, family: str, recipe: BakeRecipe, distro: str = "") -> list[str]:
    name = _option(step, "kit")
    kit = bakekits.DEV_KITS.get(name)
    if kit is None:
        raise StepError("Choose a language.")
    found = _family(family, "a toolchain install")
    if family not in kit.packages:
        raise StepError("This toolchain is not packaged for the distribution.")
    wanted = [item.strip() for item in step.packages if item.strip()]
    if wanted and not kit.install:
        raise StepError("This toolchain installs no packages of its own.")
    for item in wanted:
        if not re.fullmatch(kit.pattern, item):
            raise StepError(f"'{item}' is not a package name.")
    if name == "go":
        wanted = [item if "@" in item else f"{item}@latest" for item in wanted]
    toolchain = [*found.refresh, _install(found, found.install, list(kit.packages[family]))]
    if family in kit.completes:
        program, extra = kit.completes[family]
        toolchain.append(f"if ! command -v {program} >/dev/null; then \\\n  " + _install(found, found.install, list(extra)) + "; \\\n  fi")
    commands = [_banner(step.note or f"install {name}"), *toolchain]
    if wanted:
        commands.extend([*kit.before, _listed(kit.install, [shlex.quote(item) for item in wanted])])
    commands.extend(_cleanup(found, kit.leftovers if wanted else ()))
    lines = _run(commands)
    if wanted and kit.env:
        lines.append(" \\\n    ".join([f'ENV {kit.env[0][0]}="{kit.env[0][1]}"', *[f'{key}="{value}"' for key, value in kit.env[1:]]]))
    return lines


#: The files a session's start command is copied from on the first start of a home directory, per display server.
AUTOSTART_FILES = ("/defaults/autostart", "/defaults/autostart_wayland")


def _render_launch(step: BakeStep, family: str, recipe: BakeRecipe, distro: str = "") -> list[str]:
    lines = _script_lines(step)
    files = " ".join(AUTOSTART_FILES)
    commands = [_banner(step.note or "launch command"), "mkdir -p /defaults", _write(lines, "/tmp/autostart")]
    if _option(step, "placement") == "before":
        commands.append(
            f'for file in {files}; do if [ -f "$file" ]; then cat /tmp/autostart "$file" > /tmp/autostart.new && '
            'cat /tmp/autostart.new > "$file"; else cp /tmp/autostart "$file"; fi; done'
        )
    else:
        commands.append(f'for file in {files}; do cp /tmp/autostart "$file"; done')
    commands.append("rm -f /tmp/autostart /tmp/autostart.new")
    return _run(commands)


def _render_startup(step: BakeStep, family: str, recipe: BakeRecipe, distro: str = "") -> list[str]:
    lines = _script_lines(step)
    if not lines[0].startswith("#!"):
        lines.insert(0, "#!/usr/bin/with-contenv bash")
    service = _option(step, "mode") == "service"
    folder = "/custom-services.d" if service else "/custom-cont-init.d"
    path = f"{folder}/{_slug(_option(step, 'name', 48) or step.note, f'sealskin-{step.id}')}"
    return _run(
        [
            _banner(step.note or ("service" if service else "startup script")),
            f"mkdir -p {folder}",
            _write(lines, path),
            f"chmod 755 {path}",
        ]
    )


def join_script(script: str) -> list[str]:
    """Return a shell script's lines as the commands of one `RUN`.

    Blank lines and comments go. A line the shell is not done with (one that
    ends in a backslash, an operator, or the opening word of a block) runs into the
    next; a line before the closing word of a block ends in `;`.
    """
    lines = [line.rstrip() for line in script.replace("\r\n", "\n").split("\n")]
    lines = [line for line in lines if line.strip() and not line.lstrip().startswith("#")]
    commands: list[str] = []
    current = ""
    for index, line in enumerate(lines):
        text = line.strip() if not current else line
        following = lines[index + 1].split()[0] if index + 1 < len(lines) else ""
        words = text.split()
        if text.endswith("\\"):
            current += text + "\n  "
        elif text.endswith(_OPEN_ENDINGS[:4]) or (words and words[-1] in _OPEN_ENDINGS[4:]):
            current += text + " \\\n  "
        elif following in _CLOSERS or following.startswith(("fi;", "done;", "esac;", "}")):
            current += text + "; \\\n  "
        else:
            commands.append(current + text)
            current = ""
    if current:
        commands.append(current.rstrip(" \\\n"))
    return commands


def _render_run(step: BakeStep, family: str, recipe: BakeRecipe, distro: str = "") -> list[str]:
    commands = join_script(step.script)
    if not commands:
        raise StepError("Write at least one command.")
    return _run([_banner(step.note or "run commands"), *commands])


def _render_raw(step: BakeStep, family: str, recipe: BakeRecipe, distro: str = "") -> list[str]:
    text = step.text.replace("\r\n", "\n").strip("\n")
    if not text.strip():
        raise StepError("Write at least one instruction.")
    if any(keyword == "FROM" for _line, keyword, _args in instructions(text)):
        raise StepError("A guided recipe has one FROM line, the application's image. Stages are written in developer mode.")
    return text.split("\n")


_RENDERERS = {
    "packages": _render_packages,
    "app": _render_app,
    "ppa": _render_ppa,
    "download": _render_download,
    "desktop": _render_desktop,
    "files": _render_files,
    "fonts": _render_fonts,
    "certificates": _render_certificates,
    "env": _render_env,
    "proxy": _render_proxy,
    "docker": _render_docker,
    "devkit": _render_devkit,
    "launch": _render_launch,
    "startup": _render_startup,
    "run": _render_run,
    "raw": _render_raw,
}


def instructions(text: str) -> list[tuple[int, str, str]]:
    """Return a Dockerfile's instructions as `(line, KEYWORD, arguments)`, continuation lines joined."""
    found: list[tuple[int, str, str]] = []
    start = 0
    pending = ""
    for number, raw in enumerate(text.replace("\r\n", "\n").split("\n"), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if not pending:
            start = number
        if line.endswith("\\"):
            pending += line[:-1] + " "
            continue
        keyword, _, arguments = (pending + line).strip().partition(" ")
        found.append((start, keyword.upper(), arguments.strip()))
        pending = ""
    if pending.strip():
        keyword, _, arguments = pending.strip().partition(" ")
        found.append((start, keyword.upper(), arguments.strip()))
    return found


def _problem(level: str, message: str, line: int | None = None, step: str | None = None) -> dict[str, Any]:
    return {"level": level, "message": message, "line": line, "step": step}


def _uses_base(image: str, reference: str) -> bool:
    return reference in (f"${{{BASE_ARG}}}", f"${BASE_ARG}", image)


def lint(text: str, image: str, recipe: BakeRecipe) -> list[dict[str, Any]]:
    """Return what a Dockerfile written by hand would do that a session, or this node's builder, does not take.

    An `error` is a build that cannot start; a `warning` builds.
    """
    problems: list[dict[str, Any]] = []
    known = {f"{CONTEXT_FILES}/{item.name}" for item in recipe.files}
    declared = False
    stages: dict[str, bool] = {}
    final_from_base = False
    seen_from = False
    final: list[tuple[int, str, str]] = []
    for line, keyword, arguments in instructions(text):
        if keyword == "ARG" and not seen_from and arguments.split("=")[0].strip() == BASE_ARG:
            declared = True
        if keyword == "FROM":
            seen_from = True
            words = [word for word in arguments.split() if not word.startswith("--")]
            reference = words[0] if words else ""
            based = _uses_base(image, reference) or stages.get(reference.lower(), False)
            if reference in (f"${{{BASE_ARG}}}", f"${BASE_ARG}") and not declared:
                problems.append(_problem("error", f"FROM names {BASE_ARG} before an ARG {BASE_ARG} line declares it.", line))
            if len(words) >= 3 and words[1].upper() == "AS":
                stages[words[2].lower()] = based
            final_from_base = based
            final = []
            continue
        final.append((line, keyword, arguments))
        flags = [word for word in arguments.split() if word.startswith("--")]
        if keyword in ("RUN", "COPY", "ADD") and (
            any(flag.startswith(("--mount", "--chmod", "--link", "--network", "--security")) for flag in flags)
            or (keyword != "ADD" and re.search(r"<<-?\s*['\"]?[A-Za-z_]", arguments))
        ):
            problems.append(_problem("warning", "This line needs BuildKit; a node builds with Docker's classic builder, which refuses it.", line))
        if keyword in ("COPY", "ADD") and not any(flag.startswith("--from") for flag in flags):
            try:
                parts = json.loads(arguments) if arguments.startswith("[") else [w for w in arguments.split() if not w.startswith("--")]
            except ValueError:
                parts = []
            for source in parts[:-1]:
                if isinstance(source, str) and source.startswith(f"{CONTEXT_FILES}/") and source not in known:
                    problems.append(_problem("error", f"{source} is not among the recipe's files.", line))
    if not seen_from:
        problems.append(_problem("error", f"A Dockerfile starts from an image: ARG {BASE_ARG}, then FROM ${{{BASE_ARG}}}."))
        return problems
    if not final_from_base:
        problems.append(
            _problem("warning", f"The last stage is not built on ${{{BASE_ARG}}}: the image is not a layer on the application's.")
        )
    user = ""
    user_line: int | None = None
    for line, keyword, arguments in final:
        if keyword in ("ENTRYPOINT", "CMD"):
            problems.append(_problem("warning", f"{keyword} replaces the image's init, which starts the desktop.", line))
        elif keyword == "USER":
            user = arguments.split(":")[0].strip()
            user_line = line
        elif keyword in ("COPY", "ADD", "WORKDIR"):
            target = arguments.split()[-1].strip('"],') if arguments else ""
            if target == settings.container_config_path or target.startswith(settings.container_config_path + "/"):
                problems.append(
                    _problem("warning", f"A session mounts its home directory at {settings.container_config_path}, over what the image holds there.", line)
                )
    if user and user not in ("root", "0"):
        problems.append(_problem("warning", "The image's init runs as root; end on USER root.", user_line))
    return problems


@dataclass
class Rendered:
    """A recipe as a Dockerfile.

    Attributes:
        dockerfile: The text a build takes.
        buildargs: The build arguments it takes with it.
        blocks: For a guided recipe, each step's lines: `{"step", "start", "end"}`, one-based.
        problems: What `lint` and the steps found: `{"level", "message", "line", "step"}`.
    """

    dockerfile: str
    buildargs: dict[str, str] = field(default_factory=dict)
    blocks: list[dict[str, Any]] = field(default_factory=list)
    problems: list[dict[str, Any]] = field(default_factory=list)

    @property
    def errors(self) -> list[str]:
        """The messages of the problems that stop a build."""
        return [problem["message"] for problem in self.problems if problem["level"] == "error"]


def render(recipe: BakeRecipe, image: str, family: str, portable: bool = False, distro: str = "") -> Rendered:
    """Return the Dockerfile of a recipe for one base image.

    Args:
        recipe: The recipe.
        image: The reference of the application's image on this node.
        family: The image's distribution family, or `""`.
        portable: Write a guided recipe's `FROM` through `BASE_IMAGE`, as developer mode starts from.
        distro: The `ID` of the image's `os-release`, which the steps of one distribution alone ask for.
    """
    if recipe.mode == "dockerfile":
        text = recipe.dockerfile.replace("\r\n", "\n")
        if not text.endswith("\n"):
            text += "\n"
        return Rendered(dockerfile=text, buildargs={BASE_ARG: image}, problems=lint(text, image, recipe))
    lines = [f"ARG {BASE_ARG}", f"FROM ${{{BASE_ARG}}}"] if portable else [f"FROM {image}"]
    result = Rendered(dockerfile="")
    for step in recipe.steps:
        if not step.enabled:
            continue
        try:
            block = "\n".join(_RENDERERS[step.kind](step, recipe.family or family, recipe, distro)).split("\n")
        except StepError as exc:
            result.problems.append(_problem("error", str(exc), step=step.id))
            continue
        lines.append("")
        result.blocks.append({"step": step.id, "start": len(lines) + 1, "end": len(lines) + len(block)})
        lines.extend(block)
    result.dockerfile = "\n".join(lines) + "\n"
    return result


# --- The base image --------------------------------------------------------------


def _provider() -> Any:
    from .providers import get_provider

    return get_provider()


def available() -> bool:
    """Whether this node's backend builds images."""
    return bool(_provider().bakes)


def _read_member(data: bytes) -> tuple[str, str]:
    """Return the text of the one file an export holds, or the path a symbolic link in its place points to."""
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:*") as archive:
        for member in archive:
            if member.issym():
                return "", member.linkname
            if member.isfile() and member.size <= 65536:
                handle = archive.extractfile(member)
                return (handle.read().decode("utf-8", "replace") if handle else ""), ""
    return "", ""


async def _os_release(image: str) -> dict[str, str]:
    """Read an image's `os-release`, following the link `/etc/os-release` usually is."""
    path = "/etc/os-release"
    for _hop in range(3):
        data = await _provider().export_web_client(image, path)
        text, link = await asyncio.to_thread(_read_member, data)
        if not link:
            return parse_os_release(text)
        path = os.path.normpath(os.path.join(os.path.dirname(path), link))
    return {}


async def base_of(app: InstalledApp, pull: bool = True) -> dict[str, Any]:
    """Return what a recipe of an application builds on.

    Returns:
        `image` (the reference on this node), `image_id`, `family`, `manager`,
        `distribution` (the image's own name for itself), and `distro` (the
        `ID` of its `os-release`); `image_id` is empty when the image is not
        on this node and `pull` is off.

    Raises:
        RecipeError: When the image cannot be pulled.
    """
    provider = _provider()
    name = app.provider_config.image
    info = await provider.get_local_image_info(name)
    if not info and pull:
        try:
            await provider.pull_image(name)
        except Exception as exc:  # noqa: BLE001 - the answer carries the reason
            raise RecipeError(f"The image '{name}' could not be pulled: {exc}") from exc
        info = await provider.get_local_image_info(name)
    answer = {"image": registries.mirror(name), "image_id": info["id"] if info else "", "family": "", "manager": "", "distribution": "", "distro": ""}
    if not info:
        return answer
    if info["id"] not in _releases:
        try:
            _releases[info["id"]] = await _os_release(name)
        except Exception as exc:  # noqa: BLE001 - an image without the file has no family
            logger.warning("Could not read the os-release of '%s': %s", name, exc)
            _releases[info["id"]] = {}
    release = _releases[info["id"]]
    family = family_of(release)
    answer.update(
        family=family,
        manager=FAMILIES[family].manager if family else "",
        distribution=release.get("PRETTY_NAME") or release.get("NAME") or "",
        distro=release.get("ID", "").lower(),
    )
    return answer


# --- Building ----------------------------------------------------------------------


@dataclass
class Plan:
    """A recipe made ready to build for the image a node holds.

    Attributes:
        recipe: The recipe.
        base: What `base_of` says of the application's image.
        rendered: The Dockerfile.
        key: Hash of the base image's id, the Dockerfile, its arguments, and the files.
        tag: The image a build of this plan is.
    """

    recipe: BakeRecipe
    base: dict[str, Any]
    rendered: Rendered
    key: str
    tag: str


def _key(base_id: str, rendered: Rendered, files: list[BakeFile]) -> str:
    digest = hashlib.sha256()
    for part in (base_id, rendered.dockerfile, json.dumps(rendered.buildargs, sort_keys=True)):
        digest.update(part.encode() + b"\0")
    for item in sorted(files, key=lambda f: f.name):
        digest.update(f"{item.name}\0{item.hash}\0".encode())
    return digest.hexdigest()[:16]


async def plan_for(recipe: BakeRecipe, pull: bool = True) -> Plan:
    """Render a recipe against its application's image on this node.

    Raises:
        RecipeError: When the application is gone, its image is not here, or a step cannot be rendered.
    """
    app = state.installed_apps.get(recipe.app_id)
    if app is None:
        raise RecipeError("The recipe's application is not installed.")
    base = await base_of(app, pull)
    if not base["image_id"]:
        raise RecipeError(f"The image '{base['image']}' is not on this node.")
    rendered = render(recipe, base["image"], base["family"], distro=base["distro"])
    key = _key(base["image_id"], rendered, recipe.files)
    return Plan(recipe=recipe, base=base, rendered=rendered, key=key, tag=f"{IMAGE_PREFIX}/{recipe.app_id}:{key}")


@dataclass
class Build:
    """One build on this node.

    Attributes:
        id: What the page that follows it asks by.
        app_id: The application of the recipe.
        tag: The image it makes.
        published: Whether sessions run the image once it built: a saved recipe's build, not a trial of a draft.
        state: `queued`, `building`, `ready`, or `failed`.
        lines: The builder's output.
        error: Why it failed.
    """

    id: str
    app_id: str
    tag: str
    published: bool
    username: str = ""
    state: str = "queued"
    lines: list[str] = field(default_factory=list)
    error: str = ""
    started: float = field(default_factory=time.time)
    finished: float = 0.0
    task: asyncio.Task | None = None

    def log(self, line: str) -> None:
        """Take a line of the builder's output, without its terminal colors; called from the builder's thread."""
        if len(self.lines) < MAX_LOG_LINES:
            self.lines.append(_ANSI.sub("", line))


_builds: dict[str, Build] = {}
#: When this node first saw each stored file that no recipe names.
_unused: dict[str, float] = {}
_releases: dict[str, dict[str, str]] = {}
_indexes: dict[str, dict[str, Any]] = {}
_slot: asyncio.Semaphore | None = None


def _one_at_a_time() -> asyncio.Semaphore:
    global _slot
    if _slot is None:
        _slot = asyncio.Semaphore(1)
    return _slot


def _context(plan: Plan) -> bytes:
    """Return the build context: the Dockerfile and the recipe's files under `files/`.

    Raises:
        RecipeError: When a file is not in the store.
    """
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as archive:

        def add(name: str, data: bytes, mode: int) -> None:
            member = tarfile.TarInfo(name)
            member.size = len(data)
            member.mode = mode
            member.mtime = 0
            archive.addfile(member, io.BytesIO(data))

        add("Dockerfile", plan.rendered.dockerfile.encode(), 0o644)
        for item in plan.recipe.files:
            data = persistence.read_bytes(_file_path(item.hash))
            if data is None:
                raise RecipeError(f"The file '{item.name}' is not in the store; upload it again.")
            add(f"{CONTEXT_FILES}/{item.name}", data, 0o644)
    return buffer.getvalue()


async def _adopt(plan: Plan) -> None:
    """Make a plan's image, which this node holds, the one sessions of its recipe run."""
    info = await _provider().get_local_image_info(plan.tag)
    status = status_of(plan.recipe.app_id)
    status.update(
        state="ready",
        image=plan.tag,
        image_id=info["id"] if info else "",
        key=plan.key,
        base=plan.base["image_id"],
        built_at=time.time(),
        error="",
    )
    _keep_status(plan.recipe.app_id)


async def _exists(tag: str) -> bool:
    return bool(tag) and await _provider().get_local_image_info(tag) is not None


async def _run_build(build: Build, plan: Plan, fresh: bool) -> None:
    """Build a plan's image unless this node holds it, and record how it went."""
    status = status_of(plan.recipe.app_id)
    async with _one_at_a_time():
        build.state = "building"
        if build.published:
            status["state"] = "building"
        try:
            if not fresh and await _exists(plan.tag):
                build.log(f"{plan.tag} is already built.")
            else:
                context = await asyncio.to_thread(_context, plan)
                await _provider().build_image(
                    context,
                    plan.tag,
                    buildargs=plan.rendered.buildargs,
                    labels={BAKE_LABEL: plan.recipe.app_id, NODE_LABEL: state.instance_name},
                    log=build.log,
                    fresh=fresh,
                    idle=settings.bakery_build_idle_seconds,
                )
            build.state = "ready"
            if build.published:
                await _adopt(plan)
            logger.info("Baked %s for '%s'.", plan.tag, plan.recipe.app_id)
        except asyncio.CancelledError:
            build.state, build.error = "failed", "The server stopped."
            raise
        except Exception as exc:  # noqa: BLE001 - the build carries the reason
            build.state, build.error = "failed", str(exc)
            build.log(f"ERROR: {exc}")
            if build.published:
                status.update(state="error", error=str(exc))
                _keep_status(plan.recipe.app_id)
            logger.warning("Baking '%s' failed: %s", plan.recipe.app_id, exc)
        finally:
            build.finished = time.time()
            if build.published:
                await asyncio.to_thread(_keep_log, build)


def _keep_log(build: Build) -> None:
    """Write the output of a saved recipe's build where the next start finds it."""
    try:
        path = _state_path(f"{build.app_id}.log")
        os.makedirs(os.path.dirname(path), exist_ok=True, mode=0o700)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("\n".join(build.lines) + "\n")
    except (OSError, ValueError) as exc:
        logger.warning("Could not keep the build log of '%s': %s", build.app_id, exc)


def last_log(app_id: str) -> list[str]:
    """Return the output of the last build of a saved recipe on this node."""
    running = current_build(app_id)
    if running is not None:
        return list(running.lines)
    try:
        with open(_state_path(f"{app_id}.log"), encoding="utf-8") as handle:
            return handle.read().splitlines()
    except (OSError, ValueError):
        return []


def current_build(app_id: str) -> Build | None:
    """Return the latest build of a saved recipe this node remembers."""
    found = [build for build in _builds.values() if build.app_id == app_id and build.published]
    return max(found, key=lambda build: build.started) if found else None


def get_build(build_id: str) -> Build | None:
    """Return a build by its id."""
    return _builds.get(build_id)


def _forget_builds() -> None:
    now = time.time()
    for build_id in [k for k, b in _builds.items() if b.finished and now - b.finished > KEEP_BUILD_SECONDS]:
        del _builds[build_id]


async def start(recipe: BakeRecipe, published: bool, fresh: bool = False, username: str = "", pull: bool = True) -> Build:
    """Start building a recipe's image on this node, or join the build of it already running.

    Args:
        recipe: The recipe: the saved one, or a draft to try.
        published: Whether sessions take the image up once it built.
        fresh: Build every layer again, as after the packages of the distribution moved.
        username: The administrator who asked, for a trial.
        pull: Pull the application's image when this node has none.

    Raises:
        RecipeError: When the recipe cannot be rendered against the image.
    """
    plan = await plan_for(recipe, pull)
    if plan.rendered.errors:
        raise RecipeError(" ".join(plan.rendered.errors))
    _forget_builds()
    for build in _builds.values():
        if build.tag == plan.tag and not build.finished:
            build.published = build.published or published
            return build
    build = Build(id=str(uuid.uuid4()), app_id=recipe.app_id, tag=plan.tag, published=published, username=username)
    _builds[build.id] = build
    build.task = asyncio.create_task(_run_build(build, plan, fresh))
    return build


def start_quietly(recipe: BakeRecipe, pull: bool = False) -> None:
    """Bake a saved recipe in the background where its image is not built, logging what stops it."""

    async def bake() -> None:
        try:
            plan = await plan_for(recipe, pull)
            if await _exists(plan.tag):
                if status_of(recipe.app_id).get("image") != plan.tag:
                    await _adopt(plan)
                return
            await start(recipe, published=True, pull=pull)
        except RecipeError as exc:
            logger.info("Not baking '%s' now: %s", recipe.app_id, exc)
        except Exception as exc:  # noqa: BLE001 - a launch tries again
            logger.warning("Could not bake '%s': %s", recipe.app_id, exc)

    task = asyncio.create_task(bake())
    _background.add(task)
    task.add_done_callback(_background.discard)


_background: set[asyncio.Task] = set()


def reconcile() -> None:
    """Bake every saved recipe whose image this node holds the base of and has not built.

    Called on the event loop at startup and when the shared record changed.
    """
    if not available():
        return
    for recipe in state.bake_recipes.values():
        if recipe.enabled and recipe.app_id in state.installed_apps:
            start_quietly(recipe)


async def base_pulled(image_name: str) -> None:
    """Bake again the recipes of the applications that run an image this node just pulled."""
    if not state.bake_recipes or not available():
        return
    for recipe in list(state.bake_recipes.values()):
        app = state.installed_apps.get(recipe.app_id)
        if app is not None and recipe.enabled and app.provider_config.image == image_name:
            start_quietly(recipe)


async def image_for(app: InstalledApp) -> str | None:
    """Return the baked image a new session of an application runs, or `None` for the application's own.

    The image of the recipe as it is now where this node built it; else the
    last one that built, while the new one builds in the background; else
    the launch waits for the first build.

    Raises:
        HTTPException: 409 on a backend that builds no images; 500 when no image of the recipe can be had.
    """
    recipe = recipe_for(app)
    if recipe is None or not recipe.enabled:
        return None
    if not available():
        raise HTTPException(status_code=409, detail=f"'{app.name}' runs a baked image, which this server's backend cannot build.")
    status = status_of(recipe.app_id)
    plan: Plan | None = None
    reason = ""
    try:
        plan = await plan_for(recipe)
        if plan.rendered.errors:
            plan, reason = None, " ".join(plan.rendered.errors)
    except RecipeError as exc:
        reason = str(exc)
    if plan and await _exists(plan.tag):
        if status.get("image") != plan.tag:
            await _adopt(plan)
        return plan.tag
    last = str(status.get("image") or "")
    if await _exists(last):
        if plan and status.get("state") != "error":
            start_quietly(recipe)
        return last
    if plan is None:
        raise HTTPException(status_code=500, detail=f"The image of '{app.name}' cannot be baked: {reason}")
    progress.step("baking", app=app.name)
    build = await start(recipe, published=True)
    if build.task is not None:
        await asyncio.shield(build.task)
    if build.state != "ready":
        raise HTTPException(
            status_code=500,
            detail=f"The image of '{app.name}' failed to build. An administrator finds the build log in the Bakery.",
        )
    return build.tag


def image_ids() -> set[str]:
    """Return the ids of the baked images sessions of the saved recipes run on this node."""
    return {str(status_of(app_id).get("image_id") or "") for app_id in state.bake_recipes} - {""}


async def prune() -> None:
    """Remove the baked images no recipe runs and no recent trial made, and the files no recipe names."""
    if not available():
        return
    _forget_builds()
    keep = {build.tag for build in _builds.values()}
    for recipe in state.bake_recipes.values():
        keep.add(str(status_of(recipe.app_id).get("image") or ""))
    provider = _provider()
    try:
        built = await provider.built_images({NODE_LABEL: state.instance_name})
    except Exception as exc:  # noqa: BLE001 - the next round tries again
        logger.warning("Could not list the baked images: %s", exc)
        return
    for tag in built:
        if tag.startswith(IMAGE_PREFIX + "/") and tag not in keep and await provider.remove_image(tag):
            logger.info("Removed the baked image %s, which no recipe runs.", tag)
    for app_id in [k for k in state.bake_status if k not in state.bake_recipes]:
        del state.bake_status[app_id]
        for name in (f"{app_id}.yml", f"{app_id}.log"):
            try:
                persistence.remove(_state_path(name))
            except (OSError, ValueError):
                continue
    try:
        await asyncio.to_thread(_prune_files)
        await asyncio.to_thread(_prune_indexes)
    except Exception as exc:  # noqa: BLE001 - the next round tries again
        logger.warning("Could not prune the Bakery's files: %s", exc)


async def forget(app_id: str) -> None:
    """Drop the recipe of an application that is being removed, if it has one."""
    if state.bake_recipes.pop(app_id, None) is not None:
        await save()


async def stop() -> None:
    """Cancel the running builds; the server's shutdown calls this."""
    pending = [build.task for build in _builds.values() if build.task and not build.task.done()] + list(_background)
    for task in pending:
        task.cancel()
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)


# --- Package search ------------------------------------------------------------------


def parse_index(family: str, output: str) -> list[tuple[str, str]]:
    """Return the `(name, description)` pairs a family's index script printed, sorted by name."""
    found: dict[str, str] = {}
    if family == "arch":
        name = ""
        for line in output.splitlines():
            if line.startswith((" ", "\t")):
                if name and not found.get(name):
                    found[name] = line.strip()
            else:
                name = line.split(" ", 1)[0].rpartition("/")[2]
                if name:
                    found.setdefault(name, "")
        return sorted(found.items())
    for line in output.splitlines():
        name, separator, description = line.partition(" - ")
        name = name.strip()
        if family == "alpine":
            name = re.sub(r"-\d[^-]*-r\d+$", "", name)
        if separator and _PACKAGE.fullmatch(name):
            found.setdefault(name, description.strip())
    return sorted(found.items())


def search_index(entries: list[tuple[str, str]], query: str) -> list[dict[str, str]]:
    """Return the packages matching a query: names starting with it, then holding it, then descriptions."""
    words = query.lower().split()
    if not words:
        return []
    first = words[0]
    ranked: list[tuple[int, str, str]] = []
    for name, description in entries:
        lowered = name.lower()
        text = f"{lowered} {description.lower()}"
        if not all(word in text for word in words):
            continue
        rank = 0 if lowered == first else 1 if lowered.startswith(first) else 2 if first in lowered else 3
        ranked.append((rank, name, description))
    ranked.sort(key=lambda item: (item[0], len(item[1]), item[1]))
    return [{"name": name, "description": description} for _rank, name, description in ranked[:SEARCH_RESULTS]]


def _index_path(key: str) -> str:
    return _state_path(os.path.join("index", re.sub(r"[^A-Za-z0-9]", "_", key) + ".tsv"))


def _load_index(key: str) -> list[tuple[str, str]] | None:
    try:
        with open(_index_path(key), encoding="utf-8") as handle:
            pairs = [line.rstrip("\n").split("\t", 1) for line in handle]
    except (OSError, ValueError):
        return None
    return [(pair[0], pair[1]) for pair in pairs if len(pair) == 2]


def _keep_index(key: str, entries: list[tuple[str, str]]) -> None:
    path = _index_path(key)
    os.makedirs(os.path.dirname(path), exist_ok=True, mode=0o700)
    with open(path, "w", encoding="utf-8") as handle:
        handle.writelines(f"{name}\t{description.replace(chr(9), ' ')}\n" for name, description in entries)


def _prune_indexes() -> None:
    """Remove the package lists of images this node no longer searches."""
    folder = os.path.dirname(_index_path("x"))
    try:
        names = os.listdir(folder)
    except OSError:
        return
    for name in names:
        path = os.path.join(folder, name)
        if time.time() - os.path.getmtime(path) > INDEX_KEEP_SECONDS:
            os.remove(path)


async def _build_index(image: str, key: str, family: str) -> None:
    """Read a family's package list from a throwaway container of an image into `_indexes[key]`."""
    index = _indexes[key]
    try:
        code, output = await _provider().run_once(image, FAMILIES[family].index, INDEX_TIMEOUT)
        entries = parse_index(family, output)
        if not entries:
            raise RuntimeError(f"The image listed no packages (exit {code}).")
        index.update(state="ready", entries=entries)
        await asyncio.to_thread(_keep_index, key, entries)
    except Exception as exc:  # noqa: BLE001 - the answer carries the reason
        index.update(state="error", error=str(exc))
        logger.warning("Could not list the packages of '%s': %s", image, exc)


async def packages(app: InstalledApp, query: str, family: str = "", refresh: bool = False) -> dict[str, Any]:
    """Search the packages an application's image can install.

    The list is read once per image, by the family's own package manager in a
    throwaway container of the image, and kept by this node.

    Returns:
        `state` (`indexing`, `ready`, `error`, or `unknown` for an image of no
        known family), `results`, `total`, and `error`.
    """
    try:
        base = await base_of(app)
    except RecipeError as exc:
        return {"state": "error", "results": [], "total": 0, "error": str(exc)}
    family = family if family in FAMILIES else base["family"]
    if not family or not base["image_id"]:
        return {"state": "unknown", "results": [], "total": 0, "error": ""}
    key = f"{base['image_id']}:{family}"
    index = _indexes.get(key)
    if index is None or (refresh and index["state"] != "indexing"):
        stored = None if refresh else await asyncio.to_thread(_load_index, key)
        if stored:
            index = _indexes[key] = {"state": "ready", "entries": stored, "error": ""}
        else:
            index = _indexes[key] = {"state": "indexing", "entries": [], "error": ""}
            task = asyncio.create_task(_build_index(app.provider_config.image, key, family))
            _background.add(task)
            task.add_done_callback(_background.discard)
    return {
        "state": index["state"],
        "results": search_index(index["entries"], query) if index["state"] == "ready" else [],
        "total": len(index["entries"]),
        "error": index.get("error", ""),
    }


# --- Views ---------------------------------------------------------------------------


def view(recipe: BakeRecipe) -> dict[str, Any]:
    """Return a recipe as the editor takes it, with this node's state of its image."""
    app = state.installed_apps.get(recipe.app_id)
    status = status_of(recipe.app_id)
    build = current_build(recipe.app_id)
    running = build is not None and not build.finished
    return {
        **recipe.model_dump(),
        "app_name": app.name if app else "",
        "app_logo": app.logo if app else "",
        "image": app.provider_config.image if app else "",
        "status": {
            "state": "building" if running else status["state"],
            "image": status["image"],
            "built_at": status["built_at"],
            "error": status["error"],
            "build": build.id if running and build else "",
        },
    }


def build_view(build: Build, after: int = 0) -> dict[str, Any]:
    """Return a build for the page following it: its state and the output past line `after`."""
    after = max(0, after)
    return {
        "id": build.id,
        "app_id": build.app_id,
        "state": build.state,
        "image": build.tag if build.state == "ready" else "",
        "error": build.error,
        "lines": build.lines[after:],
        "next": len(build.lines),
        "elapsed": round((build.finished or time.time()) - build.started, 1),
    }
