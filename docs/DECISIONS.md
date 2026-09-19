# ShelfCash Backend Architecture Decisions

Baseline date: 2026-09-07.

## How to use this document

- **ACCEPTED** means the decision is part of the current intended architecture.
- **DEFERRED** means the option was considered but is not currently selected.
- **REJECTED** means the option was intentionally not selected.
- **SUPERSEDED** means a newer decision replaces it.
- **NEEDS DECISION** means evidence or explicit approval is still required.
- A **PROPOSED** idea, including a recommendation in an AS-IS document, must not be treated as accepted until this document records its approval.

For current runtime facts, code and tests remain authoritative. This document is authoritative for accepted architecture intent.

## ADR-001 — M1–M6 vs Opportunity Recommendation

- **Status:** ACCEPTED
- **Decision:** Opportunity Recommendation for new or not-yet-sold items remains architecturally separate from the active-SKU M1–M6 forecast and procurement path. New items must not silently enter Forecast M1–M6, procurement M1–M6, What-if M1–M6, or Strategy selection M1–M6.
- **Context:** New items do not have the same active-SKU demand history and cannot inherit M1–M6 semantics without an explicit model and contract decision.
- **Why:** This prevents unsupported demand assumptions from acquiring business authority through an existing pipeline.
- **Rejected/deferred alternatives:** Automatically feeding opportunity items into M1–M6 is rejected. A future explicit bridge is deferred.
- **Revisit condition:** A separately approved architecture decision defines eligibility, evidence, contracts, validation, and migration behavior for new items.
- **Affected areas:** Opportunity recommendation, forecast, procurement, What-if, strategy selection, API contracts.

## ADR-002 — Backend owns business authority

- **Status:** ACCEPTED
- **Decision:** Backend deterministic code owns forecast, BOM, inventory/FEFO, procurement quantity, candidate feasibility, strategy selection, risk calculation, scenario selection, business constraints, purchase cost, and recommendation. LLM/Qwen does not own these decisions. Narrative/LLM may own wording only where explicitly permitted.
- **Context:** LLM output is probabilistic and cannot be the source of operational truth.
- **Why:** Business outcomes must be reproducible, auditable, testable, and grounded in persisted evidence.
- **Rejected/deferred alternatives:** LLM-led computation, feasibility, recommendation, or constraint enforcement is rejected.
- **Revisit condition:** None without a new ADR and an independently auditable authority model.
- **Affected areas:** All decision services, narrative providers, OpenRouter integration, public decision payloads.

## ADR-003 — Scenario authority separation

- **Status:** ACCEPTED
- **Decision:** Selected Strategy, Conservative Design Scenario, and Stress Scenario are distinct authorities. Conservative or stress shortage/violations must not be described as selected-plan failure unless selected-plan evidence independently supports that conclusion.
- **Context:** The current evidence and narrative guards distinguish `selected_plan`, `conservative_design_scenario`, and stress provenance.
- **Why:** Collapsing these scenarios creates incorrect manager-facing claims.
- **Rejected/deferred alternatives:** Inferring selected-plan failure from conservative or stress evidence is rejected.
- **Revisit condition:** A new scenario model explicitly changes provenance and public semantics.
- **Affected areas:** Decision Package evidence, risk details, Overall Summary, Ingredient Synthesis, Strategy Explanation, What-if.

## ADR-004 — Strategy Explanation default

- **Status:** ACCEPTED / FROZEN
- **Decision:** Strategy Explanation uses deterministic presentation by default. Qwen Strategy Expression is optional `llm_polish` only. The default `GET /brief` path must not invoke Qwen for Strategy Explanation.
- **Context:** Current configuration defaults `strategy_expression_mode` to `deterministic`; current service and tests preserve optional polish.
- **Why:** Strategy facts and selection reasons are already structured and require consistent, authority-safe rendering.
- **Rejected/deferred alternatives:** Qwen as the default strategy presenter is rejected for now.
- **Revisit condition:** Concrete evidence demonstrates a product benefit that outweighs determinism, cost, latency, and guard complexity.
- **Affected areas:** `GET /api/v1/decision-runs/{decision_run_id}/brief`, strategy presentation, strategy expression configuration and tests.

Optional `llm_polish` retains semantic validation, numeric grounding, reason authority, entity authority, whole-set deterministic fallback, and no retry.

## ADR-005 — Ingredient Synthesis default

