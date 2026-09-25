"""Session backends: the provider that starts, stops, and describes session instances."""

from __future__ import annotations

from typing import Any

from .base_provider import BaseProvider


def get_provider(app_config: dict[str, Any] | None = None) -> BaseProvider:
    """Return the backend, bound to `app_config` when a launch needs it."""
    from .docker_provider import DockerProvider

    return DockerProvider(app_config)
