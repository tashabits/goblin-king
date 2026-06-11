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

## Coverage Audit: 2026-06-10

This coverage audit used `rg --files` as the canonical tracked-file inventory.
The inventory contained 297 tracked files at audit time. Every major repo area
is covered below either by an individual file row or by an explicit file-pattern
row.

Parallel audit note: the runner pool was already at its thread limit, so the
orchestrator reused the one attachable existing runner for the tests/fixtures
slice and completed the remaining subsystem slices locally. The final ledger
below is the orchestrator-integrated result.

### Coverage Summary

| Subsystem | Files Covered | Primary Classification | Notes |
| --- | ---: | --- | --- |
| Backend Python package | 34 | mixed | Module docstrings are broadly present; a few large public modules need more function/helper comments. |
| Tests and fixtures | 32 | mixed | Most tests are self-describing; a few modules need file-level comments. |
| React admin | 19 | mixed | Shared helpers are documented; large panels and test/style entrypoints need file-level context. |
| Helm and deployment config | 15 | mixed | Values and root targets are mostly clear; Helm templates need purpose comments. |
| Examples | 94 | mixed | README-backed examples are strong; several non-Python worker sources need contract comments. |
| Worker folders | 15 | sufficient | Python worker modules already explain their purpose; Dockerfiles are simple and paired with folder identity. |
| Markdown docs | 75 | documentation-only | Docs are themselves documentation; no code-comment work needed. |
| Root/config metadata | 13 | mixed | Policy/config metadata is mostly self-describing or generated-like. |

### Explicit Exclusions

| File Or Pattern | Classification | Reason |
| --- | --- | --- |
| `docs/images/*`, `docs/screenshots/*`, `*.png` | `generated/vendor-like` | Binary screenshot and image assets do not need hand-authored code comments. |
| `admin-ui/package-lock.json` | `generated/vendor-like` | Lockfile is generated dependency metadata. |
| `compatibility/*.json` and version matrix JSON | `generated/vendor-like` | Machine-readable compatibility data is validated through tests/docs rather than inline comments. |
| README-backed example folders | `documentation-only` where applicable | Per-example READMEs already explain intent, build/run shape, and contract behavior. |

### Backend Python Findings

| Subsystem | File Or Pattern | Classification | Finding | Recommended Follow-Up |
| --- | --- | --- | --- | --- |
| Backend Python | `src/goblin_king/__init__.py`, contracts, versions, JSON/metadata helpers | `sufficient` | File-level docstrings identify public surface and helper purpose clearly. | Leave unchanged. |
| Backend Python | `src/goblin_king/api_*.py` support modules | `sufficient` | Split API helper modules have concise module docstrings and narrow responsibilities. | Leave unchanged. |
| Backend Python | `src/goblin_king/store_rows.py`, `store_schema.py`, `store_migrations.py` | `sufficient` | Persistence helper modules explain schema, migration, and row-mapping boundaries. | Leave unchanged. |
| Backend Python | `src/goblin_king/api.py` | `needs function/helper comments` | Large route module has a useful module docstring, but public route groups and non-obvious auth/project-scope branches would benefit from concise intent comments. | Add targeted route-group comments in API/helper follow-up PR. |
| Backend Python | `src/goblin_king/cli.py` | `needs function/helper comments` | Large Typer command module has many operational entrypoints; major command groups need short contract/side-effect comments. | Add command-group comments in CLI follow-up PR. |
| Backend Python | `src/goblin_king/runtime.py` | `needs function/helper comments` | Docker/Kubernetes runtime adapters include complex mount/env/policy behavior where comments should name invariants and failure behavior. | Add runtime-boundary comments in runtime follow-up PR. |
| Backend Python | `src/goblin_king/store.py` | `needs function/helper comments` | Main store class is broad; persistence methods that mutate job/run/fanout state should document invariants and status transitions. | Add persistence-boundary comments in store follow-up PR. |
| Backend Python | `src/goblin_king/templates.py` | `needs function/helper comments` | Template generation has many emitted-file helpers; comments should mark generated contract expectations and write boundaries. | Add template helper comments in backend follow-up PR. |
| Backend Python | `src/goblin_king/scheduler.py`, `validation.py`, `resource_policies.py` | `sufficient` | Core validation, policy, and scheduler modules already include meaningful module/function comments around key contract boundaries. | Leave unchanged. |

### Tests And Fixtures Findings

| Subsystem | File Or Pattern | Classification | Finding | Recommended Follow-Up |
| --- | --- | --- | --- | --- |
| Tests | `tests/api_helpers.py` and tests with module docstrings | `sufficient` | Most test modules explain their phase or behavior focus and use descriptive test names. | Leave unchanged. |
| Tests | `tests/test_behavior_examples.py`, `test_cross_language_examples.py`, `test_cross_language_runtime_config.py`, `test_demo_registry.py`, `test_validation.py`, `test_wasi_examples.py` | `needs file comment` | These modules start directly with imports; a one-line module docstring would help future readers identify the behavior/example family under test. | Add file-level comments in tests follow-up PR. |
| Tests | Repeated inline project/config fixtures in `tests/test_api.py` and `tests/test_cli.py` | `needs function/helper comments` | A few inline fixtures encode resource-policy, discovery, or project-shape assumptions implicitly. | Add short fixture-intent comments or tiny helper builders only where setup is non-obvious. |
| Tests | `tests/fixtures/*.json` | `generated/vendor-like` | Tiny fixture registries are data inputs for tests and do not need comments. | Leave unchanged. |

### React Admin Findings

