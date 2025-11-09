import secrets
import logging
import asyncio
import os
import random
import json
import time
from uuid import UUID
from typing import Optional

import httpx
from fastapi import APIRouter, HTTPException, Request, Query
from fastapi.responses import RedirectResponse, HTMLResponse, FileResponse
from starlette.websockets import WebSocket, WebSocketDisconnect, WebSocketState

from .api import SESSIONS_DB, save_sessions_to_disk
from .settings import settings

logger = logging.getLogger(__name__)

router = APIRouter()

ROOM_CONNECTIONS: dict = {}

@router.get("/room/room.css", include_in_schema=False)
async def get_room_css():
    """Serves the CSS file for the collaboration room."""
    path = os.path.join(os.path.dirname(__file__), "static", "collaboration", "room.css")
    if os.path.exists(path):
        return FileResponse(path, media_type="text/css")
    raise HTTPException(status_code=404)

@router.get("/room/room.js", include_in_schema=False)
async def get_room_js():
    """Serves the JavaScript file for the collaboration room."""
    path = os.path.join(os.path.dirname(__file__), "static", "collaboration", "room.js")
    if os.path.exists(path):
        return FileResponse(path, media_type="application/javascript")
    raise HTTPException(status_code=404)

@router.get("/room/translation.js", include_in_schema=False)
async def get_translation_js():
    """Serves the JavaScript file for translations."""
    path = os.path.join(os.path.dirname(__file__), "static", "collaboration", "translation.js")
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
    session_data = SESSIONS_DB.get(session_id_str)
    if not session_data or not session_data.get("is_collaboration"):
        raise HTTPException(status_code=404, detail="Collaboration room not found.")

    main_access_token = request.query_params.get("access_token") or request.cookies.get(settings.session_cookie_name)
    is_controller_by_session = main_access_token and secrets.compare_digest(main_access_token, session_data.get("access_token", ""))

    is_controller_by_collab = collab_token == session_data.get("controller_token")
    is_viewer_by_collab = any(v['token'] == collab_token for v in session_data.get("viewers", []))
    is_new_viewer_by_collab = collab_token == session_data.get("viewer_token")

    if not (is_controller_by_session or is_controller_by_collab or is_viewer_by_collab or is_new_viewer_by_collab):
        raise HTTPException(status_code=401, detail="Invalid or missing authentication token.")

    try:
        room_template_path = os.path.join(os.path.dirname(__file__), "static", "collaboration", "room.html")
        with open(room_template_path, "r") as f:
            html_content = f.read()
    except FileNotFoundError:
        logger.error(f"Collaboration room template not found at {room_template_path}")
        raise HTTPException(status_code=500, detail="Room UI template is missing.")

    master_token = session_data["master_token"]
    controller_token = session_data["controller_token"]
    generic_viewer_token = session_data["viewer_token"]
    
    user_role = "none"
    user_token = None

    if is_controller_by_session or is_controller_by_collab:
        user_role = "controller"
        user_token = controller_token
    elif is_viewer_by_collab:
        user_role = "viewer"
        user_token = collab_token
    elif is_new_viewer_by_collab:
        new_viewer_token = secrets.token_urlsafe(16)
        session_data.setdefault("viewers", []).append({"token": new_viewer_token, "slot": None, "username": f"User-{random.randint(100, 999)}"})
        
        all_tokens = {
            controller_token: {"role": "controller", "slot": session_data.get("controller_slot")}
        }
        for viewer in session_data["viewers"]:
            all_tokens[viewer["token"]] = {"role": "viewer", "slot": viewer["slot"]}
        
        try:
            async with httpx.AsyncClient() as client:
                await client.post(
                    f"http://{session_data['ip']}:8083/tokens",
                    json=all_tokens,
                    headers={"Authorization": f"Bearer {master_token}"}
                )
            SESSIONS_DB[session_id_str] = session_data
            await save_sessions_to_disk()
            logger.info(f"[{session_id_str}] New viewer joined. Token '{new_viewer_token}' created and pushed.")
        except Exception as e:
            logger.error(f"[{session_id_str}] Failed to update downstream tokens for new viewer: {e}")
            raise HTTPException(status_code=500, detail="Failed to register new viewer.")
            
        redirect_url = request.url.replace_query_params(token=new_viewer_token)
        return RedirectResponse(url=str(redirect_url))
    else:
        return HTMLResponse(content="<h1>Invalid or expired collaboration link.</h1>", status_code=403)
    
    iframe_src = f"/{session_id_str}/?token={user_token}&embedded=true"
    viewer_join_url = str(request.url.replace_query_params(token=generic_viewer_token))
    
    client_data = {
        "sessionId": session_id_str,
        "userRole": user_role,
        "userToken": user_token,
        "viewerJoinUrl": viewer_join_url,
    }

    html_content = html_content.replace("{{IFRAME_SRC}}", iframe_src)
    html_content = html_content.replace(
        "<!-- CLIENT_DATA -->",
        f"<script>window.COLLAB_DATA = {json.dumps(client_data)};</script>"
    ) 
    response = HTMLResponse(content=html_content)
    
    initial_auth_token = request.query_params.get("access_token")
    if initial_auth_token:
        logger.info(f"[{session_id}] Room access auth: setting 'Lax' cookie.")
        response.set_cookie(
            key=settings.session_cookie_name,
            value=initial_auth_token,
            httponly=True,
            secure=True,
            samesite="lax",
        )
    return response

