"""Verify outbound failure stays bounded during proxy, DNS and controller outages."""

from __future__ import annotations

import json
import time
from uuid import uuid4

from pipeline_network_fixture import external_ingress
from pipeline_network_probes import DNS
from pipeline_network_revocation_acceptance import DENIED


def fail_closed(network):
    delivery, proof = network.delivery, network.proof
    namespace = delivery.prefix + "-python-test"
    upstream = json.loads(network.fixture.path.read_text())
    arguments = upstream | {"revoked_direct": False}
    evidence = {}

    def probe(script=DENIED):
        return json.loads(
            proof.kube(
                "-n",
                namespace,
                "exec",
                "-i",
                "deployment/app",
                "--",
                "python",
                "-c",
                script,
                document=arguments,
            )
        )

    before = probe(DNS)
    if before != {"allowed": 200, "rebind": 200, "mixed": 200}:
        raise RuntimeError("failure tests require three successful controlled upstream baselines")
    network.fixture.control()
    proof.kube("-n", namespace, "scale", "deployment/app-egress", "--replicas=0")
    try:
        delivery.wait(
            "outbound proxy termination",
            lambda: (
                not json.loads(
                    proof.kube(
                        "-n",
                        namespace,
                        "get",
                        "pods",
                        "-l",
                        "app.kubernetes.io/component=egress",
                        "-o",
                        "json",
                    )
                )["items"]
            ),
            timeout=90,
        )
        evidence["proxy-outage"] = probe()
        evidence["ingress-during-outage"] = external_ingress(proof, delivery, "python", "test")
        proof.kube("-n", delivery.control, "rollout", "restart", "deployment/delivery-api")
        proof.kube(
            "-n", delivery.control, "rollout", "status", "deployment/delivery-api", "--timeout=120s"
        )
        evidence["controller-restart"] = probe()
    finally:
        proof.kube("-n", namespace, "scale", "deployment/app-egress", "--replicas=1")
        proof.kube("-n", namespace, "rollout", "status", "deployment/app-egress", "--timeout=120s")
    configuration = json.loads(
        proof.kube("-n", namespace, "get", "configmap", "app-egress", "-o", "json")
    )
    original = configuration["data"]["squid.conf"]
    configuration["data"]["squid.conf"] = "invalid_acceptance_directive\n"
    proof.kube("replace", "-f", "-", document=configuration)
    try:
        proof.kube("-n", namespace, "rollout", "restart", "deployment/app-egress")
        delivery.wait(
            "invalid proxy configuration failure",
            lambda: any(
                item.get("status", {}).get("containerStatuses", [{}])[0].get("restartCount", 0) > 0
                for item in json.loads(
                    proof.kube(
                        "-n",
                        namespace,
                        "get",
                        "pods",
                        "-l",
                        "app.kubernetes.io/component=egress",
                        "-o",
                        "json",
                    )
                )["items"]
            ),
            timeout=120,
        )
        evidence["invalid-proxy-configuration"] = probe()
    finally:
        current = json.loads(
            proof.kube("-n", namespace, "get", "configmap", "app-egress", "-o", "json")
        )
        if current["metadata"]["uid"] != configuration["metadata"]["uid"]:
            raise RuntimeError("proxy configuration identity changed during acceptance")
        current["data"]["squid.conf"] = original
        proof.kube("replace", "-f", "-", document=current)
        proof.kube("-n", namespace, "rollout", "restart", "deployment/app-egress")
        proof.kube("-n", namespace, "rollout", "status", "deployment/app-egress", "--timeout=120s")
    try:
        network.fixture.control({"allowed.fixture.example.test": []})
        deadline = time.monotonic() + 90
        while True:
            result = probe(DNS)
            if result["allowed"] != 200 and result["rebind"] == result["mixed"] == 200:
                break
            if time.monotonic() >= deadline:
                raise RuntimeError("DNS outage did not stop approved hostname traffic")
            time.sleep(2)
        evidence["dns-unavailable"] = {"dns": result, "bypass": probe()}
    finally:
        network.fixture.control({"allowed.fixture.example.test": [upstream["address"]]})
        delivery.wait("DNS recovery", lambda: probe(DNS) == before, timeout=90)
    operation = network.probe("python", "test", "failure-recovery-" + str(uuid4()))
    if operation["status"] != "completed":
        raise RuntimeError("network observation did not recover after restored dependencies")
    evidence["recovered"] = operation
    evidence["upstream"] = network.fixture.control()
    return network.save("network-fail-closed", evidence)
