"""Fenced Kubernetes networking effects with retained identities and health evidence."""

from __future__ import annotations

import base64
import copy
import datetime
import hashlib
import http.client
import inspect
import socket
import ssl
import time

from cryptography import x509
from cryptography.hazmat.primitives import serialization
from kubernetes.client.exceptions import ApiException

from goblin_king.pipeline_kubernetes import RecoveryHold
from goblin_king.pipeline_network_resources import dns_rule, render_networking
from goblin_king.pipeline_networking import NetworkingConfiguration, proxy_environment
from goblin_king.pipeline_store import _fingerprint

KINDS = {
    "ConfigMap": ("v1", "configmaps"),
    "Service": ("v1", "services"),
    "Deployment": ("apps/v1", "deployments"),
    "Pod": ("v1", "pods"),
    "CronJob": ("batch/v1", "cronjobs"),
    "Job": ("batch/v1", "jobs"),
    "NetworkPolicy": ("networking.k8s.io/v1", "networkpolicies"),
    "HTTPRoute": ("gateway.networking.k8s.io/v1", "httproutes"),
    "BackendTrafficPolicy": ("gateway.envoyproxy.io/v1alpha1", "backendtrafficpolicies"),
    "Gateway": ("gateway.networking.k8s.io/v1", "gateways"),
    "Secret": ("v1", "secrets"),
}


def contains(actual, expected):
    """Compare declared fields while allowing Kubernetes to add server defaults."""
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(
            key in actual and contains(actual[key], value) for key, value in expected.items()
        )
    if isinstance(expected, list):
        return (
            isinstance(actual, list)
            and len(actual) == len(expected)
            and all(contains(a, e) for a, e in zip(actual, expected, strict=True))
        )
    return actual == expected


