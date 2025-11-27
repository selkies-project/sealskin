import secrets
import logging
import asyncio
import os
import random
import json
import time
import base64
from uuid import UUID
from typing import Optional

import httpx
from fastapi import APIRouter, HTTPException, Request, Query
from fastapi.responses import RedirectResponse, HTMLResponse, FileResponse
from starlette.websockets import WebSocket, WebSocketDisconnect, WebSocketState

from . import api
from .settings import settings

logger = logging.getLogger(__name__)
router = APIRouter()
ROOM_CONNECTIONS: dict = {}


@router.get("/room/room.css", include_in_schema=False)
async def get_room_css():
    path = os.path.join(
        os.path.dirname(__file__), "static", "collaboration", "room.css"
    )
    if os.path.exists(path):
        return FileResponse(path, media_type="text/css")
    raise HTTPException(status_code=404)


@router.get("/room/room.js", include_in_schema=False)
async def get_room_js():
    path = os.path.join(os.path.dirname(__file__), "static", "collaboration", "room.js")
    if os.path.exists(path):
        return FileResponse(path, media_type="application/javascript")
    raise HTTPException(status_code=404)


@router.get("/room/translation.js", include_in_schema=False)
async def get_translation_js():
    path = os.path.join(
        os.path.dirname(__file__), "static", "collaboration", "translation.js"
    )
    if os.path.exists(path):
        return FileResponse(path, media_type="application/javascript")
    raise HTTPException(status_code=404)


