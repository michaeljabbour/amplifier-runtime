import json
from pathlib import Path

from click.testing import CliRunner

from amplifier_runtime.cli import main


def test_version_identifies_neutral_runtime() -> None:
    result = CliRunner().invoke(main, ["--version"])

    assert result.exit_code == 0
    assert result.output.startswith("amplifier-runtime, version ")


def test_serve_help_exposes_session_host_controls() -> None:
    result = CliRunner().invoke(main, ["serve", "--help"])

    assert result.exit_code == 0
    for option in ("--resume", "--attach", "--actor", "--actor-kind", "--attachable"):
        assert option in result.output


def test_model_and_provider_overrides_are_atomic() -> None:
    result = CliRunner().invoke(main, ["serve", "--model", "example"])

    assert result.exit_code == 2
    assert "--model and --provider must be supplied together" in result.output


def test_provider_status_is_machine_readable(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("AMPLIFIER_HOME", str(tmp_path / "amplifier-home"))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    result = CliRunner().invoke(main, ["provider", "status", "--format", "json"])

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["configured"] is False
    assert "remediation" in payload


def test_provider_add_reads_secret_from_stdin_and_never_echoes_it(
    monkeypatch, tmp_path: Path
) -> None:
    home = tmp_path / "amplifier-home"
    monkeypatch.setenv("AMPLIFIER_HOME", str(home))
    monkeypatch.chdir(tmp_path)

    result = CliRunner().invoke(
        main,
        ["provider", "add", "openai", "--api-key-stdin", "--yes", "--model", "gpt-test"],
        input="secret-provider-key\n",
    )

    assert result.exit_code == 0, result.output
    assert "secret-provider-key" not in result.output
    assert "secret-provider-key" in (home / "keys.env").read_text(encoding="utf-8")
    settings = (home / "settings.yaml").read_text(encoding="utf-8")
    assert "provider-openai" in settings
    assert "gpt-test" in settings


def test_settings_and_config_paths_match_studio_contract(monkeypatch, tmp_path: Path) -> None:
    home = tmp_path / "amplifier-home"
    monkeypatch.setenv("AMPLIFIER_HOME", str(home))
    monkeypatch.chdir(tmp_path)

    section = CliRunner().invoke(main, ["settings", "get", "behavior"])
    paths = CliRunner().invoke(main, ["config", "paths", "--json"])

    assert section.exit_code == 0, section.output
    assert "context.max_tokens =" in section.output
    assert "tui.preflight.verify_live =" in section.output
    assert paths.exit_code == 0, paths.output
    payload = json.loads(paths.output)
    assert payload["schema"] == "amplifier-runtime/config-paths/v1"
    assert payload["keys"] == str(home / "keys.env")


def test_settings_write_round_trip(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("AMPLIFIER_HOME", str(tmp_path / "amplifier-home"))
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()

    written = runner.invoke(main, ["settings", "set", "tui.pricing.live", "false", "--global"])
    read = runner.invoke(main, ["settings", "get", "tui.pricing.live"])
    removed = runner.invoke(main, ["settings", "unset", "tui.pricing.live", "--global"])

    assert written.exit_code == 0, written.output
    assert read.exit_code == 0, read.output
    assert read.output.splitlines()[0] == "false"
    assert removed.exit_code == 0, removed.output


def test_installers_target_the_runtime_and_verify_serve_contract() -> None:
    root = Path(__file__).resolve().parents[1]
    shell = (root / "scripts/install.sh").read_text(encoding="utf-8")
    powershell = (root / "scripts/install.ps1").read_text(encoding="utf-8")

    for script in (shell, powershell):
        assert "michaeljabbour/amplifier-runtime" in script
        assert "amplifier-runtime" in script
        assert "serve --help" in script
        assert "provider status" in script
        assert "amplifier-app-tui" not in script
