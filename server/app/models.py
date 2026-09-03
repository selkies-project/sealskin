"""Pydantic models for API payloads and on-disk records."""

from __future__ import annotations

import uuid
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class GPUInfo(BaseModel):
    """A GPU exposed to sessions."""

    device: str
    driver: str


class Application(BaseModel):
    """Application summary shown to end users."""

    id: str
    name: str
    logo: str
    home_directories: bool
    nvidia_support: bool
    dri3_support: bool
    url_support: bool
    extensions: list[str]
    is_meta_app: bool = False


class LaunchRequestSimple(BaseModel):
    """Launch an application with no context."""

    application_id: str
    home_name: str | None = None
    language: str | None = None
    selected_gpu: str | None = None
    launch_in_room_mode: bool = False
    wayland_mode: bool = True


class LaunchRequestURL(BaseModel):
    """Launch an application and open a URL in it."""

    url: str
    application_id: str
    home_name: str | None = None
    language: str | None = None
    selected_gpu: str | None = None
    launch_in_room_mode: bool = False
    wayland_mode: bool = True


class LaunchRequestFile(BaseModel):
    """Launch an application with an uploaded file."""

    application_id: str
    filename: str
    upload_id: str
    total_chunks: int
    open_file_on_launch: bool = True
    home_name: str | None = None
    language: str | None = None
    selected_gpu: str | None = None
    launch_in_room_mode: bool = False
    wayland_mode: bool = True


class LaunchResponse(BaseModel):
    """Result of a successful launch."""

    session_url: str
    session_id: str


class HandshakeInitiateResponse(BaseModel):
    """First E2EE handshake step: a signed nonce."""

    nonce: str
    signature: str


class HandshakeExchangeRequest(BaseModel):
    """Second E2EE handshake step: the client's wrapped AES key."""

    encrypted_session_key: str


class HandshakeExchangeResponse(BaseModel):
    """Identifier of the established E2EE session."""

    session_id: str


class EncryptedPayload(BaseModel):
    """AES-GCM envelope used for every encrypted request and response."""

    iv: str
    ciphertext: str


class AppStore(BaseModel):
    """A remote application catalogue."""

    name: str
    url: str


class AvailableAppProviderConfig(BaseModel):
    """Provider configuration of an app as published by a store."""

    image: str
    port: int
    nvidia_support: bool
    dri3_support: bool
    type: str
    url_support: bool
    open_support: bool
    extensions: list[str]
    autostart: bool | None = False
    custom_autostart_script_b64: str | None = None
    custom_autostart_wayland_script_b64: str | None = None
    docker_overrides: dict[str, Any] | None = None


class AvailableApp(BaseModel):
    """An app as published by a store."""

    id: str
    name: str
    logo: str
    url: str
    provider: str
    provider_config: AvailableAppProviderConfig


class EnvVar(BaseModel):
    """A single environment variable override."""

    name: str
    value: str


class InstalledAppProviderConfig(AvailableAppProviderConfig):
    """Provider configuration of an installed app, with admin env overrides."""

    env: list[EnvVar] | None = []


class InstalledApp(BaseModel):
    """A fully resolved installed application.

    This is the shape the API exposes and the launch logic consumes. On disk
    the app is stored as an :class:`InstalledAppRecord`.
    """

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str
    logo: str
    url: str
    source: str
    source_app_id: str
    provider: str
    home_directories: bool
    users: list[str]
    groups: list[str]
    provider_config: InstalledAppProviderConfig
    auto_update: bool = True
    app_template: str
    is_meta_app: bool = False
    base_app_id: str | None = None
    home_template_name: str | None = None


#: Fields of :class:`InstalledApp` that belong to the record itself rather
#: than to the store entry. Everything else is derived from the store and only
#: stored when the administrator changed it.
RECORD_FIELDS: tuple[str, ...] = (
    "id",
    "source",
    "source_app_id",
    "app_template",
    "users",
    "groups",
    "auto_update",
    "home_directories",
    "is_meta_app",
    "base_app_id",
    "home_template_name",
)


