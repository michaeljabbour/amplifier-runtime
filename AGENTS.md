# Agent guide - amplifier-runtime

This repository owns Amplifier's UI-neutral session runtime. It is the single
implementation behind TUI, CLI, Studio, automation, and future remote clients.

## Boundaries

- Runtime owns Foundation/Core session creation, event normalization,
  persistence, replay, approvals, interruption, spawning, leases, attach,
  handoff, and lifecycle.
- Clients own rendering, native input, process presentation, and product update
  policy.
- Runtime is a Python host over Amplifier's hybrid Python/Rust Core. Do not add
  Textual, prompt-toolkit, Rich rendering, Tauri, or SolidJS dependencies.
- Bundles are declarative composition. Never hide session-host behavior in a
  bundle or silently hardcode a client-specific bundle.
- Raw hook payloads normalize once in `kernel/events.py`. Live and durable
  channels remain independent and correlate tools by `tool_call_id`.
- A session has one runtime writer. Preserve attach/lease/idempotency semantics;
  never create a second process for a live session.

## Compatibility

- The donor is `~/dev/amplifier-app-tui`. Extraction moves behavior here and
  leaves compatibility imports there; do not maintain two implementations.
- Keep the JSONL protocol schema-versioned. Existing records remain compatible
  unless a deliberate version transition and client migration are included.
- Store transcripts and metadata atomically with recovery. Keep the normalized
  event ledger append-only.
- Secrets are redacted, settings/key precedence is preserved, and writes are
  atomic.

## Verification

```sh
uv sync
uv run ruff check .
uv run ruff format --check .
uv run pyright src tests
uv run pytest -q
```

After any client-facing runtime change, also run the TUI's full offline suite
and the forge real-PTY capability flow. Use focused tests while iterating, but
do not claim compatibility from focused tests alone.
