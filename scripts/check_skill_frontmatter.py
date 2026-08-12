#!/usr/bin/env python3
"""Validate local agent skill frontmatter."""

from __future__ import annotations

from pathlib import Path
import sys


ROOT = Path(".agents/skills")


def parse_frontmatter(path: Path) -> tuple[dict[str, str], str | None]:
    lines = path.read_text(encoding="utf-8").splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, "missing opening YAML frontmatter delimiter"
    try:
        end = lines[1:].index("---") + 1
    except ValueError:
        return {}, "missing closing YAML frontmatter delimiter"

    data: dict[str, str] = {}
    current_key: str | None = None
    for line in lines[1:end]:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if line[:1].isspace():
            if current_key:
                data[current_key] = f"{data[current_key]} {line.strip()}".strip()
                continue
            return data, f"unexpected frontmatter continuation line: {line!r}"
        if ":" not in line:
            return data, f"invalid frontmatter line: {line!r}"
        key, value = line.split(":", 1)
        current_key = key.strip()
        data[current_key] = value.strip().strip('"').strip("'")
    return data, None


def main() -> int:
    files = sorted(ROOT.glob("**/SKILL.md"))
    errors: list[str] = []

    for path in files:
        data, err = parse_frontmatter(path)
        if err:
            errors.append(f"{path}: {err}")
            continue
        for key in ("name", "description"):
            if not data.get(key):
                errors.append(f"{path}: missing non-empty {key!r} in YAML frontmatter")

    if errors:
        for err in errors:
            print(f"ERROR: {err}", file=sys.stderr)
        print(f"skill_files={len(files)} invalid={len(errors)}")
        return 1

    print(f"skill frontmatter OK - {len(files)} SKILL.md files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