class InstalledAppRecord(BaseModel):
    """On-disk representation of an installed application.

    The record references the store entry (``source`` and ``source_app_id``)
    and keeps only the fields the administrator changed under ``overrides``.
    The effective :class:`InstalledApp` is the store entry deep-merged with
    ``overrides``; see ``config_store.resolve_app``.
    """

    model_config = ConfigDict(extra="ignore")

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    source: str
    source_app_id: str
    app_template: str = "Default"
    users: list[str] = Field(default_factory=list)
    groups: list[str] = Field(default_factory=list)
    auto_update: bool = True
    home_directories: bool = True
    is_meta_app: bool = False
    base_app_id: str | None = None
    home_template_name: str | None = None
    overrides: dict[str, Any] = Field(default_factory=dict)


class InstalledAppWithStatus(InstalledApp):
    """Installed app plus image status for the admin UI."""

    image_sha: str | None = None
    last_checked_at: float | None = None
    pull_status: str | None = None


class ImageUpdateCheckResponse(BaseModel):
    """Result of comparing the local image with the registry."""

    current_sha: str | None
    update_available: bool


class ImagePullResponse(BaseModel):
    """Result of pulling an image."""

    status: str
    new_sha: str | None


class AppTemplate(BaseModel):
    """An application template (a named set of environment variables)."""

    name: str
    settings: dict[str, Any]


class TemplateSchemaOption(BaseModel):
    """One choice of a ``select`` template setting."""

    value: str
    label: str | None = None
    label_key: str | None = None


class TemplateSchemaSetting(BaseModel):
    """Definition of one environment variable editable in templates."""

    name: str
    category: str
    type: str
    default: str = ""
    docker: bool = False
    options: list[TemplateSchemaOption] | None = None

    @field_validator("default", mode="before")
    @classmethod
    def _stringify_default(cls, value: Any) -> str:
        """Coerce YAML scalars (bools, ints) to the string the editor expects."""
        if value is None:
            return ""
        if isinstance(value, bool):
            return "true" if value else "false"
        return str(value)


class TemplateSchemaResponse(BaseModel):
    """Payload of ``GET /api/ui/template_schema``."""

    settings: list[TemplateSchemaSetting]


class UiManifest(BaseModel):
    """Payload of ``GET /api/ui/version``."""

    version: str
    bridge: int


class UserSettings(BaseModel):
    """Per-user or per-group settings."""

    active: bool = True
    group: str = "none"
    persistent_storage: bool = True
    public_sharing: bool = False
    harden_container: bool = False
    harden_openbox: bool = False
    gpu: bool = True
    storage_limit: int = -1
    session_limit: int = -1


class AdminStatusResponse(BaseModel):
    """Status payload returned to every authenticated user."""

    is_admin: bool
    username: str
    settings: UserSettings
    gpus: list[GPUInfo] = []
    cpu_model: str | None = None
    disk_total: int | None = None
    disk_used: int | None = None
    proxy_cert_expires_at: float | None = None


class User(BaseModel):
    """A user or administrator."""

    username: str
    public_key: str
    is_admin: bool
    settings: UserSettings | None = None


class Group(BaseModel):
    """A group of users sharing settings."""

    name: str
    settings: UserSettings


class ManagementDataResponse(BaseModel):
    """Everything the admin dashboard needs in one call."""

    admins: list[User]
    users: list[User]
    groups: list[Group]
    server_public_key: str
    api_port: int
    session_port: int
    gpus: list[GPUInfo] = []


class CreateUserRequest(BaseModel):
    """Create a user, optionally with a supplied public key."""

    username: str
    public_key: str | None = None
    settings: UserSettings


class CreateUserResponse(BaseModel):
    """Created user plus the generated private key, if any."""

    user: User
    private_key: str | None


class UpdateUserRequest(BaseModel):
    """Replace a user's settings."""

    settings: UserSettings


class CreateGroupRequest(BaseModel):
    """Create a group."""

    name: str = Field(..., pattern=r"^[a-zA-Z0-9_-]+$")
    settings: UserSettings


class UpdateGroupRequest(BaseModel):
    """Replace a group's settings."""

    settings: UserSettings