async def broadcast_to_room(session_id: str, payload: dict):
    """Broadcasts a message to all connected clients in a room."""
    connections = ROOM_CONNECTIONS.get(session_id)
    if not connections:
        return

    all_ws = []
    if connections.get('controller'):
        all_ws.append(connections['controller']['websocket'])
    
    for viewer_conn in connections.get('viewers', {}).values():
        all_ws.append(viewer_conn['websocket'])

    send_tasks = [ws.send_json(payload) for ws in all_ws if ws.client_state == WebSocketState.CONNECTED]
    results = await asyncio.gather(*send_tasks, return_exceptions=True)
    
    for i, result in enumerate(results):
        if isinstance(result, Exception):
            logger.warning(f"[{session_id}] Failed to send message to a client: {result}")

async def broadcast_binary_to_room(session_id: str, payload: bytes, sender_ws: WebSocket):
    """Broadcasts a binary message to all connected clients in a room, except the sender."""
    connections = ROOM_CONNECTIONS.get(session_id)
    if not connections:
        return

    all_ws = []
    if connections.get('controller') and connections['controller']['websocket'] != sender_ws:
        all_ws.append(connections['controller']['websocket'])
    
    for viewer_conn in connections.get('viewers', {}).values():
        if viewer_conn['websocket'] != sender_ws:
            all_ws.append(viewer_conn['websocket'])

    send_tasks = []
    for ws in all_ws:
        if ws.client_state == WebSocketState.CONNECTED:
            send_tasks.append(ws.send_bytes(payload))
    
    if send_tasks:
        results = await asyncio.gather(*send_tasks, return_exceptions=True)
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                logger.warning(f"[{session_id}] Failed to send binary message to a client: {result}")

