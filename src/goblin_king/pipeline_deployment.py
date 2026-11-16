"""Render-bound deployment authority and independently observed HTTP release health."""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

import yaml
from kubernetes.utils.quantity import parse_quantity

from goblin_king.pipeline_kubernetes import RecoveryHold

ALLOWED_KINDS = {"Deployment", "Service", "PersistentVolumeClaim", "ServiceAccount", "CronJob"}


def validate_rendered(raw, *, namespace, release, image, pull_secret):
    """Reject authority outside the installed profile before Helm applies any resource."""
    resources = list(yaml.safe_load_all(raw))
    for resource in resources:
        if not isinstance(resource, dict) or resource.get("kind") not in ALLOWED_KINDS:
            raise ValueError("rendered resource kind is outside the approved HTTP profile")
        meta = resource["metadata"]
        if meta.get("namespace", namespace) != namespace:
            raise ValueError("rendered resource is outside the approved namespace")
        if meta["name"] not in {
            release,
            release + "-app",
            release + "-data",
            release + "-background",
            release + "-schedule",
        }:
            raise ValueError("rendered resource name is outside the approved release")
        if meta.get("labels", {}).get("app.kubernetes.io/instance") != release:
            raise ValueError("rendered resource is missing its release ownership label")
        kind = resource["kind"]
        if kind == "ServiceAccount" and resource.get("automountServiceAccountToken") is not False:
            raise ValueError("application ServiceAccounts must not mount tokens")
        if kind == "Service" and resource["spec"].get("type", "ClusterIP") != "ClusterIP":
            raise ValueError("only internal HTTP Services are permitted")
        if kind == "PersistentVolumeClaim":
            spec = resource["spec"]
            if spec.get("accessModes") != ["ReadWriteOnce"] or parse_quantity(
                spec["resources"]["requests"]["storage"]
            ) > parse_quantity("10Gi"):
                raise ValueError("persistent data exceeds the approved storage profile")
        if kind not in {"Deployment", "CronJob"}:
            continue
        spec = resource["spec"]
        if kind == "CronJob":
            if spec.get("concurrencyPolicy") != "Forbid":
                raise ValueError("scheduled work must forbid overlap")
            spec = spec["jobTemplate"]["spec"]
        elif spec.get("replicas", 1) not in {0, 1}:
            raise ValueError("profile permits only zero or one replica")
        pod = spec["template"]["spec"]
        if (
            pod.get("automountServiceAccountToken") is not False
            or pod.get("serviceAccountName") != release + "-app"
            or any(pod.get(key) for key in ("hostNetwork", "hostPID", "hostIPC", "initContainers"))
        ):
            raise ValueError("application Pod exceeds the approved execution identity")
        if pod.get("imagePullSecrets", []) != ([{"name": pull_secret}] if pull_secret else []):
            raise ValueError("unapproved image pull Secret reference")
        for volume in pod.get("volumes", []):
            if "persistentVolumeClaim" in volume:
                if volume["persistentVolumeClaim"]["claimName"] != release + "-data":
                    raise ValueError("unapproved data volume")
            elif "emptyDir" not in volume:
                raise ValueError("unapproved volume or Secret reference")
        for container in pod["containers"]:
            security = container.get("securityContext", {})
            if (
                container["image"] != image
                or security.get("allowPrivilegeEscalation") is not False
                or security.get("readOnlyRootFilesystem") is not True
                or security.get("capabilities", {}).get("drop") != ["ALL"]
                or security.get("privileged", False)
            ):
                raise ValueError("container exceeds the approved image or security profile")
            if any("valueFrom" in env for env in container.get("env", [])):
                raise ValueError(
                    "application Secret references require an approved profile extension"
                )
            limits = container["resources"]["limits"]
            if parse_quantity(limits["cpu"]) > 1 or parse_quantity(
                limits["memory"]
            ) > parse_quantity("1Gi"):
                raise ValueError("container exceeds resource limits")
    return resources


