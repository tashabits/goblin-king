"""Optional networking authority and certificate issuance stay installation-owned."""

import json
import shutil
import subprocess

import pytest
import yaml

from tests.test_pipeline_networking import policy

pytestmark = pytest.mark.skipif(shutil.which("helm") is None, reason="Helm is not installed")


def render(tmp_path, values, *arguments):
    path = tmp_path / "values.json"
    path.write_text(json.dumps(values))
    return subprocess.run(
        [
            "helm",
            "template",
            "network-test",
            "charts/pipeline-networking",
            "-f",
            str(path),
            *arguments,
        ],
        capture_output=True,
        text=True,
    )


def values():
    return {
        "policy": policy().model_dump(mode="json"),
        "controlNamespace": "control",
        "apiServiceAccount": "api",
        "apiServerCIDRs": ["10.96.0.1/32"],
        "approvedIssuers": {"local": {"kind": "Issuer", "name": "approved-ca"}},
        "certificateRequests": [
            {
                "name": "app-tls",
                "gateway": "test",
                "certificate": "test",
                "issuer": "local",
                "dnsNames": ["one.apps.example.com"],
            }
        ],
    }


def test_disabled_networking_creates_no_optional_authority(tmp_path):
    configuration = values()
    configuration["policy"]["enabled"] = False
    result = render(tmp_path, configuration)
    assert result.returncode == 0, result.stderr
    assert not any(yaml.safe_load_all(result.stdout))


def test_certificate_issuance_requires_installed_api_and_approved_aliases(tmp_path):
    configuration = values()
    result = render(tmp_path, configuration)
    assert result.returncode != 0 and "existing cert-manager" in result.stderr
    arguments = ("--api-versions", "cert-manager.io/v1/Certificate")
    result = render(tmp_path, configuration, *arguments)
    assert result.returncode == 0, result.stderr
    cert = next(
        item for item in yaml.safe_load_all(result.stdout) if item and item["kind"] == "Certificate"
    )
    assert cert["spec"]["secretName"] == "test-tls"
    assert cert["spec"]["issuerRef"] == {
        "group": "cert-manager.io",
        "kind": "Issuer",
        "name": "approved-ca",
    }
    configuration["certificateRequests"][0]["issuer"] = "unapproved"
    result = render(tmp_path, configuration, *arguments)
    assert result.returncode != 0 and "approved issuer" in result.stderr


@pytest.mark.parametrize(
    "change,message",
    [
        ({"dnsNames": ["*.apps.example.com"]}, "exact canonical"),
        ({"dnsNames": ["other.example.com"]}, "outside approved"),
        ({"certificate": "foreign-secret"}, "approved certificate"),
    ],
)
def test_certificate_requests_cannot_expand_gateway_authority(tmp_path, change, message):
    configuration = values()
    configuration["certificateRequests"][0].update(change)
    result = render(tmp_path, configuration, "--api-versions", "cert-manager.io/v1/Certificate")
    assert result.returncode != 0 and message in result.stderr


def test_existing_gateway_certificates_are_never_adopted(tmp_path):
    configuration = values()
    configuration["policy"]["gateways"]["test"]["mode"] = "existing"
    result = render(tmp_path, configuration, "--api-versions", "cert-manager.io/v1/Certificate")
    assert result.returncode != 0 and "externally owned" in result.stderr
