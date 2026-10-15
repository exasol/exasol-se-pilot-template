#!/usr/bin/env bash
# XYZ-PILOT-157 sub-task (d) — XYZ_MASTER candidate-filing entry point.
#
# Drain the candidate queue under tests/logs/runs/<run-id>/<source>/issue-candidates.md
# files following CLAUDE.md Rule #5 single-writer + one-ticket-per-invocation
# discipline (every invocation files exactly ONE XYZ-PILOT-NNN mutation).
#
# Operates in three modes:
#
#   tests/scripts/run_xyz_master_filing.sh <run-id>
#   tests/scripts/run_xyz_master_filing.sh <run-id> --list
#       List all candidates discovered under the run folder (default mode).
#
#   tests/scripts/run_xyz_master_filing.sh <run-id> --prompt N
#       Emit a paste-ready subagent prompt for candidate #N (1-based) to
#       stdout. Pre-flight: git fetch origin main, git pull --ff-only,
#       verifier exit 0 against current local main, archive_tracker.sh
#       snapshot. Exits non-zero on any pre-flight failure with a clear
#       message — DOES NOT print the prompt if pre-flight fails.
#
#   tests/scripts/run_xyz_master_filing.sh <run-id> --all-prompts
#       Emit prompts for every candidate, separated by `=== PROMPT N ===`
#       markers. One pre-flight pass for the batch.
#
# Operator workflow:
#   1. Run with --list to confirm the candidate count + numbering.
#   2. For each candidate: run with --prompt N, paste output into Claude
#      Code's Agent tool (or anthropic-api wrapper) as a `general-purpose`
#      subagent prompt. The subagent performs the full archive + verify +
#      edit + verify + diff sequence for ONE ticket, then stop before
#      stage/commit/push until an operator explicitly approves continuation.
#   3. After each subagent run, re-run --list to confirm the candidate
#      count went down (or to spot races introduced by parallel writers).
#      Candidates already carrying `candidate-outcomes/candidate-NNN.*.md`
#      markers are displayed as decided and `--prompt N` refuses to re-drain
#      them.
#
# The script itself never mutates docs/XYZ_PILOT_ISSUE_TRACKER.md — only the
# subagent does, in line with the single-writer gatekeeper directive. The
# script's pre-flight steps (fetch + pull + verifier + archive) are read +
# snapshot only; safe to re-run.
#
# Exit codes:
#   0 — listed / prompted successfully.
#   1 — bad usage / no candidates found / pre-flight verifier failed.
#   2 — git fetch or pull failed (network or non-ff state).

set -uo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "$script_dir/../.." && pwd)"

if [ $# -lt 1 ]; then
  sed -n '2,38p' "$0" >&2
  exit 1
fi

run_id="$1"
shift
mode="--list"
prompt_n=""

while [ $# -gt 0 ]; do
  case "$1" in
    --list)
      mode="--list" ;;
    --prompt)
      mode="--prompt"
      prompt_n="${2:-}"
      [ -z "$prompt_n" ] && { echo "ERROR: --prompt requires an integer index" >&2; exit 1; }
      shift ;;
    --all-prompts)
      mode="--all-prompts" ;;
    -h|--help)
      sed -n '2,38p' "$0"
      exit 0 ;;
    *)
      echo "ERROR: unknown argument '$1'" >&2
      exit 1 ;;
  esac
  shift
done

run_dir="$repo_root/tests/logs/runs/$run_id"
if [ ! -d "$run_dir" ]; then
  echo "ERROR: run folder not found: $run_dir" >&2
  exit 1
fi

skip_preflight=0
if [ "$mode" = "--prompt" ] || [ "$mode" = "--all-prompts" ]; then
  python3 - "$run_dir" "$mode" "$prompt_n" <<'PY'
import re
import sys
from pathlib import Path

run_dir = Path(sys.argv[1])
mode = sys.argv[2]
prompt_n = sys.argv[3]
heading_re = re.compile(r"^##\s+Candidate\b", re.MULTILINE)
actionable_re = re.compile(
    r"^-\s+\*\*(?:"
    r"Agent|Run|Type|Severity|Proposed severity|Recommended severity|"
    r"Area|Proposed area|Recommended area|Observed|Expected|Evidence|"
    r"Existing ID|Existing ticket|Tracker ID|Requested status|Recommended status|"
    r"Proposed status|Status"
    r"):\*\*",
    re.MULTILINE,
)

