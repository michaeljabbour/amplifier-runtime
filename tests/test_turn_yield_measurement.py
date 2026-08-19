"""A turn's yield of zero must not be reported when nobody could measure it.

``capture_git_diff`` reports unavailable for a directory that is not a git
repository **and** for a repository with no commits yet -- ``git diff HEAD`` has
no HEAD to diff against. Both are ordinary states for a project someone is
starting from scratch, which is exactly when a turn writes the most files.

``delta_from`` then returns ``None``, and the close-out collapsed that to
``files_changed: 0`` / ``diffstat: ""`` -- indistinguishable from a genuine
measurement of no change. In session ``eec9ae98`` all 27 turns reported
``files_changed: 0``, including turns containing 94 ``write_file`` calls and
several ``cat > ... <<EOF`` heredocs.

``yield_measured`` separates "nothing changed" from "nobody could tell".
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from amplifier_runtime.kernel.events import PromptComplete, UIEvent
from amplifier_runtime.kernel.git_yield import GitDiffSnapshot, capture_git_diff
from amplifier_runtime.kernel.runtime import RealRuntime


class _RecordingBridge:
    def __init__(self) -> None:
        self.events: list[UIEvent] = []

    def emit(self, event: UIEvent) -> None:
        self.events.append(event)


def _runtime(cwd: Path) -> tuple[RealRuntime, _RecordingBridge]:
    bridge = _RecordingBridge()
    runtime = RealRuntime(bundle="offline", mode=lambda: "chat")
    runtime.bridge = bridge  # type: ignore[assignment]
    runtime._turn_cwd = lambda: cwd  # type: ignore[method-assign]
    return runtime, bridge


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
    )


async def _close_out(
    runtime: RealRuntime, bridge: _RecordingBridge, start: GitDiffSnapshot
) -> PromptComplete:
    await runtime._emit_close_out("done", start)
    completes = [event for event in bridge.events if isinstance(event, PromptComplete)]
    assert len(completes) == 1
    return completes[0]


@pytest.mark.asyncio
async def test_a_non_repository_is_unmeasured_not_unchanged(tmp_path: Path) -> None:
    """The defect: 94 write_file calls reported as "0 files changed"."""
    start = await capture_git_diff(tmp_path)
    assert start.available is False, "fixture must be a directory git cannot diff"

    runtime, bridge = _runtime(tmp_path)
    (tmp_path / "written-during-the-turn.txt").write_text("real work\n")
    event = await _close_out(runtime, bridge, start)

    assert event.yield_measured is False, (
        "an unmeasurable turn reported its yield as if it had been measured"
    )
    assert event.files_changed == 0
    assert event.diffstat == ""


@pytest.mark.asyncio
async def test_a_repository_with_no_commits_is_also_unmeasured(tmp_path: Path) -> None:
    """`git diff HEAD` has no HEAD -- the first turns of a brand-new project."""
    _git(tmp_path, "init", "-q")
    start = await capture_git_diff(tmp_path)
    assert start.available is False

    runtime, bridge = _runtime(tmp_path)
    (tmp_path / "a.txt").write_text("hello\n")

    event = await _close_out(runtime, bridge, start)
    assert event.yield_measured is False


@pytest.mark.asyncio
async def test_a_real_repository_reports_a_measured_yield(tmp_path: Path) -> None:
    """The happy path must still say it measured, and still count files."""
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "commit", "-q", "--allow-empty", "-m", "init")
    start = await capture_git_diff(tmp_path)
    assert start.available is True

    runtime, bridge = _runtime(tmp_path)
    (tmp_path / "a.txt").write_text("one\ntwo\nthree\n")
    event = await _close_out(runtime, bridge, start)

    assert event.yield_measured is True
    assert event.files_changed == 1
    assert event.diffstat, "a measured change must carry its line-delta label"


@pytest.mark.asyncio
async def test_a_measured_turn_with_no_changes_is_distinguishable(tmp_path: Path) -> None:
    """Zero-and-measured must be a different report from zero-and-unmeasured.

    This is the whole point: both carry ``files_changed == 0``, so the flag is
    the only thing separating "nothing happened" from "nobody looked".
    """
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "commit", "-q", "--allow-empty", "-m", "init")
    start = await capture_git_diff(tmp_path)

    runtime, bridge = _runtime(tmp_path)
    event = await _close_out(runtime, bridge, start)

    assert event.files_changed == 0
    assert event.yield_measured is True


def test_the_field_defaults_to_measured() -> None:
    """Raw hook payloads normalized elsewhere carry no yield fields at all.

    Defaulting to False there would mark every ordinary event as unmeasured;
    only the runtime's own close-out knows the snapshot failed.
    """
    assert PromptComplete().yield_measured is True
