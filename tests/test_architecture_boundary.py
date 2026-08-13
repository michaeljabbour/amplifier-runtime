from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).parents[1]
SOURCE = ROOT / "src" / "amplifier_runtime"
FORBIDDEN_ROOTS = {"textual", "prompt_toolkit", "rich", "tauri", "solid_js"}


def test_runtime_has_no_client_framework_imports() -> None:
    violations: list[str] = []
    for path in SOURCE.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                names.append(node.module)
            for name in names:
                if name.split(".", 1)[0] in FORBIDDEN_ROOTS:
                    violations.append(
                        f"{path.relative_to(ROOT)}:{getattr(node, 'lineno', 0)}: {name}"
                    )

    assert not violations, "client framework imports crossed into runtime:\n" + "\n".join(
        violations
    )


def test_runtime_source_does_not_import_tui_package() -> None:
    violations: list[str] = []
    for path in SOURCE.rglob("*.py"):
        if "amplifier_app_tui" in path.read_text(encoding="utf-8"):
            violations.append(str(path.relative_to(ROOT)))

    assert not violations, "runtime still imports the TUI package: " + ", ".join(violations)
