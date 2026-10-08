"""Group membership and how the settings of several groups combine."""

import pytest

from app import user_manager


@pytest.fixture
def users():
    user_manager.write_group_file("staff", {"gpu": False, "session_limit": 2, "sso_groups": ["employees"]})
    user_manager.write_group_file("power", {"gpu": True, "session_limit": 5, "public_sharing": True})
    user_manager.write_group_file("locked", {"harden_container": True, "pools_denied": ["gpu"], "allowance_hours": 10, "allowance_period": "week"})
    user_manager.write_group_file("open", {"harden_container": False, "pools": ["gpu"], "allowance_hours": 40})
    user_manager.write_user_file("alice", "", dict(user_manager.DEFAULT_USER_SETTINGS, groups=["power"], session_limit=9))
    user_manager.write_user_file("bob", "", dict(user_manager.DEFAULT_USER_SETTINGS, group="staff", public_sharing=True))
    user_manager.load_users_and_groups()


def test_a_user_in_no_group_keeps_their_own_settings(users):
    user_manager.write_user_file("carol", "", dict(user_manager.DEFAULT_USER_SETTINGS, session_limit=3, gpu=False))
    user_manager.load_users_and_groups()
    effective = user_manager.get_effective_settings("carol")
    assert (effective["session_limit"], effective["gpu"], effective["groups"]) == (3, False, [])


def test_one_group_decides_what_it_sets_and_leaves_the_rest_to_the_user(users):
    effective = user_manager.get_effective_settings("bob")
    assert effective["groups"] == ["staff"] and effective["group"] == "staff"
    assert (effective["gpu"], effective["session_limit"], effective["public_sharing"]) == (False, 2, True)


def test_where_groups_disagree_the_restricting_value_and_the_smallest_limit_win(users):
    effective = user_manager.get_effective_settings("alice", ["employees", "locked", "open"])
    assert effective["groups"][0] == "power" and set(effective["groups"]) == {"power", "staff", "locked", "open"}
    assert effective["gpu"] is False and effective["session_limit"] == 2
    assert effective["harden_container"] is True
    assert effective["public_sharing"] is True
    assert (effective["allowance_hours"], effective["allowance_period"]) == (10, "week")
    assert effective["pools"] == ["gpu"] and effective["pools_denied"] == ["gpu"]


def test_provider_groups_admit_by_name_and_by_mapping_and_unknown_ones_are_ignored(users):
    assert user_manager.groups_of("alice", ["/employees", "nobody"]) == ["power", "staff"]
    assert user_manager.groups_of("alice", ["locked"]) == ["power", "locked"]


def test_a_sign_in_creates_binds_and_refuses_another_subject(users, monkeypatch):
    created = user_manager.ensure_user("dave", "oidc", "oidc https://idp sub-1", ["employees"])
    assert created["public_key"] == "" and created["settings"]["provider_groups"] == ["employees"]
    assert user_manager.ensure_user("dave", "oidc", "oidc https://idp sub-1", ["employees"]) is created
    with pytest.raises(ValueError):
        user_manager.ensure_user("dave", "oidc", "oidc https://idp sub-2", [])
    with pytest.raises(ValueError):
        user_manager.ensure_user("root", "proxy")
    monkeypatch.setattr(user_manager.settings, "sso_create_users", False)
    with pytest.raises(ValueError):
        user_manager.ensure_user("erin", "proxy")


def test_updating_a_user_keeps_the_provider_binding(users):
    user_manager.ensure_user("dave", "saml", "saml idp dave", ["employees"])
    user_manager.update_user_settings("dave", dict(user_manager.DEFAULT_USER_SETTINGS, session_limit=1))
    stored = user_manager.get_user("dave")["settings"]
    assert stored["auth"] == {"saml": "saml idp dave"} and stored["session_limit"] == 1


def test_the_last_sign_ins_groups_count_where_a_caller_passes_none(users):
    user_manager.ensure_user("dave", "oidc", "oidc https://idp sub-1", ["employees"])
    assert user_manager.groups_of("dave") == ["staff"]
    assert user_manager.groups_of("dave", ()) == []
    assert user_manager.get_effective_settings("dave")["session_limit"] == 2


def test_a_user_a_sign_in_creates_in_no_group_is_held_until_grouped_or_approved(users, monkeypatch):
    user_manager.ensure_user("erin", "oidc", "oidc https://idp sub-9", ["nobody"])
    assert user_manager.get_user("erin")["settings"]["approved"] is False
    assert user_manager.held("erin", ["nobody"]) is True
    # A group the provider names later lets the user in, as does one by mapping.
    assert user_manager.held("erin", ["power"]) is False
    assert user_manager.held("erin", ["employees"]) is False
    user_manager.update_user_settings("erin", dict(user_manager.DEFAULT_USER_SETTINGS, groups=["power"]))
    assert user_manager.held("erin") is False
    user_manager.update_user_settings("erin", dict(user_manager.DEFAULT_USER_SETTINGS))
    assert user_manager.held("erin") is True and user_manager.get_user("erin")["settings"]["approved"] is False
    user_manager.approve("erin")
    assert user_manager.held("erin") is False
    assert user_manager.get_user("erin")["settings"]["approved"] is True
    monkeypatch.setattr(user_manager.settings, "sso_hold_new_users", False)
    user_manager.ensure_user("frank", "proxy")
    assert "approved" not in user_manager.get_user("frank")["settings"]
    assert user_manager.held("frank") is False


def test_a_user_the_provider_puts_in_a_group_or_an_administrator_made_is_never_held(users, monkeypatch):
    user_manager.ensure_user("gina", "oidc", "oidc https://idp sub-2", ["employees"])
    assert user_manager.get_user("gina")["settings"]["approved"] is False
    assert user_manager.held("gina") is False
    # The provider's administrator group counts as a group, with no SealSkin group of that name.
    monkeypatch.setattr(user_manager.settings, "sso_admin_group", "/sealskin-admins")
    user_manager.ensure_user("greg", "oidc", "oidc https://idp sub-4", ["sealskin-admins"])
    assert user_manager.held("greg") is False and user_manager.held("greg", ["sealskin-admins"]) is False
    assert user_manager.held("greg", ["nobody"]) is True
    listed = {u["username"]: (u["held"], u["admin"]) for u in user_manager.get_all_users()}
    assert listed["gina"] == (False, False) and listed["greg"] == (False, True)
    user_manager.create_user("hank", None, dict(user_manager.DEFAULT_USER_SETTINGS))
    assert user_manager.held("hank") is False
    assert user_manager.held("root") is False


def test_a_sign_in_for_an_unknown_user_while_sign_ins_create_none_names_the_missing_account(users, monkeypatch):
    monkeypatch.setattr(user_manager.settings, "sso_create_users", False)
    with pytest.raises(user_manager.NoAccount):
        user_manager.ensure_user("ivan", "oidc", "oidc https://idp sub-3")