class HelmRelease:
    """One fenced worker owns a bounded Helm operation and route verification."""

    def __init__(self, core, apps, batch, namespace, release, *, timeout=300):
        self.core, self.apps, self.batch = core, apps, batch
        self.namespace, self.release, self.timeout = namespace, release, timeout

    def helm(self, *args):
        environment = os.environ | {
            "HELM_DRIVER": "configmap",
            "HELM_CACHE_HOME": "/tmp/helm/cache",
            "HELM_CONFIG_HOME": "/tmp/helm/config",
            "HELM_DATA_HOME": "/tmp/helm/data",
        }
        result = subprocess.run(
            ["helm", "--namespace", self.namespace, *args],
            capture_output=True,
            text=True,
            env=environment,
            timeout=self.timeout + 30,
        )
        if result.returncode:
            raise RuntimeError("Helm operation failed: " + result.stderr[-2000:])
        return result.stdout

    def history(self):
        return json.loads(self.helm("history", self.release, "--output", "json"))

    def verify(self, *, image, active, port, health_path):
        deadline = time.monotonic() + self.timeout
        selector = f"app.kubernetes.io/instance={self.release}"
        while True:
            deployments = self.apps.list_namespaced_deployment(
                self.namespace, label_selector=selector
            ).items
            pods = self.core.list_namespaced_pod(self.namespace, label_selector=selector).items
            schedules = self.batch.list_namespaced_cron_job(
                self.namespace, label_selector=selector
            ).items
            if not active:
                ready = (
                    deployments
                    and all(d.spec.replicas == 0 for d in deployments)
                    and all(c.spec.suspend for c in schedules)
                    and not pods
                )
                if ready:
                    return {"active": False, "workloads_stopped": True, "pod_count": 0}
            else:
                ready = bool(deployments) and all(
                    d.status.observed_generation == d.metadata.generation
                    and (d.status.available_replicas or 0) == 1
                    and all(c.image == image for c in d.spec.template.spec.containers)
                    for d in deployments
                )
                if ready:
                    url = f"http://{self.release}.{self.namespace}.svc:{port}{health_path}"
                    try:
                        with urlopen(url, timeout=5) as response:
                            body = response.read(8192).decode()
                            if response.status == 200:
                                return {
                                    "active": True,
                                    "url": url,
                                    "status": 200,
                                    "body": body,
                                    "pods": [
                                        {
                                            "name": p.metadata.name,
                                            "uid": p.metadata.uid,
                                            "node": p.spec.node_name,
                                        }
                                        for p in pods
                                    ],
                                }
                    except (URLError, TimeoutError):
                        pass
            if time.monotonic() > deadline:
                raise RuntimeError("release readiness or service route did not become healthy")
            time.sleep(1)

    def settle_schedules(self):
        selector = f"app.kubernetes.io/instance={self.release},app.kubernetes.io/component=schedule"
        for job in self.batch.list_namespaced_job(self.namespace, label_selector=selector).items:
            self.batch.delete_namespaced_job(
                job.metadata.name,
                self.namespace,
                body={
                    "propagationPolicy": "Foreground",
                    "preconditions": {"uid": job.metadata.uid},
                },
            )

    def recover(self, values, *, previous):
        """Inspect actual Helm state after confirmed termination; never repeat an upgrade."""
        history = self.history()
        actual = json.loads(self.helm("get", "values", self.release, "--output", "json"))
        if history[-1]["status"] == "deployed" and all(
            actual.get(key) == values[key] for key in ("image", "active", "operationId")
        ):
            health = self.verify(
                image=values["image"],
                active=values["active"],
                port=values["port"],
                health_path=values["healthPath"],
            )
            return {
                "status": "success",
                "image": values["image"],
                "active": values["active"],
                "helm_revision": history[-1]["revision"],
                "health": health,
                "port": values["port"],
                "health_path": values["healthPath"],
                "recovered": True,
            }
        if previous is None:
            raise RecoveryHold("first installation is ambiguous; no known healthy rollback exists")
        self.helm(
            "rollback",
            self.release,
            str(previous["helm_revision"]),
            "--wait=watcher",
            "--timeout",
            f"{self.timeout}s",
        )
        health = self.verify(
            image=previous["image"],
            active=previous["active"],
            port=previous["port"],
            health_path=previous["health_path"],
        )
        return {
            "status": "failed",
            "error": "interrupted rollout restored the previous healthy release",
            "rollback": previous
            | {"health": health, "helm_revision": self.history()[-1]["revision"]},
        }

    def apply(self, values, *, chart, previous):
        path = Path("/tmp/release-values.json")
        path.write_text(json.dumps(values))
        rendered = self.helm("template", self.release, str(chart), "--values", str(path))
        validate_rendered(
            rendered,
            namespace=self.namespace,
            release=self.release,
            image=values["image"],
            pull_secret=values["pullSecret"],
        )
        try:
            self.helm(
                "upgrade",
                "--install",
                self.release,
                str(chart),
                "--values",
                str(path),
                "--wait=watcher",
                "--timeout",
                f"{self.timeout}s",
                "--history-max",
                "20",
            )
            if not values["active"]:
                self.settle_schedules()
            health = self.verify(
                image=values["image"],
                active=values["active"],
                port=values["port"],
                health_path=values["healthPath"],
            )
        except Exception as error:
            if previous is None:
                return {"status": "failed", "error": str(error), "rollback": None}
            try:
                self.helm(
                    "rollback",
                    self.release,
                    str(previous["helm_revision"]),
                    "--wait=watcher",
                    "--timeout",
                    f"{self.timeout}s",
                )
                health = self.verify(
                    image=previous["image"],
                    active=previous["active"],
                    port=previous["port"],
                    health_path=previous["health_path"],
                )
            except Exception as rollback_error:
                raise RecoveryHold(
                    "failed release could not restore and verify the previous state"
                ) from rollback_error
            return {
                "status": "failed",
                "error": str(error),
                "rollback": {
                    **previous,
                    "health": health,
                    "helm_revision": self.history()[-1]["revision"],
                },
            }
        return {
            "status": "success",
            "image": values["image"],
            "active": values["active"],
            "helm_revision": self.history()[-1]["revision"],
            "health": health,
            "port": values["port"],
            "health_path": values["healthPath"],
        }
