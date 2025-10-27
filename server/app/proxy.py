import secrets
import logging
import asyncio
import time

import httpx
import websockets
from contextlib import asynccontextmanager
from starlette.websockets import WebSocket, WebSocketDisconnect
from fastapi import FastAPI, Depends, HTTPException, Request, Response
from fastapi.responses import RedirectResponse, StreamingResponse

from .api import SESSIONS_DB
from .settings import settings

logger = logging.getLogger(__name__)

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Proxy server starting up...")
    async with httpx.AsyncClient() as client:
        app.state.http_client = client
        yield
    logger.info("Proxy server shutting down...")


proxy_app = FastAPI(title="SealSkin Session Proxy", lifespan=lifespan)

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

    try:
        async with websockets.connect(uri) as target_ws:
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

    rp_req = http_client.build_request(
        method=request.method,
        url=target_url,
        headers=request.headers.raw,
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
