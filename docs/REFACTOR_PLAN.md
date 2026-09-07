# ShelfCash Backend Refactor Plan

This document is a migration plan, not runtime truth. Runtime facts come from current code/tests and the two pre-refactor AS-IS snapshots. Accepted target intent comes only from `DECISIONS.md`.

## Refactor principles

1. Contract before implementation movement.
2. No hidden business semantic changes.
3. One vertical slice per task where possible.
4. Run tests before and after each slice.
5. Update `CURRENT_STATE.md` after each completed slice.
6. Update `DECISIONS.md` when architecture intent changes.
7. No big-bang refactor.
8. Historical AS-IS snapshots are preserved.
9. Proposal is not accepted architecture.

## Source-of-truth hierarchy

For current runtime behavior:

current code + tests
> runtime OpenAPI
> `CURRENT_ARCHITECTURE.md` / `CURRENT_API_CONTRACT.md`

For accepted architecture intent: `DECISIONS.md`.

For current project/refactor status: `CURRENT_STATE.md`.

For migration sequence and safety gates: `REFACTOR_PLAN.md`.

AS-IS facts, accepted targets, proposals, technical debt, and historical information must remain explicitly labeled and must not be silently merged.

## Baseline safety rules

1. No external payload changes hidden inside refactor.
2. No business semantic changes hidden inside refactor.
3. Freeze contract before moving implementation.
4. Prefer one vertical slice per refactor task.
5. Run before/after tests for each slice.
6. Update `CURRENT_STATE.md` after each completed slice.
7. Append or update an ADR when architecture intent changes.
8. `CURRENT_ARCHITECTURE.md` and `CURRENT_API_CONTRACT.md` are pre-refactor AS-IS snapshots.
9. Do not rewrite historical snapshots during active refactor.
10. Proposal is not an accepted target.
11. Archived docs are preserved before deletion.
12. No big-bang core/package consolidation.

## R0 — Pre-Refactor Baseline

Goals: review the AS-IS architecture/API snapshots; establish decisions, state, test and OpenAPI baselines; classify documentation and debt; and prepare a human-reviewed baseline commit/tag.

- [x] All 19 pre-existing mass deletions restored exactly from HEAD
- [x] `CURRENT_ARCHITECTURE.md` reviewed completely
- [x] `CURRENT_API_CONTRACT.md` reviewed completely
- [x] `DECISIONS.md` created
- [x] `CURRENT_STATE.md` created
- [x] Markdown inventory completed
- [x] API manifest verified — 58 unique paths / 70 operations; exact manifest tests pass
- [x] Frontend integration guide restored; frontend-document test passes
- [x] OpenAPI generation verified
- [x] All initial failures classified and repaired
- [x] Full test baseline green — 577 passed
- [x] Static baseline recorded
- [x] Working tree recorded
- [x] Known failing tests documented
- [x] No migration semantic change, import contract change, or LLM routing/behavior change
- [x] Canonical five documents consistent and computation-authority wording precise
- [x] No unexplained WIP — every remaining modified/untracked file is classified
- [x] No R1 or structural refactor started
- [x] R1 first slice identified
- [ ] Human baseline review completed

### Test baseline

Canonical repository command: `pytest -q` (Makefile and pytest configuration). Executed through the active project interpreter as `.venv\Scripts\python.exe -m pytest -q`.

- Collected/executed: 577
- Passed: 577
- Failed: 0
- Skipped: 0
- Xfailed/xpassed: 0/0
- Runtime: 142.39 seconds
- Warnings: 22 — one Starlette/httpx test-client deprecation and 21 SQLite datetime-adapter deprecations

R0.1 progression and triage:

| Failure group | Classification | Resolution |
|---|---|---|
| Deleted route manifest | STALE WORKING TREE | Restored from HEAD; two manifest failures resolved. |
| Deleted frontend guide | STALE WORKING TREE | Restored from HEAD; frontend-document test resolved. |
| Three migration downgrade expectations | STALE_TEST | Tests now assert the explicit irreversible `20260821_0023` policy; migration semantics are unchanged. |
| Manual import workbook missing `batch_id` | STALE_FIXTURE | Added a stable batch value to the shared fake inventory sheet; import contract is unchanged. |
| OpenRouter log capture disabled by Alembic `fileConfig` | ENVIRONMENTAL | Test explicitly re-enables `shelfcash.llm`; provider logging/routing/retry code is unchanged. |

