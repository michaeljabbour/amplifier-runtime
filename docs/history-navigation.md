# Native conversation navigation

`kernel.history_navigation.history_outline(store, session_id, cursor=None, limit=50)` returns `session_id`, `generation`, bounded `entries` (`event_id`, `kind`, `preview`), `next_cursor`, and `source`. `history_window(store, session_id, event_id=..., generation=None, before=2, after=2)` returns a bounded conversation window identified by the same durable event IDs. Call optional arguments by keyword. Outline pages allow 1–100 entries; windows allow 0–49 records on either side of the target.

These functions supply a conversation outline, not detailed tool history. Only root-session normalized `prompt_submit` and `prompt_complete` records contribute. Labels are scrubbed and limited to 240 characters. Window records contain only `event_id`, `session_id`, `kind`, `ts`, and the scrubbed `prompt` or `response`. System prompts, child instructions, provider payloads, and arbitrary event fields are omitted. Clients must label this narrower view and retain ordinary replay for full history.

The serving layer must authorize both operations as session READ operations and use the session's own store. Navigation cursors never modify reconnect cursors, advance live stream state, or count usage again. The `generation` identifies a source generation; appends preserve it, while detected replacement, truncation, or same-size changes rebuild it. An expired navigation cursor raises `NavigationCursorExpired`; refresh the outline instead of interpreting an old ordinal in new history.

The private `history-navigation.v1.sqlite3` file is a disposable offset/preview index. Queries serialize cache updates with a transient file lock, open a SQLite connection for the operation, and close it afterward. A corrupt cache header is recreated. A cold request streams the existing native ledger once; subsequent appends are indexed incrementally. Warm outline pages query bounded rows; warm windows seek directly to bounded records and verify their hashes. No full transcript is materialized. Source logs remain authoritative and unchanged. Each record is capped at 1 MiB for this navigation path; an oversized record reports unavailable navigation.

Native append-only `ui-events.jsonl` without legacy sources is supported. Legacy `events.jsonl`, transcript-only history, and any encountered rewind marker raise `NavigationUnavailable` and require ordinary replay. This deliberately avoids presenting superseded turns or inventing legacy cursor guarantees. Missing final newlines are retried on a later query; malformed complete JSON lines are skipped. Duplicate identical public records are deduplicated by durable ID; conflicting reuse of an ID fails closed. The index assumes the runtime's append-only ledger rule; an in-place rewrite combined with growth is outside that rule, and selected window hashes still detect changed indexed content.

`tests/test_history_navigation.py` exercises a 10,000-event fixture: cold indexing reads 10,000 lines; an early three-record warm window performs zero streaming index reads and returns less than 1 KiB, while a one-event append requires one new index read. Tests also cover bounded pages, privacy, stable IDs, expired generations, source truncation, partial tails, conflicting duplicates, corrupt cache recreation, and explicit rewind/legacy rejection. These are deterministic storage checks, not client memory or interactive latency measurements.

## Serving protocol

`history.outline` and `history.window` are READ operations advertised by `runtime.capabilities`, with feature `history.navigation.native-conversation`. They always query the connected runtime's session; a client-supplied session ID cannot redirect the query. Disk work runs through one tracked background task using `asyncio.to_thread`, leaving the input dispatch loop available for interrupts and other operations. A concurrent navigation request receives `navigation_busy` and may be retried after the current read completes. Authorization rejects requests before indexing when the principal lacks READ permission.

Send an optional unique `request_id` string of 1–128 characters to correlate concurrent client views. The response `type` matches the operation and echoes `request_id` and the authoritative `session_id`. Successful responses carry `ok: true` and the function result fields above. For example:

```json
{"op":"history.outline","request_id":"outline-1","limit":25}
{"op":"history.window","request_id":"window-1","event_id":"durable-event-id","generation":"outline-generation","before":2,"after":2}
```

Operation errors carry `ok: false`, `error`, and a `code`: `invalid_request`, `cursor_expired`, `navigation_unavailable`, `navigation_busy`, or `index_unavailable`. Refresh the outline on an expired cursor, offer ordinary replay for unsupported history, and allow retry for storage errors. Authorization failures retain the existing control-plane conflict response. Navigation returns neither `runtime.event` nor `history.end` records, so clients must render its records in a separate inspection view and leave replay suppression, costs, and reconnect state unchanged.

`tests/test_history_navigation_serve.py` drives the serving loop to verify off-loop dispatch, response correlation, connected-session scope, no replay emissions, READ-only access, and refusal before cache creation. It also checks explicit operation errors and capability discovery.
