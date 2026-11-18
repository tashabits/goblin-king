"""Complete an in-flight build after a newer revision becomes desired."""

from __future__ import annotations

import json


def prove_supersession(delivery):
    before = delivery.checkpoint("inflight-before", lambda: delivery.release("python", "test"))
    older = delivery.merge("inflight-older", "python", "test")

    def running_source():
        rows = [
            row
            for row in delivery.collection("builds")
            if row["document"]["request"]["source"]["commit"] == older["event"]["after"]
        ]
        if len(rows) != 1 or rows[0]["status"] != "admitted":
            return None
        build = rows[0]
        jobs = json.loads(
            delivery.proof.kube(
                "-n", delivery.prefix + "-python-build", "get", "jobs", "-o", "json"
            )
        )["items"]
        for job in jobs:
            if job["metadata"]["name"] == build["id"] + "-test-build" and job.get("status", {}).get(
                "active"
            ):
                pods = json.loads(
                    delivery.proof.kube(
                        "-n",
                        delivery.prefix + "-python-build",
                        "get",
                        "pods",
                        "-l",
                        "job-name=" + job["metadata"]["name"],
                        "-o",
                        "json",
                    )
                )["items"]
                owned = [
                    pod
                    for pod in pods
                    if pod["status"]["phase"] == "Running"
                    and any(
                        ref["uid"] == job["metadata"]["uid"]
                        for ref in pod["metadata"].get("ownerReferences", [])
                    )
                ]
                if owned:
                    return {"build": build, "job": job, "pods": owned}
        return None

    started = delivery.checkpoint(
        "inflight-started",
        lambda: delivery.wait("older build executing in its source sandbox", running_source),
    )
    newer = delivery.merge("inflight-newer", "python", "test")

    def advanced():
        release = delivery.release("python", "test")
        if release["document"]["desired_commit"] != newer["event"]["after"]:
            return None
        old = next(
            row for row in delivery.collection("builds") if row["id"] == started["build"]["id"]
        )
        if old["status"] != "admitted" or old["document"].get("result") is not None:
            raise RuntimeError(
                "older build finished before the newer desired revision was observed"
            )
        return {"release": release, "older": old}

    desired = delivery.checkpoint(
        "inflight-newer-desired", lambda: delivery.wait("newer desired revision", advanced)
    )

    def suppressed():
        old = delivery.build(older)
        if old["status"] != "superseded" or old["document"]["result"]["status"] != "success":
            raise RuntimeError("successful older build was not superseded")
        release = delivery.release("python", "test")
        if (
            release["document"]["observed"] != before["document"]["observed"]
            or release["document"]["recording"] != before["document"]["recording"]
            or release["document"]["desired_commit"] != newer["event"]["after"]
        ):
            raise RuntimeError(
                "superseded build changed the healthy release or newer desired state"
            )
        operations = delivery.collection("operations")
        if any(
            row["document"].get("execution", {}).get("build_id") == old["id"] for row in operations
        ):
            raise RuntimeError("superseded build created a release operation")
        return {"build": old, "release": release, "operations": operations}

    superseded = delivery.checkpoint("inflight-superseded", suppressed)
    built = delivery.build(newer)
    if built["status"] != "published":
        raise RuntimeError("newer revision did not publish successfully")
    final = delivery.deployed("python", "test", built["id"])
    return {
        "before": before,
        "older": older,
        "newer": newer,
        "running_source": started,
        "desired_before_completion": desired,
        "superseded": superseded,
        "final": final,
    }
