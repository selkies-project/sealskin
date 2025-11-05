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
from starlette.websockets import WebSocket, WebSocketDisconnect

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
            controller_token: {"role": "controller", "slot": None}
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

    send_tasks = [ws.send_json(payload) for ws in all_ws]
    results = await asyncio.gather(*send_tasks, return_exceptions=True)
    
    for i, result in enumerate(results):
        if isinstance(result, Exception):
            logger.warning(f"[{session_id}] Failed to send message to a client: {result}")

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

    connection_info = {'websocket': websocket, 'username': username}
    if is_controller:
        ROOM_CONNECTIONS[session_id_str]['controller'] = connection_info
    else:
        ROOM_CONNECTIONS[session_id_str]['viewers'][token] = connection_info

    await broadcast_state(session_id_str)
    try:
        while True:
            data = await websocket.receive_json()

            action = data.get("action")
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
                    await broadcast_state(session_id_str)

            elif action == "send_chat_message":
                message_text = data.get("message", "").strip()
                if message_text and 1 <= len(message_text) <= 500:
                    chat_payload = {
                        "type": "chat_message",
                        "sender": username,
                        "message": message_text,
                        "timestamp": int(time.time() * 1000)
                    }
                    await broadcast_to_room(session_id_str, chat_payload)

    except WebSocketDisconnect:
        if is_controller:
            ROOM_CONNECTIONS[session_id_str]['controller'] = None
            logger.info(f"[{session_id_str}] Controller disconnected from collab room.")
        else:
            ROOM_CONNECTIONS[session_id_str]['viewers'].pop(token, None)
            logger.info(f"[{session_id_str}] Viewer {token[:6]} disconnected from collab room.")
            
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
                    session_data["controller_token"]: {"role": "controller", "slot": None}
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

            await broadcast_state(session_id_str)

        if not ROOM_CONNECTIONS[session_id_str].get('controller') and not ROOM_CONNECTIONS[session_id_str].get('viewers'):
            ROOM_CONNECTIONS.pop(session_id_str, None)
            logger.info(f"[{session_id_str}] Collab room is now empty and has been cleaned up.")

async def broadcast_state(session_id: str):
    """Sends the current state of viewers to the controller."""
    connections = ROOM_CONNECTIONS.get(session_id)
    if not connections or not connections.get('controller'):
        return
    
    session_data = SESSIONS_DB.get(session_id)
    controller_conn = connections['controller']
    if not session_data or not controller_conn:
        return

    online_viewer_tokens = set(connections.get('viewers', {}).keys())
    viewers_with_status = []
    for v in session_data.get("viewers", []):
        viewer_info = v.copy()
        viewer_info['online'] = v['token'] in online_viewer_tokens
        viewers_with_status.append(viewer_info)
        
    state_payload = {
        "type": "state_update",
        "viewers": viewers_with_status
    }
    try:
        await controller_conn['websocket'].send_json(state_payload)
    except Exception as e:
        logger.warning(f"[{session_id}] Failed to broadcast state to controller: {e}")

async def handle_assign_slot(session_id: str, viewer_token: str, slot: Optional[int]):
    """Updates a viewer's slot, pushes changes to downstream, and broadcasts state."""
    session_data = SESSIONS_DB.get(session_id)
    if not session_data: return

    viewer_found = False
    for viewer in session_data.get("viewers", []):
        if viewer["token"] == viewer_token:
            viewer["slot"] = slot
            viewer_found = True
            break
    
    if not viewer_found:
        logger.warning(f"[{session_id}] Attempted to assign slot to non-existent viewer token.")
        return

    all_tokens = {
        session_data["controller_token"]: {"role": "controller", "slot": None}
    }
    for v in session_data["viewers"]:
        all_tokens[v["token"]] = {"role": "viewer", "slot": v["slot"]}

    try:
        async with httpx.AsyncClient() as client:
            res = await client.post( f"http://{session_data['ip']}:8083/tokens", json=all_tokens, headers={"Authorization": f"Bearer {session_data['master_token']}"})
            res.raise_for_status()
        SESSIONS_DB[session_id] = session_data
        await save_sessions_to_disk()
        logger.info(f"[{session_id}] Successfully assigned slot {slot} to viewer {viewer_token[:6]} and pushed update.")
        await broadcast_state(session_id)
    except Exception as e:
        logger.error(f"[{session_id}] Failed to update downstream tokens for slot assignment: {e}")