@router.get("/room/{session_id:uuid}", response_class=HTMLResponse)
async def collaborative_room(
    request: Request,
    session_id: UUID,
    collab_token: Optional[str] = Query(None, alias="token"),
):
    session_id_str = str(session_id)
    session_data = api.SESSIONS_DB.get(session_id_str)
    if not session_data or not session_data.get("is_collaboration"):
        raise HTTPException(status_code=404, detail="Collaboration room not found.")

    if (
        "viewer_token" in session_data
        and "participant_invite_token" not in session_data
    ):
        session_data["participant_invite_token"] = session_data.pop("viewer_token")
    if "readonly_invite_token" not in session_data:
        session_data["readonly_invite_token"] = secrets.token_urlsafe(16)

    main_access_token = request.query_params.get("access_token") or request.cookies.get(
        settings.session_cookie_name
    )
    is_controller_by_session = main_access_token and secrets.compare_digest(
        main_access_token, session_data.get("access_token", "")
    )

    is_controller_by_collab = collab_token == session_data.get("controller_token")
    is_viewer_by_collab = any(
        v["token"] == collab_token for v in session_data.get("viewers", [])
    )
    is_new_participant_by_collab = collab_token == session_data.get(
        "participant_invite_token"
    )
    is_new_readonly_by_collab = collab_token == session_data.get(
        "readonly_invite_token"
    )

    if not (
        is_controller_by_session
        or is_controller_by_collab
        or is_viewer_by_collab
        or is_new_participant_by_collab
        or is_new_readonly_by_collab
    ):
        raise HTTPException(
            status_code=401, detail="Invalid or missing authentication token."
        )

    try:
        room_template_path = os.path.join(
            os.path.dirname(__file__), "static", "collaboration", "room.html"
        )
        with open(room_template_path, "r") as f:
            html_content = f.read()
    except FileNotFoundError:
        logger.error(f"Collaboration room template not found at {room_template_path}")
        raise HTTPException(status_code=500, detail="Room UI template is missing.")

    master_token = session_data["master_token"]
    controller_token = session_data["controller_token"]

    user_role = "none"
    user_token = None
    user_permission = "participant"

    if is_controller_by_session or is_controller_by_collab:
        user_role = "controller"
        user_token = controller_token
    elif is_viewer_by_collab:
        user_role = "viewer"
        user_token = collab_token
        viewer_data = next(
            (v for v in session_data.get("viewers", []) if v["token"] == collab_token),
            None,
        )
        if viewer_data:
            user_permission = viewer_data.get("permission", "participant")
    elif is_new_participant_by_collab or is_new_readonly_by_collab:
        permission = "participant" if is_new_participant_by_collab else "readonly"
        new_viewer_token = secrets.token_urlsafe(16)
        session_data.setdefault("viewers", []).append(
            {
                "token": new_viewer_token,
                "slot": None,
                "mk_control": False,
                "username": f"User-{random.randint(100, 999)}",
                "permission": permission,
            }
        )

        mk_owner = session_data.get("mk_owner_token")

        try:
            await broadcast_token_state(session_data)
            api.SESSIONS_DB[session_id_str] = session_data
            await api.save_sessions_to_disk()
            logger.info(
                f"[{session_id_str}] New viewer ({permission}) joined. Token '{new_viewer_token}' created and pushed."
            )
        except Exception as e:
            logger.error(
                f"[{session_id_str}] Failed to update downstream tokens for new viewer: {e}"
            )
            raise HTTPException(
                status_code=500, detail="Failed to register new viewer."
            )

        redirect_url = request.url.replace_query_params(token=new_viewer_token)
        return RedirectResponse(url=str(redirect_url))
    else:
        return HTMLResponse(
            content="<h1>Invalid or expired collaboration link.</h1>", status_code=403
        )

    iframe_src = f"/{session_id_str}/?token={user_token}"

    client_data = {
        "sessionId": session_id_str,
        "userRole": user_role,
        "userToken": user_token,
        "userPermission": user_permission,
    }

    if user_role == "controller":
        client_data["participantJoinUrl"] = str(
            request.url.replace_query_params(
                token=session_data["participant_invite_token"]
            )
        )
        client_data["readonlyJoinUrl"] = str(
            request.url.replace_query_params(
                token=session_data["readonly_invite_token"]
            )
        )
    elif user_role == "viewer" and user_permission == "participant":
        client_data["readonlyJoinUrl"] = str(
            request.url.replace_query_params(
                token=session_data["readonly_invite_token"]
            )
        )

    html_content = html_content.replace("{{IFRAME_SRC}}", iframe_src)
    html_content = html_content.replace(
        "<!-- CLIENT_DATA -->",
        f"<script>window.COLLAB_DATA = {json.dumps(client_data)};</script>",
    )
    response = HTMLResponse(content=html_content)

    initial_auth_token = request.query_params.get("access_token")
    current_collab_token = user_token

    if initial_auth_token:
        logger.info(f"[{session_id_str}] Collab Room: setting unique session cookie.")
        response.set_cookie(
            key=f"{settings.session_cookie_name}_{session_id_str}",
            value=initial_auth_token,
            path=f"/{session_id_str}",
            httponly=True,
            secure=True,
            samesite="lax",
        )

    if current_collab_token:
        logger.info(
            f"[{session_id_str}] Collab Room: setting collab token cookie for iframe."
        )
        response.set_cookie(
            key=f"collab_token_{session_id_str}",
            value=current_collab_token,
            path=f"/{session_id_str}",
            httponly=True,
            secure=True,
            samesite="none",
        )

    return response


async def broadcast_token_state(session_data: dict):
    mk_owner = session_data.get("mk_owner_token")
    controller_token = session_data.get("controller_token")

    all_tokens = {
        controller_token: {
            "role": "controller",
            "slot": session_data.get("controller_slot"),
            "mk_control": (mk_owner == controller_token) if mk_owner else True,
        }
    }
    for v in session_data.get("viewers", []):
        all_tokens[v["token"]] = {
            "role": "viewer",
            "slot": v["slot"],
            "mk_control": v["token"] == mk_owner,
        }

    target_ips = set()
    if session_data.get("ip"): target_ips.add(session_data["ip"])
    for c in session_data.get("container_registry", {}).values():
        if c.get("ip"): target_ips.add(c["ip"])

    async with httpx.AsyncClient(timeout=1.0) as client:
        for ip in target_ips:
            try:
                await client.post(
                    f"http://{ip}:8083/tokens",
                    json=all_tokens,
                    headers={"Authorization": f"Bearer {session_data['master_token']}"},
                )
            except Exception:
                pass


