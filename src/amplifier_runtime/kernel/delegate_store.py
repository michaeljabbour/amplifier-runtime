"""Atomic, private child checkpoints and exclusive delegate execution ownership.

A checkpoint contains model-safe messages and the resolved child configuration.
Partial output is diagnostic and never substitutes for those messages. The
versioned child record is authoritative; normal session files are a read-only
history projection for existing clients. Resume must go through the parent.
"""

from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from datetime import UTC, datetime
import re
from typing import Any, Iterator, Literal

from filelock import FileLock, Timeout
from pydantic import BaseModel, ConfigDict, Field

from .persistence import SessionStore, _redact_secrets, _sanitize_message, _write_private_json
from .session_integrity import repair_resumed_transcript

RECORD_NAME = "delegate.v1.json"
PARTIAL_NAME = "delegate-partial.v1.json"


class DelegateRecord(BaseModel):
    """One validated, atomically replaced generation of child recovery state."""

    model_config = ConfigDict(extra="forbid", strict=True)
    version: Literal[1] = 1
    session_id: str
    parent_id: str
    agent_name: str
    project_dir: str
    config: dict[str, Any]
    overlay: dict[str, Any]
    messages: list[dict[str, Any]] = Field(default_factory=list)
    status: Literal["in_progress", "success", "incomplete", "error"] = "in_progress"
    output: str = ""
    updated_at: str = ""
    self_delegation_depth: int = 0
    execution_id: str = ""


class DelegatePartial(BaseModel):
    """Bounded diagnostics belonging to exactly one child execution."""

    model_config = ConfigDict(extra="forbid", strict=True)
    version: Literal[1] = 1
    session_id: str
    parent_id: str
    execution_id: str
    text: str = Field(max_length=16000)


class DelegateStore:
    """Use a SessionStore's project and redaction policy for durable children."""

    def __init__(self, store: SessionStore) -> None:
        self.store = store

    @contextmanager
    def claim(self, session_id: str) -> Iterator[None]:
        """Hold an OS lock for the whole child run; contention fails immediately.

        The OS releases the lock on process death. Never expire a lock based on
        wall time while its owner may still execute external tools.
        """
        directory = self.store.session_dir(session_id)
        directory.mkdir(parents=True, exist_ok=True)
        lock = FileLock(directory / "delegate-writer.lock", timeout=0)
        try:
            lock.acquire()
        except Timeout as error:
            raise RuntimeError("Delegate is already running; wait for its owner") from error
        try:
            yield
        finally:
            lock.release()

    def load(self, session_id: str, parent_id: str) -> DelegateRecord:
        """Read only this parent's child; reject legacy or corrupt checkpoints."""
        path = self.store.session_dir(session_id) / RECORD_NAME
        if not path.is_file():
            raise FileNotFoundError("Delegate has no recoverable checkpoint; start a new delegate")
        record = DelegateRecord.model_validate_json(path.read_text(encoding="utf-8"))
        if record.session_id != session_id or record.parent_id != parent_id:
            raise PermissionError("Delegate does not belong to this parent session")
        _, repair = repair_resumed_transcript(record.messages)
        if repair:
            raise ValueError("Delegate checkpoint contains incomplete tool calls; cannot resume")
        if record.status == "in_progress" and not self.is_running(session_id):
            record.status = "incomplete"
        if record.status != "success" and record.execution_id:
            try:
                partial = DelegatePartial.model_validate_json(
                    (path.parent / PARTIAL_NAME).read_text(encoding="utf-8")
                )
            except (OSError, ValueError):
                # Diagnostic corruption cannot invalidate a balanced checkpoint.
                partial = None
            if partial is not None and (
                partial.session_id == record.session_id
                and partial.parent_id == record.parent_id
                and partial.execution_id == record.execution_id
            ):
                record.output = partial.text
        return record

    def save_partial(self, record: DelegateRecord) -> None:
        """Write only bounded diagnostics; never rewrite context or its projection."""
        partial = DelegatePartial(
            session_id=record.session_id,
            parent_id=record.parent_id,
            execution_id=record.execution_id,
            text=record.output[-16000:],
        )
        _write_private_json(
            self.store.session_dir(record.session_id) / PARTIAL_NAME,
            _redact_secrets(partial.model_dump()),
        )

    def is_running(self, session_id: str) -> bool:
        """Probe the execution lock without expiring a live owner's claim."""
        lock = FileLock(self.store.session_dir(session_id) / "delegate-writer.lock", timeout=0)
        try:
            lock.acquire()
        except Timeout:
            return True
        lock.release()
        return False

    def save(self, record: DelegateRecord) -> None:
        """Publish one coherent private checkpoint, then its history projection."""
        record.updated_at = datetime.now(UTC).isoformat()
        clean = _redact_secrets(record.model_dump())
        directory = self.store.session_dir(record.session_id)
        _write_private_json(directory / RECORD_NAME, clean)
        self.store.save(
            record.session_id,
            record.messages,
            {
                "session_id": record.session_id,
                "parent_id": record.parent_id,
                "agent_name": record.agent_name,
                "project_dir": record.project_dir,
                "delegate_record": RECORD_NAME,
                "status": record.status,
                "last_updated": record.updated_at,
            },
        )

    async def checkpoint(self, record: DelegateRecord, context: Any) -> bool:
        """Persist balanced context during execution, never during cancellation."""
        messages = [_sanitize_message(m) for m in await context.get_messages()]
        _, repair = repair_resumed_transcript(messages)
        if repair:
            return False
        record.messages = messages
        self.save(record)
        return True


