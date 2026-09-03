"""Server entry point: generates the Caddyfile, starts Caddy and uvicorn."""

from __future__ import annotations

import asyncio
import logging
import os
import platform
import shutil
import signal
import subprocess
from typing import Any

import uvicorn
import uvloop

from app.logging_config import setup_logging
from app.settings import settings

setup_logging()

logger = logging.getLogger(__name__)
caddy_process: subprocess.Popen | None = None


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
        for placeholder, value in (
            ("{{API_PORT}}", str(settings.api_port)),
            ("{{SESSION_PORT}}", str(settings.session_port)),
            ("{{PROXY_CERT_PATH}}", settings.proxy_cert_path),
            ("{{PROXY_KEY_PATH}}", settings.proxy_key_path),
        ):
            config_content = config_content.replace(placeholder, value)
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as handle:
            handle.write(config_content)
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


def stop_caddy(signum: int | None = None, frame: Any = None) -> None:
    """Stop the Caddy process group if it is still running.

    Args:
        signum: Signal number when invoked as a signal handler.
        frame: Current stack frame when invoked as a signal handler.
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
        logger.warning("Caddy process did not terminate gracefully, killing.")
        if platform.system() != "Windows":
            os.killpg(os.getpgid(caddy_process.pid), signal.SIGKILL)
        else:
            caddy_process.kill()


async def main() -> None:
    """Start Caddy and serve the API until shutdown."""
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


if __name__ == "__main__":
    uvloop.install()
    if platform.system() != "Windows":
        signal.signal(signal.SIGINT, stop_caddy)
        signal.signal(signal.SIGTERM, stop_caddy)
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("API server shutting down.")
    finally:
        stop_caddy()
        logger.info("All services shut down.")
