"""Live mounted-provider diagnostics shared by interactive clients."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from amplifier_runtime.kernel import session_ops


class _Coordinator:
    def __init__(self, providers: dict[str, object]) -> None:
        self._providers = providers

    def get(self, point: str):  # noqa: ANN201
        return self._providers if point == "providers" else None


def _provider(models=(), *, priority: int = 100, error: Exception | None = None):  # noqa: ANN001, ANN202
    async def list_models():  # noqa: ANN202
        if error is not None:
            raise error
        return list(models)

    return SimpleNamespace(
        list_models=list_models,
        priority=priority,
        config={"priority": priority, "api_key": "secret-value"},
        close=lambda: (_ for _ in ()).throw(AssertionError("mounted provider must not close")),
    )


@pytest.mark.asyncio
async def test_provider_test_checks_every_mounted_provider_and_isolates_failures() -> None:
    coordinator = _Coordinator(
        {
            "good": _provider([SimpleNamespace(id="m1")], priority=1),
            "bad": _provider(error=RuntimeError("bad key secret-value"), priority=2),
        }
    )

    results = await session_ops.test_providers(coordinator)

    assert [result.name for result in results] == ["bad", "good"]
    bad, good = results
    assert not bad.ok and "secret-value" not in bad.detail
    assert good.ok and good.detail == "1 model available"


@pytest.mark.asyncio
async def test_provider_test_named_unknown_lists_live_choices() -> None:
    (result,) = await session_ops.test_providers(_Coordinator({"openai": _provider()}), "nope")

    assert not result.ok
    assert "not mounted" in result.detail
    assert "openai" in result.detail


@pytest.mark.asyncio
async def test_provider_models_defaults_to_serving_provider_and_keeps_metadata() -> None:
    coordinator = _Coordinator(
        {
            "secondary": _provider([SimpleNamespace(id="other")], priority=10),
            "primary": _provider(
                [
                    SimpleNamespace(
                        id="model-x",
                        context_window=200_000,
                        max_output_tokens=8192,
                        capabilities=("vision", "tools"),
                    )
                ],
                priority=1,
            ),
        }
    )

    result = await session_ops.provider_models(coordinator)

    assert result.name == "primary"
    assert result.error == ""
    assert result.models == (
        session_ops.ProviderModelInfo(
            id="model-x",
            context_window=200_000,
            max_output_tokens=8192,
            capabilities=("vision", "tools"),
        ),
    )


@pytest.mark.asyncio
async def test_provider_models_supports_sync_listers() -> None:
    provider = SimpleNamespace(
        list_models=lambda: ["sync-model"],
        priority=1,
        config={"priority": 1},
    )

    result = await session_ops.provider_models(_Coordinator({"sync": provider}), "sync")

    assert [model.id for model in result.models] == ["sync-model"]
