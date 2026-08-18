"""Resume-boundary transcript repair across all three failure modes.

The repair path is a deliberate merge of two mechanisms, and these tests pin
the seam between them:

- Orphaned tool calls are found by this repo's own extractor, which reads BOTH
  persisted call shapes.  Foundation's index reads only the top-level
  ``tool_calls`` key, and real transcripts in this project carry a majority of
  calls as ``content`` blocks -- so the block-shape test below is a regression
  guard against ever delegating orphan detection upstream wholesale.
- Ordering violations and incomplete assistant turns are found by foundation's
  shared diagnosis, fed an index-aligned shadow copy.

A resume must never fail because diagnosis failed, so degradation is pinned too.
"""

from __future__ import annotations

from typing import Any

import pytest

from amplifier_runtime.kernel.session_integrity import (
    TranscriptRepair,
    repair_resumed_transcript,
)


def _block_call(call_id: str, name: str = "delegate") -> dict[str, Any]:
    """An assistant message carrying its tool call as a content block."""
    return {
        "role": "assistant",
        "content": [{"type": "tool_call", "id": call_id, "name": name, "input": {}}],
    }


def _top_level_call(call_id: str, name: str = "delegate") -> dict[str, Any]:
    """An assistant message carrying its tool call in the top-level shape."""
    return {
        "role": "assistant",
        "content": "",
        "tool_calls": [{"id": call_id, "function": {"name": name}}],
    }


def _result(call_id: str, text: str = "ok") -> dict[str, Any]:
    return {"role": "tool", "tool_call_id": call_id, "content": text}


def _is_synthetic_result(message: dict[str, Any]) -> bool:
    content = message.get("content")
    return (
        message.get("role") == "tool" and isinstance(content, str) and "SYSTEM RECOVERY" in content
    )


def _is_synthetic_turn(message: dict[str, Any]) -> bool:
    content = message.get("content")
    return (
        message.get("role") == "assistant"
        and isinstance(content, str)
        and "SYSTEM RECOVERY" in content
    )


def test_empty_transcript_is_returned_unchanged() -> None:
    messages: list[dict[str, Any]] = []
    repaired, repair = repair_resumed_transcript(messages)
    assert repaired is messages
    assert repair is None


def test_healthy_transcript_is_not_copied() -> None:
    """A clean resume must cost nothing -- same object, no repair record."""
    messages = [
        {"role": "user", "content": "hi"},
        _block_call("c1"),
        _result("c1"),
        {"role": "assistant", "content": "done"},
    ]
    repaired, repair = repair_resumed_transcript(messages)
    assert repaired is messages
    assert repair is None


def test_orphan_in_block_shape_is_repaired() -> None:
    """The shape foundation's index cannot see is still repaired here.

    This is the regression guard: measured against real stored transcripts,
    block-shaped calls outnumber top-level ones, so losing this path would
    make resume repair blind to the majority of interrupted calls.
    """
    messages = [{"role": "user", "content": "delegate this"}, _block_call("toolu_missing")]

    repaired, repair = repair_resumed_transcript(messages)

    assert repair is not None
    assert [r.tool_call_id for r in repair.tool_results] == ["toolu_missing"]
    assert repair.failure_modes == ("missing_tool_results",)
    assert _is_synthetic_result(repaired[2])
    assert repaired[2]["tool_call_id"] == "toolu_missing"
    assert repaired[2]["name"] == "delegate"


def test_orphan_in_top_level_shape_is_repaired() -> None:
    messages = [{"role": "user", "content": "go"}, _top_level_call("call_1", name="bash")]

    repaired, repair = repair_resumed_transcript(messages)

    assert repair is not None
    assert [r.tool_call_id for r in repair.tool_results] == ["call_1"]
    assert _is_synthetic_result(repaired[2])
    assert repaired[2]["name"] == "bash"


def test_real_results_are_never_duplicated_or_replaced() -> None:
    """Only genuinely unmatched calls get a placeholder.

    Placement matters as much as presence: a placeholder goes immediately
    after the message that made the call, which is the ordering providers
    require when several calls are issued in parallel.  The real result keeps
    its own position after it.
    """
    messages = [
        {"role": "user", "content": "two tools"},
        {
            "role": "assistant",
            "content": [
                {"type": "tool_call", "id": "c1", "name": "read_file", "input": {}},
                {"type": "tool_call", "id": "c2", "name": "bash", "input": {}},
            ],
        },
        _result("c1", "real output"),
        {"role": "assistant", "content": "done"},
    ]

    repaired, repair = repair_resumed_transcript(messages)

    assert repair is not None
    assert repair.failure_modes == ("missing_tool_results",)
    assert [r.tool_call_id for r in repair.tool_results] == ["c2"]

    # Placeholder for the unmatched call sits directly after the assistant.
    assert _is_synthetic_result(repaired[2])
    assert repaired[2]["tool_call_id"] == "c2"
    # The real result survives untouched, in its original relative order.
    assert repaired[3] == {"role": "tool", "tool_call_id": "c1", "content": "real output"}

    synthetic = [m for m in repaired if _is_synthetic_result(m)]
    assert len(synthetic) == 1


