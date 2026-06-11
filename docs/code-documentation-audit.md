# Code Documentation Audit

This document defines a future audit for repository comments, docstrings, and
file-level documentation. It is an audit plan and findings template, not a
completed code-comment cleanup.

Core rule:

```text
Leave files alone when they already meet the documentation standard.
```

## Audit Standard

Use the contribution standards in `docs/CONTRIBUTING.md` as the source of truth:

- New public modules should start with a concise file-level comment describing
  purpose and ownership.
- Public functions, runtime entrypoints, goblin contracts, persistence
  boundaries, and non-obvious helpers should have function-level comments
  explaining purpose, inputs, outputs, and important failure behavior.
- Comments should explain contract, intent, invariants, edge cases, or
  operational consequences.
- Comments should not restate obvious code.

## File Classifications

Each reviewed file should receive one classification:

| Classification | Meaning |
| --- | --- |
| `sufficient` | Already meets the standard and should not be changed. |
| `needs file comment` | Lacks purpose or ownership context. |
| `needs function/helper comments` | Public or non-obvious logic is underexplained. |
| `over-commented` | Comments restate code or obscure intent. |
| `generated/vendor-like` | No extra hand-authored comments expected. |
| `documentation-only` | Markdown or docs content already serves as documentation. |

## Audit Procedure

Start from clean, updated `main` on a dedicated audit branch. Inventory tracked
files with `rg --files`, excluding screenshots, binary images, lockfiles, and
obvious generated artifacts from manual comment requirements.

Review by subsystem:

- Python package under `src/`.
- Python tests and fixtures.
- React admin TypeScript, TSX, and CSS.
- Helm templates and values.
- Dockerfiles, shell scripts, Compose files, and Nginx config.
- Example workers in Python, Node, Go, Rust, C/WASI, Java, .NET, Ruby, PHP, and
  shell.
- JSON configs, project examples, schemas, and image maps.
- Makefile and release/config metadata.

Record findings here without editing source comments during the audit pass.

For files marked `sufficient`, record that they should be left unchanged. For
files with gaps, record only the missing comment category and the recommended
future PR slice.

## Findings Template

Use this table shape while auditing:

| Subsystem | File Or Pattern | Classification | Finding | Recommended Follow-Up |
| --- | --- | --- | --- | --- |
| Backend | `src/goblin_king/example.py` | `sufficient` | Existing file/module comments explain the public contract. | Leave unchanged. |
| Backend | `src/goblin_king/example_helpers.py` | `needs function/helper comments` | Non-obvious helper behavior is underexplained. | Add focused helper comments in backend cleanup PR. |

## Follow-Up Implementation Slices

After the audit identifies real gaps, add comments in small behavior-preserving
PRs:

- Backend contracts, runtime, store, and scheduler.
- API, CLI, auth, and admin helper boundaries.
- React admin components, hooks, and API client helpers.
- Workers, examples, Dockerfiles, and scripts.
- Helm, Compose, config templates, and Makefile targets.
- Tests and fixtures.

Each follow-up PR should touch only files that need comment work. Files already
classified as `sufficient` should not be rewritten.

## Non-Goals

- No source-code comment changes in the audit pass.
- No runtime, API, CLI, admin UI, example, or config behavior changes.
- No formatter or generator runs that rewrite tracked files.
- No broad comment churn in files that already meet the standard.
- No noisy comments that restate obvious code.

## Acceptance Criteria

- This audit doc exists and is linked from README.
- The audit standard references `docs/CONTRIBUTING.md`.
- The doc states that files already meeting the standard should not be redone.
- The doc separates audit findings from future comment implementation.
- The follow-up implementation slices are small enough to review.
- No source code or behavior changes are included in the audit PR.
