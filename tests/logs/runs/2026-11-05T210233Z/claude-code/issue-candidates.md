# Candidate handoff

Source: Claude Code session, PR #13 (`feat/brand-skill-bootstrap`).
Scope: exactly one tracker mutation, hygiene only. No ticket ID is added or changed.

## Candidate - Hygiene: normalise the Full-register heading

- **Mutation kind:** hygiene
- **Proposed severity:** `hygiene`
- **Proposed area:** `Tracker · structure`
- **Observed:** `docs/XYZ_PILOT_ISSUE_TRACKER.md` line 21 reads `## Full Register (newest first)` with a capital R.
- **Expected:** `## Full register (newest first)`, as CLAUDE.md Rule #5, `scripts/verify_tracker_completeness.sh` (strict-descending-order check) and `scripts/xyz_master_background_filing.py` (`register_marker`) all require.
- **Evidence:** `python3 scripts/xyz_master_background_filing.py --mode dry-run --candidate-file tests/logs/runs/2026-11-05T210010Z/claude-code/issue-candidates.md` exits with `ERROR: full-register marker not found`. The verifier passes today only because its order check never matches the capitalised heading.

### Requested mutation

Replace the single line `## Full Register (newest first)` with `## Full register (newest first)`. Change nothing else: no top-table row, no detail section, no other line.

### Validation

- Pre-edit and post-edit `./scripts/verify_tracker_completeness.sh` exit 0.
- `git diff --stat docs/XYZ_PILOT_ISSUE_TRACKER.md` shows 1 insertion, 1 deletion.
- The deterministic filer dry-run above no longer reports `full-register marker not found`.
