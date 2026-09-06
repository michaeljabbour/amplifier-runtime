"""Adversarial regressions for the mounted delegate and durable host."""

from __future__ import annotations

from pathlib import Path
from types import ModuleType

import pytest

from tests import test_delegate_consumer_integration as integration
from tests.test_delegate_consumer_integration import ConsumerCoordinator
from tests.test_delegate_recovery import Harness, Session


consumer = integration.consumer


@pytest.mark.asyncio
async def test_mounted_delegate_provider_error_is_not_success(
    consumer: ModuleType,
    tmp_path: Path,
) -> None:
    harness = Harness(tmp_path)
    harness.parent.config["agents"] = {"researcher": {"instruction": "Investigate"}}
    coordinator = ConsumerCoordinator(harness.parent)
    harness.parent.coordinator = coordinator
    coordinator.register_capability("session.working_dir", str(tmp_path))
    harness.spawner.register(coordinator, harness.parent)

    async def fail(child: Session) -> str:
        raise RuntimeError("Provider unavailable")

    harness.behavior = fail
    await consumer.mount(coordinator, {"settings": {"timeout": None, "exclude_tools": []}})
    result = await coordinator.tools["delegate"].execute(
        {
            "agent": "researcher",
            "instruction": "Investigate",
            "context_depth": "none",
        }
    )
    assert not result.success, result.output


@pytest.mark.asyncio
async def test_mounted_delegate_unchanged_routing_can_resume(
    consumer: ModuleType,
    tmp_path: Path,
) -> None:
    harness = Harness(tmp_path)
    harness.parent.config["agents"] = {"researcher": {"instruction": "Investigate"}}
    coordinator = ConsumerCoordinator(harness.parent)
    harness.parent.coordinator = coordinator
    coordinator.register_capability("session.working_dir", str(tmp_path))
    harness.spawner.register(coordinator, harness.parent)
    await consumer.mount(coordinator, {"settings": {"timeout": None, "exclude_tools": []}})
    tool = coordinator.tools["delegate"]
    spawned = await tool.execute(
        {
            "agent": "researcher",
            "instruction": "Investigate",
            "context_depth": "none",
            "provider_preferences": [{"provider": "primary", "model": "root-model"}],
        }
    )
    assert spawned.success, spawned.error
    resumed = await tool.execute(
        {"session_id": spawned.output["session_id"], "instruction": "Continue"}
    )
    assert resumed.success, resumed.error


@pytest.mark.asyncio
async def test_changed_tool_source_cannot_resume_saved_child(tmp_path: Path) -> None:
    harness = Harness(tmp_path)
    harness.parent.config["tools"] = [{"module": "tool-example", "source": "reviewed-source"}]
    harness.parent.coordinator.module_resolver._paths["tool-example"] = tmp_path / "tool"
    assert (await harness.spawn())["status"] == "success"
    harness.parent.config["tools"][0]["source"] = "replacement-source"
    with pytest.raises(ValueError, match="composition changed"):
        await harness.new_spawner().resume("child", "Continue", harness.parent)


@pytest.mark.parametrize("kind", ["hooks", "context", "orchestrator"])
def test_changed_nonprovider_module_source_is_rejected(kind: str) -> None:
    from amplifier_runtime.kernel.delegate_store import refresh_provider_secrets

    saved = {"module": "example", "source": "reviewed-source"}
    live = {"module": "example", "source": "replacement-source"}
    before = {kind: [saved]} if kind == "hooks" else {"session": {kind: saved}}
    after = {kind: [live]} if kind == "hooks" else {"session": {kind: live}}
    with pytest.raises(ValueError, match="composition changed"):
        refresh_provider_secrets(before, after)


def test_immutable_child_only_module_is_preserved() -> None:
    from amplifier_runtime.kernel.delegate_store import refresh_provider_secrets

    config = {
        "tools": [{"module": "child-tool", "source": "git+https://example.test/tool@" + "a" * 40}]
    }
    assert refresh_provider_secrets(config, {}) == config


def test_unresolved_nonprovider_secret_refuses_resume() -> None:
    from amplifier_runtime.kernel.delegate_store import refresh_provider_secrets

    config = {"hooks": [{"module": "private-hook", "config": {"api_key": "[REDACTED]"}}]}
    with pytest.raises(ValueError, match="unresolved redacted"):
        refresh_provider_secrets(config, config)


