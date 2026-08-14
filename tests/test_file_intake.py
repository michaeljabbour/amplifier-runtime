"""Local path parsing and OS-native file selection."""

from __future__ import annotations

import shlex
import subprocess

import pytest

from amplifier_runtime.kernel import native_picker
from amplifier_runtime.kernel.clipboard import pasted_local_file_paths


def test_pasted_local_document_paths_preserve_multiple_files(tmp_path) -> None:
    first = tmp_path / "design brief.md"
    second = tmp_path / "requirements.pdf"
    first.write_text("brief", encoding="utf-8")
    second.write_bytes(b"pdf")
    dropped = " ".join(shlex.quote(str(path)) for path in (first, second))

    assert pasted_local_file_paths(dropped) == (first.resolve(), second.resolve())


def test_macos_picker_returns_existing_regular_files(tmp_path, monkeypatch) -> None:
    first = tmp_path / "notes one.md"
    second = tmp_path / "screen.png"
    first.write_text("notes", encoding="utf-8")
    second.write_bytes(b"png")
    payload = str(first).encode() + b"\0" + str(second).encode() + b"\n"
    monkeypatch.setattr(native_picker.sys, "platform", "darwin")
    monkeypatch.setattr(
        native_picker.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 0, payload, b""),
    )

    assert native_picker.pick_local_files() == (first.resolve(), second.resolve())


def test_macos_picker_cancellation_is_empty(monkeypatch) -> None:
    monkeypatch.setattr(native_picker.sys, "platform", "darwin")
    monkeypatch.setattr(
        native_picker.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args[0], 1, b"", b"execution error: User canceled. (-128)\n"
        ),
    )

    assert native_picker.pick_local_files() == ()


def test_picker_reports_unsupported_platform(monkeypatch) -> None:
    monkeypatch.setattr(native_picker.sys, "platform", "linux")
    with pytest.raises(native_picker.NativePickerUnavailable, match="macOS"):
        native_picker.pick_local_files()
