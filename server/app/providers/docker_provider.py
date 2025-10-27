import asyncio
import time
import logging
from typing import Dict, Optional

import docker
from docker.errors import NotFound, ImageNotFound, APIError, DockerException
from docker.types import DeviceRequest
from fastapi import HTTPException

from .base_provider import BaseProvider
from ..settings import settings

logger = logging.getLogger(__name__)


class DockerProvider(BaseProvider):
    """A provider for launching applications in Docker containers."""

    def __init__(self, app_config: Dict):
        super().__init__(app_config)
        try:
            self.client = docker.from_env()
            self.client.ping()
        except Exception as e:
            logger.error(f"Could not connect to Docker daemon: {e}")
            raise RuntimeError("Failed to connect to Docker daemon.") from e

    async def initialize(self):
        """Pulls the required image. Can be called on-demand."""
        image_name = self.app_config["provider_config"]["image"]
        logger.info(
            f"[{self.app_config.get('name', image_name)}] Initializing Docker provider..."
        )
        await self.pull_image(image_name)

    async def get_local_image_info(self, image_name: str) -> Optional[Dict]:
        """Gets information about a locally available image."""
        try:
            image = await asyncio.to_thread(self.client.images.get, image_name)
            return {
                "id": image.id,
                "short_id": image.short_id.split(":")[-1],
                "digests": image.attrs.get("RepoDigests", []),
            }
        except ImageNotFound:
            return None
        except APIError as e:
            logger.error(
                f"Docker API error getting local image info for '{image_name}': {e}"
            )
            return None

    async def get_remote_image_digest(self, image_name: str) -> Optional[str]:
        """Gets the digest of the latest image from the remote registry."""
        try:
            api_client = self.client.api
            distribution_info = await asyncio.to_thread(
                api_client.inspect_distribution, image_name
            )
            return distribution_info["Descriptor"]["digest"]
        except APIError as e:
            if e.response.status_code == 404:
                logger.warning(f"Image '{image_name}' not found in remote registry.")
            else:
                logger.error(
                    f"Docker API error inspecting remote image '{image_name}': {e}"
                )
            return None
        except DockerException as e:
            logger.error(f"Docker error inspecting remote image '{image_name}': {e}")
            return None

    async def pull_image(self, image_name: str):
        """Pulls an image from the registry and returns the new image object."""
        try:
            logger.info(f"Pulling latest image for '{image_name}'...")
            image = await asyncio.to_thread(self.client.images.pull, image_name)
            logger.info(f"Successfully pulled '{image_name}'.")
            return image
        except APIError as e:
            logger.error(f"Failed to pull image '{image_name}': {e}")
            raise

    async def launch(
        self,
        session_id: str,
        env_vars: Dict,
        volumes: Optional[Dict] = None,
        gpu_config: Optional[Dict] = None,
    ) -> Dict:
        """Launches a Docker container for the application."""
        config = self.app_config["provider_config"]
        image = config["image"]

        try:
            await asyncio.to_thread(self.client.images.get, image)
        except ImageNotFound:
            logger.info(f"[{session_id}] Image '{image}' not found locally, pulling...")
            await self.pull_image(image)

        run_kwargs = {
            "image": image,
            "detach": True,
            "shm_size": config.get("shm_size", "1g"),
            "environment": env_vars,
            "volumes": volumes,
            "devices": config.get("devices", []),
            "remove": True,
        }

        if gpu_config:
            if gpu_config["type"] == "nvidia":
                run_kwargs["runtime"] = "nvidia"
                run_kwargs["device_requests"] = [
                    DeviceRequest(
                        device_ids=[str(gpu_config["index"])],
                        capabilities=[
                            ["compute", "video", "graphics", "utility", "gpu"]
                        ],
                    )
                ]
                logger.info(
                    f"[{session_id}] Configuring container with Nvidia GPU index {gpu_config['index']}"
                )
            elif gpu_config["type"] == "dri3":
                device_path = gpu_config["device"]
                run_kwargs["devices"].append(f"{device_path}:{device_path}")
                logger.info(
                    f"[{session_id}] Configuring container with DRI3 device {device_path}"
                )

        try:
            container = await asyncio.to_thread(
                self.client.containers.run, **run_kwargs
            )
            logger.info(
                f"[{session_id}] Launched container {container.short_id} from image {image}."
            )
        except ImageNotFound:
            logger.error(
                f"[{session_id}] Image '{image}' not found after pull attempt."
            )
            raise HTTPException(
                status_code=500,
                detail=f"Application image '{image}' not found on host.",
            )
        except APIError as e:
            logger.error(f"[{session_id}] Docker API error on launch: {e}")
            if "could not select device driver" in str(
                e
            ) or "nvidia-container-runtime" in str(e):
                raise HTTPException(
                    status_code=500,
                    detail="Nvidia runtime error on host. Is nvidia-container-toolkit installed and configured?",
                )
            raise HTTPException(
                status_code=500, detail=f"Docker error: {e.explanation}"
            )

        ip_address = await self._wait_for_container_ready(
            container, session_id, env_vars.get("SUBFOLDER")
        )

        return {"instance_id": container.id, "ip": ip_address, "port": config["port"]}

    async def stop(self, instance_id: str):
        """Stops a running Docker container."""
        try:
            container = await asyncio.to_thread(self.client.containers.get, instance_id)
            logger.info(f"Stopping container {container.short_id}...")
            await asyncio.to_thread(container.stop, timeout=5)
            logger.info(f"Stopped container {container.short_id}.")
        except NotFound:
            logger.warning(
                f"Attempted to stop container {instance_id}, but it was not found."
            )
        except Exception as e:
            logger.error(f"Error stopping container {instance_id}: {e}")

    async def _wait_for_container_ready(
        self, container, session_id, subfolder, timeout=60
    ):
        import httpx

        start_time = time.time()
        while time.time() - start_time < timeout:
            try:
                await asyncio.to_thread(container.reload)
                ip_address = self._get_container_ip(container.attrs)
                if not ip_address:
                    await asyncio.sleep(0.5)
                    continue

                health_check_url = f"http://{ip_address}:{self.app_config['provider_config']['port']}{subfolder}"
                async with httpx.AsyncClient(
                    timeout=2.0, follow_redirects=True
                ) as client:
                    response = await client.get(health_check_url)
                    if response.status_code == 200:
                        logger.info(
                            f"[{session_id}] Health check passed for {health_check_url}"
                        )
                        return ip_address
            except httpx.ConnectError:
                logger.debug(
                    f"[{session_id}] Health check pending for {container.short_id}..."
                )
            except Exception as e:
                logger.warning(f"[{session_id}] Error during readiness check: {e}")
            await asyncio.sleep(2)

        logger.error(
            f"[{session_id}] Container {container.short_id} failed to become ready in time."
        )
        await self.stop(container.id)
        raise HTTPException(
            status_code=504, detail="Container failed to become ready in time."
        )

    def _get_container_ip(self, container_attrs):
        networks = container_attrs.get("NetworkSettings", {}).get("Networks", {})
        if not networks:
            return None
        if "bridge" in networks:
            return networks["bridge"].get("IPAddress")
        return next(
            (net.get("IPAddress") for net in networks.values() if net.get("IPAddress")),
            None,
        )

