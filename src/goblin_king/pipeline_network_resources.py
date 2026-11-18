"""Deterministic, namespace-bound gateway and outbound proxy resources."""

from __future__ import annotations

import hashlib
import ipaddress

from goblin_king.pipeline_networking import NetworkingConfiguration, NetworkingPolicy


def squid_configuration(configuration: NetworkingConfiguration, policy: NetworkingPolicy) -> str:
    """Emit exact destination/port pairs, with no caching or request content logging."""
    lines = [
        "http_port 3128",
        "visible_hostname goblin-king-egress",
        "pid_filename none",
        "cache_log /dev/stderr",
        "cache_store_log none",
        "cache deny all",
        "cache_mem 0 MB",
        "max_filedescriptors 1024",
        "pinger_enable off",
        "access_log none",
        "shutdown_lifetime 1 seconds",
        "connect_timeout 10 seconds",
        "request_timeout 30 seconds",
        "forwarded_for delete",
        "acl connect method CONNECT",
        "acl http proto HTTP",
        "http_access deny manager",
    ]
    if policy.forbidden_cidrs:
        lines += [
            "acl forbidden dst "
            + " ".join(
                cidr
                for cidr in policy.forbidden_cidrs
                if not (
                    ipaddress.ip_network(cidr).version == 6
                    and ipaddress.ip_network(cidr).network_address.ipv4_mapped is not None
                )
            ),
            "http_access deny forbidden",
        ]
    if configuration.egress.enabled:
        for index, destination in enumerate(configuration.egress.hosts):
            methods = "connect" if destination.protocol == "HTTPS" else "!connect http"
            lines += [
                f"acl host{index} dstdomain -n {destination.hostname}",
                f"acl port{index} port {destination.port}",
                f"http_access allow {methods} host{index} port{index}",
            ]
    return "\n".join([*lines, "http_access deny all", ""])


def dns_rule():
    return {
        "to": [
            {
                "namespaceSelector": {
                    "matchLabels": {"kubernetes.io/metadata.name": "kube-system"}
                },
                "podSelector": {"matchLabels": {"k8s-app": "kube-dns"}},
            }
        ],
        "ports": [{"protocol": protocol, "port": 53} for protocol in ("UDP", "TCP")],
    }


def application_selector(release):
    return {
        "matchLabels": {"app.kubernetes.io/instance": release},
        "matchExpressions": [
            {
                "key": "app.kubernetes.io/component",
                "operator": "In",
                "values": ["service", "background", "schedule"],
            }
        ],
    }


