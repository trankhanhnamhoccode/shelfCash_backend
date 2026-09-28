# CHAT-7 Conversational Quality Evaluation

## Decision

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