def _module_sources(config: dict[str, Any]) -> dict[str, set[str | None]]:
    """Collect active module sources by the ID used by Foundation's resolver."""
    sources: dict[str, set[str | None]] = {}
    entries: list[Any] = []
    for kind in ("providers", "tools", "hooks"):
        entries.extend(config.get(kind, []))
    session = config.get("session", {})
    for kind in ("context", "orchestrator"):
        entry = session.get(kind)
        if entry:
            entries.append(entry)
    for entry in entries:
        if isinstance(entry, str):
            module, source = entry, None
        else:
            module, source = entry.get("module"), entry.get("source")
        if module:
            sources.setdefault(module, set()).add(source)
    return sources


def _validate_module_sources(config: dict[str, Any], parent_config: dict[str, Any]) -> None:
    """Refuse conflicting resolver IDs and unpinned child-only module sources.

    A module already activated in the parent resolves by ID, ignoring saved
    source hints. Child-only sources need an immutable Git revision for lazy
    activation. Actual resolver-cache provenance remains the loader's concern.
    """
    current = _module_sources(parent_config)
    for module, sources in _module_sources(config).items():
        if len(sources) != 1:
            raise ValueError("Delegate module composition changed; conflicting sources for one ID")
        if module in current:
            if sources != current[module]:
                raise ValueError("Delegate module composition changed; start a new delegate")
        else:
            source = next(iter(sources))
            if not isinstance(source, str) or not re.fullmatch(
                r"git\+(?:https|ssh)://[^\s]+@[0-9a-f]{40}(?:#[^\s]*)?", source
            ):
                raise ValueError(
                    "Delegate child-only module needs an immutable source; start a new delegate"
                )


def _contains_redaction(value: Any) -> bool:
    """Whether configuration still contains a persisted secret placeholder."""
    if isinstance(value, str):
        return "[REDACTED" in value
    if isinstance(value, dict):
        return any(_contains_redaction(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_redaction(item) for item in value)
    return False


def refresh_provider_secrets(
    config: dict[str, Any], parent_config: dict[str, Any]
) -> dict[str, Any]:
    """Restore only redacted provider secrets, keeping persisted routing intact.

    Identity and source must still match the live parent's provider. Removing
    or replacing a provider requires a new delegation, not implicit migration.
    """
    from amplifier_core.utils.truncate import SENSITIVE_KEYS

    _validate_module_sources(config, parent_config)
    result = deepcopy(config)
    current = {
        (p.get("module"), p.get("instance_id") or p.get("id") or p.get("module")): p
        for p in parent_config.get("providers", [])
    }

    def restore(saved: Any, live: Any) -> Any:
        if isinstance(saved, dict):
            output = dict(saved)
            for key, value in saved.items():
                if key.lower() in SENSITIVE_KEYS:
                    if not isinstance(live, dict) or key not in live or live[key] == "[REDACTED]":
                        raise ValueError(
                            "Delegate provider credentials are unavailable; configure its provider"
                        )
                    output[key] = deepcopy(live[key])
                else:
                    output[key] = restore(value, live.get(key) if isinstance(live, dict) else None)
            return output
        if isinstance(saved, list):
            return [
                restore(v, live[i] if isinstance(live, list) and i < len(live) else None)
                for i, v in enumerate(saved)
            ]
        return saved

    for index, provider in enumerate(result.get("providers", [])):
        identity = (
            provider.get("module"),
            provider.get("instance_id") or provider.get("id") or provider.get("module"),
        )
        live = current.get(identity)
        if live is None or live.get("source") != provider.get("source"):
            raise ValueError("Delegate provider composition changed; start a new delegate")
        result["providers"][index] = restore(provider, live)
    if _contains_redaction(result):
        raise ValueError(
            "Delegate configuration has unresolved redacted secrets; start a new delegate"
        )
    return result
