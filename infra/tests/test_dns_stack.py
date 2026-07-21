# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

from __future__ import annotations

import dataclasses

import aws_cdk as cdk
from aws_cdk import assertions

from cert_ra_infra.stacks._config import EnvConfig, load_env
from cert_ra_infra.stacks.dns import DnsStack


def _synth_cfg(cfg: EnvConfig) -> assertions.Template:
    app = cdk.App()
    env = cdk.Environment(account="111111111111", region=cfg.region)
    stack = DnsStack(app, f"CertRa-DnsStack-{cfg.env}", env=env, env_config=cfg)
    return assertions.Template.from_stack(stack)


def _synth_stack(env_name: str = "staging") -> assertions.Template:
    return _synth_cfg(load_env(env_name))


def test_references_existing_zone_when_id_set() -> None:
    """Both envs set dns_zone_id, so the zone is referenced — not created —
    and the cert validates against that zone id. This decouples the zone
    from the cert lifecycle so a rollback can't destroy it / churn its NS."""
    cfg = load_env("staging")
    assert cfg.dns_zone_id is not None  # guards the premise of this test
    template = _synth_stack("staging")
    template.resource_count_is("AWS::Route53::HostedZone", 0)
    certs = template.find_resources("AWS::CertificateManager::Certificate")
    (cert,) = certs.values()
    dvo = cert["Properties"]["DomainValidationOptions"]
    assert any(o.get("HostedZoneId") == cfg.dns_zone_id for o in dvo)


def test_creates_zone_with_retain_when_no_zone_id() -> None:
    """With no dns_zone_id (e.g. an env's zone not yet captured), DnsStack
    creates the zone — with RETAIN so a failed/rolled-back deploy leaves it
    (and its NS) intact, ready to be captured into dns_zone_id."""
    cfg = dataclasses.replace(load_env("prod"), dns_zone_id=None)
    template = _synth_cfg(cfg)
    template.has_resource_properties(
        "AWS::Route53::HostedZone",
        {"Name": f"{cfg.domain}."},
    )
    template.has_resource("AWS::Route53::HostedZone", {"DeletionPolicy": "Retain"})


def test_certificate_is_created_with_correct_domain() -> None:
    template = _synth_stack("staging")
    template.has_resource_properties(
        "AWS::CertificateManager::Certificate",
        {"DomainName": load_env("staging").domain},
    )


def test_certificate_has_wildcard_san() -> None:
    """Wildcard SAN covers www.<domain> + any other future subdomain
    without needing a re-issue."""
    template = _synth_stack("staging")
    certs = template.find_resources("AWS::CertificateManager::Certificate")
    (cert,) = certs.values()
    sans = cert["Properties"].get("SubjectAlternativeNames", [])
    assert f"*.{load_env('staging').domain}" in sans


def test_caa_record_authorizes_amazon() -> None:
    """certora.com's CAA record set omits Amazon and is inherited by
    subdomains, so ACM can't issue (cert → FAILED) unless we publish a
    subdomain CAA authorizing amazon.com for both issue and issuewild."""
    template = _synth_stack("staging")
    recordsets = template.find_resources("AWS::Route53::RecordSet")
    caa = [r for r in recordsets.values() if r["Properties"].get("Type") == "CAA"]
    assert caa, "expected a CAA record authorizing Amazon"
    # CloudFormation ResourceRecords is a list of plain strings.
    values = caa[0]["Properties"]["ResourceRecords"]
    assert '0 issue "amazon.com"' in values
    assert '0 issuewild "amazon.com"' in values


def test_cert_depends_on_caa_record() -> None:
    """The cert must not be requested before the CAA record exists, or ACM
    sees the restrictive parent CAA and fails issuance."""
    template = _synth_stack("staging")
    certs = template.find_resources("AWS::CertificateManager::Certificate")
    (cert,) = certs.values()
    depends_on = cert.get("DependsOn", [])
    if isinstance(depends_on, str):
        depends_on = [depends_on]
    assert any("Caa" in d for d in depends_on), depends_on