Before R0.1 repair: 569 passed / 8 failed / 22 warnings in 145.49 seconds. After restoring the 19 files and before test repair: 572 passed / 5 failed / 22 warnings in 169.97 seconds. Final: 577 passed / 0 failed / 22 warnings in 142.39 seconds.

Static baseline: `.venv\Scripts\python.exe -m compileall -q app shelfcash_core tests` passed. No repository Markdown lint command or configured Markdown-lint dependency was found.

### High-value coverage observed

Existing tests cover the requested flows using actual suites: import (`test_import_business_persistence.py`, `test_import_normalized.py`, `test_manual_confirmation.py`); catalog/menu/recipe (`test_catalog_recipe_api.py`, `test_menu_addendum.py`, `test_recipe_bom.py`); forecast and parity (`test_forecast_api.py`, `test_forecast_integration.py`, `test_forecast_shadow.py`, `test_forecast_parity_qualification.py`); BOM/demand (`test_bom_adapter_regressions.py`, `test_deterministic_ingredient_metric_basis.py`); inventory/FEFO (`test_inventory_simulation.py`, `test_inventory_snapshot_integrity_acceptance.py`); procurement/strategy (`test_procurement_planner.py`, `test_strategy_outcomes.py`, `test_strategy_reason_evidence.py`); Decision Run/Brief (`test_decision_api.py`, `test_decision_assistant_contract.py`, `test_decision_intelligence.py`); deterministic Strategy/Ingredient (`test_strategy_presentation.py`, `test_ingredient_synthesis.py`); Overall Summary hybrid/fallback (`test_overall_summary.py`); Explanation/What-if (`test_ingredient_explanation.py`, `test_what_if_narrative.py`, `test_what_if_no_llm_regression.py`); OpenRouter/grounding (`test_openrouter_provider.py`, `test_semantic_evidence.py`); and route manifest (`test_api_contract_routes.py`).

### OpenAPI baseline

Direct `app.openapi()` generation succeeded without starting an external server: **58 paths / 70 operations**, matching `CURRENT_API_CONTRACT.md` by count. The restored manifest also contains 58 unique paths / 70 operations and its exact contract tests pass. Generated validation responses reference `#/components/schemas/HTTPValidationError`, while runtime handlers return ShelfCash's `{code, message, details, request_id}` envelope. The runtime surface therefore matches the documented inventory while retaining the already-documented schema drift for the future R1.1 slice.

### Architecture drift review

| Claim | Result | Finding |
|---|---|---|
| Production forecast provider | CONFIRMED | `ForecastService` only permits the `existing` provider, backed by `shelfcash_core`. |
| Shadow provider | CONFIRMED WITH QUALIFICATION | `shelfcash_forecast` is the optional forecast shadow/parity implementation. Decision Intelligence imports non-computation contracts/helpers from it, which does not make it a second production authority. |
| Core package overlap | CONFIRMED_DEBT | Both packages contain parallel forecast/BOM/scenario/inventory/optimization implementations, creating divergence and future authority-drift risk. |
| `DecisionPlanningService` breadth | CONFIRMED_DEBT | It owns demand, plan, Decision Run, read, brief, summary/synthesis persistence, explanation, What-if, and legacy plan concerns. |
| Strategy default | CONFIRMED | Deterministic; Qwen only in explicit `llm_polish`. |
| Ingredient default | CONFIRMED | Deterministic for NORMAL/WATCH/CRITICAL; optional guarded polish. |
| Overall Summary mode | CONFIRMED | Hybrid Qwen-primary wording with deterministic fallback; deterministic-first is only a proposal. |
| Decision Run assistant persistence | CONFIRMED | Core package commits first; assistant summary/synthesis persistence follows separately. Old missing artifacts are generated deterministically on read and are not silently persisted. |
| Default `GET /brief` narrative behavior | CONFIRMED | No Strategy Qwen call in deterministic mode; persisted Summary is read; missing historical Summary/Synthesis falls back deterministically. |
| LLM responsibilities | CONFIRMED | Narrative/semantic ambiguity only; backend retains all business authority. |
| `decision-core-integration.md` | DOCUMENTATION DRIFT | It says `CoreProcurementAdapter` is not enabled and residuals are not persisted; current Decision Run runtime uses it and persisted residual infrastructure exists. |

