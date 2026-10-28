"""Export the existing normalized data-channel contract for client conformance.

This module adds no session, normalization, permission or UI implementation.
Serve control messages (approval tickets, leases and history boundaries) remain
owned by kernel.serve and are deliberately distinguished from data records.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from pydantic import TypeAdapter

from .kernel.jsonl import JSONL_SCHEMA_VERSION, JsonlRecord

_DATA_ADAPTER = TypeAdapter(JsonlRecord)


def data_schema() -> dict[str, Any]:
    """Generate directly from the runtime-owned models; never copy event fields."""
    schema = _DATA_ADAPTER.json_schema()
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["$id"] = "urn:amplifier:runtime-data:v1"
    return schema


def validate_data_record(record: dict[str, Any]) -> Any:
    """Validate a normalized data record; reject control/raw-hook substitutions.

    This is a consumer boundary, not permission enforcement. Stream and durable
    events retain their distinct types. Approval replies still go to the runtime
    broker/control plane; a visible approval event does not grant authority.
    """
    version = record.get("schema_version")
    if type(version) is not int or version != JSONL_SCHEMA_VERSION:
        raise ValueError("Unsupported runtime data schema_version")
    # JSON arrays are the wire representation of tuple fields such as approval
    # options. Strict Python validation would incorrectly reject that valid wire.
    return _DATA_ADAPTER.validate_json(json.dumps(record, allow_nan=False), strict=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", type=Path, help="Write a generated schema snapshot")
    parser.add_argument("--check", type=Path, help="Check a snapshot against current models")
    args = parser.parse_args()
    rendered = json.dumps(data_schema(), indent=2, sort_keys=True) + "\n"
    if args.check:
        if args.check.read_text() != rendered:
            parser.exit(2, "Runtime data schema snapshot differs from the source models\n")
    elif args.write:
        args.write.parent.mkdir(parents=True, exist_ok=True)
        args.write.write_text(rendered)
    else:
        print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