- **Status:** ACCEPTED / FROZEN
- **Decision:** Ingredient Synthesis uses deterministic presentation by default for NORMAL, WATCH, and CRITICAL. Qwen may remain optional polish where current configuration supports it. Exact `RiskDetail.code` to authorized-evidence alignment remains authoritative for CRITICAL cases. If exact evidence is unavailable, `LIMITED_EVIDENCE` remains fail-closed.
- **Context:** Current configuration defaults `ingredient_synthesis_mode` to `deterministic`; exact evidence and provenance guards remain implemented and tested.
- **Why:** Ingredient severity and operational advice must not be invented or broadened by wording generation.
- **Rejected/deferred alternatives:** Default Qwen synthesis and evidence-by-presence matching are rejected.
- **Revisit condition:** New evidence and tests justify changing the default without weakening exact authority.
- **Affected areas:** Decision Brief ingredient synthesis, semantic evidence, risk metadata, OpenRouter optional polish.

## ADR-006 — Overall Summary remains HYBRID

- **Status:** ACCEPTED
- **Decision:** Overall Summary remains:

  authorized summary facts
  → Qwen primary wording
  → strict backend schema, grounding, provenance, numeric, and business validation
  → PASS: Qwen Summary
  → FAIL: deterministic safe fallback.

  Overall Summary is not deterministic-first. The deterministic Summary is the safe fallback.
- **Context:** Current `OverallSummaryProvider.summarize()` attempts guarded Qwen wording when a provider and recommendation are available; generation failures and validation failures use deterministic fallback. The persisted Summary is generated after the core Decision Package transaction.
- **Why:** Current product intent retains bounded LLM wording value while backend facts and guards remain authoritative.
- **Rejected/deferred alternatives:** Overall Summary deterministic default is **DEFERRED / rejected for now**. Removing all Qwen summary code is not accepted.
- **Revisit condition:** Systematic live Qwen failure, unacceptable latency/cost, demonstrated product improvement from deterministic Summary, or maintenance burden materially outweighing LLM value.
- **Affected areas:** Overall Summary provider, Decision Run assistant persistence, Decision Brief, OpenRouter transport, fallback tests.

The recommendation in `CURRENT_ARCHITECTURE.md` to consider a future deterministic presentation engine for Overall Summary is a **PROPOSAL / REFACTOR OBSERVATION**, not an accepted architecture decision. Strategy and Ingredient defaults do not imply the same migration for Overall Summary.

## ADR-007 — Public contract first during refactor

- **Status:** ACCEPTED
- **Decision:** Refactor preserves existing external API and public behavior by default. Any deliberate contract change requires an explicit old/new contract, Pydantic/OpenAPI changes, contract tests, compatibility impact, frontend/client impact, migration and rollback plan, and explicit approval.
- **Context:** The current surface contains 70 operations and both typed and untyped response boundaries.
- **Why:** Implementation movement must not silently change client-observable behavior.
- **Rejected/deferred alternatives:** Hidden response, status, error, or semantic changes inside refactor tasks are rejected.
- **Revisit condition:** A separately approved contract migration.
- **Affected areas:** API routes, schemas, OpenAPI, tests, frontend integrations, compatibility routes.

## ADR-008 — Incremental refactor only

- **Status:** ACCEPTED
- **Decision:** No big-bang rewrite. Preferred sequence: observe → freeze contract → test → refactor one vertical slice → verify → document → continue.
- **Context:** Computation, planning, persistence, and narrative paths are coupled and have historical compatibility behavior.
- **Why:** Small slices constrain regression and rollback scope.
- **Rejected/deferred alternatives:** Repository-wide structural rewrites and package-wide replacement in one task are rejected.
- **Revisit condition:** None; exceptional scope still requires an explicit new ADR and safety case.
- **Affected areas:** All refactor phases and task planning.

## ADR-009 — Historical Decision Runs are not silently rewritten

- **Status:** SUPERSEDED by ADR-013 — Disposable runtime persistence
- **Superseded decision:** Historical persisted Decision Runs and assistant artifacts had to be retained, read, and evolved through explicit compatibility/migration handling.
- **Reason for supersession:** Current SQLite persistence in the Kaggle deployment is resettable runtime state. Preservation of old database rows or old persisted JSON shapes is not a product requirement. ADR-007 continues to protect public API and client compatibility.

