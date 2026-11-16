"""The Bakery: baked applications and their recipes, their builds on this node, and trial sessions of a build."""

from __future__ import annotations

import asyncio
import os
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ValidationError

from .. import bakekits, bakery, cluster, routing
from ..launch import launch_application
from ..models import BakeRecipe, LaunchResponse
from ..security import EncryptedRoute, get_decrypted_request_body, verify_admin
from ..state import state
from . import admin
from .uploads import reassemble_file

admin_router = APIRouter(
    prefix="/api/admin/bakery",
    dependencies=[Depends(verify_admin), Depends(routing.announce_changes)],
    route_class=EncryptedRoute,
)


class FileUpload(BaseModel):
    """A finished chunked upload to keep as a recipe file."""

    upload_id: str
    total_chunks: int
    filename: str


class TrialRequest(BaseModel):
    """Launch a session of an application on the image a build made."""

    build_id: str
    language: str | None = None
    timezone: str | None = None
    selected_gpu: str | None = None
    wayland_mode: bool = True


def _available() -> None:
    if not bakery.available():
        raise HTTPException(status_code=409, detail="This server's backend builds no images: the Bakery needs Docker.")


def _app(app_id: str) -> Any:
    app = state.installed_apps.get(app_id)
    if app is None:
        raise HTTPException(status_code=404, detail="Installed app not found.")
    return app


def _recipe(app_id: str, body: dict[str, Any]) -> BakeRecipe:
    """Read a recipe for an application from a request, checked against what the server holds.

    Raises:
        HTTPException: 422 for a body that is no recipe; 400 for one that names what is not there.
    """
    content = {key: value for key, value in (body or {}).items() if key in BakeRecipe.model_fields and key not in ("app_id", "revision")}
    try:
        recipe = BakeRecipe(app_id=app_id, **content)
    except (ValidationError, TypeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    try:
        bakery.validate(recipe)
    except bakery.RecipeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return recipe


@admin_router.get("")
async def list_recipes(request: Request, user: dict[str, Any] = Depends(verify_admin)) -> dict[str, Any]:
    """Return the recipes with the state of this node's image of each, and of every other node's.

    Returns:
        `node`, this node's name; `available`, whether it builds images;
        `recipes`, each as `bakery.view` gives it; `families`, the
        distribution families a step renders for with the package manager
        of each; and `others`, per other answering node its `node` name and
        the `status` of each recipe's image there keyed by application id.
    """
    recipes = [bakery.view(recipe) for recipe in state.bake_recipes.values()]
    others = [
        {"node": answer.get("node", ""), "status": {item["app_id"]: item["status"] for item in answer.get("recipes", [])}}
        for answer in await routing.gather(request, user)
        if isinstance(answer, dict)
    ]
    return {
        "node": cluster.node_name(),
        "available": bakery.available(),
        "recipes": sorted(recipes, key=lambda item: item["app_name"].lower()),
        "families": {key: family.manager for key, family in bakery.FAMILIES.items()},
        "others": sorted(others, key=lambda item: item["node"].lower()),
    }


@admin_router.get("/base/{app_id}")
async def base_image(app_id: str) -> dict[str, Any]:
    """Return what a recipe of an application builds on: the image and the distribution read from it."""
    _available()
    try:
        return await bakery.base_of(_app(app_id))
    except bakery.RecipeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@admin_router.post("/preview/{app_id}")
async def preview(app_id: str, body: dict[str, Any] = Depends(get_decrypted_request_body)) -> dict[str, Any]:
    """Render a draft of a recipe without keeping it.

    Returns:
        `dockerfile`; `blocks`, the lines of each step; `problems`; `offers`,
        what the guided steps offer on the image (`bakekits.offers`); and for
        a guided draft `portable`, the same text with its `FROM` written
        through `BASE_IMAGE`, which developer mode starts from.
    """
    _available()
    recipe = _recipe(app_id, body)
    try:
        base = await bakery.base_of(_app(app_id))
    except bakery.RecipeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    rendered = bakery.render(recipe, base["image"], base["family"], distro=base["distro"])
    answer: dict[str, Any] = {
        "dockerfile": rendered.dockerfile,
        "blocks": rendered.blocks,
        "problems": rendered.problems,
        "base": base,
        "offers": bakekits.offers(recipe.family or base["family"], base["distro"]),
    }
    if recipe.mode == "guided":
        answer["portable"] = bakery.render(recipe, base["image"], base["family"], portable=True, distro=base["distro"]).dockerfile
    return answer


@admin_router.post("/files", status_code=201)
async def upload_file(
    body: dict[str, Any] = Depends(get_decrypted_request_body), user: dict[str, Any] = Depends(verify_admin)
) -> dict[str, Any]:
    """Keep a chunked upload as a file recipes copy into their images, and return its `name`, `hash`, and `size`."""
    try:
        req = FileUpload(**body)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=f"Invalid request body: {exc}") from exc
    path = await reassemble_file(user["username"], req.upload_id, req.total_chunks)
    try:
        if os.path.getsize(path) > bakery.MAX_FILE_BYTES:
            raise HTTPException(status_code=413, detail=f"A recipe file is at most {bakery.MAX_FILE_BYTES // (1024 * 1024)} MiB.")
        with open(path, "rb") as handle:
            data = handle.read()
    finally:
        if os.path.exists(path):
            os.remove(path)
    try:
        kept = await asyncio.to_thread(bakery.save_file, req.filename, data)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return kept.model_dump()


