"""Browser policies: the catalogs, what a policy is held to, the files it renders, and a launch's mounts."""

import asyncio
import json
import os

import pytest
from fastapi import HTTPException

from app import browser_policies, launch
from app.models import BrowserPolicy
from app.settings import settings
from app.state import state

from .test_launch import make_app


def _policy(**parts):
    return BrowserPolicy(id="p1", name="Kiosk", **parts)


def _spec(app, tmp_path):
    return launch.build_launch_spec(
        app,
        "sess",
        base_env=launch.session_base_env("sess", "u", "p", None),
        extra_env=None,
        language=None,
        wayland_mode=False,
        gpu_config=None,
        host_mount_path=str(tmp_path),
        shared_files_path=None,
    )


def test_the_catalogs_hold_every_policy_in_the_shape_the_editor_draws():
    kinds = {"boolean", "integer", "number", "string", "array", "object"}

    def walk(schema):
        assert isinstance(schema, dict)
        assert schema.get("type") in kinds | {None}
        if "x-labels" in schema:
            assert len(schema["x-labels"]) == len(schema["enum"])
        for sub in (schema.get("properties") or {}).values():
            walk(sub)
        if "items" in schema:
            walk(schema["items"])

    for engine in browser_policies.ENGINES:
        catalog = browser_policies.catalog(engine)
        assert catalog["revision"] and catalog["policies"]
        groups = {group["id"] for group in catalog["groups"]}
        names = [policy["name"] for policy in catalog["policies"]]
        assert len(names) == len(set(names))
        for policy in catalog["policies"]:
            assert policy["group"] in groups and policy["title"]
            walk(policy["schema"])
        common = sorted(policy["common"] for policy in catalog["policies"] if "common" in policy)
        assert common == list(range(1, len(common) + 1))
    with pytest.raises(KeyError):
        browser_policies.catalog("../chromium")


def test_a_policy_is_held_to_the_catalog_and_an_unknown_name_is_kept():
    browser_policies.validate(
        _policy(
            chromium={"HomepageLocation": "https://example.org", "URLBlocklist": ["*"], "EdgeOnlyPolicy": {"a": 1}},
            chromium_recommended={"ShowHomeButton": True},
            firefox={"Homepage": {"URL": "https://example.org", "StartPage": "homepage"}, "DisableAppUpdate": True},
        )
    )
    for parts in (
        {"chromium": {"HomepageLocation": 3}},
        {"chromium": {"URLBlocklist": ["*", 4]}},
        {"chromium": {"IncognitoModeAvailability": 9}},
        {"chromium": {"CloudManagementEnrollmentToken": "secret"}},
        {"chromium": {"ShowHomeButton": True}, "chromium_recommended": {"ShowHomeButton": False}},
        {"chromium_recommended": {"URLBlocklist": ["*"]}},
        {"firefox": {"Homepage": {"StartPage": "everywhere"}}},
        {"firefox": {"DisableAppUpdate": "yes"}},
    ):
        with pytest.raises(browser_policies.PolicyError):
            browser_policies.validate(_policy(**parts))


def test_each_part_renders_to_the_files_its_browsers_read():
    files = browser_policies.render(_policy(chromium={"ShowHomeButton": True}))
    assert set(files) == {f"{d}/managed/sealskin.json" for _b, d in browser_policies.CHROMIUM_DIRECTORIES}
    assert json.loads(files["/etc/chromium/policies/managed/sealskin.json"]) == {"ShowHomeButton": True}
    files = browser_policies.render(
        _policy(chromium_recommended={"ShowHomeButton": True}, firefox={"DisableAppUpdate": True})
    )
    assert "/etc/opt/chrome/policies/recommended/sealskin.json" in files
    assert not any("/managed/" in path for path in files)
    assert json.loads(files["/etc/firefox/policies/policies.json"]) == {"policies": {"DisableAppUpdate": True}}
    assert browser_policies.render(_policy()) == {}


def test_a_launch_mounts_the_policy_read_only_and_a_running_session_keeps_its_file(tmp_path):
    state.app_templates["Default"] = {"name": "Default", "settings": {}}
    state.browser_policies["p1"] = _policy(chromium={"ShowHomeButton": True})
    spec = _spec(make_app(browser_policy="p1"), tmp_path)
    mounts = {bind["bind"]: path for path, bind in spec.volumes.items() if bind["mode"] == "ro"}
    assert set(mounts) == {f"{d}/managed/sealskin.json" for _b, d in browser_policies.CHROMIUM_DIRECTORIES}
    path = mounts["/etc/chromium/policies/managed/sealskin.json"]
    assert path.startswith(settings.browser_policy_files_path + os.sep)
    assert os.stat(path).st_mode & 0o777 == 0o644
    held = open(path, "rb")
    inode = os.stat(path).st_ino

    _spec(make_app(browser_policy="p1"), tmp_path)
    assert os.stat(path).st_ino == inode

    state.browser_policies["p1"] = _policy(chromium={"ShowHomeButton": False})
    _spec(make_app(browser_policy="p1"), tmp_path)
    assert json.loads(held.read()) == {"ShowHomeButton": True}
    assert json.load(open(path)) == {"ShowHomeButton": False}

    assert not [b for b in _spec(make_app(), tmp_path).volumes.values() if b["mode"] == "ro"]


