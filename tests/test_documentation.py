"""Documentation integrity checks for user-facing manuals."""

from __future__ import annotations

import re
from pathlib import Path


def test_readme_table_of_contents_reaches_primary_sections() -> None:
    """Verify the simplified README TOC reaches each primary section."""
    readme = Path("README.md").read_text(encoding="utf-8")
    expected_headings = [
        "Why Goblin King?",
        "Mental Model",
        "Quick Start",
        "Use Goblin King In Your Project",
        "Validation Gate",
        "Resource Policies",
        "Admin UI",
        "Docker, Compose, Kubernetes, And Helm",
        "JupyterHub Lab Compatibility",
        "Goblin Directory",
        "Examples",
        "Documentation",
        "Future Work",
        "Contributing, Security, And License",
    ]
    toc_end = readme.index("## Quick Start")
    toc = readme[:toc_end]

    for heading in expected_headings:
        anchor = "#" + re.sub(r"[^a-z0-9 -]", "", heading.lower()).replace(" ", "-")
        assert f"]({anchor})" in toc

    assert "Advanced Commands And Local Proof" not in toc


def test_adopter_guide_is_linked_from_readme() -> None:
    """Verify the complete adopter guide is discoverable from the README."""
    readme = Path("README.md").read_text(encoding="utf-8")

    assert Path("docs/adopter-guide.md").exists()
    assert "docs/adopter-guide.md" in readme


def test_workload_types_are_documented() -> None:
    """Verify the current goblin workload model is discoverable and container-first."""
    readme = Path("README.md").read_text(encoding="utf-8")
    workload_doc = Path("docs/goblin-workload-types.md").read_text(encoding="utf-8")
    what_is_doc = Path("docs/what-is-a-goblin.md").read_text(encoding="utf-8")

    assert "docs/goblin-workload-types.md" in readme
    assert "container-driven task scheduler and control plane" in readme
    assert "validated container-backed" in readme
    assert "A goblin is a validated container-backed workload" in workload_doc
    assert "Task Goblins" in workload_doc
    assert "Service Goblins" in workload_doc
    assert "Notebook Function Goblins" in workload_doc
    assert "Notebook Service Goblins" in workload_doc
    assert "Directory Goblins" in workload_doc
    assert "configured runner containers" in workload_doc
    assert "not a public marketplace" in workload_doc
    assert "A goblin is a validated container-backed workload" in what_is_doc


def test_mandatory_validation_gate_is_documented() -> None:
    """Verify the scheduler validation gate has one canonical user-facing design doc."""
    readme = Path("README.md").read_text(encoding="utf-8")
    validation_doc = Path("docs/goblin-contract-validation.md").read_text(encoding="utf-8")

    assert "docs/goblin-contract-validation.md" in readme
    assert "Goblin King does not schedule arbitrary unvalidated container images" in validation_doc
    assert "validate first, then schedule" in validation_doc.lower()
    assert "| Condition | Scheduler behavior | Operator fix |" in validation_doc


def test_kubernetes_workload_security_migration_is_documented() -> None:
    """Keep the compatibility default and secure opt-in path discoverable."""
    readme = Path("README.md").read_text(encoding="utf-8")
    security_doc = Path("docs/kubernetes-workload-security.md").read_text(
        encoding="utf-8"
    )
    security_flat = " ".join(security_doc.split())

    assert "docs/kubernetes-workload-security.md" in readme
    assert "`legacy` remains the default" in security_flat
    assert "`restricted-v1`" in security_doc
    assert "automountServiceAccountToken: false" in security_doc
    assert "mounted only in the worker container" in security_flat
    assert "resourcePolicies.defaults.filesystem.read_only_root=true" in security_flat
    assert "64 MiB request and 128 MiB limit" in security_flat


