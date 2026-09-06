# Input admission receipts

Clients negotiate `input.receipts` through `runtime.capabilities`. Send `submit` or `steer` with a unique `idem` and a `request_id`; using the same value for both is supported. Each must be a nonempty string of at most 128 characters. Keep the draft until admission is known.

A correlated `input.result` contains `session_id`, `op`, `request_id`, `idem`, `input_id`, `serve_instance`, `ok`, and `stage`. The input ID equals `idem` when supplied; request-only inputs receive a generated ID. Records contain no prompt text.

| Stage | Meaning |
|---|---|
| `dispatched` | The serve loop scheduled the turn. |
| `queued` | The steering queue accepted the input. |
| `rejected` | The input was not admitted; `code` explains the reason. |

Neither successful stage claims model execution, context injection, completion, or durability of queued work. An accepted idempotent input receives a successful `control.ack` only after scheduling or enqueueing. Its receipt is remembered in the bounded control ledger. Invalid input, busy submit, full steering queue, or authorization refusal receives explicit rejection without a successful acknowledgement or remembered rejection. A user may correct a rejected request and reuse its ID. Uncorrelated legacy successful writes retain their existing output stream; legacy invalid writes receive explicit rejection.

Read a receipt with `{"op":"input.status","input_id":"...","request_id":"lookup-1"}`. This operation requires READ permission and never executes an input, acquires a write lease, or resends a prompt. The flat response has `type: input.status`, correlation fields, `serve_instance`, `ok`, and `stage`. A current-instance receipt reports its recorded admission stage, not the current queue or execution state.

Every serve loop has a new instance token. A receipt from another instance yields `ok:false`, `stage:unknown_previous_instance`, matching `code`, and `original_serve_instance`. Work could have executed before the earlier process ended; this response must never trigger automatic retry. Missing or evicted receipts yield `unknown`, with the same no-retry rule. Use session history and explicit user review to resolve uncertainty. Replaying an existing idempotency key retains its original records and original instance token; it does not re-execute the input.

Only the most recent 256 receipts are kept in serve memory. Idempotent receipts also use the existing bounded persistent control ledger; request-only receipts are memory-only. Admission and persistence are not an atomic transaction with model execution. A crash after scheduling but before receipt persistence can leave an unknown result. There is no durable prompt outbox or exactly-once execution claim. Approval and other control operations retain their existing acknowledgement semantics.
