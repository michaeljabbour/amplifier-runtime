# Question and approval recovery

Questions and approvals use separate response operations. A question answer names its `decision_id`; an approval names its `ticket_id` and choice. Accepted question answers produce `decision_answered` events. Deferred approval answers become model instructions for a later step; they do not grant the original tool call permission. That call has already been denied.

Question IDs and approval ticket IDs contain a random namespace belonging to their queue or broker instance. Clients must treat the complete ID as opaque. Recreating a runtime does not reuse the old IDs, so a stale reply cannot answer an unrelated new question or approve a new tool call. An answered decision rejects another answer. An approval rejects another answer even before its completed request has finished removing the ticket.

An ordinary user prompt may acknowledge an inferred attention notification only when no structured question or live approval remains pending. Acknowledging `attention.json` is notification bookkeeping; it does not answer a question or grant permission.

## Current recovery limits

Socket reattachment to a surviving runtime retains its question queue and approval futures. `session.status` exposes the authoritative pending questions and current approval. Stored-session restart does not restore pending question rows or approval futures. Historical events alone must not be presented as proof that those requests remain actionable. Randomized IDs prevent old replies from targeting new requests; they do not recover the old requests.

A blocking question returns its answers directly in the tool result. An Auto question returns immediately and delivers later answers through the provider-request bridge. `NeedsYouItem` currently records neither that delivery mode nor its originating turn/tool call. Persisting its current fields alone would make it impossible to distinguish a recoverable Auto question from a question whose blocking tool execution has disappeared.

## Required durable recovery work

Before enabling request restoration, persist each request's session, turn, tool-call identity, delivery mode, lifecycle, and accepted answer. Define cancellation and process-death handling for each delivery mode. Blocking approval futures must expire with their execution; restoring a notification cannot recreate permission for a different tool attempt. Record expiry or cancellation so replay and live clients remove the same request.

Accepted answers also need a durable delivery transition coordinated with model-context persistence. Marking an answer consumed before context persistence can lose it after a crash; replaying it without checking recorded delivery can apply it twice. Recover only requests whose originating execution and delivery state can be established, and return an explicit stale or expired response for the rest.