def has_actionable_candidate_fields(body: str) -> bool:
    return bool(
        actionable_re.search(body)
        or "### Top-table row" in body
        or "### Full-register detail section" in body
    )

def normalize_candidate_title(raw: str, fallback: str) -> str:
    """Return a tracker-ready title from a candidate heading suffix.

    Headings often use operational numbering such as
    `## Candidate 001 — QBR filters...`. The number is useful inside a
    multi-candidate source file, but it should not become part of the public
    tracker summary or detail heading.
    """
    title = raw.strip()
    title = re.sub(r"^[—–\-:]+\s*", "", title)
    title = re.sub(r"^\d{1,3}\s*[—–\-:]+\s*", "", title)
    title = title.strip(" —–-:").strip()
    return title or fallback

def candidate_count(src: str) -> int:
    matches = list(heading_re.finditer(src))
    if matches:
        return len(matches)
    sep = src.find("\n---\n")
    body = src[sep + len("\n---\n"):].strip() if sep != -1 else src.strip()
    return 1 if has_actionable_candidate_fields(body) else 0

states: list[tuple[int, list[Path]]] = []
global_idx = 0
for child in sorted(run_dir.iterdir()):
    if not child.is_dir():
        continue
    fp = child / "issue-candidates.md"
    if not fp.is_file():
        continue
    src = fp.read_text(errors="replace")
    count = candidate_count(src)
    for local_idx in range(1, count + 1):
        global_idx += 1
        out_dir = fp.parent / "candidate-outcomes"
        markers = sorted(out_dir.glob(f"candidate-{local_idx:03d}.*.md")) if out_dir.is_dir() else []
        states.append((global_idx, markers))

if mode == "--prompt":
    try:
        wanted = int(prompt_n)
    except ValueError:
        print(f"ERROR: --prompt argument must be an integer, got {prompt_n!r}", file=sys.stderr)
        sys.exit(1)
    if wanted < 1 or wanted > len(states):
        print(f"ERROR: --prompt {wanted} out of range (1..{len(states)})", file=sys.stderr)
        sys.exit(1)
    markers = states[wanted - 1][1]
    if markers:
        marker_text = ", ".join(str(m) for m in markers)
        print(f"ERROR: candidate {wanted} already has outcome marker(s): {marker_text}", file=sys.stderr)
        sys.exit(3)
    sys.exit(0)

if mode == "--all-prompts" and states and all(markers for _, markers in states):
    sys.exit(3)
sys.exit(0)
PY
  rc=$?
  if [ "$rc" -eq 3 ]; then
    skip_preflight=1
  elif [ "$rc" -ne 0 ]; then
    exit "$rc"
  fi
fi

# Pre-flight only when we actually plan to print prompts. --list is read-only
# discovery, no git mutation, safe to run any time.
if [ "$mode" != "--list" ] && [ "$skip_preflight" -ne 1 ]; then
  cd "$repo_root"

  # Git hygiene: fetch + pull. Refuse if non-ff (means local has un-pushed
  # work and the operator needs to deal with it before XYZ_MASTER files).
  if ! git fetch origin main >/dev/null 2>&1; then
    echo "ERROR: git fetch origin main failed" >&2
    exit 2
  fi
  if ! git pull --ff-only origin main >/dev/null 2>&1; then
    echo "ERROR: git pull --ff-only failed — local main is not fast-forward of origin/main; resolve manually before filing" >&2
    exit 2
  fi

  # Pre-edit verifier — incoming state must be clean.
  if ! ./scripts/verify_tracker_completeness.sh >/dev/null 2>&1; then
    echo "ERROR: pre-edit verifier failed against current docs/XYZ_PILOT_ISSUE_TRACKER.md — investigate before any filing." >&2
    ./scripts/verify_tracker_completeness.sh >&2
    exit 1
  fi

  # Snapshot the tracker. Reason string ties the archive to this run-id +
  # mode so multiple parallel filing passes don't shadow each other.
  reason="xyz-master-filing-$(echo "$run_id" | tr 'A-Z:' 'a-z-')"
  ./scripts/archive_tracker.sh "$reason" >/dev/null 2>&1 \
    || { echo "ERROR: archive_tracker.sh failed" >&2; exit 1; }
