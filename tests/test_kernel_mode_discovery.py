"""Mode discovery: the unfiltered, human-facing view of the mode system.

Two consumers read the mounted mode system, and they need DIFFERENT views:

- The LLM-facing ``mode`` tool (tool-mode) filters ``operation=list`` on
  ``entry.advertised``. Its own docstring is explicit that the unfiltered
  listing "is applied in the CLI, not here."
- A human-facing surface (a client's ``/modes``) must show every mode,
  marking the unadvertised ones, because an unadvertised mode is still
  activatable by name and still dispatchable by its ``shortcut:``.

``RealRuntime`` therefore reads hooks-mode's ``ModeDiscovery`` registry --
stashed at ``session_state["mode_discovery"]``, never registered as a
capability -- and falls back to the mode tool when it is absent, so a
session composed without hooks-mode behaves exactly as it did before.

Verified upstream contracts (amplifier-bundle-modes, ``hooks-mode``):

- ``ModeDiscovery.list_modes()`` returns ALL modes; ``include_unadvertised``
  is deprecated and raises a ``DeprecationWarning`` when passed.
- ``ModeListing`` is a 4-field NamedTuple: name, description, source,
  advertised. It carries no ``shortcut``.
- ``ModeDiscovery.get_shortcuts()`` maps shortcut -> mode name and gates
  only on ``mode_def.shortcut`` -- never on ``advertised``.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, NamedTuple

import pytest

from amplifier_runtime.kernel.runtime import RealRuntime


class FakeModeListing(NamedTuple):
    """The 4-field ``ModeListing`` NamedTuple hooks-mode actually returns."""

    name: str
    description: str
    source: str
    advertised: bool


class FakeDiscovery:
    """``ModeDiscovery`` stand-in: ``list_modes`` + ``get_shortcuts``."""

    def __init__(
        self,
        listings: tuple[FakeModeListing, ...] = (),
        shortcuts: dict[str, str] | None = None,
    ) -> None:
        self._listings = listings
        self._shortcuts = shortcuts or {}
        self.list_modes_kwargs: list[dict[str, Any]] = []

    def list_modes(self, **kwargs: Any) -> list[FakeModeListing]:
        self.list_modes_kwargs.append(kwargs)
        return list(self._listings)

    def get_shortcuts(self) -> dict[str, str]:
        return dict(self._shortcuts)


class FakeModeTool:
    """The mounted ``mode`` tool, recording every ``execute`` payload."""

    def __init__(self, output: Any = None, *, success: bool = True) -> None:
        self.output = output if output is not None else {"modes": []}
        self.success = success
        self.calls: list[dict[str, Any]] = []

    async def execute(self, payload: dict[str, Any]) -> SimpleNamespace:
        self.calls.append(payload)
        return SimpleNamespace(success=self.success, output=self.output)


def _runtime(
    *,
    discovery: Any = None,
    tool: Any = None,
    active_mode: str | None = None,
    started: bool = True,
) -> RealRuntime:
    """A ``RealRuntime`` with a duck-typed coordinator on ``_initialized``.

    ``started=False`` leaves ``_initialized`` as ``None`` -- the pre-boot
    state every mode accessor must tolerate.
    """
    runtime = RealRuntime(bundle=None)
    if not started:
        return runtime
    session_state: dict[str, object] = {}
    if discovery is not None:
        session_state["mode_discovery"] = discovery
    if active_mode is not None:
        session_state["active_mode"] = active_mode
    coordinator = SimpleNamespace(
        get=lambda point: {"mode": tool} if point == "tools" and tool is not None else {},
        session_state=session_state,
    )
    runtime._initialized = SimpleNamespace(coordinator=coordinator)  # type: ignore[assignment]
    return runtime


ADVERTISED = FakeModeListing("careful", "Confirm before writes", "modes", True)
HIDDEN = FakeModeListing("mode-design", "Author a new mode", "modes", False)


# --- list_native_modes: discovery first ---------------------------------


@pytest.mark.asyncio
async def test_listing_includes_unadvertised_modes_and_the_active_one() -> None:
    """The headline: a hidden mode is listed, flagged, and paired with the
    active mode -- the data a human-facing ``/modes`` needs."""
    discovery = FakeDiscovery(listings=(ADVERTISED, HIDDEN))
    runtime = _runtime(discovery=discovery, tool=FakeModeTool(), active_mode="careful")

    assert await runtime.list_native_modes() == {
        "active_mode": "careful",
        "modes": [
            {
                "name": "careful",
                "description": "Confirm before writes",
                "source": "modes",
                "advertised": True,
            },
            {
                "name": "mode-design",
                "description": "Author a new mode",
                "source": "modes",
                "advertised": False,
            },
        ],
    }


@pytest.mark.asyncio
async def test_listing_never_passes_the_deprecated_kwarg() -> None:
    """``include_unadvertised`` warns upstream; ``list_modes()`` already
    returns everything, so it must be called with no kwargs at all."""
    discovery = FakeDiscovery(listings=(ADVERTISED,))
    await _runtime(discovery=discovery).list_native_modes()
    assert discovery.list_modes_kwargs == [{}]


@pytest.mark.asyncio
async def test_listing_prefers_discovery_over_the_mounted_tool() -> None:
    """Discovery wins: the advertised-filtered tool is not consulted."""
    tool = FakeModeTool({"modes": [{"name": "from-the-tool"}]})
    runtime = _runtime(discovery=FakeDiscovery(listings=(ADVERTISED,)), tool=tool)

    result = await runtime.list_native_modes()

    assert isinstance(result, dict)
    assert [mode["name"] for mode in result["modes"]] == ["careful"]
    assert tool.calls == []


@pytest.mark.asyncio
async def test_active_mode_is_none_when_no_mode_is_active() -> None:
    runtime = _runtime(discovery=FakeDiscovery(listings=(ADVERTISED,)))
    result = await runtime.list_native_modes()
    assert isinstance(result, dict)
    assert result["active_mode"] is None


# --- list_native_modes: fallback preserves prior behavior ---------------


@pytest.mark.asyncio
async def test_listing_falls_back_to_the_tool_without_discovery() -> None:
    """A session composed WITHOUT hooks-mode keeps the old behavior exactly:
    the mode tool's raw output, passed through untouched."""
    tool = FakeModeTool("superpowers:\n  debug ...")
    runtime = _runtime(tool=tool)

    assert await runtime.list_native_modes() == "superpowers:\n  debug ..."
    assert tool.calls == [{"operation": "list"}]


