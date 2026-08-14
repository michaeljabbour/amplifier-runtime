"""OS-native local-file selection for terminal clients."""

from __future__ import annotations

import subprocess  # nosec B404 -- fixed /usr/bin/osascript invocation
import sys
from pathlib import Path

from .clipboard import MAX_CLIPBOARD_ATTACHMENTS

_MACOS_PICK_FILES_SCRIPT = r"""
set pickedFiles to choose file with prompt "Attach files to Amplifier" with multiple selections allowed
set pickedPaths to {}
repeat with pickedFile in pickedFiles
    set end of pickedPaths to POSIX path of pickedFile
end repeat
set AppleScript's text item delimiters to ASCII character 0
return pickedPaths as text
""".strip()


class NativePickerUnavailable(RuntimeError):
    """The current OS has no implemented native picker."""


def pick_local_files(*, limit: int = MAX_CLIPBOARD_ATTACHMENTS) -> tuple[Path, ...]:
    """Open the OS file picker and return validated local regular files.

    Cancellation is an ordinary empty selection. A NUL-delimited result keeps
    spaces, quotes, and newlines in filenames unambiguous.
    """
    if limit <= 0:
        raise ValueError("limit must be positive")
    if sys.platform != "darwin":
        raise NativePickerUnavailable("native file picker is currently available on macOS")

    result = subprocess.run(  # noqa: S603 -- argv is fixed; no shell
        ["/usr/bin/osascript", "-e", _MACOS_PICK_FILES_SCRIPT],
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        if b"-128" in result.stderr or b"User canceled" in result.stderr:
            return ()
        raise NativePickerUnavailable("macOS file picker could not be opened")

    payload = result.stdout.removesuffix(b"\n")
    if not payload:
        return ()
    paths: list[Path] = []
    for raw_path in payload.split(b"\0"):
        if not raw_path:
            continue
        try:
            path = Path(raw_path.decode("utf-8")).expanduser().resolve(strict=True)
        except (OSError, UnicodeError):
            continue
        if path.is_file():
            paths.append(path)
        if len(paths) >= limit:
            break
    return tuple(paths)


__all__ = ["NativePickerUnavailable", "pick_local_files"]
