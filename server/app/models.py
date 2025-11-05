from pydantic import BaseModel, Field
from typing import List, Optional
import uuid


class GPUInfo(BaseModel):
    device: str
    driver: str

class Application(BaseModel):
    id: str
    name: str
    logo: str
    home_directories: bool
    nvidia_support: bool
    dri3_support: bool
    url_support: bool
    extensions: List[str]
    is_meta_app: bool = False

class LaunchRequestSimple(BaseModel):
    application_id: str
    home_name: Optional[str] = None
    language: Optional[str] = None
    selected_gpu: Optional[str] = None
    launch_in_room_mode: bool = False

class LaunchRequestURL(BaseModel):
    url: str
    application_id: str
    home_name: Optional[str] = None
    language: Optional[str] = None
    selected_gpu: Optional[str] = None
    launch_in_room_mode: bool = False

class LaunchRequestFile(BaseModel):
    application_id: str
    filename: str
    upload_id: str
    total_chunks: int
    open_file_on_launch: bool = True
    home_name: Optional[str] = None
    language: Optional[str] = None
    selected_gpu: Optional[str] = None
    launch_in_room_mode: bool = False

class LaunchResponse(BaseModel):
    session_url: str
    session_id: str

class HandshakeInitiateResponse(BaseModel):
    nonce: str
    signature: str

class HandshakeExchangeRequest(BaseModel):
    encrypted_session_key: str

class HandshakeExchangeResponse(BaseModel):
    session_id: str

class EncryptedPayload(BaseModel):
    iv: str
    ciphertext: str

class AppStore(BaseModel):
    name: str
    url: str

class AvailableAppProviderConfig(BaseModel):
    image: str
    port: int
    nvidia_support: bool
    dri3_support: bool
    type: str
    url_support: bool
    open_support: bool
    extensions: List[str]
    autostart: Optional[bool] = False
    custom_autostart_script_b64: Optional[str] = None

class AvailableApp(BaseModel):
    id: str
    name: str
    logo: str
    url: str
    provider: str
    provider_config: AvailableAppProviderConfig

class EnvVar(BaseModel):
    name: str
    value: str

class InstalledAppProviderConfig(AvailableAppProviderConfig):
    env: Optional[List[EnvVar]] = []

class InstalledApp(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str
    logo: str
    url: str
    source: str
    source_app_id: str
    provider: str
    home_directories: bool
    users: List[str]
    groups: List[str]
    provider_config: InstalledAppProviderConfig
    auto_update: bool = True
    app_template: str
    is_meta_app: bool = False
    base_app_id: Optional[str] = None
    home_template_name: Optional[str] = None

class InstalledAppWithStatus(InstalledApp):
    image_sha: Optional[str] = None
    last_checked_at: Optional[float] = None
    pull_status: Optional[str] = None

class ImageUpdateCheckResponse(BaseModel):
    current_sha: Optional[str]
    update_available: bool

class ImagePullResponse(BaseModel):
    status: str
    new_sha: Optional[str]

class AppTemplate(BaseModel):
    name: str
    settings: dict

class UserSettings(BaseModel):
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
    is_admin: bool
    username: str
    settings: UserSettings
    gpus: List[GPUInfo] = []
    cpu_model: Optional[str] = None
    disk_total: Optional[int] = None
    disk_used: Optional[int] = None

class User(BaseModel):
    username: str
    public_key: str
    is_admin: bool
    settings: Optional[UserSettings] = None

class Group(BaseModel):
    name: str
    settings: UserSettings

class ManagementDataResponse(BaseModel):
    admins: List[User]
    users: List[User]
    groups: List[Group]
    server_public_key: str
    api_port: int
    session_port: int
    gpus: List[GPUInfo] = []

class CreateUserRequest(BaseModel):
    username: str
    public_key: Optional[str] = None
    settings: UserSettings

class CreateUserResponse(BaseModel):
    user: User
    private_key: Optional[str]

class UpdateUserRequest(BaseModel):
    settings: UserSettings

class CreateGroupRequest(BaseModel):
    name: str = Field(..., pattern=r"^[a-zA-Z0-9_-]+$")
    settings: UserSettings

class UpdateGroupRequest(BaseModel):
    settings: UserSettings

class CreateMetaAppRequest(BaseModel):
    name: str
    base_app_id: str
    logo: str
    custom_autostart_script_b64: Optional[str] = None
    users: List[str]
    groups: List[str]

class LaunchMetaCustomizeRequest(BaseModel):
    application_id: str
    language: Optional[str] = None
    selected_gpu: Optional[str] = None

class CreateAdminRequest(BaseModel):
    username: str
    public_key: Optional[str] = None

class HomeDirectoryList(BaseModel):
    home_dirs: List[str]

class HomeDirectoryCreate(BaseModel):
    home_name: str = Field(..., pattern=r"^[a-zA-Z0-9_-]+$")

class ActiveSessionInfo(BaseModel):
    session_id: str
    app_id: str
    app_name: str
    app_logo: str
    created_at: float
    session_url: str
    launch_context: Optional[dict] = None
    is_collaboration: bool = False

class SendFileToSessionRequest(BaseModel):
    filename: str
    upload_id: str
    total_chunks: int

class UserSessionList(BaseModel):
    username: str
    sessions: List[ActiveSessionInfo]

class UploadInitiateRequest(BaseModel):
    filename: str
    total_size: int


class UploadInitiateResponse(BaseModel):
    upload_id: str

class UploadChunkRequest(BaseModel):
    upload_id: str
    chunk_index: int
    chunk_data_b64: str

class UploadToStorageRequest(BaseModel):
    filename: str
    upload_id: str
    total_chunks: int
    home_name: str

class FileListItem(BaseModel):
    name: str
    path: str
    is_dir: bool
    size: int
    mtime: float

class FileListResponse(BaseModel):
    items: List[FileListItem]
    path: str
    page: int
    per_page: int
    total: int

class CreateFolderRequest(BaseModel):
    path: str
    folder_name: str = Field(..., pattern=r"^[^/\\]+$")

class DeleteItemsRequest(BaseModel):
    paths: List[str]

class DeleteTaskResponse(BaseModel):
    message: str
    task_id: str

class DeleteStatusResponse(BaseModel):
    status: str
    message: Optional[str] = None

class FinalizeUploadToDirRequest(BaseModel):
    path: str
    filename: str
    upload_id: str
    total_chunks: int

class FileChunkResponse(BaseModel):
    chunk_data_b64: str
    is_last_chunk: bool

class GenericSuccessMessage(BaseModel):
    message: str

class ShareFileRequest(BaseModel):
    home_dir: str
    path: str
    password: Optional[str] = None
    expiry_hours: Optional[int] = None

class PublicShareInfo(BaseModel):
    share_id: str
    original_filename: str
    size_bytes: int
    created_at: float
    expiry_timestamp: Optional[float] = None
    has_password: bool
    url: str

class PublicShareMetadata(BaseModel):
    owner_username: str
    original_filename: str
    created_at: float
    size_bytes: int
    password_hash: Optional[str] = None
    expiry_timestamp: Optional[float] = None