def render_networking(
    configuration,
    policy,
    *,
    namespace,
    release,
    project_id,
    environment,
    operation_id,
    active,
    port,
):
    """Render only the approved configuration; callers cannot supply arbitrary resources."""
    configuration = policy.validate_configuration(
        configuration,
        identity=namespace + "/" + release,
        project_id=project_id,
        environment=environment,
    )
    resources = []
    labels = {"app.kubernetes.io/instance": release, "goblin-king.io/networking": "true"}

    def resource(kind, name, spec, api="v1", target_namespace=namespace, **fields):
        item = {
            "apiVersion": api,
            "kind": kind,
            "metadata": {
                "name": name,
                "namespace": target_namespace,
                "labels": labels,
                "annotations": {
                    "goblin-king.io/operation": operation_id,
                    "goblin-king.io/network-revision": configuration.revision,
                },
            },
            **fields,
        }
        if spec is not None:
            item["spec"] = spec
        resources.append(item)
        return item

    ingress, egress = configuration.ingress, configuration.egress
    app_selector = application_selector(release)
    allowed_ingress, allowed_egress = [], []
    if ingress.enabled and active:
        gateway = policy.gateways[ingress.gateway]
        allowed_ingress.append(
            {
                "from": [
                    {
                        "namespaceSelector": {
                            "matchLabels": {
                                "kubernetes.io/metadata.name": gateway.dataplane_namespace
                            }
                        },
                        "podSelector": {"matchLabels": gateway.pod_selector},
                    }
                ],
                "ports": [{"protocol": "TCP", "port": port}],
            }
        )
        parent = {"name": gateway.name, "namespace": gateway.namespace}
        route = resource(
            "HTTPRoute",
            release + "-ingress",
            {
                "parentRefs": [parent | {"sectionName": gateway.listener}],
                "hostnames": [ingress.hostname],
                "rules": [
                    {
                        "matches": [{"path": {"type": ingress.path_type, "value": ingress.path}}],
                        "backendRefs": [{"name": release, "port": port}],
                        "timeouts": {"request": f"{ingress.timeout_seconds}s"},
                    }
                ],
            },
            "gateway.networking.k8s.io/v1",
        )
        resource(
            "BackendTrafficPolicy",
            release + "-ingress-limits",
            {
                "targetRefs": [
                    {
                        "group": "gateway.networking.k8s.io",
                        "kind": "HTTPRoute",
                        "name": route["metadata"]["name"],
                    }
                ],
                **(
                    {"requestBuffer": {"limit": str(ingress.max_request_bytes)}}
                    if ingress.max_request_bytes is not None
                    else {}
                ),
            },
            "gateway.envoyproxy.io/v1alpha1",
        )
        if ingress.https_redirect:
            resource(
                "HTTPRoute",
                release + "-redirect",
                {
                    "parentRefs": [parent | {"sectionName": gateway.http_listener}],
                    "hostnames": [ingress.hostname],
                    "rules": [
                        {
                            "matches": [
                                {"path": {"type": ingress.path_type, "value": ingress.path}}
                            ],
                            "filters": [
                                {
                                    "type": "RequestRedirect",
                                    "requestRedirect": {"scheme": "https", "statusCode": 301},
                                }
                            ],
                        }
                    ],
                },
                "gateway.networking.k8s.io/v1",
            )

    proxy_labels = labels | {"app.kubernetes.io/component": "egress"}
    proxy_selector = {"matchLabels": proxy_labels}
    if egress.enabled and active:
        name = release + "-egress"
        config = squid_configuration(configuration, policy)
        digest = hashlib.sha256(config.encode()).hexdigest()
        resource("ConfigMap", name, None, data={"squid.conf": config})
        resource(
            "Service",
            name,
            {
                "type": "ClusterIP",
                "selector": proxy_labels,
                "ports": [{"name": "proxy", "port": 3128, "targetPort": 3128}],
            },
        )
        resource(
            "Deployment",
            name,
            {
                "replicas": 1,
                "strategy": {"type": "Recreate"},
                "selector": proxy_selector,
                "template": {
                    "metadata": {
                        "labels": proxy_labels,
                        "annotations": {"goblin-king.io/configuration": digest},
                    },
                    "spec": {
                        "automountServiceAccountToken": False,
                        "securityContext": {
                            "runAsUser": 13,
                            "runAsGroup": 13,
                            "runAsNonRoot": True,
                            "seccompProfile": {"type": "RuntimeDefault"},
                        },
                        "containers": [
                            {
                                "name": "proxy",
                                "image": policy.proxy_image,
                                "command": [
                                    "squid",
                                    "-N",
                                    "-d",
                                    "1",
                                    "-f",
                                    "/etc/squid/squid.conf",
                                ],
                                "ports": [{"containerPort": 3128}],
                                "securityContext": {
                                    "allowPrivilegeEscalation": False,
                                    "readOnlyRootFilesystem": True,
                                    "capabilities": {"drop": ["ALL"]},
                                },
                                "resources": {
                                    "requests": {"cpu": "50m", "memory": "64Mi"},
                                    "limits": {"cpu": "500m", "memory": "256Mi"},
                                },
                                "readinessProbe": {"tcpSocket": {"port": 3128}, "periodSeconds": 2},
                                "livenessProbe": {"tcpSocket": {"port": 3128}, "periodSeconds": 10},
                                "volumeMounts": [
                                    {"name": "config", "mountPath": "/etc/squid", "readOnly": True},
                                    {"name": "tmp", "mountPath": "/tmp"},
                                ],
                            }
                        ],
                        "volumes": [
                            {"name": "config", "configMap": {"name": name}},
                            {"name": "tmp", "emptyDir": {"sizeLimit": "64Mi"}},
                        ],
                    },
                },
            },
            "apps/v1",
        )
        allowed_egress.append(
            {"to": [{"podSelector": proxy_selector}], "ports": [{"protocol": "TCP", "port": 3128}]}
        )
        for destination in egress.direct:
            allowed_egress.append(
                {
                    "to": [{"ipBlock": {"cidr": destination.cidr}}],
                    "ports": [{"protocol": destination.protocol, "port": destination.port}],
                }
            )

    # These policies add only approved paths to the installation's default-deny policy.
    resource(
        "NetworkPolicy",
        release + "-networking",
        {
            "podSelector": app_selector,
            "policyTypes": ["Ingress", "Egress"],
            "ingress": allowed_ingress,
            "egress": allowed_egress,
        },
        "networking.k8s.io/v1",
    )
    upstream_ports = sorted({h.port for h in egress.hosts}) if egress.enabled and active else []
    proxy_egress = [dns_rule()]
    if upstream_ports:
        for version, default in ((4, "0.0.0.0/0"), (6, "::/0")):
            excluded = list(
                ipaddress.collapse_addresses(
                    ipaddress.ip_network(cidr)
                    for cidr in policy.forbidden_cidrs
                    if ipaddress.ip_network(cidr).version == version
                    and not (
                        version == 6
                        and ipaddress.ip_network(cidr).network_address.ipv4_mapped is not None
                    )
                )
            )
            proxy_egress.append(
                {
                    "to": [{"ipBlock": {"cidr": default, "except": [str(n) for n in excluded]}}],
                    "ports": [{"protocol": "TCP", "port": value} for value in upstream_ports],
                }
            )
    resource(
        "NetworkPolicy",
        release + "-egress",
        {
            "podSelector": proxy_selector,
            "policyTypes": ["Ingress", "Egress"],
            "ingress": [
                {
                    "from": [{"podSelector": app_selector}],
                    "ports": [{"protocol": "TCP", "port": 3128}],
                }
            ]
            if active
            else [],
            "egress": proxy_egress if active else [],
        },
        "networking.k8s.io/v1",
    )
    return resources
