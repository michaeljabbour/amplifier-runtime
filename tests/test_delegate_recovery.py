"""Durable delegation behavior with real storage and controlled provider execution."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

from amplifier_runtime.kernel.delegate_store import DelegateStore
from amplifier_runtime.kernel.persistence import SessionStore
from amplifier_runtime.kernel.spawner import SessionSpawner


class Hooks:
    def __init__(self) -> None:
        self.handlers: dict[str, list[Any]] = {}

    def register(self, event: str, handler: Any, **kwargs: Any) -> Any:
        self.handlers.setdefault(event, []).append(handler)
        return lambda: self.handlers[event].remove(handler)

    async def emit(self, event: str, data: dict[str, Any]) -> None:
        for handler in list(self.handlers.get(event, [])):
            await handler(event, data)


class Context:
    def __init__(self) -> None:
        self.messages: list[dict[str, Any]] = []
        self.cancelling = False
        self.reads_after_cancel = 0

    async def get_messages(self) -> list[dict[str, Any]]:
        if self.cancelling:
            self.reads_after_cancel += 1
            raise AssertionError("Cancellation recovery awaited the context")
        return deepcopy(self.messages)

    async def set_messages(self, messages: list[dict[str, Any]]) -> None:
        self.messages = deepcopy(messages)

    async def add_message(self, message: dict[str, Any]) -> None:
        self.messages.append(deepcopy(message))


class Coordinator:
    def __init__(self) -> None:
        self.context = Context()
        self.hooks = Hooks()
        self.capabilities: dict[str, Any] = {}
        self.module_resolver: Any = None

    def get(self, name: str) -> Any:
        return {
            "context": self.context,
            "hooks": self.hooks,
            "module-source-resolver": self.module_resolver,
        }.get(name)

    async def mount(self, point: str, value: Any) -> None:
        assert point == "module-source-resolver"
        self.module_resolver = value

    def get_capability(self, name: str) -> Any:
        return self.capabilities.get(name)

    def register_capability(self, name: str, value: Any) -> None:
        self.capabilities[name] = value


class Session:
    def __init__(self, *, config: dict[str, Any], session_id: str, **kwargs: Any) -> None:
        self.config = config
        self.session_id = session_id
        self.coordinator = Coordinator()
        from amplifier_foundation.bundle._prepared import BundleModuleResolver
        from amplifier_runtime.kernel.delegate_store import _module_sources

        # Controlled execution does not import module files, but models the
        # production resolver's activated-module map for resume admission.
        self.coordinator.module_resolver = BundleModuleResolver(
            {module: Path("/controlled-modules") / module for module in _module_sources(config)}
        )
        self.cleaned = False
        self.behavior: Any = None

    async def initialize(self) -> None:
        pass

    async def execute(self, instruction: str) -> str:
        await self.coordinator.context.add_message({"role": "user", "content": instruction})
        await self.coordinator.hooks.emit("provider:request", {})
        if self.behavior is not None:
            return await self.behavior(self)
        await self.coordinator.context.add_message({"role": "assistant", "content": "finished"})
        return "finished"

    async def cleanup(self) -> None:
        self.cleaned = True


class Harness:
    def __init__(self, root: Path) -> None:
        self.store = SessionStore(root / "sessions", project_dir=root)
        self.created: list[Session] = []
        self.behavior: Any = None
        self.parent = Session(
            session_id="parent",
            config={
                "providers": [
                    {
                        "module": "provider-test",
                        "source": "test-provider",
                        "id": "primary",
                        "config": {
                            "api_key": "fixture-old-key",
                            "default_model": "root-model",
                            "base_url": "https://example.invalid/root",
                        },
                    }
                ]
            },
        )
        self.parent.coordinator.register_capability("session.working_dir", str(root))
        self.spawner = self.new_spawner()

    def new_spawner(self) -> SessionSpawner:
        def factory(**kwargs: Any) -> Session:
            child = Session(**kwargs)
            child.behavior = self.behavior
            self.created.append(child)
            return child

        return SessionSpawner(store=self.store, session_factory=factory)

    async def spawn(self) -> dict[str, Any]:
        return await self.spawner.spawn(
            "researcher",
            "Investigate",
            self.parent,
            sub_session_id="child",
            agent_configs={
                "researcher": {
                    "instruction": "Preserve this research persona.",
                    "providers": [
                        {
                            "module": "provider-test",
                            "id": "primary",
                            "config": {
                                "default_model": "child-model",
                                "base_url": "https://example.invalid/child",
                                "reasoning_effort": "high",
                            },
                        }
                    ],
                }
            },
        )


async def stop_at_request(harness: Harness, *, with_partial: bool = False) -> None:
    reached = asyncio.Event()

    async def blocked(child: Session) -> str:
        if with_partial:
            await child.coordinator.hooks.emit(
                "content_block:end",
                {"block": {"type": "text", "text": "Useful unfinished findings"}},
            )
        reached.set()
        try:
            await asyncio.Future()
        finally:
            child.coordinator.context.cancelling = True
        return "unreachable"

    harness.behavior = blocked
    task = asyncio.create_task(harness.spawn())
    try:
        await asyncio.wait_for(reached.wait(), 5)
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(task, 0.01)
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_first_request_timeout_is_recoverable_without_reading_cancelled_context(
    tmp_path: Path,
) -> None:
    harness = Harness(tmp_path)
    await stop_at_request(harness)
    record = DelegateStore(harness.store).load("child", "parent")
    assert record.status == "incomplete"
    assert any(m.get("content") == "Investigate" for m in record.messages)
    assert harness.created[0].coordinator.context.reads_after_cancel == 0
    assert harness.created[0].cleaned
    harness.behavior = None
    result = await harness.new_spawner().resume("child", "Continue", harness.parent)
    assert result["status"] == "success"


@pytest.mark.asyncio
async def test_resume_restores_persona_and_routing_but_refreshes_credentials(
    tmp_path: Path,
) -> None:
    harness = Harness(tmp_path)
    await stop_at_request(harness)
    serialized = (harness.store.session_dir("child") / "delegate.v1.json").read_text()
    assert "fixture-old-key" not in serialized
    live = harness.parent.config["providers"][0]["config"]
    live.update(
        api_key="fixture-new-key",
        default_model="changed-parent",
        base_url="https://example.invalid/changed",
        reasoning_effort="low",
    )
    harness.behavior = None
    result = await harness.new_spawner().resume("child", "Continue", harness.parent)
    assert result["status"] == "success"
    child = harness.created[-1]
    config = child.config["providers"][0]["config"]
    assert config["api_key"] == "fixture-new-key"
    assert config["default_model"] == "child-model"
    assert config["base_url"] == "https://example.invalid/child"
    assert config["reasoning_effort"] == "high"
    systems = [m["content"] for m in child.coordinator.context.messages if m["role"] == "system"]
    assert systems == ["Preserve this research persona."]
    users = [m["content"] for m in child.coordinator.context.messages if m["role"] == "user"]
    assert users[0] == "Investigate"
    assert "inspect actual state" in users[1]
    assert users[-1] == "Continue"


@pytest.mark.asyncio
async def test_partial_is_available_before_cancellation_and_stays_diagnostic(
    tmp_path: Path,
) -> None:
    harness = Harness(tmp_path)
    reached = asyncio.Event()

    async def blocked(child: Session) -> str:
        await child.coordinator.hooks.emit(
            "content_block:end", {"block": {"type": "text", "text": "Unfinished findings"}}
        )
        reached.set()
        await asyncio.Future()
        return "unreachable"

    harness.behavior = blocked
    task = asyncio.create_task(harness.spawn())
    try:
        await asyncio.wait_for(reached.wait(), 5)
        partial = harness.spawner.partial("child")
        assert partial and partial["text"] == "Unfinished findings"
        with pytest.raises(RuntimeError, match="already running"):
            await harness.new_spawner().resume("child", "Duplicate", harness.parent)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    record = DelegateStore(harness.store).load("child", "parent")
    assert record.output == "Unfinished findings"
    assert not any(m.get("content") == "Unfinished findings" for m in record.messages)


@pytest.mark.asyncio
async def test_other_parent_and_routing_overrides_cannot_resume_child(tmp_path: Path) -> None:
    harness = Harness(tmp_path)
    await harness.spawn()
    before = len(harness.created)
    other = Session(config=harness.parent.config, session_id="other-parent")
    with pytest.raises(PermissionError):
        await harness.spawner.resume("child", "Steal", other)
    with pytest.raises(ValueError, match="routing"):
        await harness.spawner.resume("child", "Reroute", harness.parent, model_role="fast")
    assert len(harness.created) == before


@pytest.mark.asyncio
async def test_unbalanced_tool_call_never_replaces_safe_checkpoint(tmp_path: Path) -> None:
    harness = Harness(tmp_path)
    reached = asyncio.Event()

    async def blocked(child: Session) -> str:
        await child.coordinator.context.add_message(
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "call-1",
                        "type": "function",
                        "function": {"name": "read_file", "arguments": "{}"},
                    }
                ],
            }
        )
        await child.coordinator.hooks.emit("provider:request", {})
        reached.set()
        await asyncio.Future()
        return "unreachable"

    harness.behavior = blocked
    task = asyncio.create_task(harness.spawn())
    try:
        await asyncio.wait_for(reached.wait(), 5)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    record = DelegateStore(harness.store).load("child", "parent")
    assert not any(m.get("tool_calls") for m in record.messages)
    assert any(m.get("content") == "Investigate" for m in record.messages)


@pytest.mark.asyncio
async def test_completed_tool_pair_survives_fresh_spawner_resume(tmp_path: Path) -> None:
    harness = Harness(tmp_path)
    reached = asyncio.Event()

    async def blocked(child: Session) -> str:
        await child.coordinator.context.add_message(
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "call-1",
                        "type": "function",
                        "function": {"name": "read_file", "arguments": "{}"},
                    }
                ],
            }
        )
        await child.coordinator.context.add_message(
            {"role": "tool", "tool_call_id": "call-1", "content": "verified source"}
        )
        await child.coordinator.hooks.emit("provider:request", {})
        reached.set()
        await asyncio.Future()
        return "unreachable"

    harness.behavior = blocked
    task = asyncio.create_task(harness.spawn())
    try:
        await asyncio.wait_for(reached.wait(), 5)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    harness.behavior = None
    result = await harness.new_spawner().resume("child", "Summarize", harness.parent)
    assert result["status"] == "success"
    messages = harness.created[-1].coordinator.context.messages
    calls = [call["id"] for message in messages for call in message.get("tool_calls", [])]
    replies = [message["tool_call_id"] for message in messages if message["role"] == "tool"]
    assert calls == replies == ["call-1"]
    assert any(message.get("content") == "verified source" for message in messages)


@pytest.mark.asyncio
async def test_provider_error_persists_terminal_status_and_partial(tmp_path: Path) -> None:
    harness = Harness(tmp_path)

    async def fail(child: Session) -> str:
        await child.coordinator.hooks.emit(
            "content_block:end", {"block": {"type": "text", "text": "Partial analysis"}}
        )
        raise RuntimeError("provider unavailable")

    harness.behavior = fail
    result = await harness.spawn()
    assert result["status"] == "error"
    record = DelegateStore(harness.store).load("child", "parent")
    assert record.status == "error"
    assert "Partial analysis" in record.output
    assert harness.created[0].cleaned


@pytest.mark.asyncio
async def test_replaced_provider_cannot_supply_credentials_to_saved_child(tmp_path: Path) -> None:
    harness = Harness(tmp_path)
    await harness.spawn()
    before = len(harness.created)
    harness.parent.config["providers"][0]["source"] = "different-provider-source"
    with pytest.raises(ValueError, match="composition changed"):
        await harness.spawner.resume("child", "Continue", harness.parent)
    assert len(harness.created) == before
