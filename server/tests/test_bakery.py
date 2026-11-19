"""The Bakery: rendering recipes, what a hand-written Dockerfile is told, and which image a session runs."""

import asyncio
import io
import os
import tarfile

import pytest
from fastapi import HTTPException

from app import bakekits, bakery, launch
from app.models import BakeRecipe, InstalledApp
from app.settings import settings
from app.state import state

IMAGE = "lscr.io/linuxserver/firefox:latest"


def _app(app_id="firefox", **fields):
    app = InstalledApp(
        id=app_id,
        name=app_id.title(),
        logo="",
        url="",
        source="test",
        source_app_id="firefox",
        provider="docker",
        home_directories=False,
        users=["all"],
        groups=["all"],
        provider_config={
            "image": IMAGE, "port": 3000, "type": "browser", "extensions": [],
            "nvidia_support": False, "dri3_support": False, "url_support": False, "open_support": False,
        },
        app_template="Default",
        **fields,
    )
    state.installed_apps[app.id] = app
    return app


class FakeProvider:
    """A backend that holds images by name and builds by recording the request."""

    bakes = True

    def __init__(self, release="ID=ubuntu\nID_LIKE=debian\nPRETTY_NAME=Ubuntu\n"):
        self.images = {IMAGE: "sha256:base1"}
        self.release = release
        self.built = []
        self.fail = False

    async def get_local_image_info(self, name):
        found = self.images.get(name)
        return {"id": found, "short_id": found[-5:], "digests": []} if found else None

    async def pull_image(self, name):
        raise RuntimeError("offline")

    async def export_web_client(self, _image, path):
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w") as archive:
            member = tarfile.TarInfo("os-release")
            if path == "/etc/os-release":
                member.type, member.linkname = tarfile.SYMTYPE, "../usr/lib/os-release"
                archive.addfile(member)
            else:
                data = self.release.encode()
                member.size = len(data)
                archive.addfile(member, io.BytesIO(data))
        return buffer.getvalue()

    async def build_image(self, context, tag, buildargs, labels, log, fresh=False, idle=900):
        if self.fail:
            raise RuntimeError("E: Unable to locate package nope")
        with tarfile.open(fileobj=io.BytesIO(context)) as archive:
            names = archive.getnames()
            dockerfile = archive.extractfile("Dockerfile").read().decode()
        self.built.append({"tag": tag, "names": names, "dockerfile": dockerfile, "buildargs": buildargs, "labels": labels})
        self.images[tag] = f"sha256:{len(self.built)}"
        log("Successfully built")
        return self.images[tag]

    async def built_images(self, _labels):
        return [tag for tag in self.images if tag.startswith(bakery.IMAGE_PREFIX)]

    async def remove_image(self, reference):
        return self.images.pop(reference, None) is not None


@pytest.fixture
def provider(monkeypatch):
    fake = FakeProvider()
    monkeypatch.setattr(bakery, "_provider", lambda: fake)
    for table in (bakery._builds, bakery._releases, bakery._indexes):
        table.clear()
    return fake


def _packages(app_id="firefox", names=("vim",)):
    return BakeRecipe(app_id=app_id, steps=[{"kind": "packages", "packages": list(names)}])


def test_a_package_step_installs_and_cleans_up_the_way_of_its_distribution():
    recipe = _packages(names=("vim", "git"))
    debian = bakery.render(recipe, IMAGE, "debian").dockerfile
    assert debian.startswith(f"FROM {IMAGE}\n")
    assert "apt-get update && \\" in debian and "apt-get install -y --no-install-recommends \\\n    git \\\n    vim" in debian
    assert "/var/lib/apt/lists/*" in debian
    assert "apk add --no-cache \\\n    git" in bakery.render(recipe, IMAGE, "alpine").dockerfile
    assert "/var/lib/pacman/sync/*" in bakery.render(recipe, IMAGE, "arch").dockerfile
    assert "dnf clean all" in bakery.render(recipe, IMAGE, "fedora").dockerfile


def test_every_step_problem_is_a_sentence_of_the_table():
    import inspect
    import re

    raised = set(re.findall(r'raise StepError\("([a-z_]+)"', inspect.getsource(bakery)))
    assert raised and raised <= set(bakery.STEP_PROBLEMS)
    assert str(bakery.StepError("package_name", "a b")) == "'a b' is not a package name."
    assert all(set(re.findall(r"{(\w*)}", sentence)) <= {"subject"} for sentence in bakery.STEP_PROBLEMS.values())