@pytest.mark.asyncio
async def test_listing_falls_back_when_discovery_raises() -> None:
    """A broken registry degrades to the tool rather than failing the call."""

    class BoomDiscovery:
        def list_modes(self, **kwargs: Any) -> list[FakeModeListing]:
            raise RuntimeError("discovery exploded")

    tool = FakeModeTool({"modes": [{"name": "plan"}]})
    runtime = _runtime(discovery=BoomDiscovery(), tool=tool)

    assert await runtime.list_native_modes() == {"modes": [{"name": "plan"}]}
    assert tool.calls == [{"operation": "list"}]


@pytest.mark.asyncio
async def test_listing_falls_back_when_a_listing_entry_is_malformed() -> None:
    """A registry returning entries without the 4 fields must not poison the
    listing -- the mapping comprehension raises, and the tool answers."""
    discovery = FakeDiscovery()
    discovery._listings = (SimpleNamespace(name="broken"),)  # type: ignore[assignment]
    tool = FakeModeTool({"modes": []})
    runtime = _runtime(discovery=discovery, tool=tool)

    assert await runtime.list_native_modes() == {"modes": []}
    assert tool.calls == [{"operation": "list"}]


@pytest.mark.asyncio
async def test_listing_is_empty_without_any_mode_system() -> None:
    assert await _runtime().list_native_modes() == ""
    assert await _runtime(started=False).list_native_modes() == ""


@pytest.mark.asyncio
async def test_listing_is_empty_when_the_tool_reports_failure() -> None:
    runtime = _runtime(tool=FakeModeTool({"modes": []}, success=False))
    assert await runtime.list_native_modes() == ""


# --- native_mode_shortcuts ----------------------------------------------


@pytest.mark.asyncio
async def test_shortcuts_map_to_mode_names_including_unadvertised_ones() -> None:
    """``get_shortcuts()`` never filters on ``advertised`` -- that is exactly
    what lets a client dispatch a hidden mode's shortcut."""
    discovery = FakeDiscovery(
        listings=(ADVERTISED, HIDDEN),
        shortcuts={"careful": "careful", "evaluation": "evaluation"},
    )

    assert await _runtime(discovery=discovery).native_mode_shortcuts() == {
        "careful": "careful",
        "evaluation": "evaluation",
    }


@pytest.mark.asyncio
async def test_shortcuts_are_a_copy_not_the_registry_mapping() -> None:
    """Mutating the result must not corrupt the live registry."""
    discovery = FakeDiscovery(shortcuts={"careful": "careful"})
    runtime = _runtime(discovery=discovery)

    shortcuts = await runtime.native_mode_shortcuts()
    shortcuts["careful"] = "tampered"

    assert await runtime.native_mode_shortcuts() == {"careful": "careful"}


@pytest.mark.asyncio
async def test_shortcuts_are_empty_without_a_mode_system() -> None:
    """No hooks-mode, no shortcuts -- and the mode tool has no shortcut
    concept, so there is deliberately no fallback here."""
    assert await _runtime().native_mode_shortcuts() == {}
    assert await _runtime(started=False).native_mode_shortcuts() == {}
    assert await _runtime(tool=FakeModeTool()).native_mode_shortcuts() == {}


@pytest.mark.asyncio
async def test_shortcuts_degrade_when_discovery_raises() -> None:
    class BoomDiscovery:
        def get_shortcuts(self) -> dict[str, str]:
            raise RuntimeError("discovery exploded")

    assert await _runtime(discovery=BoomDiscovery()).native_mode_shortcuts() == {}


# --- _mode_discovery itself ---------------------------------------------


def test_mode_discovery_reads_session_state_not_a_capability() -> None:
    """hooks-mode does not register the registry as a capability, so a
    coordinator whose ``get_capability`` answers must still be ignored."""
    discovery = FakeDiscovery()
    runtime = _runtime(discovery=discovery)
    assert runtime._mode_discovery() is discovery


def test_mode_discovery_degrades_when_session_state_raises() -> None:
    class BoomCoordinator:
        @property
        def session_state(self) -> dict[str, object]:
            raise RuntimeError("coordinator exploded")

    runtime = RealRuntime(bundle=None)
    runtime._initialized = SimpleNamespace(coordinator=BoomCoordinator())  # type: ignore[assignment]
    assert runtime._mode_discovery() is None


def test_mode_discovery_is_none_before_boot() -> None:
    assert RealRuntime(bundle=None)._mode_discovery() is None