def test_ordering_violation_result_is_reinserted_in_a_valid_position() -> None:
    """A result separated from its call by a real user turn is repositioned.

    The misplaced record is dropped, which leaves its call unmatched, and the
    orphan pass then re-inserts a placeholder immediately after the call --
    which is the position providers require.
    """
    messages = [
        {"role": "user", "content": "hi"},
        _top_level_call("c1"),
        {"role": "user", "content": "interrupting"},
        _result("c1", "late output"),
        {"role": "assistant", "content": "done"},
    ]

    repaired, repair = repair_resumed_transcript(messages)

    assert repair is not None
    assert "ordering_violation" in repair.failure_modes
    assert repair.misplaced_tool_ids == ("c1",)

    roles = [m["role"] for m in repaired]
    assert roles == ["user", "assistant", "tool", "user", "assistant"]
    assert _is_synthetic_result(repaired[2])
    # The late record itself is gone, not merely reordered.
    assert not any(m.get("content") == "late output" for m in repaired)


def test_incomplete_assistant_turn_is_reported_but_never_fabricated() -> None:
    """An unclosed turn is a diagnosis, not a rewrite.

    Writing a closing assistant message means putting words in the model's
    mouth, which the next request reads back as its own last utterance.  The
    real fix is the response resume is about to request.  Detection alone must
    not mark the transcript dirty either -- persisting here would rewrite a
    stored conversation that did not change.
    """
    messages = [
        {"role": "user", "content": "hi"},
        _top_level_call("c1"),
        _result("c1"),
    ]

    repaired, repair = repair_resumed_transcript(messages)

    assert repaired is messages
    assert repair is None
    assert not any(_is_synthetic_turn(m) for m in repaired)


def test_result_blocks_on_a_user_message_count_as_results() -> None:
    """Anthropic returns ``tool_result`` blocks on a *user* message.

    If diagnosis cannot see those, it reads the message as a user interruption
    and reports a turn that is not actually incomplete.
    """
    messages = [
        {"role": "user", "content": "go"},
        _top_level_call("c1"),
        {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": "c1", "content": "ok"}],
        },
        {"role": "assistant", "content": "done"},
    ]

    repaired, repair = repair_resumed_transcript(messages)

    assert repaired is messages
    assert repair is None


@pytest.mark.parametrize(
    "messages",
    [
        pytest.param(
            [{"role": "user", "content": "go"}, _block_call("c1")],
            id="orphaned-block-call",
        ),
        pytest.param(
            [{"role": "user", "content": "go"}, _top_level_call("c1"), _result("c1")],
            id="incomplete-turn",
        ),
        pytest.param(
            [
                _block_call("c1"),
                {
                    "role": "user",
                    "content": [{"type": "tool_result", "tool_use_id": "c1", "content": "ok"}],
                },
            ],
            id="result-block-on-user-message",
        ),
        pytest.param(
            [
                {"role": "user", "content": "hi"},
                _top_level_call("c1"),
                {"role": "user", "content": "interrupting"},
                _result("c1", "late output"),
                {"role": "assistant", "content": "done"},
            ],
            id="ordering-violation",
        ),
    ],
)
def test_repair_is_idempotent(messages: list[dict[str, Any]]) -> None:
    """A repaired transcript must be stable under repair.

    Resume runs this on every restore and persists the result.  A repair that
    finds new work on its own output would grow the stored transcript once per
    resume -- unbounded, and invisible until a session had been resumed enough
    times to notice.
    """
    once, _first = repair_resumed_transcript(messages)
    twice, second = repair_resumed_transcript(once)

    assert second is None
    assert twice is once


def test_diagnosis_failure_degrades_to_orphan_only(monkeypatch: pytest.MonkeyPatch) -> None:
    """A resume must survive a broken diagnosis, repairing what it still can."""
    import amplifier_foundation.session.diagnosis as diagnosis_module

    def _boom(_entries: list[dict[str, Any]]) -> dict[str, Any]:
        raise RuntimeError("diagnosis exploded")

    monkeypatch.setattr(diagnosis_module, "diagnose_transcript", _boom)

    messages = [{"role": "user", "content": "go"}, _block_call("c1")]
    repaired, repair = repair_resumed_transcript(messages)

    assert repair is not None
    assert repair.failure_modes == ("missing_tool_results",)
    assert _is_synthetic_result(repaired[2])


def test_repair_record_describes_every_mode() -> None:
    repair = TranscriptRepair(
        tool_results=(),
        misplaced_tool_ids=("a", "b"),
        incomplete_turns=1,
        entries_before=5,
        entries_after=6,
    )
    assert bool(repair) is True
    assert repair.failure_modes == ("ordering_violation", "incomplete_assistant_turn")
    assert "2 out-of-order tool result(s)" in repair.describe()
    assert "1 unclosed turn(s) (reported, not rewritten)" in repair.describe()


def test_unclosed_turn_alone_does_not_make_a_transcript_dirty() -> None:
    """Detection without rewriting must not trigger a persist."""
    repair = TranscriptRepair(incomplete_turns=3, entries_before=4, entries_after=4)
    assert not repair
    assert repair.failure_modes == ("incomplete_assistant_turn",)


def test_empty_repair_record_is_falsy() -> None:
    assert not TranscriptRepair()
    assert TranscriptRepair().describe() == "nothing"
