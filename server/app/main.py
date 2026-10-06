"""Server entry point: generates the Caddyfile, starts Caddy and uvicorn."""

from __future__ import annotations

import asyncio
import logging
import os
import platform
import secrets
import shutil
import signal
import subprocess
from typing import Any
from urllib.parse import urlsplit

import uvicorn
import uvloop

from . import cluster
from .logging_config import setup_logging
from .security import init_server_keys
from .settings import settings
from .state import state

setup_logging()

logger = logging.getLogger(__name__)
caddy_process: subprocess.Popen | None = None


#: The plain HTTP listener of `http_port`, for a reverse proxy that terminates TLS. It answers
#: the trusted proxies alone, and of their requests those the browser sent over TLS: one sent in
#: the clear goes to the public URL, and one the proxy says nothing about is refused. Traefik
#: names a WebSocket's scheme `wss`.
HTTP_SITE = """http://:{port} {{
        @stranger not remote_ip {networks}
        handle @stranger {{
                respond "This port serves the reverse proxies in SEALSKIN_TRUSTED_PROXIES alone, and {{remote_host}} is not one." 403
        }}
        @clear {{
                header X-Forwarded-Proto http
                header X-Forwarded-Proto ws
        }}
        handle @clear {{
                redir {public}{{uri}} 308
        }}
        @secure {{
                header X-Forwarded-Proto https
                header X-Forwarded-Proto wss
        }}
        handle @secure {{
                import entrance
        }}
        handle {{
                respond "The reverse proxy must send X-Forwarded-Proto: https with what it received over HTTPS." 403
        }}
}}
"""


def http_listener() -> bool:
    """Whether the plain HTTP listener is opened: `http_port` is set and a proxy is trusted to use it."""
    if not settings.http_port:
        return False
    if not any(n.strip() for n in settings.trusted_proxies.split(",")):
        logger.error("SEALSKIN_HTTP_PORT is set but SEALSKIN_TRUSTED_PROXIES names no reverse proxy: the HTTP listener stays closed.")
        return False
    return True


def advertises_own_port() -> bool:
    """Whether browsers reach the session port under its own number, with no reverse proxy before it.

    Caddy offers HTTP/3 in an `Alt-Svc` header that names the port it listens
    on. Behind a reverse proxy or a remapped port that is another service's
    port, or nobody's, so HTTP/3 is left off there.
    """
    if any(n.strip() for n in settings.trusted_proxies.split(",")):
        return False
    return (urlsplit(cluster.public_url()).port or 443) == settings.session_port


