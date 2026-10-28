# Client boundary and retained UI work — 2026-10-28

**There is one active Python kernel owner.** The TUI already delegates kernel/model imports to the installed `amplifier-runtime`. Its physical source copies are disabled by `__path__` compatibility shims. A byte comparison of 52 identical and 35 divergent kernel files is not evidence of two running kernels, and does not justify merging 87 files.

The [source comparison](client-source-comparison.json) retains all 87 matching kernel paths and nine named surfaces/adapters/experiments, with pinned revisions and exact existing paths. It distinguishes one active compatibility `__init__` from inert local bodies. The generator is read-only; the original source census remains a dated snapshot.

Runtime owns normalization, session creation, persistence, replay, approval enforcement, cancellation, leases and lifecycle. The TUI owns keyboard/rendering and its app-loop bridge. Rust TUI retains its alternative interaction/performance experiment. Studio retains rich inspection, attachments, terminal embedding and its tested reconnect/replay logic. OpenCode retains its host adapter. CLI integration is not assumed to share every runtime path already. VisualCodex and Workspaces retain their different process/chat/Markdown-worker experiments. The captured new-UI repository is a short instruction bundle, not an implemented renderer. None is a new coaching skill merely because it appears in this inventory.

## A contract generated from actual code

`python -m amplifier_runtime.client_contract` exports the data-channel schema directly from `kernel/jsonl.py` and the normalized event models. `contracts/runtime-data.v1.schema.json` is generated output, checked against those models. The small validator rejects incompatible schema versions, raw-hook substitutions and serve control tickets presented as data records. JSON arrays correctly rehydrate tuple fields such as approval options.

This schema covers session/data/error records. It does not claim to describe every serve control message. `kernel/serve.py` remains the owner of ticket-bearing `approval.required`, leases, control acknowledgements and history boundaries. A normalized approval event is presentation; it is not approval authority. Live transport sequence and durable replay cursor are different values. The authoritative reconnect cursor comes from `history.end.cursor`; consumers must not use an unrelated live sequence as a ledger offset.

## Functional proof and limits

- Runtime full offline suite after the new contract checks: **349 passed**. Ruff check/format and Pyright passed. A pre-existing Python 3.13 transport-finalizer warning was visible; it was not treated as a failure or hidden.
- TUI full default offline suite with its existing lock/pinned runtime: **4954 passed, 13 skipped, 12 Forge tests deselected**. This was the unchanged production client baseline, not a claim that every other UI passed.
- Five additional focused client-boundary tests passed: owner events rehydrate through the TUI's actual parser/adapter queue; stream/durable events and same-name tool IDs stay distinct; cancel state survives; invalid/stale approval choices are rejected by the real broker; replay reads the actual temporary session store without changing it and uses the durable cursor. A clean subprocess also loads and exercises the kernel/model imports after removing only their inert bodies in a disposable copy, against both the locked runtime and the candidate checkout.
- Required Forge demo/terminal tier: **9 passed, 3 paid-provider cases skipped** with the real lane explicitly off. The daemon was healthy; tagged test sessions were torn down. This is real PTY conformance with scripted demo data, not live-provider quality or outside use.

The TUI baseline currently pins runtime 0.1.10; the isolated canonical runtime source is the committed 0.1.11 default branch. The optional candidate-source subprocess checks this import/event boundary without silently changing the TUI's published dependency. It does not certify every unmerged branch or every downstream consumer. Other surfaces were source-reviewed, not all built or behaviorally compared in this work.

## Retirement remains a separate action

The later [replay-envelope check](evaluations/2026-10-28-replay-contract.md)
records a concrete omission in the new exported schema and its bounded
correction. The production replay producer remains unchanged; a generated
contract must describe the records that actually execute.

No original body, repository, branch, user session or data store was removed. The disposable no-body test improves readiness to remove inert package copies, but removal still needs package/file consumer inventory and supported-client acceptance. Keep compatibility import paths and the owner implementation. Preserve distinct UI experiments until their useful behavior is compared and harvested deliberately. No archive, visibility change, push or publication occurred here.

Foundation/Core and intentionally modular data/storage remain separate foundational owners. A required private/local foundation is an install/access boundary to document, not evidence that it is valueless or should be folded into a skill or UI.
