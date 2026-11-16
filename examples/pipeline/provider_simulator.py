"""Local-only GitHub App simulator backed by real Git repositories.

Review records here are synthetic test data, never evidence of a GitHub review.
The fixture control API is separately authenticated and is not a public webhook.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import os
import secrets
import shutil
import subprocess
import threading
import time
from pathlib import Path
from urllib.request import Request, urlopen
from uuid import uuid4

import jwt
import uvicorn
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI, HTTPException
from fastapi import Request as HttpRequest
from fastapi.responses import Response

FIXTURES = Path(__file__).parent / "fixtures"


class Simulator:
    """Persist synthetic provider facts independently of the delivery database."""

    def __init__(self, root: Path):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.state_path = self.root / "provider.json"
        self.key_path = self.root / "private-key.pem"
        if not self.key_path.exists():
            key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
            self.key_path.write_bytes(
                key.private_bytes(
                    serialization.Encoding.PEM,
                    serialization.PrivateFormat.PKCS8,
                    serialization.NoEncryption(),
                )
            )
            for name in ("webhook-secret", "fixture-token", "token-signing-secret"):
                (self.root / name).write_text(secrets.token_urlsafe(32))
            (self.root / "app-id").write_text("1")
            for name in (
                "private-key.pem",
                "webhook-secret",
                "fixture-token",
                "token-signing-secret",
            ):
                os.chmod(self.root / name, 0o600)
        self.private_key = serialization.load_pem_private_key(
            self.key_path.read_bytes(), password=None
        )
        self.state = (
            json.loads(self.state_path.read_text())
            if self.state_path.exists()
            else {
                "repositories": {},
                "webhook_url": None,
                "deliveries": [],
                "next_pull": 1,
                "simulated": True,
            }
        )
        if not self.state["repositories"]:
            for index, implementation in enumerate(("python-api", "node-api", "release-state"), 1):
                name = f"fixture/{implementation}"
                directory = self.root / implementation
                directory.mkdir()
                if implementation != "release-state":
                    shutil.copytree(
                        FIXTURES / implementation,
                        directory,
                        dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
                    )
                (directory / "README.md").write_text(
                    "Local simulated provider fixture. Apache-2.0.\n"
                )
                self.git(directory, "init", "--initial-branch=main")
                self.git(directory, "config", "user.name", "Local pipeline fixture")
                self.git(directory, "config", "user.email", "fixture@example.invalid")
                self.git(directory, "add", ".")
                self.git(directory, "commit", "-m", "Initialize local fixture")
                self.git(directory, "branch", "test")
                self.state["repositories"][name] = {
                    "id": index,
                    "directory": implementation,
                    "full_name": name,
                    "default_branch": "main",
                    "pulls": [],
                    "protected": True,
                }
            self.save()

    @staticmethod
    def git(directory, *args, stdin=None, binary=False):
        result = subprocess.run(
            ["git", "-C", str(directory), *args],
            input=stdin,
            capture_output=True,
            text=not binary,
            timeout=30,
        )
        if result.returncode:
            raise HTTPException(422, "fixture Git operation rejected")
        return result.stdout if binary else result.stdout.strip()

    def save(self):
        temporary = self.state_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.state, indent=2))
        temporary.replace(self.state_path)

    def secret(self, name):
        return (self.root / name).read_text()

    def authorize(self, request, repository=None, permission="contents", write=False):
        bearer = request.headers.get("authorization", "").removeprefix("Bearer ")
        try:
            claims = jwt.decode(bearer, self.secret("token-signing-secret"), algorithms=["HS256"])
            if repository is not None and repository["id"] not in claims["repository_ids"]:
                raise ValueError()
            actual = claims["permissions"].get(permission)
            if actual != "write" and (write or actual != "read"):
                raise ValueError()
        except (ValueError, KeyError, jwt.PyJWTError):
            raise HTTPException(
                403, "installation token is invalid or outside repository scope"
            ) from None

    def fixture_authorize(self, request):
        supplied = request.headers.get("authorization", "").removeprefix("Bearer ")
        if not secrets.compare_digest(supplied, self.secret("fixture-token")):
            raise HTTPException(403, "fixture control token required")

    def repository(self, owner, name):
        record = self.state["repositories"].get(f"{owner}/{name}")
        if record is None:
            raise HTTPException(404, "repository not found")
        return record, self.root / record["directory"]

    def head(self, directory, branch):
        if branch not in {"main", "test"}:
            raise HTTPException(404, "unmanaged branch")
        return self.git(directory, "rev-parse", f"refs/heads/{branch}")

    def merge(self, body):
        key = body.get("idempotency_key")
        if key and key in self.state.get("merge_requests", {}):
            prior = self.state["merge_requests"][key]
            if prior["body"] != body:
                raise HTTPException(409, "fixture request key conflicts")
            return prior["delivery"]
        repository, directory = self.repository(*body["repository"].split("/"))
        branch = body.get("branch", "test")
        parent = self.head(directory, branch)
        self.git(directory, "checkout", branch)
        number = self.state["next_pull"]
        self.state["next_pull"] += 1
        (directory / "VERSION").write_text(str(number))
        fault = body.get("fault")
        if fault == "test":
            filename = "test_app.py" if repository["directory"] == "python-api" else "test.mjs"
            (directory / filename).write_text(
                "raise RuntimeError('fixture failure')\n"
                if filename.endswith(".py")
                else "throw new Error('fixture failure');\n"
            )
        elif fault == "build":
            with (directory / "Dockerfile").open("a") as stream:
                stream.write("\nRUN exit 31\n")
        elif fault == "health":
            filename = "app.py" if repository["directory"] == "python-api" else "app.mjs"
            path = directory / filename
            original = path.read_text()
            path.write_text(
                original.replace('os.environ.get("FAIL_HEALTH") != "1"', "False").replace(
                    "process.env.FAIL_HEALTH !== '1'", "false"
                )
            )
        elif fault == "repair":
            shutil.copytree(
                FIXTURES / repository["directory"],
                directory,
                dirs_exist_ok=True,
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
            )
        elif fault == "contract":
            (directory / "pipeline-service.json").write_text('{"port":0}')
        elif fault:
            raise HTTPException(422, "unknown fixture fault")
        self.git(directory, "add", ".")
        self.git(directory, "commit", "-m", f"Synthetic reviewed change {number}")
        commit = self.head(directory, branch)
        pull = {
            "number": number,
            "merged_at": "2026-11-16T00:00:00Z",
            "merge_commit_sha": commit,
            "base": {"ref": branch, "repo": {"id": repository["id"]}},
            "head": {"sha": commit},
            "user": {"id": 7},
            "synthetic": True,
            "reviews": []
            if body.get("unreviewed")
            else [
                {
                    "id": number,
                    "user": {"id": 9},
                    "state": "APPROVED",
                    "commit_id": commit,
                    "synthetic": True,
                }
            ],
        }
        repository["pulls"].append(pull)
        event = {
            "installation": {"id": 1},
            "repository": {"id": repository["id"], "full_name": repository["full_name"]},
            "ref": f"refs/heads/{branch}",
            "before": parent,
            "after": commit,
            "sender": {"login": "local-fixture"},
        }
        delivery = {"id": str(uuid4()), "event": event, "synthetic": True}
        self.state["deliveries"].append(delivery)
        if key:
            self.state.setdefault("merge_requests", {})[key] = {"body": body, "delivery": delivery}
        self.save()
        if body.get("webhook", True) and self.state["webhook_url"]:
            threading.Thread(target=self.deliver, args=(delivery,), daemon=True).start()
        return delivery

    def deliver(self, delivery):
        raw = json.dumps(delivery["event"], separators=(",", ":")).encode()
        signature = (
            "sha256="
            + hmac.new(self.secret("webhook-secret").encode(), raw, hashlib.sha256).hexdigest()
        )
        request = Request(
            self.state["webhook_url"],
            data=raw,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "X-Hub-Signature-256": signature,
                "X-GitHub-Event": "push",
                "X-GitHub-Delivery": delivery["id"],
            },
        )
        try:
            with urlopen(request, timeout=20) as response:
                status = response.status
        except Exception:
            status = "delivery-unavailable"
        with self.lock:
            delivery["response_status"] = status
            self.save()


def create_app(root):
    simulator = Simulator(root)
    app = FastAPI(title="Local simulated GitHub provider")
    app.state.simulator = simulator

    @app.get("/health")
    def health():
        return {"healthy": True, "simulated": True}

    @app.post("/app/installations/{installation_id}/access_tokens")
    async def token(installation_id: int, request: HttpRequest):
        if installation_id != 1:
            raise HTTPException(404, "installation not found")
        bearer = request.headers.get("authorization", "").removeprefix("Bearer ")
        try:
            claims = jwt.decode(
                bearer,
                simulator.private_key.public_key(),
                algorithms=["RS256"],
                issuer="1",
                options={"require": ["exp", "iat", "iss"]},
            )
            if claims["exp"] - claims["iat"] > 600:
                raise ValueError()
        except (ValueError, jwt.PyJWTError):
            raise HTTPException(401, "invalid App JWT") from None
        body = await request.json()
        ids = body.get("repository_ids", [])
        permissions = body.get("permissions", {})
        if (
            not ids
            or not set(ids) <= {1, 2, 3}
            or not set(permissions) <= {"contents", "metadata", "pull_requests", "administration"}
        ):
            raise HTTPException(422, "invalid installation scope")
        encoded = jwt.encode(
            {"exp": int(time.time()) + 3600, "repository_ids": ids, "permissions": permissions},
            simulator.secret("token-signing-secret"),
            algorithm="HS256",
        )
        return {"token": encoded, "permissions": permissions, "simulated": True}

    @app.post("/_fixture/{action}")
    async def control(action: str, request: HttpRequest):
        simulator.fixture_authorize(request)
        body = await request.json()
        with simulator.lock:
            if action == "merge":
                return simulator.merge(body)
            if action == "webhook":
                # The control token is confined to the acceptance harness.
                simulator.state["webhook_url"] = body["url"]
                simulator.save()
                return {"configured": True}
            if action == "redeliver":
                delivery = next(d for d in simulator.state["deliveries"] if d["id"] == body["id"])
                threading.Thread(target=simulator.deliver, args=(delivery,), daemon=True).start()
                return {"delivery_id": delivery["id"]}
            if action == "recording":
                simulator.state["recording_available"] = bool(body["available"])
                simulator.save()
                return {"available": simulator.state["recording_available"]}
            raise HTTPException(404, "unknown fixture action")

    @app.get("/repos/{owner}/{name}")
    def repository(owner: str, name: str, request: HttpRequest):
        record, _ = simulator.repository(owner, name)
        simulator.authorize(request, record, "metadata")
        return {key: record[key] for key in ("id", "full_name", "default_branch")}

    @app.get("/repos/{owner}/{name}/branches/{branch}/protection")
    def protection(owner: str, name: str, branch: str, request: HttpRequest):
        record, directory = simulator.repository(owner, name)
        simulator.authorize(request, record, "administration")
        simulator.head(directory, branch)
        return {
            "enforce_admins": {"enabled": record["protected"]},
            "allow_force_pushes": {"enabled": False},
            "required_pull_request_reviews": {
                "required_approving_review_count": 1,
                "dismiss_stale_reviews": True,
                "require_last_push_approval": True,
                "require_code_owner_reviews": True,
            },
        }

    @app.get("/repos/{owner}/{name}/branches/{branch}")
    def branch_head(owner: str, name: str, branch: str, request: HttpRequest):
        record, directory = simulator.repository(owner, name)
        simulator.authorize(request, record)
        return {"name": branch, "commit": {"sha": simulator.head(directory, branch)}}

    @app.get("/repos/{owner}/{name}/contents/{path:path}")
    def content(owner: str, name: str, path: str, ref: str, request: HttpRequest):
        record, directory = simulator.repository(owner, name)
        simulator.authorize(request, record)
        if len(ref) != 40 or any(c not in "0123456789abcdef" for c in ref):
            raise HTTPException(422, "full commit required")
        data = simulator.git(directory, "show", f"{ref}:{path}", binary=True)
        return {"type": "file", "encoding": "base64", "content": base64.b64encode(data).decode()}

    @app.get("/repos/{owner}/{name}/tarball/{commit}")
    def archive(owner: str, name: str, commit: str, request: HttpRequest):
        record, directory = simulator.repository(owner, name)
        simulator.authorize(request, record)
        if len(commit) != 40 or any(c not in "0123456789abcdef" for c in commit):
            raise HTTPException(422, "full commit required")
        return Response(
            simulator.git(
                directory, "archive", "--format=tar.gz", "--prefix=source/", commit, binary=True
            ),
            media_type="application/gzip",
        )

    @app.get("/repos/{owner}/{name}/compare/{revisions}")
    def compare(owner: str, name: str, revisions: str, request: HttpRequest):
        record, directory = simulator.repository(owner, name)
        simulator.authorize(request, record)
        old, new = revisions.split("...")
        if any(len(c) != 40 or any(x not in "0123456789abcdef" for x in c) for c in (old, new)):
            raise HTTPException(422, "full commits required")
        ancestor = simulator.git(directory, "merge-base", old, new)
        return {"status": "identical" if old == new else "ahead" if ancestor == old else "diverged"}

    @app.get("/repos/{owner}/{name}/commits/{commit}/pulls")
    def pulls(owner: str, name: str, commit: str, request: HttpRequest):
        record, _ = simulator.repository(owner, name)
        simulator.authorize(request, record, "pull_requests")
        return [p for p in record["pulls"] if p["merge_commit_sha"] == commit]

    @app.get("/repos/{owner}/{name}/pulls/{number}/reviews")
    def reviews(owner: str, name: str, number: int, request: HttpRequest, page: int = 1):
        record, _ = simulator.repository(owner, name)
        simulator.authorize(request, record, "pull_requests")
        return (
            next(p["reviews"] for p in record["pulls"] if p["number"] == number)
            if page == 1
            else []
        )

    @app.api_route("/repos/{owner}/{name}/git/{resource:path}", methods=["GET", "POST", "PATCH"])
    async def git_data(owner: str, name: str, resource: str, request: HttpRequest):
        import os
        import re
        import tempfile
        from pathlib import PurePosixPath

        record, directory = simulator.repository(owner, name)
        simulator.authorize(request, record, write=request.method != "GET")
        if request.method != "GET" and not simulator.state.get("recording_available", True):
            raise HTTPException(503, "injected recording outage")
        if record["id"] != 3:
            raise HTTPException(403, "Git recording is limited to the local release-state fixture")

        def oid(value):
            if not isinstance(value, str) or not re.fullmatch("[0-9a-f]{40}", value):
                raise HTTPException(422, "full Git object identity required")
            return value

        body = await request.json() if request.method != "GET" else {}
        with simulator.lock:
            if resource == "refs/heads/main":
                old = simulator.head(directory, "main")
                if request.method == "PATCH":
                    new = oid(body.get("sha"))
                    if (
                        body.get("force") is not False
                        or simulator.git(directory, "merge-base", old, new) != old
                    ):
                        raise HTTPException(422, "non-fast-forward update rejected")
                    simulator.git(directory, "update-ref", "refs/heads/main", new, old)
                    return {"object": {"sha": new}}
                if request.method == "GET":
                    return {"object": {"sha": old}}
            if resource.startswith("commits/") and request.method == "GET":
                sha = oid(resource.split("/")[1])
                return {
                    "sha": sha,
                    "tree": {"sha": simulator.git(directory, "rev-parse", sha + "^{tree}")},
                }
            if resource.startswith("trees/") and request.method == "GET":
                sha = oid(resource.split("/")[1])
                rows = simulator.git(directory, "ls-tree", "-r", sha).splitlines()
                return {
                    "sha": sha,
                    "truncated": False,
                    "tree": [
                        {"path": row.split("\t", 1)[1], "sha": row.split()[2], "type": "blob"}
                        for row in rows
                    ],
                }
            if resource == "trees" and request.method == "POST":
                with tempfile.TemporaryDirectory() as temporary:
                    environment = os.environ | {"GIT_INDEX_FILE": str(Path(temporary) / "index")}

                    def indexed(*args):
                        output = subprocess.run(
                            ["git", "-C", str(directory), *args],
                            env=environment,
                            capture_output=True,
                            text=True,
                            timeout=30,
                        )
                        if output.returncode:
                            raise HTTPException(422, "Git tree operation failed")
                        return output.stdout.strip()

                    indexed("read-tree", oid(body["base_tree"]))
                    for entry in body["tree"]:
                        path = PurePosixPath(entry["path"])
                        if (
                            path.is_absolute()
                            or ".." in path.parts
                            or ".git" in path.parts
                            or "\\" in entry["path"]
                            or entry.get("mode") != "100644"
                            or entry.get("type") != "blob"
                        ):
                            raise HTTPException(422, "unsafe record path or mode")
                        blob = (
                            simulator.git(
                                directory,
                                "hash-object",
                                "-w",
                                "--stdin",
                                stdin=entry["content"].encode("utf-8"),
                                binary=True,
                            )
                            .decode()
                            .strip()
                        )
                        indexed("update-index", "--add", "--cacheinfo", "100644", blob, str(path))
                    return {"sha": indexed("write-tree")}
            if resource == "commits" and request.method == "POST":
                if len(body["parents"]) != 1:
                    raise HTTPException(422, "exactly one parent required")
                return {
                    "sha": simulator.git(
                        directory,
                        "commit-tree",
                        oid(body["tree"]),
                        "-p",
                        oid(body["parents"][0]),
                        stdin=body["message"],
                    )
                }
        raise HTTPException(404, "unsupported Git operation")

    return app


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", required=True, type=Path)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8770)
    args = parser.parse_args()
    uvicorn.run(create_app(args.state), host=args.host, port=args.port, access_log=False)
