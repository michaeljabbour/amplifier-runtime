"""Regression coverage for the reviewed recursive Anchors source lock."""

from __future__ import annotations

from amplifier_runtime.kernel.source_lock import LOCKED_GIT_REFS, pin_git_uri


CONTEXT_SIMPLE_REPOSITORY = "git+https://github.com/microsoft/amplifier-module-context-simple"
STICKY_COMPACTION_FIX = "a2a098bd21dc4c11e177bb66d3c86f380f77457a"


def test_context_simple_lock_contains_sticky_total_token_compaction_fix() -> None:
    """Do not regress to the pre-fix compactor that repeatedly stalled at L1."""
    assert LOCKED_GIT_REFS[CONTEXT_SIMPLE_REPOSITORY] == STICKY_COMPACTION_FIX
    assert pin_git_uri(f"{CONTEXT_SIMPLE_REPOSITORY}@main") == (
        f"{CONTEXT_SIMPLE_REPOSITORY}@{STICKY_COMPACTION_FIX}"
    )
