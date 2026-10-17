"""Session backends: containers on the local Docker daemon or pods in the server's Kubernetes namespace."""

from __future__ import annotations

import os
from functools import cache
from typing import Any

from ..settings import settings
from .base_provider import BaseProvider

SERVICE_ACCOUNT_TOKEN = "/var/run/secrets/kubernetes.io/serviceaccount/token"


@cache
def provider_class() -> type[BaseProvider]:
    """Return the backend `default_provider` names; `auto` is Kubernetes inside a pod, else Docker."""
    name = settings.default_provider.lower()
    if name == "auto":
        in_pod = "KUBERNETES_SERVICE_HOST" in os.environ and os.path.exists(SERVICE_ACCOUNT_TOKEN)
        name = "kubernetes" if in_pod else "docker"
    if name == "kubernetes":
        from .kubernetes_provider import KubernetesProvider

        return KubernetesProvider
    from .docker_provider import DockerProvider

    return DockerProvider


def get_provider(app_config: dict[str, Any] | None = None) -> BaseProvider:
    """Return the active backend, bound to `app_config` when a launch needs it."""
    return provider_class()(app_config)
