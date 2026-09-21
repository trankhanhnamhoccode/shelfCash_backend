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

## Post-R7 stochastic risk-evidence sufficiency slice â€” COMPLETE (2026-09-19)

- [x] ADR-016 accepted before the semantic boundary is released: stochastic risk authority requires explicit effective-scenario sufficiency.
- [x] Generated stochastic ingredient-demand paths are canonicalized by exact business demand vector; duplicate weights are aggregated and `N_eff` is calculated from normalized unique-path weights.
- [x] `N_eff < 10` routes to the established deterministic p25/p50/p75 design scenarios with additive diagnostics; it does not relax a strategy threshold or manufacture a risk estimate.
- [x] `RISK_CONSTRAINT_VIOLATION` is guarded by `stochastic_saa_enabled`; Exact FEFO safety-floor, service, budget, supplier, MOQ, pack, capacity, lead-time, expiry, accounting and model-match authority are unchanged.
- [x] Acceptance coverage pins duplicate collapse, insufficient fallback, sufficient high/low risk, exact-floor independence, deterministic regression, residual-fallback compatibility, and old Decision Package diagnostic compatibility.
- [x] Verification passed: focused scenario/procurement/FEFO/strategy/Decision/What-if regression **156 passed**; full suite **656 passed**; `compileall`, `git diff --check`, and runtime OpenAPI **58 paths / 70 operations** passed. The OpenAPI change is limited to additive optional `DecisionPackageTechnicalMetrics` properties.
- [x] Rollback remains local to the sufficiency helper, Decision adapter routing/diagnostics, critic risk guard, focused tests, and ADR/checkpoint documentation. No migration, data backfill, public route change, or historical package rewrite.

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
- [x] Human baseline review completed; baseline tagged `pre-refactor-backend-2026-09`

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

- [x] R1.1 — Common error/OpenAPI contract alignment
- [x] R1.2 — Decision Package typed boundary
- [x] R1.3 — IngredientDemandRun typed response
- [x] R1.4 — ProcurementPlanRun typed response
- [x] R1.5 — Menu/Product response DTO
- [x] R1.6 — Operational `Page<T>` DTOs
- [x] R1.7 — Completion/Purchase Order response DTOs

Every R1 slice must record its before contract, after contract, OpenAPI diff, contract tests, frontend/client impact, rollback scope, and acceptance gate. This list is not an instruction to execute every item in this exact order.

### Recommended first slice: R1.1 — Common error/OpenAPI contract alignment

- **WHY FIRST:** It resolves a confirmed public-description mismatch without moving computation, persistence, or business logic. All later typed-boundary work depends on truthful error schemas.
- **DEPENDENCIES:** Current exception handlers and `ErrorResponse`; inventory of validation/auth/domain error statuses; generated OpenAPI; API contract tests; frontend error-envelope assumptions.
- **CONTRACT TO FREEZE:** Preserve runtime `{code, message, details, request_id}` and status behavior. Change only OpenAPI declarations where required so validation `422` and common errors describe the actual envelope; explicitly decide whether the API-key header becomes a formal security scheme.
- **TEST GATE:** Runtime error-envelope regression tests remain green; exact OpenAPI response-schema assertions cover representative validation, auth, not-found, conflict, and server errors; 58 paths / 70 operations and success payloads remain unchanged.
- **ROLLBACK SCOPE:** Revert the centralized OpenAPI/error-response declarations and their contract tests; no data migration or runtime business rollback.

Do not implement R1.1 until R0 blockers are reviewed.

### R1.1 completion evidence

- **Status:** COMPLETE (2026-09-08).
- **Runtime contract:** unchanged. Validation remains `422`, auth remains conditional `401` through `x-shelfcash-key`, and domain/server status codes, common error envelope, request-ID behavior, success payloads, routes, business semantics, and persistence remain unchanged.
- **OpenAPI diff:** 68 automatic validation `422` responses now reference `#/components/schemas/ErrorResponse` instead of `HTTPValidationError`; 68 operations that expose the existing `x-shelfcash-key` parameter now declare the existing `401` error envelope; representative, runtime-evidenced `404`, `409`, and `500` responses are declared on `/imports/{import_id}`, ingredient create, and forecast training respectively. `HTTPValidationError` and `ValidationError` are removed from published components. Paths remain **58** and operations **70**; success schemas are unchanged.
- **Tests/gates:** `tests/test_error_openapi_contract.py` adds validation, auth, not-found, conflict, server-error, success-schema, and manifest assertions. Targeted error/OpenAPI/route/forecast tests: **18 passed**. Full suite: **581 passed, 0 failed, 0 skipped, 22 warnings in 205.82 seconds**. `compileall -q app shelfcash_core tests` and `git diff --check` passed.
- **Security decision boundary:** no OpenAPI security scheme was added. The established header-parameter behavior remains because no accepted decision or existing test requires an auth-model redesign.
- **Rollback:** revert the centralized OpenAPI builder/composition registration, `ErrorResponse` requirement alignment, R1.1 contract tests, and these completion notes. No database migration, backfill, business rollback, or Decision Run migration is involved.
- **Next state at R1.1 completion:** R1.2 was the next candidate and required its own dependency review and contract gate.

### R1.2 completion evidence

- **Status:** COMPLETE (2026-09-08).
- **Contract:** `POST /stores/{store_id}/decision-runs` and raw `GET /decision-runs/{decision_run_id}` now use the explicit `DecisionPackage` response schema. The current wire payload is unchanged: current top-level fields, ISO date serialization, JSON numeric serialization, statuses, strategy semantics, nested contents, and assistant timing/optionality remain intact.
- **Typed boundary:** identity/status/engine/recommendation, plan items, inventory simulation, candidate strategies, selection, critic, reason codes, warnings, and technical metrics are explicit. Broad business metrics and diagnostics remain bounded nested objects rather than triggering R1.3/R1.4 scope expansion. No `package_schema_version` was added because old persisted-JSON compatibility is not a sufficient public/runtime reason.
- **Disposable persistence:** ADR-013 supersedes ADR-009's old-row preservation policy. Partial historical fixtures no longer assert that raw HTTP GET must serialize their obsolete shapes; their `/brief` read fallback coverage remains. No production compatibility reader was removed in this slice.
- **OpenAPI/gates:** both raw routes reference `#/components/schemas/DecisionPackage`; paths remain **58**, operations remain **70**, and unrelated success schemas remain unchanged. Targeted Decision Package/Decision Assistant/OpenAPI/route tests: **128 passed**. Full suite: **581 passed, 0 failed, 0 skipped, 22 warnings in 158.99 seconds**.
- **Rollback:** revert the DecisionPackage schema/route declarations, affected current-contract and historical-fixture test assertions, ADR-013/docs, and this completion evidence. No Alembic migration, data backfill, or restoration of disposed historical SQLite rows is required.
- **Next state:** R1.3 (IngredientDemandRun typed response) is the next candidate only, subject to dependency review and its own contract gate.

### R1.3 completion evidence

- **Status:** COMPLETE (2026-09-08).
- **Before/after contract:** POST/GET Ingredient Demand changed from generic/untyped OpenAPI objects to the explicit `IngredientDemandRunResponse`, `IngredientDemandPredictionResponse`, and `IngredientDemandContributionResponse` boundary. Existing generated wire fields, status/error behavior, IDs, sorted prediction ordering, ISO date/datetime values, numeric p25/p50/p75 values, and string legacy contribution evidence values are unchanged.
- **Failure/warning coverage:** a persisted `blocked` demand run remains readable through the typed GET shape with `failure_code`, `failure_message`, `completed_at`, and no predictions. The existing scoped no-match warning/empty-predictions behavior remains valid.
- **OpenAPI/gates:** both operations reference `#/components/schemas/IngredientDemandRunResponse`; paths remain **58**, operations remain **70**, and no unrelated routes, methods, auth/error declarations, DecisionPackage, or ProcurementPlan schemas changed. Targeted demand/BOM/procurement/Decision/OpenAPI/route tests: **69 passed**. Full suite: **582 passed, 0 failed, 22 warnings in 150.48 seconds**.
- **Frontend/client impact:** no intended payload change; the schema documents the current runtime payload only. No persistence compatibility cleanup was needed.
- **Rollback:** revert the IngredientDemand response schemas, the two response-model declarations, R1.3 contract tests, and these completion notes. No migration, backfill, or data rollback is required.
- **Next state:** R1.4 (ProcurementPlanRun typed response) is the next candidate only, subject to its own dependency review and contract gate.

### R1.4 completion evidence

- **Status:** COMPLETE (2026-09-08).
- **Before/after contract:** POST/GET Procurement Plans changed from generic/untyped OpenAPI objects to explicit `ProcurementPlanRunResponse`, `ProcurementPlanResponse`, `ProcurementPlanLineResponse`, and daily-projection response boundaries. Existing current wire fields, plan/line ordering, strategy/recommendation meaning, dates, numeric formats, dynamic metrics, warnings, and failure behavior are unchanged.
- **Boundary choices:** plan and line scalar fields, simulation projection summary/daily rows, and consumed lots are stable and typed. `metrics` remains a bounded JSON object because its constraint, budget, shelf-life, and diagnostic contents are intentionally variable; it is not an optimizer-model redesign.
- **Failure/no-recommendation coverage:** completed runs without a feasible recommendation retain nullable `recommended_strategy` and their plans. A persisted blocked run remains readable through the typed GET shape with failure fields and no plans.
- **OpenAPI/gates:** both operations reference `#/components/schemas/ProcurementPlanRunResponse`; paths remain **58**, operations remain **70**, and no unrelated routes, methods, auth/error declarations, IngredientDemand, DecisionPackage, or legacy Plan schemas changed. Targeted procurement/strategy/Decision/OpenAPI/route tests: **90 passed**. Full suite: **584 passed, 0 failed, 22 warnings in 152.04 seconds**.
- **Frontend/client impact:** no intended payload change; the schema documents the current runtime payload only. No persistence compatibility cleanup was needed.
- **Rollback:** revert the Procurement Plan response schemas, the two response-model declarations, R1.4 contract tests, and these completion notes. No migration, backfill, or data rollback is required.
- **Next state:** R1.5 (Menu/Product response DTO) is the next candidate only, subject to its own dependency review and contract gate.

### R1.5 completion evidence

- **Status:** COMPLETE (2026-09-08).
- **Before/after contract:** Product list/create/patch, component replacement, and the menu read model changed from generic/untyped OpenAPI objects to `MenuProductResponse`, `MenuComponentResponse`, `MenuSummaryResponse`, and `MenuResponse`. The current wire fields, nullable values, JSON numeric types, fresh/replayed timestamp representations, duplicate component aliases, component/product ordering, versioning, status/active/item-type meanings, filters, and menu paging remain unchanged.
- **OpenAPI/gates:** `GET /products` is an array of `MenuProductResponse`; product create/patch/components reference `MenuProductResponse`; `GET /menu` references `MenuResponse`. Paths remain **58**, operations remain **70**, and no unrelated routes, methods, auth/error declarations, DecisionPackage, IngredientDemand, ProcurementPlan, operational-page, completion, Purchase Order, or legacy schemas changed. Targeted Product/Menu/recipe/import/OpenAPI/route tests: **70 passed**. Full suite: **588 passed, 0 failed, 22 warnings in 219.86 seconds**. `compileall -q app shelfcash_core tests` and `git diff --check` passed.
- **Frontend/client impact:** no intended payload change. The DTOs document the existing shared `MenuService.serialize` output used by Product/Menu HTTP reads; legacy-looking duplicate component fields remain public contract.
- **Persistence/rollback:** no persistence compatibility cleanup, migration, backfill, or data rollback was needed. Revert the Menu/Product response DTOs, five route response declarations, `tests/test_menu_response_contract.py`, and these notes to roll back this slice.
- **Next state:** R1.6 (Operational `Page<T>` DTOs) is the next candidate only, subject to its own dependency review and contract gate.

### R1.6 completion evidence

- **Status:** COMPLETE (2026-09-08).
- **Before/after contract:** the eight operational read endpoints — imports, recipe versions, sales history, usage history, purchase history, inventory, inventory movements, and calendar features — changed from generic/untyped OpenAPI objects to concrete generated `Page_<ItemResponse>_` schemas backed by a response-only `Page[T]` DTO. The current wire envelope remains `{items, page, page_size, total}`.
- **Boundary choices:** Import and recipe-version summaries receive dedicated DTOs. Sales, usage, purchase, and movement DTOs preserve every current field emitted by `OperationalService._public`, including the public `source_profile_id` movement field; hashes remain absent exactly as before. Inventory retains the existing computed lot/FEFO/expiry view. Calendar retains its existing six-field operational read view. Decimal-derived values remain JSON numbers; recipe-line Decimal strings remain strings.
- **Runtime and OpenAPI gates:** filters, ordering, page defaults/bounds, total semantics, empty/beyond-result behavior, status codes, auth/error behavior, inventory/history/import/recipe behavior, and persistence remain unchanged. Each scoped `200` response now references an explicit page/item schema; paths remain **58**, operations remain **70**. At R1.6 completion, Purchase Order list and Completion writes were deliberately deferred to R1.7; R1.7 has since typed those boundaries.
- **Tests/gates:** `tests/test_operational_page_contract.py` covers all eight successful page/item shapes, numeric/date representations, filters, empty/beyond-result pagination, page validation, OpenAPI refs, R1.1–R1.5 typed-boundary preservation, and the 58/70 manifest count: **3 passed**. Full suite: **591 passed, 0 failed, 22 warnings in 512.13 seconds**.
- **Frontend/client impact:** no intended payload change; schemas describe the existing runtime payloads only. No persistence compatibility cleanup, migration, backfill, business change, or database rollback is required.
- **Rollback:** hunk-selectively revert `app/schemas/operational.py`, eight route declarations in `app/api/operational.py`, `tests/test_operational_page_contract.py`, and this completion documentation. The shared working tree also contains R1.1–R1.5 WIP, which must not be reverted with this slice.
- **Next state:** R1.7 (Completion/Purchase Order response DTOs) is the next candidate only, subject to its own dependency review and contract gate.

### R1.7 completion evidence

- **Status:** COMPLETE (2026-09-09).
- **Before/after contract:** inventory count/adjustment, sales/purchase history batches, supplier POST/PUT, settings PUT, calendar PUT, and all Purchase Order operations changed from generic/untyped OpenAPI success objects to explicit DTO references. Runtime payloads and statuses remain unchanged.
- **Boundary choices:** `MovementDeltaResponse` preserves string quantities. Sales warnings remain bounded `JsonValue` because reconciliation emits structured warning records. Supplier writes use a separate DTO because their current payload intentionally omits `available_delivery_days` published by the already-typed supplier read. Calendar write reuses R1.6's exact `CalendarFeatureResponse`. PO create preserves `{orders:[...]}`, PO list reuses R1.6's `Page[T]`, and all single-PO outcomes share one response/line DTO graph.
- **PO behavior frozen:** draft-only patch; confirm reserves budget and transitions `draft -> ordered`; receive preserves partial/full transitions, current lot/receipt/movement/expiry creation, idempotency, version conflicts, ordered line identity/order, and reserved-to-spent accounting. DTOs do not compute any business facts.
- **OpenAPI/gates:** all 14 scoped success responses reference concrete schemas, including `Page_PurchaseOrderResponse_`; paths remain **58**, operations remain **70**. R1.1–R1.6 error, Decision, planning, Menu, and operational-page boundaries remain explicit. Bootstrap, dashboard, and settings GET remain untyped, outside R1.7 scope.
- **Tests/gates:** `tests/test_completion_response_contract.py` covers completion write shapes/idempotency, PO create/list/get/patch/confirm/partial/full receive, OpenAPI refs, prior typed boundaries, and 58/70: **3 passed**. Full suite: **594 passed, 0 failed, 22 warnings in 614.56 seconds**.
- **Frontend/client/persistence impact:** no intended payload or business change. No persistence compatibility cleanup, Alembic migration, backfill, or data rollback is required.
- **Rollback:** hunk-selectively revert `app/schemas/completion.py`, R1.7 route declarations in `app/api/completion.py`, the R1.7 update in `tests/test_operational_page_contract.py`, `tests/test_completion_response_contract.py`, and these documentation hunks. Do not revert prior R1 WIP in shared files.

### R1 phase-close review

- **Status:** COMPLETE — no R1 blocker.
- **Closed by R1:** common errors/OpenAPI; Decision Package; IngredientDemandRun; ProcurementPlanRun; Menu/Product; operational Page<T>; Completion/Purchase Order response boundaries.
- **Technical debt outside current R1 scope:** bootstrap, dashboard, settings-read, import-result, and other lower-risk untyped reads; broad nested Decision diagnostics; service/repository responsibility hotspots.
- **Proposed future change, not accepted architecture:** cursor pagination, generic repository/UoW, CompletionService decomposition, PO aggregate/state-machine abstraction, and broad ORM/public-DTO decoupling.
- **Closure gate:** all R1.1–R1.7 slices complete, 58 paths/70 operations retained, generated OpenAPI reviewed, full suite green, and no unresolved approved-R1 contract blocker remains. **R1 — CONTRACT HARDENING COMPLETE. R2 NOT STARTED.**

## R2 — Decision Intelligence / Narrative Cleanup

Preserve accepted routing:

- Strategy → deterministic DEFAULT.
- Ingredient → deterministic DEFAULT.
- Overall Summary → HYBRID.
- Decision Explanation / What-if → guarded hybrid where useful.
- Excel Mapping → semantic hybrid.

Remove dead or default-unused narrative paths only after runtime/call-site proof. Centralize truly shared display and authority/provenance helpers while keeping domain-specific renderers separate. Reduce unnecessary transport/guard complexity without external payload change. Do not create a giant `DeterministicNarrativeRenderer` God class. Do not switch Overall Summary to deterministic-first without a new accepted ADR.

### R2 entry dependency review — COMPLETE (2026-09-09)

