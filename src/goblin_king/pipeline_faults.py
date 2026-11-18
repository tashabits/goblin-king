"""Operator-controlled pause points for local cluster interruption acceptance."""

import json
import time

from kubernetes.client.exceptions import ApiException


def no_fault(point, identity):
    return None


class AcceptanceFaultGate:
    """A disabled gate makes no requests; applications cannot configure this gate."""

    def __init__(self, core, namespace, *, enabled=False):
        self.core, self.namespace, self.enabled = core, namespace, enabled

    def plan(self):
        if not self.enabled:
            return {}
        try:
            record = self.core.read_namespaced_config_map(
                "pipeline-acceptance-faults", self.namespace
            )
        except ApiException as error:
            if error.status == 404:
                return {}
            raise
        raw = (record.data or {}).get("fault.json", "{}")
        if len(raw) > 8192:
            raise ValueError("acceptance fault configuration exceeds bounds")
        return json.loads(raw)

    def __call__(self, point, identity):
        plan = self.plan()
        if plan.get("point") != point or not identity.startswith(plan.get("identity_prefix", "")):
            return
        if plan.get("identity") is not None and plan["identity"] != identity:
            return
        if not plan.get("id"):
            raise ValueError("acceptance fault requires a unique identity")
        print(
            json.dumps(
                {"pipeline_fault": {"id": plan["id"], "point": point, "identity": identity}}
            ),
            flush=True,
        )
        deadline = time.monotonic() + 300
        while self.plan().get("id") == plan["id"]:
            if time.monotonic() >= deadline:
                raise RuntimeError("acceptance fault was not released within five minutes")
            time.sleep(1)


def configured_gate(core, namespace, config):
    enabled = bool(config.get("acceptance_faults"))
    if enabled and not config.get("provider", {}).get("simulated_provider"):
        raise ValueError("acceptance faults require the local simulated provider")
    return AcceptanceFaultGate(core, namespace, enabled=enabled)


class PublicationFaultGate:
    """Pause a credentialed fixture process without granting it Kubernetes API access."""

    def __init__(self, plan):
        self.plan = plan

    def __call__(self, point, identity):
        plan = self.plan
        if (
            not plan.get("id")
            or plan.get("point") != point
            or not identity.startswith(plan.get("identity_prefix", ""))
        ):
            return
        print(
            json.dumps(
                {"pipeline_fault": {"id": plan["id"], "point": point, "identity": identity}}
            ),
            flush=True,
        )
        time.sleep(300)
        raise RuntimeError("publication interruption was not applied within five minutes")
