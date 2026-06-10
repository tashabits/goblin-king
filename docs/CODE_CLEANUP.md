# Repo Cleanup Notes

Phase 23 is a behavior-preserving cleanup pass. The intent is to make future feature
work less cramped without moving public adoption boundaries.

## Phase 23 Splits

| Area | Before | After |
| --- | --- | --- |
| API schemas | Request/response models lived inside `src/goblin_king/api.py`. | API schemas live in `src/goblin_king/api_models.py`; route logic remains in `api.py`. |
| Admin UI types | API-facing TypeScript shapes lived inside `admin-ui/src/App.tsx`. | Shared shapes live in `admin-ui/src/types.ts`. |
| Admin UI data helpers | Sorting, quotes, JSON parsing, and traffic compaction lived in `App.tsx`. | Small data helpers live in `admin-ui/src/adminData.ts`. |
| Admin UI components | Reusable `Stat` and `Table` lived at the bottom of `App.tsx`. | Presentational components live in `admin-ui/src/components.tsx`. |

## Cleanup 01 Backend Helpers

| Area | Before | After |
| --- | --- | --- |
| Job metadata | API, CLI, and scheduler each built goblin source/effective policy metadata locally. | `src/goblin_king/metadata.py` provides the shared internal metadata builder. |
| JSON file reads | Registry, project, API settings, worker image maps, and resource policies each repeated UTF-8 JSON file reads. | `src/goblin_king/jsonio.py` centralizes small JSON file and pretty-print helpers while preserving caller-specific errors. |
| Generated JSON snippets | Template and smoke helpers repeated pretty JSON formatting for generated files. | Template/smoke generation now use the shared pretty JSON helpers. |

## Cleanup 02 Store Persistence Split

| Area | Before | After |
| --- | --- | --- |
| SQLite schema | Table definitions lived inline with the `SQLiteStore` method implementation. | `src/goblin_king/store_schema.py` owns SQLAlchemy table definitions and metadata. |
| Existing database compatibility | Auto-ALTER compatibility logic lived inside the main store module. | `src/goblin_king/store_migrations.py` owns schema compatibility helpers. |
| Row mapping | Row-to-contract conversion helpers lived at the bottom of `store.py`. | `src/goblin_king/store_rows.py` owns deterministic row mapping and datetime coercion. |
| Public store imports | `SQLiteStore` and `DEFAULT_DB_PATH` were imported from `goblin_king.store`. | The same imports continue to work; the split is internal only. |

## Cleanup 03 API, Runtime, And CLI Split

| Area | Before | After |
| --- | --- | --- |
| API artifact helpers | Artifact path safety, storage status, and cleanup logic lived at the bottom of `api.py`. | `src/goblin_king/api_artifacts.py` owns artifact helper logic used by API routes. |
| API schedule helpers | Schedule create validation and cron/timezone checks lived in `api.py`. | `src/goblin_king/api_schedules.py` owns schedule request conversion and validation. |
| Runtime policy helpers | Docker/Kubernetes resource translation, artifact policy checking, and Kubernetes naming/client helpers lived in `runtime.py`. | `src/goblin_king/runtime_helpers.py` owns runtime helper logic while runtime adapter classes remain in `runtime.py`. |
| CLI loading helpers | Registry, worker, project, policy, input, and validation-output helpers lived at the bottom of `cli.py`. | `src/goblin_king/cli_support.py` owns CLI support helpers while command functions stay in `cli.py`. |

## Cleanup 04 Admin UI Split

| Area | Before | After |
| --- | --- | --- |
| Admin shell | Login, sidebar, hero, and dashboard chrome lived inline in `App.tsx`. | `admin-ui/src/adminPanels.tsx` owns login, shell, dashboard, Goblin Lab, and Task Board panel components. |
| Goblin Lab | Registry table, kind picker, JSON editor, and proof buttons lived inline in `App.tsx`. | `GoblinLabPanel` receives data/actions as props and renders the same tester workflow. |
| Task Board | Job cards and cancel/hard-kill controls lived inline in `App.tsx`. | `TaskBoardPanel` renders job cards while `App.tsx` keeps orchestration state and API actions. |
| Admin behavior | The app file handled both state and all visible panel rendering. | State and API orchestration stay in `App.tsx`; reusable panel rendering moved into focused components. |

## Cleanup 05 Tests, Docs, And Style Closeout

| Area | Before | After |
| --- | --- | --- |
| API test setup | API tests repeated local `TestClient`, `ApiSettings`, `SQLiteStore`, artifact root, and auth-header setup. | `tests/api_helpers.py` provides shared local API client and bearer-header helpers. |
| Proof table | Phase 23 referred broadly to cleanup work without the staged release/audit details. | `docs/proof_table.md` records the staged cleanup, baseline release, completion release, and audit proof expectations. |
| Public boundary docs | Internal module guidance named older large modules but not the new helper boundaries. | `docs/PUBLIC_API.md` describes the new helper modules as internal implementation details. |

## Phase 44 Cleanup Pass

| Area | Before | After |
| --- | --- | --- |
| API state and discovery | `api.py` owned route handlers plus registry reload, source reporting, and worker map loading. | `src/goblin_king/api_state.py` owns `AppState` and discovery state. |
| API runtime bookkeeping | `api.py` ended with runtime termination and resource-policy audit helpers. | `src/goblin_king/api_runtime.py` owns termination proof, effective policy lookup, and policy rejection events/audits. |

The split is behavior-preserving. Route names, response models, CLI commands, runtime
behavior, scheduler behavior, database behavior, and admin UX stay unchanged.

## Stable Boundary

No public imports were changed. Adopting projects should continue using the root
`goblin_king` exports and documented CLI/API surfaces. The new helper modules are
internal implementation boundaries for Goblin King itself.

## Cleanup Rules Going Forward

- Prefer cohesive helper modules over broad `utils` files.
- Keep helpers small enough that their purpose is obvious from the filename.
- Do not move behavior across public boundaries without updating `docs/PUBLIC_API.md`.
- Preserve route names, response models, CLI names, and React admin behavior during
  refactors unless a phase explicitly changes them.
