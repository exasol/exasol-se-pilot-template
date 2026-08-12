# Repository Retention Runbook

This runbook defines how generated pilot repositories review repository size
and retained evidence without deleting project history accidentally.

## Policy

- Keep source code, runbooks, tracker history, and approved evidence.
- Keep test run summaries and issue-candidate outcomes that explain tracker
  decisions.
- Do not commit raw secrets, generated deployment bundles, private keys,
  local caches, virtual environments, or transient build output.
- Treat cleanup recommendations as report-only until an operator approves the
  exact file paths.

## Review Cadence

Review repository size and high-volume folders after large swarm runs, migration
evidence imports, generated Q&A batches, and deployment package changes.

## Evidence

Retention scans should write summaries under `docs/60-evidence-inventory/` or
the relevant `tests/logs/runs/<UTC-id>/` folder.