@admin_router.get("/packages/{app_id}")
async def search_packages(app_id: str, q: str = "", family: str = "", refresh: bool = False) -> dict[str, Any]:
    """Search the packages an application's image can install (see `bakery.packages`)."""
    _available()
    return await bakery.packages(_app(app_id), q[:80], family, refresh)


@admin_router.post("/apps", status_code=201)
async def bake_app(body: dict[str, Any] = Depends(get_decrypted_request_body)) -> dict[str, Any]:
    """Make a baked application: a meta-app of an installed application, with its recipe, and start baking it.

    The body is the recipe with the application's `name`, `base_app_id`, and
    `logo` (base64, or empty for the base's own). Nothing is kept when the
    recipe does not render against the base's image.

    Raises:
        HTTPException: 400 for a base that is not an application as installed, or a recipe that does not
            render; 409 for a name an application already has.
    """
    _available()
    name = str(body.get("name") or "").strip()
    base = _app(str(body.get("base_app_id") or ""))
    if not name:
        raise HTTPException(status_code=400, detail="A baked application has a name.")
    if base.is_meta_app:
        raise HTTPException(
            status_code=400, detail="A baked application is built on an installed application, not on a baked or laboratory one."
        )
    if any(app.name.strip().lower() == name.lower() for app in state.installed_apps.values()):
        raise HTTPException(status_code=409, detail=f"An application named '{name}' exists already.")
    # Checked against the base first, so a recipe that cannot be kept leaves no application behind.
    draft = _recipe(base.id, body)
    try:
        plan = await bakery.plan_for(draft)
    except bakery.RecipeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if plan.rendered.errors:
        raise HTTPException(status_code=400, detail=" ".join(plan.rendered.errors))
    made = await admin.create_meta_app(
        {"name": name, "base_app_id": base.id, "logo": str(body.get("logo") or ""), "users": ["all"], "groups": ["all"]}
    )
    recipe = draft.model_copy(update={"app_id": made.id, "revision": 1})
    state.bake_recipes[made.id] = recipe
    try:
        await bakery.start(recipe, published=True)
        await bakery.save()
    except Exception:
        state.bake_recipes.pop(made.id, None)
        await admin.delete_installed_app(made.id)
        raise
    return bakery.view(recipe)


