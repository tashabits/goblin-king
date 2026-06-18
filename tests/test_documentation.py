"""Documentation integrity checks for user-facing manuals."""

from __future__ import annotations

import re
from pathlib import Path


def test_readme_table_of_contents_reaches_all_headings() -> None:
    """Verify each README heading has an anchor in the table of contents."""
    readme = Path("README.md").read_text(encoding="utf-8")
    headings = [
        line.lstrip("# ").strip()
        for line in readme.splitlines()
        if line.startswith("#")
    ]
    toc_end = readme.index("## Quick Start")
    toc = readme[:toc_end]

    for heading in headings:
        anchor = "#" + re.sub(r"[^a-z0-9 -]", "", heading.lower()).replace(" ", "-")
        assert f"]({anchor})" in toc


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
    assert "self-hosted control plane for validated container-backed workloads" in readme
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
