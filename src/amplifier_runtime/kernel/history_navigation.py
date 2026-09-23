"""Disposable offset index for bounded, public conversation navigation.

Navigation tokens never advance the live replay cursor. Only native UI ledgers
without legacy sources or rewind markers support this first projection.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from filelock import FileLock

from ..model.redaction import scrub_text
from .persistence import SessionStore

MAX_PAGE = 100
MAX_RECORD_BYTES = 1024 * 1024
_PUBLIC_FIELDS = {"prompt_submit": "prompt", "prompt_complete": "response"}


class NavigationUnavailable(ValueError):
    """The source cannot provide this projection; ordinary replay remains available."""


class NavigationCursorExpired(ValueError):
    """Refresh the outline because its source generation changed."""


def _bounded(value: int, name: str, maximum: int, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ValueError(f"{name} must be an integer from {minimum} to {maximum}")
    return value


def _identity(path: Path) -> tuple[int, int]:
    stat = path.stat()
    return stat.st_dev, stat.st_ino


def _read_record(handle: Any) -> tuple[int, bytes]:
    offset = handle.tell()
    raw = handle.readline(MAX_RECORD_BYTES + 1)
    if len(raw) > MAX_RECORD_BYTES:
        raise NavigationUnavailable("History record exceeds the navigation size limit")
    return offset, raw


def _open_cache(database: Path) -> sqlite3.Connection:
    """Recreate corrupt derived data; source ledgers are never modified."""
    for attempt in range(2):
        descriptor = os.open(database, os.O_CREAT | os.O_RDWR, 0o600)
        os.close(descriptor)
        connection = sqlite3.connect(database)
        try:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT)"
            )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS entries (ordinal INTEGER PRIMARY KEY, event_id TEXT UNIQUE, offset INTEGER, length INTEGER, digest TEXT, kind TEXT, preview TEXT)"
            )
            return connection
        except sqlite3.DatabaseError:
            connection.close()
            if attempt:
                raise
            database.unlink(missing_ok=True)
    raise AssertionError("Cache recreation did not return")


@contextmanager
def _index(store: SessionStore, session_id: str) -> Iterator[tuple[sqlite3.Connection, Path, str]]:
    directory = store.session_dir(session_id)
    paths = store.events_read_paths(session_id)
    if len(paths) != 1 or paths[0].name != "ui-events.jsonl":
        raise NavigationUnavailable(
            "Indexed navigation requires a native UI ledger without legacy sources"
        )
    source = paths[0]
    with FileLock(directory / "history-navigation.lock", timeout=5):
        database = directory / "history-navigation.v1.sqlite3"
        connection = _open_cache(database)
        try:
            meta = dict(connection.execute("SELECT key, value FROM metadata"))
            stat = source.stat()
            identity = f"{stat.st_dev}:{stat.st_ino}"
            scanned = int(meta.get("scanned", "0"))
            changed = meta.get("mtime") != str(stat.st_mtime_ns)
            rebuild = (
                meta.get("identity") != identity
                or stat.st_size < scanned
                or (changed and stat.st_size <= int(meta.get("size", "0")))
            )
            if rebuild:
                connection.execute("DELETE FROM entries")
                meta = {}
                scanned = 0
            generation = meta.get("generation") or uuid.uuid4().hex
            ordinal = connection.execute(
                "SELECT COALESCE(MAX(ordinal), 0) FROM entries"
            ).fetchone()[0]
            with source.open("rb") as handle:
                handle.seek(scanned)
                while handle.tell() < stat.st_size:
                    offset, raw = _read_record(handle)
                    if not raw.endswith(b"\n"):
                        break  # Retry the incomplete tail on the next call.
                    scanned = handle.tell()
                    try:
                        record = json.loads(raw)
                    except (ValueError, UnicodeDecodeError):
                        continue
                    if not isinstance(record, dict):
                        continue
                    kind = record.get("kind")
                    if kind == "rewind_marker":
                        connection.rollback()
                        raise NavigationUnavailable("Rewound history requires ordinary replay")
                    field = _PUBLIC_FIELDS.get(kind) if isinstance(kind, str) else None
                    if field is None or record.get("session_id") != session_id:
                        continue
                    event_id = record.get("event_id")
                    text = record.get(field)
                    if not isinstance(event_id, str) or not event_id or not isinstance(text, str):
                        continue
                    digest = hashlib.sha256(raw).hexdigest()
                    previous = connection.execute(
                        "SELECT digest FROM entries WHERE event_id = ?", (event_id,)
                    ).fetchone()
                    if previous:
                        if previous[0] != digest:
                            raise NavigationUnavailable("Conflicting durable event identities")
                        continue
                    ordinal += 1
                    preview = " ".join(scrub_text(text).split())[:240]
                    connection.execute(
                        "INSERT INTO entries VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (ordinal, event_id, offset, len(raw), digest, kind, preview),
                    )
            # Appends after this snapshot remain for the next query. Replacement
            # during the scan must not publish offsets against another file.
            if _identity(source) != (stat.st_dev, stat.st_ino):
                raise NavigationCursorExpired("History source changed; refresh navigation")
            connection.executemany(
                "INSERT OR REPLACE INTO metadata VALUES (?, ?)",
                {
                    "identity": identity,
                    "scanned": str(scanned),
                    "size": str(stat.st_size),
                    "mtime": str(stat.st_mtime_ns),
                    "session_id": session_id,
                    "generation": generation,
                }.items(),
            )
            connection.commit()
            yield connection, source, generation
        finally:
            connection.close()


def history_outline(
    store: SessionStore,
    session_id: str,
    *,
    cursor: str | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    """Return bounded conversation labels and an opaque navigation-only cursor."""
    _bounded(limit, "limit", MAX_PAGE, minimum=1)
    with _index(store, session_id) as (index, _source, generation):
        start = 0
        if cursor is not None:
            try:
                supplied_generation, number = cursor.split(":")
                start = int(number)
            except (ValueError, AttributeError) as error:
                raise ValueError("Invalid navigation cursor") from error
            if supplied_generation != generation:
                raise NavigationCursorExpired("History changed; refresh the outline")
            total = index.execute("SELECT COUNT(*) FROM entries").fetchone()[0]
            _bounded(start, "cursor position", total)
        rows = index.execute(
            "SELECT ordinal, event_id, kind, preview FROM entries WHERE ordinal > ? ORDER BY ordinal LIMIT ?",
            (start, limit + 1),
        ).fetchall()
        selected = rows[:limit]
        return {
            "session_id": session_id,
            "generation": generation,
            "entries": [
                {"event_id": row[1], "kind": row[2], "preview": row[3]} for row in selected
            ],
            "next_cursor": f"{generation}:{selected[-1][0]}" if len(rows) > limit else None,
            "source": "native-conversation",
        }


def history_window(
    store: SessionStore,
    session_id: str,
    *,
    event_id: str,
    generation: str | None = None,
    before: int = 2,
    after: int = 2,
) -> dict[str, Any]:
    """Seek a bounded public conversation window around an existing durable ID.

    Returned records contain only identity, kind, timestamp, and the scrubbed
    user prompt or completed assistant response. Private event fields are omitted.
    """
    _bounded(before, "before", 49)
    _bounded(after, "after", 49)
    if not isinstance(event_id, str) or not event_id or len(event_id) > 1024:
        raise ValueError("event_id must be a nonempty durable ID of at most 1024 characters")
    with _index(store, session_id) as (index, source, current):
        if generation is not None and generation != current:
            raise NavigationCursorExpired("History changed; refresh the outline")
        found = index.execute(
            "SELECT ordinal FROM entries WHERE event_id = ?", (event_id,)
        ).fetchone()
        if found is None:
            raise ValueError("Event is not in the public conversation outline")
        rows = index.execute(
            "SELECT event_id, offset, length, digest FROM entries WHERE ordinal BETWEEN ? AND ? ORDER BY ordinal",
            (found[0] - before, found[0] + after),
        ).fetchall()
        records = []
        with source.open("rb") as handle:
            for identity, offset, length, digest in rows:
                handle.seek(offset)
                raw = handle.read(length)
                if hashlib.sha256(raw).hexdigest() != digest:
                    raise NavigationCursorExpired("History source changed; refresh the outline")
                record = json.loads(raw)
                field = _PUBLIC_FIELDS[record["kind"]]
                records.append(
                    {
                        "event_id": identity,
                        "session_id": session_id,
                        "kind": record["kind"],
                        "ts": record.get("ts", ""),
                        field: scrub_text(record[field]),
                    }
                )
        return {
            "session_id": session_id,
            "generation": current,
            "target_event_id": event_id,
            "records": records,
            "source": "native-conversation",
        }
