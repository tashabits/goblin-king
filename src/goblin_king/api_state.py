"""Shared API application state and discovery loading."""

from __future__ import annotations

from pathlib import Path
from threading import RLock

from goblin_king.api_models import DiscoverySourcesResponse, DiscoveryStatusResponse
from goblin_king.api_settings import ApiSettings
from goblin_king.contracts import utc_now
from goblin_king.events import EventBus
from goblin_king.project import ProjectSettings, ProjectSettingsError
from goblin_king.registry import GoblinRegistry, RegistryError
from goblin_king.resource_policies import ResourcePolicySet
from goblin_king.store import SQLiteStore
from goblin_king.workers import WorkerConfigError, WorkerImageMap


class AppState:
    """Runtime dependencies shared by API route handlers."""

    def __init__(self, settings: ApiSettings) -> None:
        self.settings = settings
        self.store = SQLiteStore(settings.db)
        self._discovery_lock = RLock()
        self.discovery_version = 1
        self.last_successful_reload_at = utc_now()
        self.last_failed_reload_at = None
        self.last_discovery_error: str | None = None
        self._source_registry_files: list[Path] = []
        self._source_entry_points_enabled = False
        self._source_worker_image_map = settings.images
        self._project_defined_kinds: set[str] = set()
        self.registry, self.workers = self._load_discovery_state()
        self.artifact_root = settings.artifact_root.resolve()
        self.artifact_root.mkdir(parents=True, exist_ok=True)
        self.event_bus = EventBus(store=self.store, redis_url=settings.redis_url)
        self.resource_policies = (
            ResourcePolicySet.from_path(settings.resource_policies)
            if settings.resource_policies and settings.resource_policies.exists()
            else None
        )

    def reload_discovery(self) -> DiscoveryStatusResponse:
        """Reload registry and worker mappings while preserving the last good state on failure."""
        with self._discovery_lock:
            try:
                registry, workers = self._load_discovery_state()
            except (ProjectSettingsError, RegistryError, WorkerConfigError) as error:
                self.last_failed_reload_at = utc_now()
                self.last_discovery_error = str(error)
                raise
            self.registry = registry
            self.workers = workers
            self.discovery_version += 1
            self.last_successful_reload_at = utc_now()
            self.last_discovery_error = None
            return self.discovery_status()

    def discovery_status(self) -> DiscoveryStatusResponse:
        """Return a compact operator summary for the active discovery state."""
        with self._discovery_lock:
            worker_kinds = {kind for kind, _ in self.workers.items()}
            goblin_kinds = [definition.kind for definition in self.registry.list()]
            worker_unmapped = sorted(kind for kind in goblin_kinds if kind not in worker_kinds)
            return DiscoveryStatusResponse(
                active_goblin_count=len(goblin_kinds),
                worker_mapped_count=len([kind for kind in goblin_kinds if kind in worker_kinds]),
                worker_unmapped=worker_unmapped,
                discovery_version=self.discovery_version,
                last_successful_reload_at=self.last_successful_reload_at,
                last_failed_reload_at=self.last_failed_reload_at,
                last_error=self.last_discovery_error,
            )

    def discovery_sources(self) -> DiscoverySourcesResponse:
        """Return loaded source details and worker coverage for the admin Discovery panel."""
        with self._discovery_lock:
            worker_kinds = sorted(kind for kind, _ in self.workers.items())
            goblin_kinds = [definition.kind for definition in self.registry.list()]
            worker_unmapped = sorted(kind for kind in goblin_kinds if kind not in set(worker_kinds))
            has_duplicate_error = (
                self.last_discovery_error is not None
                and "duplicate goblin kind" in self.last_discovery_error
            )
            duplicate_errors = [self.last_discovery_error] if has_duplicate_error else []
            rejected = [self.last_discovery_error] if self.last_discovery_error else []
            return DiscoverySourcesResponse(
                project_settings=str(self.settings.project) if self.settings.project else None,
                registry_files=[str(path) for path in self._source_registry_files],
                entry_points_enabled=self._source_entry_points_enabled,
                worker_image_map=str(self._source_worker_image_map),
                goblin_kinds=goblin_kinds,
                worker_mapped_kinds=worker_kinds,
                worker_unmapped_kinds=worker_unmapped,
                rejected_definitions=rejected,
                duplicate_kind_errors=duplicate_errors,
            )

    def _load_discovery_state(self) -> tuple[GoblinRegistry, WorkerImageMap]:
        """Load registry and worker image sources without mutating active state."""
        if self.settings.project is not None:
            project = ProjectSettings.from_path(self.settings.project)
            registry_files = project.registries
            entry_points_enabled = project.entry_points
            worker_image_map = project.images
            project_definitions = project.registry_definitions()
            registry = GoblinRegistry.from_project_sources(
                registry_files,
                include_entry_points=entry_points_enabled,
                definitions=project_definitions,
            )
            project_workers = project.worker_definitions()
        else:
            registry_files = [self.settings.registry]
            entry_points_enabled = False
            worker_image_map = self.settings.images
            registry = GoblinRegistry.from_path(self.settings.registry)
            project_definitions = []
            project_workers = {}
        workers = WorkerImageMap.from_path_and_definitions(worker_image_map, project_workers)
        self._source_registry_files = list(registry_files)
        self._source_entry_points_enabled = entry_points_enabled
        self._source_worker_image_map = worker_image_map
        self._project_defined_kinds = {definition.kind for definition in project_definitions}
        return registry, workers
