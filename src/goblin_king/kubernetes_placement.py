"""Validated placement metadata mapping for generated Kubernetes worker Pods."""

from __future__ import annotations

from typing import Any

from goblin_king.contracts import GoblinContext, GoblinDefinition

_PREFERRED_PLACEMENT_WEIGHT = 50


def placement_metadata(
    definition: GoblinDefinition | None,
    context: GoblinContext,
) -> dict[str, dict[str, str]] | None:
    """Resolve the first explicit placement declaration from definition or context."""
    metadata_sources: list[Any] = []
    if definition is not None:
        metadata_sources.append(definition.metadata)
    context_definition = context.metadata.get("goblin_definition")
    if isinstance(context_definition, dict):
        metadata_sources.append(context_definition.get("metadata"))
    metadata_sources.append(context.metadata)

    for metadata in metadata_sources:
        placement = _normalize_placement(metadata)
        if placement is not None:
            return placement
    return None


def apply_kubernetes_placement(
    pod_spec: dict[str, Any],
    placement: dict[str, dict[str, str]],
) -> None:
    """Map constrained placement labels into node selector and affinity fields."""
    required = placement.get("required") or {}
    if required:
        pod_spec["nodeSelector"] = required

    preferred = placement.get("preferred") or {}
    if preferred:
        affinity = pod_spec.setdefault("affinity", {})
        node_affinity = affinity.setdefault("nodeAffinity", {})
        node_affinity["preferredDuringSchedulingIgnoredDuringExecution"] = [
            {
                "weight": _PREFERRED_PLACEMENT_WEIGHT,
                "preference": {
                    "matchExpressions": [
                        {"key": key, "operator": "In", "values": [value]}
                        for key, value in preferred.items()
                    ]
                },
            }
        ]


def _normalize_placement(metadata: Any) -> dict[str, dict[str, str]] | None:
    if not isinstance(metadata, dict):
        return None
    placement = metadata.get("placement")
    if not isinstance(placement, dict):
        return None

    required = _placement_label_map(placement.get("required"))
    preferred = _placement_label_map(placement.get("preferred"))
    if not required and not preferred:
        return None
    return {"required": required, "preferred": preferred}


def _placement_label_map(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    labels = {
        key: label_value
        for key, label_value in value.items()
        if isinstance(key, str)
        and key
        and isinstance(label_value, str)
        and label_value
    }
    return dict(sorted(labels.items()))