## R1 — Contract Hardening

Goal: strengthen external and application boundaries before moving architecture. `CURRENT_API_CONTRACT.md` is the starting inventory.

Candidate slices, subject to dependency review:

- R1.1 — Common error/OpenAPI contract alignment
- R1.2 — Decision Package typed/schema-version boundary
- R1.3 — IngredientDemandRun typed response
- R1.4 — ProcurementPlanRun typed response
- R1.5 — Menu/Product response DTO
- R1.6 — Operational `Page<T>` DTOs
- R1.7 — Completion/Purchase Order response DTOs

Every R1 slice must record its before contract, after contract, OpenAPI diff, contract tests, frontend/client impact, rollback scope, and acceptance gate. This list is not an instruction to execute every item in this exact order.

### Recommended first slice: R1.1 — Common error/OpenAPI contract alignment

- **WHY FIRST:** It resolves a confirmed public-description mismatch without moving computation, persistence, or business logic. All later typed-boundary work depends on truthful error schemas.
- **DEPENDENCIES:** Current exception handlers and `ErrorResponse`; inventory of validation/auth/domain error statuses; generated OpenAPI; API contract tests; frontend error-envelope assumptions.
- **CONTRACT TO FREEZE:** Preserve runtime `{code, message, details, request_id}` and status behavior. Change only OpenAPI declarations where required so validation `422` and common errors describe the actual envelope; explicitly decide whether the API-key header becomes a formal security scheme.
- **TEST GATE:** Runtime error-envelope regression tests remain green; exact OpenAPI response-schema assertions cover representative validation, auth, not-found, conflict, and server errors; 58 paths / 70 operations and success payloads remain unchanged.
- **ROLLBACK SCOPE:** Revert the centralized OpenAPI/error-response declarations and their contract tests; no data migration or runtime business rollback.

Do not implement R1.1 until R0 blockers are reviewed.

## R2 — Decision Intelligence / Narrative Cleanup

Preserve accepted routing:

- Strategy → deterministic DEFAULT.
- Ingredient → deterministic DEFAULT.
- Overall Summary → HYBRID.
- Decision Explanation / What-if → guarded hybrid where useful.
- Excel Mapping → semantic hybrid.

Remove dead or default-unused narrative paths only after runtime/call-site proof. Centralize truly shared display and authority/provenance helpers while keeping domain-specific renderers separate. Reduce unnecessary transport/guard complexity without external payload change. Do not create a giant `DeterministicNarrativeRenderer` God class. Do not switch Overall Summary to deterministic-first without a new accepted ADR.

## R3 — Canonical Computation Core

Problem: `shelfcash_core` and `shelfcash_forecast` substantially overlap.

Mandatory sequence for each vertical slice: measure parity → choose the canonical implementation for that slice → migrate its caller → regression test → remove duplicate only after proof → continue.

Potential slices: forecast, BOM, inventory simulation, scenario generation, optimization. Do not delete a package at once and do not assume which package wins before runtime/parity evidence.

## R4 — Decision Planning Use-Case Decomposition

Current hotspot: `DecisionPlanningService`.

Proposed, not accepted, use-case boundaries: `GenerateIngredientDemand`, `GenerateProcurementPlans`, `GenerateDecisionRun`, `ReadDecisionPackage`, `BuildDecisionBrief`, `ExplainDecision`, and `EvaluateWhatIf`.

Do not implement this decomposition until R1 sufficiently freezes its public/application contracts.

## R5 — Legacy / Compatibility Separation

Classify routes/components as CANONICAL, COMPATIBILITY, or DEPRECATED. Establish an explicit lifecycle, deprecation telemetry where available, and a client migration plan. Do not silently break frontend or other clients.

