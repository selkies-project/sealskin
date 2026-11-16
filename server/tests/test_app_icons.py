"""An app's icon is served only to whom the app is listed for, and kept as a PNG under its id."""

import base64
import io
import os

import pytest
from fastapi import HTTPException
from PIL import Image

from app import app_icons
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
    assert await applications.get_app_icon("alices", user={"username": "admin", "groups": [], "is_admin": True})
    for app_id in ("alices", "no-such-app"):
        with pytest.raises(HTTPException) as refused:
            await applications.get_app_icon(app_id, user={"username": "bob", "groups": []})
        assert refused.value.status_code == 404


def _picture(size=(600, 300)):
    out = io.BytesIO()
    Image.new("RGB", size, "red").save(out, format="JPEG")
    return base64.b64encode(out.getvalue()).decode()


def test_an_uploaded_icon_is_kept_as_a_png_and_copied_by_its_route():
    assert app_icons.save("one", "data:image/jpeg;base64," + _picture()) == "/api/app_icon/one"
    with Image.open(io.BytesIO(app_icons.read("one"))) as kept:
        assert kept.format == "PNG" and max(kept.size) == app_icons.EDGE
    assert app_icons.save("two", "/api/app_icon/one") == "/api/app_icon/two"
    assert app_icons.read("two") == app_icons.read("one")
    assert app_icons.save("three", "/api/app_icon/missing") == ""
    assert app_icons.save("one", "https://example.com/x.png") == "https://example.com/x.png"

    app_icons.forget("one", "/api/app_icon/one")
    assert app_icons.read("one")
    app_icons.forget("one", "https://example.com/x.png")
    assert app_icons.read("one") is None
    app_icons.forget("two")
    assert app_icons.read("two") is None


def test_data_that_is_no_picture_is_refused():
    with pytest.raises(ValueError):
        app_icons.save("one", "bm90IGEgcGljdHVyZQ==")