- **No implementation in this review:** production code, routes, OpenAPI, persistence, and runtime behavior are unchanged. Baseline review confirmed **594 passed, 0 failed, 22 warnings in 607.39 seconds** and **58 paths / 70 operations**.
- **Routing confirmed:** Strategy is deterministic by default with reachable, guarded `llm_polish`; Ingredient is deterministic by default with reachable, per-item guarded polish; Overall Summary remains active HYBRID and persists after the deterministic Decision Package commit; Explanation and What-if use grounded Qwen only for wording with task-specific deterministic/template fallbacks. None of these provider paths is dead. Excel Mapping computes a rule result first.
- **Excel routing discrepancy:** `IngestionPipeline.suggest` returns a high-confidence rule result without calling OpenRouter, but direct `POST /api/v1/llm/map-sheet` calls the provider whenever configured. This diverges from the accepted rule-first/ambiguity-only mapping intent. The public `MappingSuggestion` model remains typed, so changing its source/selection behavior requires an explicit contract gate even though no new ADR is needed.
- **Guard inventory:** semantic evidence and its provenance are backend authority; Strategy enforces whole-set reason/entity/numeric/semantic validation; Ingredient enforces exact RiskDetail/evidence/provenance and per-item fallback; Summary enforces schema, evidence-set, numeric, entity, provenance, causal/business, and display validation; Explanation and What-if have separate retrieval/fact, claim/citation, number, causal/mechanism, and raw-machine-code guards. Do not centralize these domain-specific policies merely because they share vocabulary.
- **Transport inventory:** gateway-owned async lifecycle/client pool, task profiles, structured output, retry/timeout, diagnostics, and request-local raw content are still required by active Excel Mapping, Summary, Explanation, What-if, and optional polish paths. `generate_json_sync` is already the shared synchronous bridge for Strategy/Ingredient/Summary. Explanation and What-if locally duplicate the sync/async bridge; this is confirmed technical debt, not yet a safe untested refactor.
- **ADR-013 review:** old persisted assistant compatibility alone is disposable, but missing-assistant `/brief` fallbacks also protect current failed enrichment and current public reads. Keep them until a later slice proves current callers/behavior no longer require them.

### Tentative R2 sequence (provisional)

| Candidate | Classification | Scope and gate |
|---|---|---|
| **R2.1 — Direct map-sheet rule-first parity** | **COMPLETE (2026-09-09)** | API high-confidence mapping now follows the existing import-pipeline no-LLM policy; ambiguous/provider/fallback behavior and the typed `MappingSuggestion` contract are retained. |
| R2.2 — Explanation/What-if synchronous LLM bridge consolidation | COMPLETE (2026-09-09) | Replaced both local sync/async bridges with the existing gateway-aware bridge while retaining task/context/diagnostics/owner-loop/fallback behavior. |
| R2.3 — Proven unreachable narrative fallback cleanup | COMPLETE (2026-09-09) | Removed the overwritten legacy reason-map literal and the unreachable post-return What-if numeric parser block without changing active fallback output. |
| Closed-intent deterministic Explanation routing | NEEDS ADR / PRODUCT DECISION | Current Explanation is guarded LLM where useful; routing CLOSED intents differently is a proposal, not accepted intent. |
| Remove old assistant fallback readers | DEFER | ADR-013 permits old-row cleanup, but current `/brief` fallback semantics are still public/runtime-required. |
| Deterministic-first Overall Summary; remove all Qwen | REJECTED UNDER CURRENT ADR | ADR-006 preserves Summary HYBRID; ADR-002 permits bounded wording integrations. |

### R2.1 — COMPLETE (2026-09-09)

- **Before / after runtime:** direct API built the same rule suggestion as the import pipeline but called OpenRouter whenever configured. A small shared pure policy, `mapping_requires_llm`, now governs both call sites. At the existing threshold, high-confidence API mapping returns that canonical rule result with **zero provider calls**; only below-threshold/ambiguous input continues to `LLMTask.EXCEL_MAPPING`.
- **Contract and fallback preservation:** `POST /api/v1/llm/map-sheet` still returns `MappingSuggestion`. High-confidence output is the canonical rule result (`source="rule"`, `requires_review=false`, `raw_response=null`); low-confidence provider output, recognized-rule fallback (`rule_fallback` plus review metadata), unavailable-provider handling, `503` for unknown unusable rules, request/auth/error behavior, threshold, and mapping values are unchanged.
- **Acceptance evidence:** new direct/pipeline parity coverage proves a configured high-confidence case makes zero calls, an unavailable-provider high-confidence case succeeds identically, ambiguous input calls the existing mapping provider, and ambiguous provider failure preserves the direct safe fallback (**3 passed**). Existing focused OpenRouter map-sheet/task/provider-unavailable tests: **4 passed**. Generated OpenAPI remains `MappingSuggestion`; manifest remains **58 paths / 70 operations**. Full suite: **597 passed, 0 failed, 22 warnings in 510.56 seconds**; `compileall` and `git diff --check` passed.
- **Client/persistence impact:** no public schema/route/status change and no migration, backfill, data cleanup, provider removal, or frontend change.
- **Rollback:** R2.1-only hunks in the direct API, shared policy/import-pipeline policy call, focused tests, and these R2.1 documentation notes. No data rollback; shared-file rollback is hunk-selective because of prior R1 WIP.
- **Explicit non-goals:** no transport lifecycle change, no task-profile/config cleanup, no mapping-schema redesign, no generic LLM abstraction, no narrative routing change, no removal of Qwen. At R2.1 completion, R2.2 and R2.3 had not started.

### R2.2 — COMPLETE (2026-09-09)

- **Before / after mechanics:** Explanation embedded a running-loop check and one-worker thread around `generate_json`; What-if used a separate `_run_async` implementation with the same mechanics. Both now call the pre-existing `app.llm.runtime.generate_json_sync` helper. It preserves the provider-owned owner-loop path by delegating to `provider.generate_json_sync` when available, while retaining the existing async-provider/test-double fallback.
- **Frozen task contract:** both task families keep `LLMTask.DECISION_NARRATIVE`, their existing prompt/input assembly, request-local diagnostic context, OpenRouter task profile, retry/timeout, structured output, raw response capture, and provider call frequency. Explanation's retrieval/grounding/schema/entity/numeric/causal/citation guards and deterministic fallback stay local. What-if's deterministic recomputation, What-if guard, and deterministic fallback stay local.
- **Acceptance evidence:** direct shared-bridge tests cover gateway-owned synchronous invocation and async invocation from an already-running loop. Explanation/What-if success, schema/grounding/provider-failure/no-provider behavior, task/context identity, What-if deterministic-before-narration behavior, OpenRouter behavior, Strategy/Ingredient/Summary smoke, and R2.1 mapping regression: **181 passed**. API/contract What-if regression: **18 passed**. OpenAPI remains `DecisionExplanationResponse` / `WhatIfResponse` at **58 paths / 70 operations**. Full suite: **599 passed, 0 failed, 22 warnings in 184.40 seconds**; `compileall` and `git diff --check` passed.
- **Client/persistence impact:** no public route/schema/status change, business computation change, persistence change, migration, backfill, provider construction, teardown, task-profile, retry, or timeout change.
- **Rollback:** R2.2-only hunks in the two narrative modules, bridge/narrative/What-if tests, and these notes. No data rollback. Shared-file rollback remains hunk-selective because R1/R2.1 WIP is present.
- **Explicit non-goals:** no Explanation routing change, deterministic closed-intent routing, What-if computation change, Strategy/Ingredient/Summary call-site refactor, gateway/provider redesign, guard/fallback deletion, or R2.3 work. At R2.2 completion, R2.3 had not started.

### R2.3 — COMPLETE (2026-09-09)

- **Exact removed fragments:** `DecisionPlanningService._template_explanation` had a four-entry raw reason-code message literal immediately overwritten by `mapping={}` before the loop; the literal could never affect output. What-if `_validate_numbers` returned unconditionally after its active allowed-display check; the subsequent legacy numeric parse/compare block and private `_parse_display_number` had no control-flow or static caller reachability. Only those fragments were removed.
- **Unreachability proof:** neither fragment was wired by settings, dependency injection, dynamic dispatch, persistence reads, or public API compatibility. The active template function is still called by `explain_decision` on its outer failure path and retains its existing generic fallback; a focused test freezes that output with package reason codes present. What-if's active pre-return numeric guard is still called from its claim and answer guards and is covered for authorized and unauthorized mentions. No active provider failure/unavailable/schema/grounding branch was deleted.
- **Active fallback preservation:** Strategy deterministic/default and polish fallback; Ingredient deterministic/per-item/CRITICAL/LIMITED_EVIDENCE behavior; Summary HYBRID deterministic-safe fallback; Explanation provider/validation/template fallback; What-if deterministic-computation/narrative fallback; and Excel Mapping rule-first/provider-fallback/unknown-`503` behavior remain unchanged. Current `/brief` assistant-data fallbacks remain out of scope because they serve current reads/failures.
- **Acceptance evidence:** fallback-focused Strategy/Ingredient/Summary/Explanation/What-if/Mapping/provider/bridge tests: **183 passed**; Decision/API contract regression: **17 passed**. Generated OpenAPI and typed response refs remain unchanged at **58 paths / 70 operations**. Full suite: **601 passed, 0 failed, 22 warnings in 178.70 seconds**; `compileall` and `git diff --check` passed.
- **Client/persistence impact:** no response/schema/source/warning/diagnostic/raw-response/citation/task-routing/provider-call behavior changed. No migration, backfill, persistence schema change, or data cleanup.
- **Rollback:** R2.3-only hunks in `app/services/decision_planning_service.py`, `app/decision_intelligence/what_if_evidence.py`, `tests/test_narrative_fallback_cleanup.py`, and these notes. No data rollback; shared worktree rollback is hunk-selective.

### R2 phase-close review — COMPLETE (2026-09-09)

- **Closure evidence:** the approved current R2 inventory is R2.1 direct map-sheet parity, R2.2 bridge consolidation, and R2.3 proven-unreachable cleanup; all are complete with stable public contracts, 58 paths / 70 operations, and a green full suite.
- **Technical debt outside current R2 scope:** active provider/guard complexity and current `/brief` missing-assistant fallbacks remain current-runtime protections.
- **Proposed future change:** old assistant-reader removal needs a separate current-read contract gate; broad provider/narrative simplification remains unapproved.
- **Requires ADR / product decision:** closed-intent deterministic Explanation routing. Deterministic-first Overall Summary and all-Qwen removal remain rejected under current ADRs.
- **R2 blocker:** none. **R2 — DECISION INTELLIGENCE / NARRATIVE CLEANUP COMPLETE. R3 NOT STARTED.**

## R3 — Canonical Computation Core

Problem: `shelfcash_core` and `shelfcash_forecast` substantially overlap.

Mandatory evidence sequence for each vertical slice: measure parity/callers → identify actual authority → harden a safe boundary where justified → classify semantic divergence or resolve use-case authority by ADR where needed → migrate/remove only after a separate proof that it is safe → regression test → document.

Potential slices: forecast, BOM, inventory simulation, scenario generation, optimization. Do not delete a package at once and do not assume which package wins before runtime/parity evidence.

### R3 entry review - COMPLETE (2026-09-09)

**Phase state:** R1 COMPLETE; R2 COMPLETE; **R3 ENTRY REVIEW COMPLETE; R3 IMPLEMENTATION NOT STARTED.** This was an inspection/documentation task only. ADR-010 remains unchanged.

| Domain | Production caller and terminal implementation | Current authority | `shelfcash_forecast` role / parity |
|---|---|---|---|
| Forecast | `ForecastService -> ExistingForecastCoreProvider -> shelfcash_core.train_forecast_core/predict_demand` | `shelfcash_core` | Optional default-disabled shadow |
| BOM / Ingredient Demand | `DecisionPlanningService.generate_demand -> CoreBomAdapter -> shelfcash_core.bom.engine` | `shelfcash_core` | Duplicate candidate; no package parity |
| Unit conversion | App Decimal boundary plus core BOM/inventory conversion contracts | Split boundary; core owns core execution | Text-equivalent BOM helper, not safe to share yet |
| FEFO / exact simulation / feasibility / risk / selection | Decision Run and What-if: `CoreProcurementAdapter -> shelfcash_core` optimizer, resimulation and critic | `shelfcash_core` | Semantic divergence; no parity |
| Procurement-plan endpoint | `ProcurementPlanningService.build()` | **Split production fact** | No forecast-package runtime caller |
| Scenario computation | Core adapter -> core scenario composition/BOM | `shelfcash_core` | Duplicate candidate; no parity |

`app -> shelfcash_core` is computation authority for forecast, BOM, scenario/BOM, FEFO simulation, optimization and inbound expiry. `app -> shelfcash_forecast` is only the forecast shadow plus Decision Intelligence `DecisionGraph`/`EvidencePackage`/evidence/retrieval helper reuse. That helper reuse is not business authority. No runtime core-to-forecast or forecast-to-core imports were found.

#### HISTORICAL R3 ENTRY FACT - ADR-010 procurement scope

Forecast and the Decision Run/What-if M1-M5 path match ADR-010. Typed `POST /stores/{store_id}/forecast-runs/{forecast_run_id}/procurement-plans` invokes `ProcurementPlanningService.build()` and persists that application-layer planner's real result; it is not a shadow. `CoreProcurementAdapter` itself reuses that service only for ORM-state reads (`_lots`, `_terms`, `_open_inbound`). The prior ADR-010 scope ambiguity is now resolved by ADR-014: operational and Decision procurement are intentionally separate authorities.

Do not reinterpret or edit ADR-010 in an implementation slice. ADR-014 accepts the existing authority boundary. Do not migrate the endpoint merely to make package ownership look uniform.

#### Confirmed debt, parity and semantic gates

- The packages have 21 byte-identical relative Python files, 60 divergent same-path files and three core-only files. After package-name normalization, 31 files are identical. File similarity is not semantic equivalence.
- Forecast parity is **STRONG** for the synthetic H1/H3/H7 qualification fixture: feature/reconstruction, calibration metrics, zero numerical drift, closed-calendar/crossing behavior and short-history warnings. Shadow failure cannot affect production persistence. Its runtime comparator records numerical drift but does not make numerical drift incompatible.
- BOM, Ingredient Demand, FEFO, Procurement, Strategy and Risk have **NONE** cross-package parity. Core FEFO adds expiry mode/non-perishable/missing-receipt semantics; core resimulation adds capacity context and separate risk simulation; core selects lowest exact-valid cost then strategy name while forecast uses a fixed preference. They are similar-but-semantically-different, not merge-ready.
- Freeze EOD cutoff D -> planning D+1 through D+horizon, units, float/Decimal boundaries, quantiles, MOQ/pack rounding, inclusive expiry, failures/warnings/reason codes and all ADR-012 manager-safe meanings. Parity must include failure semantics, not only values.

| Domain | Current golden/edge evidence | Required future parity gate |
|---|---|---|
| Forecast | H1/H3/H7, missing day, zero observation, stockout, closed calendar, crossing, short history | exact package fixture; add zero-history/cold-start plus response/persistence assertions |
| BOM / Ingredient Demand | Core adapter/DTO regressions only | missing recipe, combo, unit mismatch, duplicate ingredient, contribution/warning/failure wire parity |
| FEFO | Core chronology/inventory tests only | expiry inclusivity, same expiry, non-perishable, missing receipt, shortage/waste/reason parity |
| Procurement / Strategy | Legacy planner and core each tested independently | raw/order/MOQ/pack/cost, supplier/delivery/budget, feasibility, tie/no-feasible, stress semantics |

#### Ranked provisional sequence

1. **R3.1 Forecast production/shadow boundary hardening - COMPLETE (2026-09-09).** The explicit provider seam is frozen and verified without changing forecast authority.
2. **Procurement-plan convergence - NOT A CURRENT MIGRATION TARGET.** ADR-014 accepts distinct operational and Decision procurement authorities. Preserve their intentional semantic divergence; any future convergence needs its own approved architecture and contract-migration gate.
3. **BOM/Ingredient Demand package parity qualification - NEEDS PARITY TESTS.** Core is active but no cross-package parity covers recipe/unit/contribution semantics.
4. **FEFO/exact simulation/strategy parity qualification - DEFER.** Expiry, risk/capacity and selection semantics differ materially.
5. **Shared helper extraction/package consolidation - NEEDS ADR / DEFER.** It could weaken shadow independence and is not itself proof of business truth.

#### RECOMMENDED FIRST R3 SLICE - R3.1 Forecast production/shadow boundary hardening

- **Current authority/alternate:** production is `ExistingForecastCoreProvider -> shelfcash_core.train_forecast_core/predict_demand`; alternate is `ShelfCashForecastProvider -> shelfcash_forecast` shadow only. Shadow never selects, overwrites or persists production rows.
- **Callers/contracts:** ForecastService train/predict HTTP flow; downstream persisted forecast consumers are Ingredient Demand, Procurement Plan and Decision Run. Freeze `ForecastResponse`/legacy forecast surfaces and indirect R1.3/R1.4/DecisionPackage consumers; paths/operations remain 58/70.
- **Semantics/tests:** canonical history through D, targets D+1..D+horizon, H1/H3/H7, quantile order/precision, calendar/cold-start warnings, model/artifact separation and production-only persistence. Retain exact qualification and shadow-isolation tests; add zero-history/cold-start and public response/persisted-row checks; run forecast API/integration plus Ingredient Demand/Decision regressions.
- **Rollback/non-goals:** revert provider-boundary/parity-test hunks only. No algorithm, model-artifact, route, DTO, DB, Decision Intelligence, package deletion, shared parity-critical helper, or procurement caller migration.

#### R3.1 completion evidence