fi

python3 - "$run_dir" "$run_id" "$mode" "$prompt_n" "$repo_root" <<'PY'
import json, os, re, sys
from pathlib import Path

run_dir = Path(sys.argv[1])
run_id = sys.argv[2]
mode = sys.argv[3]
prompt_n_arg = sys.argv[4]
repo_root = Path(sys.argv[5])

# Discover candidate files: tests/logs/runs/<run-id>/<source>/issue-candidates.md
candidate_files: list[tuple[str, Path]] = []
for child in sorted(run_dir.iterdir()):
    if not child.is_dir():
        continue
    f = child / "issue-candidates.md"
    if f.is_file():
        candidate_files.append((child.name, f))

if not candidate_files:
    print(f"ERROR: no issue-candidates.md found under {run_dir}/*/ — nothing to file.", file=sys.stderr)
    sys.exit(1)

# Parse each candidate file into individual `## Candidate ...` blocks.
# A file may contain one or many candidates. Both are valid.
RE_CANDIDATE_HEADING = re.compile(r"^##\s+Candidate\b(.*)$", re.MULTILINE)
RE_ACTIONABLE_FIELD = re.compile(
    r"^-\s+\*\*(?:"
    r"Agent|Run|Type|Severity|Proposed severity|Recommended severity|"
    r"Area|Proposed area|Recommended area|Observed|Expected|Evidence|"
    r"Existing ID|Existing ticket|Tracker ID|Requested status|Recommended status|"
    r"Proposed status|Status"
    r"):\*\*",
    re.MULTILINE,
)

def has_actionable_candidate_fields(body: str) -> bool:
    return bool(
        RE_ACTIONABLE_FIELD.search(body)
        or "### Top-table row" in body
        or "### Full-register detail section" in body
    )

def normalize_candidate_title(raw: str, fallback: str) -> str:
    """Return a tracker-ready title from a candidate heading suffix.

    Headings often use operational numbering such as
    `## Candidate 001 — QBR filters...`. The number is useful inside a
    multi-candidate source file, but it should not become part of the public
    tracker summary or detail heading.
    """
    title = raw.strip()
    title = re.sub(r"^[—–\-:]+\s*", "", title)
    title = re.sub(r"^\d{1,3}\s*[—–\-:]+\s*", "", title)
    title = title.strip(" —–-:").strip()
    return title or fallback

def outcome_markers(file_path: Path, local_idx: int) -> list[Path]:
    out_dir = file_path.parent / "candidate-outcomes"
    if not out_dir.is_dir():
        return []
    return sorted(out_dir.glob(f"candidate-{local_idx:03d}.*.md"))


class Candidate:
    def __init__(self, idx: int, local_idx: int, source: str, file_path: Path, title: str, body: str, file_preamble: str):
        self.idx = idx              # 1-based global index across all sources
        self.local_idx = local_idx  # 1-based index within this source candidate file
        self.source = source        # e.g. "CLAUDE_CODE", "XYZ_PILOT_CHATBOT"
        self.file_path = file_path  # absolute path to the issue-candidates.md
        self.title = title          # text after `## Candidate`
        self.body = body            # the candidate body markdown
        self.file_preamble = file_preamble  # the file's top-of-file context (header + metadata bullets)
        self.outcomes = outcome_markers(file_path, local_idx)