async def broadcast_to_room(session_id: str, payload: dict):
    connections = ROOM_CONNECTIONS.get(session_id)
    if not connections:
        return

    all_ws = []
    if connections.get("controller"):
        all_ws.append(connections["controller"]["websocket"])

    for viewer_conn in connections.get("viewers", {}).values():
        all_ws.append(viewer_conn["websocket"])

    send_tasks = [
        ws.send_json(payload)
        for ws in all_ws
        if ws.client_state == WebSocketState.CONNECTED
    ]
    results = await asyncio.gather(*send_tasks, return_exceptions=True)

    for i, result in enumerate(results):
        if isinstance(result, Exception):
            logger.warning(
                f"[{session_id}] Failed to send message to a client: {result}"
            )


async def broadcast_binary_to_room(
    session_id: str, payload: bytes, sender_ws: WebSocket
):
    connections = ROOM_CONNECTIONS.get(session_id)
    if not connections:
        return

    all_ws = []
    if (
        connections.get("controller")
        and connections["controller"]["websocket"] != sender_ws
    ):
        all_ws.append(connections["controller"]["websocket"])

    for viewer_conn in connections.get("viewers", {}).values():
        if viewer_conn["websocket"] != sender_ws:
            all_ws.append(viewer_conn["websocket"])

    send_tasks = []
    for ws in all_ws:
        if ws.client_state == WebSocketState.CONNECTED:
            send_tasks.append(ws.send_bytes(payload))

    if send_tasks:
        results = await asyncio.gather(*send_tasks, return_exceptions=True)
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                logger.warning(
                    f"[{session_id}] Failed to send binary message to a client: {result}"
                )


