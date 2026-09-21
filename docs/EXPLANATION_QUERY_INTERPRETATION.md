# Explanation Query Interpretation

## Current implemented boundary

Explanation interprets a user question in three separate deterministic steps:

1. query purpose (generic explanation, ingredient explanation, unsupported);
2. canonical ingredient identity when ingredient explanation is requested;
3. persisted Decision Run evidence selection.

An ingredient mention is not, by itself, a procurement-explanation request.
For example, `Tôi muốn ăn chuối?` is unsupported rather than an explanation of
why Chuối is purchased. Uninterpretable text is likewise unsupported.

Unsupported requests return the existing error envelope with HTTP 422 and
`EXPLANATION_QUERY_UNSUPPORTED`; details contain only
`query_classification: "unsupported"`. This is preferred over a generic
success response because it prevents invented plan narration and needs no
success DTO change.

## Question numbers

Numbers in a question are context, never Decision Run facts. Grounded LLM
claims may contain only authorized numeric display values from selected
evidence. A deterministic ingredient fallback recognizes an explicit purchase
quantity only when it is adjacent to a purchase verb and uses the existing
canonical unit normalizer. If it differs from the persisted procurement
quantity in the same normalized unit, it states a correction while citing and
claiming only the persisted quantity. It does not convert units or infer that
unrelated values such as horizon days are procurement quantities.

No query interpretation result is persisted, and query text never changes
forecast, procurement, or any Decision Package business fact.
