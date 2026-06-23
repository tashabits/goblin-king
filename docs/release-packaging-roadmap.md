# Release Packaging Roadmap

This roadmap describes future release, packaging, install, and lightweight
docs-site work. It is a planning document, not an implemented feature.

Core rule:

```text
Releases should be easy to install, easy to verify, and honest about alpha
boundaries.
```

## Goals

- Plan a repeatable release checklist for Python packages, Docker images, Helm
  charts, docs, and local proof.
- Keep the generic portable worker backbone proof ahead of profile-specific proof.
- Treat RAG as the first profile/use case layered onto that backbone, not as a
  separate runtime or deployment model.
- Make future installs friendlier for trusted self-hosted adopters.
- Keep release notes tied to validation, admin proof, and adopter smoke proof.
- Leave security signing and provenance as explicit follow-up until implemented.

## Additional Guardrails

- Do not claim public PyPI, image registry, chart registry, or docs-site support
  exists before it is implemented.
- Published artifacts must preserve the alpha safety posture.
- Packaging must not imply untrusted third-party container execution support.
- Packaging must not weaken the validation gate.
- Goblin task containers must not receive the Docker socket.
- Docker images and Helm charts should remain configurable for self-hosted use.
- JupyterHub notebook function, notebook ASGI service, Goblin Directory browser UI, and
  JupyterLab picker paths must be documented as compatible optional surfaces over the
  same backbone, not as competing release tracks.

## Release Proof Slice: Portable Worker Backbone

The current release proof should establish the generic backbone before any profile
story:

```bash
make worker-backbone-proof
```

That target is intentionally static: it validates the adopter project config, lists the
resolved project goblins, and renders the Helm chart with host-project values. Runtime
proof can add Docker/Redis/Kubernetes checks when the change touches runtime behavior.

RAG is the first planned profile/use case for this backbone. The profile proof target
checks that a profile-shaped project still fits the same project config, image-map,
resource-policy, and Helm render path:

```bash
make rag-profile-proof
```

The target defaults to the bundled portable worker-backbone fixture and its local RAG
first-use-case worker. A private RAG profile can override `RAG_PROFILE_PROJECT`,
`RAG_PROFILE_HELM_VALUES`, `RAG_PROFILE_KIND`, and `RAG_PROFILE_RESOURCE_POLICIES`.

This proof slice does not publish packages, container images, Helm charts, or docs. It
only gives release candidates a repeatable local evidence path.

## Release Phase 1: Versioning And Release Checklist

Plan a stricter release checklist for tags, changelog discipline, compatibility
matrix updates, local CI, admin proof, adopter smoke proof, Docker proof, Helm
render proof, and release notes.

The checklist should explain what makes a release suitable for public alpha
testing versus internal-only validation.

## Release Phase 2: Python Package Publishing

Plan future Python package publication. Future install examples may include
`pipx install goblin-king` or `uv tool install goblin-king`, but those must stay
clearly marked as planned until package publishing exists.

Packaging should keep Python as the control-plane implementation, not the
required worker runtime.

## Release Phase 3: Docker Image Publishing

Plan future published Docker images for the API, scheduler, admin UI, and sample
support services where appropriate.

The plan should include image tags, compatibility with project worker images,
local smoke proof, and clear separation from goblin task containers.

## Release Phase 4: Helm Chart Packaging

Plan future versioned Helm chart packaging for optional Kubernetes deployments.

The chart plan should include values compatibility, upgrade notes, admin ingress
behavior, storage settings, Redis settings, validation gate expectations, and
render/smoke proof.

## Release Phase 5: Minimal Docs Site

Plan a minimal docs site that organizes user guides, adopter guides, roadmap
docs, API notes, security posture, and release notes without replacing the
README as the repository entrypoint.

The docs site should avoid implying planned features are available today.

## Release Phase 6: Security And Provenance Follow-Up

Plan later signing and provenance work, such as signed release artifacts, signed
container images, SBOMs, vulnerability scanning, and chart provenance.

These should remain follow-up items until implemented and proven.

## Non-Goals

- No packaging, publishing, or docs-site implementation in this roadmap.
- No claim that public PyPI publication exists today.
- No claim that signed artifacts or images exist today.
- No production multi-tenant hardening claim.
- No untrusted third-party container execution.
- No federation, remote runners, geographic placement, or distributed
  experiment orchestration.

## Acceptance Criteria

- Future release work has a clear packaging and proof sequence.
- `make worker-backbone-proof` proves the generic portable worker backbone before
  profile checks.
- `make rag-profile-proof` documents and exercises the first profile/use-case shape
  without creating a separate runtime model.
- Planned install commands are clearly marked as future examples.
- Release docs preserve the project-adoptable alpha safety posture.
- Docker image, Helm chart, and Python package plans remain self-hosted and
  validation-aware.
- Security and provenance work is tracked without being implied complete.
