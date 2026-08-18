"""An interrupt must stop one turn, not disable the session.

Regression cover for session ``eec9ae98``. ``interrupt()`` requests cancellation
on the coordinator's token -- kernel-owned state that outlives the turn it
stopped. Resetting the runtime's own ``_interrupt_requested`` flag does not
touch it, and nothing else did, so the next ``session.execute`` read a token
that was still cancelled and stopped within milliseconds of starting.

The observed damage: 9 prompts cancelled 21-35 ms after ``execution:start`` with
``turn_count: 0``, LLM-request gaps of 1,064 s and 56,395 s, and a user typing
into a session that could no longer answer. Only tearing the session down and
resuming recovered it.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from amplifier_core.cancellation import CancellationToken

from amplifier_runtime.kernel.runtime import TURN_ABORTED_MARKER, RealRuntime


class _Coordinator:
    def __init__(self, cancellation: Any) -> None:
        self.cancellation = cancellation


class _Initialized:
    """Duck-typed stand-in: the runtime only reaches for ``.coordinator``."""

    def __init__(self, coordinator: Any) -> None:
        self.coordinator = coordinator


def _runtime_with(cancellation: Any) -> RealRuntime:
    runtime = RealRuntime(bundle="offline", mode=lambda: "chat")
    runtime._initialized = _Initialized(_Coordinator(cancellation))  # type: ignore[assignment]
    return runtime


def test_a_cancelled_token_is_cleared_and_reported() -> None:
    """The defect itself: cancellation surviving into the next turn."""
    token = CancellationToken()
    token.request_graceful()
    assert token.is_cancelled, "fixture must start from a genuinely cancelled token"

    runtime = _runtime_with(token)
    was_stale = runtime._clear_stale_cancellation()

    assert was_stale is True, "a live cancellation must be reported, not cleared silently"
    assert not token.is_cancelled, (
        "the kernel token is still cancelled -- the next turn would self-cancel "
        "before reaching the model"
    )


def test_clearing_is_quiet_when_nothing_was_cancelled() -> None:
    """The normal path must not warn; a warning here would train people to ignore it."""
    runtime = _runtime_with(CancellationToken())
    assert runtime._clear_stale_cancellation() is False


def test_a_second_turn_starts_clean_after_an_interrupt() -> None:
    """End of the actual failure sequence: interrupt, then start another turn."""
    token = CancellationToken()
    runtime = _runtime_with(token)

    token.request_graceful()  # the user pressed Esc
    runtime._clear_stale_cancellation()  # next submit()
    assert not token.is_cancelled

    token.request_graceful()  # and again, later
    runtime._clear_stale_cancellation()
    assert not token.is_cancelled


@pytest.mark.parametrize(
    "initialized",
    [
        pytest.param(_Initialized(object()), id="coordinator-without-cancellation"),
        pytest.param(_Initialized(_Coordinator(None)), id="cancellation-is-none"),
        pytest.param(_Initialized(_Coordinator(object())), id="token-without-reset"),
        # The one that actually bit: `.coordinator` is a PROPERTY delegating to
        # `session.coordinator`, so a partial double raises rather than
        # returning None -- and this runs on the hot path of every turn, so the
        # AttributeError took down submit() itself. Nine tests in the TUI's
        # `test_kernel_turn_yield.py` use exactly this shape.
        pytest.param(SimpleNamespace(), id="initialized-without-coordinator"),
        pytest.param(SimpleNamespace(coordinator=None), id="coordinator-is-none"),
    ],
)
def test_clearing_never_raises_on_a_partial_session(initialized: Any) -> None:
    """Duck-typed like ``interrupt()`` -- test doubles must not break a turn."""
    runtime = RealRuntime(bundle="offline", mode=lambda: "chat")
    runtime._initialized = initialized  # type: ignore[assignment]
    assert runtime._clear_stale_cancellation() is False


def test_clearing_is_a_no_op_before_start() -> None:
    runtime = RealRuntime(bundle="offline", mode=lambda: "chat")
    assert runtime._clear_stale_cancellation() is False


def test_submit_clears_before_it_marks_the_turn_executing() -> None:
    """Ordering matters: the clear must happen before anything else in submit().

    Read the source rather than driving a turn -- a full turn needs the offline
    harness, and what is being pinned here is sequence, not behaviour: the
    clear has to precede ``_executing = True`` and the ``try:`` block, or a
    turn can be admitted on a cancelled token.
    """
    import inspect

    source = inspect.getsource(RealRuntime.submit)
    clear_at = source.index("_clear_stale_cancellation")
    executing_at = source.index("self._executing = True")
    assert clear_at < executing_at, (
        "submit() marks the turn executing before clearing a stale cancellation"
    )


class _RecordingContext:
    def __init__(self) -> None:
        self.added: list[dict[str, Any]] = []

    def add_message(self, message: dict[str, Any]) -> None:
        self.added.append(message)


class _ContextCoordinator:
    def __init__(self, context: Any) -> None:
        self._context = context

    def get(self, name: str) -> Any:
        return self._context if name == "context" else None


@pytest.mark.asyncio
async def test_turn_aborted_marker_is_not_persisted_as_assistant_speech() -> None:
    """An interrupt is a fact about the environment, not something the model said.

    Persisted as ``assistant``, the marker becomes the model's own last
    utterance -- a strong pattern to continue, so the next reply tends to parrot
    being interrupted, and each interrupt appends another.

    ``system`` is not the answer either: the Anthropic provider extracts
    system-role messages out of the conversation into the single top-level
    system block, so one of these would rewrite that block on every interrupt
    and bust its cache breakpoint. ``user`` keeps it in the conversation region,
    the same conclusion the compaction notice reached for the same reason.
    """
    context = _RecordingContext()
    runtime = RealRuntime(bundle="offline", mode=lambda: "chat")
    runtime._initialized = _Initialized(_ContextCoordinator(context))  # type: ignore[assignment]

    assert await runtime._append_turn_aborted_marker() is True

    assert len(context.added) == 1
    marker = context.added[0]
    assert marker["role"] == "user", (
        f"marker persisted as {marker['role']!r}; assistant makes the model parrot "
        "the interruption, system rewrites the cached system block"
    )
    assert marker["content"] == TURN_ABORTED_MARKER


@pytest.mark.asyncio
async def test_marker_failure_does_not_break_the_interrupt_path() -> None:
    """Interruption must still close cleanly when the context cannot persist."""
    runtime = RealRuntime(bundle="offline", mode=lambda: "chat")
    runtime._initialized = _Initialized(_ContextCoordinator(object()))  # type: ignore[assignment]

    assert await runtime._append_turn_aborted_marker() is False