@router.websocket("/ws/room/{session_id:uuid}")
async def room_websocket(websocket: WebSocket, session_id: UUID):
    session_id_str = str(session_id)
    token = websocket.query_params.get("token")

    session_data = api.SESSIONS_DB.get(session_id_str)
    if not session_data or not session_data.get("is_collaboration"):
        await websocket.close(code=1008)
        return

    is_controller = token == session_data.get("controller_token")
    viewer_data_ref = next(
        (v for v in session_data.get("viewers", []) if v["token"] == token), None
    )
    is_viewer = viewer_data_ref is not None

    if not is_controller and not is_viewer:
        await websocket.close(code=1008)
        return

    await websocket.accept()

    if session_id_str not in ROOM_CONNECTIONS:
        ROOM_CONNECTIONS[session_id_str] = {"controller": None, "viewers": {}}

    username = "Controller"
    if is_viewer:
        username = viewer_data_ref.get("username", f"User-{token[:6]}")

    public_id = secrets.token_hex(4)
    connection_info = {
        "websocket": websocket,
        "username": username,
        "token": token,
        "public_id": public_id,
        "has_joined": False,
    }
    if is_controller:
        ROOM_CONNECTIONS[session_id_str]["controller"] = connection_info
        join_payload = {
            "type": "user_joined",
            "username": "Controller",
            "timestamp": int(time.time() * 1000),
        }
        await broadcast_to_room(session_id_str, join_payload)
        connection_info["has_joined"] = True
        await broadcast_state(session_id_str)
    else:
        ROOM_CONNECTIONS[session_id_str]["viewers"][token] = connection_info
        join_payload = {
            "type": "user_joined",
            "username": username,
            "timestamp": int(time.time() * 1000),
        }
        await broadcast_to_room(session_id_str, join_payload)
        connection_info["has_joined"] = True
        await broadcast_state(session_id_str)

    async def send_app_list():
        user_apps = []
        session_registry = session_data.get("container_registry", {})
        
        for app in api.INSTALLED_APPS.values():
            if "all" in app.users or session_data.get("username") in app.users:
                    logo_src = app.logo
                    if logo_src and logo_src.startswith("/api/app_icon/"):
                        try:
                            icon_path = os.path.join(settings.app_icons_path, f"{app.id}.png")
                            if os.path.exists(icon_path):
                                with open(icon_path, "rb") as f:
                                    b64_data = base64.b64encode(f.read()).decode('utf-8')
                                    logo_src = f"data:image/png;base64,{b64_data}"
                        except Exception as e:
                            logger.warning(f"Failed to load icon for app {app.id}: {e}")

                    user_apps.append({
                        "id": app.id,
                        "name": app.name,
                        "running": app.id in session_registry,
                        "active": app.id == session_data.get("provider_app_id"),
                        "logo": logo_src
                    })
        
        await websocket.send_json({
            "type": "app_list",
            "apps": sorted(user_apps, key=lambda x: x['name'])
        })

    async def handle_restart_app(target_app_id):
        try:
            await api.stop_container_in_session(session_id_str, target_app_id)
            container_info = await api.ensure_container_for_session(session_id_str, target_app_id)
            
            if target_app_id == session_data.get("provider_app_id"):
                session_data["instance_id"] = container_info["instance_id"]
                session_data["ip"] = container_info["ip"]
                session_data["port"] = container_info["port"]
                api.SESSIONS_DB[session_id_str] = session_data
                await api.save_sessions_to_disk()
                
                await broadcast_to_room(session_id_str, {
                    "type": "app_swapped",
                    "app_name": session_data.get("app_name"),
                    "timestamp": int(time.time() * 1000)
                })
        except Exception as e:
            logger.error(f"Restart failed: {e}")
            if websocket.client_state == WebSocketState.CONNECTED:
                await websocket.send_json({"type": "error", "message": "Failed to restart application."})

    async def handle_swap_app(target_app_id):
        try:
            container_info = await api.ensure_container_for_session(session_id_str, target_app_id)
            app_config = api.INSTALLED_APPS.get(target_app_id)
            
            session_data["instance_id"] = container_info["instance_id"]
            session_data["ip"] = container_info["ip"]
            session_data["port"] = container_info["port"]
            session_data["provider_app_id"] = target_app_id
            session_data["app_name"] = app_config.name
            session_data["app_logo"] = app_config.logo

            await broadcast_token_state(session_data)                            
            api.SESSIONS_DB[session_id_str] = session_data
            await api.save_sessions_to_disk()
            
            await broadcast_to_room(session_id_str, {
                "type": "app_swapped",
                "app_name": app_config.name,
                "timestamp": int(time.time() * 1000)
            })
        except Exception as e:
            logger.error(f"Swap failed: {e}")
            if websocket.client_state == WebSocketState.CONNECTED:
                await websocket.send_json({"type": "error", "message": "Failed to swap application."})

    try:
        while True:
            message = await websocket.receive()
            if "text" in message:
                data = json.loads(message["text"])
                action = data.get("action")

                data["sender_token"] = token

                if action == "assign_slot" and is_controller:
                    viewer_token = data.get("viewer_token")
                    slot = data.get("slot")
                    await handle_assign_slot(session_id_str, viewer_token, slot)
                elif action == "assign_mk" and is_controller:
                    target_token = data.get("token")
                    await handle_assign_mk(session_id_str, target_token)
                elif action == "set_designated_speaker" and is_controller:
                    speaker_token = data.get("token")
                    session_data["designated_speaker"] = speaker_token
                    api.SESSIONS_DB[session_id_str] = session_data
                    await api.save_sessions_to_disk()
                    logger.info(
                        f"[{session_id_str}] Designated speaker set to: {speaker_token}"
                    )
                    await broadcast_state(session_id_str)
                elif action == "set_username" and is_viewer:
                    now = time.time()
                    last_change = connection_info.get("last_username_change", 0)
                    if now - last_change < 2.0:
                        continue
                    new_username = data.get("username", "").strip()
                    if (
                        new_username
                        and viewer_data_ref
                        and 1 <= len(new_username) <= 25
                    ):
                        old_username = viewer_data_ref.get("username")
                        viewer_data_ref["username"] = new_username
                        if old_username == new_username:
                            continue

                        viewer_data_ref["username"] = new_username
                        connection_info["last_username_change"] = now
                        username = new_username
                        api.SESSIONS_DB[session_id_str] = session_data
                        await api.save_sessions_to_disk()
                        logger.info(
                            f"[{session_id_str}] Viewer {token[:6]} changed name from '{old_username}' to '{new_username}'."
                        )

                        change_payload = {
                            "type": "username_changed",
                            "old_username": old_username,
                            "new_username": new_username,
                            "timestamp": int(time.time() * 1000),
                        }
                        await broadcast_to_room(session_id_str, change_payload)

                        await broadcast_state(session_id_str)

                elif action == "send_chat_message":
                    message_text = data.get("message", "").strip()
                    if message_text and 1 <= len(message_text) <= 500:
                        chat_payload = {
                            "type": "chat_message",
                            "sender": username,
                            "message": message_text,
                            "timestamp": int(time.time() * 1000),
                            "messageId": f"{int(time.time() * 1000)}-{secrets.token_hex(4)}",
                            "replyTo": data.get("replyTo"),
                        }
                        await broadcast_to_room(session_id_str, chat_payload)

                elif action in ["video_state", "audio_state"]:
                    await broadcast_to_room(
                        session_id_str, {"type": "control", "payload": data}
                    )

                elif action == "get_apps" and is_controller:
                    await send_app_list()

                elif action == "stop_app" and is_controller:
                    target_app_id = data.get("app_id")
                    if target_app_id and target_app_id != session_data.get("provider_app_id"):
                        await api.stop_container_in_session(session_id_str, target_app_id)
                        await send_app_list()

                elif action == "restart_app" and is_controller:
                    target_app_id = data.get("app_id")
                    if target_app_id:
                        asyncio.create_task(handle_restart_app(target_app_id))

                elif action == "swap_app" and is_controller:
                    target_app_id = data.get("app_id")
                    if target_app_id:
                        asyncio.create_task(handle_swap_app(target_app_id))

            elif "bytes" in message:
                binary_data = message["bytes"]
                if is_viewer and viewer_data_ref.get("permission") == "readonly":
                    logger.warning(
                        f"[{session_id_str}] Received binary packet from read-only user {token[:6]}, discarding."
                    )
                    continue

                if len(binary_data) > (1024 * 1024):
                    logger.warning(
                        f"[{session_id_str}] Received oversized binary packet ({len(binary_data)} bytes) from {token[:6]}, discarding."
                    )
                    continue

                designated_speaker = session_data.get("designated_speaker")
                is_audio_packet = binary_data[0] == 0x02
                if (
                    designated_speaker
                    and is_audio_packet
                    and token != designated_speaker
                ):
                    continue

                await broadcast_binary_to_room(session_id_str, binary_data, websocket)

    except (WebSocketDisconnect, RuntimeError):
        logger.info(
            f"[{session_id_str}] WebSocket disconnected for {username} ({token[:6]})."
        )
    except Exception as e:
        logger.error(
            f"[{session_id_str}] Unhandled exception in websocket handler for {username} ({token[:6]}): {e}",
            exc_info=True,
        )
    finally:
        current_username = connection_info.get("username")
        has_joined = connection_info.get("has_joined")

        if is_controller:
            if ROOM_CONNECTIONS.get(session_id_str):
                ROOM_CONNECTIONS[session_id_str]["controller"] = None
            logger.info(f"[{session_id_str}] Controller disconnected from collab room.")
            await broadcast_to_room(session_id_str, {"type": "controller_disconnected"})
        else:
            if ROOM_CONNECTIONS.get(session_id_str) and ROOM_CONNECTIONS[
                session_id_str
            ].get("viewers"):
                ROOM_CONNECTIONS[session_id_str]["viewers"].pop(token, None)
            logger.info(
                f"[{session_id_str}] Viewer {token[:6]} disconnected from collab room."
            )

            session_data = api.SESSIONS_DB.get(session_id_str)
            viewer_removed = False
            mk_reverted = False
            if session_data:
                if session_data.get("designated_speaker") == token:
                    session_data["designated_speaker"] = None

                if "viewers" in session_data:
                    disconnected_viewer = next(
                        (
                            v
                            for v in session_data.get("viewers", [])
                            if v.get("token") == token
                        ),
                        None,
                    )
                    if disconnected_viewer:
                        assigned_slot = disconnected_viewer.get("slot")
                        if assigned_slot:
                            username_for_msg = disconnected_viewer.get(
                                "username", "A user"
                            )
                            notification_payload = {
                                "type": "gamepad_change",
                                "message": f"{username_for_msg} disconnected and was unassigned from Gamepad {assigned_slot}.",
                                "timestamp": int(time.time() * 1000),
                            }
                            await broadcast_to_room(
                                session_id_str, notification_payload
                            )

                    if session_data.get("mk_owner_token") == token:
                        session_data["mk_owner_token"] = None
                        mk_reverted = True
                        await broadcast_to_room(
                            session_id_str,
                            {
                                "type": "mk_change",
                                "message": f"{disconnected_viewer.get('username', 'User')} disconnected. MK control reverted to Controller.",
                                "timestamp": int(time.time() * 1000),
                            },
                        )

                    initial_count = len(session_data["viewers"])
                    session_data["viewers"] = [
                        v for v in session_data["viewers"] if v.get("token") != token
                    ]
                    if len(session_data["viewers"]) < initial_count:
                        viewer_removed = True

                if viewer_removed:
                    logger.info(
                        f"[{session_id_str}] Removed disconnected viewer {token[:6]} from session database."
                    )
                    api.SESSIONS_DB[session_id_str] = session_data
                    await api.save_sessions_to_disk()

                    try:
                        await broadcast_token_state(session_data)
                        logger.info(
                            f"[{session_id_str}] Pushed token update to downstream after viewer disconnect."
                        )
                    except Exception as e:
                        logger.error(
                            f"[{session_id_str}] Failed to push token update after viewer disconnect: {e}"
                        )

        if has_joined:
            leave_payload = {
                "type": "user_left",
                "username": current_username,
                "timestamp": int(time.time() * 1000),
            }
            await broadcast_to_room(session_id_str, leave_payload)

        await broadcast_state(session_id_str)

        if (
            ROOM_CONNECTIONS.get(session_id_str)
            and not ROOM_CONNECTIONS[session_id_str].get("controller")
            and not ROOM_CONNECTIONS[session_id_str].get("viewers")
        ):
            ROOM_CONNECTIONS.pop(session_id_str, None)
            logger.info(
                f"[{session_id_str}] Collab room is now empty and has been cleaned up."
            )


