"""Files written by tools must be visible to the client.

``artifact:write`` is emitted by ``amplifier-module-tool-filesystem`` from both
``write`` and ``edit`` as ``{"path": ..., "bytes": ...}``. It was never listed
in ``CONSUMED_EVENTS``, so ``normalize()`` returned ``None`` and no UI event was
ever produced.

In session ``eec9ae98`` there were 15 ``artifact:write`` records in
``events.jsonl`` and no corresponding kind anywhere in ``ui-events.jsonl``. The
TUI's own canary caught it and logged ``unbridged event kind - artifact:write``
-- while the user was asking where the files had gone.
"""

from __future__ import annotations

from amplifier_runtime.kernel.events import ArtifactWrite, normalize
from amplifier_runtime.kernel.queue_bridge import CONSUMED_EVENTS

# Exactly what tool-filesystem emits (write.py / edit.py).
EMITTER_PAYLOAD = {"path": "/Users/someone/Desktop/ora/overseer/app.py", "bytes": 4096}


def test_artifact_write_is_consumed_at_all() -> None:
    """The defect itself: the event name was simply not in the bridge's set."""
    assert "artifact:write" in CONSUMED_EVENTS, (
        "artifact:write is unbridged, so every file a tool writes stays invisible to the client"
    )


def test_the_real_emitter_payload_normalizes() -> None:
    event = normalize("artifact:write", EMITTER_PAYLOAD)

    assert isinstance(event, ArtifactWrite)
    assert event.kind == "artifact_write"
    assert event.path == "/Users/someone/Desktop/ora/overseer/app.py"
    assert event.bytes_written == 4096


def test_the_bytes_field_is_accepted_under_either_name() -> None:
    """The emitter says ``bytes``; the model field is ``bytes_written``."""
    assert normalize("artifact:write", {"bytes": 12}).bytes_written == 12  # type: ignore[union-attr]
    assert normalize("artifact:write", {"bytes_written": 12}).bytes_written == 12  # type: ignore[union-attr]


def test_a_partial_payload_degrades_instead_of_raising() -> None:
    """A rendering pipeline must not crash on payload drift."""
    for payload in ({}, None, {"path": None}, {"bytes": "not a number"}):
        event = normalize("artifact:write", payload)  # type: ignore[arg-type]
        assert isinstance(event, ArtifactWrite)
        assert isinstance(event.path, str)
        assert isinstance(event.bytes_written, int)


def test_every_consumed_event_normalizes_to_something() -> None:
    """The invariant that would have caught this class of gap.

    A name in ``CONSUMED_EVENTS`` that ``normalize()`` does not handle produces
    a silent hole exactly like the one this file exists for.
    """
    unhandled = [name for name in CONSUMED_EVENTS if normalize(name, {}) is None]
    assert not unhandled, f"consumed but not normalized: {unhandled}"
