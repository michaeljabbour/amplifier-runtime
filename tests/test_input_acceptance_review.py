"""Control receipts must not claim an ignored busy submit was accepted."""

from pathlib import Path

import pytest

from amplifier_runtime.kernel.persistence import SessionStore
from tests.test_serve_control import _Connection, _ControlRuntime


@pytest.mark.asyncio
async def test_busy_submit_has_no_successful_idempotency_receipt(tmp_path: Path) -> None:
    runtime = _ControlRuntime(SessionStore(tmp_path / "sessions"), "session")
    runtime.block_submit = True
    connection = _Connection(runtime)
    try:
        connection.send(op="submit", text="first")
        await connection.wait(runtime.submit_started.is_set)
        connection.send(
            op="submit", text="second", idem="second-input", actor={"id": "human", "kind": "human"}
        )
        await connection.wait(
            lambda: any(record.get("idem") == "second-input" for record in connection.out.lines)
        )
        receipts = [
            record for record in connection.out.lines if record.get("idem") == "second-input"
        ]
        assert not any(record.get("ok") is True for record in receipts)
        assert runtime.submits == ["first"]
    finally:
        runtime.submit_release.set()
        await connection.drop()


@pytest.mark.asyncio
async def test_rejected_input_id_can_retry_and_status_never_resubmits(tmp_path: Path) -> None:
    runtime = _ControlRuntime(SessionStore(tmp_path / "sessions"), "session")
    connection = _Connection(runtime)
    try:
        connection.send(op="submit", text="", idem="input", request_id="bad")
        await connection.wait(lambda: connection.out.find("input.result") is not None)
        assert connection.out.all("input.result")[-1]["stage"] == "rejected"
        assert not connection.out.all("control.ack")
        connection.send(op="submit", text="valid", idem="input", request_id="good")
        await connection.wait(lambda: connection.out.find("turn.completed") is not None)
        receipt = connection.out.all("input.result")[-1]
        assert receipt["stage"] == "dispatched"
        assert receipt["request_id"] == "good"
        connection.send(op="input.status", input_id="input", request_id="status")
        await connection.wait(lambda: connection.out.find("input.status") is not None)
        status = connection.out.find("input.status")
        assert status is not None and status["ok"] and status["stage"] == "dispatched"
        assert status["request_id"] == "status"
        assert runtime.submits == ["valid"]
    finally:
        await connection.drop()


@pytest.mark.asyncio
async def test_previous_serve_receipt_does_not_claim_current_admission(tmp_path: Path) -> None:
    store = SessionStore(tmp_path / "sessions")
    first = _ControlRuntime(store, "session")
    connection = _Connection(first)
    connection.send(op="submit", text="valid", idem="input")
    await connection.wait(lambda: connection.out.find("turn.completed") is not None)
    old = connection.out.find("input.result")
    await connection.drop()
    runtime = _ControlRuntime(store, "session")
    reconnect = _Connection(runtime)
    try:
        reconnect.send(op="input.status", input_id="input", request_id="lookup")
        await reconnect.wait(lambda: reconnect.out.find("input.status") is not None)
        status = reconnect.out.find("input.status")
        assert status is not None and old is not None
        assert status["stage"] == "unknown_previous_instance"
        assert status["ok"] is False
        assert status["original_serve_instance"] == old["serve_instance"]
        assert status["serve_instance"] != old["serve_instance"]
        assert runtime.submits == []
        assert not reconnect.out.all("control.ack")
    finally:
        await reconnect.drop()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "op,code",
    [
        ({"op": "submit", "text": []}, "invalid_text"),
        ({"op": "submit", "text": "", "attachments": [{}]}, "invalid_attachments"),
        ({"op": "steer", "text": "  "}, "empty_or_invalid_text"),
        ({"op": "steer", "text": "x" * 32769}, "text_too_long"),
        ({"op": "submit", "text": "valid", "request_id": "x" * 129}, "invalid_id"),
    ],
)
async def test_invalid_input_is_rejected_without_success_receipt(
    tmp_path: Path, op: dict, code: str
) -> None:
    runtime = _ControlRuntime(SessionStore(tmp_path / "sessions"), "session")
    connection = _Connection(runtime)
    try:
        connection.send(**{**op, "idem": "input"})
        await connection.wait(lambda: connection.out.find("input.result") is not None)
        result = connection.out.find("input.result")
        assert result is not None and result["code"] == code and not result["ok"]
        assert not connection.out.all("control.ack")
        assert runtime.submits == []
    finally:
        await connection.drop()


