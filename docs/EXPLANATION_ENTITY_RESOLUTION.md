# Explanation Ingredient Entity Resolution Contract

## Status and scope

- **CURRENT (Prompt 3):** `/decision-runs/{id}/explanation` accepts an
  optional `ingredient_id`. If supplied, it is checked directly against the
  persisted package's `ingredient_demand[]` and `recommended_plan.items[]`
  before Brief construction or an LLM call, then scopes evidence to that ID.
  No current catalog lookup is required for a run-valid ID. Without an ID,
  question text remains legacy narration retrieval behavior and is not a
  reliable entity-resolution contract. Question-only requests now resolve
  canonical catalog names, approved aliases, lookup-normalized names, and
  conservative typos before entering that same ID-scoped path.
- **ACCEPTED AUTHORITY:** the backend owns canonical identity and persisted
  Decision Run membership; Qwen only words already-selected evidence.
- **LEXICAL SAFETY POLICY:** the deterministic fuzzy thresholds below are
  implemented typo-recovery safeguards, not business truth. They may change
  only in an evidence-backed maintenance change with resolver regression tests.
- **DEFERRED:** any LLM-assisted mention-span extraction and any new persisted
  snapshot name/alias representation.

This concerns only which canonical ingredient an Explanation targets.  It
does not compute forecast, BOM, inventory, procurement, feasibility, risk, or
strategy.

## Authorities

The backend owns canonical ingredient identity and the persisted Decision Run
evidence it selects.  Qwen may phrase already-authorized evidence; it must not
select, translate into, or otherwise assign a canonical `ingredient_id`.

Canonical catalog identity is per-store:

- `ingredients.ingredient_id` is the stable canonical identifier;
- `ingredients.ingredient`, `normalized_name`, and `sku` are catalog fields;
- `ingredient_aliases.ingredient_id`, `alias`, and `normalized_alias` are the
  approved alias mechanism, unique by `(store_id, normalized_alias)`;
- `normalize_name` is NFC, whitespace-normalized, case-folded exact lookup;
  `normalize_lookup_name` is accent/case/spacing insensitive, but is not used
  by the current catalog repository resolver.

Import persistence already uses `EntityResolutionService`: ID, SKU, registered
alias, then canonical normalized name (and can create an import ingredient).
Explanation must reuse this catalog/alias authority where it fits; it must not
create another ingredient registry or create ingredients.

## Decision Run universe

For a target run, the authoritative ingredient universe is the union of the
canonical IDs in the persisted package's `ingredient_demand[]` and
`recommended_plan.items[]`.  This is the same practical set currently used by
`ExplainDecision._require_decision_ingredient` through the Brief's demand and
procurement rows.

`risk_details`, `ingredient_synthesis`, and semantic evidence are derived
views/evidence, not independent membership sources.  They can enrich an
already-authorized explanation but cannot make a catalog ingredient a target.

Current Brief construction obtains display names from the current catalog for
IDs found in the persisted package.  Therefore a catalog rename can change a
display label on a later Brief read, although it does not change the package ID
or evidence identity.  Future resolution must read package IDs for membership
and evidence.  Current catalog names/aliases may be used only to resolve user
text to an ID; after that, the ID must be filtered to the package universe.
They must never provide current catalog business facts as historical evidence,
write/backfill the package, or replace a package ID.

The selected candidate-universe model is **C**: look up canonical names and
registered aliases in the store catalog, then filter candidate IDs to the
target Decision Run universe.  All-active-catalog lookup alone leaks unrelated
targets; run-only lookup loses registered aliases.  Catalog state is a lookup
aid, not historical truth.

## Accepted resolution flow

### Supplied ID (ID-first)

1. Validate request syntax.
2. Read the target run's persisted package universe.
3. Validate the supplied canonical ID is a member of that universe.
4. Retrieve only that ID's semantic evidence plus permitted run-level context.
5. Explain it.

`ingredient_id` is the authoritative selector.  Question text is context and
must never silently redirect it.  A live catalog lookup is not required to
invalidate a historical package ID; package membership is the evidence-safe
identity check.

### No supplied ID (free text)

1. Deterministically find a mention from known canonical/registered alias
   terms.  The first implementation must not require an LLM for this step.
2. Resolve exact canonical name, then exact registered alias, then the accepted
   normalization policy.
3. Later, apply constrained fuzzy matching only if it has a unique,
   high-confidence candidate within the run universe.
4. If one candidate remains, validate it against the run universe and use the
   ID-first evidence path.
5. If none or more than one acceptable candidate remains, fail closed.

The ordering records resolver authority, not a promise that raw substring
search is sufficient mention extraction.  If future LLM assistance proposes a
span, the backend resolver still performs every matching and ID assignment
step.

## Mismatch, ambiguity, and not-found

### ID plus conflicting text

**Accepted:** fail closed on a confident conflict.  When the supplied ID is
run-valid and deterministic text resolution uniquely and confidently resolves
the question to a different run-valid ID, return:

```json
{
  "code": "INGREDIENT_QUESTION_MISMATCH",
  "message": "Nguyên liệu trong câu hỏi không khớp với ingredient_id.",
  "details": {
    "ingredient_id": "ingredient-banana",
    "question_ingredient_id": "ingredient-mango",
    "resolution_source": "canonical_exact"
  },
  "request_id": "..."
}
```

Status is **422**.  Do not make a mismatch from ambiguous, weak, or merely
semantic/translated text.  This detects FE wiring defects and avoids answering
one ingredient when the user clearly asked about another; the confidence gate
limits false positives.

### Supplied ID invalid or outside the run

