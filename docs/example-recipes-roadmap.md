# Example Recipes Roadmap

This roadmap describes future practical recipes beyond hello-world examples. It
is a planning document, not an implemented feature.

Mental model:

```text
Recipes show realistic patterns without smuggling in production secrets or
unsafe assumptions.
```

## Goals

- Plan practical examples that help adopters recognize their own job shapes.
- Show success, artifact output, controlled failure, validation, and admin proof.
- Keep every recipe container-first and project-config driven.
- Avoid examples that depend on real credentials or uncontrolled external
  services.

## Additional Guardrails

- No production credentials in recipes.
- No required real external service dependency unless mocked or optional.
- No scraping, abuse-oriented, or credential-harvesting examples.
- No untrusted container execution.
- Generated or copied recipe goblins must validate before scheduling.
- Python may be one recipe language, but not the required worker runtime.

## Recipe Phase 1: Recipe Structure And Standards

Plan a standard recipe shape: problem statement, project config snippet,
worker folder layout, sample input, validation proof, run proof, artifact proof
when relevant, admin proof, cleanup, and security notes.

Recipes should use the existing `GoblinProject` model and should not introduce a
new example configuration format.

## Recipe Phase 2: Local Utility Recipes

Plan local utility recipes that do not require network access or credentials,
such as CSV importer, static site build task, image thumbnailer, and legacy Java
jar wrapper.

Each recipe should explain inputs, outputs, result envelope, artifacts, resource
policy, and validation.

## Recipe Phase 3: Artifact-Producing Recipes

Plan artifact-focused recipes such as PDF invoice renderer, nightly report
builder, and artifact-producing report generator.

These recipes should demonstrate artifact metadata, safe artifact paths, size
limits, admin artifact inspection, and cleanup expectations.

## Recipe Phase 4: Network-Aware Trusted Recipes

Plan network-aware recipes such as HTTP health checker and network probe, but
only for trusted local or mocked endpoints.

These recipes should clearly mark network access as optional and policy-bound,
and they should not encourage scraping or abuse.

## Recipe Phase 5: Admin And CLI Proof

Plan proof steps that show each recipe through validation, CLI submission,
scheduler execution, admin run detail, event visibility, and artifact inspection
where applicable.

Proof should make controlled failures readable rather than treating every failed
run as a broken recipe.

## Recipe Phase 6: Closeout And Docs

Close out recipe work by updating README, examples index, adopter guide,
screenshots, proof table, and recipe navigation.

Closeout proof should show at least one local utility recipe, one
artifact-producing recipe, one controlled-failure recipe, and one optional
network-aware trusted recipe.

## Non-Goals

- No production credentials.
- No required real external service dependencies.
- No scraping or abuse-oriented examples.
- No untrusted third-party container execution.
- No production/multi-tenant hardening claim.
- No federation, remote runners, geographic placement, or distributed
  experiment orchestration.

## Acceptance Criteria

- Future recipes are practical, safe, and project-config driven.
- Every recipe goblin remains a contract-compliant Docker/OCI container.
- Every recipe validates before scheduling.
- Recipe proof includes CLI and admin inspection.
- Recipe docs do not imply the planned examples exist before implementation.
