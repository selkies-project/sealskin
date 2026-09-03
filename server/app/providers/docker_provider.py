"""Docker implementation of the provider interface."""

from __future__ import annotations

import asyncio
import base64
import logging
import time
from typing import Any

import httpx
from docker.errors import APIError, DockerException, ImageNotFound, NotFound
from docker.types import DeviceRequest
from fastapi import HTTPException

from ..docker_utils import get_docker_client
from .base_provider import BaseProvider

logger = logging.getLogger(__name__)


def image_provider(image_name: str) -> DockerProvider:
    """Build a provider for image-only operations (pull, inspect).

    Args:
        image_name: Image reference.

    Returns:
        A :class:`DockerProvider` whose configuration only names the image.
    """
    return DockerProvider({"provider_config": {"image": image_name}})


class DockerProvider(BaseProvider):
    """Launches applications as Docker containers on the local daemon."""

    def __init__(self, app_config: dict[str, Any]) -> None:
        """Create a provider bound to the shared Docker client.

        Args:
            app_config: Resolved application dictionary.

        Raises:
            RuntimeError: If the Docker daemon is unreachable.
        """
        super().__init__(app_config)
        self.client = get_docker_client()

    async def initialize(self) -> None:
        """Pull the application's image."""
        image_name = self.app_config["provider_config"]["image"]
        logger.info("[%s] Initializing Docker provider...", self.app_config.get("name", image_name))
        await self.pull_image(image_name)

    async def get_local_image_info(self, image_name: str) -> dict[str, Any] | None:
        """Return id and digests of a locally available image.

        Args:
            image_name: Image reference.

        Returns:
            ``{"id", "short_id", "digests"}`` or ``None`` when not present.
        """
        try:
            image = await asyncio.to_thread(self.client.images.get, image_name)
            return {
                "id": image.id,
                "short_id": image.short_id.split(":")[-1],
                "digests": image.attrs.get("RepoDigests", []),
            }
        except ImageNotFound:
            return None
        except APIError as exc:
            logger.error("Docker API error getting local image info for '%s': %s", image_name, exc)
            return None

    async def get_remote_image_digest(self, image_name: str) -> str | None:
        """Return the digest the registry currently serves for an image.

        Args:
            image_name: Image reference.

        Returns:
            The registry digest, or ``None`` if it could not be determined.
        """
        try:
            distribution_info = await asyncio.to_thread(
                self.client.api.inspect_distribution, image_name
            )
            return distribution_info["Descriptor"]["digest"]
        except APIError as exc:
            if exc.response is not None and exc.response.status_code == 404:
                logger.warning("Image '%s' not found in remote registry.", image_name)
            else:
                logger.error("Docker API error inspecting remote image '%s': %s", image_name, exc)
            return None
        except DockerException as exc:
            logger.error("Docker error inspecting remote image '%s': %s", image_name, exc)
            return None

    async def pull_image(self, image_name: str) -> Any:
        """Pull an image from its registry.

        Args:
            image_name: Image reference.

        Returns:
            The pulled image object.

        Raises:
            APIError: If the pull fails.
        """
        try:
            logger.info("Pulling latest image for '%s'...", image_name)
            image = await asyncio.to_thread(self.client.images.pull, image_name)
            logger.info("Successfully pulled '%s'.", image_name)
            return image
        except APIError as exc:
            logger.error("Failed to pull image '%s': %s", image_name, exc)
            raise

    async def launch(
        self,
        session_id: str,
        env_vars: dict[str, str],
        volumes: dict[str, Any] | None = None,
        gpu_config: dict[str, Any] | None = None,
        network: str | None = None,
        is_collaboration: bool = False,
        master_token: str | None = None,
        initial_tokens: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Run the application container and wait until it answers HTTP.

        See :meth:`BaseProvider.launch` for the arguments.

        Raises:
            HTTPException: On Docker errors or readiness timeout.
        """
        config = self.app_config["provider_config"]
        image = config["image"]

        try:
            await asyncio.to_thread(self.client.images.get, image)
        except ImageNotFound:
            logger.info("[%s] Image '%s' not found locally, pulling...", session_id, image)
            await self.pull_image(image)

        run_kwargs: dict[str, Any] = {
            "image": image,
            "detach": True,
            "shm_size": config.get("shm_size", "1g"),
            "environment": env_vars,
            "volumes": volumes,
            "devices": list(config.get("devices", [])),
            "remove": True,
            "network": network,
        }

        if config.get("docker_overrides"):
            run_kwargs.update(config["docker_overrides"])

        if gpu_config:
            if gpu_config["type"] == "nvidia":
                run_kwargs["runtime"] = "nvidia"
                run_kwargs["device_requests"] = [
                    DeviceRequest(
                        device_ids=[str(gpu_config["index"])],
                        capabilities=[
                            ["compute", "video", "graphics", "utility", "gpu", "display"]
                        ],
                    )
                ]
                run_kwargs["devices"].append("/dev/nvidia-modeset:/dev/nvidia-modeset")
                logger.info(
                    "[%s] Configuring container with Nvidia GPU index %s",
                    session_id,
                    gpu_config["index"],
                )
            elif gpu_config["type"] == "dri3":
                device_path = gpu_config["device"]
                run_kwargs["devices"].append(f"{device_path}:{device_path}")
                logger.info(
                    "[%s] Configuring container with DRI3 device %s", session_id, device_path
                )

        try:
            try:
                container = await asyncio.to_thread(self.client.containers.run, **run_kwargs)
            except APIError as exc:
                error_msg = str(exc).lower()
                if "/dev/nvidia-modeset" in error_msg and "no such file or directory" in error_msg:
                    run_kwargs["devices"].remove("/dev/nvidia-modeset:/dev/nvidia-modeset")
                    container = await asyncio.to_thread(self.client.containers.run, **run_kwargs)
                else:
                    raise
            logger.info(
                "[%s] Launched container %s from image %s.", session_id, container.short_id, image
            )
        except ImageNotFound as exc:
            logger.error("[%s] Image '%s' not found after pull attempt.", session_id, image)
            raise HTTPException(
                status_code=500, detail=f"Application image '{image}' not found on host."
            ) from exc
        except APIError as exc:
            logger.error("[%s] Docker API error on launch: %s", session_id, exc)
            if "could not select device driver" in str(exc) or "nvidia-container-runtime" in str(
                exc
            ):
                raise HTTPException(
                    status_code=500,
                    detail=(
                        "Nvidia runtime error on host. Is nvidia-container-toolkit installed "
                        "and configured?"
                    ),
                ) from exc
            raise HTTPException(
                status_code=500, detail=f"Docker error: {exc.explanation}"
            ) from exc

        ip_address = await self._wait_for_container_ready(
            container,
            session_id,
            env_vars.get("SUBFOLDER", "/"),
            env_vars,
            is_collaboration=is_collaboration,
            master_token=master_token,
            initial_tokens=initial_tokens,
        )

        return {"instance_id": container.id, "ip": ip_address, "port": config["port"]}

    async def stop(self, instance_id: str) -> None:
        """Stop (and remove) a container.

        Args:
            instance_id: Container id.
        """
        try:
            container = await asyncio.to_thread(self.client.containers.get, instance_id)
            if container.status == "running":
                logger.info("Stopping container %s...", container.short_id)
                await asyncio.to_thread(container.stop, timeout=5)
                logger.info("Stopped container %s.", container.short_id)
            else:
                logger.info("Container %s is not running, removing it.", container.short_id)
                try:
                    await asyncio.to_thread(container.remove)
                except APIError as exc:
                    if exc.response is None or exc.response.status_code != 409:
                        raise
        except NotFound:
            logger.warning("Attempted to stop container %s, but it was not found.", instance_id)
        except Exception as exc:  # noqa: BLE001
            logger.error("Error stopping container %s: %s", instance_id, exc)

    async def _wait_for_container_ready(
        self,
        container: Any,
        session_id: str,
        subfolder: str,
        env_vars: dict[str, str],
        timeout: int = 60,
        is_collaboration: bool = False,
        master_token: str | None = None,
        initial_tokens: dict[str, Any] | None = None,
    ) -> str:
        """Poll the container until its web endpoint answers.

        Args:
            container: Docker container object.
            session_id: Session id for log prefixes.
            subfolder: URL prefix the app serves under.
            env_vars: Environment used to derive the basic-auth credentials.
            timeout: Seconds to wait before giving up.
            is_collaboration: Also post the initial tokens to the control plane.
            master_token: Control-plane master token.
            initial_tokens: Tokens to post.

        Returns:
            The container's IP address.

        Raises:
            HTTPException: 504 when the container never becomes ready.
        """
        auth_header = None
        if "CUSTOM_USER" in env_vars and "PASSWORD" in env_vars:
            auth_str = f"{env_vars['CUSTOM_USER']}:{env_vars['PASSWORD']}"
            auth_header = {"Authorization": f"Basic {base64.b64encode(auth_str.encode()).decode()}"}

        port = self.app_config["provider_config"]["port"]
        health_check_passed = False
        start_time = time.time()
        while time.time() - start_time < timeout:
            try:
                await asyncio.to_thread(container.reload)
                ip_address = self._get_container_ip(container.attrs)
                if not ip_address:
                    await asyncio.sleep(0.5)
                    continue

                if not health_check_passed:
                    health_check_url = f"http://{ip_address}:{port}{subfolder}"
                    async with httpx.AsyncClient(
                        timeout=2.0, follow_redirects=True, headers=auth_header
                    ) as client:
                        response = await client.get(health_check_url)
                    if response.status_code == 200:
                        logger.info(
                            "[%s] Basic health check passed for %s", session_id, health_check_url
                        )
                        health_check_passed = True
                        if not is_collaboration:
                            return ip_address
                    else:
                        await asyncio.sleep(2)
                        continue

                if health_check_passed and is_collaboration:
                    logger.info("[%s] Performing collaboration health check...", session_id)
                    stacked_headers = {"Selkies-Authorization": f"Bearer {master_token}"}
                    if auth_header:
                        stacked_headers.update(auth_header)
                    control_plane_targets = [
                        (
                            f"http://{ip_address}:{port}{subfolder.rstrip('/')}/api/tokens",
                            stacked_headers,
                        ),
                        (
                            f"http://{ip_address}:8083/tokens",
                            {"Authorization": f"Bearer {master_token}"},
                        ),
                    ]
                    async with httpx.AsyncClient(timeout=5.0) as client:
                        for control_plane_url, control_plane_headers in control_plane_targets:
                            try:
                                response = await client.post(
                                    control_plane_url,
                                    json=initial_tokens,
                                    headers=control_plane_headers,
                                )
                                if response.status_code == 200:
                                    from ..collaboration import TOKEN_ENDPOINT_CACHE

                                    TOKEN_ENDPOINT_CACHE[ip_address] = control_plane_url
                                    logger.info(
                                        "[%s] Collaboration health check passed. Initial tokens set.",
                                        session_id,
                                    )
                                    return ip_address
                                logger.warning(
                                    "[%s] Collaboration health check failed with status %s at %s",
                                    session_id,
                                    response.status_code,
                                    control_plane_url,
                                )
                            except httpx.RequestError as exc:
                                logger.debug(
                                    "[%s] Collaboration control plane not reachable at %s: %s",
                                    session_id,
                                    control_plane_url,
                                    exc,
                                )
                    logger.warning(
                        "[%s] Collaboration health check failed on all endpoints.", session_id
                    )

            except httpx.ConnectError:
                logger.debug("[%s] Health check pending for %s...", session_id, container.short_id)
            except Exception as exc:  # noqa: BLE001
                logger.warning("[%s] Error during readiness check: %s", session_id, exc)
            await asyncio.sleep(2)

        logger.error(
            "[%s] Container %s failed to become ready in time.", session_id, container.short_id
        )
        await self.stop(container.id)
        raise HTTPException(status_code=504, detail="Container failed to become ready in time.")

    @staticmethod
    def _get_container_ip(container_attrs: dict[str, Any]) -> str | None:
        """Extract the first usable IP address from container attributes."""
        networks = container_attrs.get("NetworkSettings", {}).get("Networks", {})
        if not networks:
            return None
        if "bridge" in networks:
            return networks["bridge"].get("IPAddress")
        return next(
            (net.get("IPAddress") for net in networks.values() if net.get("IPAddress")), None
        )
