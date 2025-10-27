import asyncio
import os
import uvicorn
import logging

from app.settings import settings
from app.logging_config import setup_logging

setup_logging()

from app.api import api_app
from app.proxy import proxy_app

logger = logging.getLogger(__name__)

def check_ssl_certs():
    """Checks for proxy SSL certificates and warns if they are missing."""
    if not os.path.exists(settings.proxy_key_path) or not os.path.exists(settings.proxy_cert_path):
        warning_msg = (
            "\n" + "="*80 +
            "\nWARNING: SSL certificate for proxy not found." +
            f"\nLooking for '{settings.proxy_key_path}' and '{settings.proxy_cert_path}'." +
            '\nGenerate a self-signed one with: openssl req -x509 -newkey rsa:4096 ' +
            f'-keyout {settings.proxy_key_path} -out {settings.proxy_cert_path} ' +
            '-sha256 -days 365 -nodes -subj "/CN=localhost"' +
            "\n" + "="*80
        )
        logger.warning(warning_msg)

async def main():
    """Sets up and runs the API and Proxy servers."""
    check_ssl_certs()
    
    api_config = uvicorn.Config(
        "app.api:api_app",
        host="0.0.0.0",
        port=settings.api_port,
        log_config=None
    )
    api_server = uvicorn.Server(api_config)

    proxy_config = uvicorn.Config(
        "app.proxy:proxy_app",
        host="0.0.0.0",
        port=settings.session_port,
        log_config=None,
        ssl_keyfile=settings.proxy_key_path,
        ssl_certfile=settings.proxy_cert_path
    )
    proxy_server = uvicorn.Server(proxy_config)
    
    logger.info(f"Starting API server on port {settings.api_port} and Proxy server on port {settings.session_port}...")
    await asyncio.gather(api_server.serve(), proxy_server.serve())

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Servers shutting down gracefully.")
