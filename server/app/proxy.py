import secrets
import logging
import asyncio
import os
import hashlib
import base64
import time
import yaml

import httpx
import websockets
from contextlib import asynccontextmanager
from starlette.websockets import WebSocket, WebSocketDisconnect
from fastapi import FastAPI, Depends, HTTPException, Request, Response, Form

from fastapi.responses import RedirectResponse, StreamingResponse, FileResponse, HTMLResponse

from .api import SESSIONS_DB
from .models import PublicShareMetadata
from .settings import settings

logger = logging.getLogger(__name__)
from pydantic import ValidationError
DOWNLOAD_TOKENS: dict = {}

def _load_shares_from_file() -> dict:
    metadata_path = settings.public_shares_metadata_path
    if not os.path.exists(metadata_path):
        logger.warning(f"[PROXY_LOAD] Metadata file not found at '{metadata_path}'.")
        return {}

    try:
        with open(metadata_path, "r") as f:
            data = yaml.safe_load(f) or {}
            
        parsed_shares = {
            share_id: PublicShareMetadata(**metadata)
            for share_id, metadata in data.items()
        }
        return parsed_shares
    except (IOError, yaml.YAMLError, ValidationError) as e:
        logger.error(f"[PROXY_LOAD] Error reading or parsing shares metadata file: {e}")
        return {}

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Proxy server starting up...")
    async with httpx.AsyncClient() as client:
        app.state.http_client = client
        yield
    logger.info("Proxy server shutting down...")


proxy_app = FastAPI(title="SealSkin Session Proxy", lifespan=lifespan)

@proxy_app.get("/public/download/{token}")
async def download_shared_file(token: str):
    token_data = DOWNLOAD_TOKENS.pop(token, None)
    if not token_data or token_data.get("expires_at", 0) < time.time():
        raise HTTPException(status_code=403, detail="Invalid or expired download token.")
    
    share_id = token_data.get("share_id")
    all_shares = _load_shares_from_file()
    metadata = all_shares.get(share_id)
    if not metadata:
        raise HTTPException(status_code=404, detail="Shared file not found.")

    file_path = os.path.join(settings.public_storage_path, share_id)
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="Shared file not found on disk.")

    return FileResponse(
        path=file_path,
        filename=metadata.original_filename,
        media_type="application/octet-stream"
    )

@proxy_app.get("/public/{share_id}")
async def access_public_share_get(share_id: str, request: Request):
    all_shares = _load_shares_from_file()
    metadata = all_shares.get(share_id)
    if not metadata:
        raise HTTPException(status_code=404, detail="Share not found.")

    if metadata.expiry_timestamp and metadata.expiry_timestamp < time.time():
        return HTMLResponse(content="<h1>This link has expired.</h1>", status_code=410)

    if metadata.password_hash:
        password_page_path = os.path.join(os.path.dirname(__file__), "static", "public_password.html")
        if os.path.exists(password_page_path):
            with open(password_page_path, "r") as f:
                html_content = f.read()
            html_content = html_content.replace("{{SHARE_ID}}", share_id).replace("{{ERROR_MESSAGE}}", "")
            return HTMLResponse(content=html_content)
        else:
            return HTMLResponse(content="<h1>Password protected</h1><p>Error: Password page template not found.</p>", status_code=500)

    file_path = os.path.join(settings.public_storage_path, share_id)
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="Shared file not found on disk.")

    return FileResponse(
        path=file_path,
        filename=metadata.original_filename,
        media_type="application/octet-stream"
    )

@proxy_app.post("/public/{share_id}")
async def access_public_share_post(share_id: str, password: str = Form(...)):
    all_shares = _load_shares_from_file()
    metadata = all_shares.get(share_id)
    if not metadata: raise HTTPException(status_code=404, detail="Share not found.")
    if metadata.expiry_timestamp and metadata.expiry_timestamp < time.time():
        return HTMLResponse(content="<h1>This link has expired.</h1>", status_code=410)
    if not metadata.password_hash: raise HTTPException(status_code=400, detail="This share is not password protected.")

    submitted_hash = hashlib.sha256(password.encode()).hexdigest()
    if secrets.compare_digest(submitted_hash, metadata.password_hash):
        token = secrets.token_urlsafe(32)
        DOWNLOAD_TOKENS[token] = {"share_id": share_id, "expires_at": time.time() + 60}
        return RedirectResponse(url=f"/public/download/{token}", status_code=303)
    else:
        password_page_path = os.path.join(os.path.dirname(__file__), "static", "public_password.html")
        if os.path.exists(password_page_path):
            with open(password_page_path, "r") as f:
                html_content = f.read()
            html_content = html_content.replace("{{SHARE_ID}}", share_id).replace("{{ERROR_MESSAGE}}", "Incorrect password. Please try again.")
            return HTMLResponse(content=html_content, status_code=401)
        else:
            return HTMLResponse(content="<h1>Incorrect Password</h1>", status_code=401)

