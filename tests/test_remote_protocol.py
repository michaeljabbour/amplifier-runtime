from __future__ import annotations

import base64
import os
from pathlib import Path
from types import SimpleNamespace

from amplifier_runtime.kernel.persistence import SessionStore
from amplifier_runtime.kernel.serve import (
    OP_PERMISSIONS,
    _artifact_read_record,
    _runtime_capabilities_record,
    _settings_apply_record,
    _settings_get_record,
    _settings_schema_record,
)


def _runtime(project_dir: Path) -> SimpleNamespace:
    return SimpleNamespace(project_dir=project_dir)


def test_capabilities_are_derived_from_the_audited_operation_registry() -> None:
    record = _runtime_capabilities_record()

    assert record["type"] == "runtime.capabilities"
    assert record["protocol"] == {
        "name": "amplifier-runtime-jsonl",
        "version": 1,
        "minimum": 1,
        "maximum": 1,
    }
    assert set(record["operations"]) == set(OP_PERMISSIONS)
    assert record["operations"]["artifact.read"]["permission"] == "read"
    assert record["operations"]["settings.apply"]["permission"] == "write"


def test_artifact_read_is_chunked_and_cannot_escape_the_project(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    artifact = project / "report.txt"
    artifact.write_bytes(b"remote evidence")
    outside = tmp_path / "secret.txt"
    outside.write_text("not yours", encoding="utf-8")

    chunk = _artifact_read_record(_runtime(project), {"path": "report.txt", "limit": 6})
    escaped = _artifact_read_record(_runtime(project), {"path": str(outside)})

    assert chunk["ok"] is True
    assert base64.b64decode(chunk["data"]) == b"remote"
    assert chunk["offset"] == 0
    assert chunk["eof"] is False
    assert escaped["ok"] is False
    assert "outside the session project" in escaped["error"]


def test_remote_settings_are_redacted_and_credentials_are_host_only(
    monkeypatch, tmp_path: Path
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    home = tmp_path / "amplifier-home"
    monkeypatch.setenv("AMPLIFIER_HOME", str(home))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "never-on-the-wire")
    runtime = _runtime(project)

    schema = _settings_schema_record(runtime)
    before = _settings_get_record(runtime, {"path": "providers.anthropic.api_key"})
    refused = _settings_apply_record(
        runtime,
        {
            "changes": [
                {
                    "path": "providers.anthropic.api_key",
                    "action": "set",
                    "value": "also-secret",
                    "scope": "global",
                }
            ]
        },
    )
    applied = _settings_apply_record(
        runtime,
        {
            "changes": [
                {
                    "path": "tui.pricing.live",
                    "action": "set",
                    "value": "false",
                    "scope": "global",
                }
            ]
        },
    )

    secret_field = next(
        field for field in schema["fields"] if field["path"] == "providers.anthropic.api_key"
    )
    assert secret_field["default"] is None
    assert secret_field["remote_writable"] is False
    assert before["values"][0]["display"] == "configured"
    assert "never-on-the-wire" not in str(before)
    assert refused["ok"] is False
    assert "runtime host" in refused["results"][0]["message"]
    assert applied["ok"] is True
    assert applied["current_session_changed"] is False
    assert "live: false" in (home / "settings.yaml").read_text(encoding="utf-8")


def test_session_store_honors_relocatable_amplifier_home(monkeypatch, tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    home = tmp_path / "durable-volume" / "amplifier"
    monkeypatch.setenv("AMPLIFIER_HOME", str(home))

    store = SessionStore(project_dir=project)

    assert store.base_dir.is_relative_to(home)
    assert store.base_dir.parent.name == "sessions" or store.base_dir.name == "sessions"
    assert str(store.base_dir).startswith(str(home))
    assert os.environ["AMPLIFIER_HOME"] == str(home)