candidates: list[Candidate] = []
global_idx = 0
for source, fp in candidate_files:
    src = fp.read_text()
    heading_matches = list(RE_CANDIDATE_HEADING.finditer(src))
    if not heading_matches:
        # Single-candidate file with no explicit `## Candidate` heading — treat
        # the body after the first `---` separator as one candidate only when it
        # contains actual filing fields. Empty agent placeholders are ignored.
        sep = src.find("\n---\n")
        if sep == -1:
            # Whole file is the candidate. Use the file's first heading as title.
            first_h_m = re.search(r"^#\s+(.+?)$", src, re.MULTILINE)
            title = normalize_candidate_title(
                first_h_m.group(1) if first_h_m else "",
                f"(implicit candidate from {fp.name})",
            )
            file_preamble = ""
            body = src.strip()
        else:
            preamble = src[:sep]
            body = src[sep + len("\n---\n"):].strip()
            first_h_m = re.search(r"^#\s+(.+?)$", preamble, re.MULTILINE)
            title = normalize_candidate_title(
                first_h_m.group(1) if first_h_m else "",
                f"(implicit candidate from {fp.name})",
            )
            file_preamble = preamble
        if not has_actionable_candidate_fields(body):
            continue
        global_idx += 1
        candidates.append(Candidate(global_idx, 1, source, fp, title, body, file_preamble))
        continue

    # Multi-candidate file: file_preamble = everything before the first `## Candidate`.
    file_preamble = src[: heading_matches[0].start()]
    for i, m in enumerate(heading_matches):
        title = normalize_candidate_title(m.group(1), f"(candidate {i+1})")
        body_start = m.end()
        body_end = heading_matches[i + 1].start() if i + 1 < len(heading_matches) else len(src)
        body = src[body_start:body_end].strip()
        global_idx += 1
        candidates.append(Candidate(global_idx, i + 1, source, fp, title, body, file_preamble))

if mode == "--list":
    print(f"Candidates under tests/logs/runs/{run_id}/  ({len(candidates)} total)")
    decided = sum(1 for c in candidates if c.outcomes)
    pending = len(candidates) - decided
    print(f"Pending: {pending} · decided: {decided}")
    print()
    for c in candidates:
        rel = c.file_path.relative_to(repo_root)
        state = "DECIDED" if c.outcomes else "PENDING"
        print(f"  [{c.idx}] {state:<7} {c.source:<20} {c.title}")
        print(f"        {rel}")
        if c.outcomes:
            for marker in c.outcomes:
                print(f"        outcome: {marker.relative_to(repo_root)}")
    print()
    print(f"Run with `--prompt N` (1..{len(candidates)}) to emit a paste-ready subagent prompt for one candidate.")
    print("Candidates with outcome markers are already decided; `--prompt N` refuses them.")
    print(f"Pre-flight (git fetch/pull + verifier + archive_tracker.sh) runs only when emitting prompts.")
    sys.exit(0)