async def get_http_session(session_id: str, request: Request) -> dict:
    token = request.query_params.get("access_token") or request.cookies.get(
        settings.session_cookie_name
    )
    if not token:
        raise HTTPException(status_code=401, detail="Authentication token missing.")

    session = SESSIONS_DB.get(session_id)
    if not session or not secrets.compare_digest(
        token, session.get("access_token", "")
    ):
        raise HTTPException(
            status_code=403, detail="Forbidden: Invalid session or token."
        )

    return session


async def get_websocket_session(session_id: str, websocket: WebSocket) -> dict:
    token = websocket.query_params.get("access_token") or websocket.cookies.get(
        settings.session_cookie_name
    )
    if not token:
        await websocket.close(code=1008, reason="Authentication token missing.")
        raise WebSocketDisconnect(code=1008)

    session = SESSIONS_DB.get(session_id)
    if not session or not secrets.compare_digest(
        token, session.get("access_token", "")
    ):
        await websocket.close(code=1008, reason="Forbidden: Invalid session or token.")
        raise WebSocketDisconnect(code=1008)

    return session


@proxy_app.websocket("/{session_id:str}/{path:path}")
async def websocket_proxy(
    websocket: WebSocket,
    session_id: str,
    path: str,
    session: dict = Depends(get_websocket_session),
):
    await websocket.accept()
    target_ip = session["ip"]
    target_port = session["port"]

    query_params = [
        f"{k}={v}" for k, v in websocket.query_params.items() if k != "access_token"
    ]
    target_path = f"/{session_id}/{path}"
    uri = f"ws://{target_ip}:{target_port}{target_path}"
    if query_params:
        uri += f"?{'&'.join(query_params)}"

    additional_headers = {}
    if "custom_user" in session and "password" in session:
        auth_str = f"{session['custom_user']}:{session['password']}"
        auth_b64 = base64.b64encode(auth_str.encode()).decode()
        additional_headers["Authorization"] = f"Basic {auth_b64}"

    try:
        async with websockets.connect(uri, additional_headers=additional_headers) as target_ws:
            logger.info(f"[{session_id}] WS connection opened to {uri}")

            async def client_to_target():
                try:
                    while True:
                        message = await websocket.receive()
                        if message.get("text"):
                            await target_ws.send(message["text"])
                        elif message.get("bytes"):
                            await target_ws.send(message["bytes"])
                except (WebSocketDisconnect, RuntimeError):
                    pass

            async def target_to_client():
                try:
                    async for message in target_ws:
                        if isinstance(message, str):
                            await websocket.send_text(message)
                        elif isinstance(message, bytes):
                            await websocket.send_bytes(message)
                except websockets.exceptions.ConnectionClosed:
                    pass

            await asyncio.gather(client_to_target(), target_to_client())

    except Exception as e:
        logger.warning(f"[{session_id}] WS Proxy Error: {e}")
    finally:
        logger.info(f"[{session_id}] WS proxy connection closed for {uri}")


@proxy_app.api_route(
    "/{session_id:str}/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"]
)
async def http_reverse_proxy(
    request: Request,
    session_id: str,
    path: str,
    session: dict = Depends(get_http_session),
):
    http_client = request.app.state.http_client
    target_ip = session["ip"]
    target_port = session["port"]
    initial_auth_token = request.query_params.get("access_token")

    if initial_auth_token and request.method == "GET":
        redirect_url = request.url.remove_query_params("access_token")
        response = RedirectResponse(url=str(redirect_url))
        logger.info(
            f"[{session_id}] Initial auth: setting cookie and redirecting to {redirect_url}"
        )
        response.set_cookie(
            key=settings.session_cookie_name,
            value=initial_auth_token,
            httponly=True,
            secure=True,
            samesite="lax",
        )
        return response

    query_params_bytes = "&".join(
        [f"{k}={v}" for k, v in request.query_params.items() if k != "access_token"]
    ).encode("utf-8")
    target_url = httpx.URL(
        scheme="http",
        host=target_ip,
        port=target_port,
        path=f"/{session_id}/{path}",
        query=query_params_bytes,
    )

    req_headers = request.headers.raw
    if "custom_user" in session and "password" in session:
        auth_str = f"{session['custom_user']}:{session['password']}"
        auth_b64 = base64.b64encode(auth_str.encode()).decode()
        req_headers = list(req_headers)
        req_headers.append(
            (b"Authorization", f"Basic {auth_b64}".encode())
        )

    rp_req = http_client.build_request(
        method=request.method,
        url=target_url,
        headers=req_headers,
        content=await request.body(),
    )
    logger.debug(f"[{session_id}] Forwarding {request.method} to {rp_req.url}")

    try:
        rp_resp = await http_client.send(rp_req, stream=True)
        return StreamingResponse(
            rp_resp.aiter_raw(),
            status_code=rp_resp.status_code,
            headers=rp_resp.headers,
            background=rp_resp.aclose,
        )
    except httpx.ConnectError as e:
        logger.error(f"[{session_id}] Cannot connect to backend: {e}")
        return Response(
            status_code=502,
            content="Bad Gateway: Cannot connect to application container.",
        )