@router.websocket("/ws/room/{session_id:uuid}")
async def room_websocket(websocket: WebSocket, session_id: UUID):
    session_id_str = str(session_id)
    token = websocket.query_params.get("token")
    
    session_data = SESSIONS_DB.get(session_id_str)
    if not session_data or not session_data.get("is_collaboration"):
        await websocket.close(code=1008)
        return

    is_controller = token == session_data.get("controller_token")
    is_viewer = any(v['token'] == token for v in session_data.get("viewers", []))

    if not is_controller and not is_viewer:
        await websocket.close(code=1008)
        return

    await websocket.accept()

    if session_id_str not in ROOM_CONNECTIONS:
        ROOM_CONNECTIONS[session_id_str] = {'controller': None, 'viewers': {}}    
    
    viewer_data_ref = None
    username = "Controller"
    if is_viewer:
        for v in session_data.get("viewers", []):
            if v['token'] == token:
                viewer_data_ref = v
                username = v.get("username", f"User-{token[:6]}")
                break

    connection_info = {'websocket': websocket, 'username': username, 'token': token, 'has_joined': False}
    if is_controller:
        ROOM_CONNECTIONS[session_id_str]['controller'] = connection_info
        join_payload = {"type": "user_joined", "username": "Controller", "timestamp": int(time.time() * 1000)}
        await broadcast_to_room(session_id_str, join_payload)
        connection_info['has_joined'] = True
    else:
        ROOM_CONNECTIONS[session_id_str]['viewers'][token] = connection_info

    await broadcast_state(session_id_str)
    
    try:
        while True:
            message = await websocket.receive()
            if "text" in message:
                data = json.loads(message["text"])
                action = data.get("action")
                
                data['sender_token'] = token

                if action == "assign_slot" and is_controller:
                    viewer_token = data.get("viewer_token")
                    slot = data.get("slot")
                    await handle_assign_slot(session_id_str, viewer_token, slot)
                elif action == "set_username" and is_viewer:
                    new_username = data.get("username", "").strip()
                    if new_username and viewer_data_ref and 1 <= len(new_username) <= 25:
                        old_username = viewer_data_ref.get("username")
                        viewer_data_ref["username"] = new_username
                        connection_info["username"] = new_username 
                        username = new_username
                        SESSIONS_DB[session_id_str] = session_data
                        await save_sessions_to_disk()
                        logger.info(f"[{session_id_str}] Viewer {token[:6]} changed name from '{old_username}' to '{new_username}'.")
                        
                        if not connection_info.get('has_joined'):
                            join_payload = {"type": "user_joined", "username": new_username, "timestamp": int(time.time() * 1000)}
                            await broadcast_to_room(session_id_str, join_payload)
                            connection_info['has_joined'] = True
                        elif old_username != new_username:
                            change_payload = {"type": "username_changed", "old_username": old_username, "new_username": new_username, "timestamp": int(time.time() * 1000)}
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
                            "replyTo": data.get("replyTo")
                        }
                        await broadcast_to_room(session_id_str, chat_payload)
                
                elif action in ["video_state", "audio_state"]:
                    await broadcast_to_room(session_id_str, {"type": "control", "payload": data})

            elif "bytes" in message:
                binary_data = message["bytes"]
                if len(binary_data) > (1024 * 1024): # 1MB limit
                    logger.warning(f"[{session_id_str}] Received oversized binary packet from {token[:6]}, discarding.")
                    continue
                
                sender_token_bytes = token.encode('utf-8')
                token_len = len(sender_token_bytes)
                if token_len > 255: continue
                
                header = bytes([token_len]) + sender_token_bytes
                full_payload = header + binary_data
                await broadcast_binary_to_room(session_id_str, full_payload, websocket)

    except (WebSocketDisconnect, RuntimeError):
        logger.info(f"[{session_id_str}] WebSocket disconnected for {username} ({token[:6]}).")
    except Exception as e:
        logger.error(f"[{session_id_str}] Unhandled exception in websocket handler for {username} ({token[:6]}): {e}", exc_info=True)
    finally:
        current_username = connection_info.get("username")
        has_joined = connection_info.get("has_joined")

        if not is_controller:
            session_data = SESSIONS_DB.get(session_id_str)
            if session_data:
                disconnected_viewer = next((v for v in session_data.get("viewers", []) if v.get("token") == token), None)
                if disconnected_viewer:
                    assigned_slot = disconnected_viewer.get("slot")
                    if assigned_slot:
                        username_for_msg = disconnected_viewer.get('username', 'A user')
                        notification_payload = {
                            "type": "gamepad_change", 
                            "message": f"{username_for_msg} disconnected and was unassigned from Gamepad {assigned_slot}.",
                            "timestamp": int(time.time() * 1000)
                        }
                        await broadcast_to_room(session_id_str, notification_payload)

        if is_controller:
            if ROOM_CONNECTIONS.get(session_id_str):
                ROOM_CONNECTIONS[session_id_str]['controller'] = None
            logger.info(f"[{session_id_str}] Controller disconnected from collab room.")
        else:
            if ROOM_CONNECTIONS.get(session_id_str) and ROOM_CONNECTIONS[session_id_str].get('viewers'):
                ROOM_CONNECTIONS[session_id_str]['viewers'].pop(token, None)
            logger.info(f"[{session_id_str}] Viewer {token[:6]} disconnected from collab room.")
            
            session_data = SESSIONS_DB.get(session_id_str)
            viewer_removed = False
            if session_data and "viewers" in session_data:
                initial_count = len(session_data["viewers"])
                session_data["viewers"] = [v for v in session_data["viewers"] if v.get("token") != token]
                if len(session_data["viewers"]) < initial_count:
                    viewer_removed = True

            if viewer_removed:
                logger.info(f"[{session_id_str}] Removed disconnected viewer {token[:6]} from session database.")
                SESSIONS_DB[session_id_str] = session_data
                await save_sessions_to_disk()

                all_tokens = {
                    session_data["controller_token"]: {"role": "controller", "slot": session_data.get("controller_slot")}
                }
                for v in session_data["viewers"]:
                    all_tokens[v["token"]] = {"role": "viewer", "slot": v["slot"]}

                try:
                    async with httpx.AsyncClient() as client:
                        res = await client.post(
                            f"http://{session_data['ip']}:8083/tokens",
                            json=all_tokens,
                            headers={"Authorization": f"Bearer {session_data['master_token']}"}
                        )
                        res.raise_for_status()
                    logger.info(f"[{session_id_str}] Pushed token update to downstream after viewer disconnect.")
                except Exception as e:
                    logger.error(f"[{session_id_str}] Failed to push token update after viewer disconnect: {e}")

        if has_joined:
            leave_payload = {"type": "user_left", "username": current_username, "timestamp": int(time.time() * 1000)}
            await broadcast_to_room(session_id_str, leave_payload)

        await broadcast_state(session_id_str)

        if ROOM_CONNECTIONS.get(session_id_str) and \
           not ROOM_CONNECTIONS[session_id_str].get('controller') and \
           not ROOM_CONNECTIONS[session_id_str].get('viewers'):
            ROOM_CONNECTIONS.pop(session_id_str, None)
            logger.info(f"[{session_id_str}] Collab room is now empty and has been cleaned up.")

