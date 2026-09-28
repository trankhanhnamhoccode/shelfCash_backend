# CHAT-7 Conversational Quality Evaluation

## Decision

## CHAT-8 follow-up (2026-09-29)

Manual live evidence supplied by the user exposed five cases: (A) available budget denied by Qwen yet accepted grounded; (B) invented Cam procurement causality and shortage; (C) inverted negative alignment fallback; (D) UI `40 quả Cam` with authoritative `kg`; (E) repeated backticks ending at 1,200 tokens. CHAT-8 reproduces A/B/C and token-limit fallback with deterministic fixtures and verifies the backend serialized response for D uses `40 kg Cam`. The exact live UI response was not available, so D remains a frontend display boundary investigation. Existing provider token-limit handling remains unchanged.

Root causes: Budget guard previously checked the evidence category but no boolean/state polarity. The ingredient why brief omitted the available baseline and exposed neighboring numerical fields without their semantic roles. The deterministic alignment renderer used `absolute_gap_magnitude` and always said “above.” The backend unit path reads `ProcurementRowBrief.unit`; no ingredient-name unit heuristic was found in this response path. Procurement reason codes are not authoritative reason facts.

No `OPENROUTER_API_KEY` is present in this environment or local `.env`, so CHAT-7 corpus and seven specified manual questions could not be replayed against Qwen. Live call counts, accept/repair/fallback frequency, and token-limit recurrence remain **not measured**. No provider/model settings changed. The next evaluation should use the existing CHAT-7 profile and compare raw Qwen output with final backend answer by `ACCEPTED`, `REPAIRED`, `SENTENCE_REMOVED`, or `FALLBACK`.

Deterministic RAW/FINAL reproductions: available budget + “Không có dữ liệu ngân sách…” → `FALLBACK` to persisted budget status; not-exceeded budget + “đang vượt ngân sách” → `FALLBACK`; 40 kg purchase + 58.88 kg p50 + invented causal shortage → `FALLBACK`; token-limit error (`TOKEN_LIMIT`) or JSON-valid punctuation run → `FALLBACK`. No live accepted/repaired/trimmed counts are claimed. CHAT-8 regressions: **17 passed, 1 warning**; full suite: **764 passed, 22 warnings**. Remaining live issue classification: `PROVIDER` recurrence unmeasured; `FRONTEND_DISPLAY` exact observed card response unavailable; `CONTEXT` exact procurement-reason decomposition absent from deterministic facts. Raw risk codes remain contract-facing diagnostic strings; manager-facing mapping belongs to frontend UX work.

**PARTIAL — provider quality is not measured.** The local runtime has no `OPENROUTER_API_KEY`; no model call, fabricated answer, or quality conclusion was made.

## Environment

- Provider configured: `false`
- Model/profile retained: `qwen/qwen3.5-9b`, SiliconFlow-only route, temperature `0.0`, max tokens `1200`, reasoning `false`.
- Transport: strict JSON `{ "answer": "string" }`.
- No model, setting, prompt, routing, brief, or fallback-policy change made.

## Stable fixture and corpus

The harness is deterministic development support only. It accepts an injected Decision Explanation response and captures provider, intent, grounded status, claims/citations count, fallback stage, and repair observability. It neither creates a Decision Run nor changes business facts. A real run must reuse the existing deterministic budget, strategy, ingredient, and What-if fixtures in the Decision Explanation tests.

The corpus covers budget status/utilization/remaining/overage; absolute and relative budget What-if; strategy selection, risk, cost, and trade-offs; ingredient reason, no-purchase, and quantity; natural Vietnamese wording; and three intentionally ambiguous referents. Three three-turn conversations cover budget, strategy, and ingredient continuity.

## Actual results

| Metric | Result |
|---|---|
| Real Qwen calls | 0 |
| Provider failures | Not applicable: unavailable before call |
| Grounding rejections | Not measured |
| Numeric repair/removal | Not observable in current diagnostics; not inferred |
| Routing/context/naturalness/directness | Not measured without provider output |
| Harness tests | 2 passed, 2 warnings |

No good/bad answer examples exist because no provider answer was generated.

## Deterministic observations

Runtime trace is request/history resolution → `QuestionScope` → semantic retrieval → compact business brief + task hint + bounded history → `CONVERSATIONAL_EXPLANATION` → answer-only parse → sentence-level backend guard/repair → public claims/citations/metadata. Current diagnostics record provider failure stage (for example HTTP, schema, grounding), but do not expose a dedicated numeric-repair counter; the harness records this as `not_observable` rather than claiming none occurred.

## Recommendation

**D. No change yet; collect more evidence.** Configure a non-production OpenRouter credential and execute the corpus once against a stable existing Decision fixture. Only then compare directness, naturalness, fallback stages, repair frequency, context resolution, and business-brief sufficiency before considering prompt, intent, or model-setting work.
