"""The template schema and every locale's strings describe the same settings.

The editor renders whatever `template_schema.yml` serves, with labels looked
up from the client's translations by variable name, so an entry added to one
and not the other shows up as a raw variable name in some language, and a
select whose default is not one of its options stores a value the editor
cannot show. Both are checked here rather than found in the editor.
"""
import json
import os

from app.routers.ui import load_template_schema

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
I18N = os.path.join(REPO, "client", "src", "i18n")
CATEGORIES = {"ui", "app", "hardening", "general", "webrtc", "docker"}
TYPES = {"text", "boolean", "select"}


def locale_strings():
    for name in sorted(os.listdir(I18N)):
        if name.endswith(".json"):
            with open(os.path.join(I18N, name), encoding="utf-8") as handle:
                yield name, json.load(handle)["options"]["appTemplates"]["settings"]


def test_schema_entries_are_well_formed():
    entries = load_template_schema()
    names = [entry.name for entry in entries]
    assert len(names) == len(set(names)), "duplicate names"
    for entry in entries:
        assert entry.category in CATEGORIES, entry.name
        assert entry.type in TYPES, entry.name
        if entry.type == "boolean":
            assert entry.default in ("true", "false"), entry.name
        if entry.type == "select":
            values = [option.value for option in entry.options or []]
            assert values and entry.default in values, entry.name
            assert all(option.label or option.label_key for option in entry.options), entry.name
        else:
            assert not entry.options, entry.name


def test_every_locale_labels_every_setting():
    entries = load_template_schema()
    for locale, strings in locale_strings():
        assert set(strings) == {entry.name for entry in entries}, locale
        for entry in entries:
            text = strings[entry.name]
            assert text.get("label") and text.get("description"), (locale, entry.name)
            keys = {option.label_key for option in entry.options or [] if option.label_key}
            assert set(text.get("options", {})) == keys, (locale, entry.name)
