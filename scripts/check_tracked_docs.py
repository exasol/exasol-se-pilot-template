#!/usr/bin/env python3
"""Verify daily-status TRACKED_DOCS covers the curated documentation set."""

from __future__ import annotations

import ast
from pathlib import Path
import sys


SOURCE = Path("scheduler/daily_status.py")
COMPONENT_READMES = [
    "scheduler/README.md",
    "pilot_mcp_stack/README.md",
    "services/chatbot/README.md",
    "services/streamlit-insights/README.md",
    "scripts/exasol_warmup/README.md",
    "tests/README.md",
]


def load_tracked_docs() -> list[str]:
    module = ast.parse(SOURCE.read_text(encoding="utf-8"))
    for node in module.body:
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id == "TRACKED_DOCS":
                value = ast.literal_eval(node.value)
                if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
                    raise ValueError("TRACKED_DOCS must be a list of strings")
                return value
    raise ValueError("TRACKED_DOCS assignment not found")


def main() -> int:
    tracked = load_tracked_docs()
    tracked_set = set(tracked)
    top_docs = sorted(str(path) for path in Path("docs").glob("*.md"))
    expected = ["README.md", *top_docs, *COMPONENT_READMES]

    missing = [path for path in expected if path not in tracked_set]
    extra = [path for path in tracked if path not in expected]
    missing_files = [path for path in expected if not Path(path).is_file()]

    for label, paths in (
        ("missing_expected_paths", missing),
        ("extra_curated_paths", extra),
        ("expected_paths_not_on_disk", missing_files),
    ):
        if paths:
            print(f"ERROR: {label}:")
            for path in paths:
                print(f"  - {path}")

    print(f"tracked_count={len(tracked)}")
    print(f"top_level_docs_count={len(top_docs)}")
    print(f"expected_curated_count={len(expected)}")

    if missing or extra or missing_files:
        return 1

    print("daily-status TRACKED_DOCS coverage OK")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