- **Before/after boundary:** before and after, `ExistingForecastCoreProvider -> shelfcash_core` is the production computation authority; `ShelfCashForecastProvider -> shelfcash_forecast` is optional/default-disabled comparison only. The current service persists/returns production output before invoking shadow observation. R3.1 adds regression proof, not a provider, algorithm, configuration-default, package, schema or API change.
- **Parity:** H1/H3/H7 remain exact on the existing synthetic qualification fixture. Cold-start is the one-observation unseen product path and matches exactly, including `UNSEEN_PRODUCT`/`INSUFFICIENT_HISTORY`. Empty qualifying sales are a frozen `DataValidationError` before prediction for both independent providers; this is an expected failure-boundary MATCH, not a synthetic zero forecast.
- **Non-interference:** a public `POST /api/v1/forecasts` acceptance test compares shadow OFF versus ON for identical request input. Its canonical response fields and persisted `ForecastRunModel`/`ForecastPredictionModel` fields are identical; production is called once in each run, while shadow is called only when enabled. Existing injected failure coverage proves a shadow exception logs `forecast_shadow_inference_failed` after core success without changing canonical completion or rows.
- **Public/persistence/downstream gates:** forecast response shape/status/dates/float quantiles/model metadata/warnings are unchanged; no OpenAPI or persistence schema/migration/backfill change. OpenAPI remains 58 paths/70 operations. Focused forecast/Ingredient Demand/Planning/Decision/What-if/API regressions pass.
- **Verification:** forecast boundary/qualification **8 passed**; focused downstream/API **18 passed**; OpenAPI/route **6 passed**; full suite **602 passed, 0 failed, 22 warnings in 163.49s**; compile and `git diff --check` pass.
- **Rollback/non-goals:** only R3.1 test/documentation hunks may be reverted. Do not change forecast algorithms, formulas, models, provider selection/defaults, package dependency direction, public contracts, persistence, Decision Intelligence, procurement, BOM, FEFO, strategy/risk, or ADR-010.

#### R3.2 Post-Forecast computation parity gate - COMPLETE (2026-09-09)

- **Reviewed authority:** Ingredient Demand is `DecisionPlanningService.generate_demand -> CoreBomAdapter -> shelfcash_core.bom.engine`. Decision Run/What-if are core adapter -> core scenario composition, exact FEFO, optimization, critic/risk/selection. Typed Procurement Plans remains `DecisionPlanningService.generate_plans -> ProcurementPlanningService.build -> InventorySimulationService`; it is a current public/persisted authority, not a shadow. `RecipeBomService` has no runtime production caller.
- **Semantic matrix:** BOM numeric output/date/unit/warnings match on the hand-verifiable fixture, but core retains `recipe_line_id` contribution provenance that forecast lacks: full Ingredient Demand is **SEMANTIC_DIVERGENCE**, not package parity. Unit conversion has an exact core/forecast helper pair but a distinct Decimal app boundary. FEFO matches only for known-expiry happy path; core's non-perishable FIFO and optional-received-date contracts differ from forecast's unknown-expiry/required-received-date behavior: **SEMANTIC_DIVERGENCE**. Exact simulation, feasibility, procurement, strategy and risk are **SEMANTIC_DIVERGENCE**: legacy uses a different daily simulator and balanced-first recommendation, whereas core uses exact FEFO/critic and lowest exact-valid cost then strategy-name. Scenario composer and ingredient-demand pipeline are implementation-equivalent but have no production alternate caller.
- **Characterization tests:** `tests/test_post_forecast_parity_characterization.py` proves BOM numeric/date/unit/warning equality plus `recipe_line_id` divergence; FEFO known-expiry equality; and intended non-perishable/unknown-expiry warning divergence. It does not make shadow output authoritative or share computation code. Relevant existing BOM/inventory/procurement/strategy regression set totals **45 passed, 1 warning in 13.51s**.
- **Historical procurement ADR finding:** R3.2 correctly identified an ambiguity, not a mechanical caller migration. ADR-014 now resolves it by accepting the legacy endpoint as Operational Procurement Authority and Decision Run/What-if core as Decision Procurement Authority. Their conversion, MOQ/pack/supplier/delivery, shelf-life/inbound, FEFO/capacity, feasibility and selection divergence is intentionally preserved.
- **Ranked provisional candidates:** (1) **R3.3 BOM shadow contribution-provenance parity hardening — READY FOR ITS OWN GATE**: shadow-only `recipe_line_id` contract parity, then missing-recipe/duplicate-line/unit-error characterization; no caller migration, API/persistence/ADR impact. (2) Unit-conversion authority review — **DEFER**: no safe runtime caller seam and app Decimal/core float semantics differ. (3) FEFO/exact simulation — **DEFER**: semantic divergence is intentional/high-risk. (4) Procurement endpoint reconciliation — **NEEDS ADR + PARITY + CONTRACT TESTS**. (5) package merge/shared algorithm extraction — **DEFER / NEEDS ADR**.
- **Current update to the historical ranking:** ADR-014 makes procurement endpoint reconciliation **NOT A CURRENT MIGRATION TARGET**. The pre-decision `NEEDS ADR + PARITY + CONTRACT TESTS` status is retained above solely as historical R3.2 evidence.
- **R3.3 implementation gate and rollback:** current authority is `CoreBomAdapter -> shelfcash_core.bom.engine`; alternate is `shelfcash_forecast.bom` adapter/engine. Freeze D+1 target date, P25/P50/P75, canonical unit, rounding, missing-recipe/unit errors, warnings and source provenance including `recipe_line_id`; protect `IngredientDemandRunResponse` contribution string/numeric distinctions and `IngredientDemandRun` prediction persistence. Required tests: direct package parity for normal/missing-recipe/duplicate-line/unit-error plus existing Ingredient Demand API/persistence regression. Likely rollback: only shadow BOM contracts/adapter/engine and its parity tests. Non-goals: CoreBomAdapter caller changes, RecipeBomService cleanup, FEFO, procurement, DecisionPlanningService decomposition, public contracts, DB, and ADR-010.

#### R3.3 BOM shadow contribution-provenance parity hardening - COMPLETE (2026-09-09)

- **Before/after:** production remains `CoreBomAdapter -> shelfcash_core.bom.engine`; the alternate has no runtime production caller. Before, shadow `adapt_recipes()` lost the supplied source identity, so its `RecipeRecord` and then `IngredientDemandSource` omitted `recipe_line_id`. After, shadow only passes that existing identity through its own contract, adapter and engine. No core algorithm, adapter authority, shared calculation helper, runtime selection, public DTO, schema, persistence or ADR changed.
- **Truthful provenance contract:** `recipe_line_id` is a `str | None` in both pure package contracts. It is the actual `RecipeLineModel.recipe_line_id` primary key for canonical application recipe input and is not generated when missing. Nullable remains solely for the already-supported pure-DataFrame boundary where a source row genuinely has no persisted line identity. The app's canonical `CoreBomAdapter.recipe_frame()` always supplies the non-null ORM ID.
- **Parity/characterization:** normal recipe now matches core on D+1 target, P25/P50/P75, unit, warnings and complete contribution source. The direct duplicate-line fixture preserves two distinct IDs/contributions in both engines; real persistence disallows this shape with `uq_recipe_line_ingredient`, so it is an engine characterization rather than a new ORM behavior. Missing active recipe remains matching `MISSING_RECIPE`/no prediction; incompatible product/yield units retain matching structured conversion failures. No tolerance or ordering weakening was introduced.
- **Wire/persistence/non-authority gates:** current core API keeps prediction quantiles as JSON numbers and contribution `product_*`/`ingredient_*` quantiles as strings, including its existing `recipe_line_id`; `IngredientDemandRunResponse`, canonical `IngredientDemandRun` prediction persistence and OpenAPI are unchanged. A monkeypatched shadow BOM failure does not affect canonical Ingredient Demand POST, proving it remains unreferenced by the production call path. No shadow persistence was introduced.
- **Verification:** direct and API/persistence/Decision/narrative/OpenAPI BOM set passed; full suite **608 passed, 0 failed, 22 warnings in 177.15s**. OpenAPI/route manifest remains **58 paths / 70 operations**; compile and `git diff --check` pass.
- **Rollback/non-goals:** revert only the shadow BOM contract/adapter/engine hunks, R3.3 parity/API assertions and these notes. No data rollback/migration. Do not change `CoreBomAdapter`, RecipeBomService, unit conversion, FEFO, procurement, strategy/risk, DecisionPlanningService, package layout, public contracts or ADR-010. At this historical checkpoint R3.4 remained a candidate only.

#### R3.4 Procurement authority ADR clarification / migration gate review - COMPLETE (2026-09-09)

- **CURRENT FACT - callers and pipelines:** the typed public endpoint is `planning.generate_plans -> DecisionPlanningService.generate_plans -> ProcurementPlanningService.build -> InventorySimulationService`; it writes `ProcurementPlanRun`, `ProcurementPlan`, `ProcurementPlanLine`, `metrics_json` and `daily_projections_json`, then reads them back as `ProcurementPlanRunResponse`. The legacy `/plan-runs` path calls `generate_plans` transitively. Decision Run and What-if use `DecisionPlanningService.generate_decision/what_if_decision -> CoreProcurementAdapter.optimize -> shelfcash_core` candidate generation, exact FEFO resimulation, critic, feasibility/risk and recommendation. The core adapter only uses legacy `_lots`, `_terms` and `_open_inbound` state readers.

| Concern | Legacy `/procurement-plans` | Core Decision / What-if | Classification |
|---|---|---|---|
| Demand and snapshot | Persisted ingredient quantile per named strategy; inventory at EOD D | Same persisted demand boundary and EOD D state, converted to core contracts/design scenarios | ADAPTER DIFFERENCE |
| Open POs / expiry | Legacy inbound dictionaries and shelf-life policy | Same raw reader, but `resolve_inbound_expiry` plus expiry-tracking contracts | SEMANTIC DIVERGENCE |
| Supplier / MOQ / packs | Pre-select one term; Decimal ceiling, MOQ then pack rounding | Optimizer considers all offers and feasible packs | SEMANTIC DIVERGENCE (output parity unproven) |
| Delivery / dates | Order and arrival from D+1, lead time and calendar | Core offers begin D+1 and support calendar delivery | ADAPTER DIFFERENCE; D+1 matches, output parity unproven |
| Shelf life / FEFO | Shelf-life candidate policy and legacy daily simulator | Expiry-aware exact FEFO resimulation/critic | SEMANTIC DIVERGENCE |
| Budget / capacity | Budget resolved at cutoff D; storage may be evaluated/violated | Budget resolved at D+1; store capacity may be `not_evaluated` | SEMANTIC DIVERGENCE |
| Feasibility / risk | Legacy violations, warnings and expiry risk | Exact critic, separate risk/stress semantics | SEMANTIC DIVERGENCE |
| Recommendation / tie | Feasible `balanced` first, then request order | Lowest exact-valid cost, then strategy name | SEMANTIC DIVERGENCE |
| Daily projections / reason codes | Typed legacy daily/lot trace persisted in plan run | Decision-package simulation/critic evidence; no plan-run adapter | SEMANTIC DIVERGENCE |

- **Characterization evidence:** `test_procurement_planner.py`, `test_inventory_constraints.py` and `test_inventory_simulation.py` freeze legacy MOQ/pack/supplier/order-date, shelf-life, capacity and projection semantics. `test_optimization_diagnostics.py`, `test_infeasibility_diagnostics.py`, `test_risk_aware_optimization.py`, `test_stochastic_risk_evaluation.py` and `test_planned_inbound_shelf_life.py` freeze core optimizer/critic, FEFO, delivery, budget and risk semantics. `test_planning_api.py`, `test_forecast_integration.py`, `test_decision_api.py`, `test_what_if_no_llm_regression.py`, `test_api_contract_routes.py` and `test_error_openapi_contract.py` freeze public callers/contracts. Selected suite: **77 passed, 15 warnings in 40.55s**; no test or production source was changed by R3.4.
- **Historical migration gate:** R3.4 classified a caller replacement as **NOT SAFE TO MIGRATE** pending an ADR because it can change recommendation, feasibility/status, quantities/cost, warnings/reason codes, daily projections and the persisted R1.4 read model. That evidence remains the future convergence safety baseline.
- **ADR resolution:** ADR-014 accepts **Option B - intentional dual authority**. `/procurement-plans` owns operational `ProcurementPlanRun` semantics; Decision Run/What-if core owns Decision semantics. This is accepted architecture, not a pending package-authority conflict.
- **Current migration policy:** procurement endpoint reconciliation is **NOT A CURRENT MIGRATION TARGET**. Option A (core for all surfaces) and Option C (core-backed compatibility adapter) remain future proposals. Any convergence requires a separate approved decision and the historical R3.4 semantic/public/persistence/client/migration/rollback gate.
- **Historical ADR options:** before approval, A was core for every procurement surface, B was intentional dual authority, and C was a core-backed compatibility adapter. **B is now ACCEPTED by ADR-014.** A and C remain future proposals; neither is an approved migration target.
- **Non-goals/rollback:** no caller migration, planner/core algorithm change, FEFO/MOQ/supplier/budget/strategy change, DTO/OpenAPI/schema/migration, service decomposition, legacy retirement, package consolidation or ADR edit. This review changed only these R3.4 documentation hunks; rollback is hunk-selective and needs no data rollback.

#### R3.5 Remaining computation duplication classification and safe-removal gate - COMPLETE (2026-09-09)

- **Review method and threshold:** the current module inventory is `shelfcash_core` 84 Python modules, `shelfcash_forecast` 131, with 81 common relative paths (21 byte-identical; 60 divergent). Similarity is discovery evidence only. A `PROVEN_DEAD_DUPLICATE` must have zero runtime, configuration, public export, Decision-Intelligence, test-oracle, CLI/script, dynamic-import and accepted-ADR role. No reviewed module meets every condition.

| Domain / module group | Caller/import evidence | Classification | Safe-removal finding |
|---|---|---|---|
| Core forecast, core BOM, core Decision scenario/optimization/FEFO/critic/risk | `ForecastService`, `CoreBomAdapter`, `CoreProcurementAdapter`, Completion and Decision/What-if callers | `PRODUCTION_AUTHORITY` | Keep. |
| Forecast provider and forecast BOM | Config-gated `ShelfCashForecastProvider`; R3.1 H1/H3/H7/zero-history/cold-start and R3.3 provenance qualification tests | `INTENTIONAL_SHADOW_PARITY` | Keep independent; its absence from canonical writes is intentional. |
| `/procurement-plans` planner and `InventorySimulationService` | `DecisionPlanningService.generate_plans -> ProcurementPlanningService.build`; typed plan-run persistence/read route | `ACCEPTED_ALTERNATE_AUTHORITY` | Keep under ADR-014; not a convergence/removal target. |
| Core versus forecast inventory/FEFO | R3.2 characterization: non-perishable `not_required`, unknown expiry, FIFO and warning contracts differ | `SEMANTIC_DIVERGENCE` | Keep; no mechanical merge/removal. |
| Forecast Decision-Intelligence computation gateway, optimizer and critic | Reached from the eager forecast Decision-Intelligence export graph; its contracts are imported by the app adapter | `ACTIVE_RUNTIME_HELPER` | Keep; it is an active import-time dependency. |
| Core versus forecast optimizer, strategy, risk and critic relationship | Core is used by `CoreProcurementAdapter`; forecast implementation has distinct selection/risk semantics | `SEMANTIC_DIVERGENCE` | Keep independent; semantics must not be silently collapsed. |
| Forecast scenario composer/BOM/pipeline | No direct app/test/config call found; package and scenario `__init__` re-export public scenario functions | `PROVEN_UNUSED_CANDIDATE` | Not dead: supported-export status and independent-role decision are still required. |
| Decision-Intelligence contracts/evidence/retrieval | `app/decision_intelligence/adapter.py` imports `DecisionGraph`, `EvidencePackage`, `EvidenceCollector`, `StructuredLocalRetriever`, `build_retrieval_context` | `ACTIVE_NON_COMPUTATION_DEPENDENCY` | Keep; package deletion is blocked. |
| Decision-Intelligence agents/approval/computation gateway/regret/what-if | Eagerly imported by `shelfcash_forecast.decision_intelligence.__init__` when app imports its contracts | `ACTIVE_RUNTIME_HELPER` | Keep until the import/export boundary is separately reviewed; no HTTP-only inference. |
| Decision-Intelligence `evaluation` and `evaluation_part2` | No direct app/test/config caller found in reviewed roots; each has public `__all__` evaluation API | `PROVEN_UNUSED_CANDIDATE` | Not dead; `NEEDS_CALLER_PROOF` of non-supported public/CLI use before a leaf removal. |
| Core unit conversion | Core BOM/forecast and Decision contracts import the core conversion helper | `PRODUCTION_AUTHORITY` | Keep as the production computation contract. |
| App Decimal conversion boundary | Operational/API paths use its aliases and error boundary | `ACTIVE_RUNTIME_HELPER` | Keep; it has distinct Decimal/error semantics. |
| Forecast unit conversion | Forecast/BOM shadow paths import the independent helper | `INTENTIONAL_SHADOW_PARITY` | Keep; shared computation would weaken the parity oracle. |
| `RecipeBomService` | No production caller; retained legacy/test coverage from prior review | `TEST_ONLY_BUT_VALUABLE` | Out of R3.5 scope; no cleanup recommendation. |

- **Candidate ranking:** forecast/BOM shadow cleanup is `INTENTIONAL_DUPLICATION_KEEP`; operational procurement is `INTENTIONAL_DUPLICATION_KEEP`; FEFO and strategy/risk cleanup is `SEMANTIC_DIVERGENCE_KEEP`; forecast scenario and both Decision-Intelligence evaluation packages are `NEEDS_CALLER_PROOF`; package merge/delete and shared parity-critical algorithm extraction are `DEFER`. **No item is `READY_FOR_SAFE_REMOVAL`.**
- **Recommended next slice:** **NO SAFE REMOVAL CANDIDATE.** Do not create or start R3.6 from this review. A future leaf-removal proposal must demonstrate non-supported public API status, zero configuration/dynamic/CLI use and zero oracle value, then retain a small hunk-selective rollback and contract regression gate.
- **Non-goals:** no production code, caller migration, package merge, module deletion, FEFO/strategy/risk convergence, `/procurement-plans` migration, DTO/OpenAPI/persistence change, DecisionPlanningService decomposition or ADR update. Forecast/BOM independent parity code and ADR-014 dual procurement authority remain protected.
- **Verification:** caller/import/config/export searches; `tests/test_forecast_shadow.py`, `tests/test_forecast_parity_qualification.py`, `tests/test_post_forecast_parity_characterization.py`, `tests/test_decision_intelligence.py`, `tests/test_semantic_evidence.py`, `tests/test_api_contract_routes.py`: **38 passed, 1 warning in 14.82s**. Runtime manifest is **58 paths / 70 operations**. No full suite was necessary for documentation-only R3.5; latest full evidence is **608 passed, 0 failed** from R3.3. `git diff --check` passed.
- **Rollback:** R3.5 is documentation-only; revert only its hunk-selective `CURRENT_STATE.md` and `REFACTOR_PLAN.md` entries. No data rollback or migration exists.