def run_caddy() -> None:
    """Render the Caddyfile template and start Caddy as a child process."""
    global caddy_process

    caddy_executable = "caddy"
    if not shutil.which(caddy_executable):
        logger.error(
            "'caddy' executable not found in PATH. Please install Caddy and ensure it's in your "
            "system's PATH. See https://caddyserver.com/docs/install"
        )
        return

    template_path = os.path.join(os.path.dirname(__file__), "Caddyfile.tpl")
    output_path = settings.caddyfile_path
    if not os.path.exists(template_path):
        logger.error("Caddyfile template not found at %s. Caddy will not be started.", template_path)
        return

    try:
        logger.info("Generating Caddyfile from template: %s", template_path)
        with open(template_path, encoding="utf-8") as handle:
            config_content = handle.read()
        init_server_keys()
        cluster.init()
        cluster.reload_proxy = reload_caddy
        peer_cert, peer_key, peer_trust = cluster.peer_certificate_paths()
        state.proxy_secret = state.proxy_secret or secrets.token_urlsafe(32)
        networks = " ".join(n.strip() for n in settings.trusted_proxies.split(",") if n.strip())
        options = []
        if networks:
            # Strict: the address is the last one a trusted proxy did not add, never one the visitor wrote.
            options += [f"trusted_proxies static {networks}", "trusted_proxies_strict"]
        if not advertises_own_port():
            options.append("protocols h1 h2")
        trusted = "        servers {\n" + "".join(f"                {line}\n" for line in options) + "        }" if options else ""
        http_site = HTTP_SITE.format(port=settings.http_port, networks=networks, public=cluster.public_url()) if http_listener() else ""
        for placeholder, value in (
            ("{{API_PORT}}", str(settings.api_port)),
            ("{{SESSION_PORT}}", str(settings.session_port)),
            ("{{PEER_PORT}}", str(settings.peer_port)),
            ("{{PROXY_CERT_PATH}}", settings.proxy_cert_path),
            ("{{PROXY_KEY_PATH}}", settings.proxy_key_path),
            ("{{PEER_CERT_PATH}}", peer_cert),
            ("{{PEER_KEY_PATH}}", peer_key),
            ("{{PEER_TRUST_PATH}}", peer_trust),
            ("{{PROXY_SECRET}}", state.proxy_secret),
            ("{{TRUSTED_PROXIES}}", trusted),
            ("{{HTTP_SITE}}", http_site),
        ):
            config_content = config_content.replace(placeholder, value)
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        fd = os.open(output_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(config_content)
        os.chmod(output_path, 0o600)
        logger.info("Caddyfile written to %s", output_path)
    except OSError as exc:
        logger.error("Failed to generate Caddyfile: %s", exc)
        return

    command = [caddy_executable, "run", "--config", output_path, "--adapter", "caddyfile"]
    logger.info("Starting Caddy with command: %s", " ".join(command))
    preexec_fn = os.setsid if platform.system() != "Windows" else None
    try:
        caddy_process = subprocess.Popen(command, preexec_fn=preexec_fn)
        logger.info("Caddy process started with PID: %s", caddy_process.pid)
    except OSError as exc:
        logger.error("Failed to start Caddy: %s", exc)
        caddy_process = None


def reload_caddy() -> None:
    """Have the running Caddy read its configuration again, as when the peer trust bundle changed."""
    if not caddy_process or caddy_process.poll() is not None:
        return
    try:
        subprocess.run(
            ["caddy", "reload", "--force", "--config", settings.caddyfile_path, "--adapter", "caddyfile"],
            check=True,
            capture_output=True,
            timeout=30,
        )
        logger.info("Caddy reloaded its configuration.")
    except (OSError, subprocess.SubprocessError) as exc:
        logger.error("Could not reload Caddy: %s", exc)


def stop_caddy(signum: int | None = None, frame: Any = None) -> None:
    """Stop the Caddy process group if it is still running.

    Args:
        signum: Signal number when invoked as a signal handler.
        frame: Current stack frame (unused).
    """
    global caddy_process
    if not caddy_process or caddy_process.poll() is not None:
        return
    logger.info("Stopping Caddy process group (PID: %s)...", caddy_process.pid)
    try:
        if platform.system() != "Windows":
            os.killpg(os.getpgid(caddy_process.pid), signal.SIGTERM)
        else:
            caddy_process.terminate()
        caddy_process.wait(timeout=5)
        logger.info("Caddy process stopped.")
    except (ProcessLookupError, PermissionError):
        logger.warning("Caddy process already stopped.")
    except subprocess.TimeoutExpired:
        logger.warning("Caddy did not terminate gracefully, killing.")
        if platform.system() != "Windows":
            os.killpg(os.getpgid(caddy_process.pid), signal.SIGKILL)
        else:
            caddy_process.kill()


async def _serve() -> None:
    """Start Caddy and the API server, waiting until shutdown."""
    run_caddy()
    api_config = uvicorn.Config(
        "app.api:api_app",
        host="",
        port=settings.api_port,
        log_config=None,
        proxy_headers=True,
        forwarded_allow_ips=["127.0.0.1", "::1"],
    )
    api_server = uvicorn.Server(api_config)
    logger.info("Starting API server on port %s...", settings.api_port)
    try:
        await api_server.serve()
    finally:
        stop_caddy()


def main() -> None:
    """Synchronous entry point: install uvloop, install signal handlers, run."""
    uvloop.install()
    if platform.system() != "Windows":
        signal.signal(signal.SIGINT, stop_caddy)
        signal.signal(signal.SIGTERM, stop_caddy)
    try:
        asyncio.run(_serve())
    except KeyboardInterrupt:
        logger.info("API server shutting down.")
    finally:
        stop_caddy()
        logger.info("All services shut down.")
