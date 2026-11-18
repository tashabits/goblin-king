"""Typed networking configuration bounded by installation-owned capabilities."""

from __future__ import annotations

import ipaddress
import re
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from goblin_king.pipeline_hashing import fingerprint as _fingerprint

Port = Annotated[int, Field(strict=True, ge=1, le=65535)]
Name = Annotated[str, Field(pattern=r"^[a-z0-9]([-a-z0-9]{0,61}[a-z0-9])?$")]


def hostname(value: str) -> str:
    """Accept canonical exact DNS names, never URLs, wildcards, literals or ACL syntax."""
    value = value.lower().rstrip(".")
    if len(value) > 253 or "." not in value:
        raise ValueError("an exact fully qualified DNS hostname is required")
    if any(not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", p) for p in value.split(".")):
        raise ValueError("invalid DNS hostname")
    try:
        ipaddress.ip_address(value)
    except ValueError:
        return value
    raise ValueError("IP literals are not permitted as hostnames")


class NetworkModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class OutboundHost(NetworkModel):
    hostname: str
    port: Port = 443
    protocol: Literal["HTTP", "HTTPS"] = "HTTPS"

    _hostname = field_validator("hostname")(hostname)


class DirectDestination(NetworkModel):
    cidr: str
    port: Port
    protocol: Literal["TCP", "UDP"] = "TCP"

    @field_validator("cidr")
    @classmethod
    def canonical_network(cls, value):
        return str(ipaddress.ip_network(value, strict=True))


class IngressConfiguration(NetworkModel):
    enabled: bool = False
    gateway: str = ""
    hostname: str = ""
    path: str = "/"
    path_type: Literal["Exact", "PathPrefix"] = "PathPrefix"
    exposure: Literal["internal", "external"] = "internal"
    certificate: str = ""
    https_redirect: bool = True
    timeout_seconds: int = Field(default=30, strict=True, ge=1, le=300)
    max_request_bytes: int | None = Field(default=1048576, strict=True, ge=1024, le=10485760)
    streaming: bool = False

    @field_validator("hostname")
    @classmethod
    def canonical_hostname(cls, value):
        return hostname(value) if value else value

    @field_validator("path")
    @classmethod
    def canonical_path(cls, value):
        # Avoid ambiguous normalization and controller-specific matching differences.
        if (
            not re.fullmatch(r"/[A-Za-z0-9_~./-]*", value)
            or any(part in {".", ".."} for part in value.split("/"))
            or "//" in value
        ):
            raise ValueError("path must be an unambiguous absolute URL path")
        return value

    @model_validator(mode="after")
    def complete(self):
        if self.enabled and not all((self.gateway, self.hostname, self.certificate)):
            raise ValueError("enabled ingress requires gateway, hostname and certificate")
        if self.streaming and self.max_request_bytes is not None:
            raise ValueError("streaming and WebSocket routes require request buffering disabled")
        return self


class EgressConfiguration(NetworkModel):
    enabled: bool = False
    hosts: tuple[OutboundHost, ...] = Field(default=(), max_length=64)
    direct: tuple[DirectDestination, ...] = Field(default=(), max_length=64)

    @model_validator(mode="after")
    def unique_rules(self):
        for rules in (self.hosts, self.direct):
            if len(set(rules)) != len(rules):
                raise ValueError("duplicate outbound rules")
        return self


class NetworkingConfiguration(NetworkModel):
    ingress: IngressConfiguration = Field(default_factory=IngressConfiguration)
    egress: EgressConfiguration = Field(default_factory=EgressConfiguration)

    @property
    def revision(self):
        return _fingerprint(self.model_dump(mode="json"))


class GatewayBinding(NetworkModel):
    name: Name
    namespace: Name
    mode: Literal["managed", "existing"] = "managed"
    environment: Literal["test", "prod"]
    listener: Name = "https"
    http_listener: Name = "http"
    exposure: Literal["internal", "external"] = "internal"
    hostname_suffixes: tuple[str, ...] = Field(min_length=1)
    # Alias -> operator-owned Secret name; keys alone are visible in project forms.
    certificates: dict[str, Name] = Field(default_factory=dict)
    pod_selector: dict[str, str] = Field(min_length=1)
    dataplane_namespace: Name
    probe_address: str = ""
    probe_port: Port = 443

    @field_validator("hostname_suffixes")
    @classmethod
    def canonical_suffixes(cls, values):
        return tuple(hostname(v) for v in values)


class ReleaseNetworkPolicy(NetworkModel):
    project_id: str = Field(min_length=1)
    environment: Literal["test", "prod"]
    gateways: tuple[str, ...] = ()
    outbound_hosts: tuple[OutboundHost, ...] = ()
    direct_destinations: tuple[DirectDestination, ...] = ()


class NetworkingPolicy(NetworkModel):
    """Only installation settings may supply this authority envelope."""

    enabled: bool = False
    ingress_available: bool = False
    egress_available: bool = False
    gateways: dict[str, GatewayBinding] = Field(default_factory=dict)
    releases: dict[str, ReleaseNetworkPolicy] = Field(default_factory=dict)
    # Cluster/node/service networks are added to these exclusions by installation.
    forbidden_cidrs: tuple[str, ...] = (
        "0.0.0.0/8",
        "10.0.0.0/8",
        "100.64.0.0/10",
        "127.0.0.0/8",
        "169.254.0.0/16",
        "172.16.0.0/12",
        "192.0.0.0/24",
        "192.0.2.0/24",
        "192.168.0.0/16",
        "198.18.0.0/15",
        "198.51.100.0/24",
        "203.0.113.0/24",
        "224.0.0.0/4",
        "240.0.0.0/4",
        "::/128",
        "::1/128",
        "::ffff:0:0/96",
        "64:ff9b::/96",
        "100::/64",
        "2001:db8::/32",
        "fc00::/7",
        "fe80::/10",
        "ff00::/8",
    )
    proxy_image: str = ""

    @field_validator("forbidden_cidrs")
    @classmethod
    def canonical_cidrs(cls, values):
        return tuple(str(ipaddress.ip_network(v, strict=True)) for v in values)

    @model_validator(mode="after")
    def coherent(self):
        if self.egress_available and not re.fullmatch(
            r"[^\s@]+@sha256:[a-f0-9]{64}", self.proxy_image
        ):
            raise ValueError("egress requires a digest-pinned proxy image")
        for identity, release in self.releases.items():
            if not re.fullmatch(r"[a-z0-9-]+/[a-z0-9-]+", identity):
                raise ValueError("installed release identity must be namespace/release")
            for alias in release.gateways:
                if (
                    alias not in self.gateways
                    or self.gateways[alias].environment != release.environment
                ):
                    raise ValueError("gateway binding crosses environment authority")
        return self

    @property
    def revision(self):
        return _fingerprint(self.model_dump(mode="json"))

    def validate_configuration(self, configuration, *, identity, project_id, environment):
        configuration = NetworkingConfiguration.model_validate(configuration)
        if not self.enabled:
            raise ValueError("networking is not installed")
        allowed = self.releases.get(identity)
        if not allowed or (allowed.project_id, allowed.environment) != (project_id, environment):
            raise ValueError("release has no installed networking authority")
        ingress, egress = configuration.ingress, configuration.egress
        if ingress.enabled:
            if not self.ingress_available or ingress.gateway not in allowed.gateways:
                raise ValueError("gateway is outside installed release authority")
            gateway = self.gateways[ingress.gateway]
            if ingress.exposure != gateway.exposure:
                raise ValueError("exposure differs from the installed listener")
            if not any(
                ingress.hostname == s or ingress.hostname.endswith("." + s)
                for s in gateway.hostname_suffixes
            ):
                raise ValueError("hostname is outside approved domains")
            if ingress.certificate not in gateway.certificates:
                raise ValueError("certificate reference is not approved")
        if egress.enabled:
            if not self.egress_available:
                raise ValueError("egress is not installed")
            if not set(egress.hosts).issubset(allowed.outbound_hosts):
                raise ValueError("outbound hostname or port is not approved")
            for rule in egress.direct:
                network = ipaddress.ip_network(rule.cidr)
                if not any(
                    rule.protocol == entry.protocol
                    and rule.port == entry.port
                    and network.version == ipaddress.ip_network(entry.cidr).version
                    and network.subnet_of(ipaddress.ip_network(entry.cidr))
                    for entry in allowed.direct_destinations
                ):
                    raise ValueError("direct destination is not approved")
        return configuration


def routes_overlap(first: IngressConfiguration, second: IngressConfiguration) -> bool:
    if not first.enabled or not second.enabled or first.hostname != second.hostname:
        return False
    if first.path == second.path:
        return True
    return any(
        prefix.path_type == "PathPrefix"
        and (other.path.rstrip("/") + "/").startswith(prefix.path.rstrip("/") + "/")
        for prefix, other in ((first, second), (second, first))
    )


def proxy_environment(configuration: NetworkingConfiguration, namespace: str, release: str):
    if not configuration.egress.enabled:
        return []
    url = f"http://{release}-egress.{namespace}.svc:3128"
    return [
        {"name": name, "value": url}
        for name in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY")
    ]
