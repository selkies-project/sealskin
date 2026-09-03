"""Abstract provider interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class BaseProvider(ABC):
    """Abstract base class for all application providers."""

    def __init__(self, app_config: dict[str, Any]) -> None:
        """Initialise the provider with an application configuration.

        Args:
            app_config: Resolved application dictionary (``InstalledApp`` shape)
                including ``provider_config``.
        """
        self.app_config = app_config

    @abstractmethod
    async def initialize(self) -> None:
        """Perform one-time initialisation such as pulling the image."""

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
        """Launch an instance of the application.

        Args:
            session_id: Unique id of the session.
            env_vars: Environment variables for the instance.
            volumes: Volume mounts in Docker SDK format.
            gpu_config: GPU details when a GPU was requested.
            network: Network to attach the instance to.
            is_collaboration: Whether this is a collaboration session.
            master_token: Master token of the downstream control plane.
            initial_tokens: Initial token set for the downstream control plane.

        Returns:
            ``{"instance_id": str, "ip": str, "port": int}``.
        """

    @abstractmethod
    async def stop(self, instance_id: str) -> None:
        """Stop a running instance.

        Args:
            instance_id: Identifier returned by :meth:`launch`.
        """