@pytest.mark.asyncio
async def test_steer_queue_overflow_rejected_and_same_id_can_retry(tmp_path: Path) -> None:
    from amplifier_runtime.model.queues import MAX_QUEUE_ITEMS

    runtime = _ControlRuntime(SessionStore(tmp_path / "sessions"), "session")
    runtime.block_submit = True
    connection = _Connection(runtime)
    try:
        connection.send(op="submit", text="first")
        await connection.wait(runtime.submit_started.is_set)
        for _ in range(MAX_QUEUE_ITEMS):
            runtime.steering.enqueue("pending")
        connection.send(op="steer", text="new", idem="steer")
        await connection.wait(lambda: connection.out.find("input.result") is not None)
        assert connection.out.all("input.result")[-1]["code"] == "queue_rejected"
        assert not connection.out.all("control.ack")
        runtime.steering.drain_steers()
        connection.send(op="steer", text="new", idem="steer")
        await connection.wait(lambda: len(connection.out.all("input.result")) == 2)
        assert connection.out.all("input.result")[-1]["stage"] == "queued"
        assert len(runtime.steering.pending_steers) == 1
        connection.send(op="input.status", input_id="steer")
        await connection.wait(lambda: connection.out.find("input.status") is not None)
        assert len(runtime.steering.pending_steers) == 1
    finally:
        runtime.steering.drain_steers()
        runtime.submit_release.set()
        await connection.drop()


@pytest.mark.asyncio
async def test_busy_rejection_is_retriable_after_turn_finishes(tmp_path: Path) -> None:
    runtime = _ControlRuntime(SessionStore(tmp_path / "sessions"), "session")
    runtime.block_submit = True
    connection = _Connection(runtime)
    try:
        connection.send(op="submit", text="first")
        await connection.wait(runtime.submit_started.is_set)
        connection.send(op="submit", text="second", idem="second")
        await connection.wait(lambda: connection.out.find("input.result") is not None)
        assert connection.out.all("input.result")[-1]["code"] == "turn_busy"
        runtime.submit_release.set()
        await connection.wait(lambda: connection.out.find("turn.completed") is not None)
        connection.send(op="submit", text="second", idem="second")
        await connection.wait(lambda: len(runtime.submits) == 2)
        assert connection.out.all("input.result")[-1]["stage"] == "dispatched"
        assert runtime.submits == ["first", "second"]
    finally:
        runtime.submit_release.set()
        await connection.drop()


@pytest.mark.asyncio
async def test_request_only_receipt_marks_old_instance_unknown(tmp_path: Path) -> None:
    store = SessionStore(tmp_path / "sessions")
    connection = _Connection(_ControlRuntime(store, "session"))
    connection.send(op="submit", text="valid", request_id="correlation")
    await connection.wait(lambda: connection.out.find("turn.completed") is not None)
    result = connection.out.find("input.result")
    assert result is not None
    await connection.drop()
    reconnect = _Connection(_ControlRuntime(store, "session"))
    try:
        reconnect.send(op="input.status", input_id=result["input_id"])
        await reconnect.wait(lambda: reconnect.out.find("input.status") is not None)
        assert reconnect.out.all("input.status")[-1]["stage"] == "unknown_previous_instance"
    finally:
        await reconnect.drop()