class CreateMetaAppRequest(BaseModel):
    """Create a meta-app derived from an installed app."""

    name: str
    base_app_id: str
    logo: str
    custom_autostart_script_b64: str | None = None
    custom_autostart_wayland_script_b64: str | None = None
    users: list[str]
    groups: list[str]


class LaunchMetaCustomizeRequest(BaseModel):
    """Launch a meta-app with its template mounted read-write."""

    application_id: str
    language: str | None = None
    selected_gpu: str | None = None
    wayland_mode: bool = True


class CreateAdminRequest(BaseModel):
    """Create an administrator."""

    username: str
    public_key: str | None = None


class HomeDirectoryList(BaseModel):
    """Home directories of a user."""

    home_dirs: list[str]


class HomeDirectoryCreate(BaseModel):
    """Create a home directory."""

    home_name: str = Field(..., pattern=r"^[a-zA-Z0-9_-]+$")


class ActiveSessionInfo(BaseModel):
    """A running session as seen by its owner."""

    session_id: str
    app_id: str
    app_name: str
    app_logo: str
    created_at: float
    session_url: str
    launch_context: dict[str, Any] | None = None
    is_collaboration: bool = False


class SendFileToSessionRequest(BaseModel):
    """Deliver an uploaded file into a running session."""

    filename: str
    upload_id: str
    total_chunks: int


class UserSessionList(BaseModel):
    """Sessions grouped by user for the admin view."""

    username: str
    sessions: list[ActiveSessionInfo]


class UploadInitiateRequest(BaseModel):
    """Start a chunked upload."""

    filename: str
    total_size: int


class UploadInitiateResponse(BaseModel):
    """Identifier of a chunked upload."""

    upload_id: str


class UploadChunkRequest(BaseModel):
    """One chunk of a chunked upload."""

    upload_id: str
    chunk_index: int
    chunk_data_b64: str


class UploadToStorageRequest(BaseModel):
    """Finalise an upload into the user's shared files."""

    filename: str
    upload_id: str
    total_chunks: int
    home_name: str


class FileListItem(BaseModel):
    """A directory entry."""

    name: str
    path: str
    is_dir: bool
    size: int
    mtime: float


class FileListResponse(BaseModel):
    """A page of directory entries."""

    items: list[FileListItem]
    path: str
    page: int
    per_page: int
    total: int


class CreateFolderRequest(BaseModel):
    """Create a folder inside a home directory."""

    path: str
    folder_name: str = Field(..., pattern=r"^[^/\\]+$")


class DeleteItemsRequest(BaseModel):
    """Delete files or folders inside a home directory."""

    paths: list[str]


class DeleteTaskResponse(BaseModel):
    """Handle of a background deletion."""

    message: str
    task_id: str


class DeleteStatusResponse(BaseModel):
    """Progress of a background deletion."""

    status: str
    message: str | None = None


class FinalizeUploadToDirRequest(BaseModel):
    """Finalise an upload into a directory of a home directory."""

    path: str
    filename: str
    upload_id: str
    total_chunks: int


class FileChunkResponse(BaseModel):
    """A chunk of a downloaded file."""

    chunk_data_b64: str
    is_last_chunk: bool


class GenericSuccessMessage(BaseModel):
    """A simple success message."""

    message: str


class ShareFileRequest(BaseModel):
    """Create a public share of a file."""

    home_dir: str
    path: str
    password: str | None = None
    expiry_hours: int | None = None


class PublicShareInfo(BaseModel):
    """A public share as shown to its owner."""

    share_id: str
    original_filename: str
    size_bytes: int
    created_at: float
    expiry_timestamp: float | None = None
    has_password: bool
    url: str


class PublicShareMetadata(BaseModel):
    """On-disk metadata of a public share."""

    owner_username: str
    original_filename: str
    created_at: float
    size_bytes: int
    password_hash: str | None = None
    expiry_timestamp: float | None = None


class LaunchRequestFilePath(BaseModel):
    """Launch an application with a file already on the server."""

    application_id: str
    home_name: str | None = None
    filename: str
    language: str | None = None
    selected_gpu: str | None = None
    wayland_mode: bool = True


class LaunchFromStorageRequest(BaseModel):
    """Ask the client to open the launcher for a server-side file."""

    filename: str
