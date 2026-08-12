#!/usr/bin/env python3
"""Validate repository-local Markdown links and anchors for XYZ-PILOT-153."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import os
from pathlib import Path
import re
import subprocess
import sys
from urllib.parse import unquote, urlsplit


EXCLUDED_PREFIXES = (
    ".agents/skills/",
    "docs/archive/",
    "docs/70-generated-qa/",
    "clients/<CUSTOMER_NAME>/",
    "tests/logs/",
)
EXCLUDED_FILES = {
    "docs/XYZ_PILOT_ISSUE_TRACKER.md",
}

INLINE_LINK_RE = re.compile(r"!?\[[^\]\n]*\]\(([^)\n]+)\)")
REF_LINK_RE = re.compile(r"^\s{0,3}\[[^\]]+\]:\s*(\S+)", re.MULTILINE)
CODE_SPAN_RE = re.compile(r"`[^`]*`")
HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s+(.+?)\s*#*\s*$")
HTML_ANCHOR_RE = re.compile(r"<a\s+[^>]*(?:id|name)=[\"']([^\"']+)[\"']", re.IGNORECASE)
HTML_TAG_RE = re.compile(r"<[^>]+>")
MD_LINK_TEXT_RE = re.compile(r"!?\[([^\]]*)\]\([^)]+\)")
LINE_FRAGMENT_RE = re.compile(r"L(\d+)(?:-L(\d+))?$")


@dataclass(frozen=True)
class Link:
    source: Path
    line: int
    target: str


def git_ls_files(pattern: str | None = None) -> list[Path]:
    cmd = ["git", "ls-files", "-z"]
    if pattern:
        cmd.append(pattern)
    out = subprocess.check_output(cmd)
    return [Path(raw.decode("utf-8")) for raw in out.split(b"\0") if raw]


def should_skip(path: Path) -> bool:
    posix = path.as_posix()
    return posix in EXCLUDED_FILES or any(posix.startswith(prefix) for prefix in EXCLUDED_PREFIXES)


def tracked_markdown_files() -> list[Path]:
    return [path for path in git_ls_files("*.md") if not should_skip(path)]


def tracked_markdown_targets() -> list[Path]:
    return git_ls_files("*.md")


def iter_non_code_lines(path: Path, strip_inline_code: bool = True) -> list[tuple[int, str]]:
    lines: list[tuple[int, str]] = []
    in_fence = False
    for lineno, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        stripped = raw_line.lstrip()
        if stripped.startswith(("```", "~~~")):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        line = CODE_SPAN_RE.sub("", raw_line) if strip_inline_code else raw_line
        lines.append((lineno, line))
    return lines


def iter_links(path: Path) -> list[Link]:
    links: list[Link] = []
    for lineno, line in iter_non_code_lines(path):
        for match in INLINE_LINK_RE.finditer(line):
            links.append(Link(path, lineno, normalize_raw_target(match.group(1))))
        for match in REF_LINK_RE.finditer(line):
            links.append(Link(path, lineno, normalize_raw_target(match.group(1))))
    return links


def normalize_raw_target(raw: str) -> str:
    target = raw.strip()
    if target.startswith("<") and target.endswith(">"):
        target = target[1:-1].strip()
    if (target.startswith('"') and target.endswith('"')) or (
        target.startswith("'") and target.endswith("'")
    ):
        target = target[1:-1].strip()
    return target


def github_slug(heading: str) -> str:
    text = MD_LINK_TEXT_RE.sub(r"\1", heading)
    text = CODE_SPAN_RE.sub(lambda m: m.group(0).strip("`"), text)
    text = HTML_TAG_RE.sub("", text)
    text = text.strip().lower()
    text = re.sub(r"[^\w\s-]", "", text, flags=re.UNICODE)
    text = re.sub(r"\s+", "-", text)
    return text.strip("-")


def anchors_for(path: Path) -> set[str]:
    text = path.read_text(encoding="utf-8")
    anchors: set[str] = set()
    anchors.update(anchor.strip().lower() for anchor in HTML_ANCHOR_RE.findall(text))

    seen: dict[str, int] = {}
    for _lineno, line in iter_non_code_lines(path, strip_inline_code=False):
        match = HEADING_RE.match(line)
        if not match:
            continue
        base = github_slug(match.group(1))
        if not base:
            continue
        count = seen.get(base, 0)
        seen[base] = count + 1
        anchors.add(base if count == 0 else f"{base}-{count}")
    return anchors


def is_external(target: str) -> bool:
    if not target or target.startswith("//"):
        return True
    parsed = urlsplit(target)
    return bool(parsed.scheme or parsed.netloc)


def resolve_target(repo: Path, source: Path, target: str) -> tuple[Path, str]:
    base, hash_mark, fragment = target.partition("#")
    base = base.split("?", 1)[0]
    fragment = unquote(fragment) if hash_mark else ""

    if base == "":
        return source, fragment
    path_text = unquote(base)
    candidate = repo / path_text.lstrip("/") if path_text.startswith("/") else source.parent / path_text
    return candidate.resolve(), fragment


def tracked_path_sets(repo: Path) -> tuple[set[Path], set[Path]]:
    files = {(repo / path).resolve() for path in git_ls_files()}
    dirs: set[Path] = {repo}
    for file_path in files:
        current = file_path.parent
        while current != repo.parent:
            dirs.add(current)
            if current == repo:
                break
            current = current.parent
    return files, dirs


def has_exact_tracked_path(
    repo: Path, target: Path, tracked_files: set[Path], tracked_dirs: set[Path]
) -> bool:
    try:
        relative = target.relative_to(repo)
    except ValueError:
        return False
    current = repo
    for part in relative.parts:
        try:
            names = {child.name for child in current.iterdir()}
        except OSError:
            return False
        if part not in names:
            return False
        current = current / part
    return target in tracked_dirs if target.is_dir() else target in tracked_files


def line_count(path: Path) -> int:
    with path.open("r", encoding="utf-8") as handle:
        return sum(1 for _ in handle)


def validate_links(github_summary: bool = False) -> int:
    repo = Path.cwd().resolve()
    markdown_files = tracked_markdown_files()
    markdown_targets = tracked_markdown_targets()
    tracked_files, tracked_dirs = tracked_path_sets(repo)
    markdown_set = {path.resolve(): path for path in markdown_targets}
    anchor_cache = {path.resolve(): anchors_for(path) for path in markdown_targets}
    errors: list[str] = []
    checked = 0
    skipped_external = 0

    for source in markdown_files:
        for link in iter_links(source):
            target = link.target
            if is_external(target):
                skipped_external += 1
                continue

            checked += 1
            target_path, fragment = resolve_target(repo, source.resolve(), target)
            if not has_exact_tracked_path(repo, target_path, tracked_files, tracked_dirs):
                errors.append(f"{link.source}:{link.line}: missing local target `{target}`")
                continue

            if not fragment:
                continue

            fragment_key = fragment.lower()
            if fragment_key.startswith("user-content-"):
                fragment_key = fragment_key.removeprefix("user-content-")

            line_match = LINE_FRAGMENT_RE.fullmatch(fragment)
            if line_match:
                start = int(line_match.group(1))
                end = int(line_match.group(2) or start)
                total = line_count(target_path)
                if start < 1 or end < start or end > total:
                    errors.append(
                        f"{link.source}:{link.line}: line fragment `{fragment}` exceeds "
                        f"{target_path.relative_to(repo)} ({total} lines)"
                    )
                continue

            if target_path.suffix.lower() != ".md":
                errors.append(
                    f"{link.source}:{link.line}: non-Markdown target `{target}` has unsupported fragment"
                )
                continue

            anchors = anchor_cache.get(target_path)
            if anchors is None and target_path in markdown_set:
                anchors = anchors_for(markdown_set[target_path])
                anchor_cache[target_path] = anchors
            if not anchors or fragment_key not in anchors:
                rel_target = target_path.relative_to(repo)
                errors.append(
                    f"{link.source}:{link.line}: missing anchor `#{fragment}` in `{rel_target}`"
                )

    if github_summary:
        output = [
            "## Markdown Link / Anchor Check",
            "",
            f"- Markdown files checked: {len(markdown_files)}",
            f"- Repository-local links checked: {checked}",
            f"- External links skipped: {skipped_external}",
            f"- Errors: {len(errors)}",
            "",
        ]
        if errors:
            output.append("### Errors")
            output.extend(f"- `{err}`" for err in errors[:100])
            if len(errors) > 100:
                output.append(f"- ... {len(errors) - 100} more error(s)")

        summary_file = os.environ.get("GITHUB_STEP_SUMMARY")
        if summary_file:
            with open(summary_file, "a", encoding="utf-8") as handle:
                handle.write("\n".join(output) + "\n")

    for err in errors:
        print(f"ERROR: {err}", file=sys.stderr)

    print(
        "markdown_links_checked="
        f"{checked} markdown_files={len(markdown_files)} external_skipped={skipped_external} "
        f"errors={len(errors)}"
    )
    return 1 if errors else 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--github-summary", action="store_true")
    args = parser.parse_args()
    return validate_links(github_summary=args.github_summary)


if __name__ == "__main__":
    raise SystemExit(main())
