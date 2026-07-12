"""Shared construction boundary for generated Kubernetes worker runtimes."""

from __future__ import annotations

from goblin_king.events import EventBus
from goblin_king.kubernetes_runtime import KubernetesRuntime
from goblin_king.kubernetes_runtime_settings import KubernetesRuntimeSettings
from goblin_king.workers import WorkerImageMap


def build_kubernetes_runtime(
    *,
    workers: WorkerImageMap,
    redis_url: str,
    event_bus: EventBus | None,
    settings: KubernetesRuntimeSettings,
) -> KubernetesRuntime:
    """Build every control-plane worker runtime from one typed settings boundary."""
    return KubernetesRuntime(
        workers=workers,
        redis_url=redis_url,
        event_bus=event_bus,
        settings=settings,
    )
