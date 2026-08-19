"""A pasted image must not be re-serialized into the transcript forever.

Session `eec9ae98` destroyed itself over two pasted screenshots. The estimator
bug that turned them into a 235-pass compaction spiral is fixed elsewhere, but
the *enabler* lives here: `ClipboardImageInjector` writes the multimodal message
into the stored context, and `_save_transcript` serializes the base64 straight
into `transcript.jsonl`.

Measured on real sessions on this machine before the fix -- 7 of 5,323
transcripts carry `"type": "base64"`, and the worst is not close:

    transcript.jsonl   3,113,952 base64 chars in ONE message   (4.9 MB file)
    metadata           {"source": "tui-clipboard", "attachment_count": 1}

`source: "tui-clipboard"` is the exact stamp `build_image_message` writes, so
that is a causal fingerprint rather than a coincidence. Two costs the token
estimator fix does nothing about:

* `_write_with_backup` keeps a `.backup` copy, so it is ~10 MB on disk per
  screenshot, not 5.
* `IncrementalSaver` fires on every `tool:post`, so that file is fully
  re-serialized, rewritten and fsynced hundreds of times in a session.

Zero tests touched this path before this file. That is the condition that let a
21-hour session destroy itself, and it is the thing to fix first: whatever
policy is chosen, the bound it accepts should be written down where the next
person can see it.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any

from amplifier_runtime.kernel.persistence import TRANSCRIPT_FILENAME, SessionStore

SESSION_ID = "session-with-a-pasted-screenshot"
# Roughly a full-screen PNG. The incident's image decoded to ~2.23 MB.
IMAGE_BYTES = b"\x89PNG\r\n\x1a\n" + b"\xa5" * (2 * 1024 * 1024)
MAX_STORED_IMAGE_CHARS = 4096
"""What one image is allowed to cost the transcript, in characters.

A reference plus its metadata is a few hundred characters. This is deliberately
loose enough not to be brittle and tight enough that inline base64 -- which is
4/3 of the raw bytes, so ~2.8 million characters here -- cannot pass.
"""


def _image_message(count: int = 1) -> dict[str, Any]:
    encoded = base64.b64encode(IMAGE_BYTES).decode("ascii")
    content: list[dict[str, Any]] = [{"type": "text", "text": "what is wrong here?"}]
    content.extend(
        {
            "type": "image",
            "source": {"type": "base64", "media_type": "image/png", "data": encoded},
        }
        for _ in range(count)
    )
    return {
        "role": "user",
        "content": content,
        "metadata": {"source": "tui-clipboard", "attachment_count": count},
    }


def _transcript_text(store: SessionStore, session_id: str) -> str:
    return (store.session_dir(session_id) / TRANSCRIPT_FILENAME).read_text("utf-8")


def test_a_pasted_image_does_not_land_in_the_transcript_verbatim(tmp_path: Path) -> None:
    """The bound this repo accepts, written down where it can be checked."""
    store = SessionStore(tmp_path)
    store.save(SESSION_ID, [_image_message()], {"session_id": SESSION_ID})

    text = _transcript_text(store, SESSION_ID)

    assert len(text) < MAX_STORED_IMAGE_CHARS, (
        f"transcript.jsonl is {len(text):,} characters for a single "
        f"{len(IMAGE_BYTES):,}-byte image -- and it is rewritten on every "
        f"tool:post, with a .backup copy alongside"
    )


def test_the_round_trip_is_byte_identical(tmp_path: Path) -> None:
    """Externalizing must be invisible above the persistence sink.

    The injector, the orchestrator, the estimator, rewind and the provider all
    read the in-memory shape. If a save/load cycle changes it, this stops being
    a storage change and becomes a behaviour change.
    """
    store = SessionStore(tmp_path)
    original = _image_message()
    store.save(SESSION_ID, [original], {"session_id": SESSION_ID})

    loaded, _metadata = store.load(SESSION_ID)

    assert loaded == [original]


def test_several_images_in_one_message_all_survive(tmp_path: Path) -> None:
    store = SessionStore(tmp_path)
    original = _image_message(count=3)
    store.save(SESSION_ID, [original], {"session_id": SESSION_ID})

    loaded, _metadata = store.load(SESSION_ID)

    assert loaded == [original]
    assert len(_transcript_text(store, SESSION_ID)) < MAX_STORED_IMAGE_CHARS


def test_identical_images_are_stored_once(tmp_path: Path) -> None:
    """Content addressing: the same screenshot pasted twice costs one copy."""
    store = SessionStore(tmp_path)
    store.save(
        SESSION_ID,
        [_image_message(), _image_message()],
        {"session_id": SESSION_ID},
    )

    blobs = list((store.session_dir(SESSION_ID) / "blobs").glob("*"))
    assert len(blobs) == 1, f"expected one stored blob, found {len(blobs)}"


def test_a_transcript_written_before_this_change_still_loads(tmp_path: Path) -> None:
    """Migration is 'do nothing': inline base64 must keep working on read.

    Every transcript already on disk contains inline images. Rehydration has to
    be a no-op for them, or this change orphans existing sessions.
    """
    store = SessionStore(tmp_path)
    session_dir = store.session_dir(SESSION_ID)
    session_dir.mkdir(parents=True, exist_ok=True)
    legacy = _image_message()
    (session_dir / TRANSCRIPT_FILENAME).write_text(
        json.dumps(legacy, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    loaded, _metadata = store.load(SESSION_ID)

    assert loaded == [legacy]


def test_ordinary_messages_are_untouched(tmp_path: Path) -> None:
    """Nothing about a text conversation should change shape."""
    store = SessionStore(tmp_path)
    plain = [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": [{"type": "text", "text": "hi"}]},
    ]
    store.save(SESSION_ID, plain, {"session_id": SESSION_ID})

    loaded, _metadata = store.load(SESSION_ID)

    assert loaded == plain
    assert not (store.session_dir(SESSION_ID) / "blobs").exists()
