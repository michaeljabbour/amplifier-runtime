"""Answering inline must clear the "needs you" record, same as replying out of band.

Every out-of-band answer path clears attention: ``ambient/reply.py`` writes
through :class:`AttentionStore` so an ntfy reply resolves the state
cross-process. Answering *inline*, in the TUI, cleared nothing -- there was no
caller on the submit path at all, so the durable record stayed
``acknowledged: false`` indefinitely.

Observed in session ``eec9ae98``: four decisions raised at 17:11:48, answered
inline at 20:52:05, written up by the agent to a decisions file by 21:06 -- and
all four still sitting in ``attention.json`` as unacknowledged under
``reason: "awaiting_clarification"`` when the session ended hours later.

Submitting a prompt is the strongest available evidence that the wait is over.
"""

from __future__ import annotations

import inspect
from pathlib import Path
from typing import Any

import pytest

from amplifier_runtime.kernel.attention_store import AttentionRow, AttentionStore
from amplifier_runtime.kernel.runtime import RealRuntime

SESSION_ID = "session-under-test"


class _Hooks:
    def __init__(self) -> None:
        self.emitted: list[tuple[str, dict[str, Any]]] = []

    async def emit(self, event: str, data: dict[str, Any]) -> None:
        self.emitted.append((event, data))


class _Coordinator:
    def __init__(self, hooks: _Hooks) -> None:
        self.hooks = hooks


class _Initialized:
    def __init__(self, hooks: _Hooks) -> None:
        self.session_id = SESSION_ID
        self.coordinator = _Coordinator(hooks)


def _runtime(session_dir: Path | None) -> tuple[RealRuntime, _Hooks]:
    hooks = _Hooks()
    runtime = RealRuntime(bundle="offline", mode=lambda: "chat")
    runtime._initialized = _Initialized(hooks)  # type: ignore[assignment]
    runtime.session_dir = lambda: session_dir  # type: ignore[method-assign]
    return runtime, hooks


def _pending(session_dir: Path, *, acknowledged: bool = False) -> AttentionRow:
    row = AttentionRow(
        session_id=SESSION_ID,
        reason="awaiting_clarification",
        event_id="decision-1",
        detail="Custom build or off the shelf? - Auto continues while this waits",
        created_at=1.0,
        acknowledged=acknowledged,
    )
    store = AttentionStore(session_dir)
    store.save({row.event_id: row}, {SESSION_ID: row.event_id})
    return row


@pytest.mark.asyncio
async def test_a_pending_decision_is_resolved_when_the_user_speaks(tmp_path: Path) -> None:
    """The defect: four decisions answered inline stayed pending for hours."""
    _pending(tmp_path)
    runtime, hooks = _runtime(tmp_path)

    resolved = await runtime._resolve_pending_attention()

    assert resolved == "decision-1"
    by_id, _current = AttentionStore(tmp_path).load()
    assert by_id["decision-1"].acknowledged is True, (
        "the durable record is still unacknowledged after the user answered"
    )
    assert [event for event, _ in hooks.emitted] == ["attention:acknowledged"]
    _event, payload = hooks.emitted[0]
    assert payload["event_id"] == "decision-1"
    assert payload["acknowledged"] is True
    assert payload["reason"] == "awaiting_clarification", (
        "out-of-band destinations correlate on the record's own fields"
    )


@pytest.mark.asyncio
async def test_nothing_pending_is_a_silent_no_op(tmp_path: Path) -> None:
    """Every turn calls this; a session with no notification must pay nothing."""
    runtime, hooks = _runtime(tmp_path)

    assert await runtime._resolve_pending_attention() is None
    assert hooks.emitted == []


@pytest.mark.asyncio
async def test_an_already_acknowledged_record_is_not_re_announced(tmp_path: Path) -> None:
    """Re-emitting would make destinations clear a notification twice."""
    _pending(tmp_path, acknowledged=True)
    runtime, hooks = _runtime(tmp_path)

    assert await runtime._resolve_pending_attention() is None
    assert hooks.emitted == []


@pytest.mark.asyncio
async def test_it_is_a_no_op_before_the_session_has_started() -> None:
    runtime, hooks = _runtime(None)

    assert await runtime._resolve_pending_attention() is None
    assert hooks.emitted == []


@pytest.mark.asyncio
async def test_a_store_failure_never_blocks_the_turn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Best-effort throughout: a notification must not be able to stop work."""
    _pending(tmp_path)
    runtime, hooks = _runtime(tmp_path)

    def _boom(self: AttentionStore, session_id: str) -> None:
        raise OSError("attention.json is unreadable")

    monkeypatch.setattr(AttentionStore, "acknowledge", _boom)

    assert await runtime._resolve_pending_attention() is None
    assert hooks.emitted == []


def test_submit_resolves_attention_before_it_marks_the_turn_executing() -> None:
    """Ordering, read from the source rather than by driving a whole turn.

    The clear has to happen on the way in. Deferring it to the end of the turn
    would leave the record pending for the entire duration of the work the user
    just asked for.
    """
    source = inspect.getsource(RealRuntime.submit)
    resolve_at = source.index("_resolve_pending_attention")
    executing_at = source.index("self._executing = True")
    assert resolve_at < executing_at