Use the existing **422** `DECISION_RUN_INGREDIENT_NOT_FOUND` for an ID that is
not a member of the package universe, whether it is unknown, belongs to
another store, or is catalog-known but run-absent.  Details remain
`{decision_run_id, ingredient_id}`.  This intentionally does not disclose
whether the ID exists in the current catalog.  A current catalog `active` flag
does not invalidate a historical package member.  Empty/too-long values retain
ordinary request validation `422` behavior.

### Text not found or not relevant to this run

Use **422** `INGREDIENT_RESOLUTION_NOT_FOUND`:

```json
{
  "code": "INGREDIENT_RESOLUTION_NOT_FOUND",
  "message": "Không xác định được nguyên liệu từ câu hỏi.",
  "details": {"mention": "saffron", "resolution_scope": "decision_run"},
  "request_id": "..."
}
```

This is also the manager-safe public result when text resolves only to a
current catalog ingredient outside the target run.  Internal telemetry may
distinguish catalog-miss from run-scope rejection, but the response must not
leak unrelated catalog identity.

### Text ambiguous

Use **422** `INGREDIENT_RESOLUTION_AMBIGUOUS`:

```json
{
  "code": "INGREDIENT_RESOLUTION_AMBIGUOUS",
  "message": "Không xác định được nguyên liệu duy nhất từ câu hỏi.",
  "details": {
    "mention": "bột",
    "candidates": [
      {"ingredient_id": "...", "ingredient_name": "Bột mì"},
      {"ingredient_id": "...", "ingredient_name": "Bột gạo"}
    ]
  },
  "request_id": "..."
}
```

Candidates must be deterministic, deduplicated, and restricted to the target
run universe.  FE may ask the user to select one and resend that canonical ID.
These codes use the existing ShelfCash `{code, message, details, request_id}`
envelope; they are future endpoint semantics, not current runtime responses.

## Multilingual and typo policy

No translation model or inferred dictionary is identity authority.  `banana`
may resolve to canonical `Chuối` only when the store has an explicitly
registered canonical alias/synonym that maps it to `ingredient-banana` (or a
future approved equivalent catalog mechanism).  Without one, it is not found;
Qwen must not decide that it means `Chuối`.

Constrained fuzzy matching runs only after deterministic exact and authorized
alias matching, only within the target run candidate universe, and only for
one high-confidence candidate. It uses Python standard-library
`difflib.SequenceMatcher` over `normalize_lookup_name` word tokens: both the
question token and candidate token must have at least four characters; the
winner must score at least `0.88` and lead the runner-up by at least `0.12`.
Scores below `0.70` are ignored altogether so generic words cannot become a
not-found ingredient mention. This admits the tested `chúi` → `Chuối` typo
(`chui`/`chuoi`) while rejecting short-token recovery and weak/unrelated text.
Multiple plausible high-score candidates are ambiguous; weak candidates are
not found. No semantic translation is attempted.

For supplied-ID mismatch detection, exact/alias/normalized unique resolution
is safe. A fuzzy alternative is mismatch-safe only at the stricter `0.95`
score and `0.15` margin; otherwise the supplied ID remains authoritative.

These values are a **lexical resolution safety policy**, not forecast,
procurement, or strategy business semantics. They must not change silently:
any adjustment requires focused resolver regression evidence for positive,
weak, competing, and short-token cases.

## FE boundary

For an action started from a Brief procurement/demand row, FE must send that
row's `ingredient_id`; it must not name-search, fuzzy-match, translate, or map
aliases locally.  For a wholly free-form question, FE may omit the ID and the
backend resolver owns the result.  On ambiguity, FE may show server candidates
and retry with the selected ID.  FE never supplies a locally inferred ID as if
it were canonical.

## Future acceptance matrix

1. Run-valid supplied ID returns ingredient-specific evidence.
2. Supplied ID remains authoritative when the question has no name.
3. Unknown supplied ID fails closed.
4. Catalog-valid but run-absent supplied ID fails closed.
5. Question text never silently overrides a supplied ID.
6. Banana ID plus confidently resolved mango text returns mismatch.
7. Ambiguous text does not create a mismatch.
8. Canonical exact text resolves.
9. Registered alias exact text resolves.
10. Case/diacritic handling follows the accepted normalization policy.
11. One strong unique typo may resolve.
12. Multiple plausible typos are ambiguous.
13. A weak typo is not found.
14. An explicitly registered English alias resolves.
15. Unregistered English text is not translated into an ID.
16. A catalog rename does not change persisted historical evidence ID.
17. Every text-resolved ID is relevant to the target run.
18. Resolution performs no historical package mutation/backfill.
19. Qwen cannot assign a canonical ID.
20. Resolver behavior is deterministic without OpenRouter.

## ADR disposition

**Existing ADR coverage sufficient.** ADR-002 already establishes backend
business authority and LLM wording-only limits; ADR-007/ADR-008 require an
explicit, tested public-contract slice; ADR-015 preserves Explanation's
focused orchestration boundary.  This document applies those accepted rules to
identity-controlled evidence retrieval without creating a duplicate ADR.

## Next implementation slice

**Prompt 2 — Implement ID-first ingredient-specific Explanation path** may
implement the run-universe validator and ID-scoped evidence path, preserve the
existing envelope/status semantics, add targeted characterization/acceptance
tests, and update implementation-facing documentation.  It must not implement
fuzzy matching, translation, a new alias table, frontend code, package
mutation/backfill, or business computation changes unless separately scoped.

**Prompt 2 status: COMPLETE.** Its supplied-ID validation and evidence scope
remain the single ingredient-specific Explanation path.

**Prompt 3 status: COMPLETE.** Question-only catalog/alias lookup reflects
the current store catalog and aliases, then filters to the historical package
universe. A historical package with no current catalog record remains
explainable by supplied ID, but cannot gain a question-only display-name match
until a current catalog/alias identity aid exists. No package or alias is
mutated on read.