### R3 phase-close review / R3.6 - COMPLETE (2026-09-09)

- **Exit decision:** **R3 COMPLETE. R3 BLOCKER: NONE.** R3's purpose was authority/parity/divergence evidence, not a forced one-package end state. Forecast and BOM authority/shadow seams are explicit and protected; procurement ambiguity is resolved by ADR-014; FEFO and strategy/risk differences are explicitly retained semantic divergence; R3.5 proved no safe removal candidate was left half-done.
- **Final authority:** `ExistingForecastCoreProvider -> shelfcash_core` is Forecast production; `ShelfCashForecastProvider -> shelfcash_forecast` is optional non-authoritative shadow. `CoreBomAdapter -> shelfcash_core.bom.engine` owns BOM/Ingredient Demand; forecast BOM is provenance-qualified parity shadow. `ProcurementPlanningService.build -> InventorySimulationService` owns operational `ProcurementPlanRun`; `CoreProcurementAdapter -> shelfcash_core` owns Decision Run/What-if procurement, exact FEFO, critic, feasibility, risk, scenarios, strategy and recommendation. Do not silently unify the two procurement authorities.
- **Closure evidence:** forecast H1/H3/H7, zero-history and cold-start remain exact; shadow ON/OFF and shadow-failure non-interference preserve canonical persistence/public behavior. BOM normal/duplicate-line/missing-recipe/unit-failure/D+1 tests retain numeric/date/unit/warning/provenance qualification, including truthful `recipe_line_id`. Representative R3 regressions: **57 passed, 1 warning in 23.69s**. Full suite: **608 passed, 0 failed, 22 warnings in 206.51s**. Runtime OpenAPI remains **58 paths / 70 operations**; compile and `git diff --check` pass.
- **Preservation:** no R3 exit API/OpenAPI/DTO/persistence-schema/migration/backfill change. `ForecastRun`, `IngredientDemandRun`, operational `ProcurementPlanRun`, and Decision `DecisionRun` retain their current write/read ownership. R2 narrative architecture is unchanged.

| Residual item | Classification | Future handling / owner |
|---|---|---|
| Independent Forecast shadow | INTENTIONAL DUPLICATION | Retain for drift detection; reconsider only with an approved shadow-role decision. |
| Independent BOM shadow | INTENTIONAL DUPLICATION | Retain for independent contribution-provenance qualification; do not share parity-critical algorithm. |
| Operational vs Decision Procurement | ACCEPTED ARCHITECTURE | ADR-014; convergence is not currently planned and needs a separate approved architecture/contract migration. |
| Core/forecast FEFO | SEMANTIC DIVERGENCE | Separate semantic review only; no mechanical merge. |
| Strategy/Risk/optimizer semantics | SEMANTIC DIVERGENCE | Separate business-semantics review only; no automatic parity target. |
| Forecast scenario public exports | PROVEN_UNUSED_CANDIDATE | R5 or a separate package-public API/caller review before any leaf removal. |
| Decision-Intelligence evaluation public exports | PROVEN_UNUSED_CANDIDATE | R5 or a separate package-public API/caller review before any leaf removal. |
| App use of forecast Decision-Intelligence contracts/evidence/retrieval | TECHNICAL DEBT | Active non-computation dependency; future package-boundary review, not package deletion. |
| Package consolidation | DEFERRED ARCHITECTURE DECISION | Requires an explicit end-state decision and independent safety case. |

- **R4 handoff/readiness:** ADR-015 now accepts incremental focused use-case decomposition with `DecisionPlanningService` permitted as a temporary facade. Exact implementation boundaries remain slice-driven migration guidance, not frozen class layout. R4 must preserve `CoreBomAdapter` authority; Operational Procurement Authority; Decision Procurement Authority; Decision Package/Brief/What-if contracts; deterministic backend business authority; and R2 narrative routing. It must not use service decomposition to converge procurement semantics or move business truth into an LLM.
- **Non-goals/rollback:** no R3.6 production change, computation migration, module deletion, package merge, ADR edit, persistence redesign or R4 implementation. Rollback is documentation-only and hunk-selective.

| Proposal | Disposition | Reason |
|---|---|---|
| Keep core production / forecast shadow for forecast | KEEP | Runtime and ADR-010 agree; this is the strongest parity seam. |
| Decision Intelligence helper/contract extraction | DEFER | It is helper reuse; R2 is closed. |
| Typed procurement-plan migration to core | NOT A CURRENT MIGRATION TARGET | ADR-014 accepts separate operational and Decision authorities; future convergence needs a new approved gate. |
| Merge/delete/rename packages | REJECT for this entry | No end-state decision; broad deletion bypasses evidence. |

## R4 — Decision Planning Use-Case Decomposition — COMPLETE (2026-09-09)

### R4 entry review - COMPLETE (2026-09-09)

- **CURRENT FACT - responsibilities and callers:** one singleton-like application facade, `DecisionPlanningService(session_factory, settings, llm_provider)`, is wired in `app.main`. It owns typed planning routes in `app/api/planning.py`: Ingredient Demand generate/read, Operational Procurement generate/read, Decision Run generate/read, Brief, Explanation and What-if. It also owns active compatibility routes in `app/api/completion.py`: legacy `/plan-runs` create/metadata/result. Internal composition includes `generate_decision -> generate_demand(refresh=True)`, legacy-plan creation -> demand/plans, legacy result -> plan read, Explanation -> Brief/package reads, and Decision core commit -> assistant enrichment.

| Current responsibility | Method(s) | Public contract / caller | Transaction and authority |
|---|---|---|---|
| Ingredient Demand command/query | `generate_demand`, `get_demand` | Typed Ingredient Demand POST/GET | One session commits completed/blocked `IngredientDemandRun`; `CoreBomAdapter -> shelfcash_core`. |
| Operational Procurement command/query | `generate_plans`, `get_plans` | Typed Procurement Plans POST/GET | Running `ProcurementPlanRun` commits before operational build; completion/failure commits later; `ProcurementPlanningService -> InventorySimulationService`. |
| Legacy plan compatibility | `create_legacy_plan`, `get_legacy_plan_metadata`, `get_legacy_plan_result` | Active legacy `/plan-runs` POST/GET/result | Multi-session compatibility row/recommendation lifecycle; delegates only to Operational Procurement. |
| Decision command/query | `generate_decision`, `get_decision`, `_decision_package` | Typed Decision Run POST/GET | Refreshes core BOM demand, commits deterministic `DecisionRun` package, then separately enriches assistant metadata; `CoreProcurementAdapter -> shelfcash_core`. |
| Brief and assistant enrichment | `get_decision_brief`, `_generate_and_persist_overall_summary` | Typed GET Brief / internal post-commit call | Brief is read-only; enrichment is a separate post-core-commit write. R2 presentation/fallback modes apply. |
| Explanation | `explain_decision`, `_require_decision_ingredient`, `_template_explanation` | Typed Explanation POST | Read-only semantic evidence/provider/fallback path; no Decision package mutation. |
| What-if | `what_if_decision`, `_select_hypothetical_strategy` | Typed What-if POST | In-memory hypothetical M1-M5 recomputation plus guarded wording; no persisted hypothetical DecisionRun. |
| Shared validation/coordination | `_forecast`, idempotency/audit calls | Internal | Shared forecast/status/store reader, direct ORM/repository coordination. |

| Use case | Session owner | Commit boundary | Rollback/failure behavior | Post-commit work |
|---|---|---|---|---|
| Generate Ingredient Demand | Service session factory | Completed or blocked demand run | `PlanningError` persisted as blocked then re-raised | Typed read response. |
| Generate Operational Procurement Plans | Service session factory | Commit running run before planner; commit result or terminal failure | Blocked/failed `ProcurementPlanRun` persisted | Typed plan-run read response. |
| Generate Legacy Plan | Service session factory plus nested demand/plan calls | Running compatibility row, then terminal legacy row/recommendations | Blocked/failed `PlanRun` persisted | Legacy metadata/result reads. |
| Generate Decision Run | Service session factory plus demand refresh | Deterministic `DecisionRun.package_json` commits first | Core optimize failure raises; assistant failure logs only | `_generate_and_persist_overall_summary` has its own commit. |
| Read Decision / Demand / Plans | Service session factory | None | Read errors only | None. |
| Build Brief / Explain | Service session factory plus provider calls | None | Guarded deterministic/legacy fallback where applicable | None. |
| Evaluate What-if | Service session factory | None | Hypothetical failure is returned as planning error | In-memory package and guarded narrative only. |

- **Dependency/coupling map:** `factory` is used by every responsibility; `settings` is needed by Decision mode/scenarios and narrative modes; `llm_provider` is needed only by post-commit assistant enrichment, Brief polish/fallback construction, Explanation and What-if narration. `PlanningRepository`/direct ORM are mixed persistence readers; R6 owns any repository/UoW cleanup. `CoreBomAdapter` is demand authority; `ProcurementPlanningService` is Operational Procurement authority; `CoreProcurementAdapter` is Decision Procurement authority. Its state readers (`_lots`, `_terms`, `_open_inbound`) are shared canonical-input readers only, not a shared procurement policy.
- **Coupling assessment:** HIGH - `generate_decision` (fresh demand, core optimizer, package persistence, idempotency, second assistant write) and `create_legacy_plan` (compatibility lifecycle plus nested Operational flows). MEDIUM - Brief, Explanation and What-if because of immutable-package/evidence/narrative coupling. LOW - direct read methods, although their frozen DTO serialization must remain exact. The service itself has no mutable cache; per-request adapters are session-scoped.

| Proposed use case | Current methods | Status | Reason |
|---|---|---|---|
| `GenerateIngredientDemand` / `ReadIngredientDemand` | `generate_demand`, `get_demand` | KEEP_TOGETHER | Command status/idempotency/persistence is frozen; query is structurally small but future split needs the common architecture decision. |
| `GenerateOperationalProcurementPlans` / `ReadOperationalProcurementPlans` | `generate_plans`, `get_plans` | KEEP_TOGETHER | Must retain the separately accepted Operational Procurement authority and two-commit lifecycle. |
| Legacy Operational Plan Compatibility | `create_legacy_plan`, legacy reads | DO_NOT_SPLIT | Active public compatibility facade; R5 owns lifecycle changes. |
| `GenerateDecisionRun` | `generate_decision`, `_decision_package`, enrichment call | KEEP_TOGETHER | Core commit != assistant post-commit enrichment is a high-risk transaction gate. |
| `ReadDecisionPackage` | `get_decision` | R4.1 COMPLETE | Structurally read-only, low coupling, and extracted behind the existing facade without contract or transaction drift. |
| `BuildDecisionBrief` | `get_decision_brief` | R4.3 COMPLETE | Extracted focused Brief orchestration behind the existing facade; builders/providers remain in their existing ownership. |
| `ExplainDecision` | `explain_decision` | R4.5 COMPLETE | Extracted focused read-oriented orchestration behind the existing facade; Brief/raw readers and R2 narrative/provider ownership remain unchanged. |
| `EvaluateWhatIf` | `what_if_decision` | KEEP_TOGETHER | Baseline read, M1-M5 hypothetical computation, comparison and guarded wording form one application intent. |

- **ACCEPTED TARGET DIRECTION (ADR-015):** `DecisionPlanningService` may be incrementally decomposed into focused application use-case services and may remain a temporary route/compatibility facade. Exact candidate names, class count, final composition-root shape and final route wiring are intentionally not frozen. Do not introduce ambiguous `GenerateProcurementPlans`; Operational Procurement and internal Decision Procurement retain their separate ADR-014 authority models.
- **Architecture gate:** **RESOLVED by ADR-015.** R4 is active and eligible for controlled, one-use-case slices, not a big-bang rewrite. Every extraction must preserve ADR-007 contracts; ADR-010 computation authority; ADR-014 procurement separation; current transaction, rollback, idempotency, run-status and failure-persistence behavior; deterministic Decision Run commit before separate assistant enrichment; active compatibility; and R2 narrative routing. Persistence/UoW redesign remains R6 and legacy lifecycle remains R5.
- **R4.1 candidate:** `ReadDecisionPackage` was the first structurally low-risk candidate: `get_decision`, only GET Decision Run callers, frozen `DecisionPackage`, one read-only session, no idempotency or computation, and rollback limited to a new use-case file, façade delegation, composition hunk and read-contract tests. It is completed below; this remains migration guidance, not a frozen ADR class requirement.
- **Preservation/non-goals:** no public route/DTO/OpenAPI change (still **58 paths / 70 operations**), persistence/migration/UoW change, computation-authority change, procurement convergence, narrative routing change, package merge or R4 extraction. Active legacy compatibility remains in place.
- **Verification:** targeted planning/procurement/inventory/Decision/Brief/Explanation/What-if/OpenAPI set: **40 passed, 1 warning in 62.42s**. Latest full-suite evidence remains R3.6: **608 passed, 0 failed, 22 warnings in 206.51s**. This Entry changed documentation only.

### R4.1 — ReadDecisionPackage extraction — COMPLETE (2026-09-09)

- **Before / after:** `DecisionPlanningService.get_decision` directly owned a factory-session ORM read, `DECISION_RUN_NOT_FOUND` exception, and `package_json` decoding. It now remains as the stable facade method and delegates to focused `ReadDecisionPackage.read` at `app/services/decision/read_decision_package.py`.
- **Scope and invariants:** the new boundary reads one persisted `DecisionRunModel` package by ID through the same session factory and owns no write, computation, narrative, idempotency, audit, procurement, or compatibility-lifecycle behavior. Routes, `DecisionPackage`, status/error/request-ID semantics, OpenAPI, current JSON decoding, transaction/session behavior, and existing callers are unchanged. No route rewiring, repository/UoW work, DTO/schema/migration/backfill, Decision Run write/enrichment, Brief, Explanation, What-if, Operational Procurement, Decision Procurement, or R2 change occurred.
- **Acceptance evidence:** `tests/test_read_decision_package.py` proves focused reader output, facade delegation, missing-run `PlanningError`, route error envelope, and no package/timestamp mutation. Existing Decision API, Brief/Explanation/What-if, and route-contract coverage remains in the full suite. Full suite: **612 passed, 0 failed, 22 warnings in 736.20s**. Compile passed; runtime OpenAPI is **58 paths / 70 operations**; `git diff --check` passed with only existing CRLF conversion warnings.
- **Rollback:** hunk-selectively remove the reader module, facade import/delegation, focused tests, and this documentation block. No data, schema, migration, route, or OpenAPI rollback is necessary.
- **Next state at R4.1 completion:** R4 remained active; **R4.1 COMPLETE** and **R4.2 NOT STARTED.** No next extraction was implied by that slice.

### R4.2 — BuildDecisionBrief boundary characterization — COMPLETE (2026-09-09)

- **Scope / call graph:** characterized `GET /api/v1/decision-runs/{decision_run_id}/brief -> DecisionPlanningService.get_decision_brief`; the same method is also used by Explanation, not legacy routes. The facade owns one session, missing-run error semantics, R2 presentation-mode routing, read-time compatibility fallback, and final evidence composition. It delegates deterministic fact/presentation projection to `DecisionBriefBuilder`, semantic evidence/projections, strategy presentation/expression, Overall Summary, Ingredient Synthesis, and `ShelfCashDecisionIntelligenceAdapter`.
- **Ownership findings:** `DecisionBriefBuilder` is already the focused shared presentation/fact-projection collaborator for Brief, post-commit assistant enrichment, and What-if. It reads `DecisionRun.package_json`, related ingredient/supplier/forecast data, parses valid persisted assistant artifacts, and projects `DecisionBriefFacts`; it does not rerun business computation. `_generate_and_persist_overall_summary` remains the separate post-core-commit writer for HYBRID Overall Summary and Ingredient Synthesis. GET Brief consumes persisted artifacts; absent/invalid summary uses deterministic fallback and absent synthesis uses a provider-disabled deterministic synthesis. Neither fallback writes, backfills, or spends tokens.
- **R2 / LLM / contract gates:** Strategy Explanation remains deterministic by default and optional validated whole-set `llm_polish`; ingredient synthesis retains deterministic default, exact risk evidence and fail-closed behavior; Overall Summary remains HYBRID. GET Brief can invoke Qwen only for explicit strategy-expression polish. `DecisionBriefFacts`, route/status/error semantics, OpenAPI (**58 paths / 70 operations**), persistence schema, Decision Package, procurement authorities, and Decision Run commit-before-enrichment choreography are unchanged.
- **Boundary classification:** **APPLICATION_USE_CASE_BOUNDARY — EXTRACTION JUSTIFIED.** Creating a wrapper around `DecisionBriefBuilder` alone would be redundant, but the facade's session/error/mode/fallback/evidence orchestration is a distinct application intent. **ReadDecisionPackage: NOT_NEEDED** because Brief must retain its same-session `DecisionRunModel` plus related-data projection; a separate raw-package session would provide no current value.
- **R4.3 candidate:** BuildDecisionBrief extraction behind the existing `DecisionPlanningService` facade was completed below. Its scope was only the current Brief orchestration; it reuses existing Decision Intelligence collaborators and preserves one read session, missing-assistant fallback, optional strategy polish, DTO/error/OpenAPI semantics, and no-write behavior.
- **Verification:** Brief/Decision API/Decision Intelligence/Overall Summary/Ingredient Synthesis/Strategy Expression/Decision Assistant contract plus R4.1 reader coverage: **145 passed, 1 warning in 17.24s**. No production or test code changed. Latest full-suite evidence remains R4.1: **612 passed, 0 failed, 22 warnings in 736.20s**; R4.2 did not rerun it.
- **Rollback:** documentation-only, hunk-selective `CURRENT_STATE.md` and `REFACTOR_PLAN.md`; no data, schema, route, or production rollback.

### R4.3 — BuildDecisionBrief extraction — COMPLETE (2026-09-09)