PROMPT_TEMPLATE = """You are being invoked as **XYZ_MASTER**, the single authorised writer to `docs/XYZ_PILOT_ISSUE_TRACKER.md` in the XYZ Pilot repository at `{repo_root}`. Before you do anything, READ ALL THREE of these:

1. `tests/skills/XYZ_MASTER.md` — the skill profile. **Pay special attention** to the "One-ticket-per-invocation (2026-05-29 directive)" section in the Operating Lens.
2. `tests/agents/xyz_master/run.sh` — the existing harness for reference.
3. `CLAUDE.md` Rule #5 in full — every sub-bullet is binding. Also read the "Parallel local-session / dirty-worktree guardrail (2026-05-29)" paragraph between Rules #4 and #5.

This prompt was generated by `tests/scripts/run_xyz_master_filing.sh` on the operator's request. The mechanical pre-flight (git fetch + git pull --ff-only + pre-edit verifier + archive_tracker.sh snapshot) has already been done by the script before you read this prompt; you can verify with `git log -1` and by listing `docs/archive/XYZ_PILOT_ISSUE_TRACKER_*` for the most recent snapshot.

## Your single task this invocation

**File exactly ONE `XYZ-PILOT-NNN` mutation** based on the candidate below. Per the one-ticket-per-invocation discipline, do NOT touch any other ticket in this pass. Do NOT consolidate, batch, or piggy-back any other change. If the candidate body asks for more than one mutation, file the FIRST one only and STOP — the operator will invoke you again for the second.

## Candidate source

- **Run ID:** `{run_id}`
- **Source agent:** `{source}`
- **Candidate file:** `{file_rel}`
- **Candidate index in this run:** {idx} of {total}
- **Candidate index in source file:** {local_idx}
- **Candidate title:** {title}
- **Outcome marker directory:** `{outcome_dir_rel}`

## Candidate file preamble (context)

{preamble_block}

## Candidate body (authoritative content — apply this verbatim where it gives markdown)

{body}

## Required sequence (do NOT skip)

a) **Pre-action git hygiene re-check:** `git status --short --branch` + `git diff --name-only`. If you find any uncommitted change to `docs/XYZ_PILOT_ISSUE_TRACKER.md` or `docs/archive/`, STOP and report — the script's pre-flight snapshot was clean and any drift means another writer raced in.

b) **Pre-edit verifier re-check:** `./scripts/verify_tracker_completeness.sh`. MUST exit 0. If it fails, STOP and report exactly which checks failed.

c) **Edit `docs/XYZ_PILOT_ISSUE_TRACKER.md`** — apply ONLY the mutations specified in the candidate body. Common shapes:

   - **New ticket**: add a new top-table row at the top of the Active issues table (descending by ID); add a new `### XYZ-PILOT-NNN` detail section at the top of the Full register (newest first), with `<a id="xyz-pilot-NNN"></a>` anchor + standard four-bullet header (`Status` / `Severity` / `Area` / `Surfaced`). For the ID: discover the current max via `rg -o 'XYZ-PILOT-[0-9]{{3}}' docs/XYZ_PILOT_ISSUE_TRACKER.md | sort -u | tail -1` and assign max+1.

   - **Status transition (e.g. → Resolved)**: update the top-table row to strike-through (ID, Severity, Area cells) + append ` (Resolved YYYY-MM-DD)` suffix to the Summary cell; update the detail section's `**Status:**` line; append a `#### Resolution` (or appropriate) subsection at the END of the detail body, plain prose only (Rule #5 `(G)` ban on strike markup in Full register).

   - **Detail-section append (e.g. Phase 2 shipped note)**: update the `**Status:**` line if the phase changes the overall ticket state; append the new subsection at the END of the detail body.

   - **Last-refresh header line at top of tracker**: rewrite to mention the new mutation if non-trivial.

d) **Post-edit verifier:** `./scripts/verify_tracker_completeness.sh`. MUST exit 0. The verifier exercises checks `(A)..(I)` including the new `(I)` duplicate `####` sub-heading detection — if your edit accidentally produces a duplicate it will fire.

e) **Git diff sanity:** `git diff --stat docs/XYZ_PILOT_ISSUE_TRACKER.md` — ONE file, modest insertion count. If the diff touches anything else, STOP and report.

f) **Prepare candidate outcome marker (after post-edit verifier, before staging):** use `scripts/write_candidate_outcome.py` to create exactly one marker under the source folder's `candidate-outcomes/` directory. For accepted tracker filings, use:

```
python3 scripts/write_candidate_outcome.py \
  --candidate-file {file_rel} \
  --candidate-index {local_idx} \
  --outcome accepted \
  --classification <minor|major|critical|feature|external|hygiene|status> \
  --tracker-id XYZ-PILOT-NNN \
  --tracker-status "<Open|Resolved YYYY-MM-DD|...>" \
  --commit same-commit \
  --title "<short title>"
```

For denied duplicate / wontfix candidates, do NOT edit the tracker; create a denied marker instead, for example `--outcome denied --denial-reason duplicate --duplicate-of XYZ-PILOT-XYZ`, then stop at the approval gate with only the marker path proposed for staging.

g) **Operator approval gate — STOP HERE before staging.** Do NOT run `git add`, `git commit`, or `git push` yet. Report:
   - candidate source and intended XYZ-PILOT-NNN mutation;
   - archive file path created by the wrapper;
   - candidate outcome marker path;
   - pre-edit and post-edit verifier results;
   - `git diff --stat docs/XYZ_PILOT_ISSUE_TRACKER.md`;
   - exact paths you intend to stage.

   Wait for explicit approval from Thomas D. or another operator before continuing.

h) **Only after explicit operator approval: Stage + commit (explicit paths only, NO `git add .` / `git add -A` / `git commit -a`):** stage ONLY `docs/XYZ_PILOT_ISSUE_TRACKER.md`, the NEW archive file under `docs/archive/`, and the NEW candidate outcome marker under `candidate-outcomes/`. Commit message format (use a here-doc):

```
XYZ_MASTER: <one-line description of the mutation>

Consolidates {source} candidate handoff from
{file_rel} (candidate {idx} of {total}, "{title}").

<one or two paragraphs of context — what changed and why, drawn from
the candidate body's evidence + validation sections>

One-ticket-per-invocation discipline (CLAUDE.md Rule #5 +
tests/skills/XYZ_MASTER.md): this pass files XYZ-PILOT-NNN only.

Co-Authored-By: Claude Opus 4.7 (1M context) <<OPERATOR_EMAIL>>
```

i) **Push:** `git push origin main`. Capture push output. If push hits non-fast-forward, run `git rebase --autostash origin/main`, re-verify, then push again. NEVER use `--force` / `--no-verify` / `--amend`.

j) **Final report back (≤ 200 words):**
   - XYZ-PILOT-NNN assigned or transitioned + new status
   - candidate outcome marker path
   - archive file path (already snapshotted by the script — confirm it's in your commit)
   - pre-edit verifier counts + post-edit verifier counts
   - diff summary (`git diff --stat`)
   - commit SHA + push result
   - **explicit confirmation:** "filed exactly ONE XYZ-PILOT-NNN mutation this pass"
   - any concurrent-writer artefacts encountered + judgement calls

## Critical constraints

- Use `AWS_PROFILE=exasol` (or `AWS_PROFILE=default` — both bind to account `<SOURCE_AWS_ACCOUNT_ID>`) for any `aws` invocation against XYZ Pilot resources (none expected for tracker filing). `AWS_PROFILE=<SOURCE_AWS_PROFILE>` is obsolete — the legacy source account `<AWS_ACCOUNT_ID>` was decommissioned 2026-06-30 and points at an empty account.
- Never print any secret.
- Touch ONLY `docs/XYZ_PILOT_ISSUE_TRACKER.md`, the new archive file, and the candidate outcome marker. Nothing else.
- Forbidden: `--no-verify`, `--amend`, `--force`, `-i`, `git add .`, `git add -A`, `git commit -a`.
- If anything looks wrong at any step, ABORT and report.

Go."""


