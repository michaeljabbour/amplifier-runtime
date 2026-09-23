"""Opt-in immutable delegate consumer tests against Runtime's real spawner/store.

Set AMPLIFIER_DELEGATE_SOURCE to a Foundation checkout at the packaged delegate
revision. The normal offline suite does not download modules implicitly.
"""

from __future__ import annotations

import asyncio
import importlib.util
import os
import subprocess
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from amplifier_runtime.kernel.delegate_store import DelegateStore
from tests.test_delegate_recovery import Coordinator, Harness, Session
from tests.test_delegate_source import CONSUMER_REVISION


@pytest.fixture
def consumer() -> ModuleType:
    source = os.environ.get("AMPLIFIER_DELEGATE_SOURCE")
    if not source:
        pytest.skip("Set AMPLIFIER_DELEGATE_SOURCE to the immutable Foundation review checkout")
    root = Path(source)
    revision = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True)
    assert revision.strip() == CONSUMER_REVISION
    path = root / "modules/tool-delegate/amplifier_module_tool_delegate/__init__.py"
    spec = importlib.util.spec_from_file_location("runtime_review_delegate", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ConsumerCoordinator(Coordinator):
    """Core module mounting surface around the controlled provider fixture."""

    def __init__(self, parent: Session) -> None:
        super().__init__()
        self.module_resolver = parent.coordinator.module_resolver
        self.session = parent
        self.session_id = parent.session_id
        self.config = parent.config
        self.session_state: dict[str, Any] = {}
        self._tool_dispatch_context: dict[str, Any] = {}
        self._tool_dispatch_contexts: dict[str, Any] = {}
        self.tools: dict[str, Any] = {}
        self.contributors: dict[str, Any] = {}

    def register_contributor(self, point: str, name: str, value: Any) -> None:
        self.contributors[f"{point}/{name}"] = value

    async def mount(self, point: str, tool: Any, *, name: str = "") -> None:
        if point == "module-source-resolver":
            self.module_resolver = tool
            return
        assert point == "tools"
        self.tools[name] = tool

    def get(self, name: str) -> Any:
        if name == "agents":
            return self.config["agents"]
        return super().get(name)


@pytest.mark.asyncio
async def test_mounted_consumer_timeout_lease_and_persisted_resume(
    consumer: ModuleType,
    tmp_path: Path,
) -> None:
    harness = Harness(tmp_path)
    harness.parent.config["agents"] = {
        "researcher": {"instruction": "Preserve the research persona."}
    }
    coordinator = ConsumerCoordinator(harness.parent)
    harness.parent.coordinator = coordinator
    coordinator.register_capability("session.working_dir", str(tmp_path))
    harness.spawner.register(coordinator, harness.parent)
    emitted = asyncio.Event()
    cancelling = asyncio.Event()
    release_cleanup = asyncio.Event()
    cleanup_finished = asyncio.Event()

    async def blocked(child: Session) -> str:
        await child.coordinator.hooks.emit(
            "content_block:end", {"block": {"type": "text", "text": "Retained finding"}}
        )
        emitted.set()
        try:
            await asyncio.Future()
        finally:
            cancelling.set()
            await release_cleanup.wait()
            cleanup_finished.set()
        return "unreachable"

    harness.behavior = blocked
    await consumer.mount(coordinator, {"settings": {"timeout": 1, "exclude_tools": []}})
    tool = coordinator.tools["delegate"]
    task = asyncio.create_task(
        tool.execute({"agent": "researcher", "instruction": "Investigate", "context_depth": "none"})
    )
    try:
        await asyncio.wait_for(emitted.wait(), 5)
        result = await asyncio.wait_for(task, 5)
        assert not result.success
        assert result.output["status"] == "timeout"
        assert result.output["partial_available"] is True
        assert result.output["partial_response"] == "Retained finding"
        assert "response" not in result.output
        child_id = result.output["session_id"]
        await asyncio.wait_for(cancelling.wait(), 5)
        assert not cleanup_finished.is_set()
        rejected = await tool.execute({"session_id": child_id, "instruction": "Too soon"})
        assert not rejected.success
        assert "already running" in str(rejected.error)
        assert len(harness.created) == 1
    finally:
        release_cleanup.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        detached = list(tool._detached_child_tasks)
        if detached:
            await asyncio.wait_for(asyncio.gather(*detached, return_exceptions=True), 5)

    record = DelegateStore(harness.store).load(child_id, "parent")
    assert record.status == "incomplete"
    assert record.output == "Retained finding"
    assert not any(m.get("content") == "Retained finding" for m in record.messages)
    assert harness.created[0].cleaned
    harness.behavior = None
    fresh_spawner = harness.new_spawner()
    fresh_spawner.register(coordinator, harness.parent)
    resumed = await tool.execute({"session_id": child_id, "instruction": "Continue"})
    assert resumed.success, resumed.error
    assert resumed.output["response"] == "finished"
    child = harness.created[-1]
    user_messages = [
        m["content"] for m in child.coordinator.context.messages if m["role"] == "user"
    ]
    assert user_messages[0] == "Investigate"
    assert user_messages[-1] == "Continue"
    assert len(user_messages) == 3
    assert "inspect actual state before repeating side effects" in user_messages[1]
    assert [m["content"] for m in child.coordinator.context.messages if m["role"] == "system"] == [
        "Preserve the research persona."
    ]
    assert DelegateStore(harness.store).load(child_id, "parent").status == "success"