- **Before / after:** `DecisionPlanningService.get_decision_brief` directly implemented one-session run load/error handling, Brief construction, strategy-expression mode routing/logging, read-time assistant compatibility fallback, and evidence composition. It now delegates to `BuildDecisionBrief.build` in `app/services/decision/build_decision_brief.py`. The facade constructs the boundary from its current session factory, settings, and LLM provider values, preserving existing runtime overrides without composition-root or route rewiring.
- **Focused ownership:** `BuildDecisionBrief` owns only Brief application orchestration: one read session, missing-run `PlanningError`, existing-collaborator coordination, optional strategy polish, deterministic missing-summary/synthesis fallback, and final evidence attachment. `DecisionBriefBuilder` remains the shared deterministic package/ORM fact-projection collaborator; Decision Intelligence providers retain all presentation, summary, synthesis, semantic-evidence, validation, and fallback algorithms. Raw `ReadDecisionPackage` remains separate and is intentionally not reused.
- **Frozen behavior:** no `add`, `flush`, `commit`, mutation, package rewrite, assistant persistence, audit/idempotency write, schema/migration/backfill, computation, procurement, or R2 narrative change. Brief uses one session. Missing assistant artifacts remain response-only deterministic fallbacks. Default strategy presentation remains no-provider; `llm_polish` remains validated whole-set-or-deterministic fallback. Explanation keeps using the facade and What-if keeps its existing shared builder behavior.
- **Acceptance evidence:** `tests/test_build_decision_brief.py` adds facade-delegation, direct and HTTP missing-run errors, missing-artifact fallback, and no-mutation coverage. Focused Brief/reader/API/Decision Intelligence/Overall Summary/Ingredient Synthesis/Strategy Expression/assistant-contract/What-if regression: **150 passed, 1 warning in 20.43s**. Full suite: **616 passed, 0 failed, 22 warnings in 212.81s**. Compile passed; runtime OpenAPI remains **58 paths / 70 operations**; `git diff --check` passed with only existing CRLF conversion warnings.
- **Rollback:** hunk-selectively remove `BuildDecisionBrief`, restore the former facade body/import, remove focused tests, and revert this documentation. No data, schema, migration, route, or OpenAPI rollback.
- **Historical next-step note:** R4.3 left Ingredient Demand, Operational Procurement, active legacy plans, Decision Run generation/enrichment, raw Decision read facade, Brief facade, Explanation, What-if, and shared validation/idempotency/audit. R4.4 subsequently characterized these responsibilities below; no subsequent extraction was approved by R4.3 itself.

### R4.4 — Remaining responsibility / next-safe-boundary characterization — COMPLETE (2026-09-09)

- **Scope / facade separation:** re-verified that `get_decision -> ReadDecisionPackage` and `get_decision_brief -> BuildDecisionBrief` are temporary **FACADE_ONLY** methods, not remaining implementation hotspots. Reviewed all remaining non-legacy responsibility families: Explanation; Ingredient Demand command/query; Operational Procurement command/query; Decision Run generation/enrichment; What-if; and shared state/serialization/idempotency/audit helpers. Active legacy plan compatibility is **OUT_OF_SCOPE_FOR_R4** and remains R5 work.
- **Characterization results:** Explanation coordinates current Brief and raw-package reads, immutable ingredient snapshot validation, semantic evidence, `DecisionNarrativeProvider`, provider/template fallback, and `DecisionExplanationResponse`; it has one route caller, no direct write/commit/idempotency/audit, and no business computation. Ingredient Demand retains CoreBomAdapter/core authority, run lifecycle, idempotency, audit and read response coupling. Operational Procurement retains ADR-014 authority, a pre-build committed `running` row, later terminal status commit, idempotency/audit and operational response persistence. Decision Run's source-level idempotency registration/replay check precedes refreshed demand and its resource link is completed with final package persistence; the deterministic core package still commits before independent assistant enrichment. Existing regression protects the core/enrichment split, but an independent Decision Run idempotency replay characterization remains a test gap before any write-flow extraction. What-if remains a single in-memory deterministic recomputation/presentation/narrative intent and persists no hypothetical run.
- **Shared-helper gate:** `_forecast` is an authority-sensitive store/forecast/prediction reader across demand, procurement, Decision Run, What-if and legacy flows; `_decision_package` is shared Decision/What-if package serialization; neither is an R4.5 cleanup target. `_require_decision_ingredient` and `_template_explanation` are Explanation-specific. Idempotency and audit are write-flow coordination, not generic utility candidates. No `PlanningUtils`, repository/UoW, or authority-sharing abstraction is proposed.
- **Candidate classification:** Explanation — **READY_FOR_EXTRACTION**; Ingredient Demand — **CHARACTERIZATION_COMPLETE_BUT_HIGH_RISK**; Operational Procurement — **CHARACTERIZATION_COMPLETE_BUT_HIGH_RISK**; Decision Run generation/enrichment — **KEEP_TOGETHER**; What-if — **KEEP_TOGETHER**; legacy compatibility — **OUT_OF_SCOPE**. Explanation has the smallest rollback and does not move computation, public contracts, or persistence authority.
- **Historical R4.5 gate:** ExplainDecision extraction behind the existing facade was completed below. It preserved separate Brief/raw-package read behavior, immutable ingredient validation and existing `DECISION_RUN_INGREDIENT_NOT_FOUND` behavior, semantic evidence, `LLMTask.DECISION_NARRATIVE`, provider/template fallback, `DecisionExplanationResponse`, no-write behavior, and unchanged OpenAPI. It did not rewire routes, move narrative algorithms, alter What-if, or change Decision/Operational Procurement.
- **ADR / verification:** ADR-015 is sufficient; no new ADR required. Targeted Brief/raw-read/Decision API/Decision Intelligence/Ingredient Demand/Operational Procurement/Decision Run/Overall Summary/Ingredient Synthesis/Strategy Expression/What-if/Explanation regression: **166 passed, 1 warning in 22.30s**. R4.4 added no production or test code. Latest full-suite evidence remains R4.3: **616 passed, 0 failed, 22 warnings in 212.81s**. OpenAPI remains **58 paths / 70 operations**. `DECISIONS.md` and AS-IS snapshots remain unchanged.
- **Rollback / next state:** documentation-only and hunk-selective. R4.5 is completed below.

### R4.5 — ExplainDecision extraction — COMPLETE (2026-09-09)

- **Before / after:** the `DecisionPlanningService.explain_decision` facade previously implemented M6 explanation coordination itself. It now delegates to `ExplainDecision.explain` in `app/services/decision/explain_decision.py`, receiving the current per-call `BuildDecisionBrief`, existing `ReadDecisionPackage`, settings, and provider. No HTTP route, constructor, or composition-root migration was needed.
- **Focused responsibility:** `ExplainDecision` coordinates Brief retrieval, immutable raw-package retrieval, snapshot ingredient validation, semantic evidence, `DecisionNarrativeProvider`, and the existing template fallback. It owns the moved Explanation-only validation and fallback helpers. `BuildDecisionBrief` continues to own Brief orchestration, `ReadDecisionPackage` continues raw JSON read, and Decision Intelligence continues semantic/narrative/provider algorithms. No circular dependency, session unification, business recomputation, repository/UoW work, or new generic narrative service was introduced.
- **Frozen semantics:** the two existing read collaborator sessions remain independent; Explanation introduces no write. Ingredient validation/error details, `LLMTask.DECISION_NARRATIVE`, provider and template fallback semantics, `DecisionExplanationResponse`, request/error envelope, and R2 narrative authority remain unchanged. What-if and all computation/procurement authorities remain untouched.
- **Acceptance evidence:** `tests/test_explain_decision.py` adds facade delegation, direct/HTTP missing-run, and package/timestamp non-mutation coverage; `test_narrative_fallback_cleanup.py` now exercises the focused fallback owner. Existing ingredient validation, Decision Narrative task/provider fallback, Brief, raw-read, What-if, Overall Summary, Ingredient Synthesis, Strategy Expression, Decision API and assistant-contract regression passed. Focused set: **188 passed, 1 warning in 21.61s**. Full suite: **620 passed, 0 failed, 22 warnings in 171.75s**. Compile passed; runtime OpenAPI remains **58 paths / 70 operations**; `git diff --check` passed with only existing CRLF conversion warnings.
- **Historical remaining-work note:** hunk-selectively remove `ExplainDecision`, restore the former facade method/helpers, remove focused tests, and revert this documentation to roll back R4.5. No data or schema rollback. Remaining real ownership is Ingredient Demand, Operational Procurement, Decision Run generation/enrichment, What-if, shared validation/idempotency/audit/serialization, and R5 legacy compatibility; `get_decision`, `get_decision_brief`, and `explain_decision` are facade-only. R4.6 records the phase-close handoff below.

### R4.6 — R4 Exit Review / Remaining Write-Flow Handoff — COMPLETE (2026-09-09)

- **Exit result:** **R4 COMPLETE. R4 BLOCKER: NONE.** ADR-015's controlled, incremental decomposition is complete for the evidence-backed safe scope: `ReadDecisionPackage`, `BuildDecisionBrief`, and `ExplainDecision` now own raw read, Brief orchestration, and Explanation orchestration respectively. The existing `DecisionPlanningService` methods for those surfaces are temporary **FACADE_ONLY** delegates, which ADR-015 expressly permits. No duplicate active implementation, cycle, contract drift, authority drift, transaction drift, or narrative drift was found.
- **Retained write/coherent flows:** Ingredient Demand is **RETAINED_WRITE_FLOW** because Core BOM authority, run state, idempotency/audit and POST-to-GET continuity are coupled. Operational Procurement is **RETAINED_WRITE_FLOW / AUTHORITY-SENSITIVE** because ADR-014, its committed running row, later terminal commits, idempotency/audit, and persisted `ProcurementPlanRun` semantics must remain together. Decision Run generation/enrichment is a **RETAINED_COHERENT_USE_CASE** because deterministic package commit must precede separate best-effort enrichment persistence. What-if is a **RETAINED_COHERENT_USE_CASE** because baseline, in-memory core recomputation, Brief projection, comparison and guarded narrative form one no-persistence intent. No additional low-risk, useful application boundary remains.
- **Shared/legacy handoff:** `_forecast` remains an authority-sensitive state reader; `_decision_package` remains Decision/What-if serialization; idempotency/audit are write-flow coordination, not `PlanningUtils` candidates. They are **SHARED_COORDINATION** and persistence/session/repository concerns remain R6 review scope. Active legacy `/plan-runs` behavior is **R5_LEGACY** and is handed to R5 Entry Review unchanged. Decision Run idempotency replay remains a **TEST GAP** to characterize before any future write-flow extraction; it does not block R4 because no write extraction is half-complete or approved by this phase exit.
- **Preserved invariants:** ADR-007 public contracts, ADR-010 computation authority, ADR-014 Operational Procurement != Decision Procurement, current transaction/idempotency/failure persistence, deterministic Decision Run commit before assistant enrichment, active compatibility, and R2 narrative routing remain unchanged. No schema/migration/backfill, repository/UoW redesign, route rewiring, computation migration, procurement convergence, or facade removal occurred.
- **Evidence:** targeted extracted-boundary and retained-flow regression: **100 passed, 1 warning in 25.10s**. Full suite: **620 passed, 0 failed, 22 warnings in 170.29s**. Runtime OpenAPI: **58 paths / 70 operations**. Compile and `git diff --check` passed. R4.6 has no production-code change; its rollback is documentation-only and hunk-selective.
- **Historical next-phase note:** R5 Entry Review is completed below. R6 remains the later persistence/session/repository boundary review. No R4.7 is created.

## R5 — Legacy / Compatibility Separation

### R5 Entry Review — COMPLETE (2026-09-10)

- **Scope / current public inventory:** R5 reviewed all runtime legacy/compatibility surfaces without changing production code. Three `/stores/{store_id}/plan-runs` operations are typed, OpenAPI-visible and not deprecated; they delegate from `completion.py` to `DecisionPlanningService` legacy create/metadata/result methods. Three store-scoped forecast-run compatibility operations are likewise typed, OpenAPI-visible and not deprecated. The two `/forecasts` operations are OpenAPI-visible with `deprecated=true`, but remain active. Route-manifest entries and current tests are contract evidence; no in-repo frontend, SDK, CLI, script, example, or generated-client call to `/plan-runs` was found, which is **NO CALLER FOUND IN REPO**, not proof that no external caller exists.
- **Plan compatibility lifecycle / authority:** `create_legacy_plan` maps the legacy strategy, validates the forecast cutoff, registers POST idempotency, commits a running `PlanRunModel`, calls current demand and Operational Procurement, links the resulting `ProcurementPlanRun`, persists legacy `RecommendationModel` rows and audit, then commits terminal completed/blocked/failed state. Metadata/result reads preserve `LegacyPlanMetadataResponse`/`LegacyPlanResultResponse` and their current error/idempotency/auth semantics. The adapter has no independent computation authority: it delegates to current `CoreBomAdapter -> shelfcash_core` demand and ADR-014 `ProcurementPlanningService -> InventorySimulationService` Operational Procurement. Do not converge it with Decision Procurement.
- **Compatibility classification:** `/plan-runs` and store-scoped forecast-run compatibility are **CURRENT_COMPATIBILITY_REQUIRED**. Deprecated `/forecasts` is **DEPRECATED_BUT_ACTIVE**. The current ImportModel sync/read/backfill bridge is **DEPRECATED_BUT_ACTIVE**, because current import paths still write/read it. GET Brief missing-assistant fallback is **CURRENT_COMPATIBILITY_REQUIRED** and **OUT_OF_SCOPE**: it protects current enrichment-failure response behavior, not only historical rows. `LegacySupplierInventoryValueModel` is **PROVEN_UNUSED_CANDIDATE**, not dead, because it remains exported with migration/archive evidence. `ImportBusinessPersistenceService._persist_supplier_constraints_legacy` is **PROVEN_DEAD**: it is private/unexported; exact search finds no runtime/test/script/config caller; its dynamic dispatch can only select canonical `_persist_supplier_constraints`; it has no route, OpenAPI, persistence, client, or ADR role.
- **ADR-013 gate:** disposable historical SQLite rows do not justify breaking current HTTP routes, active in-version persistence lifecycles, current client contracts, or Brief fallback. Conversely, historical rows alone do not protect otherwise dead private code. This distinction drives the selected leaf removal and blocks no current public behavior.
- **Candidate risk / decision:** internal `LegacyPlanCompatibility` extraction is **justified** as a future behavior-preserving isolation boundary, but is not the smallest next slice because its compatibility row, nested demand/procurement, idempotency/audit and multi-commit lifecycle is transaction-sensitive. Public plan/forecast route removal is **NEW ADR REQUIRED**: ADR-007 contract migration/deprecation approval and external-client evidence are missing. A removal of the proven-dead private supplier-constraint helper is **NO ADR NEEDED**, has LOW contract/transaction/authority/caller/rollback risk, and is selected.
- **R5.1 — COMPLETE (2026-09-10):** removed only the private, unexported `ImportBusinessPersistenceService._persist_supplier_constraints_legacy` from `app/services/business_persistence.py`. Immediate pre-edit search and post-removal search found no runtime/test/route/OpenAPI/config/dynamic-import/script/CLI/export reference; canonical dynamic dispatch continues to target active `_persist_supplier_constraints` for the `supplier_constraints` sheet type. The deleted body had no current persistence lifecycle, although it would have written supplier-term/import-issue rows if invoked. No same-file imports became unused. Active canonical import persistence, the deprecated-but-active ImportModel legacy-row bridge, public plan/forecast routes, Brief fallback, `LegacySupplierInventoryValueModel`, migrations/tables/schema/DTOs, and OpenAPI were preserved. Focused import/compatibility regression: **112 passed, 15 warnings in 69.92s**; full suite: **620 passed, 0 failed, 22 warnings in 175.05s**; `compileall` and final `git diff --check` passed. Rollback restores the one method; no data rollback. **R5.2 — LegacyPlanCompatibility Boundary Characterization** is recommended, not started.
- **R5.2 — COMPLETE (2026-09-10):** `/plan-runs` is one substantive compatibility lifecycle, not a thin wrapper. The command first commits legacy idempotency plus `PlanRun(running)`, then delegates demand (only if no completed run) and Operational Procurement through their current independent sessions/commits, then atomically writes the PlanRun-to-ProcurementPlanRun link, positive-line `RecommendationModel` projections, `legacy_plan_generated` audit, and completed state. A downstream `PlanningError` persists legacy `blocked`; an unexpected error persists legacy `failed`; pre-initial-commit validation creates no PlanRun. Exact existing replay behavior is important: an identical key returns metadata for its resource, including `blocked`; a different payload is the 409 idempotency-key reuse error. Metadata/result reads are read-only and use PlanRun, linked operational plan, recommendation, ingredient, and supplier state without re-running business computation. `RecommendationModel` also supports current Purchase Order creation, so it is not removable compatibility-only storage.
- **Authority / extraction gate:** legacy owns compatibility strategy mapping, PlanRun lifecycle, legacy idempotency/replay, compatibility recommendation projection, audit naming, and DTO composition only. It delegates BOM to `CoreBomAdapter -> shelfcash_core` and procurement to ADR-014 `ProcurementPlanningService -> InventorySimulationService`; no Decision Procurement path is involved. **EXTRACTION JUSTIFIED BUT REQUIRES IMPLEMENTATION GATES:** preserve initial/nested/final commit choreography, exact status/error/replay behavior, audit/recommendation atomicity, store scoping, and Completion/Purchase Order model reads; inject one-way current collaborators for demand, operational procurement, and plan reads rather than create `LegacyPlanCompatibility -> DecisionPlanningService` recursion. Internal behavior-preserving extraction needs **NO ADR NEEDED**; public route removal remains **NEW ADR REQUIRED**. Targeted characterization tests for failure/replay and success recommendation/audit plus planning/compatibility/OpenAPI regression: **22 passed, 1 warning in 20.36s**. No production code, contract, schema, or authority change. **R5.3 — LegacyPlanCompatibility Extraction** is recommended but not started; rollback will remove its new boundary and restore the existing facade body, with no data rollback.
- **Verification / non-goals:** legacy plan create/replay/metadata/result/wrong-store/error, nested Ingredient Demand/Operational Procurement, forecast compatibility/deprecation, import normalized bridge, Brief fallback, planning and OpenAPI/error regressions: **58 passed, 1 warning in 107.42s**. Runtime OpenAPI remains **58 paths / 70 operations**. This Entry is docs-only; latest full suite remains R4.6's **620 passed, 0 failed, 22 warnings**. No migration, backfill, repository/UoW redesign, computation/procurement/FEFO/strategy/risk change, narrative change, or public API removal occurred.

