#!/usr/bin/env python3
"""Read-only source comparison; do not infer active implementations from copies.

Use the retained audit snapshots for other surfaces and two explicit Git checkouts
for the kernel matrix. No package, source repository or user data is modified.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess


SURFACES = {
    "amplifier-runtime": (
        [
            "src/amplifier_runtime/kernel/events.py",
            "src/amplifier_runtime/kernel/serve.py",
            "src/amplifier_runtime/kernel/jsonl.py",
        ],
        "Canonical session/event/control owner; no UI rendering dependencies.",
    ),
    "amplifier-app-tui": (
        [
            "src/amplifier_app_tui/kernel/__init__.py",
            "src/amplifier_app_tui/model/__init__.py",
            "src/amplifier_app_tui/ui/runtime_adapter.py",
        ],
        "Textual presentation plus compatibility imports; local kernel/model body copies are excluded by their __path__ shims.",
    ),
    "amplifier-app-tui-rust": (
        [
            "src/ui/runtime_adapter.rs",
            "src/ui/composer.rs",
            "src/ui/approval_bar.rs",
            "PARITY.md",
            "PERFORMANCE.md",
        ],
        "Retain the Rust interaction/performance experiment and wire-client differences; no automatic parity claim.",
    ),
    "amplifier-app-studio": (
        [
            "src/protocol.ts",
            "src/transport.ts",
            "src/reducer.test.ts",
            "src/components/Inspector.tsx",
            "src/components/TerminalWorkSurface.tsx",
        ],
        "Rich desktop/mobile inspection, attachments and replay/reconnect behavior; preserve surface-specific tests.",
    ),
    "amplifier-app-cli": (
        ["amplifier_app_cli/approval_provider.py", "amplifier_app_cli/session_runner.py"],
        "Existing CLI/Foundation integration; do not assume every path already consumes the neutral runtime.",
    ),
    "opencode-amplifier": (
        ["src/runtime/client.ts", "src/runtime/contracts.ts", "src/plugin/hooks.ts"],
        "Host integration adapter; share runtime protocol while retaining host lifecycle/presentation.",
    ),
    "amplifier-new-ui": (
        ["bundle.md", "context/ui-instructions.md"],
        "Instruction-only bundle in the captured tree, not an implemented UI renderer. Retain its source/learning separately.",
    ),
    "visualcodex": (
        ["src/main/main.ts", "src/renderer/components/InputArea.tsx", "src/shared/types/index.ts"],
        "Independent desktop interaction experiment; compare approval/composer and process boundaries before harvesting.",
    ),
    "workspaces-spa": (
        ["spa/chat_state.js", "spa/markdown-worker.js", "spa/markdown.js", "api/provider.py"],
        "Lean streaming/chat/Markdown-worker experiment; its provider path is not assumed runtime-compatible.",
    ),
}


def git(root: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()


def kernel_tree(root: Path, prefix: str) -> dict[str, str]:
    result = {}
    for line in git(root, "ls-tree", "-r", "HEAD", "--", prefix).splitlines():
        meta, path = line.split("\t", 1)
        if path.endswith(".py"):
            result[path.removeprefix(prefix + "/")] = meta.split()[2]
    return result


def snapshot(source_dir: Path, name: str) -> tuple[str, set[str]]:
    value = json.loads((source_dir / (name + ".json")).read_text())
    tree = value.get("tree", {})
    if isinstance(tree, dict) and "content" in tree:
        tree = json.loads(tree["content"])
    entries = tree.get("entries", tree.get("tree", [])) if isinstance(tree, dict) else tree
    revision = (
        value.get("commit_sha")
        or (tree.get("sha") if isinstance(tree, dict) else None)
        or value.get("tree_sha")
    )
    if not revision:
        raise ValueError("Missing source revision: " + name)
    return revision, {item["path"] for item in entries}


def compare(runtime: Path, tui: Path, source_dir: Path) -> dict:
    left = kernel_tree(runtime, "src/amplifier_runtime/kernel")
    right = kernel_tree(tui, "src/amplifier_app_tui/kernel")
    shared = sorted(set(left) & set(right))
    rows = [
        {
            "path": path,
            "runtime_blob": left[path],
            "tui_blob": right[path],
            "byte_identical": left[path] == right[path],
            "tui_execution_role": "active compatibility shim"
            if path == "__init__.py"
            else "inert local body under the configured runtime-only package path",
            "owner": "amplifier-runtime; the TUI retains its compatibility shim",
        }
        for path in shared
    ]
    surfaces = []
    for name, (paths, finding) in SURFACES.items():
        revision, known = snapshot(source_dir, name)
        if missing := set(paths) - known:
            raise ValueError(name + ": source paths not captured: " + str(sorted(missing)))
        surfaces.append(
            {
                "repository": name,
                "source_revision": revision,
                "exact_paths": paths,
                "finding": finding,
                "sources": [
                    f"https://github.com/michaeljabbour/{name}/blob/{revision}/{path}"
                    for path in paths
                ],
                "test_scope": "See the conformance report; existence of tests is not an executed parity result.",
            }
        )
    return {
        "schema_version": "client-boundary-source-review/v1",
        "date": "2026-10-28",
        "scope": "Nine explicitly listed session surfaces/adapters/experiments; not every visual learning artifact or a complete consumer inventory.",
        "runtime_revision": git(runtime, "rev-parse", "HEAD"),
        "tui_revision": git(tui, "rev-parse", "HEAD"),
        "kernel_paths_compared": len(shared),
        "identical_blob_pairs": sum(x["byte_identical"] for x in rows),
        "divergent_blob_pairs": sum(not x["byte_identical"] for x in rows),
        "correction": "Blob duplication is physical source retention, not evidence of two active kernels. The TUI kernel/model package paths already delegate to the separately installed runtime, with no fallback.",
        "path_matrix": rows,
        "surface_reviews": surfaces,
        "retirement_boundary": "No original body or repository removed. Disposable no-body import/behavior tests improve retirement readiness; a supported consumer/package migration and full acceptance still govern an actual removal.",
        "preserved_independent_foundations": "Foundation/Core and modular data/storage owners remain separate; this work does not fold a storage foundation into a skill or UI.",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", required=True, type=Path)
    parser.add_argument("--tui", required=True, type=Path)
    parser.add_argument("--sources", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    result = compare(args.runtime, args.tui, args.sources)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(
        json.dumps(
            {
                "surfaces": len(result["surface_reviews"]),
                "kernel_paths": result["kernel_paths_compared"],
                "identical": result["identical_blob_pairs"],
                "divergent": result["divergent_blob_pairs"],
            }
        )
    )


if __name__ == "__main__":
    main()