async def broadcast_state(session_id: str):
    connections = ROOM_CONNECTIONS.get(session_id)
    session_data = api.SESSIONS_DB.get(session_id)
    if not connections or not session_data:
        return

    controller_info = {
        "token": session_data.get("controller_token"),
        "username": "Controller",
        "slot": session_data.get("controller_slot"),
        "online": connections.get("controller") is not None,
        "has_mk": (session_data.get("mk_owner_token") == session_data.get("controller_token")) or (session_data.get("mk_owner_token") is None),
        "permission": "controller",
        "publicId": connections["controller"]["public_id"]
        if connections.get("controller")
        else None,
    }

    online_viewer_tokens = set(connections.get("viewers", {}).keys())
    users_with_status = [controller_info]
    for v in session_data.get("viewers", []):
        viewer_info = v.copy()
        is_online = v["token"] in online_viewer_tokens
        viewer_info["has_mk"] = session_data.get("mk_owner_token") == v["token"]
        viewer_info["online"] = is_online
        if is_online:
            viewer_conn = connections["viewers"].get(v["token"])
            if viewer_conn:
                viewer_info["publicId"] = viewer_conn.get("public_id")
        users_with_status.append(viewer_info)

    state_payload = {
        "type": "state_update",
        "viewers": users_with_status,
        "designated_speaker": session_data.get("designated_speaker"),
    }
    await broadcast_to_room(session_id, state_payload)