def test_references_env_exports_outputs_without_nameservers() -> None:
    """A referenced zone is already delegated, so there's no NameServers
    output to act on — but the id/domain/cert outputs are still emitted."""
    template = _synth_stack("staging")
    outputs = set(template.find_outputs("*").keys())
    assert {"HostedZoneId", "DomainName", "CertificateArn"}.issubset(outputs)
    assert "NameServers" not in outputs


def test_emits_nameservers_output_when_creating_zone() -> None:
    """When the zone IS created (no dns_zone_id), the NS output reminds the
    operator to delegate from the parent zone at Cloudflare."""
    cfg = dataclasses.replace(load_env("prod"), dns_zone_id=None)
    template = _synth_cfg(cfg)
    outputs = template.find_outputs("NameServers")
    (output,) = outputs.values()
    assert "delegate" in output.get("Description", "").lower()


_NEXT_DOMAIN = "risk-next.example.com"


def _migration_cfg() -> EnvConfig:
    """Prod-shaped config exercising the FIRST migration deploy (zone not
    yet captured), with a same-account parent zone for the delegation."""
    return dataclasses.replace(
        load_env("prod"),
        next_domain=_NEXT_DOMAIN,
        next_dns_parent_zone_id="ZPARENT0000TEST",
        next_dns_parent_zone_name="example.com",
        next_dns_zone_id=None,
    )


def test_migration_builds_next_zone_alongside_old_cert() -> None:
    """With next_domain set, the old cert stays (AppStack still imports it
    until its own cutover deploy) and the new zone + wildcard cert appear."""
    cfg = _migration_cfg()
    template = _synth_cfg(cfg)
    template.has_resource_properties(
        "AWS::Route53::HostedZone", {"Name": f"{_NEXT_DOMAIN}."}
    )
    certs = template.find_resources("AWS::CertificateManager::Certificate")
    domains = {c["Properties"]["DomainName"] for c in certs.values()}
    assert domains == {cfg.domain, _NEXT_DOMAIN}
    next_cert = next(
        c for c in certs.values() if c["Properties"]["DomainName"] == _NEXT_DOMAIN
    )
    sans = next_cert["Properties"].get("SubjectAlternativeNames", [])
    assert f"*.{_NEXT_DOMAIN}" in sans


def test_migration_delegates_ns_into_parent_zone() -> None:
    """The same-account parent zone gets the NS delegation record, and the
    new cert waits on it so DNS validation can resolve."""
    template = _synth_cfg(_migration_cfg())
    recordsets = template.find_resources("AWS::Route53::RecordSet")
    ns = [
        r
        for r in recordsets.values()
        if r["Properties"].get("Type") == "NS"
        and r["Properties"].get("Name") == f"{_NEXT_DOMAIN}."
    ]
    (delegation,) = ns
    assert delegation["Properties"]["HostedZoneId"] == "ZPARENT0000TEST"
    certs = template.find_resources("AWS::CertificateManager::Certificate")
    next_cert = next(
        c for c in certs.values() if c["Properties"]["DomainName"] == _NEXT_DOMAIN
    )
    depends_on = next_cert.get("DependsOn", [])
    if isinstance(depends_on, str):
        depends_on = [depends_on]
    assert any("ParentDelegation" in d for d in depends_on), depends_on


def test_migration_outputs_point_at_next_domain() -> None:
    """Operator-facing outputs (used by upgrade.sh / verify-deploy.sh
    health checks) must follow the ACTIVE domain during a migration."""
    template = _synth_cfg(_migration_cfg())
    outputs = template.find_outputs("DomainName")
    (domain_out,) = outputs.values()
    assert domain_out["Value"] == _NEXT_DOMAIN
    assert "NextHostedZoneId" in template.find_outputs("*")


def test_migration_pins_old_certificate_export() -> None:
    """The old cert ARN's auto-export must survive while the deployed
    AppStack still imports it, or the DnsStack update is rejected."""
    template = _synth_cfg(_migration_cfg())
    exports = [
        o
        for name, o in template.find_outputs("*").items()
        if name.startswith("ExportsOutput") and "Certificate" in name
    ]
    assert exports, "expected a pinned export for the old certificate ARN"