## ADR-010 — Computation package authority

- **Status:** ACCEPTED for current runtime authority; NEEDS DECISION for future consolidation
- **Decision:** In the current runtime, `shelfcash_core` is the production computation authority for forecast, BOM, scenario composition, exact inventory/FEFO simulation, and procurement optimization. `shelfcash_forecast` is the forecast shadow/parity provider when enabled; it is not the production forecast provider.
- **Procurement qualification:** Procurement authority across public/use-case surfaces is further qualified by ADR-014. That ADR records the accepted operational-versus-decision authority separation; it does not change this package-authority decision.
- **Context:** `ForecastService` only permits `forecast_core_provider="existing"`, whose provider delegates to `shelfcash_core`. `shelfcash_forecast` is loaded by `ShelfCashForecastProvider` for shadow comparison. Application decision-intelligence adapters also import non-computation contracts/evidence/retrieval helpers from `shelfcash_forecast`; those imports do not grant it production business authority. Production services directly import `shelfcash_core` for forecast, BOM, FEFO/procurement, and inbound expiry.
- **Why:** This records actual authority without prematurely selecting the end-state package layout.
- **Rejected/deferred alternatives:** “Delete `shelfcash_forecast`” and “make `shelfcash_forecast` canonical” are both deferred. Canonical core consolidation is a future evidence-based decision.
- **Revisit condition:** Per-slice parity measurement, caller inventory, regression evidence, and an explicit canonical-package decision.
- **Affected areas:** `app/services/forecast_service.py`, `app/forecasting/providers.py`, decision adapters, completion service, both computation packages and parity tests.

## ADR-011 — Cutoff chronology

- **Status:** ACCEPTED
- **Decision:** The cutoff/as-of date is the end-of-day snapshot boundary for day D. Future forecast, procurement, FEFO, stress, and What-if planning begins at D+1.
- **Context:** `app/core/business_time.py`, forecast horizon construction, inventory adapters, and chronology tests use this invariant.
- **Why:** It prevents historical observations and planned future activity from overlapping by one day.
- **Rejected/deferred alternatives:** Starting future demand or simulation on D is rejected.
- **Revisit condition:** A new business-time contract with explicit migration and regression coverage.
- **Affected areas:** Forecast, inventory snapshots, FEFO, procurement, scenarios, Decision Runs, What-if.

## ADR-012 — Manager-safe semantic invariants

- **Status:** ACCEPTED
- **Decision:**
  - `fill_rate` means “tỷ lệ đáp ứng nhu cầu”.
  - `CAPACITY_NOT_EVALUATED` means capacity was not evaluated, not exceeded.
  - `STRESS_SHORTAGE_OBSERVED` does not automatically reject a strategy.
  - `STRESS_CAPACITY_VIOLATION` does not automatically reject a strategy.
  - Conservative shortage does not mean selected-plan shortage.
- **Context:** Current risk classification, strategy outcome projection, deterministic presentation, and LLM validation tests enforce these meanings.
- **Why:** These distinctions prevent operational warnings from becoming false claims or hard constraints.
- **Rejected/deferred alternatives:** “Tỷ lệ lấp kho”, capacity-exceeded wording without evidence, and automatic rejection from stress-only evidence are rejected.
- **Revisit condition:** An explicit change to business semantics and every affected contract/test.
- **Affected areas:** Risk details, strategy reasons, ingredient synthesis, Overall Summary, What-if, frontend copy.

## ADR-013 — Disposable runtime persistence

- **Status:** ACCEPTED
- **Decision:** SQLite persistence in the current Kaggle deployment is disposable runtime state. Backward compatibility for old database rows, including prior Decision Package JSON shapes and historical Decision Runs, is not an architecture requirement.
- **Supersedes:** ADR-009's persisted-history preservation and old-shape read/migration requirements.
- **Important boundary:** Database compatibility is not public API compatibility. This ADR does not supersede ADR-007, ADR-008, backend business authority, narrative routing, or existing HTTP/client contract expectations.
- **Why:** The deployment database is resettable. The cost and complexity of preserving or migrating old ephemeral rows does not provide proportional product value.
- **Consequences:** A database reset, destructive schema cleanup when needed, removal of code that exists solely to read old persisted shapes, and no backfill or migration framework for old Decision Runs are permitted. Indefinite `dict.get` fallbacks that exist solely for old rows are not required.
- **Not permitted:** Hidden HTTP payload or business-semantic changes, removal of legacy HTTP/client compatibility solely because old database data is disposable, or breakage of the current frontend contract.
- **Affected areas:** SQLite/Alembic policy, Decision Run persistence, persisted JSON readers, historical-fixture tests, and future persistence cleanup.

