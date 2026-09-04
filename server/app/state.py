"""Process-wide runtime state.

All mutable in-memory state lives on the single `state` instance so that
routers, the collaboration module and the launch logic can share it without
importing each other.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any

from .models import AppStore, InstalledApp, InstalledAppRecord, PublicShareMetadata


@dataclass
class CryptoSession:
    """An established end-to-end encryption session.

    Attributes:
        key: Raw AES-256-GCM key bytes negotiated during the handshake.
        last_used: Unix timestamp of the last request that used the key.
    """

    key: bytes
    last_used: float = field(default_factory=time.time)


@dataclass
class RuntimeState:
    """Container for everything the server keeps in memory.

    Attributes:
        sessions: Live application sessions keyed by session id. Persisted to
            `sessions.yml`.
        sessions_lock: Guards writes of the sessions file.
        crypto_sessions: E2EE sessions keyed by session id.
        installed_records: Installed app records (reference plus overrides)
            keyed by app id, exactly as stored on disk.
        installed_apps: Resolved installed apps keyed by app id.
        app_stores: Configured app stores.
        store_entries: Processed store entries keyed by store name then app id.
        app_templates: App templates keyed by template name.
        template_files: On-disk path of every loaded template keyed by name.
        available_gpus: GPUs detected on the host.
        public_shares: Public share metadata keyed by share id.
        metadata_lock: Guards writes of the public shares file.
        image_metadata: Cached image digests keyed by image name.
        deletion_tasks: Background deletion task status keyed by task id.
        pull_status: Images currently being pulled keyed by image name.
        system_stats_cache: Cached CPU and disk statistics.
        cpu_model: CPU model string read from `/proc/cpuinfo`.
        path_prefix_map: Container mount path to host path mapping.
        discovered_api_port: Externally mapped API port.
        discovered_session_port: Externally mapped session port.
        discovered_network: Docker network the server container is attached to.
        download_tokens: One-shot public download tokens.
        server_private_key: The server's RSA private key object.
        server_public_key_pem: PEM encoding of the server's public key.
    """

    sessions: dict[str, dict[str, Any]] = field(default_factory=dict)
    sessions_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    crypto_sessions: dict[str, CryptoSession] = field(default_factory=dict)
    installed_records: dict[str, InstalledAppRecord] = field(default_factory=dict)
    installed_apps: dict[str, InstalledApp] = field(default_factory=dict)
    app_stores: list[AppStore] = field(default_factory=list)
    store_entries: dict[str, dict[str, dict[str, Any]]] = field(default_factory=dict)
    app_templates: dict[str, dict[str, Any]] = field(default_factory=dict)
    template_files: dict[str, str] = field(default_factory=dict)
    available_gpus: list[dict[str, Any]] = field(default_factory=list)
    public_shares: dict[str, PublicShareMetadata] = field(default_factory=dict)
    metadata_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    image_metadata: dict[str, dict[str, Any]] = field(default_factory=dict)
    deletion_tasks: dict[str, dict[str, Any]] = field(default_factory=dict)
    pull_status: dict[str, str] = field(default_factory=dict)
    system_stats_cache: dict[str, Any] = field(
        default_factory=lambda: {"data": None, "timestamp": 0.0}
    )
    cpu_model: str = "Unknown"
    path_prefix_map: dict[str, str] = field(default_factory=dict)
    discovered_api_port: int = 0
    discovered_session_port: int = 0
    discovered_network: str | None = None
    download_tokens: dict[str, dict[str, Any]] = field(default_factory=dict)
    server_private_key: Any = None
    server_public_key_pem: str = ""


state = RuntimeState()
