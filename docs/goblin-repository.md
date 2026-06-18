# Goblin Repository Contract

The Goblin Repository is planned as an optional service for sharing approved
notebook-authored goblins. The first durable contract is a two-part model:

- `RepositoryEntryRecord` represents the project-scoped catalog name users search
  and call.
- `RepositoryVersionRecord` represents one submitted source bundle, runner image,
  validation proof, approval state, and publication state.

Repository names are unique within a project while active. Source or runner-image
changes must create the next draft version. Published versions are immutable; later
service and notebook APIs should resolve by repository entry name plus either a
specific version or the latest approved published version.

The review flow is:

```text
draft -> validated -> pending_review -> approved -> published
```

`rejected` and `retired` are side states. Validation is still mandatory before
runtime use, and approval is a sharing gate rather than a security certification.
