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
