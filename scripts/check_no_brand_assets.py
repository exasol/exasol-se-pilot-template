#!/usr/bin/env python3
"""Fail when Exasol brand assets are tracked in this repository.

This repository is public. The official letterhead, the logo extracts and the
brand slide template are internal Exasol material: they live in an internal
repository and are installed at runtime into an ignored path by
scripts/install_brand_skill.sh. Committing one here would publish it.

The check is name-based on purpose. It is a guard against the accident - a
stray `git add`, a skill copied into a tracked folder - not against someone
determined to rename a file first.

    python3 scripts/check_no_brand_assets.py
    python3 scripts/check_no_brand_assets.py --self-test
"""

from __future__ import annotations

import re
import subprocess
import sys

# Matched against the repository-relative path of every tracked file.
FORBIDDEN = [
    (re.compile(r"(^|/)letterhead-header\.(jpg|jpeg|png)$", re.I),
     "official page-header lock-up"),
    (re.compile(r"(^|/)Exasol-?Letterhead[^/]*\.(docx|dotx|pdf)$", re.I),
     "official Word letterhead"),
    (re.compile(r"(^|/)Exasol_Brand_Template[^/]*\.(pptx|potx)$", re.I),
     "official brand slide template"),
    (re.compile(r"(^|/)(wordmark|x-mark)-[a-z]+\.(png|svg|jpg)$", re.I),
     "wordmark or X-mark extract"),
    (re.compile(r"(^|/)\.agents/skills/exasol-branded-document(/|$)", re.I),
     "branded-document skill in the tracked skill path"),
]


def offenders(paths: list[str]) -> list[tuple[str, str]]:
    hits = []
    for path in paths:
        for pattern, what in FORBIDDEN:
            if pattern.search(path):
                hits.append((path, what))
                break
    return hits


def tracked_files() -> list[str]:
    out = subprocess.run(
        ["git", "ls-files", "-z"], capture_output=True, text=True, check=True
    ).stdout
    return [p for p in out.split("\0") if p]


def self_test() -> None:
    bad = [
        "assets/letterhead-header.jpg",
        "x/Exasol-Letterhead_Dec-2025.docx",
        "assets/Exasol_Brand_Template_2026.pptx",
        "assets/wordmark-navy.png",
        "assets/x-mark-duotone.png",
        ".agents/skills/exasol-branded-document/SKILL.md",
    ]
    good = [
        "scripts/install_brand_skill.sh",
        "docs/letterhead-policy.md",
        ".claude/skills/exasol-branded-document/SKILL.md",
        "services/streamlit-insights/app.py",
    ]
    assert len(offenders(bad)) == len(bad), offenders(bad)
    assert offenders(good) == [], offenders(good)
    print("PASS: brand-asset matcher")


def main() -> int:
    if "--self-test" in sys.argv:
        self_test()
        return 0

    hits = offenders(tracked_files())
    if not hits:
        print("OK: no Exasol brand assets are tracked in this repository.")
        return 0

    print("FAIL: Exasol brand assets are tracked in this public repository.\n")
    for path, what in hits:
        print(f"  {path}\n      {what}")
    print(
        "\nRemove them with 'git rm --cached <path>' and install the skill at "
        "runtime instead:\n    ./scripts/install_brand_skill.sh\n"
        "The skill belongs in .claude/skills/, which .gitignore excludes."
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