def render(c: Candidate, total: int) -> str:
    file_rel = c.file_path.relative_to(repo_root).as_posix()
    preamble_block = c.file_preamble.strip() or "(no preamble — single-candidate file)"
    return PROMPT_TEMPLATE.format(
        repo_root=str(repo_root),
        run_id=run_id,
        source=c.source,
        file_rel=file_rel,
        idx=c.idx,
        local_idx=c.local_idx,
        total=total,
        title=c.title,
        outcome_dir_rel=(c.file_path.parent / "candidate-outcomes").relative_to(repo_root).as_posix(),
        preamble_block=preamble_block,
        body=c.body,
    )


if mode == "--prompt":
    try:
        n = int(prompt_n_arg)
    except ValueError:
        print(f"ERROR: --prompt argument must be an integer 1..{len(candidates)}, got '{prompt_n_arg}'", file=sys.stderr)
        sys.exit(1)
    if n < 1 or n > len(candidates):
        print(f"ERROR: --prompt {n} out of range (1..{len(candidates)})", file=sys.stderr)
        sys.exit(1)
    candidate = candidates[n - 1]
    if candidate.outcomes:
        markers = ", ".join(m.relative_to(repo_root).as_posix() for m in candidate.outcomes)
        print(f"ERROR: candidate {n} already has outcome marker(s): {markers}", file=sys.stderr)
        sys.exit(1)
    print(render(candidate, len(candidates)))
    sys.exit(0)

if mode == "--all-prompts":
    for c in candidates:
        if c.outcomes:
            markers = ", ".join(m.relative_to(repo_root).as_posix() for m in c.outcomes)
            print(f"=== SKIP PROMPT {c.idx} of {len(candidates)} — outcome marker exists: {markers} ===")
            print()
            continue
        print(f"=== PROMPT {c.idx} of {len(candidates)} — {c.source}: {c.title} ===")
        print()
        print(render(c, len(candidates)))
        print()
        print(f"=== END PROMPT {c.idx} ===")
        print()
    sys.exit(0)

print(f"ERROR: unknown mode '{mode}'", file=sys.stderr)
sys.exit(1)
PY
