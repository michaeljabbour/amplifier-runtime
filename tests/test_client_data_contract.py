"""Source-owned data contracts preserve stream/durable/approval distinctions."""

from __future__ import annotations

from pathlib import Path

import pytest

from amplifier_runtime.client_contract import data_schema, validate_data_record
from amplifier_runtime.kernel.events import (
    ApprovalRequired,
    ContentBlockEnd,
    OrchestratorComplete,
    StreamBlockDelta,
    ToolPre,
)
from amplifier_runtime.kernel.jsonl import JsonlRecords


def test_generated_schema_is_current() -> None:
    import json

    source = Path(__file__).resolve().parents[1] / "contracts/runtime-data.v1.schema.json"
    assert json.loads(source.read_text()) == data_schema()


def test_real_event_models_round_trip_without_merging_channels_or_tools() -> None:
    factory = JsonlRecords()
    events = [
        StreamBlockDelta(event_id="stream-1", session_id="fictional", text="draft"),
        ContentBlockEnd(event_id="durable-1", session_id="fictional", block={"text": "final"}),
        ToolPre(event_id="tool-1", tool_name="same-tool", tool_call_id="call-A"),
        ToolPre(event_id="tool-2", tool_name="same-tool", tool_call_id="call-B"),
        ApprovalRequired(
            event_id="ask-1", prompt="Fictional operation", options=("Allow once", "Deny")
        ),
        OrchestratorComplete(event_id="stop-1", status="cancelled"),
    ]
    decoded = [
        validate_data_record(factory.runtime_event(e).model_dump(mode="json")) for e in events
    ]
    assert decoded[0].event.kind == "stream_block_delta"
    assert decoded[1].event.kind == "content_block_end"
    assert decoded[2].event.tool_call_id != decoded[3].event.tool_call_id
    assert decoded[4].event.kind == "approval_required"
    assert decoded[5].event.status == "cancelled"


def test_control_ticket_and_raw_hook_are_not_fabricated_as_data_events() -> None:
    with pytest.raises(ValueError):
        validate_data_record(
            {"schema_version": 1, "type": "approval.required", "ticket_id": "ask-1"}
        )
    with pytest.raises(ValueError):
        validate_data_record({"schema_version": True, "type": "runtime.event"})
    with pytest.raises(ValueError):
        validate_data_record(
            {
                "schema_version": 1,
                "type": "runtime.event",
                "sequence": 1,
                "timestamp": "fictional",
                "event": {"event": "llm:stream_block_delta", "delta": "raw"},
            }
        )