## R6 — Persistence / Transaction Cleanup

Potential goals: clarify repository versus direct ORM policy; assess Unit of Work use rather than assuming it; version persisted Decision Package JSON; define schema-migration strategy; and reduce indefinite `dict.get` compatibility logic. No database rewrite.

## R7 — Stabilization

After controlled slices are complete: run full regression; review OpenAPI diff; verify historical Decision Runs; verify public frontend contracts; remove only proven dead code; check performance sanity and migration compatibility; produce stabilized architecture documentation and an updated stable API contract; then archive pre-refactor snapshots.

Only at this point should `STABILIZED_ARCHITECTURE.md` be considered and made authoritative.

## High-risk contract debt review

| Boundary | Classification | Evidence and intended handling |
|---|---|---|
| Raw Decision Package | CONFIRMED_DEBT | POST/GET Decision Run use `dict[str, Any]`; persisted package is unversioned JSON. Freeze and version before internal movement. |
| IngredientDemandRun | CONFIRMED_DEBT | Four planning routes include untyped demand responses; define response DTO without changing shape. |
| ProcurementPlanRun | CONFIRMED_DEBT | Generate/read plan responses lack `response_model`; freeze strategy/candidate payload first. |
| Menu/Product | CONFIRMED_DEBT | Product CRUD/menu/components responses are runtime dicts; recipes are already typed. |
| Operational page responses | CONFIRMED_DEBT | Operational list endpoints return page-shaped dicts without `Page<T>` schemas. |
| Completion write responses | CONFIRMED_DEBT | Several inventory/history/supplier/settings/calendar writes are untyped; inventory-constraint writes are already typed. |
| Purchase Orders | CONFIRMED_DEBT | Create/list/get/patch/confirm/receive responses are untyped. |
| Common error envelope/OpenAPI | CONFIRMED_DEBT | Runtime envelope and generated validation `422` schema disagree. |

No reviewed high-risk item above is `ALREADY_FIXED`; typed neighboring endpoints do not close these specific boundaries.

## Technical debt priorities

| Priority | Debt | Reason |
|---|---|---|
| P0 | OpenAPI/common-error mismatch | Public contract correctness and generated-client risk. |
| P0 | Raw, unversioned Decision Package API/persistence boundary | Contract and historical-compatibility risk at the central decision artifact. |
| P0 | Duplicated/diverged computation implementations with authority-drift risk | `shelfcash_core` remains production authority; overlap and helper/contract cross-imports require evidence before consolidation. |
| P1 | Untyped demand, procurement, Menu/Product, page, completion, and PO responses | High-change public boundaries are difficult to diff and protect. |
| P1 | `DecisionPlanningService` breadth | High complexity and broad change blast radius; decomposition waits for R1. |
| P1 | Persisted JSON without explicit schema versioning | Migration and historical-run risk. |
| P1 | Mixed repository/direct ORM and transaction ownership | Testability and consistency risk. |
| P1 | Legacy/canonical API overlap without lifecycle registry | Client migration and accidental-break risk. |
| P1 | LLM transport/guard complexity after default LLM scope shrank | Maintenance burden; only proven unused paths may be removed. |
| P2 | Dense route/service responsibility hotspots | Readability and local maintenance; not a correctness reason by itself. |
| P2 | Stale and duplicated historical documentation | Developer confusion; handle only in `DOC-CLEANUP-0` after baseline approval. |

## Documentation Inventory

Classification describes each restored/current Markdown file's actual role. All 28 backend-relevant Markdown files are present. No archival or deletion action in the final column is executed by R0.1.