@pytest.mark.asyncio
async def test_resume_resolver_ignores_another_childs_cached_source(tmp_path: Path) -> None:
    from unittest.mock import AsyncMock, MagicMock

    from amplifier_foundation.bundle._prepared import BundleModuleResolver
    from amplifier_runtime.kernel.spawner import _inherit_module_resolver

    activator = MagicMock()
    activator.activate = AsyncMock(return_value=tmp_path / "saved-child-module")
    original = BundleModuleResolver({"child-tool": tmp_path / "other-child-module"}, activator)
    parent = MagicMock()
    parent.get.return_value = original
    child = MagicMock()
    child.mount = AsyncMock()
    source = "git+https://example.test/tool@" + "a" * 40
    await _inherit_module_resolver(
        parent,
        child,
        resume_config={"tools": [{"module": "child-tool", "source": source}]},
        parent_config={},
    )
    isolated = child.mount.call_args.args[1]
    assert isolated is not original
    assert isolated.get_module_source("child-tool") == str(tmp_path / "saved-child-module")
    assert original.get_module_source("child-tool") == str(tmp_path / "other-child-module")
    activator.activate.assert_awaited_once_with("child-tool", source)


@pytest.mark.asyncio
async def test_resume_rejects_unknown_resolver_instead_of_sharing_cache() -> None:
    from unittest.mock import AsyncMock, MagicMock

    from amplifier_runtime.kernel.spawner import _inherit_module_resolver

    parent = MagicMock()
    child = MagicMock()
    child.mount = AsyncMock()
    with pytest.raises(ValueError, match="source-isolated"):
        await _inherit_module_resolver(parent, child, resume_config={}, parent_config={})
    child.mount.assert_not_awaited()


@pytest.mark.asyncio
async def test_resume_with_sources_and_no_resolver_is_rejected() -> None:
    from unittest.mock import AsyncMock, MagicMock

    from amplifier_runtime.kernel.spawner import _inherit_module_resolver

    parent = MagicMock()
    parent.get.return_value = None
    child = MagicMock()
    child.mount = AsyncMock()
    with pytest.raises(ValueError, match="no module resolver"):
        await _inherit_module_resolver(
            parent,
            child,
            resume_config={"tools": [{"module": "tool", "source": "saved-source"}]},
            parent_config={},
        )
    child.mount.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("explicit_source", [True, False])
async def test_initial_durable_spawn_does_not_reuse_another_childs_module(
    tmp_path: Path, explicit_source: bool
) -> None:
    from unittest.mock import AsyncMock, MagicMock

    from amplifier_foundation.bundle._prepared import BundleModuleResolver

    harness = Harness(tmp_path)
    source = "git+https://example.test/tool@" + "b" * 40
    module = {"module": "child-tool"}
    if explicit_source:
        module["source"] = source
    activator = MagicMock()
    activator.activate = AsyncMock(return_value=tmp_path / "intended-child-module")
    original = BundleModuleResolver(
        {
            "provider-test": tmp_path / "provider",
            "child-tool": tmp_path / "another-child-module",
        },
        activator,
    )
    harness.parent.coordinator.module_resolver = original
    result = await harness.spawner.spawn(
        "researcher",
        "Investigate",
        harness.parent,
        sub_session_id="child",
        agent_configs={"researcher": {"tools": [module]}},
    )
    assert result["status"] == "success"
    child_resolver = harness.created[0].coordinator.module_resolver
    assert child_resolver is not original
    assert original.get_module_source("child-tool") == str(tmp_path / "another-child-module")
    if explicit_source:
        assert child_resolver.get_module_source("child-tool") == str(
            tmp_path / "intended-child-module"
        )
        activator.activate.assert_awaited_once_with("child-tool", source)
    else:
        # No cached source is injected into entry-point-only module loading.
        assert child_resolver.get_module_source("child-tool") is None
        activator.activate.assert_not_awaited()


@pytest.mark.parametrize(
    "source", ["file:///tmp/tool@" + "a" * 40, "git+https://example.test/tool@main"]
)
def test_mutable_child_source_cannot_masquerade_as_immutable(source: str) -> None:
    from amplifier_runtime.kernel.delegate_store import refresh_provider_secrets

    with pytest.raises(ValueError, match="immutable source"):
        refresh_provider_secrets({"tools": [{"module": "child-tool", "source": source}]}, {})
