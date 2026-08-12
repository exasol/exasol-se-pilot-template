#!/usr/bin/env python3
"""XYZ-PILOT-336 — split the tracker Full register (per-issue, verify-every-step).

For each ticket that is Status **Resolved** AND resolved more than CUTOFF_DAYS ago,
one issue at a time ("heart surgery"):
  1. COPY & PASTE its full detail section VERBATIM into
     docs/XYZ_PILOT_ISSUE_TRACKER_ARCHIVE.md (nothing re-written).
  2. CONFIRM the exact content landed in the archive (verbatim substring + anchor).
  3. DELETE it from the primary, leaving a redirect STUB whose header (anchor +
     heading + metadata bullets) is COPIED verbatim from the original.
  4. Run the FULL tracker verifier policy (verify_tracker_completeness.sh).
     If it fails, SELF-HEAL (revert this one issue in both files) and abort — so
     docs/XYZ_PILOT_ISSUE_TRACKER.md is healthy at every single step.

Non-Resolved tickets (Open / Workaround / backlog / Wontfix) and recently-resolved
(<= CUTOFF_DAYS) stay in the primary with full detail. Idempotent + re-runnable.

Usage:
  python3 scripts/split_tracker_archive.py            # dry-run (report only)
  python3 scripts/split_tracker_archive.py --apply    # per-issue verified surgery
"""
from __future__ import annotations
import argparse, datetime, re, subprocess, sys
from pathlib import Path

PRIMARY = Path("docs/XYZ_PILOT_ISSUE_TRACKER.md")
ARCHIVE = Path("docs/XYZ_PILOT_ISSUE_TRACKER_ARCHIVE.md")
VERIFIER = ["./scripts/verify_tracker_completeness.sh"]
CUTOFF_DAYS = 30
TODAY = datetime.date(2026, 7, 16)
CUTOFF = TODAY - datetime.timedelta(days=CUTOFF_DAYS)
STUB_MARKER = "— full detail moved to"

ANCHOR_RE = re.compile(r'(?m)^<a id="xyz-pilot-(\d+)"></a>\s*$')
STATUS_RE = re.compile(r'\*\*Status:\*\*\s*(.+)')
RESOLVED_RE = re.compile(r'Resolved\s+(\d{4})-(\d{2})-(\d{2})')

ARCHIVE_HEADER = (
    "# XYZ Pilot — Issue Tracker Archive (Full-register overflow)\n\n"
    "Detail sections for tickets **Resolved more than 30 days** are relocated here from "
    "[XYZ_PILOT_ISSUE_TRACKER.md](XYZ_PILOT_ISSUE_TRACKER.md) (XYZ-PILOT-336) so the primary tracker stays "
    "under GitHub's markdown rich-render size ceiling. Anchors are preserved: each archived ticket keeps "
    "a redirect stub in the primary, so `#xyz-pilot-NNN` deep links one-hop here. Generated verbatim "
    "(copy & paste, no re-write) by `scripts/split_tracker_archive.py`; re-run to refresh. The primary "
    "tracker remains the single source of truth for status changes.\n\n---\n\n"
    "## Archived register (newest-first)\n\n"
)


def status_of(text: str) -> str:
    for line in text.splitlines()[:8]:
        m = STATUS_RE.search(line)
        if m:
            return m.group(1).strip()
    return ""


def is_move(text: str) -> bool:
    m = RESOLVED_RE.search(status_of(text))
    if not m:
        return False
    return datetime.date(int(m.group(1)), int(m.group(2)), int(m.group(3))) < CUTOFF


def make_stub(rid: str, text: str) -> str:
    """Verbatim header copy (anchor+heading+metadata bullets, up to first '#### ') + redirect.
    rid is the raw zero-padded anchor id (e.g. '099'), used verbatim so anchors never drift."""
    a = f"xyz-pilot-{rid}"
    lines = text.splitlines()
    cut = next((i for i, l in enumerate(lines) if l.startswith("#### ")), len(lines))
    header = "\n".join(lines[:cut]).rstrip()
    return f"{header}\n\n_Archived {TODAY} (Resolved > {CUTOFF_DAYS} days) — full detail moved to [{ARCHIVE.name}#{a}]({ARCHIVE.name}#{a})_\n\n"


def backlink(rid: str) -> str:
    return f"\n\n_[← back to primary tracker]({PRIMARY.name}#xyz-pilot-{rid})_\n"


def run_verifier() -> tuple[int, str]:
    r = subprocess.run(VERIFIER, capture_output=True, text=True)
    return r.returncode, (r.stdout + r.stderr).strip()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    src = PRIMARY.read_text(encoding="utf-8")
    matches = list(ANCHOR_RE.finditer(src))
    preamble = src[: matches[0].start()]
    parts = [preamble]
    ids = []
    raw_ids = []
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(src)
        parts.append(src[m.start():end])
        ids.append(int(m.group(1)))
        raw_ids.append(m.group(1))

    move_k = [k for k in range(1, len(parts)) if STUB_MARKER not in parts[k] and is_move(parts[k])]
    print(f"cutoff (Resolved before): {CUTOFF} | sections: {len(ids)} | to MOVE: {len(move_k)}")
    if not args.apply:
        print("DRY-RUN — pass --apply for the per-issue verified surgery")
        return 0

    rc, out = run_verifier()
    if rc != 0:
        print(f"ABORT: incoming tracker not clean:\n{out}")
        return 2
    print("pre-flight verifier: clean")
    ARCHIVE.write_text(ARCHIVE_HEADER, encoding="utf-8")

    done = 0
    for k in move_k:
        nid = ids[k - 1]
        rid = raw_ids[k - 1]
        full = parts[k]
        # 1+2. COPY & PASTE verbatim to archive, CONFIRM landed
        arch_before = ARCHIVE.read_text(encoding="utf-8")
        ARCHIVE.write_text(arch_before + full.rstrip() + backlink(rid) + "\n", encoding="utf-8")
        arch_after = ARCHIVE.read_text(encoding="utf-8")
        if full.rstrip() not in arch_after or f'<a id="xyz-pilot-{rid}"></a>' not in arch_after:
            print(f"ABORT at XYZ-PILOT-{rid}: archive copy not confirmed; reverting")
            ARCHIVE.write_text(arch_before, encoding="utf-8")
            return 3
        # 3. DELETE from primary (verbatim-header stub)
        parts[k] = make_stub(rid, full)
        PRIMARY.write_text("".join(parts), encoding="utf-8")
        # 4. FULL verifier policy — tracker must stay healthy
        rc, out = run_verifier()
        if rc != 0:
            print(f"ABORT at XYZ-PILOT-{nid}: verifier FAILED — self-healing this issue:\n{out}")
            parts[k] = full
            PRIMARY.write_text("".join(parts), encoding="utf-8")
            ARCHIVE.write_text(arch_before, encoding="utf-8")
            rc2, _ = run_verifier()
            print(f"self-heal complete; tracker healthy again: {rc2 == 0} (moved {done} before abort)")
            return 4
        done += 1
        if done % 25 == 0 or done == len(move_k):
            print(f"  ✓ moved {done}/{len(move_k)} (last XYZ-PILOT-{nid}); verifier clean")

    print(f"DONE — moved {done} sections, verifier clean at every step.")
    print(f"primary now {PRIMARY.stat().st_size/1048576:.2f} MiB | archive {ARCHIVE.stat().st_size/1048576:.2f} MiB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
