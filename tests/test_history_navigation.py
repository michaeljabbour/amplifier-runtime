"""Bounded native conversation navigation, with explicit legacy and rewind limits."""

import json
from pathlib import Path
from typing import Any

import pytest

from amplifier_runtime.kernel import history_navigation as navigation
from amplifier_runtime.kernel.persistence import SessionStore


def ledger(tmp_path: Path, count: int = 8) -> tuple[SessionStore, Path]:
    store = SessionStore(tmp_path / "sessions")
    directory = store.session_dir("session")
    directory.mkdir()
    path = directory / "ui-events.jsonl"
    with path.open("w") as handle:
        for number in range(count):
            handle.write(
                json.dumps(
                    {
                        "event_id": f"event-{number}",
                        "session_id": "session",
                        "kind": "prompt_submit",
                        "prompt": f"User turn {number}",
                    }
                )
                + "\n"
            )
    return store, path


def append(path: Path, **record: Any) -> None:
    with path.open("a") as handle:
        handle.write(json.dumps({"session_id": "session", **record}) + "\n")


def test_warm_early_window_seeks_without_rescanning_large_ledger(
    tmp_path: Path, monkeypatch: Any
) -> None:
    store, path = ledger(tmp_path, 10_000)
    reads = 0
    original = navigation._read_record

    def measured(handle: Any) -> tuple[int, bytes]:
        nonlocal reads
        reads += 1
        return original(handle)

    monkeypatch.setattr(navigation, "_read_record", measured)
    outline = navigation.history_outline(store, "session", limit=3)
    assert reads == 10_000
    assert len(outline["entries"]) == 3
    assert path.stat().st_size > 1_000_000
    reads = 0
    result = navigation.history_window(
        store, "session", event_id="event-1", generation=outline["generation"], before=1, after=1
    )
    assert reads == 0
    assert [record["event_id"] for record in result["records"]] == ["event-0", "event-1", "event-2"]
    assert sum(len(json.dumps(record)) for record in result["records"]) < 1000
    assert result["session_id"] == "session"


def test_append_indexes_only_new_records_and_keeps_navigation_generation(
    tmp_path: Path, monkeypatch: Any
) -> None:
    store, path = ledger(tmp_path)
    first = navigation.history_outline(store, "session", limit=3)
    calls = 0
    original = navigation._read_record

    def measured(handle: Any) -> tuple[int, bytes]:
        nonlocal calls
        calls += 1
        return original(handle)

    monkeypatch.setattr(navigation, "_read_record", measured)
    append(path, event_id="new", kind="prompt_complete", response="Finished")
    next_page = navigation.history_outline(store, "session", cursor=first["next_cursor"])
    assert calls == 1
    assert next_page["generation"] == first["generation"]
    assert next_page["entries"][-1]["event_id"] == "new"


def test_projection_omits_private_fields_child_prompts_and_nonconversation_events(
    tmp_path: Path,
) -> None:
    store, path = ledger(tmp_path, 0)
    append(
        path,
        event_id="public",
        kind="prompt_submit",
        prompt="Visible user intent",
        system_prompt="PRIVATE SYSTEM",
        config={"api_key": "PRIVATE KEY"},
    )
    append(path, event_id="child", kind="prompt_submit", prompt="PRIVATE CHILD", session_id="child")
    append(path, event_id="request", kind="provider_request", system_prompt="PRIVATE REQUEST")
    outline = navigation.history_outline(store, "session")
    window = navigation.history_window(store, "session", event_id="public")
    assert len(outline["entries"]) == 1
    assert "PRIVATE" not in json.dumps([outline, window])
    assert window["records"][0]["prompt"] == "Visible user intent"


@pytest.mark.parametrize(
    "arguments", [{"limit": 0}, {"limit": 101}, {"limit": True}, {"cursor": "nonsense"}]
)
def test_invalid_outline_bounds_are_rejected(tmp_path: Path, arguments: dict[str, Any]) -> None:
    store, _ = ledger(tmp_path)
    with pytest.raises(ValueError):
        navigation.history_outline(store, "session", **arguments)


def test_window_bounds_unknown_ids_and_expired_generations_are_rejected(tmp_path: Path) -> None:
    store, _ = ledger(tmp_path)
    with pytest.raises(ValueError):
        navigation.history_window(store, "session", event_id="event-0", before=-1)
    with pytest.raises(ValueError):
        navigation.history_window(store, "session", event_id="absent")
    with pytest.raises(navigation.NavigationCursorExpired):
        navigation.history_window(store, "session", event_id="event-0", generation="stale")


def test_truncation_rebuilds_and_invalidates_cursor(tmp_path: Path) -> None:
    store, path = ledger(tmp_path)
    first = navigation.history_outline(store, "session", limit=1)
    path.write_text("")
    append(path, event_id="replacement", kind="prompt_submit", prompt="Replacement")
    with pytest.raises(navigation.NavigationCursorExpired):
        navigation.history_outline(store, "session", cursor=first["next_cursor"])
    fresh = navigation.history_outline(store, "session")
    assert fresh["generation"] != first["generation"]
    assert fresh["entries"][0]["event_id"] == "replacement"


def test_rewind_and_legacy_sources_fail_closed(tmp_path: Path) -> None:
    store, path = ledger(tmp_path)
    navigation.history_outline(store, "session")
    append(path, event_id="rewind", kind="rewind_marker", kept_turns=1)
    with pytest.raises(navigation.NavigationUnavailable, match="Rewound"):
        navigation.history_window(store, "session", event_id="event-0")
    path.with_name("events.jsonl").write_text("{}\n")
    with pytest.raises(navigation.NavigationUnavailable, match="legacy"):
        navigation.history_outline(store, "session")


def test_partial_tail_is_retried_and_conflicting_duplicate_identity_rejected(
    tmp_path: Path,
) -> None:
    store, path = ledger(tmp_path, 1)
    with path.open("a") as handle:
        handle.write('{"session_id":"session","event_id":"later",')
    assert len(navigation.history_outline(store, "session")["entries"]) == 1
    with path.open("a") as handle:
        handle.write('"kind":"prompt_submit","prompt":"Later"}\n')
    assert len(navigation.history_outline(store, "session")["entries"]) == 2
    append(path, event_id="later", kind="prompt_submit", prompt="Different event")
    with pytest.raises(navigation.NavigationUnavailable, match="Conflicting"):
        navigation.history_outline(store, "session")


def test_corrupt_disposable_index_is_rebuilt_without_changing_ledger(tmp_path: Path) -> None:
    store, path = ledger(tmp_path)
    before = path.read_bytes()
    index = path.with_name("history-navigation.v1.sqlite3")
    index.write_bytes(b"not a sqlite database")
    result = navigation.history_outline(store, "session")
    assert len(result["entries"]) == 8
    assert path.read_bytes() == before
