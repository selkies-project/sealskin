"""The dashboards as a user's home page reads them and as an administrator lays them out.

A signed-in user reads their dashboards (`GET /api/dashboard`) and asks for
the data of the widgets on the page in one request
(`POST /api/dashboard/data`), on every lane. An administrator reads and
replaces the dashboards and the widget library under
`/api/admin/dashboards`, and tries a widget before saving it.
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field, ValidationError

from .. import dashboards, routing, user_manager, widgets
from ..dashboards import Dashboard, LibraryWidget
from ..security import EncryptedRoute, get_decrypted_request_body, verify_admin, verify_token

#: Widgets one request may ask the data of.
MAX_ITEMS = 60

#: An uploaded icon is public by its hash, as an `img` tag carries no sign-in.
router = APIRouter(prefix="/api/dashboard")
user_router = APIRouter(prefix="/api/dashboard", route_class=EncryptedRoute)
admin_router = APIRouter(
    prefix="/api/admin/dashboards",
    dependencies=[Depends(verify_admin), Depends(routing.announce_changes)],
    route_class=EncryptedRoute,
)


class DataRequest(BaseModel):
    """The widgets a page asks the data of.

    Attributes:
        items: Each `{key, widget}` for a library widget or `{key, type, config}` for one of the user's own.
        fresh: Read again whatever is cached; honored for an administrator.
    """

    items: list[dict[str, Any]] = Field(max_length=MAX_ITEMS)
    fresh: bool = False


class PlacesRequest(BaseModel):
    """A place name to look up for a weather widget."""

    name: str = Field(max_length=80)


class IconNames(BaseModel):
    """Icon pack names a page asks the pictures of."""

    names: list[str] = Field(max_length=200)


class SiteIcon(BaseModel):
    """A site whose own icon a tile is to carry."""

    url: str = Field(max_length=2000)


class IconUpload(BaseModel):
    """A picture, base64 encoded, to keep as an icon."""

    data: str = Field(max_length=1_400_000)


class BoardsBody(BaseModel):
    """Every dashboard, in order."""

    boards: list[Dashboard]


class LibraryBody(BaseModel):
    """Every widget of the library."""

    widgets: list[LibraryWidget]


class HomepageImport(BaseModel):
    """A Homepage `customapi` widget definition, as YAML."""

    text: str = Field(max_length=20000)


class WidgetTest(BaseModel):
    """A widget to try before it is saved."""

    type: str
    config: dict[str, Any] = {}


def _parse(model: type[BaseModel], body: dict[str, Any]) -> Any:
    """Validate a request body, answering 422 with the first fault in words."""
    try:
        return model.model_validate(body)
    except ValidationError as exc:
        first = exc.errors()[0]
        where = ".".join(str(part) for part in first["loc"])
        raise HTTPException(status_code=422, detail=f"{where}: {first['msg']}") from exc


@user_router.get("")
async def get_dashboard(user: dict[str, Any] = Depends(verify_token)) -> dict[str, Any]:
    """Return the caller's dashboards, the widgets they may place, and what they may change."""
    return dashboards.view(user)


@user_router.post("/data")
async def widget_data(
    body: dict[str, Any] = Depends(get_decrypted_request_body), user: dict[str, Any] = Depends(verify_token)
) -> dict[str, Any]:
    """Return what each widget asked for shows, by the key the page gave it."""
    asked = _parse(DataRequest, body)
    items = [item for item in asked.items if isinstance(item.get("key"), str) and len(item["key"]) <= 120]
    answers = await asyncio.gather(*(dashboards.data_for(user, item, asked.fresh) for item in items))
    return {"results": {item["key"]: answer for item, answer in zip(items, answers, strict=True)}}


@user_router.post("/places")
async def find_places(
    body: dict[str, Any] = Depends(get_decrypted_request_body), user: dict[str, Any] = Depends(verify_token)
) -> dict[str, Any]:
    """Return the places a name may mean, for a weather widget's form."""
    if not dashboards.policy(user)["customize"]:
        raise HTTPException(status_code=403, detail="Your administrator has turned off changes to the page.")
    try:
        return {"places": await widgets.places(_parse(PlacesRequest, body).name)}
    except widgets.WidgetError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/icons/{name}", include_in_schema=False)
async def icon(name: str) -> Response:
    """Serve an uploaded icon."""
    return dashboards.icon_response(name)


@user_router.post("/icons")
async def pack_icons(
    body: dict[str, Any] = Depends(get_decrypted_request_body), user: dict[str, Any] = Depends(verify_token)
) -> dict[str, Any]:
    """Return where the page loads each icon pack name from."""
    return {"images": dashboards.pack_images(_parse(IconNames, body).names)}


@user_router.post("/site-icon")
async def site_icon(
    body: dict[str, Any] = Depends(get_decrypted_request_body), user: dict[str, Any] = Depends(verify_token)
) -> dict[str, Any]:
    """Return a site's own icon as a data URL, for a link tile; a user's site is read only on the public internet."""
    if not (user.get("is_admin") or dashboards.policy(user)["links"]):
        raise HTTPException(status_code=403, detail="Your administrator has turned off links of your own.")
    try:
        return {"data": await dashboards.site_icon(_parse(SiteIcon, body).url, public_only=not user.get("is_admin"))}
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@admin_router.post("/icons")
async def upload_icon(body: dict[str, Any] = Depends(get_decrypted_request_body)) -> dict[str, Any]:
    """Keep an uploaded picture as an icon for the dashboards' tiles."""
    try:
        name = dashboards.save_icon(_parse(IconUpload, body).data)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"icon": name, "image": dashboards.icon_image(name)}


@admin_router.get("")
async def read_dashboards() -> dict[str, Any]:
    """Return the dashboards and the library as kept, the secrets this node holds by name, the link schemes, and the groups."""
    return {
        "boards": [board.model_dump() for board in dashboards.boards()],
        "widgets": [widget.model_dump() for widget in dashboards.library()],
        "secrets": widgets.secret_names(),
        "schemes": dashboards.link_schemes(),
        "types": list(widgets.TYPES),
        "groups": sorted({user_manager.DEFAULT_GROUP, *user_manager.GROUP_DATA}),
    }


@admin_router.put("/boards")
async def write_boards(body: dict[str, Any] = Depends(get_decrypted_request_body)) -> dict[str, Any]:
    """Replace the dashboards."""
    try:
        dashboards.save_boards(_parse(BoardsBody, body).boards)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"boards": [board.model_dump() for board in dashboards.boards()]}


@admin_router.put("/widgets")
async def write_library(body: dict[str, Any] = Depends(get_decrypted_request_body)) -> dict[str, Any]:
    """Replace the widget library."""
    try:
        dashboards.save_library(_parse(LibraryBody, body).widgets)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"widgets": [widget.model_dump() for widget in dashboards.library()]}


@admin_router.post("/widgets/test")
async def test_widget(body: dict[str, Any] = Depends(get_decrypted_request_body)) -> dict[str, Any]:
    """Read a widget as configured, without saving it, and return what it would show."""
    asked = _parse(WidgetTest, body)
    try:
        config = widgets.validate(asked.type, asked.config)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return await widgets.data(asked.type, config, fresh=True)


@admin_router.post("/widgets/homepage")
async def import_homepage(body: dict[str, Any] = Depends(get_decrypted_request_body)) -> dict[str, Any]:
    """Turn a Homepage `customapi` widget definition into an `api` widget's configuration."""
    try:
        return widgets.from_homepage(_parse(HomepageImport, body).text)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
