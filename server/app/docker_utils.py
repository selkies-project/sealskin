"""Docker host helpers: client access, self-inspection, GPUs and images.

Everything that talks to the Docker daemon outside of a launch goes through
this module so the rest of the server never creates its own client.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import time
from typing import Any

import docker
from docker.errors import DockerException, NotFound

from .settings import settings
from .state import state

logger = logging.getLogger(__name__)

_CLIENT: docker.DockerClient | None = None


def get_docker_client() -> docker.DockerClient:
    """Return the shared Docker client, creating it on first use.

    Returns:
        A :class:`docker.DockerClient` connected from the environment.

    Raises:
        RuntimeError: If the daemon cannot be reached.
    """
    global _CLIENT
    if _CLIENT is None:
        try:
            client = docker.from_env()
            client.ping()
        except Exception as exc:  # noqa: BLE001
            logger.error("Could not connect to Docker daemon: %s", exc)
            raise RuntimeError("Failed to connect to Docker daemon.") from exc
        _CLIENT = client
    return _CLIENT


async def container_exists(instance_id: str) -> bool:
    """Tell whether a container still exists on the host.

    Args:
        instance_id: Container id.

    Returns:
        ``True`` if Docker knows the container, ``False`` if it is gone.

    Raises:
        DockerException: If the daemon cannot be queried.
    """
    client = await asyncio.to_thread(get_docker_client)
    try:
        await asyncio.to_thread(client.containers.get, instance_id)
        return True
    except NotFound:
        return False


async def prune_dangling_images() -> None:
    """Remove dangling images left behind by pulls."""
    try:
        client = await asyncio.to_thread(get_docker_client)
        await asyncio.to_thread(client.images.prune, filters={"dangling": True})
        logger.info("Successfully cleaned up dangling images.")
    except Exception as exc:  # noqa: BLE001
        logger.error("Failed to prune dangling images: %s", exc)


async def inspect_self_container() -> None:
    """Discover mount mappings, ports and network of the server's own container.

    Populates ``state.path_prefix_map``, ``state.discovered_api_port``,
    ``state.discovered_session_port`` and ``state.discovered_network``. When
    the server is not running inside Docker nothing is changed.
    """
    state.discovered_api_port = settings.api_port
    state.discovered_session_port = settings.session_port
    if not os.path.exists("/var/run/docker.sock"):
        logger.info("Docker socket not found. Assuming running on host.")
        return

    container = None
    try:
        client = await asyncio.to_thread(get_docker_client)
        containers = await asyncio.to_thread(client.containers.list, filters={"name": "sealskin"})

        if not containers:
            hostname = os.uname()[1]
            logger.info(
                "No container named 'sealskin' found. Trying with hostname '%s'.", hostname
            )
            try:
                container = await asyncio.to_thread(client.containers.get, hostname)
            except NotFound:
                logger.warning(
                    "Could not find self-container by name 'sealskin' or hostname. "
                    "Path remapping will be disabled."
                )
                return
        else:
            container = containers[0]

        logger.info("Found self-container '%s'. Inspecting mounts.", container.name)
        for mount in container.attrs.get("Mounts", []) or []:
            host_path = mount.get("Source")
            container_path = mount.get("Destination")
            if host_path and container_path:
                state.path_prefix_map[container_path] = host_path

        if state.path_prefix_map:
            logger.info("Detected container mount prefixes: %s", state.path_prefix_map)
        else:
            logger.warning("Could not find any usable mount points on the current container.")
    except (DockerException, RuntimeError) as exc:
        logger.warning("Could not inspect self in Docker. Path mapping disabled. Error: %s", exc)
        return
    except Exception as exc:  # noqa: BLE001
        logger.error("An unexpected error occurred during Docker self-inspection: %s", exc)
        return

    network_settings = container.attrs.get("NetworkSettings", {})
    networks = network_settings.get("Networks", {})
    if networks:
        state.discovered_network = list(networks.keys())[0]
        logger.info("Discovered self-container network: %s", state.discovered_network)

    ports = network_settings.get("Ports", {}) or {}
    if not ports:
        logger.info("Container '%s' has no port mappings to inspect.", container.name)
        return

    api_internal = f"{settings.api_port}/tcp"
    session_internal = f"{settings.session_port}/tcp"
    if ports.get(api_internal):
        if host_port := ports[api_internal][0].get("HostPort"):
            state.discovered_api_port = int(host_port)
            logger.info(
                "Discovered external API port mapping: %s -> %s",
                settings.api_port,
                state.discovered_api_port,
            )
    if ports.get(session_internal):
        if host_port := ports[session_internal][0].get("HostPort"):
            state.discovered_session_port = int(host_port)
            logger.info(
                "Discovered external Session port mapping: %s -> %s",
                settings.session_port,
                state.discovered_session_port,
            )


def translate_path_to_host(internal_path: str) -> str:
    """Map a path inside the server container to the equivalent host path.

    Args:
        internal_path: Absolute path as seen by the server process.

    Returns:
        The host path when a mount prefix matches, otherwise the input.
    """
    if not state.path_prefix_map or not internal_path:
        return internal_path

    for container_prefix in sorted(state.path_prefix_map, key=len, reverse=True):
        if internal_path == container_prefix or internal_path.startswith(container_prefix + "/"):
            host_prefix = state.path_prefix_map[container_prefix]
            relative_path = os.path.relpath(internal_path, container_prefix)
            if relative_path == ".":
                return host_prefix
            translated = os.path.join(host_prefix, relative_path)
            logger.debug("Translated path '%s' -> '%s'", internal_path, translated)
            return translated

    return internal_path


def read_cpu_model() -> None:
    """Read the CPU model name from ``/proc/cpuinfo`` into ``state.cpu_model``."""
    try:
        with open("/proc/cpuinfo", encoding="utf-8") as handle:
            for line in handle:
                if "model name" in line:
                    state.cpu_model = line.split(":", 1)[1].strip()
                    logger.info("Detected CPU Model: %s", state.cpu_model)
                    break
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not read CPU model from /proc/cpuinfo: %s", exc)


def get_system_stats() -> dict[str, Any]:
    """Return CPU model and storage usage, cached for one minute.

    Returns:
        Dictionary with ``cpu_model``, ``disk_total`` and ``disk_used``.
    """
    now = time.time()
    cache = state.system_stats_cache
    if cache["data"] and (now - cache["timestamp"] < 60):
        return cache["data"]

    try:
        usage = shutil.disk_usage(settings.storage_path)
        stats = {
            "cpu_model": state.cpu_model,
            "disk_total": usage.total,
            "disk_used": usage.used,
        }
        cache["data"] = stats
        cache["timestamp"] = now
        return stats
    except Exception as exc:  # noqa: BLE001
        logger.error("Failed to get system stats: %s", exc)
        return {"cpu_model": state.cpu_model, "disk_total": None, "disk_used": None}


def detect_gpus() -> None:
    """Detect render nodes under ``/sys/class/drm`` into ``state.available_gpus``."""
    state.available_gpus.clear()
    drm_root = "/sys/class/drm"
    try:
        render_devices = sorted(
            [name for name in os.listdir(drm_root) if name.startswith("renderD")],
            key=lambda name: int(name.replace("renderD", "")),
        )
    except FileNotFoundError:
        logger.info("No DRM devices found. No GPUs will be available.")
        return
    except Exception as exc:  # noqa: BLE001
        logger.error("An unexpected error occurred during GPU detection: %s", exc)
        return

    nvidia_index = 0
    for device_name in render_devices:
        driver_link = os.path.join(drm_root, device_name, "device", "driver")
        try:
            driver = os.path.basename(os.path.realpath(driver_link))
        except OSError:
            continue
        if not driver or not os.path.exists(driver_link):
            continue
        gpu_info: dict[str, Any] = {"device": f"/dev/dri/{device_name}", "driver": driver}
        if driver == "nvidia":
            gpu_info["type"] = "nvidia"
            gpu_info["index"] = nvidia_index
            nvidia_index += 1
        else:
            gpu_info["type"] = "dri3"
        state.available_gpus.append(gpu_info)

    logger.info("Detected %d GPU(s): %s", len(state.available_gpus), state.available_gpus)


async def get_and_cache_image_metadata(image_name: str, force_refresh: bool = False) -> None:
    """Record the local digest of an image in ``state.image_metadata``.

    Args:
        image_name: Image reference such as ``ghcr.io/org/app:latest``.
        force_refresh: Re-query Docker even when a digest is cached.
    """
    if (
        not force_refresh
        and image_name in state.image_metadata
        and "sha" in state.image_metadata[image_name]
    ):
        return

    from .providers.docker_provider import image_provider

    provider = image_provider(image_name)
    info = await provider.get_local_image_info(image_name)

    entry = state.image_metadata.setdefault(image_name, {})
    if info:
        entry["sha"] = info["short_id"]
        entry["digests"] = info["digests"]
    else:
        entry["sha"] = None
        entry["digests"] = []


async def pull_and_cache_image(image_name: str) -> None:
    """Pull an image in the background and refresh its cached digest.

    Args:
        image_name: Image reference to pull.
    """
    if state.pull_status.get(image_name) == "pulling":
        logger.info("Pull for image '%s' is already in progress.", image_name)
        return

    from .providers.docker_provider import image_provider

    state.pull_status[image_name] = "pulling"
    try:
        logger.info("Starting background pull for image '%s'...", image_name)
        await image_provider(image_name).pull_image(image_name)
        await get_and_cache_image_metadata(image_name, force_refresh=True)
        state.image_metadata.setdefault(image_name, {})["last_checked_at"] = time.time()
        logger.info("Background pull for '%s' completed successfully.", image_name)
    except Exception as exc:  # noqa: BLE001
        logger.error("Background pull for image '%s' failed: %s", image_name, exc)
    finally:
        state.pull_status.pop(image_name, None)
