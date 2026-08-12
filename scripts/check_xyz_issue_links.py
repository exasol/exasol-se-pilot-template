#!/usr/bin/env python3
"""Check Markdown links to XYZ_PILOT_ISSUE_TRACKER anchors."""

from __future__ import annotations

from pathlib import Path
import re
import subprocess
import sys


TRACKER = Path("docs/XYZ_PILOT_ISSUE_TRACKER.md")
LINK_RE = re.compile(
    r"\[XYZ-PILOT-(\d{3})\]\((?:(?:\.\./)+)?(?:docs/)?XYZ_PILOT_ISSUE_TRACKER\.md#XYZ-PILOT-(\d{3})\)"
)


def tracked_markdown_files() -> list[Path]:
    out = subprocess.check_output(["git", "ls-files", "-z", "*.md"])
    paths = []
    for raw_b in out.split(b"\0"):
        if not raw_b:
            continue
        raw = raw_b.decode("utf-8")
        if raw.startswith(("docs/archive/", "docs/70-generated-qa/qa/", "docs/70-generated-qa/architecture/")):
            continue
        paths.append(Path(raw))
    return paths


def main() -> int:
    tracker_text = TRACKER.read_text(encoding="utf-8")
    anchors = set(re.findall(r'<a id="XYZ-PILOT-(\d{3})"></a>', tracker_text))
    errors: list[str] = []
    checked = 0

    for path in tracked_markdown_files():
        text = path.read_text(encoding="utf-8")
        for lineno, line in enumerate(text.splitlines(), start=1):
            for match in LINK_RE.finditer(line):
                label_id, anchor_id = match.groups()
                checked += 1
                if label_id != anchor_id:
                    errors.append(
                        f"{path}:{lineno}: link label XYZ-PILOT-{label_id} points to XYZ-PILOT-{anchor_id}"
                    )
                if anchor_id not in anchors:
                    errors.append(
                        f"{path}:{lineno}: link points to missing tracker anchor XYZ-PILOT-{anchor_id}"
                    )

    if errors:
        for err in errors:
            print(f"ERROR: {err}", file=sys.stderr)
        print(f"pilot_issue_links_checked={checked} errors={len(errors)}")
        return 1

    print(f"<CUSTOMER_NAME> issue tracker links OK - {checked} links checked")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