@admin_router.put("/recipes/{app_id}")
async def save_recipe(app_id: str, body: dict[str, Any] = Depends(get_decrypted_request_body)) -> dict[str, Any]:
    """Replace a baked application's recipe and start baking it on this node; the other nodes follow the record.

    Raises:
        HTTPException: 404 for an application that is not baked; 400 for a recipe that does not render
            against the application's image.
    """
    _available()
    existing = state.bake_recipes.get(app_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="No such baked application.")
    recipe = _recipe(app_id, body)
    recipe.revision = existing.revision + 1
    if recipe.enabled:
        try:
            await bakery.start(recipe, published=True)
        except bakery.RecipeError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    state.bake_recipes[app_id] = recipe
    await bakery.save()
    return bakery.view(recipe)


@admin_router.delete("/recipes/{app_id}", status_code=204)
async def delete_recipe(app_id: str) -> Response:
    """Delete a baked application, recipe and all; a recipe kept on an installed application goes alone.

    Raises:
        HTTPException: 409 while a meta-app is built on the baked application.
    """
    if app_id not in state.bake_recipes:
        raise HTTPException(status_code=404, detail="No such baked application.")
    app = state.installed_apps.get(app_id)
    if app is not None and app.is_meta_app:
        return await admin.delete_installed_app(app_id)
    await bakery.forget(app_id)
    return Response(status_code=204)


@admin_router.post("/recipes/{app_id}/build")
async def rebuild(app_id: str, fresh: bool = False) -> dict[str, Any]:
    """Bake a saved recipe on this node now; `fresh` builds every layer again."""
    _available()
    recipe = state.bake_recipes.get(app_id)
    if recipe is None:
        raise HTTPException(status_code=404, detail="No such recipe.")
    try:
        build = await bakery.start(recipe, published=True, fresh=fresh)
    except bakery.RecipeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return bakery.build_view(build)


@admin_router.get("/recipes/{app_id}/log")
async def recipe_log(app_id: str) -> dict[str, Any]:
    """Return the output of the last build of a saved recipe on this node."""
    if app_id not in state.bake_recipes:
        raise HTTPException(status_code=404, detail="No such recipe.")
    return {"lines": bakery.last_log(app_id)}


@admin_router.post("/builds/{app_id}", status_code=201)
async def try_build(
    app_id: str, body: dict[str, Any] = Depends(get_decrypted_request_body), user: dict[str, Any] = Depends(verify_admin)
) -> dict[str, Any]:
    """Build a draft of a recipe on this node without keeping it or changing what sessions run."""
    _available()
    recipe = _recipe(app_id, body)
    try:
        build = await bakery.start(recipe, published=False, username=user["username"])
    except bakery.RecipeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return bakery.build_view(build)


@admin_router.get("/builds/{build_id}")
async def follow_build(build_id: str, after: int = 0) -> dict[str, Any]:
    """Return a build's state and the lines of its output past `after`."""
    build = bakery.get_build(build_id)
    if build is None:
        raise HTTPException(status_code=404, detail="This node remembers no such build.")
    return bakery.build_view(build, after)


@admin_router.post("/try", response_model=LaunchResponse, dependencies=[Depends(routing.follow_launch)])
async def launch_trial(
    body: dict[str, Any] = Depends(get_decrypted_request_body), user: dict[str, Any] = Depends(verify_admin)
) -> dict[str, str]:
    """Launch a session of the build's application on the image the build made, with a home directory of its own.

    Raises:
        HTTPException: 404 for a build this node does not remember; 409 for one that has no image.
    """
    try:
        req = TrialRequest(**body)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=f"Invalid request body: {exc}") from exc
    build = bakery.get_build(req.build_id)
    if build is None:
        raise HTTPException(status_code=404, detail="This node remembers no such build.")
    if build.state != "ready":
        raise HTTPException(status_code=409, detail="The build has no image to try.")
    return await launch_application(
        application_id=build.app_id,
        username=user["username"],
        effective_settings=user["effective_settings"],
        home_name="cleanroom",
        env_vars={},
        language=req.language,
        selected_gpu=req.selected_gpu,
        wayland_mode=req.wayland_mode,
        timezone=req.timezone,
        auth_user=user,
        baked_image=build.tag,
    )
