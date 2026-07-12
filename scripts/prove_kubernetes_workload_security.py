"""Run one retained Kubernetes Job for workload-security inspection."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from goblin_king.contracts import GoblinContext, GoblinDefinition
from goblin_king.kubernetes_runtime import KubernetesRuntime
from goblin_king.kubernetes_runtime_settings import KubernetesRuntimeSettings
from goblin_king.runtime_helpers import kubernetes_name
from goblin_king.workers import WorkerImageDefinition, WorkerImageMap

KIND = "example.echo"


class RetainedKubernetesRuntime(KubernetesRuntime):
    """Retain transient objects until the proof namespace is deleted externally."""

    def _cleanup(self, **_kwargs) -> None:
        return


def main() -> int:
    args = _arguments()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    workers = WorkerImageMap(
        {KIND: WorkerImageDefinition(context=".", image=args.worker_image)},
        root=".",
    )
    definition = GoblinDefinition(
        kind=KIND,
        display_name="Kubernetes security proof",
        module="container.only",
    )
    context = GoblinContext(
        run_id="issue-148-restricted-proof",
        artifact_root=".goblin-king/artifacts/issue-148-restricted-proof",
        metadata={"job_id": "issue-148-restricted-proof", "kind": KIND},
    )
    legacy = KubernetesRuntime(
        workers=workers,
        redis_url=args.redis_url,
        namespace=args.namespace,
        result_forwarder_image=args.forwarder_image,
        image_pull_policy="Never",
    )
    restricted_settings = KubernetesRuntimeSettings(
        result_forwarder_image=args.forwarder_image,
        worker_image_pull_policy="Never",
        result_forwarder_image_pull_policy="Never",
        workload_security_profile="restricted-v1",
    )
    restricted = RetainedKubernetesRuntime(
        workers=workers,
        redis_url=args.redis_url,
        namespace=args.namespace,
        settings=restricted_settings,
        poll_interval_seconds=0.25,
    )

    manifest_arguments = {
        "name": kubernetes_name(f"gk-{KIND}-{context.run_id}"),
        "config_name": kubernetes_name(f"gk-{KIND}-{context.run_id}") + "-input",
        "image": args.worker_image,
        "context": context,
        "worker_id": "k8s-worker-issue-148-proof",
        "timeout_seconds": 60,
        "kind": KIND,
    }
    _write_json(output / "legacy-manifest.json", legacy._job_manifest(**manifest_arguments))
    _write_json(
        output / "restricted-manifest.json",
        restricted._job_manifest(**manifest_arguments),
    )

    result = restricted.run(
        definition,
        None,
        {"message": "restricted live proof"},
        context,
        timeout_seconds=60,
    )
    summary = {
        "job_name": manifest_arguments["name"],
        "run_id": context.run_id,
        "result": result.model_dump(mode="json"),
        "security_identity": restricted_settings.validation_image_identity(
            args.worker_image,
            KIND,
        ),
    }
    _write_json(output / "result.json", summary)
    print(json.dumps(summary, indent=2))
    return 0 if result.status == "success" else 1


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--namespace", required=True)
    parser.add_argument("--redis-url", required=True)
    parser.add_argument("--worker-image", required=True)
    parser.add_argument("--forwarder-image", required=True)
    parser.add_argument("--output", type=Path, default=Path("/tmp/issue-148-proof"))
    return parser.parse_args()


def _write_json(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
