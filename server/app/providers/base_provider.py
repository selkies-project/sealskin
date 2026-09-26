"""Abstract provider interface and the readiness check every backend shares."""

from __future__ import annotations

import asyncio
import base64
import logging
import re
import time
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any

import httpx
from fastapi import HTTPException

from ..state import state

logger = logging.getLogger(__name__)

#: Seconds an instance's web endpoint gets to answer once it runs.
READY_TIMEOUT = 60

MANAGED_BY_LABEL = "app.kubernetes.io/managed-by"
INSTANCE_LABEL = "app.kubernetes.io/instance"
SESSION_LABEL = "sealskin.app/session"


def host_port(ip: str, port: int | str) -> str:
    """Join an address and a port, bracketing IPv6 literals."""
    return f"[{ip}]:{port}" if ":" in ip else f"{ip}:{port}"


def epoch(timestamp: str) -> float:
    """Parse an RFC 3339 time, nanoseconds included, to seconds since the epoch."""
    return datetime.fromisoformat(
        re.sub(r"(\.\d{6})\d+", r"\1", timestamp).replace("Z", "+00:00")
    ).timestamp()


def instance_labels(session_id: str) -> dict[str, str]:
    """Labels that mark an instance as this server's, for `managed_instances`."""
    return {
        MANAGED_BY_LABEL: "sealskin",
        INSTANCE_LABEL: state.instance_name,
        SESSION_LABEL: session_id,
    }


class BaseProvider(ABC):
    """A backend that runs session instances and reports on its host.

    Attributes:
        orphan_grace: Seconds an unreferenced instance may belong to a launch
            still in progress before `managed_instances` callers remove it.
    """

    orphan_grace: float = READY_TIMEOUT * 2

    def __init__(self, app_config: dict[str, Any] | None = None) -> None:
        """Bind the provider to an application.

        Args:
            app_config: Resolved application dictionary (`InstalledApp` shape)
                including `provider_config`; omitted for host-level calls.
        """
        self.app_config = app_config or {"provider_config": {}}

    @abstractmethod
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
        """Launch an instance of the application and wait until it answers.

        Args:
            session_id: Unique id of the session.
            env_vars: Environment variables for the instance.
            volumes: Server paths to mount, each to `{"bind": <path in the instance>}`.
            gpu_config: GPU descriptor from `state.available_gpus`.
            network: Docker network to attach the instance to.
            is_collaboration: Whether this is a collaboration session.
            master_token: Master token of the downstream control plane.
            initial_tokens: Initial token set for the downstream control plane.

        Returns:
            `{"instance_id": str, "ip": str, "port": int}`.
        """

    @abstractmethod
    async def stop(self, instance_id: str) -> None:
        """Stop and remove an instance; a missing one is not an error."""

    @abstractmethod
    async def is_running(self, instance_id: str) -> bool:
        """Tell whether an instance still runs.

        Raises:
            Exception: When the backend cannot be asked, so callers never
                mistake an outage for an ended instance.
        """

    @abstractmethod
    async def managed_instances(self) -> dict[str, float]:
        """Return the instances this server labelled, with their creation times."""

    @abstractmethod
    async def inspect_self(self) -> None:
        """Discover what the backend needs to know about the server's own container."""

    @abstractmethod
    async def detect_gpus(self) -> None:
        """Fill `state.available_gpus` with the GPUs sessions can request."""

    @abstractmethod
    async def get_local_image_info(self, image_name: str) -> dict[str, Any] | None:
        """Return `{"id", "short_id", "digests"}` of the image sessions run, or `None`."""

    @abstractmethod
    async def get_remote_image_digest(self, image_name: str) -> str | None:
        """Return the digest the registry serves for an image now, or `None`."""

    @abstractmethod
    async def pull_image(self, image_name: str) -> Any:
        """Make new sessions run the registry's current image."""

    @abstractmethod
    async def prune_images(self) -> None:
        """Remove images no session can use any more."""

    async def _wait_until_ready(
        self,
        session_id: str,
        current_ip: Callable[[], Awaitable[str | None]],
        env_vars: dict[str, str],
        is_collaboration: bool = False,
        master_token: str | None = None,
        initial_tokens: dict[str, Any] | None = None,
    ) -> str:
        """Poll the instance's web endpoint until it answers, then seed a room's tokens.

        Args:
            session_id: Session id for log prefixes.
            current_ip: Returns the instance's address, `None` while it has none;
                raises `HTTPException` when the instance can no longer start.
            env_vars: Environment the basic-auth credentials and subfolder come from.
            is_collaboration: Also post the initial tokens to the control plane.
            master_token: Control-plane master token.
            initial_tokens: Tokens to post.

        Returns:
            The instance's IP address.

        Raises:
            HTTPException: 504 when the instance never becomes ready.
        """
        auth_header = None
        if "CUSTOM_USER" in env_vars and "PASSWORD" in env_vars:
            auth_str = f"{env_vars['CUSTOM_USER']}:{env_vars['PASSWORD']}"
            auth_header = {"Authorization": f"Basic {base64.b64encode(auth_str.encode()).decode()}"}

        port = self.app_config["provider_config"]["port"]
        subfolder = env_vars.get("SUBFOLDER", "/")
        health_check_passed = False
        deadline = time.monotonic() + READY_TIMEOUT
        while time.monotonic() < deadline:
            try:
                ip_address = await current_ip()
                if not ip_address:
                    await asyncio.sleep(0.5)
                    continue
                address = host_port(ip_address, port)

                if not health_check_passed:
                    health_check_url = f"http://{address}{subfolder}"
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
                            f"http://{address}{subfolder.rstrip('/')}/api/tokens",
                            stacked_headers,
                        ),
                        (
                            f"http://{host_port(ip_address, 8083)}/tokens",
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

            except HTTPException:
                raise
            except httpx.ConnectError:
                logger.debug("[%s] Health check pending...", session_id)
            except Exception as exc:  # noqa: BLE001
                logger.warning("[%s] Error during readiness check: %s", session_id, exc)
            await asyncio.sleep(2)

        logger.error("[%s] Instance failed to become ready in time.", session_id)
        raise HTTPException(status_code=504, detail="Container failed to become ready in time.")