class NetworkRuntime:
    def __init__(
        self, api, ledger, policy, *, namespace, release, project_id, environment, timeout=300
    ):
        self.api, self.ledger, self.policy = api, ledger, policy
        self.namespace, self.release = namespace, release
        self.project_id, self.environment = project_id, environment
        self.deadline = time.monotonic() + timeout
        self.response_arguments = (
            {"response_types_map": {200: "object", 201: "object", 202: "object"}}
            if "response_types_map" in inspect.signature(api.call_api).parameters
            else {"response_type": "object"}
        )

    def call(self, kind, method, name="", *, namespace=None, body=None, query=None):
        version, plural = KINDS[kind]
        prefix = "/api/" if version == "v1" else "/apis/"
        path = f"{prefix}{version}/namespaces/{namespace or self.namespace}/{plural}"
        if name:
            path += "/" + name
        return self.api.call_api(
            path,
            method,
            body=body,
            query_params=query or [],
            **self.response_arguments,
            auth_settings=["BearerToken"],
            header_params={
                "Accept": "application/json",
                "Content-Type": "application/merge-patch+json"
                if method == "PATCH"
                else "application/json",
            },
            _return_http_data_only=True,
            _request_timeout=20,
        )

    def read(self, kind, name, *, namespace=None):
        try:
            resource = self.call(kind, "GET", name, namespace=namespace)
            if kind == "NetworkPolicy":
                for direction in ("Ingress", "Egress"):
                    if direction in resource["spec"].get("policyTypes", []):
                        resource["spec"].setdefault(direction.lower(), [])
            return resource
        except ApiException as error:
            if error.status != 404:
                raise
            return None

    def wait(self, check, reason):
        while True:
            observation = check()
            if observation:
                return observation
            if time.monotonic() >= self.deadline:
                raise RecoveryHold(reason)
            time.sleep(1)

    def owned(self, resource):
        labels = resource["metadata"].get("labels", {})
        return (
            resource["metadata"].get("namespace") == self.namespace
            and labels.get("app.kubernetes.io/instance") == self.release
            and labels.get("goblin-king.io/networking") == "true"
        )

    def _intent(self, key, desired):
        fingerprint = _fingerprint(desired)
        effects = self.ledger.document.setdefault("network_effects", {})
        existing = effects.get(key)
        if existing and existing["fingerprint"] != fingerprint:
            raise RecoveryHold("network effect conflicts with durable intent")
        if not existing:
            effects[key] = {"fingerprint": fingerprint, "complete": False}
            self.ledger.save()
        return effects[key]

    def apply(self, desired):
        kind, name = desired["kind"], desired["metadata"]["name"]
        if not self.owned(desired):
            raise ValueError("network resource is outside this release")
        key = "apply/" + kind + "/" + name
        effect = self._intent(key, desired)
        current = self.read(kind, name)
        if effect["complete"]:
            if not current or current["metadata"]["uid"] != effect["uid"]:
                raise RecoveryHold("acknowledged network resource was removed or replaced")
            if not contains(current, desired):
                raise RecoveryHold("acknowledged network resource has drifted")
            return current
        if current and not self.owned(current):
            raise RecoveryHold("network resource name belongs to another owner")
        if current and effect.get("uid") and current["metadata"]["uid"] != effect["uid"]:
            raise RecoveryHold("network resource identity changed during apply")
        if current:
            effect["uid"] = current["metadata"]["uid"]
            self.ledger.save()
        body = copy.deepcopy(desired)
        body["metadata"]["annotations"]["goblin-king.io/effect"] = effect["fingerprint"]
        if (
            current
            and current["metadata"].get("annotations", {}).get("goblin-king.io/effect")
            == effect["fingerprint"]
            and not contains(current, desired)
        ):
            raise RecoveryHold("network intent marker differs from actual resource content")
        # A response lost after an update is recognized by its identity and intent marker.
        if (
            not current
            or current["metadata"].get("annotations", {}).get("goblin-king.io/effect")
            != effect["fingerprint"]
        ):
            if current:
                body["metadata"]["resourceVersion"] = current["metadata"]["resourceVersion"]
                if kind == "Service":
                    for field in ("clusterIP", "clusterIPs", "ipFamilies", "ipFamilyPolicy"):
                        if field in current["spec"]:
                            body["spec"][field] = current["spec"][field]
            self.ledger.fault_hook("networking.before_apply", key)
            current = self.call(
                kind, "PUT" if current else "POST", name if current else "", body=body
            )
            self.ledger.fault_hook("networking.after_apply", key)
        effect.update(
            complete=True,
            uid=current["metadata"]["uid"],
            generation=current["metadata"].get("generation"),
        )
        self.ledger.save()
        self.ledger.fault_hook("networking.after_apply_ack", key)
        return current

    def remove(self, kind, name):
        key = "remove/" + kind + "/" + name
        current = self.read(kind, name)
        effect = self._intent(key, {"kind": kind, "name": name})
        if effect["complete"] and current:
            raise RecoveryHold("removed network resource reappeared")
        if current:
            if not self.owned(current):
                raise RecoveryHold("refusing to remove another owner's network resource")
            if effect.get("uid") and effect["uid"] != current["metadata"]["uid"]:
                raise RecoveryHold("network resource was replaced during removal")
            effect["uid"] = current["metadata"]["uid"]
            self.ledger.save()
            self.ledger.fault_hook("networking.before_remove", key)
            self.call(
                kind,
                "DELETE",
                name,
                body={
                    "apiVersion": "v1",
                    "kind": "DeleteOptions",
                    "propagationPolicy": "Foreground",
                    "preconditions": {"uid": effect["uid"]},
                },
            )
            self.ledger.fault_hook("networking.after_remove", key)
            self.wait(
                lambda: self.read(kind, name) is None, "network resource deletion is unsettled"
            )
        effect["complete"] = True
        self.ledger.save()
        self.ledger.fault_hook("networking.after_remove_ack", key)

    def verify_certificate(self, ingress):
        binding = self.policy.gateways[ingress.gateway]
        gateway = self.read("Gateway", binding.name, namespace=binding.namespace)
        if not gateway:
            raise ValueError("approved gateway is absent")
        listeners = [
            item for item in gateway["spec"]["listeners"] if item["name"] == binding.listener
        ]
        secret_name = binding.certificates[ingress.certificate]
        if not listeners or not any(
            ref.get("name") == secret_name
            and ref.get("namespace", binding.namespace) == binding.namespace
            for ref in listeners[0].get("tls", {}).get("certificateRefs", [])
        ):
            raise ValueError("certificate is not attached to the approved listener")
        secret = self.read("Secret", secret_name, namespace=binding.namespace)
        if not secret or secret.get("type") != "kubernetes.io/tls":
            raise ValueError("approved TLS certificate is unavailable")
        certificate = x509.load_pem_x509_certificate(base64.b64decode(secret["data"]["tls.crt"]))
        now = datetime.datetime.now(datetime.UTC)
        if not certificate.not_valid_before_utc <= now < certificate.not_valid_after_utc:
            raise ValueError("TLS certificate is outside its validity period")
        names = certificate.extensions.get_extension_for_class(
            x509.SubjectAlternativeName
        ).value.get_values_for_type(x509.DNSName)
        if not any(
            ingress.hostname == name
            or (
                name.startswith("*.")
                and ingress.hostname.count(".") == name.count(".")
                and ingress.hostname.endswith(name[1:])
            )
            for name in names
        ):
            raise ValueError("TLS certificate does not cover the configured hostname")
        return {
            "sha256": hashlib.sha256(
                certificate.public_bytes(serialization.Encoding.DER)
            ).hexdigest(),
            "expires_at": certificate.not_valid_after_utc.isoformat(),
            "hostname": ingress.hostname,
        }

    def verify_resources(self, desired):
        observations = []
        for resource in desired:
            kind, name = resource["kind"], resource["metadata"]["name"]

            def ready(kind=kind, name=name, resource=resource):
                current = self.read(kind, name)
                if not current:
                    return None
                if kind == "Deployment":
                    status = current.get("status", {})
                    if (
                        status.get("observedGeneration") != current["metadata"]["generation"]
                        or status.get("availableReplicas", 0) != current["spec"]["replicas"]
                    ):
                        return None
                if kind == "HTTPRoute":
                    parents = current.get("status", {}).get("parents", [])
                    # A foreign controller's status is not evidence for the selected parent.
                    expected = resource["spec"]["parentRefs"][0]
                    matches = [
                        entry
                        for entry in parents
                        if all(
                            entry["parentRef"].get(key) == value for key, value in expected.items()
                        )
                    ]
                    if not matches or not all(
                        any(
                            c["type"] == condition
                            and c["status"] == "True"
                            and c.get("observedGeneration") == current["metadata"]["generation"]
                            for c in entry.get("conditions", [])
                        )
                        for entry in matches
                        for condition in ("Accepted", "ResolvedRefs")
                    ):
                        return None
                if kind == "BackendTrafficPolicy":
                    ancestors = current.get("status", {}).get("ancestors", [])
                    if not ancestors or not all(
                        any(
                            condition["type"] == "Accepted"
                            and condition["status"] == "True"
                            and condition.get("observedGeneration")
                            == current["metadata"]["generation"]
                            for condition in entry.get("conditions", [])
                        )
                        for entry in ancestors
                    ):
                        return None
                return current

            current = self.wait(ready, "network resource did not become ready: " + name)
            observations.append(
                {
                    "kind": kind,
                    "name": name,
                    "namespace": self.namespace,
                    "uid": current["metadata"]["uid"],
                    "generation": current["metadata"].get("generation"),
                    "status": current.get("status", {}),
                }
            )
        return observations

    def inspect_policy_boundary(self):
        """Additional Kubernetes allow rules cannot be counteracted by a deny policy."""
        for item in self.call("NetworkPolicy", "GET")["items"]:
            if self.owned(item) and item["metadata"]["name"] in {
                self.release + "-networking",
                self.release + "-egress",
            }:
                continue
            spec = item["spec"]
            # This installation rule selects only the dedicated configuration Pods.
            # Application and proxy templates never carry this identity label.
            if spec.get("podSelector") == {
                "matchLabels": {"goblin-king.io/trust": "network-control"}
            }:
                continue
            if any(rule != dns_rule() for rule in spec.get("egress", [])):
                raise RecoveryHold(
                    "additional outbound policy requires installation review: "
                    + item["metadata"]["name"]
                )
            # The installed namespace policy permits its own pods and named control namespaces.
            for rule in spec.get("ingress", []):
                sources = rule.get("from")
                if not sources:
                    raise RecoveryHold("additional unrestricted ingress policy")
                for source in sources:
                    if "ipBlock" in source:
                        raise RecoveryHold("additional ingress address policy requires review")
                    selector = source.get("namespaceSelector")
                    if selector is not None and not selector.get("matchLabels", {}).get(
                        "kubernetes.io/metadata.name"
                    ):
                        raise RecoveryHold("additional namespace ingress policy requires review")

    def application_workloads(self):
        if "application_workloads" not in self.ledger.document:
            saved = []
            for kind in ("Deployment", "CronJob"):
                for item in self.call(
                    kind,
                    "GET",
                    query=[("labelSelector", "app.kubernetes.io/instance=" + self.release)],
                )["items"]:
                    if self.owned(item):
                        continue
                    if item["metadata"]["name"] not in {
                        self.release,
                        self.release + "-background",
                        self.release + "-schedule",
                    }:
                        raise RecoveryHold("unexpected application workload identity")
                    saved.append(
                        {
                            "kind": kind,
                            "name": item["metadata"]["name"],
                            "uid": item["metadata"]["uid"],
                            "spec": item["spec"],
                        }
                    )
            self.ledger.document["application_workloads"] = saved
            self.ledger.save()
        return self.ledger.document["application_workloads"]

    def change_workload(self, item, changes, phase):
        key = phase + "/" + item["kind"] + "/" + item["name"]
        effect = self._intent(key, {"uid": item["uid"], "changes": changes})
        current = self.read(item["kind"], item["name"])
        if not current or current["metadata"]["uid"] != item["uid"]:
            raise RecoveryHold("application identity changed during network transition")
        if effect["complete"]:
            return
        if not contains(current, {"spec": changes}):
            self.ledger.fault_hook("networking.before_workload_change", key)
            self.call(
                item["kind"],
                "PATCH",
                item["name"],
                body={
                    "metadata": {"resourceVersion": current["metadata"]["resourceVersion"]},
                    "spec": changes,
                },
            )
            self.ledger.fault_hook("networking.after_workload_change", key)
        effect["complete"] = True
        self.ledger.save()
        self.ledger.fault_hook("networking.after_workload_change_ack", key)

    def settle_application(self):
        if self.ledger.document.get("application_settled"):
            return
        for item in self.application_workloads():
            changes = {"suspend": True} if item["kind"] == "CronJob" else {"replicas": 0}
            self.change_workload(item, changes, "quiesce")
        schedules = {
            item["uid"] for item in self.application_workloads() if item["kind"] == "CronJob"
        }
        for job in self.call(
            "Job", "GET", query=[("labelSelector", "app.kubernetes.io/instance=" + self.release)]
        )["items"]:
            if not any(
                ref["uid"] in schedules for ref in job["metadata"].get("ownerReferences", [])
            ):
                raise RecoveryHold("scheduled Job has an unexpected owner")
            key = "settle/Job/" + job["metadata"]["uid"]
            effect = self._intent(key, {"uid": job["metadata"]["uid"]})
            self.ledger.fault_hook("networking.before_job_removal", key)
            self.call(
                "Job",
                "DELETE",
                job["metadata"]["name"],
                body={
                    "propagationPolicy": "Foreground",
                    "preconditions": {"uid": job["metadata"]["uid"]},
                },
            )
            self.ledger.fault_hook("networking.after_job_removal", key)
            effect["complete"] = True
            self.ledger.save()
        selector = (
            "app.kubernetes.io/instance="
            + self.release
            + ",app.kubernetes.io/component in (service,background,schedule)"
        )
        self.wait(
            lambda: not self.call("Pod", "GET", query=[("labelSelector", selector)])["items"],
            "application connections have not terminated",
        )
        self.ledger.document["application_settled"] = True
        self.ledger.save()

    def restore_application(self, configuration, active):
        names = {"http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY", "no_proxy", "NO_PROXY"}
        variables = proxy_environment(configuration, self.namespace, self.release)
        for item in self.application_workloads():
            spec = copy.deepcopy(item["spec"])
            template = (
                spec["jobTemplate"]["spec"]["template"]
                if item["kind"] == "CronJob"
                else spec["template"]
            )
            for container in template["spec"]["containers"]:
                container["env"] = [
                    entry for entry in container.get("env", []) if entry["name"] not in names
                ] + variables
            if item["kind"] == "CronJob":
                changes = {"suspend": not active, "jobTemplate": spec["jobTemplate"]}
            else:
                changes = {"replicas": 1 if active else 0, "template": template}
            self.change_workload(item, changes, "restore")

    def verify_route(self, ingress, health_path):
        binding = self.policy.gateways[ingress.gateway]
        gateway = self.read("Gateway", binding.name, namespace=binding.namespace)
        addresses = gateway.get("status", {}).get("addresses", [])
        address = binding.probe_address or (addresses[0]["value"] if addresses else "")
        if not address:
            raise RecoveryHold("gateway has no approved probe address")
        secret = self.read(
            "Secret", binding.certificates[ingress.certificate], namespace=binding.namespace
        )
        context = ssl.create_default_context(
            cadata=base64.b64decode(secret["data"]["tls.crt"]).decode()
        )

        class Connection(http.client.HTTPSConnection):
            def connect(connection):
                raw = socket.create_connection((address, binding.probe_port), timeout=5)
                try:
                    connection.sock = context.wrap_socket(raw, server_hostname=ingress.hostname)
                except Exception:
                    raw.close()
                    raise

        # Health paths must be reachable through the selected route, not rewritten implicitly.
        if ingress.path_type == "Exact":
            path = ingress.path
        elif ingress.path == "/" or health_path.startswith(ingress.path.rstrip("/") + "/"):
            path = health_path
        else:
            path = ingress.path

        def probe():
            connection = Connection(ingress.hostname, timeout=5)
            try:
                connection.request("GET", path, headers={"Host": ingress.hostname})
                response = connection.getresponse()
                body = response.read(8192)
                if response.status != 200:
                    return None
                return {
                    "status": response.status,
                    "url": "https://" + ingress.hostname + path,
                    "body_sha256": hashlib.sha256(body).hexdigest(),
                    "tls_verified": True,
                    "vantage": "cluster",
                    "address": address,
                }
            except (OSError, http.client.HTTPException):
                return None
            finally:
                connection.close()

        return self.wait(probe, "gateway TLS or application route health did not pass")

    def verify_application(self, *, active, port, health_path):
        if not active:
            return {"active": False}

        def probe():
            for item in self.application_workloads():
                current = self.read(item["kind"], item["name"])
                if not current or current["metadata"]["uid"] != item["uid"]:
                    raise RecoveryHold(
                        "application identity changed during networking health check"
                    )
                if item["kind"] == "Deployment" and (
                    current.get("status", {}).get("observedGeneration")
                    != current["metadata"]["generation"]
                    or current.get("status", {}).get("availableReplicas", 0) != 1
                ):
                    return None
            host = f"{self.release}.{self.namespace}.svc"
            connection = http.client.HTTPConnection(host, port, timeout=5)
            try:
                connection.request("GET", health_path)
                response = connection.getresponse()
                body = response.read(8192)
                if response.status != 200:
                    return None
                return {
                    "active": True,
                    "status": 200,
                    "body_sha256": hashlib.sha256(body).hexdigest(),
                }
            except (OSError, http.client.HTTPException):
                return None
            finally:
                connection.close()

        return self.wait(probe, "application did not recover after network configuration")

    def configure(
        self,
        configuration,
        *,
        operation_id,
        active,
        port,
        previous=None,
        health_path="/health",
        prepare_only=False,
        probe_only=False,
    ):
        configuration = NetworkingConfiguration.model_validate(configuration)
        desired = render_networking(
            configuration,
            self.policy,
            namespace=self.namespace,
            release=self.release,
            project_id=self.project_id,
            environment=self.environment,
            operation_id=operation_id,
            active=active,
            port=port,
        )
        certificate = (
            self.verify_certificate(configuration.ingress)
            if configuration.ingress.enabled
            else None
        )
        self.inspect_policy_boundary()
        if probe_only:
            for resource in desired:
                current = self.read(resource["kind"], resource["metadata"]["name"])
                expected = {key: resource[key] for key in ("spec", "data") if key in resource}
                if not current or not self.owned(current) or not contains(current, expected):
                    raise RecoveryHold("network resource differs from approved configuration")
            for kind, suffix in (
                ("HTTPRoute", "-ingress"),
                ("HTTPRoute", "-redirect"),
                ("Deployment", "-egress"),
                ("Service", "-egress"),
            ):
                if not any(
                    item["kind"] == kind and item["metadata"]["name"] == self.release + suffix
                    for item in desired
                ) and self.read(kind, self.release + suffix):
                    raise RecoveryHold("unexpected network resource remains active")
            return self.observe(configuration, desired, certificate, active, port, health_path)
        old = NetworkingConfiguration.model_validate((previous or {}).get("configuration", {}))
        if not active:
            self.remove("HTTPRoute", self.release + "-ingress")
            self.remove("HTTPRoute", self.release + "-redirect")
        if (
            old.egress != configuration.egress
            or (previous or {}).get("policy_revision") != self.policy.revision
            or not active
        ):
            self.settle_application()
            # Terminate old CONNECT tunnels before publishing a changed destination policy.
            if not self.ledger.document.get("proxy_quiesced"):
                self.remove("Deployment", self.release + "-egress")
                self.wait(
                    lambda: (
                        not self.call(
                            "Pod",
                            "GET",
                            query=[
                                (
                                    "labelSelector",
                                    "app.kubernetes.io/instance="
                                    + self.release
                                    + ",app.kubernetes.io/component=egress",
                                )
                            ],
                        )["items"]
                    ),
                    "outbound proxy connections have not terminated",
                )
                self.ledger.document["proxy_quiesced"] = True
                self.ledger.save()
        for resource in desired:
            self.apply(resource)
        wanted = {(item["kind"], item["metadata"]["name"]) for item in desired}
        for kind, suffix in (
            ("HTTPRoute", "-ingress"),
            ("HTTPRoute", "-redirect"),
            ("BackendTrafficPolicy", "-ingress-limits"),
            ("Deployment", "-egress"),
            ("Service", "-egress"),
            ("ConfigMap", "-egress"),
        ):
            if (kind, self.release + suffix) not in wanted:
                self.remove(kind, self.release + suffix)
        if not prepare_only:
            self.restore_application(configuration, active)
        return self.observe(
            configuration,
            desired,
            certificate,
            active,
            port,
            health_path,
            prepare_only=prepare_only,
        )

    def observe(
        self, configuration, desired, certificate, active, port, health_path, *, prepare_only=False
    ):
        resources = self.verify_resources(desired)
        application_health = (
            self.verify_application(active=active, port=port, health_path=health_path)
            if not prepare_only
            else None
        )
        route_health = (
            self.verify_route(configuration.ingress, health_path)
            if active and configuration.ingress.enabled and not prepare_only
            else None
        )
        return {
            "configuration": configuration.model_dump(mode="json"),
            "configuration_revision": configuration.revision,
            "policy_revision": self.policy.revision,
            "resources": resources,
            "route_health": route_health,
            "application_health": application_health,
            "certificate": certificate,
            "external_reachability": "unverified",
            "active": active,
        }
