"""Verify stopped application activity while retaining completed networking evidence."""


def verify_inactive_workloads(items):
    jobs = {item["metadata"]["uid"]: item for item in items if item["kind"] == "Job"}
    retained = []
    for item in items:
        kind = item["kind"]
        if kind == "Deployment" and item["spec"]["replicas"] != 0:
            raise RuntimeError("inactive Deployment has replicas")
        if kind == "CronJob" and not item["spec"]["suspend"]:
            raise RuntimeError("inactive schedule is not suspended")
        if kind == "Job" and item.get("status", {}).get("active", 0):
            raise RuntimeError("active Job remains after deactivation")
        if kind != "Pod":
            continue
        metadata, status = item["metadata"], item.get("status", {})
        labels = metadata.get("labels", {})
        owners = [owner for owner in metadata.get("ownerReferences", []) if owner.get("controller")]
        owner = owners[0] if len(owners) == 1 else {}
        job = jobs.get(owner.get("uid"), {})
        terminal = any(
            condition.get("type") in {"Complete", "Failed"} and condition.get("status") == "True"
            for condition in job.get("status", {}).get("conditions", [])
        )
        if (
            status.get("phase") not in {"Succeeded", "Failed"}
            or labels.get("goblin-king.io/trust") != "network-control"
            or labels.get("app.kubernetes.io/instance")
            or owner.get("kind") != "Job"
            or job.get("metadata", {}).get("name") != owner.get("name")
            or not job.get("metadata", {}).get("labels", {}).get("goblin-king.io/operation")
            or not terminal
        ):
            raise RuntimeError("scheduled or background Pod remains after deactivation")
        retained.append(
            {
                "name": metadata["name"],
                "uid": metadata["uid"],
                "phase": status["phase"],
                "job_name": owner["name"],
                "job_uid": owner["uid"],
            }
        )
    return retained
