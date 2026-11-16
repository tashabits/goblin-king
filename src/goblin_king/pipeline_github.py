"""GitHub App authentication and authoritative repository/merge verification."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import Request, urlopen

import jwt

from goblin_king.pipeline_contracts import PipelineServiceContract, _revision


class ProviderUnavailable(RuntimeError):
    """A bounded provider request failed without leaking credentials."""


class UnapprovedSource(ValueError):
    """The provider does not establish an eligible reviewed merge."""


@dataclass(frozen=True)
class AppCredentials:
    app_id: int
    installation_id: int
    private_key_path: Path
    webhook_secret_path: Path
    api_url: str = "https://api.github.com"
    simulated: bool = False

    def __post_init__(self):
        url = urlsplit(self.api_url)
        if url.username or url.password or url.query or url.fragment:
            raise ValueError("provider URL must not contain credentials, query, or fragment")
        if not self.simulated and self.api_url != "https://api.github.com":
            raise ValueError("custom provider endpoints require explicit simulated mode")
        if self.simulated and url.scheme not in {"http", "https"}:
            raise ValueError("simulator requires an HTTP endpoint")


class GitHubProvider:
    """Use repository-scoped installation credentials; webhook data is never authority."""

    def __init__(self, credentials: AppCredentials):
        self.credentials = credentials
        self._tokens = {}

    def _request(self, path, *, token, body=None, method="GET"):
        request = Request(
            self.credentials.api_url.rstrip("/") + path,
            data=json.dumps(body).encode() if body is not None else None,
            method=method,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "Content-Type": "application/json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        for attempt in range(3):
            try:
                with urlopen(request, timeout=20) as response:
                    return json.load(response)
            except HTTPError as error:
                if error.code in {401, 403, 404, 422}:
                    raise UnapprovedSource(f"provider rejected request ({error.code})") from None
                if error.code not in {429, 500, 502, 503, 504}:
                    raise ProviderUnavailable(f"provider HTTP {error.code}") from None
            except (URLError, TimeoutError, json.JSONDecodeError):
                pass
            if attempt < 2:
                time.sleep(0.25 * 2**attempt)
        raise ProviderUnavailable("provider request failed after three attempts")

    def token(self, repository_id, *, write=False):
        key = (repository_id, write)
        cached = self._tokens.get(key)
        if cached and cached[0] > time.time():
            return cached[1]
        now = int(time.time())
        app_jwt = jwt.encode(
            {"iat": now - 60, "exp": now + 540, "iss": str(self.credentials.app_id)},
            self.credentials.private_key_path.read_bytes(),
            algorithm="RS256",
        )
        permissions = (
            {"contents": "write", "metadata": "read"}
            if write
            else {
                "contents": "read",
                "pull_requests": "read",
                "administration": "read",
                "metadata": "read",
            }
        )
        result = self._request(
            f"/app/installations/{self.credentials.installation_id}/access_tokens",
            token=app_jwt,
            method="POST",
            body={"repository_ids": [repository_id], "permissions": permissions},
        )
        # Refresh early; never put a bearer token in durable state or a public response.
        self._tokens[key] = (time.time() + 3000, result["token"])
        return result["token"]

    def get(self, repository, repository_id, suffix):
        return self._request(f"/repos/{repository}{suffix}", token=self.token(repository_id))

    def head(self, repository, repository_id, branch):
        result = self.get(repository, repository_id, f"/branches/{quote(branch, safe='')}")
        return _revision(result["commit"]["sha"])

    def protection(self, repository, repository_id, branch):
        policy = self.get(
            repository, repository_id, f"/branches/{quote(branch, safe='')}/protection"
        )
        reviews = policy.get("required_pull_request_reviews") or {}
        if (
            not policy.get("enforce_admins", {}).get("enabled")
            or reviews.get("required_approving_review_count", 0) < 1
            or not reviews.get("dismiss_stale_reviews")
            or not reviews.get("require_last_push_approval")
            or policy.get("allow_force_pushes", {}).get("enabled", False)
        ):
            raise UnapprovedSource(
                "branch must enforce fresh approving reviews, including administrators"
            )
        return policy

    def content(self, repository, repository_id, path, commit):
        _revision(commit)
        document = self.get(
            repository,
            repository_id,
            f"/contents/{quote(path, safe='/')}?{urlencode({'ref': commit})}",
        )
        if document.get("type") != "file" or document.get("encoding") != "base64":
            raise UnapprovedSource("source contract and Dockerfile must be regular files")
        return base64.b64decode(document["content"], validate=False)

    def discover(self, registration, repository_id):
        repository = registration.source_repository
        info = self.get(repository, repository_id, "")
        if info["id"] != repository_id or info["full_name"].lower() != repository:
            raise UnapprovedSource("numeric repository identity does not match registration")
        routes = []
        for environment in registration.environments:
            policy = self.protection(repository, repository_id, environment.branch)
            commit = self.head(repository, repository_id, environment.branch)
            contract = PipelineServiceContract.model_validate_json(
                self.content(repository, repository_id, "pipeline-service.json", commit)
            )
            if contract.context != "." or contract.dockerfile != "Dockerfile":
                raise UnapprovedSource(
                    "this delivery profile builds Dockerfile from the repository root"
                )
            dockerfile = self.content(
                repository, repository_id, contract.dockerfile, commit
            ).decode()
            targets = re.findall(
                r"^\s*FROM\s+.+?\s+AS\s+([\w.-]+)\s*$",
                dockerfile,
                flags=re.IGNORECASE | re.MULTILINE,
            )
            for target in (contract.test_target, contract.runtime_target):
                if target and target not in targets:
                    raise UnapprovedSource(f"Dockerfile is missing target {target!r}")
            routes.append(
                {
                    "environment": environment.name,
                    "branch": environment.branch,
                    "head": commit,
                    "contract": contract.model_dump(mode="json"),
                    "dockerfile_targets": targets,
                    "review_policy": policy,
                    "namespace": environment.namespace,
                    "release": environment.release,
                    "image_repository": environment.image_repository,
                }
            )
        return {
            "repository_id": repository_id,
            "repository": repository,
            "routes": routes,
            "simulated": self.credentials.simulated,
        }

    def verify_merge(self, registration, repository_id, branch, commit, previous):
        repository = registration.source_repository
        if branch not in {e.branch for e in registration.environments}:
            raise UnapprovedSource("unmanaged branch")
        if self.head(repository, repository_id, branch) != commit:
            raise UnapprovedSource("event is not the authoritative current branch head")
        policy = self.protection(repository, repository_id, branch)
        if previous == commit:
            return None
        comparison = self.get(repository, repository_id, f"/compare/{previous}...{commit}")
        if comparison["status"] != "ahead":
            raise UnapprovedSource("source does not descend from the last observed head")
        pulls = self.get(repository, repository_id, f"/commits/{commit}/pulls?per_page=100")
        for pull in pulls:
            if (
                not pull.get("merged_at")
                or pull.get("merge_commit_sha") != commit
                or pull["base"]["ref"] != branch
                or pull["base"]["repo"]["id"] != repository_id
            ):
                continue
            latest = {}
            page = 1
            while True:
                batch = self.get(
                    repository,
                    repository_id,
                    f"/pulls/{pull['number']}/reviews?per_page=100&page={page}",
                )
                for review in batch:
                    if review["state"] in {"APPROVED", "CHANGES_REQUESTED", "DISMISSED"}:
                        latest[review["user"]["id"]] = review
                if len(batch) < 100:
                    break
                page += 1
                if page > 20:
                    raise UnapprovedSource("review history exceeds verification bound")
            approved = [
                review
                for review in latest.values()
                if review["state"] == "APPROVED"
                and review["commit_id"] == pull["head"]["sha"]
                and review["user"]["id"] != pull["user"]["id"]
            ]
            required = policy["required_pull_request_reviews"]["required_approving_review_count"]
            if len(approved) >= required and not any(
                review["state"] == "CHANGES_REQUESTED" for review in latest.values()
            ):
                return {
                    "repository_id": repository_id,
                    "installation_id": self.credentials.installation_id,
                    "commit": commit,
                    "previous": previous,
                    "pull_request": pull["number"],
                    "reviews": [review["id"] for review in approved],
                    "simulated": self.credentials.simulated,
                }
        raise UnapprovedSource("no eligible reviewed merge establishes this branch head")

    def verify_webhook(self, raw, signature):
        if len(raw) > 1024 * 1024:
            raise UnapprovedSource("webhook body exceeds limit")
        expected = (
            "sha256="
            + hmac.new(
                self.credentials.webhook_secret_path.read_bytes(), raw, hashlib.sha256
            ).hexdigest()
        )
        if not hmac.compare_digest(expected, signature):
            raise UnapprovedSource("invalid webhook signature")
        try:
            event = json.loads(raw)
        except (ValueError, UnicodeDecodeError):
            raise UnapprovedSource("invalid webhook JSON") from None
        if event.get("installation", {}).get("id") != self.credentials.installation_id:
            raise UnapprovedSource("unexpected installation")
        return event