def test_a_launch_is_refused_when_the_policy_is_missing(tmp_path):
    state.app_templates["Default"] = {"name": "Default", "settings": {}}
    with pytest.raises(HTTPException) as refused:
        _spec(make_app(browser_policy="gone"), tmp_path)
    assert refused.value.status_code == 409


def test_policies_are_kept_shared_and_one_in_use_is_not_deleted(tmp_path):
    asyncio.run(browser_policies.put(_policy(firefox={"DisableAppUpdate": True})))
    with pytest.raises(browser_policies.PolicyError):
        asyncio.run(browser_policies.put(BrowserPolicy(id="p2", name="kiosk")))
    browser_policies.mounts_for("p1")
    state.browser_policies.clear()
    browser_policies.load()
    assert state.browser_policies["p1"].firefox == {"DisableAppUpdate": True}

    app = make_app(browser_policy="p1")
    state.installed_apps[app.id] = app
    with pytest.raises(browser_policies.PolicyError):
        asyncio.run(browser_policies.delete("p1"))
    state.installed_apps.clear()
    asyncio.run(browser_policies.delete("p1"))
    assert not os.path.exists(os.path.join(settings.browser_policy_files_path, "p1"))
    browser_policies.load()
    assert state.browser_policies == {}


def test_a_catalog_is_served_in_a_packaged_language_and_in_english_otherwise(tmp_path, monkeypatch):
    english = browser_policies.catalog("chromium")
    (tmp_path / "l10n").mkdir()
    for engine in browser_policies.ENGINES:
        (tmp_path / f"{engine}.json").write_text(json.dumps(browser_policies.catalog(engine)))
    (tmp_path / "l10n" / "chromium.de.json").write_text(
        json.dumps(
            {
                "groups": {"Startup": "Start"},
                "policies": {
                    "HomepageLocation": {"title": "Startseite"},
                    "RestoreOnStartup": {"labels": ["eins"]},
                },
            }
        )
    )
    monkeypatch.setattr(browser_policies, "CATALOG_PATH", str(tmp_path))
    browser_policies.localized.cache_clear()
    try:
        german = browser_policies.localized("chromium", "de-DE")
        by_name = {policy["name"]: policy for policy in german["policies"]}
        source = {policy["name"]: policy for policy in english["policies"]}
        assert by_name["HomepageLocation"]["title"] == "Startseite"
        assert by_name["HomepageLocation"]["description"] == source["HomepageLocation"]["description"]
        assert by_name["RestoreOnStartup"]["schema"]["x-labels"] == source["RestoreOnStartup"]["schema"]["x-labels"]
        assert {"id": "Startup", "title": "Start"} in german["groups"]
        assert source["HomepageLocation"]["title"] != "Startseite"
        for language in ("fr", "", "../../de", "xx"):
            assert browser_policies.localized("chromium", language) is english
    finally:
        browser_policies.localized.cache_clear()


def test_a_policy_another_node_wrote_is_taken_up_and_applied_here(tmp_path, monkeypatch):
    from app import api, persistence, store

    assert store.key_for(settings.browser_policies_path) == "browser_policies.yml"
    assert "browser_policies.yml" in store.WATCHED
    assert settings.browser_policies_path in api._watch_targets()
    assert store.key_for(settings.browser_policy_files_path) is None

    state.app_templates["Default"] = {"name": "Default", "settings": {}}
    app = make_app(browser_policy="p1")
    with pytest.raises(HTTPException):
        _spec(app, tmp_path)

    persistence.write_yaml_sync(settings.browser_policies_path, [_policy(firefox={"DisableAppUpdate": True}).model_dump()])
    asyncio.run(api._reload_browser_policies(settings.browser_policies_path))
    mounts = {bind["bind"]: path for path, bind in _spec(app, tmp_path).volumes.items() if bind["mode"] == "ro"}
    assert json.load(open(mounts["/etc/firefox/policies/policies.json"])) == {"policies": {"DisableAppUpdate": True}}

    persistence.write_yaml_sync(settings.browser_policies_path, [])
    asyncio.run(api._reload_browser_policies(settings.browser_policies_path))
    assert not os.path.exists(os.path.join(settings.browser_policy_files_path, "p1"))
    with pytest.raises(HTTPException):
        _spec(app, tmp_path)


def test_a_policy_the_store_refused_is_not_kept_on_this_node(monkeypatch):
    from app import store

    asyncio.run(browser_policies.put(_policy(firefox={"DisableAppUpdate": True})))

    async def refuse():
        raise store.Conflict("changed")

    monkeypatch.setattr(browser_policies, "_save", refuse)
    with pytest.raises(store.Conflict):
        asyncio.run(browser_policies.put(_policy(firefox={"DisableAppUpdate": False})))
    with pytest.raises(store.Conflict):
        asyncio.run(browser_policies.put(BrowserPolicy(id="p2", name="Other")))
    with pytest.raises(store.Conflict):
        asyncio.run(browser_policies.delete("p1"))
    assert set(state.browser_policies) == {"p1"}
    assert state.browser_policies["p1"].firefox == {"DisableAppUpdate": True}