def test_a_step_that_cannot_render_is_a_problem_of_that_step_alone():
    recipe = BakeRecipe(
        app_id="firefox",
        steps=[
            {"id": "a", "kind": "packages", "packages": ["vim; rm -rf /"]},
            {"id": "b", "kind": "raw", "text": "FROM scratch"},
            {"id": "c", "kind": "files", "files": ["missing"], "dest": "/opt/"},
            {"id": "d", "kind": "env", "env": [{"name": "A", "value": "$HOME"}]},
        ],
    )
    rendered = bakery.render(recipe, IMAGE, "")
    assert [problem["step"] for problem in rendered.problems] == ["a", "b", "c"]
    # Each says what is wrong in a sentence of the table, naming the step's own value where it has one.
    assert [problem["message"] for problem in rendered.problems] == [
        "'vim; rm -rf /' is not a package name.",
        bakery.STEP_PROBLEMS["guided_from"],
        "The file 'missing' is not among the recipe's files.",
    ]
    assert 'ENV A="\\$HOME"' in rendered.dockerfile and "scratch" not in rendered.dockerfile
    assert rendered.blocks == [{"step": "d", "start": 3, "end": 3}]


def _one(kind, family="debian", distro="debian", **fields):
    recipe = BakeRecipe(app_id="firefox", steps=[{"id": "a", "kind": kind, **fields}])
    return bakery.render(recipe, IMAGE, family, distro=distro)


def test_an_application_comes_from_where_its_distribution_gets_it():
    debian = _one("app", options={"app": "chromium"}).dockerfile
    assert "add-apt-repository" not in debian and "grep -lE '^Exec=[^ ]*chromium'" in debian and "--no-sandbox|" in debian
    assert "> /etc/xdg/mimeapps.list" in debian and "<command>chromium --no-sandbox</command>" in debian
    assert "add-apt-repository -y ppa:xtradeb/apps" in _one("app", distro="ubuntu", options={"app": "chromium"}).dockerfile
    fedora = _one("app", family="fedora", distro="fedora", options={"app": "chromium", "menu": False, "default": False}).dockerfile
    assert "Exec=[^ ]*chromium-browser" in fedora and "mimeapps" not in fedora and "menu.xml" not in fedora
    chrome = _one("app", distro="ubuntu", options={"app": "chrome"}).dockerfile
    assert "signed-by=/etc/apt/keyrings/google-chrome.asc" in chrome and chrome.index("sources.list.d") < chrome.index("apt-get update")
    assert "Pin: origin packages.mozilla.org" in _one("app", options={"app": "firefox"}).dockerfile
    assert _one("app", family="alpine", distro="alpine", options={"app": "chrome"}).problems
    assert "chrome" not in bakekits.offers("alpine", "alpine")["apps"] and "firefox" in bakekits.offers("alpine", "alpine")["apps"]


def test_a_personal_package_archive_is_an_ubuntu_step():
    step = {"options": {"ppas": ["ppa:libretro/stable"]}, "packages": ["retroarch"]}
    assert "add-apt-repository -y ppa:libretro/stable" in _one("ppa", distro="ubuntu", **step).dockerfile
    assert _one("ppa", **step).problems and _one("ppa", distro="ubuntu", options={"ppas": ["x; id"]}, packages=["a"]).problems
    assert bakekits.offers("debian", "debian")["hidden"] == ["ppa"] and bakekits.offers("debian", "ubuntu")["hidden"] == []


def test_a_desktop_entry_is_written_from_what_the_administrator_names():
    rendered = _one("desktop", options={"name": "My Tool & Co", "command": "/opt/tool/AppRun", "sandbox": True, "category": "Office"})
    text = rendered.dockerfile
    assert "'Name=My Tool & Co'" in text and "'Exec=/opt/tool/AppRun --no-sandbox'" in text and "'Categories=Office;'" in text
    assert "> /usr/share/applications/sealskin-my-tool-co.desktop" in text and 'label="My Tool &amp; Co"' in text
    assert _one("desktop", options={"name": "x"}).problems and _one("desktop", options={"name": "x", "command": "y", "category": "z"}).problems


