from __future__ import annotations

import asyncio
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

from amplifier_runtime.kernel import setup


def test_editable_provider_is_importable_in_the_installing_process(
    tmp_path: Path, monkeypatch
) -> None:
    """A new editable .pth is not processed until a second Python process."""
    module_name = "amplifier_module_provider_cold_repair"
    (tmp_path / f"{module_name}.py").write_text(
        "class ColdRepairProvider:\n    pass\n", encoding="utf-8"
    )
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda command, **kwargs: SimpleNamespace(returncode=0, stdout="", stderr=""),
    )
    sys.modules.pop(module_name, None)
    try:
        ok, detail = asyncio.run(
            setup.install_provider_module("provider-cold-repair", str(tmp_path))
        )
        assert ok, detail
        assert str(tmp_path) in sys.path
    finally:
        sys.modules.pop(module_name, None)
        while str(tmp_path) in sys.path:
            sys.path.remove(str(tmp_path))
