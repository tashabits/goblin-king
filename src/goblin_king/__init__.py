"""Lightweight public package surface for Goblin King adopters."""

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
    NotebookGoblinRecord,
    NotebookServiceRecord,
    ProjectRecord,
    RepositoryEntryRecord,
    RepositoryVersionRecord,
    RunRecord,
    ScheduleRecord,
    UserRecord,
    WorkerValidationRecord,
)
from goblin_king.notebooks import (
    GoblinKingNotebookClient,
    NotebookASGIService,
    NotebookFunctionGoblin,
)
from goblin_king.project import ProjectSettings, ProjectSettingsError
from goblin_king.registry import (
    ENTRY_POINT_GROUP,
    GoblinRegistry,
    RegistryError,
    discover_entry_point_definitions,
)
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

_LAZY_COMPAT_EXPORTS = {
    "create_app": ("goblin_king.api", "create_app"),
    "Scheduler": ("goblin_king.scheduler", "Scheduler"),
    "SQLiteStore": ("goblin_king.store", "SQLiteStore"),
}

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
    "GoblinKingNotebookClient",
    "GoblinRegistry",
    "GoblinResult",
    "HandoffRecord",
    "HeartbeatRecord",
    "JobRecord",
    "LongServiceRecord",
    "NotebookASGIService",
    "NotebookFunctionGoblin",
    "NotebookGoblinRecord",
    "NotebookServiceRecord",
    "ProjectRecord",
    "PROJECT_CONFIG_API_VERSION",
    "PROJECT_CONFIG_KIND",
    "ProjectSettings",
    "ProjectSettingsError",
    "RegistryError",
    "REGISTRY_SCHEMA_VERSION",
    "RepositoryEntryRecord",
    "RepositoryVersionRecord",
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
    "WorkerValidationRecord",
    "create_app",
    "discover_entry_point_definitions",
    "init_package",
]


def __getattr__(name: str) -> object:
    """Load legacy heavy root exports only when callers explicitly request them."""
    if name not in _LAZY_COMPAT_EXPORTS:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attribute_name = _LAZY_COMPAT_EXPORTS[name]
    from importlib import import_module

    value = getattr(import_module(module_name), attribute_name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    """Return the documented root surface, including lazy compatibility exports."""
    return sorted(set(globals()) | set(__all__))