def test_an_appimage_is_unpacked_into_a_folder_of_its_own():
    text = _one("download", url="https://example.com/App.AppImage", action="appimage", dest="/opt/app").dockerfile
    assert "/tmp/app.AppImage --appimage-extract" in text and "mv squashfs-root /opt/app" in text
    assert _one("download", url="https://example.com/App.AppImage", action="appimage", dest="/opt").problems


def test_docker_is_put_where_the_image_looks_for_it():
    latest = _one("docker", family="").dockerfile
    assert "tar -xzf - -C /usr/local/bin --strip-components=1" in latest and "sort -uV" in latest
    assert "cli-plugins/docker-buildx" in latest and "cli-plugins/docker-compose" in latest
    pinned = _one("docker", options={"version": "28.5.1", "compose": False, "buildx": False}).dockerfile
    assert "DOCKER_RELEASE=28.5.1 &&" in pinned and "cli-plugins" not in pinned
    assert _one("docker", options={"version": "latest; id"}).problems


def test_fonts_install_the_sets_of_the_distribution_and_the_uploaded_files():
    cjk = _one("fonts", options={"packs": ["cjk", "office"]}).dockerfile
    assert "rm -f /etc/dpkg/dpkg.cfg.d/selkies-fonts" in cjk and "--reinstall \\\n    fonts-crosextra-caladea" in cjk
    assert "--reinstall" not in _one("fonts", family="arch", options={"packs": ["cjk"]}).dockerfile
    assert _one("fonts", options={"packs": ["microsoft"]}).problems
    assert "debconf-set-selections" in _one("fonts", options={"packs": ["microsoft"], "eula": True}).dockerfile
    assert _one("fonts", family="fedora", options={"packs": ["microsoft"], "eula": True}).problems and _one("fonts").problems


def test_a_development_kit_installs_its_packages_outside_the_home_directory():
    python = _one("devkit", options={"kit": "python"}, packages=["requests>=2"]).dockerfile
    assert "/opt/venv/bin/pip install --no-cache-dir \\\n    'requests>=2'" in python and 'ENV PATH="${PATH}:/opt/venv/bin"' in python
    assert "ENV" not in _one("devkit", options={"kit": "python"}).dockerfile
    assert "nodejs && \\\n  if ! command -v npm >/dev/null; then" in _one("devkit", options={"kit": "node"}, packages=["typescript"]).dockerfile
    assert "github.com/junegunn/fzf@latest" in _one("devkit", options={"kit": "go"}, packages=["github.com/junegunn/fzf"]).dockerfile
    assert _one("devkit", options={"kit": "java"}, packages=["x"]).problems and _one("devkit", options={"kit": "python"}, packages=["a; id"]).problems


def test_the_start_of_a_session_and_of_its_container_are_files_of_the_image():
    replaced = _one("launch", script="app --flag\n").dockerfile
    assert "'app --flag'" in replaced and 'do cp /tmp/autostart "$file"; done' in replaced
    assert 'cat /tmp/autostart "$file" > /tmp/autostart.new' in _one("launch", options={"placement": "before"}, script="xterm &").dockerfile
    once = _one("startup", options={"name": "Fix Perms"}, script="chown abc /data").dockerfile
    assert "'#!/usr/bin/with-contenv bash'" in once and "chmod 755 /custom-cont-init.d/fix-perms" in once
    assert "/custom-services.d/a" not in once and "/custom-services.d/sealskin-a" in _one("startup", options={"mode": "service"}, script="#!/bin/sh\nexec sleep 1").dockerfile
    assert _one("launch").problems and _one("startup", script="  \n").problems


def test_a_proxy_keeps_the_loopback_out_of_it():
    text = _one("proxy", options={"proxy": "http://proxy.lan:3128", "no_proxy": "lan, 10.0.0.0/8", "npm": "https://npm.lan"}).dockerfile
    assert 'no_proxy="localhost,127.0.0.1,lan,10.0.0.0/8"' in text and 'HTTPS_PROXY="http://proxy.lan:3128"' in text
    assert 'NPM_CONFIG_REGISTRY="https://npm.lan"' in text
    assert _one("proxy").problems and _one("proxy", options={"proxy": "proxy.lan"}).problems


