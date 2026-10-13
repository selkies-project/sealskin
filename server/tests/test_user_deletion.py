"""Deleting a user ends what the user had: running sessions, unfinished uploads, public shares."""

import os

from app.models import PublicShareMetadata
from app.routers import admin, shares, uploads
from app.settings import settings
from app.state import state


async def test_deleting_a_user_ends_their_sessions_uploads_and_shares(monkeypatch):
    stopped = []

    async def stop(session_id):
        stopped.append(session_id)
        state.sessions.pop(session_id, None)

    async def nothing(*_args, **_kwargs):
        return None

    monkeypatch.setattr(admin.user_manager, "delete_user", lambda username: None)
    monkeypatch.setattr(admin.cluster, "forget_user", nothing)
    monkeypatch.setattr(admin.sso, "end_sign_ins_of", nothing)
    monkeypatch.setattr(admin.application_keys, "revoke_all", lambda username: None)
    monkeypatch.setattr(admin, "stop_session", stop)
    monkeypatch.setattr(shares.config_store, "save_public_shares", nothing)
    state.sessions.update({"s1": {"username": "carol"}, "s2": {"username": "dave"}})
    upload = os.path.join(uploads.user_upload_root("carol"), "u1")
    os.makedirs(upload)
    os.makedirs(settings.public_storage_path, exist_ok=True)
    for share_id, owner in (("sh-carol", "carol"), ("sh-dave", "dave")):
        open(os.path.join(settings.public_storage_path, share_id), "w").close()
        state.public_shares[share_id] = PublicShareMetadata.model_construct(owner_username=owner)
    try:
        response = await admin.delete_user("carol")
        assert response.status_code == 204
        assert stopped == ["s1"] and "s2" in state.sessions
        assert not os.path.exists(upload)
        assert "sh-carol" not in state.public_shares and "sh-dave" in state.public_shares
        assert not os.path.exists(os.path.join(settings.public_storage_path, "sh-carol"))
    finally:
        state.sessions.pop("s2", None)
        state.public_shares.pop("sh-dave", None)
