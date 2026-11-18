"""Networking authority, input ambiguity, and cross-release routing regressions."""

import pytest

from goblin_king.pipeline_networking import (
    IngressConfiguration,
    NetworkingConfiguration,
    NetworkingPolicy,
    hostname,
    routes_overlap,
)


def policy():
    return NetworkingPolicy.model_validate(
        {
            "enabled": True,
            "ingress_available": True,
            "egress_available": True,
            "proxy_image": "example/squid@sha256:" + "a" * 64,
            "gateways": {
                "test": {
                    "name": "test",
                    "namespace": "gateways",
                    "environment": "test",
                    "hostname_suffixes": ["apps.example.com"],
                    "certificates": {"test": "test-tls"},
                    "pod_selector": {"gateway": "test"},
                    "dataplane_namespace": "gateways",
                }
            },
            "releases": {
                "app-test/app": {
                    "project_id": "fixture",
                    "environment": "test",
                    "gateways": ["test"],
                    "outbound_hosts": [{"hostname": "api.example.com"}],
                    "direct_destinations": [{"cidr": "203.0.113.8/32", "port": 5432}],
                }
            },
        }
    )


def configuration():
    return {
        "ingress": {
            "enabled": True,
            "gateway": "test",
            "hostname": "one.apps.example.com",
            "certificate": "test",
        },
        "egress": {"enabled": True, "hosts": [{"hostname": "api.example.com"}]},
    }


def validate(value, **kwargs):
    return policy().validate_configuration(
        value,
        **(
            {
                "identity": "app-test/app",
                "project_id": "fixture",
                "environment": "test",
            }
            | kwargs
        ),
    )


def test_default_networking_does_not_enable_exposure():
    value = NetworkingConfiguration()
    assert not value.ingress.enabled and not value.egress.enabled
    assert not NetworkingPolicy().enabled


@pytest.mark.parametrize(
    "value",
    [
        "*.example.com",
        "127.0.0.1",
        "::1",
        "api",
        "https://x.com",
        "x.com\nhttp_access allow all",
        "-x.com",
        "x..com",
        "x.com:443",
    ],
)
def test_hostname_rejects_literals_injection_and_ambiguous_names(value):
    with pytest.raises(ValueError):
        hostname(value)


def test_canonical_names_and_stable_revision():
    value = configuration()
    value["ingress"]["hostname"] = "ONE.APPS.EXAMPLE.COM."
    assert validate(value).revision == validate(configuration()).revision


@pytest.mark.parametrize(
    "changes",
    [
        {"hostname": "apps.example.com.attacker.com"},
        {"gateway": "production"},
        {"exposure": "external"},
        {"certificate": "other-project-key"},
        {"path": "/../admin"},
        {"path": "/%2e%2e/admin"},
        {"path": "//admin"},
        {"path": "/a?x=b"},
        {"annotations": {"arbitrary": "true"}},
    ],
)
def test_ingress_cannot_expand_installation_authority(changes):
    value = configuration()
    value["ingress"].update(changes)
    with pytest.raises(ValueError):
        validate(value)


@pytest.mark.parametrize(
    "rules",
    [
        {"hosts": [{"hostname": "unapproved.example.com"}]},
        {"hosts": [{"hostname": "api.example.com", "port": 22}]},
        {"direct": [{"cidr": "0.0.0.0/0", "port": 5432}]},
        {"direct": [{"cidr": "203.0.113.8/32", "port": 5432, "protocol": "UDP"}]},
    ],
)
def test_egress_cannot_expand_destination_authority(rules):
    value = configuration()
    value["egress"].update(rules)
    with pytest.raises(ValueError):
        validate(value)


@pytest.mark.parametrize(
    "scope", [{"project_id": "other"}, {"environment": "production"}, {"identity": "other/app"}]
)
def test_release_authority_is_project_and_environment_scoped(scope):
    with pytest.raises(ValueError):
        validate(configuration(), **scope)


def test_direct_rules_require_explicit_approval():
    value = configuration()
    value["egress"]["direct"] = [{"cidr": "203.0.113.8/32", "port": 5432}]
    assert validate(value).egress.direct[0].port == 5432


@pytest.mark.parametrize(
    "first,second,overlap",
    [
        (("/", "PathPrefix"), ("/api", "Exact"), True),
        (("/api", "PathPrefix"), ("/api/v1", "Exact"), True),
        (("/api", "PathPrefix"), ("/apis", "Exact"), False),
        (("/api", "Exact"), ("/api/v1", "PathPrefix"), False),
        (("/api", "Exact"), ("/api", "Exact"), True),
    ],
)
def test_route_conflicts_follow_path_segment_boundaries(first, second, overlap):
    base = configuration()["ingress"]
    a = IngressConfiguration(**(base | {"path": first[0], "path_type": first[1]}))
    b = IngressConfiguration(**(base | {"path": second[0], "path_type": second[1]}))
    assert routes_overlap(a, b) is overlap
    assert routes_overlap(b, a) is overlap
