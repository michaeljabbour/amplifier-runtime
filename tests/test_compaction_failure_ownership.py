"""An internal compaction failure must not retry a possibly mutated context."""

from types import SimpleNamespace

import pytest

from amplifier_runtime.kernel.session_ops import compact_context


@pytest.mark.asyncio
async def test_compaction_body_type_error_is_not_retried_as_signature_fallback() -> None:
    class Context:
        calls = 0

        async def get_messages(self):
            return [{"role": "user", "content": "Keep pending intent"}]

        async def compact(self, focus: str = "") -> None:
            self.calls += 1
            # A provider/context bug after work begins is different from a
            # signature rejecting focus before entering the function.
            raise TypeError("summary assembly failed")

    context = Context()
    ok, message = await compact_context(SimpleNamespace(get=lambda name: context), "pending intent")
    assert not ok
    assert "summary assembly failed" in message
    assert context.calls == 1


@pytest.mark.asyncio
async def test_context_without_focus_parameter_is_compacted_once() -> None:
    class Context:
        calls = 0

        async def get_messages(self):
            return []

        async def compact(self) -> None:
            self.calls += 1

    context = Context()
    ok, _ = await compact_context(SimpleNamespace(get=lambda name: context), "pending intent")
    assert ok
    assert context.calls == 1


@pytest.mark.asyncio
async def test_compaction_with_kwargs_receives_focus() -> None:
    received = []

    async def compact(**kwargs):
        received.append(kwargs)

    context = SimpleNamespace(compact=compact)
    ok, _ = await compact_context(SimpleNamespace(get=lambda name: context), "pending intent")
    assert ok
    assert received == [{"focus": "pending intent"}]


@pytest.mark.asyncio
async def test_opaque_callable_failure_is_invoked_once(monkeypatch) -> None:
    from amplifier_runtime.kernel import session_ops

    calls = []

    async def compact(**kwargs):
        calls.append(kwargs)
        raise TypeError("internal failure")

    def unavailable_signature(value):
        raise ValueError("native signature unavailable")

    monkeypatch.setattr(session_ops.inspect, "signature", unavailable_signature)
    context = SimpleNamespace(compact=compact)
    ok, message = await compact_context(SimpleNamespace(get=lambda name: context), "pending intent")
    assert not ok and "internal failure" in message
    assert calls == [{"focus": "pending intent"}]
