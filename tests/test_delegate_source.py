"""The recovery consumer advances independently of the Anchors composition."""

from __future__ import annotations

import yaml

from amplifier_runtime.kernel.config import build_source_resolver, packaged_bundles_dir
from amplifier_runtime.kernel.source_lock import ANCHORS_COMMIT, pin_mount_plan_sources

FOUNDATION = "git+https://github.com/microsoft/amplifier-foundation"
CONSUMER_REVISION = "52cbf74f99cc16ae88a2043840b253576269c704"


def test_delegate_consumer_keeps_its_explicit_revision_through_source_resolution() -> None:
    text = (packaged_bundles_dir() / "tui.md").read_text(encoding="utf-8")
    plan = yaml.safe_load(text.split("---", 2)[1])
    resolver = build_source_resolver({})
    pin_mount_plan_sources(plan, resolver)
    delegate = next(tool for tool in plan["tools"] if tool["module"] == "tool-delegate")
    assert delegate["source"] == (
        f"{FOUNDATION}@{CONSUMER_REVISION}#subdirectory=modules/tool-delegate"
    )
    assert delegate["config"]["settings"]["timeout"] is None
    assert delegate["config"]["features"]["session_resume"]["enabled"] is True
    assert resolver("unrelated-foundation-module", f"{FOUNDATION}@main#subdirectory=other") == (
        f"{FOUNDATION}@{ANCHORS_COMMIT}#subdirectory=other"
    )


def test_user_delegate_source_override_remains_authoritative() -> None:
    user_source = "git+https://example.test/custom-delegate@v1"
    resolver = build_source_resolver({"sources": {"modules": {"tool-delegate": user_source}}})
    assert resolver("tool-delegate", f"{FOUNDATION}@{CONSUMER_REVISION}") == user_source
