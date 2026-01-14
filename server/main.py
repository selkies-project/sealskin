import asyncio
import os
import uvicorn
import logging
import subprocess
import signal
import platform
import uvloop

from app.settings import settings
from app.logging_config import setup_logging

setup_logging()

from app.api import api_app

logger = logging.getLogger(__name__)
caddy_process = None

def run_caddy():
    global caddy_process
    
    caddy_executable = "caddy"
    if not shutil.which(caddy_executable):
        logger.error(
            "'caddy' executable not found in PATH. "
            "Please install Caddy and ensure it's in your system's PATH. "
            "See https://caddyserver.com/docs/install"
        )
        return

    template_path = os.path.join(os.path.dirname(__file__), "Caddyfile.tpl")
    output_path = settings.caddyfile_path

    if not os.path.exists(template_path):
        logger.error(f"Caddyfile template not found at {template_path}. Caddy will not be started.")
        return

    try:
        logger.info(f"Generating Caddyfile from template: {template_path}")
        with open(template_path, 'r') as f:
            template_content = f.read()

        config_content = template_content.replace("{{API_PORT}}", str(settings.api_port))
        config_content = config_content.replace("{{SESSION_PORT}}", str(settings.session_port))
        config_content = config_content.replace("{{PROXY_CERT_PATH}}", settings.proxy_cert_path)
        config_content = config_content.replace("{{PROXY_KEY_PATH}}", settings.proxy_key_path)

        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        with open(output_path, 'w') as f:
            f.write(config_content)
        logger.info(f"Caddyfile written to {output_path}")

    except Exception as e:
        logger.error(f"Failed to generate Caddyfile: {e}")
        return

    command = [
        caddy_executable,
        "run",
        "--config", output_path,
        "--adapter", "caddyfile"
    ]
    
    logger.info(f"Starting Caddy with command: {' '.join(command)}")
    
    preexec_fn = os.setsid if platform.system() != "Windows" else None
    
    try:
        caddy_process = subprocess.Popen(command, preexec_fn=preexec_fn)
        logger.info(f"Caddy process started with PID: {caddy_process.pid}")
    except Exception as e:
        logger.error(f"Failed to start Caddy: {e}")
        caddy_process = None

def stop_caddy(signum=None, frame=None):
    global caddy_process
    if caddy_process and caddy_process.poll() is None:
        logger.info(f"Stopping Caddy process group (PID: {caddy_process.pid})...")
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

async def main():
    run_caddy()
    
    api_config = uvicorn.Config(
        "app.api:api_app",
        host="0.0.0.0",
        port=settings.api_port,
        log_config=None,
        proxy_headers=True,
        forwarded_allow_ips="127.0.0.1"
    )
    api_server = uvicorn.Server(api_config)
    
    logger.info(f"Starting API server on port {settings.api_port}...")
    try:
        await api_server.serve()
    finally:
        stop_caddy()

if __name__ == "__main__":
    import shutil
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