| File | Classification | Current role | Referenced by | Recommended future action |
|------|----------------|--------------|---------------|---------------------------|
| `README.md` | STILL_REFERENCED | Repository entry point and developer workflow | Repository entry point | KEEP |
| `docs/README.md` | STILL_REFERENCED | Maintained-doc index | `README.md` | KEEP; update after baseline review |
| `docs/CURRENT_ARCHITECTURE.md` | CANONICAL | Pre-refactor AS-IS architecture snapshot | `CURRENT_API_CONTRACT.md`, `DECISIONS.md`, `CURRENT_STATE.md`, this plan | KEEP |
| `docs/CURRENT_API_CONTRACT.md` | CANONICAL | Pre-refactor AS-IS API/contract snapshot | `CURRENT_ARCHITECTURE.md`, `DECISIONS.md`, `CURRENT_STATE.md`, this plan | KEEP |
| `docs/DECISIONS.md` | CANONICAL | Accepted architecture intent | `CURRENT_STATE.md`, this plan | KEEP |
| `docs/CURRENT_STATE.md` | CANONICAL | Current phase and operational checkpoint | This plan | KEEP |
| `docs/REFACTOR_PLAN.md` | CANONICAL | Migration order and safety gates | `CURRENT_STATE.md` | KEEP |
| `docs/decision-runs-api.md` | STILL_REFERENCED | Specialized Decision Run/What-if behavior | `docs/README.md` | KEEP; reconcile later with canonical API snapshot |
| `docs/decision_assistant_api_contract.md` | STILL_REFERENCED | Specialized frozen Decision Assistant contract | `docs/README.md`, `CURRENT_API_CONTRACT.md`, `tests/test_frontend_decision_assistant_docs.py` | KEEP |
| `docs/FORECAST_MODEL_PREPROCESSING_SCHEMA.md` | STILL_REFERENCED | Forecast feature/preprocessing contract | `docs/README.md` | KEEP |
| `docs/INVENTORY_CONSTRAINT_CONTRACT.md` | STILL_REFERENCED | Inventory/business constraint semantics | `docs/README.md` | KEEP |
| `docs/MENU_IMPORT_AND_API.md` | STILL_REFERENCED | Menu import/API specialization | `docs/README.md` | KEEP |
| `docs/frontend_decision_assistant_integration.md` | STILL_REFERENCED | Frontend integration guide and current test dependency | `tests/test_frontend_decision_assistant_docs.py` | KEEP through baseline; reconsider only in DOC-CLEANUP-0 |
| `docs/decision-core-integration.md` | HISTORICAL / ARCHIVE_CANDIDATE | Superseded core-integration checkpoint; runtime claims are stale | `docs/README.md` | ARCHIVE_AFTER_BASELINE; update index link |
| `docs/API_OPERATION_BEHAVIOR_AUDIT.md` | HISTORICAL / ARCHIVE_CANDIDATE | Old 53-operation implementation audit | NO_CURRENT_REFERENCES_FOUND | ARCHIVE_AFTER_BASELINE |
| `docs/CANONICAL_BUSINESS_SCHEMA.md` | HISTORICAL / ARCHIVE_CANDIDATE | Early canonical-schema checkpoint | NO_CURRENT_REFERENCES_FOUND | ARCHIVE_AFTER_BASELINE |
| `docs/CATALOG_AND_RECIPE_API.md` | HISTORICAL / ARCHIVE_CANDIDATE | Early catalog/recipe checkpoint | NO_CURRENT_REFERENCES_FOUND | ARCHIVE_AFTER_BASELINE |
| `docs/CHECKPOINT_0_CODEBASE_AUDIT.md` | HISTORICAL / ARCHIVE_CANDIDATE | Initial codebase audit | NO_CURRENT_REFERENCES_FOUND | ARCHIVE_AFTER_BASELINE |
| `docs/DECISION_INTELLIGENCE_API.md` | HISTORICAL / ARCHIVE_CANDIDATE | Superseded Decision Intelligence API design | NO_CURRENT_REFERENCES_FOUND | ARCHIVE_AFTER_BASELINE |
| `docs/FE_DECISION_BRIEF_HANDOFF.md` | HISTORICAL / ARCHIVE_CANDIDATE | Earlier frontend brief handoff | NO_CURRENT_REFERENCES_FOUND | ARCHIVE_AFTER_BASELINE |
| `docs/FE_DECISION_RUNTIME_CONTRACT.md` | HISTORICAL / ARCHIVE_CANDIDATE | File identifies itself as an archived pre-Phase-7 draft | NO_CURRENT_REFERENCES_FOUND | ARCHIVE_AFTER_BASELINE |
| `docs/IMPORT_PERSISTENCE.md` | HISTORICAL / ARCHIVE_CANDIDATE | Early import-persistence checkpoint | NO_CURRENT_REFERENCES_FOUND | ARCHIVE_AFTER_BASELINE |
| `docs/IMPORT_TO_BUSINESS_PERSISTENCE.md` | HISTORICAL / ARCHIVE_CANDIDATE | Early import-to-business checkpoint | NO_CURRENT_REFERENCES_FOUND | ARCHIVE_AFTER_BASELINE |
| `docs/ShelfCash_API_Contract_v1(3).md` | HISTORICAL / ARCHIVE_CANDIDATE | Consolidated older v1.1 API contract | NO_CURRENT_REFERENCES_FOUND | ARCHIVE_AFTER_BASELINE |
| `docs/ShelfCash_Menu_API_Contract_v1.md` | HISTORICAL / ARCHIVE_CANDIDATE | Older menu contract/addendum | NO_CURRENT_REFERENCES_FOUND | ARCHIVE_AFTER_BASELINE |
| `docs/API_IMPLEMENTATION_STATUS.md` | REDUNDANT / DELETE_CANDIDATE | Superseded generated implementation status | NO_CURRENT_REFERENCES_FOUND | DELETE_AFTER_REVIEW |
| `docs/CODEX_MASTER_INSTRUCTIONS.md` | REDUNDANT / DELETE_CANDIDATE | Obsolete checkpoint execution instructions | NO_CURRENT_REFERENCES_FOUND | DELETE_AFTER_REVIEW |
| `docs/ShelfCash_API_Contract_v1.md` | REDUNDANT / DELETE_CANDIDATE | Older draft superseded by consolidated v1.1 and current snapshot | NO_CURRENT_REFERENCES_FOUND | DELETE_AFTER_REVIEW |

