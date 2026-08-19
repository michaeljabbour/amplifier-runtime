"""UI event ids must be unique across every process that appends to one log.

``ui-events.jsonl`` is SESSION-scoped and append-only. A resume starts a fresh
process, so a plain process-global counter restarts at 1 and the resumed run
re-mints ids the original run already used.

Observed in session ``eec9ae98``: both ``session_resume`` records carried
``event_id: "ev3"`` -- the same id the original ``session_start`` had. Anything
downstream that treats ``event_id`` as a key (dedup, acknowledgement,
correlation, the attention store's clear-by-id path) then silently conflates two
unrelated events.

The cross-process case is the one that broke, so it is tested by actually
starting another process rather than by simulating one.
"""

from __future__ import annotations

import subprocess
import sys

from amplifier_runtime.kernel.events import _mint_event_id

_MINT_IN_CHILD = (
    "from amplifier_runtime.kernel.events import _mint_event_id;"
    "print('\\n'.join(_mint_event_id() for _ in range(20)))"
)


def _mint_in_a_separate_process() -> list[str]:
    """Ids minted by a fresh interpreter -- i.e. what a resume produces."""
    completed = subprocess.run(
        [sys.executable, "-c", _MINT_IN_CHILD],
        capture_output=True,
        text=True,
        check=True,
    )
    return completed.stdout.split()


def test_ids_are_unique_within_a_single_run() -> None:
    minted = [_mint_event_id() for _ in range(200)]
    assert len(set(minted)) == len(minted)


def test_ids_share_one_run_tag_and_stay_monotonic() -> None:
    """Readability matters: ids should still sort and group by run."""
    minted = [_mint_event_id() for _ in range(5)]

    tags = {event_id.rsplit("-", 1)[0] for event_id in minted}
    assert len(tags) == 1, f"ids from one run carry different tags: {minted}"

    counters = [int(event_id.rsplit("-", 1)[1]) for event_id in minted]
    assert counters == sorted(counters)
    assert counters == list(range(counters[0], counters[0] + 5)), "counter skipped"


def test_ids_do_not_collide_across_processes() -> None:
    """The defect: a resume is a new process appending to the SAME log."""
    ours = {_mint_event_id() for _ in range(20)}
    theirs = set(_mint_in_a_separate_process())

    assert theirs, "child process minted nothing"
    assert ours.isdisjoint(theirs), (
        f"a fresh process re-minted ids this one already used: {sorted(ours & theirs)}. "
        "Appending both runs to one ui-events.jsonl would produce duplicate event_ids."
    )


def test_two_separate_processes_do_not_collide_with_each_other() -> None:
    """Neither run is privileged -- two resumes must not collide either."""
    first = set(_mint_in_a_separate_process())
    second = set(_mint_in_a_separate_process())

    assert first and second
    assert first.isdisjoint(second), sorted(first & second)
