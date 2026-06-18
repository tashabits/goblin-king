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


def test_mandatory_validation_gate_is_documented() -> None:
    """Verify the scheduler validation gate has one canonical user-facing design doc."""
    readme = Path("README.md").read_text(encoding="utf-8")
    validation_doc = Path("docs/goblin-contract-validation.md").read_text(encoding="utf-8")

    assert "docs/goblin-contract-validation.md" in readme
    assert "Goblin King does not schedule arbitrary unvalidated container images" in validation_doc
    assert "validate first, then schedule" in validation_doc.lower()
    assert "| Condition | Scheduler behavior | Operator fix |" in validation_doc


def test_repository_operator_docs_cover_stack_enablement() -> None:
    """Verify optional repository service docs cover local and cluster operators."""
    readme = Path("README.md").read_text(encoding="utf-8")
    repository_doc = Path("docs/goblin-directory.md").read_text(encoding="utf-8")

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
    assert "make jupyterhub-repository-proof" in repository_doc
    assert "client.run_directory_function" in repository_doc
    assert "client.directory_service" in repository_doc
    assert "Non-admin callers cannot request another `project_id`" in repository_doc


def test_jupyterhub_docs_cover_repository_proof_users() -> None:
    """Verify Hub docs describe the repository proof target and users."""
    hub_doc = Path("docs/jupyterhub-service-access.md").read_text(encoding="utf-8")

    assert "make jupyterhub-repository-proof" in hub_doc
    assert "make jupyterhub-directory-ui-proof" in hub_doc
    assert "`bob` submits" in hub_doc
    assert "`alice` approves" in hub_doc
    assert "`carol` searches" in hub_doc
    assert "`mallory` is denied" in hub_doc
