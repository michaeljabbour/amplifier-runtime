"""Delegate recovery is advertised from the initialized session capability."""

from types import SimpleNamespace

from amplifier_runtime.kernel.serve import _runtime_capabilities_record


def test_recovery_feature_requires_callable_initialized_session_resume() -> None:
    assert "delegates.resume" not in _runtime_capabilities_record()["features"]
    coordinator = SimpleNamespace(get_capability=lambda name: None)
    runtime = SimpleNamespace(_initialized=SimpleNamespace(coordinator=coordinator))
    assert "delegates.resume" not in _runtime_capabilities_record(runtime)["features"]
    coordinator.get_capability = lambda name: (lambda: None) if name == "session.resume" else None
    assert "delegates.resume" in _runtime_capabilities_record(runtime)["features"]
