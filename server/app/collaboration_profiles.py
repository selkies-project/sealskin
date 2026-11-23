"""Collaboration profiles: what the rooms of users and groups may do.

A profile switches the features of a collaboration room on or off (chat,
cameras, microphones, the application switcher, gamepad and mouse and
keyboard handoff, each kind of invite), says how the stream is sized, and
caps how many people may be in the room. The profiles are one shared record,
`collaboration_profiles.yml`.

A user's rooms run under the profiles their groups name or, where no group
names one, the user's own (`user_manager.get_effective_settings` lists them
as `collaboration_profiles`). Where several apply, each feature takes its
most restrictive value: it is on only where every profile has it on, the
stream is the controller's where any profile says so, and the smallest limit
wins. A room always takes one kind of invite: where the profiles together
turn both off, read-only invites stay on. A user under no profile gets
`DEFAULT_RULES`, every feature on.

The rules are copied into a session's record when it starts (`rules_for`),
so a room keeps the rules it started under. The room page leaves out what is
off, and `collaboration` refuses it. A room under a limit of people hands
a viewer the session's stream only while the room admits them: a viewer's
token joins the token table as their room socket is let in and leaves it as
the socket goes (`admitted`). A room is not started under a profile that is
no longer kept, which would lift its rules.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from pydantic import ValidationError

from . import persistence, user_manager
from .models import CollaborationProfile
from .settings import settings
from .state import state

logger = logging.getLogger(__name__)

#: The features a profile switches on or off.
FEATURES = (
    "chat",
    "webcams",
    "microphones",
    "app_switcher",
    "gamepads",
    "input_handoff",
    "participant_invites",
    "readonly_invites",
    "participants_share",
)
#: The rules of a room no profile governs.
DEFAULT_RULES: dict[str, Any] = {**dict.fromkeys(FEATURES, True), "resolution": "follow", "max_viewers": -1}


class ProfileError(Exception):
    """A profile cannot be kept or removed as asked."""


def load() -> None:
    """Read the shared record into `state.collaboration_profiles`."""
    records = persistence.read_yaml(settings.collaboration_profiles_path, []) or []
    loaded: dict[str, CollaborationProfile] = {}
    for item in records if isinstance(records, list) else []:
        try:
            profile = CollaborationProfile(**item)
        except (ValidationError, TypeError) as exc:
            logger.warning("Skipping a collaboration profile record that does not validate: %s", exc)
            continue
        loaded[profile.id] = profile
    state.collaboration_profiles = loaded


async def _save() -> None:
    await persistence.write_yaml(
        settings.collaboration_profiles_path,
        [profile.model_dump() for profile in state.collaboration_profiles.values()],
    )


def new_id() -> str:
    """Return an identifier for a new profile."""
    return uuid.uuid4().hex


async def put(profile: CollaborationProfile) -> CollaborationProfile:
    """Keep a profile, new or changed; rooms started from then on run under it.

    Raises:
        ProfileError: When another profile has its name.
        store.Conflict: When another node changed the record meanwhile; nothing is kept.
        store.StoreUnavailable: When the shared record cannot be written; nothing is kept.
    """
    taken = any(
        other.id != profile.id and other.name.casefold() == profile.name.casefold()
        for other in state.collaboration_profiles.values()
    )
    if taken:
        raise ProfileError(f"Another collaboration profile is named '{profile.name}'.")
    previous = state.collaboration_profiles.get(profile.id)
    state.collaboration_profiles[profile.id] = profile
    try:
        await _save()
    except BaseException:
        if previous is None:
            del state.collaboration_profiles[profile.id]
        else:
            state.collaboration_profiles[profile.id] = previous
        raise
    return profile


def used_by(profile_id: str) -> dict[str, list[str]]:
    """Return the users and groups that name a profile, as `{"users": [...], "groups": [...]}`."""
    users = sorted(
        user["username"]
        for user in user_manager.USER_DATA.values()
        if (user.get("settings") or {}).get("collaboration_profile") == profile_id
    )
    groups = sorted(
        name
        for name, group in user_manager.GROUP_DATA.items()
        if (group.get("settings") or {}).get("collaboration_profile") == profile_id
    )
    return {"users": users, "groups": groups}


async def delete(profile_id: str) -> None:
    """Remove a profile no user or group names.

    Raises:
        KeyError: When there is no such profile.
        ProfileError: While a user or group names it, since removing it would lift its rules.
    """
    if profile_id not in state.collaboration_profiles:
        raise KeyError(profile_id)
    users = used_by(profile_id)
    named = users["groups"] + users["users"]
    if named:
        raise ProfileError(f"The profile is in use by {', '.join(named)}; choose another profile for them first.")
    removed = state.collaboration_profiles.pop(profile_id)
    try:
        await _save()
    except BaseException:
        state.collaboration_profiles[profile_id] = removed
        raise


def merge(profiles: list[CollaborationProfile]) -> dict[str, Any]:
    """Return the rules of a room under every one of `profiles`, each feature at its most restrictive."""
    rules = dict(DEFAULT_RULES)
    for profile in profiles:
        for feature in FEATURES:
            rules[feature] = rules[feature] and getattr(profile, feature)
        if profile.resolution == "controller":
            rules["resolution"] = "controller"
        if profile.max_viewers >= 0:
            current = rules["max_viewers"]
            rules["max_viewers"] = profile.max_viewers if current < 0 else min(current, profile.max_viewers)
    if not (rules["participant_invites"] or rules["readonly_invites"]):
        rules["readonly_invites"] = True
    return rules


def rules_for(effective_settings: dict[str, Any] | None) -> dict[str, Any]:
    """Return the rules a user's rooms run under, from their effective settings.

    Raises:
        ProfileError: When a profile the settings name is no longer kept.
    """
    named = (effective_settings or {}).get("collaboration_profiles") or []
    if any(pid not in state.collaboration_profiles for pid in named):
        raise ProfileError("A collaboration profile assigned to this account no longer exists; ask an administrator.")
    return merge([state.collaboration_profiles[pid] for pid in named])


def rules_of(session: dict[str, Any]) -> dict[str, Any]:
    """Return the rules a room runs under, as its session record keeps them."""
    kept = session.get("collaboration_rules")
    if not isinstance(kept, dict):
        return dict(DEFAULT_RULES)
    return {key: kept.get(key, default) for key, default in DEFAULT_RULES.items()}