def test_a_script_becomes_one_run_whatever_its_shell_blocks():
    commands = bakery.join_script("# note\nsed -i \\\n  s/a/b/ /x\nif [ -f /a ]; then\n  rm /a\nfi\necho done\n")
    assert commands == ["sed -i \\\n    s/a/b/ /x", "if [ -f /a ]; then \\\n    rm /a; \\\n  fi", "echo done"]


def test_a_dockerfile_is_built_as_written_and_told_what_a_session_does_not_take():
    text = "ARG BASE_IMAGE\nFROM golang AS build\nRUN go build\nFROM ${BASE_IMAGE}\nCOPY --from=build /x /usr/bin/x\n"
    rendered = bakery.render(BakeRecipe(app_id="firefox", mode="dockerfile", dockerfile=text), IMAGE, "debian")
    assert rendered.dockerfile == text and rendered.buildargs == {"BASE_IMAGE": IMAGE} and rendered.problems == []
    odd = "FROM alpine\nRUN --mount=type=cache,target=/c true\nCOPY files/none /config/x\nUSER abc\nCMD [\"sh\"]\n"
    problems = bakery.lint(odd, IMAGE, BakeRecipe(app_id="firefox"))
    assert [problem["level"] for problem in problems].count("error") == 1
    assert {problem["line"] for problem in problems} == {None, 2, 3, 4, 5}


def test_the_distribution_is_read_from_the_image_through_its_link(provider):
    base = asyncio.run(bakery.base_of(_app()))
    assert (base["family"], base["manager"], base["distribution"], base["image_id"]) == ("debian", "apt", "Ubuntu", "sha256:base1")


async def _settle():
    """Let the background bakes and the builds they started finish."""
    await asyncio.gather(*bakery._background)
    await asyncio.gather(*(build.task for build in bakery._builds.values() if not build.task.done()), return_exceptions=True)


def test_a_launch_waits_for_the_first_bake_and_then_runs_the_last_good_one(provider):
    app = _app()

    async def launches():
        assert await bakery.image_for(app) is None
        state.bake_recipes[app.id] = _packages()
        first = await bakery.image_for(app)
        assert first.startswith(f"{bakery.IMAGE_PREFIX}/firefox:") and provider.built[0]["names"] == ["Dockerfile"]
        assert provider.built[0]["labels"][bakery.BAKE_LABEL] == "firefox"
        assert await bakery.image_for(app) == first and len(provider.built) == 1
        # A recipe that stops building leaves sessions on the image that built.
        state.bake_recipes[app.id] = _packages(names=("nope",))
        provider.fail = True
        assert await bakery.image_for(app) == first
        await _settle()
        assert await bakery.image_for(app) == first

    asyncio.run(launches())
    assert bakery.status_of(app.id)["state"] == "error" and "nope" in bakery.status_of(app.id)["error"]


def test_a_launch_fails_where_no_image_was_ever_baked(provider):
    app = _app()
    state.bake_recipes[app.id] = _packages()
    provider.fail = True
    with pytest.raises(HTTPException) as refused:
        asyncio.run(bakery.image_for(app))
    assert refused.value.status_code == 500


def test_a_pulled_base_is_baked_again_under_a_new_tag(provider):
    app = _app()
    state.bake_recipes[app.id] = _packages()

    async def pull():
        first = await bakery.image_for(app)
        provider.images[IMAGE] = "sha256:base2"
        await bakery.base_pulled(IMAGE)
        await _settle()
        return first, await bakery.image_for(app)

    first, second = asyncio.run(pull())
    assert first != second and len(provider.built) == 2
    assert bakery.status_of(app.id)["image"] == second


def test_a_meta_app_runs_the_bake_of_the_application_it_was_made_from(provider):
    base = _app()
    meta = _app("variant", is_meta_app=True, base_app_id="firefox", home_template_name="meta_variant")
    state.bake_recipes[base.id] = _packages()
    assert bakery.recipe_for(meta) is state.bake_recipes[base.id]
    state.bake_recipes[meta.id] = _packages("variant", names=("git",))
    assert bakery.recipe_for(meta) is state.bake_recipes[meta.id]


def test_a_paused_recipe_and_a_backend_that_builds_nothing(provider):
    app = _app()
    state.bake_recipes[app.id] = _packages().model_copy(update={"enabled": False})
    assert asyncio.run(bakery.image_for(app)) is None
    state.bake_recipes[app.id] = _packages()
    provider.bakes = False
    with pytest.raises(HTTPException) as refused:
        asyncio.run(bakery.image_for(app))
    assert refused.value.status_code == 409