async def broadcast_state(session_id: str):
    """Sends the current state of users to all clients."""
    connections = ROOM_CONNECTIONS.get(session_id)
    session_data = SESSIONS_DB.get(session_id)
    if not connections or not session_data:
        return

    controller_info = {
        "token": session_data.get("controller_token"),
        "username": "Controller",
        "slot": session_data.get("controller_slot"),
        "online": connections.get('controller') is not None
    }

    online_viewer_tokens = set(connections.get('viewers', {}).keys())
    users_with_status = [controller_info]
    for v in session_data.get("viewers", []):
        viewer_info = v.copy()
        viewer_info['online'] = v['token'] in online_viewer_tokens
        users_with_status.append(viewer_info)
        
    state_payload = {
        "type": "state_update",
        "viewers": users_with_status
    }
    await broadcast_to_room(session_id, state_payload)


async def handle_assign_slot(session_id: str, viewer_token: str, slot: Optional[int]):
    """Updates a user's slot, pushes changes to downstream, and broadcasts state."""
    session_data = SESSIONS_DB.get(session_id)
    if not session_data: return

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
        logger.warning(f"[{session_id}] Attempted to assign slot to non-existent user token.")
        return

    notifications = []
    
    if slot is not None:
        previous_owner_cleared = False
        if session_data.get("controller_slot") == slot and session_data.get("controller_token") != viewer_token:
            session_data["controller_slot"] = None
            notifications.append(f"Controller was unassigned from Gamepad {slot}.")
            previous_owner_cleared = True
        
        if not previous_owner_cleared:
            for v in session_data.get("viewers", []):
                if v.get("slot") == slot and v.get("token") != viewer_token:
                    v["slot"] = None
                    notifications.append(f"{v.get('username', 'Unnamed')} was unassigned from Gamepad {slot}.")
                    break

    if 'is_collaboration' in target_user:
        target_user["controller_slot"] = slot
    else:
        target_user["slot"] = slot

    if slot is not None and old_slot_for_target != slot:
        notifications.append(f"Gamepad {slot} was assigned to {target_username}.")
    elif slot is None and old_slot_for_target is not None:
        notifications.append(f"{target_username} was unassigned from Gamepad {old_slot_for_target}.")

    all_tokens = {
        session_data["controller_token"]: {"role": "controller", "slot": session_data.get("controller_slot")}
    }
    for v in session_data["viewers"]:
        all_tokens[v["token"]] = {"role": "viewer", "slot": v["slot"]}

    try:
        async with httpx.AsyncClient() as client:
            res = await client.post( f"http://{session_data['ip']}:8083/tokens", json=all_tokens, headers={"Authorization": f"Bearer {session_data['master_token']}"})
            res.raise_for_status()
        SESSIONS_DB[session_id] = session_data
        await save_sessions_to_disk()
        logger.info(f"[{session_id}] Successfully assigned slot {slot} to user {viewer_token[:6]} and pushed update.")
        
        for msg in notifications:
            notification_payload = {
                "type": "gamepad_change",
                "message": msg,
                "timestamp": int(time.time() * 1000)
            }
            await broadcast_to_room(session_id, notification_payload)

        await broadcast_state(session_id)
    except Exception as e:
        logger.error(f"[{session_id}] Failed to update downstream tokens for slot assignment: {e}")
