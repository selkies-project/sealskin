"""Reference + overrides resolution and legacy migration."""

import copy

import yaml

from app import config_store, persistence
from app.models import InstalledApp, InstalledAppRecord
from app.settings import settings
from app.state import state


def test_deep_merge_and_diff_roundtrip():
    base = {"a": 1, "nested": {"x": 1, "y": [1, 2]}, "list": [1]}
    overrides = {"nested": {"y": [3]}, "list": [2], "new": "v"}
    merged = config_store.deep_merge(base, overrides)
    assert merged == {"a": 1, "nested": {"x": 1, "y": [3]}, "list": [2], "new": "v"}
    assert config_store.diff_overrides(merged, base) == overrides
    assert config_store.diff_overrides(base, base) == {}


def test_resolve_app_merges_store_entry_with_overrides(store_with_firefox):
    record = InstalledAppRecord(
        id="app-1",
        source="Test Store",
        source_app_id="firefox",
        app_template="Default",
        users=["all"],
        overrides={"name": "My Firefox", "provider_config": {"env": [{"name": "FOO", "value": "bar"}]}},
    )
    app = config_store.resolve_app(record)
    assert isinstance(app, InstalledApp)
    assert app.name == "My Firefox"
    assert app.provider_config.image == "lscr.io/linuxserver/firefox:latest"
    assert app.provider_config.extensions == ["html", "htm", "pdf"]
    assert app.provider_config.env[0].name == "FOO"
    assert app.users == ["all"]


def test_resolve_app_without_store_entry_is_skipped():
    record = InstalledAppRecord(id="x", source="Missing", source_app_id="nope", overrides={"name": "n"})
    assert config_store.resolve_app(record) is None


def test_record_from_app_keeps_only_changes(store_with_firefox):
    entry = copy.deepcopy(store_with_firefox)
    entry["provider_config"]["extensions"] = ["html", "htm", "pdf"]
    app = InstalledApp(
        id="app-2",
        source="Test Store",
        source_app_id="firefox",
        app_template="Default",
        home_directories=True,
        users=["alice"],
        groups=[],
        **{k: entry[k] for k in ("name", "logo", "url", "provider", "provider_config")},
    )
    app.provider_config.port = 3001
    record = config_store.record_from_app(app)
    assert record.overrides == {"provider_config": {"port": 3001}}
    assert record.users == ["alice"]


def test_legacy_snapshot_is_migrated_and_file_rewritten(store_with_firefox):
    legacy = copy.deepcopy(store_with_firefox)
    legacy["provider_config"]["extensions"] = ["html", "htm", "pdf"]
    legacy.update(
        {
            "id": "legacy-1",
            "source": "Test Store",
            "source_app_id": "firefox",
            "home_directories": True,
            "users": ["all"],
            "groups": [],
            "auto_update": True,
            "app_template": "Default",
            "is_meta_app": False,
        }
    )
    legacy["name"] = "Renamed Firefox"
    legacy["provider_config"]["env"] = [{"name": "A", "value": "1"}]
    persistence.write_yaml_sync(settings.installed_apps_path, [legacy])

    config_store.load_installed_apps()

    record = state.installed_records["legacy-1"]
    assert record.overrides == {
        "name": "Renamed Firefox",
        "provider_config": {"env": [{"name": "A", "value": "1"}]},
    }
    assert state.installed_apps["legacy-1"].name == "Renamed Firefox"
    with open(settings.installed_apps_path, encoding="utf-8") as handle:
        on_disk = yaml.safe_load(handle)
    assert on_disk[0]["overrides"] == record.overrides
    assert "image" not in yaml.safe_dump(on_disk)


def test_store_update_applies_after_reload(store_with_firefox):
    record = InstalledAppRecord(id="app-3", source="Test Store", source_app_id="firefox")
    config_store.set_record(record)
    assert state.installed_apps["app-3"].provider_config.image.endswith(":latest")

    updated = copy.deepcopy(store_with_firefox)
    updated["provider_config"]["image"] = "lscr.io/linuxserver/firefox:2.0"
    with open(config_store.store_cache_file("Test Store"), "w", encoding="utf-8") as handle:
        yaml.safe_dump({"apps": [updated]}, handle)
    config_store.load_store_entries()
    config_store.resolve_all_apps()
    assert state.installed_apps["app-3"].provider_config.image.endswith(":2.0")


def test_invalid_record_does_not_wipe_list(store_with_firefox):
    good = {"id": "ok", "source": "Test Store", "source_app_id": "firefox"}
    bad = {"id": "bad", "source": None}
    persistence.write_yaml_sync(settings.installed_apps_path, [good, bad, "garbage"])
    config_store.load_installed_apps()
    assert set(state.installed_apps) == {"ok"}


def test_apply_partial_update(store_with_firefox):
    record = InstalledAppRecord(id="app-4", source="Test Store", source_app_id="firefox")
    updated = config_store.apply_partial_update(
        record, {"users": ["bob"], "name": "Bob's Firefox", "provider_config": {"port": 4000}}
    )
    assert updated.users == ["bob"]
    assert updated.overrides == {"name": "Bob's Firefox", "provider_config": {"port": 4000}}
    reverted = config_store.apply_partial_update(updated, {"provider_config": {"port": 3000}})
    assert reverted.overrides == {"name": "Bob's Firefox"}


def test_template_save_reuses_hand_edited_file(tmp_path):
    import asyncio

    from app.models import AppTemplate

    config_store.ensure_config_dir()
    odd_path = f"{settings.app_templates_path}/whatever.yml"
    persistence.write_yaml_sync(odd_path, {"name": "Gaming", "settings": {"A": "1"}})
    config_store.load_app_templates()
    assert state.template_files["Gaming"] == odd_path

    asyncio.run(config_store.save_app_template(AppTemplate(name="Gaming", settings={"A": "2"})))
    assert state.app_templates["Gaming"]["settings"] == {"A": "2"}
    assert state.template_files["Gaming"] == odd_path
    config_store.delete_app_template("Gaming")
    assert "Gaming" not in state.app_templates
