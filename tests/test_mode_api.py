from __future__ import annotations

import pytest

from amplifier_runtime.kernel.serve import OP_PERMISSIONS, mode_state, set_modes


class Runtime:
    session_id = "modes-test"
    active = None
    calls = 0

    async def list_native_modes(self):
        return {
            "active_mode": self.active,
            "modes": [
                {
                    "name": "review",
                    "description": "Review only",
                    "source": "mj",
                    "advertised": False,
                },
                {"name": "plan", "description": "Plan work", "source": "modes"},
            ],
        }

    async def set_native_mode(self, name):
        self.calls += 1
        self.active = name
        return True, "Applied"


@pytest.mark.asyncio
async def test_discovery_includes_human_only_modes_and_actual_active_slot():
    runtime = Runtime()
    state = await mode_state(runtime)
    assert state["modes"][0]["advertised"] is False
    assert state["max_active"] == 1
    assert state["active"] == []
    assert (await set_modes(runtime, {"names": ["review"]}, busy=False))["active"] == ["review"]
    assert (await set_modes(runtime, {"names": []}, busy=False))["active"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize("names", [["missing"], ["review", "plan"], "review", [None]])
async def test_rejected_selections_never_call_mode_engine(names):
    runtime = Runtime()
    result = await set_modes(runtime, {"names": names}, busy=False)
    assert result["ok"] is False
    assert runtime.calls == 0


@pytest.mark.asyncio
async def test_busy_turn_blocks_policy_changes():
    runtime = Runtime()
    assert (await set_modes(runtime, {"names": ["review"]}, busy=True))["ok"] is False
    assert runtime.calls == 0


def test_mode_ops_preserve_read_write_authorization():
    assert OP_PERMISSIONS["modes.get"] == "read"
    assert OP_PERMISSIONS["modes.set"] == "write"


@pytest.mark.asyncio
async def test_plugin_exception_after_activation_reads_back_actual_state():
    class FailingRuntime(Runtime):
        async def set_native_mode(self, name):
            self.active = name
            raise RuntimeError("plugin failed after mutation")

    state = await set_modes(FailingRuntime(), {"names": ["review"]}, busy=False)
    assert state["ok"] is False
    assert state["active"] == ["review"]


@pytest.mark.asyncio
async def test_discovery_failure_does_not_apply_unknown_policy():
    class FailingRuntime(Runtime):
        async def list_native_modes(self):
            raise RuntimeError("unavailable")

    runtime = FailingRuntime()
    state = await set_modes(runtime, {"names": ["review"]}, busy=False)
    assert state["ok"] is False
    assert runtime.calls == 0


@pytest.mark.asyncio
async def test_success_without_engine_confirmation_is_rejected():
    class UnconfirmedRuntime(Runtime):
        async def set_native_mode(self, name):
            return True, "Applied"

    state = await set_modes(UnconfirmedRuntime(), {"names": ["review"]}, busy=False)
    assert state["ok"] is False
    assert state["active"] == []


@pytest.mark.asyncio
async def test_combined_selection_requires_engine_capacity_and_readback():
    class CombinedRuntime(Runtime):
        names = []

        async def list_native_modes(self):
            base = await super().list_native_modes()
            return {**base, "active_modes": self.names, "max_active": 8}

        async def set_native_modes(self, names):
            self.names = names
            return True, "Combined"

    runtime = CombinedRuntime()
    result = await set_modes(runtime, {"names": ["review", "plan"]}, busy=False)
    assert result["ok"]
    assert result["active"] == ["review", "plan"]
    assert result["max_active"] == 8


@pytest.mark.asyncio
async def test_model_change_is_busy_gated_and_uses_confirmed_runtime_state():
    from amplifier_runtime.kernel.serve import set_model_record

    class ModelRuntime:
        session_id = "model-test"
        model_name = "sample/old"

        async def set_model(self, selection):
            assert selection == "sample new"
            self.model_name = "sample/new"
            return True, "Changed"

    runtime = ModelRuntime()
    op = {"provider": "sample", "model": "new"}
    assert not (await set_model_record(runtime, op, busy=True))["ok"]
    assert runtime.model_name == "sample/old"
    result = await set_model_record(runtime, op, busy=False)
    assert result["ok"]
    assert result["model"] == "sample/new"
    assert OP_PERMISSIONS["model.set"] == "write"
