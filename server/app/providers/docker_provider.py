"""Docker implementation of the provider interface."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from docker.errors import APIError, DockerException, ImageNotFound, NotFound
from docker.types import DeviceRequest
from fastapi import HTTPException

from ..docker_utils import (
    get_docker_client,
    inspect_self_container,
    prune_dangling_images,
    scan_render_nodes,
    translate_path_to_host,
)
from ..state import state
from .base_provider import BaseProvider

logger = logging.getLogger(__name__)


def _bind_volumes(bind_mounts: Any) -> dict[str, dict[str, str]]:
    """Normalise Docker bind mounts (a `volumes` dict or `host:path[:mode]` list) to a dict."""
    if isinstance(bind_mounts, dict):
        return dict(bind_mounts)
    result: dict[str, dict[str, str]] = {}
    for mount in bind_mounts or []:
        host, _, rest = str(mount).partition(":")
        bind, _, mode = rest.partition(":")
        if host and bind:
            result[host] = {"bind": bind, "mode": mode or "rw"}
    return result


class DockerProvider(BaseProvider):
    """Launches applications as Docker containers on the local daemon."""

    @property
    def client(self) -> Any:
        """The shared Docker client.

        Raises:
            RuntimeError: If the Docker daemon is unreachable.
        """
        return get_docker_client()

    async def inspect_self(self) -> None:
        """Map the server container's mounts, ports, and network (see `inspect_self_container`)."""
        await inspect_self_container()

    async def detect_gpus(self) -> None:
        """Offer the host's render nodes; NVIDIA ones carry their device index."""
        state.available_gpus[:] = scan_render_nodes()
        logger.info("Detected %d GPU(s): %s", len(state.available_gpus), state.available_gpus)

    async def get_local_image_info(self, image_name: str) -> dict[str, Any] | None:
        """Return id and digests of a locally available image.

        Args:
            image_name: Image reference.

        Returns:
            `{"id", "short_id", "digests"}` or `None` when not present.
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
            The registry digest, or `None` if it could not be determined.
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

    async def prune_images(self) -> None:
        """Remove dangling images left behind by pulls."""
        await prune_dangling_images()

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

        See `BaseProvider.launch` for the arguments; volume keys are server
        paths and are translated to host paths here.

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

        overrides = dict(config.get("docker_overrides") or {})
        bind_mounts = _bind_volumes(overrides.pop("volumes", None))
        run_kwargs: dict[str, Any] = {
            "image": image,
            "detach": True,
            "shm_size": "1g",
            "environment": env_vars,
            "devices": [],
            "remove": True,
            "network": network,
        }
        run_kwargs.update(overrides)
        run_kwargs["devices"] = list(run_kwargs["devices"])
        run_kwargs["volumes"] = {
            translate_path_to_host(path): bind for path, bind in (volumes or {}).items()
        }
        run_kwargs["volumes"].update(bind_mounts)

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

        async def current_ip() -> str | None:
            await asyncio.to_thread(container.reload)
            return self._get_container_ip(container.attrs)

        try:
            ip_address = await self._wait_until_ready(
                session_id,
                current_ip,
                env_vars,
                is_collaboration=is_collaboration,
                master_token=master_token,
                initial_tokens=initial_tokens,
            )
        except HTTPException:
            await self.stop(container.id)
            raise
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