### R5.3 — Canonical Write-Flow Collaborator Prerequisite Characterization — COMPLETE (2026-09-10)

- **Corrected migration evidence:** direct `LegacyPlanCompatibility` extraction was blocked before production change. Calling `DecisionPlanningService.generate_demand`, `generate_plans`, and `get_plans` from the new legacy boundary would create `DecisionPlanningService -> LegacyPlanCompatibility -> DecisionPlanningService`; copying those methods would duplicate active write flows. `CoreBomAdapter` and `ProcurementPlanningService.build()` own computation only, not lifecycle persistence, idempotency, audit, commit/rollback, failure state, or response mapping.
- **Result:** both missing one-way application boundaries are behavior-preservingly extractable under ADR-015 without R6 redesign. **INGREDIENT_DEMAND_EXTRACTION_READY:** retain current completed-forecast validation, idempotency, terminal commit, `IngredientDemandRun`/prediction persistence, blocked semantics, audit, response mapping, and `CoreBomAdapter -> shelfcash_core` authority. **OPERATIONAL_PROCUREMENT_EXTRACTION_READY:** retain precondition validation, committed idempotency + `ProcurementPlanRun(running)` before compute, terminal completed/blocked/failed commits, audit, response mapping, and `get_plans`; `ProcurementPlanningService -> InventorySimulationService` remains the ADR-014 operational authority.
- **Shared state-reader / R5-R6 resolution:** `_forecast` is authority-sensitive and must be supplied as a narrow injected completed-forecast/store reader or callable, not duplicated, made generic, or routed back through the facade. `get_demand`/`get_plans` move with their lifecycle for replay and POST-to-GET continuity. Both flows are **R5_PREREQUISITE**, not `R6_BLOCKED_PREREQUISITE`: direct ORM, factory/session, flush, explicit commits, and rollback-on-close move unchanged, and no evidence requires transaction redesign.
- **Evidence and gates:** R5.3 adds Demand success/blocked replay, resource-link, audit, and unexpected-rollback assertions; Procurement success replay/conflict/audit, committed-running visibility and replay, blocked replay, and unexpected-failed persistence/replay. Focused planning characterization: **10 passed, 1 warning**; targeted planning/idempotency/audit/compatibility/OpenAPI regression: **25 passed, 1 warning in 21.35s**. Runtime API remains **58 paths / 70 operations**; production code is unchanged. Future gates freeze routes/DTOs/status/errors/OpenAPI, store scope, idempotency, audit, persistence shape, commit ordering, terminal semantics, authority, GET behavior, facade-only DPS, and exactly one implementation. Rollback is code-only; no data conversion.
- **Approved sequence:** **R5.4 — Ingredient Demand Application Boundary Extraction**; **R5.5 — Operational Procurement Application Boundary Extraction**; **R5.6 — Retry LegacyPlanCompatibility Extraction**. One focused write flow per slice, with tests/full regression before the next. This encodes the missing prerequisite for legacy isolation. **NO ADR NEEDED.**

### R5.4 — Ingredient Demand Application Boundary Extraction — COMPLETE (2026-09-10)

- **Prerequisite implemented:** `IngredientDemandLifecycle` now independently owns the current Demand command/read application lifecycle; `DecisionPlanningService.generate_demand` and `get_demand` are facade delegates. `CompletedForecastReader` is the narrow one-way shared completed-forecast/store/prediction collaborator; it replaces duplicated `_forecast` implementation while the facade delegates to it for remaining flows. No boundary depends on `DecisionPlanningService`.
- **Behavior preserved:** no pre-computation committed running row; successful predictions/completed status/audit/idempotency resource link retain one terminal commit; `PlanningError` retains blocked commit and original HTTP semantics; unexpected errors retain rollback of uncommitted Demand/idempotency writes. `ingredient_demand_generated`, store scope, auth, POST-to-GET continuity, successful and blocked replay behavior, route/DTO/OpenAPI surface, and `CoreBomAdapter -> shelfcash_core` authority are unchanged. No ORM/model/schema/migration/backfill/repository/Unit-of-Work change.
- **Evidence:** focused planning/facade lifecycle tests **11 passed, 1 warning**; BOM/forecast/OpenAPI regression **13 passed, 1 warning**; full suite **626 passed, 0 failed, 22 warnings in 263.02s**; compile and whitespace gates passed; OpenAPI remains **58 paths / 70 operations**. Rollback is code-only and needs no data conversion. **NO ADR NEEDED.**
- **Next sequence:** R5.4 satisfies only the Ingredient Demand prerequisite. **R5.5 — Operational Procurement Application Boundary Extraction** is next; do not retry `LegacyPlanCompatibility` until its generate/read lifecycle can also be supplied one-way.

### R5.5 — Operational Procurement Application Boundary Extraction — COMPLETE (2026-09-10)

- **Prerequisite implemented:** `OperationalProcurementLifecycle` now independently owns the current Operational Procurement command/read lifecycle; `DecisionPlanningService.generate_plans` and `get_plans` are facade delegates. It reuses the narrow one-way `CompletedForecastReader`, so no forecast validation is copied and no boundary depends on `DecisionPlanningService`.
- **Behavior and authority preserved:** the idempotency record, `ProcurementPlanRun(running)`, and resource link still commit before computation. `ProcurementPlanningService.build() -> InventorySimulationService` remains the ADR-014 Operational Procurement authority. Success still commits plans/lines, completed state, response status, and `procurement_plans_generated` together; `PlanningError` still commits blocked and rethrows; unexpected compute failure still commits failed with normalized `PROCUREMENT_PLAN_INFEASIBLE` response semantics; a final persistence failure retains the first committed running row. Running, completed, blocked, and failed replay/read behavior, store scope, auth, routes, DTOs, errors, and OpenAPI are unchanged.
- **Evidence / persistence boundary:** focused planning/facade lifecycle tests **12 passed, 1 warning in 14.07s**; BOM/forecast/OpenAPI regression **13 passed, 1 warning in 17.08s**; procurement/recipe/forecast regression **22 passed, 1 warning in 13.10s**; final full suite **627 passed, 0 failed, 22 warnings in 322.57s**. Final compile and whitespace gates passed. No ORM/model/schema/migration/backfill/repository/Unit-of-Work change. Rollback is code-only: restore the two facade bodies, remove the focused collaborator/wiring/tests/docs; no data conversion.
- **Corrected next sequence:** both canonical write-flow prerequisites are complete. `LegacyPlanCompatibility` can now use `IngredientDemandLifecycle` and `OperationalProcurementLifecycle` one-way, without `LegacyPlanCompatibility -> DecisionPlanningService`. **R5.6 — LegacyPlanCompatibility Extraction** is the next slice; do not begin it in R5.5. **NO ADR NEEDED** under ADR-015.

### R5.6 — LegacyPlanCompatibility Extraction — COMPLETE (2026-09-10)

- **Isolation implemented:** `LegacyPlanCompatibility` now owns the active `/plan-runs` compatibility lifecycle while `DecisionPlanningService` retains only create/metadata/result facade methods. It directly calls `IngredientDemandLifecycle` and `OperationalProcurementLifecycle`, resolving the R5.3 circular-dependency blocker without a `LegacyPlanCompatibility -> DecisionPlanningService` edge.
- **Behavior preserved:** initial outer idempotency plus `PlanRun(running)` commit remains before nested work; Demand and Operational Procurement retain their established independent lifecycles and authorities. Final canonical-run link, Recommendation projection, `legacy_plan_generated`, and completed status remain one terminal transaction. Nested planning errors retain legacy blocked persistence; unexpected orchestration/finalization failure retains terminal rollback followed by failed persistence. Metadata/result reads, replay/conflict, store scope, auth, routes, DTOs, errors, OpenAPI, and Recommendation-to-Purchase Order compatibility are unchanged.
- **Evidence / rollback:** focused legacy/facade/atomicity/PO tests **14 passed, 1 warning in 34.19s**; completion/forecast/BOM/error/OpenAPI regression **26 passed, 1 warning in 65.06s**; final full suite **629 passed, 0 failed, 22 warnings in 470.30s**. Final compile and whitespace gates passed. No model/schema/migration/backfill/repository/Unit-of-Work change. Rollback restores the three former facade bodies and imports, removes this boundary/wiring/tests/docs; no data rollback.
- **Remaining R5 work:** the original extraction prerequisite is complete, but no public compatibility classification changed. `/plan-runs` and store forecast-run routes remain **CURRENT_COMPATIBILITY_REQUIRED**; `/forecasts` and import bridge remain **DEPRECATED_BUT_ACTIVE**; Brief fallback remains **CURRENT_COMPATIBILITY_REQUIRED / OUT_OF_SCOPE**; `LegacySupplierInventoryValueModel` remains **PROVEN_UNUSED_CANDIDATE**. Public `/plan-runs` removal remains **PUBLIC_API_DECISION_NEEDED**. **R5.7 — R5 Exit Review** is the next slice; do not remove or deprecate routes. **NO ADR NEEDED.**

### R5.7 — R5 Exit Review / Compatibility Handoff — COMPLETE (2026-09-10)

- **Phase decision: R5 COMPLETE.** The accepted phase goal was compatibility inventory, proven-dead removal where evidence allows, active compatibility isolation, and explicit removal handoff—not deletion of every legacy surface. R5 Entry Review through R5.6 meet those gates: R5.1 removed the one proven-dead private helper; R5.2 froze PlanRun behavior; R5.3 established behavior-preserving one-way prerequisites; R5.4/R5.5 extracted Demand and Operational Procurement lifecycles; R5.6 isolated LegacyPlanCompatibility.
- **Final architecture:** `DecisionPlanningService` now retains Decision Run generation/enrichment and What-if ownership while acting as a facade for focused read, brief, explanation, Demand, Operational Procurement, and LegacyPlanCompatibility collaborators. The graph is acyclic: `LegacyPlanCompatibility -> {IngredientDemandLifecycle, OperationalProcurementLifecycle}` and never back to the facade. Authority and transaction/idempotency/audit semantics are unchanged, including ADR-014's operational-versus-decision procurement separation and Recommendation-to-Purchase Order persistence.
- **Intentional retention / public handoff:** `/plan-runs` is **CURRENT_COMPATIBILITY_REQUIRED / NOW ISOLATED**; store forecast-run compatibility is **CURRENT_COMPATIBILITY_REQUIRED** with explicit `ForecastService` ownership; `/forecasts` and the ImportModel bridge are **DEPRECATED_BUT_ACTIVE**; Brief fallback is **CURRENT_COMPATIBILITY_REQUIRED / OUT_OF_SCOPE**; `LegacySupplierInventoryValueModel` remains **PROVEN_UNUSED_CANDIDATE**. No additional item has a full proven-dead caller/export/runtime proof, so no further R5 implementation is required. Public route removal is **PUBLIC_API_DECISION_NEEDED**: no in-repo caller is not proof that no external caller exists, and ADR-007 requires a separate public-contract decision.
- **Verification / next phase:** R5.7 stabilization (`tests/test_planning_api.py`, `tests/test_completion_behavior.py`, `tests/test_completion_response_contract.py`, `tests/test_forecast_api.py`, `tests/test_forecast_integration.py`, `tests/test_bom_adapter_regressions.py`, `tests/test_error_openapi_contract.py`) **47 passed, 1 warning in 133.35s**; final full suite **629 passed, 0 failed, 22 warnings in 336.89s**. Final compile and whitespace gates passed. No production code was changed in R5.7. R6 remains **ENTRY REVIEW NOT STARTED** and is limited to persistence architecture characterization: mixed repository/direct ORM, session-factory spread, transaction/commit ownership, and persisted JSON/versioning policy. **NO NEW ADR NEEDED.**

## R6 — Persistence / Transaction Cleanup

### R6 Entry Review — Persistence & Transaction Architecture Characterization — COMPLETE (2026-09-10)

- **Current evidence:** application ownership remains as established in R4/R5. Focused lifecycle services own their sessions and explicit transaction boundaries; `AuditService`/`IdempotencyService` add rows to the supplied session without committing, `ImportBusinessPersistenceService` flushes in its caller-owned session, and `ProcurementPlanningService` computes using its supplied Operational Procurement session. Therefore mixed repository/direct ORM is not itself a defect, and no global Repository or Unit of Work policy is accepted or justified.
- **Frozen intentional differences:** Demand has no pre-computation running commit; Operational Procurement commits a running claim before computation; Legacy PlanRun commits its outer running claim before nested lifecycles and then finalizes Recommendation/audit/completed state atomically; Decision Run commits deterministic package before optional assistant enrichment. Forecast uses distinct running/terminal commits around external core/artifact work. These are runtime contracts, not candidates for cosmetic normalization.
- **Concrete hotspot:** `ImportService.process()` flushes `ImportJob(status=processing)` and start audit but does not commit before long parsing/business persistence. Its independent-session `processing` guard therefore cannot act as a durable cross-request claim. The service additionally combines normalized import rows, active legacy-bridge synchronization, and a best-effort result-file derivative; normalized `result_json` remains the tested API source when that file is absent.
- **Approved next slice:** **R6.1 — Import Processing Claim Transaction Ownership Hardening** (NOT STARTED). Scope: characterize concurrent processing claim visibility, add focused characterization gates, and only then make the existing processing claim durable before expensive work if that preserves all current public and business semantics. Freeze ImportJob statuses/errors/replay, atomic-validation and preview checkpoints, terminal business persistence + legacy bridge + audit grouping, failure persistence, and best-effort file behavior. No API/DTO/schema/migration/backfill, business-persistence rewrite, ImportModel bridge removal, repository-wide conversion, UoW, forecast/planning/Completion change, or filesystem redesign. Rollback is code/test/doc-only; no data conversion. **NO NEW ADR NEEDED** unless evidence forces a global persistence rule.
- **Provisional later R6 sequence:** R6.2 reassess Import terminal finalization after R6.1; R6.3 assess exactly one Completion write-family boundary if a hidden-commit or duplicated-policy failure is demonstrated; R6.4 reassess current-version persisted Decision Package JSON contract only if a concrete compatibility failure exists; R6 Exit Review. These are provisional, not accepted design decisions.
- **Policy handoff:** direct ORM in a focused service remains acceptable where session/commit ownership is clear; repositories remain selective semantic/query collaborators. ADR-013 means historical resettable SQLite JSON alone does not warrant version/backfill machinery. No database rewrite.
- **Verification:** targeted Import/Forecast/Planning/Completion/OpenAPI stabilization passed **94 tests, 1 warning in 77.62s**; full suite passed **629 tests, 0 failed, 22 warnings in 441.53s**. Compile and whitespace gates passed; OpenAPI contract tests retain **58 paths / 70 operations**. Entry Review changed no production code.

### R6.1 — Import Processing Claim Transaction Ownership Hardening — COMPLETE (2026-09-11)

- **Defect confirmed before implementation:** independent sessions both read a confirmed ImportJob; an uncommitted `flush()` claim is invisible to the second session, while a stale second ORM instance can write `processing` after a plain first status commit. SQLite's writer lock can reject a competing write, but it is not a durable, current-compatible application claim.
- **Narrow mechanism:** `ImportService` now acquires ownership through a conditional status update restricted to the existing eligible states (`confirmed` and previously eligible incomplete `completed`), checks affected rows, and commits the winning claim before any pipeline/business work. A failed compare-and-set re-evaluates fresh state and returns the existing completed replay or `IMPORT_PROCESSING` rather than running work from a stale read. The worker retains the previous terminal transaction for normalized persistence, active legacy bridge, terminal audit/status, and result JSON; `import_processing_started` remains a worker audit, not a new claim event.
- **Behavior / rollback:** post-claim failures retain the current failed-state, issue, legacy bridge, and `import_failed` behavior; failed retry remains the current `INVALID_STATE_TRANSITION`. Atomic validation, preview, result-file best-effort behavior, API/OpenAPI, and business mapping remain unchanged. Rollback restores the former in-session status/flush lines, removes the claim helper and focused tests/docs; no data or schema rollback.
- **Evidence / next slice:** deterministic two-session barrier coverage proves exactly one pipeline and `ImportBusinessPersistenceService.persist()` execution, one success audit pair, and no loser work. Import/bridge/business tests passed **59 tests, 1 warning in 92.66s**; API/OpenAPI regression passed **40 tests, 1 warning in 103.06s**; required full suite passed **633 tests, 0 failed, 22 warnings in 537.58s**; compile and whitespace gates passed. OpenAPI remains **58 paths / 70 operations**. **R6.2 — Completion Purchase Order Transaction Characterization** is recommended and NOT STARTED: it must characterize the multi-aggregate PO create/confirm/receive transaction before proposing any implementation. This is a review slice, not a repository/UoW conversion. **NO NEW ADR NEEDED.**

### R6.2 — Completion Purchase Order Transaction Characterization — COMPLETE (2026-09-11)