## ADR-014 — Procurement authority separation

- **Status:** ACCEPTED
- **Decision:** ShelfCash intentionally maintains two deterministic procurement authorities for distinct public/use-case surfaces. **Operational Procurement Authority** is `POST`/`GET /api/v1/stores/{store_id}/forecast-runs/{forecast_run_id}/procurement-plans` through `DecisionPlanningService.generate_plans -> ProcurementPlanningService.build -> InventorySimulationService`; it owns current operational `ProcurementPlanRun` computation and persistence semantics. **Decision Procurement Authority** is Decision Run and What-if through `CoreProcurementAdapter -> shelfcash_core`; it owns decision candidate optimization, exact FEFO resimulation, critic validation, deterministic feasibility, risk/scenario evaluation, strategy selection and recommendation. Operational Procurement Authority != Decision Procurement Authority. Neither may silently replace the other.
- **Context:** R3.4 established material semantic differences: open-PO/expiry interpretation, supplier preselection, Decimal MOQ/pack behavior, shelf-life/FEFO simulation, budget timing, capacity, feasibility, risk/reason codes, recommendation/tie-breaking, and daily projections. Replacing the operational planner with core is therefore not a mechanical caller migration. These authorities are not two implementations competing for one interchangeable procurement truth; they own separate contracts and use cases.
- **Why:** This records accepted product architecture without an unsafe business-semantic, public-contract, or persistence migration. Both paths remain deterministic backend computation under ADR-002 and preserve shared applicable ADR-011 chronology and ADR-012 manager-safe semantics.
- **Consequences:** Dual authority itself is accepted architecture, not authority ambiguity debt. Duplicated implementation, overlapping helpers/calculations, and maintenance cost remain technical debt where evidenced. Global legacy/core output parity is intentionally not required; targeted characterization of shared invariants such as EOD D -> D+1 chronology and intended unit interpretation remains useful.
- **Rejected/deferred alternatives:** Core for every procurement surface is DEFERRED. A core-backed compatibility adapter is DEFERRED. Silent caller replacement is REJECTED.
- **Revisit condition:** A separately approved convergence decision must define old/new business semantics, public and persistence impact, parity/characterization evidence, client impact, migration, rollback, and explicit architecture approval.
- **Affected areas:** `/procurement-plans`, `ProcurementPlanningService`, `InventorySimulationService`, `CoreProcurementAdapter`, Decision Run, What-if, `ProcurementPlanRun`, legacy plan adapters, and procurement characterization/parity tests.

## ADR-015 — Decision Planning use-case decomposition

- **Status:** ACCEPTED
- **Decision:** `DecisionPlanningService` may be incrementally decomposed into focused application use-case services with explicit responsibilities. During migration it may remain a temporary facade so current route/caller contracts stay stable and each extraction has a small rollback scope. Exact class names, service count, final composition-root shape, and final route wiring are not frozen by this decision.
- **Required invariants:** Each extraction preserves ADR-007 public routes, request/response DTOs, status codes, error semantics, and OpenAPI; current deterministic business semantics and ADR-010 computation authority; ADR-014's Operational Procurement Authority != Decision Procurement Authority; current session ownership, commit ordering, rollback behavior, run-status lifecycle, idempotency ownership, and failure persistence; and active legacy compatibility. The deterministic `DecisionRun` package must persist and commit before assistant summary / ingredient-synthesis enrichment is persisted separately; assistant/narrative failure must not invalidate committed deterministic business truth. R2 narrative ownership and routing, including HYBRID Overall Summary, remain unchanged.
- **Context:** R4 Entry Review established that `DecisionPlanningService` currently spans Ingredient Demand, Operational Procurement, active legacy plan compatibility, Decision Run generation/read, Brief/enrichment, Explanation, What-if, and shared orchestration/persistence concerns. Its responsibilities have materially different transaction and coupling profiles, making an incremental use-case/facade direction an architecture decision rather than simple file movement.
- **Why:** Focused application-use-case boundaries can reduce responsibility coupling and change blast radius while preserving established computation, contract, transaction, procurement, compatibility, and narrative authorities.
- **Rejected/deferred alternatives:** A big-bang `DecisionPlanningService` rewrite and a generic replacement `PlanningService`/God service are rejected. Immediate route rewiring for every use case and freezing an exact final class layout are deferred. Persistence/Unit-of-Work redesign remains R6 work; legacy lifecycle redesign remains R5 work.
- **Migration guidance:** Every slice observes the current method; freezes contract, transaction, idempotency and failure semantics; extracts one proven application intent; delegates through the existing facade where useful; runs targeted and regression tests plus OpenAPI verification; documents the result; and then evaluates the next slice. A focused boundary must have one clear application intent, one explicit authority model, known contract/transaction/persistence dependencies, a small caller set, and a small rollback scope.
- **Revisit condition:** Evidence shows incremental decomposition increases coupling, requires semantic or contract changes, or an explicitly approved alternative application-ownership model is selected.
- **Affected areas:** `DecisionPlanningService`, planning routes, composition root, Ingredient Demand, Operational Procurement, Decision Run, Brief, Explanation, What-if, legacy compatibility, and transaction/idempotency tests.

