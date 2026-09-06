"""A departed process releases ownership without requiring checkpoint cleanup."""

import subprocess
import sys
from pathlib import Path

from amplifier_runtime.kernel.delegate_store import DelegateStore
from amplifier_runtime.kernel.persistence import SessionStore


def test_process_exit_retains_checkpoint_and_releases_delegate_writer(tmp_path: Path) -> None:
    script = """
import os
import sys
from pathlib import Path
from amplifier_runtime.kernel.delegate_store import DelegateRecord, DelegateStore
from amplifier_runtime.kernel.persistence import SessionStore
root = Path(sys.argv[1])
store = DelegateStore(SessionStore(root / "sessions", project_dir=root))
with store.claim("child"):
    record = DelegateRecord(session_id="child", parent_id="parent", agent_name="scout",
        project_dir=str(root), config={}, overlay={}, execution_id="crashed-attempt",
        messages=[{"role": "user", "content": "safe saved instruction"}])
    store.save(record)
    record.output = "retained before process death"
    store.save_partial(record)
    os._exit(0)
"""
    subprocess.run([sys.executable, "-c", script, str(tmp_path)], check=True, timeout=15)
    store = DelegateStore(SessionStore(tmp_path / "sessions", project_dir=tmp_path))
    record = store.load("child", "parent")
    assert record.status == "incomplete"
    assert record.output == "retained before process death"
    assert record.messages == [{"role": "user", "content": "safe saved instruction"}]
    with store.claim("child"):
        assert store.is_running("child")
