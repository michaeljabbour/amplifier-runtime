"""Explicit execution profiles survive app overlays carrying another loop."""

from pathlib import Path

import pytest
from amplifier_foundation import load_bundle

from amplifier_runtime.kernel.config import preserve_selected_orchestrator


@pytest.mark.asyncio
async def test_selected_loop_survives_app_foundation_overlay(tmp_path: Path) -> None:
    (tmp_path / "profile.yaml").write_text(
        "bundle:\n  name: profile\nsession:\n  orchestrator:\n"
        "    module: loop-fast-decisions\n    config:\n      mode: active\n"
    )
    (tmp_path / "app.yaml").write_text(
        "bundle:\n  name: app\nsession:\n  orchestrator:\n"
        "    module: loop-streaming\n    config:\n      max_iterations: 3\n"
        "tools:\n  - module: tool-app\n"
    )
    root = await load_bundle(str(tmp_path / "profile.yaml"))
    overlay = await load_bundle(str(tmp_path / "app.yaml"))
    composed = root.compose(overlay)
    assert composed.session["orchestrator"]["module"] == "loop-streaming"
    preserve_selected_orchestrator(root, composed)
    assert composed.session["orchestrator"] == {
        "module": "loop-fast-decisions",
        "config": {"mode": "active"},
    }
    assert composed.tools[0]["module"] == "tool-app"
    composed.session["orchestrator"]["config"]["mode"] = "off"
    assert root.session["orchestrator"]["config"]["mode"] == "active"


@pytest.mark.asyncio
async def test_inherited_loop_remains_replaceable(tmp_path: Path) -> None:
    base = tmp_path / "base.yaml"
    base.write_text("bundle:\n  name: base\nsession:\n  orchestrator:\n    module: base-loop\n")
    profile = tmp_path / "profile.yaml"
    profile.write_text(f"bundle:\n  name: profile\nincludes:\n  - bundle: {base}\n")
    overlay = tmp_path / "app.yaml"
    overlay.write_text("bundle:\n  name: app\nsession:\n  orchestrator:\n    module: app-loop\n")
    root = await load_bundle(str(profile))
    composed = root.compose(await load_bundle(str(overlay)))
    preserve_selected_orchestrator(root, composed)
    assert composed.session["orchestrator"]["module"] == "app-loop"


@pytest.mark.asyncio
async def test_same_loop_overlay_config_still_wins(tmp_path: Path) -> None:
    root_path = tmp_path / "root.yaml"
    root_path.write_text(
        "bundle:\n  name: root\nsession:\n  orchestrator:\n"
        "    module: same-loop\n    config:\n      max_iterations: 5\n"
    )
    overlay = tmp_path / "app.yaml"
    overlay.write_text(
        "bundle:\n  name: app\nsession:\n  orchestrator:\n"
        "    module: same-loop\n    config:\n      max_iterations: 10\n"
    )
    root = await load_bundle(str(root_path))
    composed = root.compose(await load_bundle(str(overlay)))
    preserve_selected_orchestrator(root, composed)
    assert composed.session["orchestrator"]["config"]["max_iterations"] == 10
