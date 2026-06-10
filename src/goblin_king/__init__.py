"""Public package surface for the Goblin King scheduler kernel."""

from goblin_king.api import create_app
from goblin_king.api_settings import ApiSettings, ApiSettingsError
from goblin_king.contracts import (
    ApiTokenRecord,
    ArtifactRecord,
    AuditLogRecord,
    EventRecord,
    FanoutRecord,
    GoblinContext,
    GoblinDefinition,
    GoblinResult,
    HandoffRecord,
    HeartbeatRecord,
    JobRecord,
    LongServiceRecord,
    ProjectRecord,
    RunRecord,
    ScheduleRecord,
    UserRecord,
)
from goblin_king.project import ProjectSettings, ProjectSettingsError
from goblin_king.registry import (
    ENTRY_POINT_GROUP,
    GoblinRegistry,
    RegistryError,
    discover_entry_point_definitions,
)
from goblin_king.scheduler import Scheduler
from goblin_king.store import SQLiteStore
from goblin_king.templates import TemplateError, init_package
from goblin_king.versions import (
    API_SETTINGS_SCHEMA_VERSION,
    GOBLIN_CONTAINER_CONTRACT_VERSION,
    PROJECT_CONFIG_API_VERSION,
    PROJECT_CONFIG_KIND,
    REGISTRY_SCHEMA_VERSION,
    WORKER_HEARTBEAT_CONTRACT_VERSION,
    WORKER_IMAGE_MAP_SCHEMA_VERSION,
    WORKER_RESULT_CONTRACT_VERSION,
)
from goblin_king.workers import (
    WorkerConfigError,
    WorkerImageDefinition,
    WorkerImageMap,
)

__all__ = [
    "ApiSettings",
    "ApiSettingsError",
    "API_SETTINGS_SCHEMA_VERSION",
    "ApiTokenRecord",
    "ArtifactRecord",
    "AuditLogRecord",
    "ENTRY_POINT_GROUP",
    "EventRecord",
    "FanoutRecord",
    "GOBLIN_CONTAINER_CONTRACT_VERSION",
    "GoblinContext",
    "GoblinDefinition",
    "GoblinRegistry",
    "GoblinResult",
    "HandoffRecord",
    "HeartbeatRecord",
    "JobRecord",
    "LongServiceRecord",
    "ProjectRecord",
    "PROJECT_CONFIG_API_VERSION",
    "PROJECT_CONFIG_KIND",
    "ProjectSettings",
    "ProjectSettingsError",
    "RegistryError",
    "REGISTRY_SCHEMA_VERSION",
    "RunRecord",
    "ScheduleRecord",
    "Scheduler",
    "SQLiteStore",
    "TemplateError",
    "UserRecord",
    "WorkerConfigError",
    "WORKER_HEARTBEAT_CONTRACT_VERSION",
    "WorkerImageDefinition",
    "WorkerImageMap",
    "WORKER_IMAGE_MAP_SCHEMA_VERSION",
    "WORKER_RESULT_CONTRACT_VERSION",
    "create_app",
    "discover_entry_point_definitions",
    "init_package",
]
