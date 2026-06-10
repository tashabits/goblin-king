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
