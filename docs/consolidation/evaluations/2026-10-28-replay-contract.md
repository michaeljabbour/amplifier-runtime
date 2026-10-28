# Replay envelope conformance — 28 October 2026

The exported client-data contract now describes the runtime's existing durable
replay envelope. Before the change, `_history_replay_records` produced
`runtime.event` with `replay: true` and a numeric ledger timestamp, while the
new contract accepted only the one-shot ISO timestamp envelope.

The correction adds `ReplayedRuntimeEvent` alongside the existing one-shot
models and includes it in the generated data schema. It retains the existing
normalized UIEvent union. It does not change the producer, stored events,
one-shot factory, approval authority or TUI dependency pin. History boundaries,
transcript fallback and approval control tickets remain outside this schema.

Preregistration, the original failed record, the candidate record, source
import paths and raw check output are retained in the campaign's
`research/evaluations/2026-10-28/runtime-replay-contract/`. The registration's
inline event was an illustrative placeholder; the baseline and candidate used
the actual serve producer as specified. That deviation is recorded separately.

Twenty new deterministic cases exercise fresh event/cursor/limit combinations,
unchanged one-shot shape and negative envelope values. The implementer wrote
these after the candidate; they are conformance checks, not an independent
blinded holdout or evidence of an LLM skill benefit.

The bounded correction is accepted for local conformance. The full runtime
suite passed 369 tests with the previously observed Python 3.13 transport
finalizer warning retained. Ruff check/format and Pyright passed. The locked
runtime TUI suite passed 4,959 tests, with 13 skipped, 12 Forge deselected and
one warning. The full TUI suite using the candidate runtime source also passed
4,959 tests, with the same 13 skips, 12 deselections and one warning. Source
paths were captured to confirm that run imported the candidate checkout.
The required real-PTY Forge tier passed nine scripted-data cases;
three paid-provider cases were skipped and no tagged test sessions remained.

No external skill instructions or code were imported into the runtime. The
research lesson is to check actual execution and original requirements;
this deterministic mismatch is local evidence in its own right.

