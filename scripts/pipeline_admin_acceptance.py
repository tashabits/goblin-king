"""Verify retained browser evidence against live, authenticated admin results."""

import hashlib
import json
import os
from urllib.parse import quote, urlsplit
from urllib.request import Request, urlopen

from admin_runtime_audit import EXPECTED_FAILURE_KINDS
from pipeline_build_images import source_identity


def verify(proof, runtime):
    directory = proof.output / (runtime + "-admin-audit")
    path = directory / "browser-proof.json"
    if not path.is_file():
        raise RuntimeError("browser audit evidence is required: " + str(path))
    record = json.loads(path.read_text())
    if (
        record.get("runtime") != runtime
        or record.get("revision") != proof.state["revision"]
        or record.get("source_sha256") != source_identity()
        or record.get("acceptance_run_id") != proof.state["run_id"]
    ):
        raise RuntimeError(
            "browser audit does not identify this acceptance run and source revision"
        )
    base = record["api_base_url"].rstrip("/")
    parsed = urlsplit(base)
    if parsed.scheme not in {"http", "https"} or parsed.hostname not in {"127.0.0.1", "localhost"}:
        raise RuntimeError("local acceptance requires a loopback admin endpoint")
    token = os.environ.get("GOBLIN_KING_AUDIT_TOKEN")
    if not token:
        raise RuntimeError(
            "set GOBLIN_KING_AUDIT_TOKEN for authenticated browser-evidence verification"
        )

    def get(route, *, raw=False):
        with urlopen(
            Request(base + route, headers={"Authorization": "Bearer " + token}), timeout=30
        ) as response:
            content = response.read(16 * 1024**2 + 1)
            if len(content) > 16 * 1024**2:
                raise RuntimeError("audit response exceeds evidence size bound")
            return content if raw else json.loads(content)

    def retained(entry):
        artifact = (directory / entry["path"]).resolve()
        if not artifact.is_relative_to(directory.resolve()) or not artifact.is_file():
            raise RuntimeError("browser evidence path is missing or outside its audit directory")
        if hashlib.sha256(artifact.read_bytes()).hexdigest() != entry["sha256"]:
            raise RuntimeError("browser evidence checksum changed")
        return artifact

    for purpose in ("kinds", "runs", "events", "heartbeats", "services", "artifacts"):
        screenshot = retained(record["screenshots"][purpose])
        if screenshot.suffix.lower() not in {".jpg", ".jpeg", ".png"}:
            raise RuntimeError("browser screenshots require retained image files")
    registered = {item["kind"] for item in get("/admin-api/goblins")}
    if set(record["jobs"]) != registered - {"example.long-hello"}:
        raise RuntimeError("browser submissions do not cover every registered one-shot kind")
    rows, downloads = [], []
    for kind, submission in record["jobs"].items():
        ui_record = retained(submission["browser_submission"]).read_text(encoding="utf-8")
        job = get("/admin-api/jobs/" + quote(submission["job_id"], safe=""))
        run = get("/admin-api/runs/" + quote(submission["run_id"], safe=""))
        if job["kind"] != kind or run["job_id"] != job["id"] or job["id"] not in ui_record:
            raise RuntimeError("browser submission and authoritative execution identities differ")
        expected = "failed" if kind in EXPECTED_FAILURE_KINDS else "completed"
        if job["status"] != expected or run["status"] != expected:
            raise RuntimeError("unexpected browser execution result for " + kind)
        result = run["result"]
        if result["status"] != ("failed" if expected == "failed" else "success"):
            raise RuntimeError("unexpected result envelope for " + kind)
        if expected == "failed" and "controlled failure" not in (result.get("error") or "").lower():
            raise RuntimeError("expected failure lacks its documented diagnostic")
        for artifact in result.get("artifacts", []):
            downloaded = retained(submission["downloads"][artifact["name"]])
            actual = get(
                "/admin-api/runs/"
                + quote(run["id"], safe="")
                + "/artifacts/"
                + quote(artifact["name"], safe=""),
                raw=True,
            )
            if downloaded.read_bytes() != actual:
                raise RuntimeError("browser artifact differs from the retained result")
            downloads.append(
                {
                    "run_id": run["id"],
                    "name": artifact["name"],
                    "sha256": hashlib.sha256(actual).hexdigest(),
                }
            )
        rows.append({"kind": kind, "job": job, "run": run})
    probes = [json.loads(retained(entry).read_text()) for entry in record["service_probes"]]
    if (
        len(probes) != 2
        or any(item["status"] != 200 for item in probes)
        or probes[0]["timestamp"] >= probes[1]["timestamp"]
    ):
        raise RuntimeError("two successful service probes with advancing timestamps are required")
    observations = {
        "revision": proof.state["revision"],
        "source_sha256": record["source_sha256"],
        "runtime": runtime,
        "rows": rows,
        "downloads": downloads,
        "service_probes": probes,
    }
    (directory / "verified.json").write_text(json.dumps(observations, indent=2))
    return {"evidence": str((directory / "verified.json").relative_to(proof.output))}