def test_kubernetes_artifact_retention_is_documented_with_honest_proof_limits() -> None:
    """Keep retention, security, upgrade, compatibility, and proof guidance discoverable."""
    readme = Path("README.md").read_text(encoding="utf-8")
    retention = Path("docs/kubernetes-artifact-retention.md").read_text(encoding="utf-8")
    proof = Path("docs/proofs/issue-147-kubernetes-artifact-retention.md").read_text(
        encoding="utf-8"
    )
    acceptance = Path("scripts/kubernetes_artifact_retention_proof.py").read_text(
        encoding="utf-8"
    )
    upgrade = Path("docs/UPGRADING.md").read_text(encoding="utf-8")
    security = Path("docs/security-model.md").read_text(encoding="utf-8")

    assert "docs/kubernetes-artifact-retention.md" in readme
    assert "persistence.artifactSubdirectory" in retention
    assert "all-or-nothing" in retention
    assert "ReadWriteMany" in retention
    assert "scripts/kubernetes_artifact_retention_proof.py" in proof
    assert acceptance.index('"/admin/workers/validate-kubernetes"') < acceptance.index(
        '"/jobs"'
    )
    assert '"/goblins"' in acceptance
    assert "assert_artifact_root_empty" in acceptance
    assert '"-mindepth"' in acceptance
    assert "validation_identity" in acceptance
    assert "## Final Automated Acceptance" in proof
    assert "validation-first, cross-identity, cleanup-complete cluster receipt" in proof
    assert "Kubernetes Artifact Retention" in upgrade
    assert "Kubernetes Artifact Boundary" in security


def test_repository_operator_docs_cover_stack_enablement() -> None:
    """Verify optional Directory service docs cover local and cluster operators."""
    readme = Path("README.md").read_text(encoding="utf-8")
    repository_doc = Path("docs/goblin-directory.md").read_text(encoding="utf-8")
    repository_doc_flat = " ".join(repository_doc.split())

    assert "docs/goblin-directory.md" in readme
    assert '"repository": {' in repository_doc
    assert "## Docker Compose Enablement" in repository_doc
    assert "## Helm Enablement" in repository_doc
    assert "## JupyterHub Stack Enablement" in repository_doc
    assert "## Invoke Published Goblins By Name" in repository_doc
    assert "## Notebook Directory Workflow" in repository_doc
    assert "/repository/functions/demo.hello/run" in repository_doc
    assert "/repository/services/demo.long-hello/start" in repository_doc
    assert "client.submit_repository_function" in repository_doc
    assert "client.submit_repository_service" in repository_doc
    assert "client.approve_repository_entry" in repository_doc
    assert "workbook-directory-submit.ipynb" in repository_doc
    assert "workbook-directory-admin.ipynb" in repository_doc
    assert "workbook-directory-consume.ipynb" in repository_doc
    assert "make jupyterhub-directory-proof" in repository_doc
    assert "client.run_directory_function" in repository_doc
    assert "client.directory_service" in repository_doc
    assert "JupyterLab Goblin Directory Picker" in repository_doc
    assert "published entries in a dropdown" in repository_doc
    assert "Non-admin callers cannot request another `project_id`" in repository_doc
    assert "## Directory Scope" in repository_doc
    assert "belongs to one Goblin King deployment" in repository_doc
    assert "Approval is a sharing gate rather than a security certification" in repository_doc
    assert "Validation proves contract compliance, not trustworthiness" in repository_doc_flat


def test_jupyterhub_docs_cover_repository_proof_users() -> None:
    """Verify Hub docs describe the Directory proof target and users."""
    hub_doc = Path("docs/jupyterhub-service-access.md").read_text(encoding="utf-8")
    hub_doc_flat = " ".join(hub_doc.split())

    assert "make jupyterhub-directory-proof" in hub_doc
    assert "make jupyterhub-directory-ui-proof" in hub_doc
    assert "`bob` submits" in hub_doc
    assert "`alice` approves" in hub_doc
    assert "`carol` searches" in hub_doc
    assert "`mallory` is denied" in hub_doc
    assert "configured function runner container" in hub_doc_flat
    assert "configured service runner container" in hub_doc_flat
    assert "deployment-local" in hub_doc
    assert "goblin-king-directory-singleuser" in hub_doc
    assert "/goblin-directory/api/..." in hub_doc