| Subsystem | File Or Pattern | Classification | Finding | Recommended Follow-Up |
| --- | --- | --- | --- | --- |
| React admin | `admin-ui/src/adminData.ts`, `components.tsx`, `types.ts`, `vite-env.d.ts` | `sufficient` | Shared data, component, type, and Vite declaration files already identify their purpose. | Leave unchanged. |
| React admin | `admin-ui/src/App.tsx`, `adminPanels.tsx` | `needs file comment` | Large UI surface files start at imports; a file-level comment should explain shell/panel ownership and data-flow expectations. | Add file-level comments in React admin follow-up PR. |
| React admin | `admin-ui/src/App.test.tsx`, `src/test/setup.ts` | `needs file comment` | Test and setup intent is clear from names but should be explicit for future frontend maintainers. | Add concise file-level comments in React admin follow-up PR. |
| React admin | `admin-ui/src/main.tsx`, `styles.css` | `needs file comment` | Entrypoint and global style organization would benefit from one top-level purpose comment. | Add entry/style comments in React admin follow-up PR. |
| React admin | `admin-ui/Dockerfile`, `nginx.conf`, `docker-entrypoint.d/20-admin-config.sh` | `needs file comment` | Operational files are short but should state admin serving/proxy/config-generation responsibilities. | Add operational comments in React admin follow-up PR. |
| React admin | `admin-ui/package.json`, `tsconfig.json`, `vite.config.ts`, `index.html` | `sufficient` | Standard tool config files are small and conventional. | Leave unchanged. |

### Helm And Deployment Config Findings

| Subsystem | File Or Pattern | Classification | Finding | Recommended Follow-Up |
| --- | --- | --- | --- | --- |
| Helm | `charts/goblin-king/values.yaml` | `sufficient` | Values file is organized by service/runtime concerns and is clearer than template-level comments would be. | Leave unchanged. |
| Helm | `charts/goblin-king/templates/*.yaml`, `_helpers.tpl` | `needs file comment` | Templates start directly with YAML/Go template logic; top comments should identify resource ownership and any non-obvious conditionals. | Add concise template-purpose comments in Helm follow-up PR. |
| Deployment | `Makefile` | `sufficient` | Help target documents operational targets; comments would likely duplicate command names. | Leave unchanged. |
| Deployment | `Dockerfile`, `docker-compose.yml` | `needs file comment` | Root build/Compose files should state control-plane scope and Docker socket boundary expectations. | Add operational comments in deployment follow-up PR. |
| GitHub config | `.github/*` | `sufficient` | CODEOWNERS, Dependabot, issue templates, and PR template are self-describing repository governance files. | Leave unchanged. |
| Metadata | `pyproject.toml`, `goblin-king-api.json`, `goblin-resource-policies.json`, root registries/image maps | `sufficient` | Config purpose is documented elsewhere and field names are readable. | Leave unchanged. |

### Example And Worker Findings

| Subsystem | File Or Pattern | Classification | Finding | Recommended Follow-Up |
| --- | --- | --- | --- | --- |
| Example Python goblins | `examples/goblins/*.py` | `sufficient` | Python example modules have clear docstrings describing sample purpose. | Leave unchanged. |
| Packaged workers | `workers/example.*/*.py` | `sufficient` | Worker modules have concise file-level docstrings and simple contract handling. | Leave unchanged. |
| Example READMEs | `examples/goblins/*/README.md`, adopting-project READMEs | `documentation-only` | README files already explain each example’s purpose and contract role. | Leave unchanged. |
| Non-Python hello workers | `examples/goblins/hello-{go,node,dotnet,java,php,ruby,rust}/**` | `needs file comment` | Several source files start directly with imports/package statements; add one language-native comment naming the Goblin container contract expectation. | Add starter-source comments in examples follow-up PR. |
| Behavior and WASI worker sources | `examples/goblins/behavior-*/*`, `wasi-*/*` | `needs file comment` | Some wrappers/sources rely on README context; source files should briefly state wrapper/contract role. | Add contract comments in examples follow-up PR. |
| Example Dockerfiles | `examples/goblins/*/Dockerfile`, `workers/*/Dockerfile` | `needs file comment` | Dockerfiles are short but should state worker image purpose and contract mount assumptions where not obvious. | Add Dockerfile comments in examples/workers follow-up PR. |
| Example registries, image maps, schemas, input JSON | `examples/**/*.json`, `examples/**/*.schema.json` | `generated/vendor-like` | Data/config fixtures are covered by docs and tests; inline comments are not valid JSON. | Leave unchanged. |

### Markdown Documentation Findings

| Subsystem | File Or Pattern | Classification | Finding | Recommended Follow-Up |
| --- | --- | --- | --- | --- |
| Documentation | `docs/**/*.md`, root README, CONTRIBUTING, SECURITY, CHANGELOG | `documentation-only` | Markdown files are the project documentation surface; no code-comment cleanup needed. | Leave unchanged unless content becomes stale. |
| Documentation standards | `docs/CONTRIBUTING.md`, `docs/goblin-king-plan.md` | `sufficient` | Comment standards are consistent: useful file/function comments, no noisy restatements. | Leave unchanged. |

### Follow-Up PR Order

1. Add comments to large backend API/CLI/runtime/store/template boundaries.
2. Add file-level comments to underdocumented test modules and non-obvious inline fixtures.
3. Add file-level comments to React admin shell/panel/test/style/operational files.
4. Add comments to Helm templates plus root Docker/Compose operational files.
5. Add language-native contract comments to non-Python example worker sources and Dockerfiles.

Each follow-up should be behavior-preserving and should skip every file marked
`sufficient`, `documentation-only`, or `generated/vendor-like`.
