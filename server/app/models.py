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
    type: str = ""


class LaunchProgress(BaseModel):
    """Where a launch is (see `app.progress`).

    Attributes:
        stage: The stage the launch reached.
        detail: What the stage is about: `node` or `image`.
        elapsed: Seconds since the launch began.
        session_url: Path the session opens at, once `stage` is `ready`.
        session_id: Its id, once `stage` is `ready`.
        error: Why it failed, once `stage` is `failed`.
    """

    stage: str
    detail: dict[str, Any] = {}
    elapsed: float = 0
    session_url: str | None = None
    session_id: str | None = None
    error: str | None = None


class LaunchRequestSimple(BaseModel):
    """Launch an application with no context."""

    application_id: str
    home_name: str | None = None
    language: str | None = None
    timezone: str | None = None
    selected_gpu: str | None = None
    launch_in_room_mode: bool = False
    wayland_mode: bool = True


class LaunchRequestURL(BaseModel):
    """Launch an application and open a URL in it."""

    url: str
    application_id: str
    home_name: str | None = None
    language: str | None = None
    timezone: str | None = None
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
    timezone: str | None = None
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


class SignInConfig(BaseModel):
    """The sign-ins the web app offers."""

    oidc: bool
    saml: bool
    proxy: bool = False
    root: bool = True
    key: bool = True


class SignInRegistrationRequest(BaseModel):
    """The grant of a finished identity provider flow."""

    grant: str = Field(max_length=128)


class RootSignInRequest(BaseModel):
    """The root token."""

    token: str = Field(max_length=512)


class SignInRegistration(BaseModel):
    """A started web sign-in."""

    username: str
    via: str
    expires: float


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
    the app is stored as an `InstalledAppRecord`.
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


#: Fields of `InstalledApp` that belong to the record itself rather
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

    The record references the store entry (`source` and `source_app_id`)
    and keeps only the fields the administrator changed under `overrides`.
    The effective `InstalledApp` is the store entry deep-merged with
    `overrides`; see `config_store.resolve_app`.
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
    """One choice of a `select` template setting."""

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
    """Payload of `GET /api/ui/template_schema`."""

    settings: list[TemplateSchemaSetting]


class UiManifest(BaseModel):
    """Payload of `GET /api/ui/version`."""

    version: str
    bridge: int


class UserSettings(BaseModel):
    """A user's own settings, and the form of the settings a request runs under.

    Attributes:
        active: The account may sign in.
        group: The first of `groups`, as older clients read it.
        groups: Groups the user is in.
        admin: The user administers the server.
        persistent_storage: The user has home directories.
        public_sharing: The user may share files by public link.
        harden_container: Sessions run with the container hardening.
        harden_openbox: Sessions run with the desktop hardening.
        edit_templates: The user may edit app templates.
        gpu: Sessions may use a GPU.
        gpu_share: Sessions may use a GPU other sessions use; off gives each a GPU of its own.
        home_migration: The user may move their home directories between nodes.
        storage_limit: Gigabytes of storage per node; negative for no limit.
        session_limit: Sessions at once, on all nodes; negative for no limit.
        session_cpus: CPUs per session; negative for no limit.
        session_memory_mb: Megabytes of memory per session; negative for no limit.
        session_hours: Hours a session may run; negative for no limit.
        allowance_hours: Weighted session hours per `allowance_period`; negative for no limit.
        allowance_period: `day`, `week`, or `month`.
        pools: Restricted pools open to the user.
        pools_denied: Pools closed to the user.
        proot_catalog: Id of the PRoot Apps catalog the user's sessions install from, or `None`.
        provider_groups: Groups the identity provider named at the last sign-in.
        approved: False for a user a sign-in created who waits for an administrator; the server keeps it.
    """

    active: bool = True
    group: str = "none"
    groups: list[str] = []
    admin: bool = False
    persistent_storage: bool = True
    public_sharing: bool = False
    harden_container: bool = False
    harden_openbox: bool = False
    edit_templates: bool = False
    gpu: bool = True
    gpu_share: bool = True
    home_migration: bool = False
    storage_limit: int = -1
    session_limit: int = -1
    session_cpus: float = -1
    session_memory_mb: int = -1
    session_hours: float = -1
    allowance_hours: float = -1
    allowance_period: str = Field(default="month", pattern=r"^(day|week|month)$")
    pools: list[str] = []
    pools_denied: list[str] = []
    proot_catalog: str | None = None
    provider_groups: list[str] = []
    approved: bool = True


