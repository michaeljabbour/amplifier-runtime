"""Navigation uses the real serving authorization and stays separate from replay."""

from pathlib import Path
from threading import get_ident
from typing import Any

import pytest

from amplifier_runtime.kernel import serve
from amplifier_runtime.kernel.session_authz import TokenPolicy, TokenStore
from tests.test_serve_control import _Connection, _ControlRuntime
from tests.test_history_navigation import ledger


@pytest.mark.asyncio
async def test_dispatch_is_correlated_off_loop_and_never_emits_replay(
    tmp_path: Path, monkeypatch: Any
) -> None:
    store, _ = ledger(tmp_path)
    runtime = _ControlRuntime(store, "session")
    original = serve._history_navigation_record
    worker_threads = []
    loop_thread = get_ident()

    def measured(runtime: Any, op: dict[str, Any]) -> dict[str, Any]:
        worker_threads.append(get_ident())
        return original(runtime, op)

    monkeypatch.setattr(serve, "_history_navigation_record", measured)
    connection = _Connection(runtime)
    try:
        connection.send(
            op="history.outline", request_id="outline-1", limit=2, session_id="foreign-session"
        )
        await connection.wait(lambda: connection.out.find("history.outline"))
        outline = connection.out.find("history.outline")
        assert outline is not None and outline["ok"]
        assert outline["request_id"] == "outline-1"
        assert outline["session_id"] == "session"
        connection.send(
            op="history.window",
            request_id="window-1",
            event_id="event-0",
            generation=outline["generation"],
            before=0,
            after=0,
        )
        await connection.wait(lambda: connection.out.find("history.window"))
        window = connection.out.find("history.window")
        assert window is not None and window["ok"]
        assert window["request_id"] == "window-1"
        assert len(window["records"]) == 1
        assert not connection.out.all("runtime.event")
        assert not connection.out.all("history.end")
        assert worker_threads and all(thread != loop_thread for thread in worker_threads)
        assert not runtime.submits
    finally:
        await connection.drop()


@pytest.mark.asyncio
async def test_read_permission_allows_navigation_but_missing_read_denies_before_indexing(
    tmp_path: Path,
) -> None:
    store, path = ledger(tmp_path)
    runtime = _ControlRuntime(store, "session")
    tokens = TokenStore(tmp_path / "auth.json")
    writer, _ = tokens.issue("writer", permissions=["write"])
    reader, _ = tokens.issue("reader", permissions=["read"])
    connection = _Connection(runtime, authorization_policy=TokenPolicy(tokens))
    try:
        connection.send(op="history.outline", request_id="denied", auth={"token": writer})
        await connection.wait(lambda: connection.out.conflicts())
        assert not connection.out.find("history.outline")
        assert not path.with_name("history-navigation.v1.sqlite3").exists()
        connection.send(op="history.outline", request_id="allowed", auth={"token": reader})
        await connection.wait(lambda: connection.out.find("history.outline"))
        response = connection.out.find("history.outline")
        assert response is not None and response["ok"]
        assert response["request_id"] == "allowed"
        assert not runtime.submits
    finally:
        await connection.drop()


def test_navigation_errors_are_explicit_and_capabilities_are_read_only(tmp_path: Path) -> None:
    store, path = ledger(tmp_path)
    runtime = _ControlRuntime(store, "session")
    invalid = serve._history_navigation_record(
        runtime, {"op": "history.outline", "request_id": "bad", "limit": 0}
    )
    assert invalid["code"] == "invalid_request" and invalid["request_id"] == "bad"
    expired = serve._history_navigation_record(
        runtime, {"op": "history.window", "event_id": "event-0", "generation": "old"}
    )
    assert expired["code"] == "cursor_expired"
    path.with_name("events.jsonl").write_text("{}\n")
    unavailable = serve._history_navigation_record(runtime, {"op": "history.outline"})
    assert unavailable["code"] == "navigation_unavailable"
    capabilities = serve._runtime_capabilities_record()
    assert capabilities["operations"]["history.outline"]["permission"] == "read"
    assert capabilities["operations"]["history.window"]["permission"] == "read"
    assert "history.navigation.native-conversation" in capabilities["features"]


@pytest.mark.asyncio
async def test_cold_scan_does_not_block_interrupt_or_admit_unbounded_readers(
    tmp_path: Path, monkeypatch: Any
) -> None:
    import asyncio
    from threading import Event

    store, _ = ledger(tmp_path)
    runtime = _ControlRuntime(store, "session")
    entered, release = Event(), Event()
    original = serve._history_navigation_record

    def blocked(runtime: Any, op: dict[str, Any]) -> dict[str, Any]:
        entered.set()
        assert release.wait(5), "Test did not release navigation worker"
        return original(runtime, op)

    monkeypatch.setattr(serve, "_history_navigation_record", blocked)
    connection = _Connection(runtime)
    try:
        connection.send(op="history.outline", request_id="cold")
        assert await asyncio.to_thread(entered.wait, 5)
        connection.send(op="history.window", request_id="second", event_id="event-0")
        connection.send(op="interrupt")
        await connection.wait(lambda: runtime.interrupts == 1)
        response = connection.out.find("history.window")
        assert response is not None and response["code"] == "navigation_busy"
        assert not connection.out.find("history.outline")
    finally:
        release.set()
        await connection.drop()
    response = connection.out.find("history.outline")
    assert response is not None and response["ok"]