No backend-relevant Markdown file remains unclassified. No file was classified as `NEEDS_REVIEW`; the restored frontend guide remains `STILL_REFERENCED` because it is a current test dependency.

## Baseline readiness gate

- [x] `CURRENT_ARCHITECTURE.md` reviewed
- [x] `CURRENT_API_CONTRACT.md` reviewed
- [x] `DECISIONS.md` created
- [x] `CURRENT_STATE.md` created
- [x] `REFACTOR_PLAN.md` created
- [x] Documentation inventory completed
- [x] Source-of-truth hierarchy recorded
- [x] Accepted decisions separated from proposals
- [x] Current branch/HEAD recorded
- [x] Working tree state known
- [x] Test baseline executed
- [x] Test failures documented
- [x] OpenAPI baseline verified and drift documented
- [x] High-risk contract debt classified
- [x] High-risk architecture debt classified
- [x] All 19 pre-existing deletions restored
- [x] Route manifest and frontend guide available with green dependency tests
- [x] Full suite green where technically achievable
- [x] No hidden migration, import-contract, or LLM behavior change
- [x] Computation debt wording distinguishes implementation duplication from production authority
- [x] No unexplained WIP
- [x] One R1 first slice recommended
- [x] No production refactor performed
- [x] No Markdown files deleted/moved/renamed by this task
- [x] No commit
- [x] No tag
- [x] No push

**Readiness: READY_FOR_REFACTOR after human freeze.** All technical R0 gates are green: the 19 deletions are restored, every initial failure is triaged and resolved, the full suite passes, OpenAPI/manifest remain 58 paths / 70 operations, and every remaining file is explained. The next action is human review plus baseline repair/docs commits and tag; R1 is not active yet.

## Human baseline and documentation cleanup sequence

1. Review the classified pre-existing documentation updates and R0 test repairs.
2. Review the inventory classifications and the stale `decision-core-integration.md` link in `docs/README.md`.
3. Create the separate baseline-repair and canonical-document commits, then tag the reviewed baseline.
4. Run a separate documentation-only task, **DOC-CLEANUP-0**, to create `docs/archive` if approved, move archive candidates, update links, delete only approved redundant files, and preserve provenance.
5. Start R1.1 only after the baseline freeze and controlled documentation cleanup.
