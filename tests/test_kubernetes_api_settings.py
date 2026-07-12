"""API route proof for the shared Kubernetes runtime settings object."""

from pathlib import Path

from fastapi.testclient import TestClient

from goblin_king.api import create_app
from goblin_king.api_settings import ApiSettings
from goblin_king.kubernetes_runtime_settings import KubernetesRuntimeSettings
from goblin_king.store import SQLiteStore
from goblin_king.validation import WorkerValidationResult


def test_notebook_and_repository_validation_share_runtime_settings(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runtime_settings = KubernetesRuntimeSettings(
        result_forwarder_image="registry.example/control@sha256:" + "a" * 64,
        workload_image_pull_secret_names=["registry-main"],
        workload_security_profile="restricted-v1",
        restricted_workload={
            "worker_service_account_names": {
                "notebook.settings-proof": "goblin-notebook-reader"
            }
        },
    )
    api_settings = ApiSettings(
        registry=Path("examples/goblins.json").resolve(),
        images=Path("goblin-images.json").resolve(),
        db=tmp_path / "api.sqlite3",
        artifact_root=tmp_path / "artifacts",
        auth_token="test-token",
        repository={"enabled": True, "url": "http://repository:8000"},
        kubernetes_runtime=runtime_settings,
    )
    client = TestClient(create_app(api_settings))
    captured: list[KubernetesRuntimeSettings] = []

    def fake_kubernetes_validation(**kwargs):
        captured.append(kwargs["kubernetes_runtime_settings"])
        record = kwargs["record"]
        return WorkerValidationResult(
            kind=record.kind,
            ok=True,
            image=record.image,
            image_digest="sha256:test",
            result_status="success",
            checks=["kubernetes-job"],
        )

    monkeypatch.setattr("goblin_king.api._running_in_kubernetes", lambda: True)
    monkeypatch.setattr(
        "goblin_king.api._validate_notebook_with_kubernetes",
        fake_kubernetes_validation,
    )

    notebook = client.post(
        "/notebooks/goblins",
        headers=_admin_headers(),
        json={
            "kind": "notebook.settings-proof",
            "source": "def run(payload):\n    return payload\n",
            "function_name": "run",
        },
    )
    assert notebook.status_code == 200
    notebook_validation = client.post(
        "/notebooks/goblins/notebook.settings-proof/validate",
        headers=_admin_headers(),
        json={"input": {}},
    )
    assert notebook_validation.status_code == 200

    user = client.post(
        "/admin/users",
        headers=_admin_headers(),
        json={"email": "owner@example.test", "display_name": "Owner"},
    ).json()
    project = client.post(
        "/admin/projects",
        headers=_admin_headers(),
        json={"name": "Settings proof"},
    ).json()
    token = client.post(
        "/admin/tokens",
        headers=_admin_headers(),
        json={
            "name": "owner-token",
            "user_id": user["id"],
            "project_id": project["id"],
            "role": "member",
        },
    ).json()["raw_token"]
    owner_headers = {"Authorization": f"Bearer {token}"}
    submitted = client.post(
        "/repository/entries",
        headers=owner_headers,
        json={
            "name": "shared.settings-proof",
            "type": "notebook_function",
            "source": "def run(payload):\n    return payload\n",
            "function_name": "run",
        },
    )
    assert submitted.status_code == 200
    entry_id = submitted.json()["entry"]["id"]
    repository_kind = submitted.json()["version"]["kind"]
    repository_validation = client.post(
        f"/repository/entries/{entry_id}/validate",
        headers=owner_headers,
        json={"input": {}},
    )
    assert repository_validation.status_code == 200

    assert len(captured) == 2
    assert all(settings is api_settings.kubernetes_runtime for settings in captured)
    store = SQLiteStore(api_settings.db)
    for kind in ("notebook.settings-proof", repository_kind):
        proof = store.latest_worker_validation_for_kind(kind)
        assert proof is not None
        assert proof.effective_policy == {
            "kubernetes_workload_security": (
                runtime_settings.effective_workload_security(kind)
            )
        }


def _admin_headers() -> dict[str, str]:
    return {"Authorization": "Bearer test-token"}
