"""Atomic networking previews, route reservations and serialized change requests."""

import json
from uuid import uuid4

from sqlalchemy import select

from goblin_king.contracts import utc_now
from goblin_king.pipeline_networking import NetworkingConfiguration, routes_overlap
from goblin_king.pipeline_state import PipelineConflict, encoded
from goblin_king.pipeline_store import _fingerprint
from goblin_king.store_schema import pipeline_actions_table as actions
from goblin_king.store_schema import pipeline_environment_states_table as releases
from goblin_king.store_schema import pipeline_networking_table as networking
from goblin_king.store_schema import pipeline_operations_table as operations


class NetworkManagement:
    def __init__(self, state, policy, *, recovery_mode=False):
        self.state, self.policy, self.recovery_mode = state, policy, recovery_mode

    def release(self, key):
        with self.state.store.engine.connect() as connection:
            row = self.state.row(connection, releases, key)
        if row is None:
            raise KeyError(key)
        return row

    def inspect(self, key):
        with self.state.store.engine.connect() as connection:
            release = self.state.row(connection, releases, key)
            if release is None:
                raise KeyError(key)
            row = self.state.row(connection, networking, key)
        return {
            "release_key": key,
            "version": release["version"],
            "network_version": row["version"] if row else 0,
            "desired": row["document"]["desired"]
            if row
            else NetworkingConfiguration().model_dump(mode="json"),
            "observed": row["document"].get("observed") if row else None,
            "last_probe": row["document"].get("last_probe") if row else None,
            "operation_id": release["document"]["operation_id"],
            "hold": release["document"]["hold"],
            "active": release["document"]["active"],
            "policy_revision": self.policy.revision,
        }

    def diagnostics(self, project_id):
        items = []
        for row in self.state.list(releases, project_id=project_id):
            current = self.inspect(row["key"])
            observed = current["observed"]
            reasons = []
            if current["hold"]:
                reasons.append("Resolve the retained recovery hold before changing networking.")
            if current["operation_id"]:
                reasons.append("A release operation is in progress; inspect its retained evidence.")
            if current["network_version"] and not observed:
                reasons.append("No verified network observation is recorded yet.")
            if observed and observed.get("policy_revision") != self.policy.revision:
                reasons.append(
                    "Installation policy changed; preview and apply the configuration again."
                )
            if (
                observed
                and observed.get("configuration_revision")
                != NetworkingConfiguration.model_validate(current["desired"]).revision
            ):
                reasons.append("Desired and observed network configurations differ.")
            if current["last_probe"] and (
                current["last_probe"].get("status") != "success"
                or current["last_probe"].get("data", {}).get("recovery_hold")
            ):
                reasons.append(
                    "The last health probe failed; inspect its operation before retrying."
                )
            items.append(
                {
                    "release_key": row["key"],
                    "version": current["version"],
                    "namespace": row["document"]["environment"]["namespace"],
                    "environment": row["document"]["environment"]["name"],
                    "network_version": current["network_version"],
                    "operation_id": current["operation_id"],
                    "status": "attention"
                    if reasons
                    else "observed"
                    if observed
                    else "not-configured",
                    "actions": reasons,
                    "last_probe": current["last_probe"],
                    "application_health": (observed or {}).get("application_health"),
                    "route_health": (observed or {}).get("route_health"),
                    "certificate": (observed or {}).get("certificate"),
                    "external_reachability": (observed or {}).get(
                        "external_reachability", "unverified"
                    ),
                }
            )
        return {
            "policy_revision": self.policy.revision,
            "recovery_mode": self.recovery_mode,
            "observation_scope": "Retained observations; request a health probe for a fresh check.",
            "items": items,
        }

    def capabilities(self, project_id):
        entries = {
            key: value
            for key, value in self.policy.releases.items()
            if value.project_id == project_id
        }
        aliases = {name for value in entries.values() for name in value.gateways}
        return {
            "enabled": self.policy.enabled,
            "ingress_available": self.policy.enabled and self.policy.ingress_available,
            "egress_available": self.policy.enabled and self.policy.egress_available,
            "policy_revision": self.policy.revision,
            "releases": {key: value.model_dump(mode="json") for key, value in entries.items()},
            "gateways": {
                name: {
                    "mode": self.policy.gateways[name].mode,
                    "environment": self.policy.gateways[name].environment,
                    "exposure": self.policy.gateways[name].exposure,
                    "hostname_suffixes": self.policy.gateways[name].hostname_suffixes,
                    "certificates": sorted(self.policy.gateways[name].certificates),
                }
                for name in sorted(aliases)
            },
        }

    def _validate(self, connection, release, configuration):
        target = release["document"]["environment"]
        value = self.policy.validate_configuration(
            configuration,
            identity=target["namespace"] + "/" + target["release"],
            project_id=release["project_id"],
            environment=target["name"],
        )
        for raw in connection.execute(
            select(networking).where(networking.c.key != release["key"])
        ).mappings():
            document = json.loads(raw["document"])
            # Retain reservations for both desired and still-observed routes during a change.
            configurations = [document["desired"]]
            if document.get("observed"):
                configurations.append(document["observed"]["configuration"])
            for candidate in configurations:
                if routes_overlap(
                    value.ingress, NetworkingConfiguration.model_validate(candidate).ingress
                ):
                    raise PipelineConflict("hostname/path is already reserved")
        return value

    def _preview(self, connection, release, configuration):
        value = self._validate(connection, release, configuration)
        row = self.state.row(connection, networking, release["key"])
        prior = (
            row["document"]["desired"] if row else NetworkingConfiguration().model_dump(mode="json")
        )
        document = value.model_dump(mode="json")
        preview = {
            "release_key": release["key"],
            "expected_version": release["version"],
            "policy_revision": self.policy.revision,
            "configuration_revision": value.revision,
            "configuration": document,
            "previous": prior,
            "active": release["document"]["active"],
            "workload_restart": prior["egress"] != document["egress"]
            or bool(row and row["document"].get("policy_revision") != self.policy.revision),
            "url": "https://" + value.ingress.hostname + value.ingress.path
            if value.ingress.enabled
            else None,
            "external_reachability": "unverified" if value.ingress.enabled else "disabled",
        }
        preview["preview_id"] = _fingerprint(preview)
        return preview

    def preview(self, key, configuration):
        with self.state.transaction() as connection:
            release = self.state.row(connection, releases, key)
            if release is None:
                raise KeyError(key)
            return self._preview(connection, release, configuration)

    def apply(
        self,
        key,
        configuration,
        *,
        expected_version,
        preview_id,
        actor,
        idempotency_key,
        probe_only=False,
    ):
        if self.recovery_mode:
            raise PipelineConflict("installation is in recovery mode")
        config = NetworkingConfiguration.model_validate(configuration).model_dump(mode="json")
        fingerprint = _fingerprint(
            [expected_version, actor, "probe"]
            if probe_only
            else [config, expected_version, preview_id, actor]
        )
        state = self.state
        with state.transaction() as connection:
            release = state.row(connection, releases, key)
            if release is None:
                raise KeyError(key)
            action_key = _fingerprint(
                [
                    release["project_id"],
                    key,
                    "networking-probe" if probe_only else "networking",
                    idempotency_key,
                ]
            )
            prior = (
                connection.execute(select(actions).where(actions.c.key == action_key))
                .mappings()
                .one_or_none()
            )
            if prior:
                if prior["fingerprint"] != fingerprint:
                    raise PipelineConflict("idempotency key conflicts")
                return json.loads(prior["response"])
            state.expected(release, expected_version)
            if release["document"]["operation_id"]:
                raise PipelineConflict("release operation is still owned")
            if release["document"]["hold"]:
                raise PipelineConflict("release is held; reconcile before changing networking")
            preview = self._preview(connection, release, config)
            if preview["preview_id"] != preview_id:
                raise PipelineConflict(
                    "preview is stale; preview the current policy and configuration"
                )
            operation_id = str(uuid4())
            row = state.row(connection, networking, key)
            if probe_only and (not row or row["document"]["desired"] != config):
                raise PipelineConflict("probe requires the existing desired configuration")
            document = {
                "desired": config,
                "policy_revision": self.policy.revision,
                "configuration_revision": preview["configuration_revision"],
                "observed": row["document"].get("observed") if row else None,
                "operation_id": operation_id,
            }
            if row:
                state.cas(connection, networking, row, document)
            else:
                connection.execute(
                    networking.insert().values(
                        key=key,
                        project_id=release["project_id"],
                        version=1,
                        document=encoded(document),
                    )
                )
            connection.execute(
                operations.insert().values(
                    id=operation_id,
                    project_id=release["project_id"],
                    release_key=key,
                    version=1,
                    status="networking-pending",
                    document=encoded(
                        {
                            "action": "networking",
                            "probe_only": probe_only,
                            "actor": actor,
                            "payload": preview,
                            "generation": release["document"]["generation"],
                            "before": release["document"]["observed"],
                            "worker": None,
                            "evidence": [],
                            "started_at": utc_now().isoformat(),
                        }
                    ),
                )
            )
            state.cas(
                connection, releases, release, release["document"] | {"operation_id": operation_id}
            )
            response = {"operation_id": operation_id, "version": expected_version + 1}
            connection.execute(
                actions.insert().values(
                    key=action_key, fingerprint=fingerprint, response=encoded(response)
                )
            )
            state.audit(
                connection,
                release["project_id"],
                operation_id,
                "networking.requested",
                {
                    "actor": actor,
                    "configuration_revision": preview["configuration_revision"],
                    "policy_revision": self.policy.revision,
                },
            )
            return response