- **Current lifecycle ownership:** `CompletionService.po()` owns the session, direct ORM reads/writes, final commit, and explicit `IntegrityError` rollback for create, patch, confirm, and receive. The only repositories/collaborators on this path are `StoreRepository`, `AuditLogRepository`, and `IdempotencyRepository` plus same-session expiry classification; none opens a session, commits, or rolls back. Direct ORM is therefore an explicit current pattern, not an independent defect. No Unit of Work or repository consolidation is justified.
- **Frozen persistence groups:** create writes a possible `BudgetPeriodModel`, supplier-grouped `PurchaseOrderModel`/`PurchaseOrderLineModel`, audit and optional idempotency response before one commit. Confirm atomically changes budget reservation, PO state/version/timestamp and audit. Receive atomically writes/updates inventory lots, receipt movements, `PurchaseReceiptModel`, PO-line quantities/versions, budget reservation/spend, PO partial/final status, expiry classification, audit, and optional idempotency response before its one final commit. Existing receipt/lot identity conflicts explicitly roll back; any non-commit exception leaves the context-managed session to roll back pending writes. `RecommendationModel -> PurchaseOrder` remains active compatibility behavior; PO consumes stored intent and does not recalculate procurement authority.
- **Characterized defect:** **`CONCURRENT_RECEIVE_RISK` / `PO_STATUS_INVENTORY_DIVERGENCE`.** A deterministic two-independent-session barrier forced two receive commands to read the same version and remaining quantity before either wrote. With distinct delivery and lot identities, both were accepted and committed: 2 receipt rows, +20 inventory movement and 2 `purchase_order_receive` audits, while stale last-write-wins left the PO line at 10 received and budget spent at 1000. The in-memory body-version comparison is not a persistence-level compare-and-set. This is a current-version correctness defect; it is not caused by a hidden commit or multi-aggregate transaction itself.
- **R6.3 recommendation — Purchase Order Receive Optimistic-Concurrency Hardening (NOT STARTED):** preserve the current state/status/version contract, one final successful receive transaction, response/error/idempotency/audit/store/API/OpenAPI behavior, Recommendation input, inventory/FEFO/procurement authority, and receipt identity checks. Introduce the smallest SQLite-compatible database-enforced version/state compare-and-set before receipt/inventory side effects; a loser must return the existing compatible version/state rejection and leave no receipt, movement, lot, budget, or success audit. Acceptance tests must cover two-session full-receipt race, loser no-side-effects, same-key replay/conflict, partial receipt, post-claim/finalization rollback, audit, Recommendation-to-PO regression and OpenAPI. Rollback is code/test/doc only. No schema/migration/backfill, lifecycle redesign, repository-wide change, UoW, or authority change. **NO NEW ADR NEEDED.**
- **Verification:** focused PO state-machine plus deterministic concurrency characterization passed **2 tests, 1 warning in 2.94s**; Completion/PO/planning/error/OpenAPI regression passed **34 tests, 1 warning in 76.34s**. Required full suite passed **634 tests, 0 failed, 22 warnings in 520.40s**; final compile and whitespace gates passed, and OpenAPI remains **58 paths / 70 operations**. R6.2 changes no production code.

### R6.3 — Purchase Order Receive Optimistic-Concurrency Hardening — COMPLETE (2026-09-11)

- **Problem and selected mechanism:** R6.2's deterministic two-session test proved that an in-memory PO version check permitted duplicate additive receive effects. `CompletionService._claim_receive_version()` now executes a direct SQLite-compatible compare-and-set (CAS): `UPDATE purchase_orders` matches the current `po_id`, `store_id`, body expected version and existing receive-eligible state, then increments version. `rowcount == 1` is the sole receive winner; SQLAlchemy refreshes the loaded PO, and the later receive state update no longer increments version. A zero-row CAS rolls back any pending request-local work, re-reads state, and returns the existing scoped not-found, `VERSION_CONFLICT`, or invalid-state error.
- **Transaction / authority preservation:** CAS is not committed independently. It is the first write in the original single receive transaction, followed by the same lot, inventory movement, receipt, line, budget, PO status, expiry classification, audit and optional idempotency work, then one final commit. The concurrency loser produces no additive effect. SQLite contention in the deterministic barrier test waits through the short winner transaction and resolves as an existing version conflict, not a raw lock response. No supplier/procurement/quantity/FEFO/expiry business rule, Recommendation-to-PO input, Operational Procurement or Decision Procurement authority changed.
- **Acceptance evidence / rollback:** the former two-winner test now proves one success, one `VERSION_CONFLICT`, exactly one lot/receipt/audit and +10 movement with one budget spend/release; a stale HTTP receive remains 409. An injected audit failure after successful CAS proves the version bump, receipt, movement, budget, and audit all rollback. Existing normal, partial/sequential, idempotent and receipt/lot identity paths remain covered by Completion contract tests. Rollback restores the prior in-memory authorization/version increment, removes the guarded helper and focused test assertions, and reverts these docs; no data/schema rollback.
- **Non-goals / next review:** no schema/migration/backfill, repository/UoW, retry framework, Completion decomposition, API/DTO/OpenAPI/auth/store scope change, or public concurrency error was introduced. **NO NEW ADR NEEDED.** The next highest-value review is **R6.4 — Decision Run Persistence/Enrichment Transaction Characterization (NOT STARTED)**: characterize the already-intentional deterministic DecisionRun commit before optional assistant enrichment, its failure/rollback and persisted package/artifact behavior before proposing any change.
- **Verification:** focused PO state-machine/concurrency/rollback test set passed **2 tests, 1 warning in 2.30s**; broader Completion/PO/planning/error/OpenAPI regression passed **34 tests, 1 warning in 39.70s**. Required full suite passed **634 tests, 0 failed, 22 warnings in 266.24s**; final compile and whitespace gates passed, and OpenAPI remains **58 paths / 70 operations**.

### R6.4 — Decision Run Persistence / Enrichment Transaction Characterization — COMPLETE (2026-09-11)

- **Frozen two-transaction behavior:** `DecisionPlanningService` owns deterministic Decision Run generation. Its post-Demand/core-procurement session writes `DecisionRunModel` identity, deterministic status/scenario/strategy metadata, request/package/warnings JSON, timestamps and (when available) the resource-link fields, then commits. `_generate_and_persist_overall_summary()` subsequently opens a separate session, reads the committed run/package, performs guarded Summary/Synthesis or their deterministic fallbacks, writes only the `assistant` projection inside `package_json`, and commits. There is no Decision Run audit write. This split is an intentional business-truth/failure-isolation boundary, not a reason to introduce a Repository or Unit of Work.
- **Failure/read characterization:** provider/schema/grounding failures yield persisted deterministic fallback wording. An injected assistant-persistence commit failure rolls back the assistant package mutation while the deterministic package remains available through `ReadDecisionPackage`; `/brief` reconstructs safe deterministic Summary/Synthesis with no provider call and no GET backfill. The helper holds a read session during any Qwen/OpenRouter work but performs no write before that work, so no SQLite write transaction spans provider latency. What-if is in-memory/hypothetical; Explanation is request-time guarded wording; neither mutates the Decision Run. No concurrent public assistant writer or business-field stale-overwrite path was found.
- **Characterized defect — `DECISION_RUN_IDEMPOTENCY_TRANSACTION_GAP`:** the optional key is registered/flush-only in an initial validation session. That session closes before `generate_demand()` and before the deterministic persistence session; the record is rolled back. The later session attempts to load/link that absent record. The focused two-session test freezes this current behavior. This is a transaction-continuity defect in Decision Run idempotency, not an enrichment defect and not evidence to merge the two commits.
- **R6.5 recommendation — Decision Run Idempotency Transaction Continuity Hardening (NOT STARTED):** make idempotency registration, same-key replay/conflict, DecisionRun resource link and deterministic commit a single coherent lifecycle without changing public contract or joining assistant enrichment. Acceptance must prove original keyed success, replay after deterministic-only/assistant-failure/success states, different-payload conflict, no duplicate deterministic run, and preserved demand/procurement/assistant/read semantics. Rollback is limited to Decision Run idempotency wiring/tests/docs; no schema, migration/backfill, UoW, repository redesign, business authority, narrative architecture, API or OpenAPI change. **NO NEW ADR NEEDED.**
- **Verification:** Decision/Brief/Explanation/Overall Summary/What-if/OpenAPI regression passed **63 tests, 1 warning in 61.27s**; focused assistant-commit rollback and idempotency-session-gap characterization passed **2 tests, 1 warning in 4.00s**. Required full suite passed **636 tests, 0 failed, 22 warnings in 491.03s**; `compileall`, `git diff --check`, and generated OpenAPI (**58 paths / 70 operations**) passed.

### R6.5 — Decision Run Idempotency Transaction Continuity Hardening — COMPLETE (2026-09-11)

- **Root cause and selected lifecycle:** a new optional key was previously added/flush-only in the early validation session and was rolled back when that session closed. R6.5 retains an early read-only existing-key/hash check, but moves new-record registration into the existing deterministic DecisionRun persistence session. That session atomically persists the `IdempotencyRecord`, `DecisionRunModel`, `resource_type=decision_run`, `resource_id`, and current response-status metadata before its one deterministic commit. The separate assistant enrichment session remains strictly after that commit.
- **Race and recovery:** a concurrent unique-key `IntegrityError` is handled only at this Decision Run boundary: rollback, re-read through the unchanged shared service, then replay the existing resource for the same hash or raise its existing request-reuse conflict for a different hash. The focused two-session barrier proves one Decision Run and one linked idempotency record for same-key/same-body callers. Core failure leaves no record; injected final deterministic persistence failure rolls back both record and run; assistant failure rolls back only assistant persistence while durable deterministic replay remains available. Expensive core work and Qwen calls do not hold a SQLite write transaction.
- **Non-goals / rollback / next recommendation:** no shared helper auto-commit, schema/migration/backfill, repository/UoW/global retry framework, API/DTO/OpenAPI/auth/store-scope/idempotency-header change, narrative or business-authority change was introduced. Rollback restores the former Decision Run idempotency wiring, removes the focused regression tests, and reverts this documentation; no data rollback. **NO NEW ADR NEEDED.** Next: **R6.6 — Import Terminal Finalization Transaction Characterization** (NOT STARTED), a review-only reassessment of Import's normalized writes, active legacy bridge, terminal state/audit, and best-effort artifact semantics after R6.1; it must prove a current defect before proposing any change.
- **Verification:** R6.5 targeted Decision/Brief/Explanation/Overall Summary/What-if/planning/error/OpenAPI regression passed **85 tests, 1 warning in 39.70s**. Required full suite passed **638 tests, 0 failed, 22 warnings in 199.94s**. `compileall`, `git diff --check`, and generated OpenAPI (**58 paths / 70 operations**) passed.

### R6.6 — Import Terminal Finalization Transaction Characterization — COMPLETE (2026-09-11)

- **Terminal transaction evidence:** following R6.1's committed processing claim, `ImportService` owns the terminal success session. `ImportBusinessPersistenceService.persist()` uses the supplied session and flushes, never commits. Its normalized business writes, expiry/issue writes, ImportJob result/summary/schema/timestamp/completed fields, active `ImportModel` compatibility projection, and `import_completed` audit all commit together. Injected failures after business-persistence flush, during bridge sync, and during terminal success audit each roll back canonical writes, completed result/state, bridge and success audit; `_mark_failed()` then uses an independent failure-finalization session for failed state, issue, failed bridge projection and `import_failed` audit. No hidden collaborator commit/session was found.
- **Artifact and recovery evidence:** `result_json` is the API source of truth; the result file is a post-commit best-effort derivative. Its write failure preserves the completed database state and GET result works without the file. A terminal DB rollback cannot leave that post-commit artifact. Normal handled failures become non-retryable failed jobs; a process crash or database failure inside `_mark_failed()` can leave the previously committed processing state because no stale-job recovery exists. That catastrophic residual is distinguished from the tested handled-failure path.
- **Concrete defect / R6.7 proposal:** **`IMPORT_PROCESSING_STARTED_AUDIT_LONG_WRITE_TRANSACTION`**. The post-claim processing session records `import_processing_started` before pipeline processing. `AuditLogRepository.add()` flushes immediately; therefore SQLite owns a write transaction across canonical normalization/validation and business staging until final commit/rollback. A focused independent-session probe at `process_sheet()` deterministically received `database is locked`. **R6.7 — Import Processing-Started Audit Transaction Scope Hardening** (NOT STARTED) must narrow this audit timing/scope while preserving claim exclusivity, audit semantics, terminal atomicity, failed finalization, legacy bridge, result JSON/file and public API. Acceptance must prove no SQLite write lock during pipeline work, exactly-one start/success/failure audit according to frozen outcomes, all R6.1 claim behavior, and terminal failure rollback. No schema/state-machine/API/migration/backfill, Repository/UoW/global retry, business-rule, compatibility-bridge or filesystem redesign. **NO NEW ADR NEEDED.**
- **Verification:** focused terminal persistence/legacy/audit/artifact/claim/failure tests plus Import/Forecast/Planning/Completion/error/OpenAPI stabilization passed **103 tests, 1 warning in 82.78s**. Required full suite passed **643 tests, 0 failed, 22 warnings in 222.92s**. `compileall` and generated OpenAPI (**58 paths / 70 operations**) passed. R6.6 adds characterization tests and documentation only; no production code changes.

### R6.7 — Import Processing-Started Audit Transaction Scope Hardening — COMPLETE (2026-09-11)

- **Root cause and implementation:** R6.6 proved that the first `import_processing_started` audit flush opened SQLite's write transaction in the long processing session. R6.7 leaves `AuditService` and `AuditLogRepository` unchanged and adds the narrow caller-owned `ImportService._record_processing_started()` boundary after R6.1's committed CAS claim. It verifies current eligible/processing state, writes the exact existing audit payload, and commits before `process_sheet()`. The terminal processing session no longer stages that audit and remains responsible only for its existing business/finalization group.
- **Behavior and failure preservation:** success has claim commit → start-audit commit → terminal success commit; a handled post-start error has claim commit → start-audit commit → terminal rollback → failure-finalization commit. The independent writer probe now succeeds during `process_sheet()` instead of receiving `database is locked`. One winner creates one start audit; a concurrent R6.1 loser creates none. A start-audit persistence failure after a winning claim performs no pipeline work and follows `_mark_failed()`; a request rejected before claim retains its existing invalid-state result without failure mutation. R6.6 canonical/legacy/result/audit rollback, best-effort artifact, result JSON authority, and catastrophic `_mark_failed()`/crash residual remain unchanged.
- **Scope / handoff / rollback:** no schema/migration/backfill, state/API/DTO/OpenAPI/auth/store-scope change, global audit helper change, repository/UoW/retry/watchdog, mapping/pipeline/business-rule, legacy-bridge or filesystem redesign occurred. Rollback restores the former in-session start-audit call, restores the old contention expectation, and reverts focused tests/docs; no data rollback. **NO NEW ADR NEEDED.** No further concrete persistence defect is currently evidenced; **R6 Exit Review** is the recommended next step (NOT STARTED).
- **Verification:** focused Import lock/claim/audit/failure/terminal-artifact tests plus Import/Forecast/Planning/Completion/error/OpenAPI stabilization passed **104 tests, 1 warning in 87.09s**. Required full suite passed **644 tests, 0 failed, 22 warnings in 235.90s**. `compileall` and generated OpenAPI (**58 paths / 70 operations**) passed.

### R6 Exit Review — Persistence & Transaction Architecture Closure — COMPLETE (2026-09-11)

- **Decision:** **R6 COMPLETE.** Entry Review, R6.1, R6.2, R6.3, R6.4, R6.5, R6.6, R6.7 and this exit review followed the approved discipline: characterize runtime behavior, prove a concrete defect, apply only a narrow fix, preserve contracts, and regress. No R6.8 cleanup slice is justified.
- **Closed correctness findings:** (1) Import processing claim ownership now uses committed status compare-and-set before expensive work; (2) Purchase Order receive uses an in-transaction version/state compare-and-set before any additive receipt/inventory/budget/audit work; (3) Decision Run deterministic persistence atomically commits its new idempotency record and resource link; (4) Import processing-start audit has a short caller-owned durability boundary before the pipeline rather than a write lock across it. Focused regression tests retain each prior race, rollback, idempotency, and SQLite-contention proof.
- **Intentional boundaries retained:** PO's normal multi-aggregate work remains caller-owned; DecisionRun deterministic truth commits separately from optional assistant language enrichment; Import terminal normalized state, active legacy bridge, result JSON, terminal state and success audit remain one transaction, with independent handled-failure finalization and a post-commit best-effort result file. Ingredient Demand, Operational Procurement and Legacy PlanRun retain their intentionally different precompute/staged transaction semantics.
- **Final architecture conclusion:** application/use-case owners own sessions and commits; Audit/Idempotency helpers remain caller-session helpers without hidden commits. Focused direct ORM is acceptable, selective repositories remain useful, and neither a repository-everywhere migration nor a global Unit of Work is justified by the evidence. R6 made no schema/migration/backfill, database, API/OpenAPI, business-authority, narrative-authority, or compatibility-surface change.
- **Residuals / handoff:** Import crash or double failure in `_mark_failed()` can still leave a durable `processing` claim; classify it as deferred operational recovery risk, not an R6 closure blocker. SQLite's inherent single-writer limit remains, while the proven unnecessary start-audit lock was removed. No current-version JSON-shape defect is evidenced. The next accepted phase already present in this plan is **R7 — Stabilization** (NOT STARTED); do not create R6.8 merely for cleanup.
- **Verification:** targeted R6 Import/PO/Decision/Planning regression passed **74 tests, 1 warning in 72.69s**; exit-review full suite passed **644 tests, 0 failed, 22 warnings in 251.35s**; runtime OpenAPI remained **58 paths / 70 operations**; `compileall` and `git diff --check` passed. Exit Review production diff: **NONE**.

## R7 — Stabilization

### R7 Entry Review — Stabilization — COMPLETE (2026-09-11)

