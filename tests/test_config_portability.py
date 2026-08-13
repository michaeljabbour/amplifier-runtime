"""Cross-client compatibility gates for runtime configuration discovery."""

from __future__ import annotations

from pathlib import Path

from amplifier_runtime.kernel.config import _foundation_load_source, discover_bundle


def test_bundle_discovery_preserves_the_existing_local_path_api(tmp_path: Path) -> None:
    bundle = tmp_path / "local.md"
    bundle.write_text("---\nbundle:\n  name: local\n---\n", encoding="utf-8")

    assert discover_bundle("local", [tmp_path]) == str(bundle)
    assert discover_bundle(str(bundle), []) == str(bundle)


def test_only_the_windows_foundation_load_boundary_receives_a_file_uri() -> None:
    windows_path = r"C:\Users\example\project\.amplifier\bundles\local.md"

    assert _foundation_load_source(windows_path, platform="nt") == (
        "file://C:/Users/example/project/.amplifier/bundles/local.md"
    )
    assert _foundation_load_source("/project/local.md", platform="posix") == "/project/local.md"
    assert _foundation_load_source("git+https://example.test/bundle@abc", platform="nt") == (
        "git+https://example.test/bundle@abc"
    )
