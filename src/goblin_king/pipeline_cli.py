"""Delivery operator commands use authenticated HTTP, never direct database edits."""

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Annotated
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import typer

app = typer.Typer(help="Manage repository delivery through the authenticated API.")
networking_app = typer.Typer(help="Inspect and configure approved ingress and egress.")
app.add_typer(networking_app, name="networking")


@networking_app.command("capabilities")
def networking_capabilities(project: str):
    """Show installed networking choices for a project."""
    call("/networking/capabilities?" + urlencode({"project_id": project}))


@networking_app.command("inspect")
def networking_inspect(release_key: str):
    """Inspect desired and observed network configuration."""
    call(f"/releases/{release_key}/networking")


@networking_app.command("diagnostics")
def networking_diagnostics(project: str):
    """Show retained health evidence and actionable configuration problems."""
    call("/networking/diagnostics?" + urlencode({"project_id": project}))


@networking_app.command("preview")
def networking_preview(release_key: str, configuration: Path):
    """Validate a configuration file without changing any resources."""
    call(
        "/networking/preview",
        {"release_key": release_key, "configuration": json.loads(configuration.read_text())},
    )


@networking_app.command("apply")
def networking_apply(release_key: str, preview: Path, idempotency_key: str):
    """Apply a saved preview; changed state or policy requires a new preview."""
    value = json.loads(preview.read_text())
    if value.get("release_key") != release_key:
        raise typer.BadParameter("preview belongs to another release")
    call(
        f"/releases/{release_key}/networking",
        {
            "configuration": value["configuration"],
            "expected_version": value["expected_version"],
            "preview_id": value["preview_id"],
            "idempotency_key": idempotency_key,
        },
    )


@networking_app.command("probe")
def networking_probe(release_key: str, expected_version: int, idempotency_key: str):
    """Verify route health without changing routes, policies or application workloads."""
    call(
        f"/releases/{release_key}/networking/probe",
        {
            "expected_version": expected_version,
            "idempotency_key": idempotency_key,
        },
    )


@networking_app.command("disable")
def networking_disable(release_key: str, preview: Path, idempotency_key: str):
    """Apply a reviewed preview that disables both networking features."""
    value = json.loads(preview.read_text())
    if any(value["configuration"][kind]["enabled"] for kind in ("ingress", "egress")):
        raise typer.BadParameter("disable requires a preview with ingress and egress disabled")
    networking_apply(release_key, preview, idempotency_key)


def call(path, body=None, method=None):
    token = os.environ.get("GOBLIN_KING_API_TOKEN")
    if not token:
        raise typer.BadParameter("set GOBLIN_KING_API_TOKEN in the environment")
    base = os.environ.get("GOBLIN_KING_API_URL", "http://127.0.0.1:8000").rstrip("/")
    request = Request(
        base + "/pipeline" + path,
        data=json.dumps(body).encode() if body else None,
        method=method or ("POST" if body else "GET"),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    try:
        with urlopen(request, timeout=60) as response:
            result = json.load(response)
    except HTTPError as error:
        try:
            detail = json.load(error).get("detail", f"HTTP {error.code}")
        except ValueError:
            detail = f"HTTP {error.code}"
        typer.echo(str(detail), err=True)
        raise typer.Exit(1) from None
    typer.echo(json.dumps(result, indent=2))
    return result


@app.command()
def status():
    """Inspect provider mode, recovery holds, and reconciliation errors."""
    call("/status")


@app.command()
def diagnostics():
    """Inspect installation mode and unresolved delivery state as an administrator."""
    call("/status")
    call("/recovery")


@app.command()
def acceptance(
    command: str,
    output: Annotated[Path, typer.Option()],
    context: str = "docker-desktop",
    images: Path | None = None,
    image: str | None = None,
    only: str | None = None,
):
    """Prepare, run, resume, report, or clean up real-cluster acceptance."""
    if command not in {"prepare", "run", "resume", "report", "cleanup"}:
        raise typer.BadParameter("choose prepare, run, resume, report, or cleanup")
    script = Path(__file__).resolve().parents[2] / "scripts/pipeline_acceptance.py"
    if not script.is_file():
        raise typer.BadParameter(
            "acceptance requires the source checkout or the installation image"
        )
    args = [sys.executable, str(script), command, "--output", str(output), "--context", context]
    for flag, value in (("--images", images), ("--image", image), ("--only", only)):
        if value is not None:
            args.extend((flag, str(value)))
    raise typer.Exit(subprocess.call(args))


@app.command()
def discover(config: Path):
    """Preview a registration and repository_id JSON document before attachment."""
    call("/repositories/discover", json.loads(config.read_text()))


@app.command()
def attach(config: Path, expected_version: int = 0, idempotency_key: str = typer.Option(...)):
    """Attach configuration without deploying an existing branch head."""
    body = json.loads(config.read_text())
    body.update(expected_version=expected_version, idempotency_key=idempotency_key)
    call("/repositories", body)


@app.command()
def inspect(collection: str, project: str = typer.Option(...)):
    """List repositories, builds, releases, or operations for one project."""
    if collection not in {"repositories", "builds", "releases", "operations"}:
        raise typer.BadParameter("choose repositories, builds, releases, or operations")
    call(f"/{collection}?{urlencode({'project_id': project})}")


@app.command()
def management(key: str, enabled: bool, expected_version: int, idempotency_key: str):
    """Enable or disable a versioned repository registration."""
    call(
        f"/repositories/{key}/management",
        {
            "enabled": enabled,
            "expected_version": expected_version,
            "idempotency_key": idempotency_key,
        },
    )


@app.command()
def rebuild(build_id: str, expected_version: int, idempotency_key: str):
    """Start an explicit new build attempt with a new immutable publication identity."""
    call(
        f"/builds/{build_id}/rebuild",
        {"expected_version": expected_version, "idempotency_key": idempotency_key},
    )


@app.command()
def recover_build(build_id: str, expected_version: int, idempotency_key: str):
    """Settle a held build after all source and publication execution has terminated."""
    call(
        f"/builds/{build_id}/recover",
        {"expected_version": expected_version, "idempotency_key": idempotency_key},
    )


@app.command()
def recover(
    operation_id: str,
    expected_version: int,
    idempotency_key: str,
    networking_preview_id: str | None = None,
):
    """Reconcile an interrupted release after its previous execution has terminated."""
    call(
        f"/operations/{operation_id}/recover",
        {
            "expected_version": expected_version,
            "idempotency_key": idempotency_key,
            "networking_preview_id": networking_preview_id,
        },
    )


@app.command()
def release(
    key: str, action: str, expected_version: int, idempotency_key: str, payload: Path | None = None
):
    """Start activation, deactivation, deployment, rollback, recording, or hold recovery."""
    call(
        f"/releases/{key}/operations",
        {
            "action": action,
            "expected_version": expected_version,
            "idempotency_key": idempotency_key,
            "payload": json.loads(payload.read_text()) if payload else {},
        },
    )
