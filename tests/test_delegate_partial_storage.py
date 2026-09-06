"""Partial writes stay bounded and cannot contaminate another execution."""

import json
from pathlib import Path

from amplifier_runtime.kernel.delegate_store import (
    PARTIAL_NAME,
    RECORD_NAME,
    DelegateRecord,
    DelegateStore,
)
from amplifier_runtime.kernel.persistence import SessionStore


def test_partial_updates_do_not_rewrite_transcript_or_safe_checkpoint(tmp_path: Path) -> None:
    store = DelegateStore(SessionStore(tmp_path / "sessions", project_dir=tmp_path))
    record = DelegateRecord(
        session_id="child",
        parent_id="parent",
        agent_name="scout",
        project_dir=str(tmp_path),
        config={},
        overlay={},
        execution_id="attempt-one",
        messages=[{"role": "user", "content": "Retain this safe context"}],
    )
    store.save(record)
    directory = store.store.session_dir("child")
    before = {
        name: (directory / name).read_bytes()
        for name in (RECORD_NAME, "transcript.jsonl", "metadata.json")
    }
    record.output = "x" * 24000
    store.save_partial(record)
    assert all((directory / name).read_bytes() == content for name, content in before.items())
    assert (directory / PARTIAL_NAME).stat().st_size < 17000
    assert len(store.load("child", "parent").output) == 16000
    assert json.loads((directory / RECORD_NAME).read_text())["output"] == ""

    record.execution_id = "attempt-two"
    record.output = ""
    store.save(record)
    assert store.load("child", "parent").output == ""
    (directory / PARTIAL_NAME).write_text("broken diagnostics")
    assert store.load("child", "parent").messages == record.messages


def test_terminal_success_ignores_earlier_diagnostics(tmp_path: Path) -> None:
    store = DelegateStore(SessionStore(tmp_path / "sessions", project_dir=tmp_path))
    record = DelegateRecord(
        session_id="child",
        parent_id="parent",
        agent_name="scout",
        project_dir=str(tmp_path),
        config={},
        overlay={},
        execution_id="one",
        output="unfinished",
    )
    store.save_partial(record)
    record.status = "success"
    record.output = "final answer"
    store.save(record)
    assert store.load("child", "parent").output == "final answer"