class GroupSettings(BaseModel):
    """What a group sets for its members; a setting left out is not the group's to decide.

    Where the groups of a user disagree, the restricting value of a switch
    wins and the smallest limit does (see `user_manager.get_effective_settings`).

    Attributes:
        proot_catalog: Id of the PRoot Apps catalog the members' sessions install from.
        sso_groups: Identity provider groups whose members are in this group.
    """

    active: bool | None = None
    admin: bool | None = None
    persistent_storage: bool | None = None
    public_sharing: bool | None = None
    harden_container: bool | None = None
    harden_openbox: bool | None = None
    edit_templates: bool | None = None
    gpu: bool | None = None
    gpu_share: bool | None = None
    home_migration: bool | None = None
    storage_limit: int | None = None
    session_limit: int | None = None
    session_cpus: float | None = None
    session_memory_mb: int | None = None
    session_hours: float | None = None
    allowance_hours: float | None = None
    allowance_period: str | None = Field(default=None, pattern=r"^(day|week|month)$")
    pools: list[str] = []
    pools_denied: list[str] = []
    proot_catalog: str | None = None
    sso_groups: list[str] = []


class AdminStatusResponse(BaseModel):
    """Status payload returned to every authenticated user; `held` marks one who waits for an administrator."""

    is_admin: bool
    held: bool = False
    username: str
    settings: UserSettings
    via: str = "key"
    sign_out_url: str = ""
    session_domain: str = ""
    session_isolation: bool = False
    clustered: bool = False
    node_id: str = ""
    allowance: dict[str, Any] | None = None
    gpus: list[GPUInfo] = []
    cpu_model: str | None = None
    disk_total: int | None = None
    disk_used: int | None = None
    proxy_cert_expires_at: float | None = None


class ProotCatalogApp(BaseModel):
    """One app of a PRoot Apps catalog: a package of a remote.

    Attributes:
        remote: GitHub `owner/repo` the app is published from.
        name: The app's name there, which is its image tag.
    """

    remote: str = Field(..., pattern=r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
    name: str = Field(..., pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")


class ProotCatalog(BaseModel):
    """A PRoot Apps catalog: the apps an administrator picked for a folder sessions install from.

    Attributes:
        id: Generated identifier; the folder of the catalog on every node.
        name: Name shown in the dashboard and the user and group settings.
        apps: The apps in the catalog.
        auto_update: Fetch the apps again on the auto-update interval when their package changed.
        revision: Bumped by every change and by an update asked for by hand; a node whose copy
            is of an older revision syncs it.
    """

    id: str = Field(..., pattern=r"^[A-Za-z0-9_-]{1,64}$")
    name: str = Field(..., min_length=1, max_length=80)
    apps: list[ProotCatalogApp] = []
    auto_update: bool = True
    revision: int = 1


class ProotCatalogName(BaseModel):
    """What a user or group setting picks a catalog by."""

    id: str
    name: str


class ProotCatalogStatus(ProotCatalog):
    """A catalog with the state of this node's copy of it.

    Attributes:
        state: `pending` before the first sync, `syncing`, `ready`, or `error`.
        message: What the last sync reported, or the error that ended it.
        synced_revision: The revision this node's copy was made from.
        synced_at: Unix time the last sync finished.
        done: Apps handled so far by a running sync.
        total: Apps a running sync handles.
        current: The app a running sync is on.
        size: Bytes the copy takes on this node.
        present: Apps whose package is in the copy, keyed by image folder.
    """

    state: str = "pending"
    message: str = ""
    synced_revision: int = 0
    synced_at: float | None = None
    done: int = 0
    total: int = 0
    current: str = ""
    size: int = 0
    present: dict[str, dict[str, Any]] = {}


class ProotRemoteApp(BaseModel):
    """An app a PRoot Apps remote publishes, as its metadata lists it.

    Attributes:
        remote: The remote.
        name: The app's name, its image tag.
        full_name: Display name.
        description: What the app is.
        arch: Comma-separated platforms the package is built for.
        icon: URL of the app's icon.
        disabled: The remote marks the app as not offered.
    """

    remote: str
    name: str
    full_name: str = ""
    description: str = ""
    arch: str = ""
    icon: str = ""
    disabled: bool = False


class User(BaseModel):
    """A user or administrator."""

    username: str
    public_key: str
    is_admin: bool
    settings: UserSettings | None = None
    #: Waiting for an administrator (see `user_manager.held`); set in the dashboard's listing.
    held: bool = False
    #: Signs in as an administrator (see `user_manager.administers`); set in the dashboard's listing.
    admin: bool = False


class Group(BaseModel):
    """A group of users sharing settings."""

    name: str
    settings: GroupSettings


class ManagementDataResponse(BaseModel):
    """Everything the admin dashboard needs in one call."""

    admins: list[User]
    users: list[User]
    groups: list[Group]
    server_public_key: str
    api_port: int
    session_port: int
    gpus: list[GPUInfo] = []
    proot_catalogs: list[ProotCatalogName] = []
    proot_remote: str = ""


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
    settings: GroupSettings


class UpdateGroupRequest(BaseModel):
    """Replace a group's settings."""

    settings: GroupSettings


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
    timezone: str | None = None
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
    own_origin: bool = False
    node: str = ""
    home: str = ""
    gpu: bool = False
    gpu_device: str = ""
    language: str = ""
    wayland_mode: bool = True


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
    timezone: str | None = None
    selected_gpu: str | None = None
    wayland_mode: bool = True


class LaunchFromStorageRequest(BaseModel):
    """Ask the client to open the launcher for a server-side file."""

    filename: str
