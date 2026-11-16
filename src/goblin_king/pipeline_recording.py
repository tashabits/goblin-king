"""Non-force Git recording with immutable operation records and bounded conflict retries."""

import json
import time
from urllib.parse import quote

from goblin_king.pipeline_github import ProviderUnavailable, UnapprovedSource
from goblin_king.pipeline_state import PipelineConflict


class ReleaseRecorder:
    def __init__(self, provider, repository, repository_id, branch="main"):
        self.provider, self.repository, self.repository_id = provider, repository, repository_id
        self.branch = branch

    def request(self, suffix, *, body=None, method="GET"):
        return self.provider._request(
            "/repos/" + self.repository + suffix,
            token=self.provider.token(self.repository_id, write=True),
            body=body,
            method=method,
        )

    def record(self, release_key, operation_id, record):
        raw = json.dumps(record, sort_keys=True, indent=2) + "\n"
        immutable = f"operations/{operation_id}.json"
        current = f"releases/{release_key}.json"
        reference = "/git/refs/heads/" + quote(self.branch, safe="")
        for attempt in range(4):
            head = self.request(reference)["object"]["sha"]
            commit = self.request("/git/commits/" + head)
            tree = self.request("/git/trees/" + commit["tree"]["sha"] + "?recursive=1")
            if tree.get("truncated"):
                raise PipelineConflict("release-state tree exceeds recording bounds")
            paths = {entry["path"] for entry in tree["tree"]}
            if immutable in paths:
                existing = self.provider.content(
                    self.repository, self.repository_id, immutable, head
                )
                if existing.decode() != raw:
                    raise PipelineConflict(
                        "immutable operation record already has different content"
                    )
                return {"commit": head, "operation_path": immutable, "recovered": True}
            changelog = (
                self.provider.content(
                    self.repository, self.repository_id, "CHANGELOG.md", head
                ).decode()
                if "CHANGELOG.md" in paths
                else "# Observed releases\n"
            )
            changelog += f"\n- `{operation_id}`: `{release_key}` — `{record['image']}`\n"
            entries = [
                {"path": path, "mode": "100644", "type": "blob", "content": content}
                for path, content in [(immutable, raw), (current, raw), ("CHANGELOG.md", changelog)]
            ]
            tree_sha = self.request(
                "/git/trees",
                method="POST",
                body={"base_tree": commit["tree"]["sha"], "tree": entries},
            )["sha"]
            new_commit = self.request(
                "/git/commits",
                method="POST",
                body={
                    "message": f"Record observed release {operation_id}",
                    "tree": tree_sha,
                    "parents": [head],
                },
            )["sha"]
            try:
                self.request(reference, method="PATCH", body={"sha": new_commit, "force": False})
                return {"commit": new_commit, "operation_path": immutable, "recovered": False}
            except (UnapprovedSource, ProviderUnavailable):
                if attempt == 3:
                    raise
                time.sleep(0.1 * 2**attempt)
        raise ProviderUnavailable("release recording exhausted conflict retries")
