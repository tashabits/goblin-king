"""Cleanup treats namespace aliases as one resource without weakening ownership checks."""

import importlib
import json
from pathlib import Path

import pytest


def proof(monkeypatch, tmp_path, entries):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "scripts"))
    module = importlib.import_module("pipeline_acceptance")
    result = module.Acceptance(tmp_path)
    result.state = {"context": "test", "run_id": "run", "namespaces": entries, "baseline": {}}
    monkeypatch.setattr(result, "baseline", lambda: {})
    monkeypatch.setattr(result, "verify_baseline_health", lambda: None)
    return result


def test_cleanup_deletes_aliases_once_and_can_resume(monkeypatch, tmp_path):
    entry = {"name": "fixture-demo", "uid": "original"}
    result = proof(monkeypatch, tmp_path, {"short": dict(entry), "fixture-demo": dict(entry)})
    deleted, commands = [], []

    def kube(*args):
        if args[0] == "delete":
            assert args[2] not in deleted
            deleted.append(args[2])
            return ""
        assert args[:3] == ("get", "namespace", "fixture-demo")
        assert not deleted
        return json.dumps({"metadata": {**entry, "labels": {"goblin-king.io/acceptance": "run"}}})

    monkeypatch.setattr(result, "kube", kube)
    monkeypatch.setattr(result, "command", lambda argv: commands.append(argv))
    result.cleanup()
    result.cleanup()
    assert deleted == ["fixture-demo"]
    assert len(commands) == 1
    assert all(row["removed"] for row in result.state["namespaces"].values())
    assert (tmp_path / "cleanup.json").is_file()


def test_cleanup_propagates_existing_alias_deletion_receipt(monkeypatch, tmp_path):
    entry = {"name": "fixture", "uid": "original"}
    result = proof(monkeypatch, tmp_path, {"short": entry | {"removed": True}, "fixture": entry})
    monkeypatch.setattr(result, "kube", lambda *args: pytest.fail("deleted identity revisited"))
    result.cleanup()
    assert all(row["removed"] for row in result.state["namespaces"].values())


@pytest.mark.parametrize("removed", [False, True])
def test_cleanup_rejects_conflicting_alias_identity_before_commands(monkeypatch, tmp_path, removed):
    result = proof(monkeypatch, tmp_path, {
        "short": {"name": "fixture", "uid": "original", "removed": removed},
        "fixture": {"name": "fixture", "uid": "replacement"},
    })
    monkeypatch.setattr(result, "kube", lambda *args: pytest.fail("ownership conflict ignored"))
    with pytest.raises(RuntimeError, match="conflicting namespace identities"):
        result.cleanup()


def test_cleanup_checks_all_live_owners_before_uninstall(monkeypatch, tmp_path):
    result = proof(monkeypatch, tmp_path, {
        "first": {"name": "first-demo", "uid": "one"},
        "second": {"name": "second-demo", "uid": "two"},
    })

    def kube(*args):
        assert args[0] == "get", "cleanup mutated before validating every owner"
        name = args[2]
        return json.dumps({"metadata": {
            "name": name, "uid": "one" if name == "first-demo" else "two",
            "labels": {"goblin-king.io/acceptance": "run" if name == "first-demo" else "foreign"},
        }})

    monkeypatch.setattr(result, "kube", kube)
    monkeypatch.setattr(result, "command", lambda argv: pytest.fail("uninstall before validation"))
    with pytest.raises(RuntimeError, match="namespace ownership mismatch"):
        result.cleanup()