def test_the_recipe_and_its_files_are_shared_records(provider):
    app = _app()
    kept = bakery.save_file("corp.pem", b"certificate")
    recipe = BakeRecipe(app_id=app.id, files=[kept], steps=[{"kind": "certificates", "files": ["corp.pem"]}])
    bakery.validate(recipe)
    state.bake_recipes[app.id] = recipe
    asyncio.run(bakery.save())
    state.bake_recipes.clear()
    bakery.load()
    assert state.bake_recipes[app.id].files[0].hash == kept.hash
    asyncio.run(bakery.image_for(app))
    assert provider.built[0]["names"] == ["Dockerfile", "files/corp.pem"]
    with pytest.raises(bakery.RecipeError):
        bakery.validate(recipe.model_copy(update={"files": [kept.model_copy(update={"hash": "0" * 64})]}))


def test_the_baked_image_is_the_one_a_launch_runs():
    app = _app()
    spec = launch.build_launch_spec(
        app,
        "session",
        base_env={},
        extra_env=None,
        language=None,
        wayland_mode=True,
        gpu_config=None,
        host_mount_path=None,
        shared_files_path=None,
        image=f"{bakery.IMAGE_PREFIX}/firefox:abc",
    )
    assert spec.app_config["provider_config"]["image"] == f"{bakery.IMAGE_PREFIX}/firefox:abc"
    assert app.provider_config.image == IMAGE


def test_package_lists_are_parsed_per_family_and_searched_by_name_first():
    assert bakery.parse_index("alpine", "vim-9.1.0-r0 - Improved vi\n") == [("vim", "Improved vi")]
    assert bakery.parse_index("arch", "extra/vim 9.1-1\n    Vi Improved\n") == [("vim", "Vi Improved")]
    assert bakery.parse_index("debian", "vim - Vi IMproved\nWARNING junk\n") == [("vim", "Vi IMproved")]
    entries = [("neovim", "editor"), ("vim", "editor"), ("nano", "not vim")]
    assert [found["name"] for found in bakery.search_index(entries, "vim")] == ["vim", "neovim", "nano"]


def test_an_application_is_held_by_what_is_built_on_it(provider):
    base = _app()
    baked = _app("variant", is_meta_app=True, base_app_id="firefox", home_template_name="meta_variant")
    _app("labbed", is_meta_app=True, base_app_id="variant", home_template_name="meta_labbed")
    _app("plain", is_meta_app=True, base_app_id="firefox", home_template_name="meta_plain")
    state.bake_recipes[baked.id] = _packages("variant")
    # A baked application holds its base, a meta-app of a baked one holds that; a laboratory's own holds nothing.
    assert bakery.held_by(base.id) == ["Variant"]
    assert bakery.held_by(baked.id) == ["Labbed"]
    assert bakery.held_by("labbed") == []


def test_an_unnamed_file_is_removed_only_after_it_stayed_unnamed(provider):
    app = _app()
    draft = bakery.save_file("draft.txt", b"not saved yet")
    named = bakery.save_file("named.txt", b"in a recipe")
    state.bake_recipes[app.id] = BakeRecipe(app_id=app.id, files=[named])
    folder = os.path.join(settings.bakery_path, bakery.FILES_DIR)
    bakery._unused.clear()
    bakery._prune_files()
    assert sorted(os.listdir(folder)) == sorted([draft.hash, named.hash])
    bakery._unused[draft.hash] -= bakery.UNUSED_FILE_SECONDS
    bakery._prune_files()
    assert os.listdir(folder) == [named.hash]


def test_a_baked_application_gets_its_empty_template_on_any_node(provider):
    baked = _app("variant", is_meta_app=True, base_app_id="firefox", home_template_name="meta_variant")
    labbed = _app("labbed", is_meta_app=True, base_app_id="firefox", home_template_name="meta_labbed")
    state.bake_recipes[baked.id] = _packages("variant")
    assert bakery.ensure_template(baked) and os.path.isdir(os.path.join(settings.home_templates_path, "meta_variant", "Desktop", "files"))
    assert not bakery.ensure_template(labbed) and not os.path.exists(os.path.join(settings.home_templates_path, "meta_labbed"))