## ADR-016 — Stochastic risk evidence sufficiency gate

- **Status:** ACCEPTED
- **Decision:** Stochastic stockout-risk constraints have business authority only when the generated stochastic ingredient-demand paths pass an explicit effective-scenario sufficiency gate. The current minimum is `MIN_EFFECTIVE_STOCHASTIC_SCENARIOS = 10`. Paths are canonicalized by their exact business demand vector, duplicate-path weights are aggregated, and `N_eff = 1 / sum(w_i ** 2)` is calculated on normalized unique-path weights. When `N_eff < 10`, Decision Procurement falls back to the existing deterministic p25/p50/p75 design scenarios, records `stochastic_saa_enabled=false` and `stochastic_fallback_reason=insufficient_effective_scenarios`, and does not grant `RISK_CONSTRAINT_VIOLATION` authority from that insufficient set.
- **Context:** Generated records can have distinct random IDs while representing one identical ingredient-demand path. Treating those records as independent observations can turn duplicate bootstrap output into an apparent empirical stockout probability of zero or one and incorrectly hard-reject every candidate.
- **Why:** Insufficient stochastic evidence is not equivalent to high stochastic risk. The gate preserves deterministic backend authority, auditable scenario provenance, and existing feasibility semantics without inventing confidence that the evidence does not support.
- **Preserved authority:** The deterministic design fallback still runs Exact FEFO and the critic. `EXACT_SIMULATION_SAFETY_FLOOR`, service-level, budget, supplier, MOQ, pack, capacity, lead-time, expiry, accounting, model-match and every other existing hard rule retain their current authority. Existing LEAN/BALANCED/PROTECTED thresholds and the lowest-exact-valid-cost-then-strategy-name selection rule are unchanged.
- **Explicit non-goals:** This does not relax strategy stockout thresholds, vary a threshold by scenario count, introduce confidence intervals or Bayesian bounds, change Exact FEFO, change deterministic design scenarios, change supplier/MOQ/budget/capacity/service-level semantics, change the optimizer objective, change Overall Summary or LLM authority, redesign public API beyond additive diagnostics, or rewrite/backfill historical Decision Runs.
- **Rejected/deferred alternatives:** Treating record count as independent evidence, inferring risk probability from an insufficient sample, and dynamic threshold relaxation are rejected. Statistical interval/bound approaches are deferred to a separately approved slice.
- **Rollback:** Remove only the scenario-sufficiency helper, Decision adapter gate/routing, additive diagnostics, focused tests, and this ADR/checkpoint documentation. No schema migration, data migration, or historical rewrite is required.
- **Affected areas:** `shelfcash_core.scenario`, `CoreProcurementAdapter`, Exact FEFO risk/critic handoff, Decision Package technical diagnostics, Decision Run persistence, and stochastic regression tests.

## Deferred proposals requiring a future decision

The following are not accepted target architecture: merging the computation packages; deterministic-first Overall Summary; removing all Qwen; adopting Unit of Work everywhere; rewriting persistence; replacing SQLite; renaming or moving modules; and removing legacy routes. Exact final `DecisionPlanningService` layout remains intentionally unfrozen; ADR-015 governs its incremental decomposition. The remaining items are proposals or technical debt until separately approved.
