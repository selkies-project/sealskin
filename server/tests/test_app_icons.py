"""An app's icon is served only to whom the app is listed for."""

import os

import pytest
from fastapi import HTTPException

from app.models import InstalledApp
from app.routers import applications
from app.settings import settings
from app.state import state


@pytest.fixture
def alices_app():
    state.installed_apps["alices"] = InstalledApp.model_construct(id="alices", users=["alice"], groups=[])
    icons = settings.app_icons_path
    os.makedirs(icons, exist_ok=True)
    with open(os.path.join(icons, "alices.png"), "wb") as handle:
        handle.write(b"\x89PNG icon")
    yield
    state.installed_apps.pop("alices", None)


async def test_an_icon_is_served_to_whom_its_app_is_listed_for(alices_app):
    shown = await applications.get_app_icon("alices", user={"username": "alice", "groups": []})
    assert shown["icon_data_b64"]
    assert await applications.get_app_icon("alices", user={"username": "root", "groups": [], "is_admin": True})
    for app_id in ("alices", "no-such-app"):
        with pytest.raises(HTTPException) as refused:
            await applications.get_app_icon(app_id, user={"username": "bob", "groups": []})
        assert refused.value.status_code == 404
