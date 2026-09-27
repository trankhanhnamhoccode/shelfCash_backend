# Frontend contract: `/explanation` as a chat interaction

## Decision

Keep the current HTTP semantics. A `200` means the backend produced a
grounded explanation (including a deterministic fallback). A `422` means the
request could not safely be answered as asked. The frontend must nevertheless
render a `422` **inside the conversation as an assistant message**, not as a
page-level error, toast-only notification, or automatic page reload.

The frontend must branch on `code`, never on the server's human-readable
`message`. It must not turn a `422` into a retry loop, silently replace an
ingredient, or invent an answer locally.

## Request

```http
POST /api/v1/decision-runs/{decision_run_id}/explanation
Content-Type: application/json
```

```ts
type ExplanationRequest = {
  language?: "vi" | "en";             // default: "vi"
  detail_level?: "simple" | "manager" | "technical"; // default: "simple"
  question?: string | null;             // max 2000 characters
  ingredient_id?: string | null;         // preferred for row-level actions
};
```

For an action started from a demand/procurement row, send that row's canonical
`ingredient_id`. For a fully free-text chat question, omit it and let the
backend resolve a name/approved alias conservatively.

## Transport response union

```ts
type ExplanationSuccess = {
  source: string;
  language: "vi" | "en";
  detail_level: "simple" | "manager" | "technical";
  decision_run_id: string;
  answer: string;
  summary: string;
  why_this_plan: string[];
  main_risks: string[];
  tradeoffs: string[];
  important_assumptions: string[];
  intent: string;
  entities: { ingredient_ids: string[]; supplier_ids: string[] };
  claims: Array<{ type: string; value: unknown; unit?: string; evidence_ids: string[] }>;
  citations: Array<{ evidence_id: string; label: string; source_type: string }>;
  grounded: boolean;
  provider: string;
};

type ApiError = {
  code: string;
  message: string; // diagnostics/fallback only; do not branch on this
  details: Record<string, unknown>;
  request_id: string;
};
```

`200` returns `ExplanationSuccess`. `422` returns `ApiError`. Other existing
resource/availability errors retain their normal status (`404`, `503`, etc.).
Do not render `raw_response` or `llm_diagnostics` in the customer-facing UI.

## Chat presentation contract

The submitted user text always remains visible. Replace the transient typing
state with exactly one assistant message for both a success and a handled
error.

```ts
type ExplanationChatMessage =
  | {
      kind: "answer";
      text: string; // ExplanationSuccess.answer
      response: ExplanationSuccess;
      showEvidence: boolean; // true only when this product surface supports it
    }
  | {
      kind: "guidance";
      reason:
        | "unsupported"
        | "ingredient_not_found"
        | "ingredient_ambiguous"
        | "ingredient_mismatch"
        | "ingredient_unavailable"
        | "invalid_request";
      text: string;
      suggestedPrompts?: string[];
      candidates?: Array<{ ingredient_id: string; ingredient_name: string }>;
      retry?: { question: string; ingredient_id?: string };
      requestId: string;
    };
```

## Required `422` mapping

| Backend code | Assistant bubble (VI) | UI action | Retry rule |
| --- | --- | --- | --- |
| `EXPLANATION_QUERY_UNSUPPORTED` | “Mình chỉ có thể giải thích kế hoạch, chiến lược, rủi ro hoặc nguyên liệu trong Decision Run này. Bạn có thể hỏi, ví dụ: ‘Tại sao chọn Balanced?’ hoặc ‘Tại sao cần nhập [nguyên liệu]?’” | Show prompt chips. | User must choose/type a new question. Do not resend automatically. |
| `INGREDIENT_RESOLUTION_NOT_FOUND` | “Mình chưa xác định được nguyên liệu này trong Decision Run hiện tại. Hãy chọn một nguyên liệu trong kế hoạch hoặc hỏi về kế hoạch chung.” | Offer a “Xem nguyên liệu trong kế hoạch” action; optionally show row chips already loaded by the Brief. | Resend only after the user chooses a row, with its `ingredient_id`. |
| `INGREDIENT_RESOLUTION_AMBIGUOUS` | “Có nhiều nguyên liệu có thể khớp với câu hỏi. Bạn muốn hỏi nguyên liệu nào?” | Render `details.candidates` as selection buttons. | On selection, resend the original question with the selected `ingredient_id`. |
| `INGREDIENT_QUESTION_MISMATCH` | “Nguyên liệu bạn chọn khác với nguyên liệu được nêu trong câu hỏi. Hãy chọn đúng nguyên liệu hoặc sửa lại câu hỏi.” | Show “Giữ nguyên nguyên liệu đã chọn” and “Đổi nguyên liệu”. | Never silently choose either. Require an explicit user choice. |
| `DECISION_RUN_INGREDIENT_NOT_FOUND` | “Nguyên liệu này không còn có dữ liệu trong Decision Run đang xem.” | Disable the stale row action; offer “Hỏi về kế hoạch chung”. | Do not retry with another ingredient automatically. |
| request validation / unrecognized `422` | “Câu hỏi chưa đúng định dạng. Hãy thử lại với một câu ngắn hơn.” | Keep the draft editable. | User edits and resubmits. |

When rendering `INGREDIENT_RESOLUTION_AMBIGUOUS`, read candidates only from
`details.candidates`; do not search/map names locally. Candidate selection is
the one allowed automatic construction of a retry request, because it is an
explicit user choice.

## `200` degradation and non-chat errors

| Condition | UI behavior |
| --- | --- |
| `200`, `provider: "deterministic_fallback"` | Render as a normal answer. Do not show an outage/error to the user. |
| `200`, no feasible plan wording | Render as a valid business result, not an error. |
| `404 DECISION_RUN_NOT_FOUND` | Leave the chat transcript visible; show a stale-resource state with a return/reload action. |
| `503` or network failure | Keep the user message and render a retry bubble: “Chưa thể lấy lời giải thích lúc này. Thử lại.” Retry only after the user presses the button. |

## Suggested client algorithm

```ts
async function askExplanation(request: ExplanationRequest) {
  appendUserMessage(request.question ?? defaultQuestion(request.language));
  appendTypingMessage();

  const result = await postExplanation(request);
  removeTypingMessage();

  if (result.status === 200) {
    appendAnswerMessage(result.body.answer, result.body);
    return;
  }
  if (result.status === 422) {
    appendGuidanceMessage(mapExplanation422(result.body, request));
    return;
  }
  appendRecoverableTransportMessage(result);
}
```

The page must not reload for a handled `422`: the Decision Run and existing
chat context remain valid. Store `request_id` with the guidance message for
support/debugging, but do not show it by default.