async def handle_assign_slot(session_id: str, viewer_token: str, slot: Optional[int]):
    session_data = api.SESSIONS_DB.get(session_id)
    if not session_data:
        return

    target_user = None
    target_username = "Unknown"
    old_slot_for_target = None

    if viewer_token == session_data.get("controller_token"):
        target_user = session_data
        target_username = "Controller"
        old_slot_for_target = session_data.get("controller_slot")
    else:
        for v in session_data.get("viewers", []):
            if v["token"] == viewer_token:
                target_user = v
                target_username = v.get("username", "Unnamed")
                old_slot_for_target = v.get("slot")
                break

    if not target_user:
        logger.warning(
            f"[{session_id}] Attempted to assign slot to non-existent user token."
        )
        return

    notifications = []

    if slot is not None:
        previous_owner_cleared = False
        if (
            session_data.get("controller_slot") == slot
            and session_data.get("controller_token") != viewer_token
        ):
            session_data["controller_slot"] = None
            notifications.append(f"Controller was unassigned from Gamepad {slot}.")
            previous_owner_cleared = True

        if not previous_owner_cleared:
            for v in session_data.get("viewers", []):
                if v.get("slot") == slot and v.get("token") != viewer_token:
                    v["slot"] = None
                    notifications.append(
                        f"{v.get('username', 'Unnamed')} was unassigned from Gamepad {slot}."
                    )
                    break

    if "is_collaboration" in target_user:
        target_user["controller_slot"] = slot
    else:
        target_user["slot"] = slot

    if slot is not None and old_slot_for_target != slot:
        notifications.append(f"Gamepad {slot} was assigned to {target_username}.")
    elif slot is None and old_slot_for_target is not None:
        notifications.append(
            f"{target_username} was unassigned from Gamepad {old_slot_for_target}."
        )

    try:
        await broadcast_token_state(session_data)
        api.SESSIONS_DB[session_id] = session_data
        await api.save_sessions_to_disk()
        logger.info(
            f"[{session_id}] Successfully assigned slot {slot} to user {viewer_token[:6]} and pushed update."
        )

        for msg in notifications:
            notification_payload = {
                "type": "gamepad_change",
                "message": msg,
                "timestamp": int(time.time() * 1000),
            }
            await broadcast_to_room(session_id, notification_payload)

        await broadcast_state(session_id)
    except Exception as e:
        logger.error(
            f"[{session_id}] Failed to update downstream tokens for slot assignment: {e}"
        )


async def handle_assign_mk(session_id: str, target_token: Optional[str]):
    session_data = api.SESSIONS_DB.get(session_id)
    if not session_data:
        return

    if target_token == session_data.get("controller_token"):
        target_token = None

    current_owner = session_data.get("mk_owner_token")
    if current_owner == target_token:
        return

    session_data["mk_owner_token"] = target_token

    username = "Controller"
    if target_token:
        for v in session_data.get("viewers", []):
            if v["token"] == target_token:
                username = v.get("username", "User")
                break

    try:
        await broadcast_token_state(session_data)
        api.SESSIONS_DB[session_id] = session_data
        await api.save_sessions_to_disk()

        msg = f"Mouse & Keyboard control assigned to {username}."
        await broadcast_to_room(
            session_id,
            {
                "type": "mk_change",
                "message": msg,
                "timestamp": int(time.time() * 1000),
            },
        )
        await broadcast_state(session_id)
    except Exception as e:
        logger.error(f"[{session_id}] Failed to assign MK control: {e}")