- **Evidence-led review:** R7 performed no production refactor. It re-ran permanent R6 concurrency/rollback gates and broad integration, contract, fallback, compatibility, clean SQLite migration/startup and health coverage. Targeted stabilization regression passed **194 tests, 5 warnings in 178.76s**; full suite passed **645 tests, 0 failed, 22 warnings in 251.27s**. Runtime OpenAPI remains **58 paths / 70 operations**; compile and whitespace gates pass. The warning inventory is one Starlette/httpx TestClient dependency deprecation and 21 Python sqlite datetime-adapter deprecations in test/migration paths, with no current runtime leak or correctness evidence.
- **Stable boundaries confirmed:** Import's R6.1/R6.7 claim/start-audit/terminal boundaries, PO Receive's R6.3 guarded atomic receipt, DecisionRun deterministic/idempotency commit before optional assistant commit, Forecast production/shadow isolation, Ingredient Demand and Operational Procurement's intentionally different lifecycles, Legacy PlanRun compatibility, Brief missing-assistant fallback, provider fallbacks, current store scoping, error envelope/auth and OpenAPI remained intact. Direct ORM with explicit application ownership, selective repositories, and no global Unit of Work remain the accepted current pattern.
- **Highest-priority concrete defect:** **`PO_CONFIRM_OPTIMISTIC_CONCURRENCY_GAP`**. `CompletionService.po("confirm")` still validates `PurchaseOrder.version` and `draft` status only on its loaded ORM row. A new deterministic two-session barrier test synchronizes two callers after both read version `1`; both confirm successfully and both persist `purchase_order_confirm` audits. Final scalar state masks the race (`ordered`, version `2`, one `1000` reservation) because the stale second write overwrites the same scalar values. It reproduced in three standalone runs and violates expected-version command semantics, despite not reproducing the additive receipt/inventory damage that justified R6.3.
- **R7.1 proposal — Purchase Order Confirm Optimistic-Concurrency Hardening (NOT STARTED):** introduce one local SQLite compare-and-set at the confirmation write boundary: match PO id/store, `draft`, and request version; progress version once in the existing confirmation transaction, then reserve budget / set ordered / write success audit. Re-read a zero-row result to retain existing not-found, `VERSION_CONFLICT`, and invalid-state responses. Acceptance: concurrent same-version confirms yield exactly one success and one conflict, exactly one budget reservation/audit/version progression; terminal confirmation failure rolls back guard and reservation; sequential/invalid-state/idempotency/API/store-scope behavior and R6.3 receive remain unchanged. No schema/migration/API/business-rule/authority/repository/UoW/refactor is authorized. Rollback is local code/test/docs only. **NO NEW ADR NEEDED.**
- **Non-blocking observations:** no material new SQLite long-write hotspot, raw error-path leak, compatibility regression, authority drift, store-scope breach, default unexpected provider dependency, shadow-forecast authority leak, or clean-current-version startup failure was found. Draft PATCH and other Completion writes have Python version checks but no demonstrated concurrent correctness defect; keep them unprioritized until independently characterized. Do not create cleanup-only R7 slices.

### R7.1 — Purchase Order Confirm Optimistic-Concurrency Hardening — COMPLETE (2026-09-11)

- **Root cause and local fix:** R7's deterministic two-session characterization proved that Confirm's loaded-object `version`/`draft` checks could authorize the same command twice. `CompletionService._claim_confirm_version()` now executes a SQLite-compatible conditional `UPDATE` for the scoped PO only when its stored version equals the request version and status is `draft`; it advances version once with `synchronize_session=False`, refreshes the winner's ORM object, and never commits itself. Confirm no longer performs its former Python `po.version += 1`.
- **Transaction and public behavior:** the successful path remains one caller-owned transaction: CAS authorization → unchanged budget reservation/calculation → `draft` to `ordered` → `confirmed_at`/response projection → existing `purchase_order_confirm` audit → commit. A zero-row CAS rolls back/reloads before preserving current not-found, stale `VERSION_CONFLICT`, and invalid-state precedence. The winner alone may reserve budget or write a success audit. Budget or audit failure after CAS rolls the version authorization and all staged state back; no durable pre-confirm claim exists. PO Receive's independent R6.3 guard and all public routes/DTOs/errors/auth/store scope remain unchanged.
- **Acceptance and rollback:** the converted barrier regression passed three consecutive runs with one success, one `VERSION_CONFLICT`, final `ordered` version `2`, one `1000` reservation, and one audit. It also freezes sequential stale/non-draft typed errors and injected post-CAS budget/audit rollback. Focused Completion plus R7 stabilization regressions passed **109 tests, 1 warning in 95.19s**; the full suite passed **646 tests, 0 failed, 22 warnings in 308.73s**. Runtime OpenAPI remains **58 paths / 70 operations**; `compileall` and `git diff --check` passed. No schema/migration/backfill, business/procurement authority, audit-helper, Repository, Unit of Work, generic concurrency framework, API/OpenAPI, or state-machine change. Rollback reverts this local helper/call, focused regression tests, and documentation; no data rollback. **NO NEW ADR NEEDED.**

### R7 Exit Review — Stabilization Closure — COMPLETE (2026-09-12)

- **Decision:** **R7 COMPLETE.** The final gate re-ran R7.1 Confirm success/stale/concurrent/post-CAS rollback and Confirm → Receive compatibility, permanent R6 Import claim/lock/terminal tests, PO Receive winner-only behavior, Decision idempotency and assistant-failure isolation, provider/fallback, planning/legacy compatibility, clean SQLite migration/startup, error/route/OpenAPI contracts, and broad integration coverage. No reproducible HIGH/BLOCKER current-version defect remained; no R7.2 cleanup or theoretical hardening slice is justified.
- **Final invariants:** PO Confirm's local SQLite version/state CAS remains within its final caller-owned transaction and produces one success, one `VERSION_CONFLICT`, one reservation, one version progression, and one audit for competing version-`V` commands. Import retains CAS claim → short start-audit commit → unlocked pipeline → coherent terminal/failure boundaries and post-commit best-effort artifact. DecisionRun retains deterministic run/idempotency persistence before optional assistant enrichment. Ingredient Demand, Operational Procurement, Legacy PlanRun, Forecast/shadow, R5 compatibility, business authority, and narrative architecture retain their intentionally distinct accepted lifecycles.
- **Residual classifications:** Import crash or `_mark_failed()` double-DB-failure remains deferred operational recovery risk; draft PATCH concurrency remains observation-only absent a reproduced incorrect result. Mixed direct ORM/repository use, current JSON permissiveness, active legacy compatibility, core/shadow duplication, warning debt, and SQLite's inherent single-writer property are non-blocking debt/constraints, not R7 blockers. Direct ORM remains acceptable under explicit application ownership; repository-everywhere and a global Unit of Work remain not justified.
- **Verification and rollback:** final R7 targeted gate passed **195 tests, 5 warnings in 289.69s**; final full suite passed **646 tests, 0 failed, 22 warnings in 552.42s**; runtime OpenAPI remains **58 paths / 70 operations**; `compileall` and `git diff --check` passed. Exit Review production changes: **NONE**. Rollback is documentation-only; no data/schema rollback. **NO NEW ADR NEEDED. NEXT PHASE NOT YET DEFINED.**

### Post-R7 Decision Package no-feasible conformance fix — COMPLETE (2026-09-12)

- **CURRENT FACT / root cause:** `_decision_package()` correctly receives a core result with `recommended_strategy=None` when all evaluated candidates are infeasible, but previously derived `selected={}` and therefore persisted `critic={}`. POST commits that package before its response is validated; both POST and raw GET then cross the existing `DecisionPackage` response model, whose critic status is required.
- **ACCEPTED CHANGE:** the local package-boundary helper keeps the selected critic exactly for successful recommendations. For no-feasible outcomes it returns `status=fail`, deterministic deduplicated existing hard-violation findings with per-strategy evidence, deterministic existing warnings, and contract-valid empty checks/details. `recommended_strategy` remains null and `recommended_plan` remains empty.
- **NON-GOALS / TECHNICAL DEBT:** no optimizer or feasibility rule, strategy selection, stress-warning semantics, LLM path, persistence schema, migration/backfill, or broad Decision Package diagnostic redesign changed. Existing broad nested diagnostics remain technical debt.
- **HISTORICAL INFORMATION:** no persisted historical row is rewritten; current producer conformance preserves ADR-007 while ADR-013 remains unchanged. **PROPOSAL:** none; no new ADR is needed.
- **Acceptance / verification:** the real core optimizer is driven through all-infeasible zero-budget input at POST, then validates the HTTP response, persisted JSON, and raw GET; it also asserts a selected successful critic remains unchanged. Targeted Decision API/Intelligence tests: **17 passed, 1 warning**. Full suite: **647 passed, 22 warnings in 245.28s**. Generated OpenAPI remains **58 paths / 70 operations**, with both Decision Run success responses still referencing `DecisionPackage`; `compileall` and `git diff --check` passed.
- **Rollback:** revert the `_package_critic` helper/call, the Decision API regression assertions, and this documentation section. No database/data rollback is required.

### Post-R7 Strategy Selection Presentation — COMPLETE (2026-09-21)

- **Accepted additive contract:** `DecisionBriefFacts` adds typed `strategy_selection_presentation`, a deterministic manager-facing selection/read model with outcome, selected strategy, headline/summary and per-strategy status/message/detail/reason/evidence fields. Existing `strategy_comparison` and `strategy_evaluations` are preserved.
- **Authority and lifecycle:** the builder reads only the persisted package and reuses the existing strategy outcome, reason-evidence and presentation projections. It performs no optimizer/critic/risk/scenario recomputation, no Qwen call on default Brief, and no persistence, migration, backfill or historical package rewrite. Stress and conservative evidence remain non-causal unless the existing candidate finding supplies selected-plan authority.
- **Rollback:** remove the new DTOs/composer/wiring and focused tests, then revert this documentation and the ADR/frontend contract notes. No data rollback is needed.
- **Verification:** final targeted gates passed **49 tests, 2 warnings**. Final full suite passed **664 tests, 0 failed, 22 warnings in 652.11s**; `compileall`, `git diff --check`, and generated OpenAPI (**58 paths / 70 operations**) passed.

After controlled slices are complete: run full regression; review OpenAPI diff; verify historical Decision Runs; verify public frontend contracts; remove only proven dead code; check performance sanity and migration compatibility; produce stabilized architecture documentation and an updated stable API contract; then archive pre-refactor snapshots.

Only at this point should `STABILIZED_ARCHITECTURE.md` be considered and made authoritative.

## High-risk contract debt review

| Boundary | Classification | Evidence and intended handling |
|---|---|---|
| Raw Decision Package | R1.2 COMPLETE | POST/GET Decision Run now use the explicit `DecisionPackage` contract. Old persisted JSON compatibility/version migration is not required under ADR-013. |
| IngredientDemandRun | R1.3 COMPLETE | POST/GET Ingredient Demand now use the explicit run/prediction/contribution response contract; current runtime payload is unchanged. |
| ProcurementPlanRun | R1.4 COMPLETE | POST/GET Procurement Plans now use explicit run/plan/line/daily response contracts; current runtime payload is unchanged. |
| Menu/Product | R1.5 COMPLETE | Product CRUD/menu/components now use explicit Product, component, summary, and menu-read response DTOs; current runtime payload is unchanged. |
| Operational page responses | R1.6 COMPLETE | Eight scoped operational reads now use explicit `Page<T>` item DTOs; current runtime payloads are unchanged. |
| Completion write responses | R1.7 COMPLETE | Inventory/history/supplier/settings/calendar writes now use explicit current-response DTOs; inventory-constraint writes remain typed. |
| Purchase Orders | R1.7 COMPLETE | Create/list/get/patch/confirm/receive now use explicit Purchase Order DTOs and page/create wrappers. |
| Common error envelope/OpenAPI | R1.1 COMPLETE | The runtime error envelope and generated validation `422` schema now use the explicit common error contract. |

No reviewed high-risk item above is `ALREADY_FIXED`; typed neighboring endpoints do not close these specific boundaries.

## Technical debt priorities

| Priority | Debt | Reason |
|---|---|---|
| P0 | OpenAPI/common-error mismatch | Public contract correctness and generated-client risk. |
| P0 | Remaining nested Decision Package JSON breadth | Public top-level boundary is typed in R1.2; deeper metrics/diagnostics remain intentionally bounded until a separately scoped contract slice needs them. Old SQLite compatibility is not the reason to version/migrate. |
| P0 | Duplicated/diverged computation implementations with authority-drift risk | `shelfcash_core` remains production authority; overlap and helper/contract cross-imports require evidence before consolidation. |
| P1 | Lower-risk untyped read responses | Bootstrap, dashboard, settings-read, import-result, and other non-candidate reads still need separate scope. |
| P1 | `DecisionPlanningService` breadth | High complexity and broad change blast radius; decomposition waits for R1. |
| P1 | Persisted JSON without explicit schema versioning | Migration and historical-run risk. |
| P1 | Mixed repository/direct ORM and transaction ownership | Testability and consistency risk. |
| P1 | Legacy/canonical API overlap without lifecycle registry | Client migration and accidental-break risk. |
| P1 | LLM transport/guard complexity after default LLM scope shrank | Maintenance burden; only proven unused paths may be removed. |
| P2 | Dense route/service responsibility hotspots | Readability and local maintenance; not a correctness reason by itself. |
| P2 | Stale and duplicated historical documentation | Developer confusion; handle only in `DOC-CLEANUP-0` after baseline approval. |

## Documentation Inventory

Classification records the final DOC-CLEANUP-0 disposition. The canonical set remains at the `docs/` top level; historical documents are retained under `docs/archive/`.

| File | Classification | Current role | Referenced by | Recommended future action |
|------|----------------|--------------|---------------|---------------------------|
| `README.md` | STILL_REFERENCED | Repository entry point and developer workflow | Repository entry point | KEEP |
| `docs/README.md` | STILL_REFERENCED | Documentation navigation hub | `README.md` | KEEP |
| `docs/CURRENT_ARCHITECTURE.md` | CANONICAL | Pre-refactor AS-IS architecture snapshot | Canonical control set | KEEP |
| `docs/CURRENT_API_CONTRACT.md` | CANONICAL | Pre-refactor AS-IS API/contract snapshot | Canonical control set | KEEP |
| `docs/DECISIONS.md` | CANONICAL | Accepted architecture intent | Canonical control set | KEEP |
| `docs/CURRENT_STATE.md` | CANONICAL | Current phase and operational checkpoint | Canonical control set | KEEP |
| `docs/REFACTOR_PLAN.md` | CANONICAL | Migration order and safety gates | Canonical control set | KEEP |
| `docs/decision-runs-api.md` | STILL_REFERENCED | Specialized Decision Run/What-if behavior | `docs/README.md` | KEEP |
| `docs/decision_assistant_api_contract.md` | STILL_REFERENCED | Specialized frozen Decision Assistant contract | `docs/README.md`, `CURRENT_API_CONTRACT.md`, frontend-doc test | KEEP |
| `docs/FORECAST_MODEL_PREPROCESSING_SCHEMA.md` | STILL_REFERENCED | Forecast feature/preprocessing contract | `docs/README.md` | KEEP |
| `docs/INVENTORY_CONSTRAINT_CONTRACT.md` | STILL_REFERENCED | Inventory/business constraint semantics | `docs/README.md` | KEEP |
| `docs/MENU_IMPORT_AND_API.md` | STILL_REFERENCED | Menu import/API specialization | `docs/README.md` | KEEP |
| `docs/frontend_decision_assistant_integration.md` | STILL_REFERENCED | Frontend integration guide and test dependency | frontend-doc test | KEEP |
| `docs/archive/old-designs/decision-core-integration.md` | ARCHIVED | Superseded core-integration checkpoint with stale runtime claims | Historical archive | ARCHIVED |
| `docs/archive/checkpoints/API_OPERATION_BEHAVIOR_AUDIT.md` | ARCHIVED | Old 53-operation implementation audit | Historical archive | ARCHIVED |
| `docs/archive/checkpoints/CANONICAL_BUSINESS_SCHEMA.md` | ARCHIVED | Early canonical-schema checkpoint | Historical archive | ARCHIVED |
| `docs/archive/checkpoints/CATALOG_AND_RECIPE_API.md` | ARCHIVED | Early catalog/recipe checkpoint | Historical archive | ARCHIVED |
| `docs/archive/checkpoints/CHECKPOINT_0_CODEBASE_AUDIT.md` | ARCHIVED | Initial codebase audit | Historical archive | ARCHIVED |
| `docs/archive/old-designs/DECISION_INTELLIGENCE_API.md` | ARCHIVED | Superseded Decision Intelligence API design | Historical archive | ARCHIVED |
| `docs/archive/handoffs/FE_DECISION_BRIEF_HANDOFF.md` | ARCHIVED | Earlier frontend brief handoff | Historical archive | ARCHIVED |
| `docs/archive/handoffs/FE_DECISION_RUNTIME_CONTRACT.md` | ARCHIVED | Pre-Phase-7 frontend runtime draft | Historical archive | ARCHIVED |
| `docs/archive/checkpoints/IMPORT_PERSISTENCE.md` | ARCHIVED | Early import-persistence checkpoint | Historical archive | ARCHIVED |
| `docs/archive/checkpoints/IMPORT_TO_BUSINESS_PERSISTENCE.md` | ARCHIVED | Early import-to-business checkpoint | Historical archive | ARCHIVED |
| `docs/archive/legacy-contracts/ShelfCash_API_Contract_v1(3).md` | ARCHIVED | Consolidated older v1.1 API contract | Historical archive | ARCHIVED |
| `docs/archive/legacy-contracts/ShelfCash_Menu_API_Contract_v1.md` | ARCHIVED | Older menu contract/addendum | Historical archive | ARCHIVED |
| `docs/API_IMPLEMENTATION_STATUS.md` | DELETED | Superseded 53-operation status report | `CURRENT_API_CONTRACT.md` and route manifest | DELETED |
| `docs/CODEX_MASTER_INSTRUCTIONS.md` | DELETED | Obsolete checkpoint instructions | `DECISIONS.md`, `REFACTOR_PLAN.md`, and canonical snapshots | DELETED |
| `docs/ShelfCash_API_Contract_v1.md` | DELETED | Older draft API contract | `CURRENT_API_CONTRACT.md` | DELETED |

No backend-relevant Markdown file remains unclassified. No file was classified as `NEEDS_REVIEW`; the frontend guide remains `STILL_REFERENCED` because it is a current test dependency.

### DOC-CLEANUP-0 completion

- [x] Historical archive created with traceable `git mv` renames.
- [x] Redundant delete candidates individually reference-reviewed and removed with `git rm`.
- [x] Documentation navigation and moved internal links updated.
- [x] Canonical and still-referenced documents retained.
- [x] R1 not started.

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
- [x] Baseline tagged `pre-refactor-backend-2026-09`
- [x] No push

**Readiness: PRE-REFACTOR BASELINE FROZEN.** All technical R0 gates are green: the 19 deletions are restored, every initial failure is triaged and resolved, the full suite passes, OpenAPI/manifest remain 58 paths / 70 operations, and every remaining file is explained. DOC-CLEANUP-0 has completed; R1 is not active yet.

## Baseline and documentation cleanup sequence

1. The reviewed baseline was committed and tagged `pre-refactor-backend-2026-09`.
2. **DOC-CLEANUP-0** completed the controlled archival/deletion inventory actions.
3. R1 remains inactive. Its recommended first slice is R1.1, subject to its own contract-hardening gate.
