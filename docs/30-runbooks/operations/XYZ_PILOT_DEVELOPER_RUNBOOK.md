# Pilot Developer Runbook

## Daily Discipline

- Start with `git status --short --branch`.
- Treat unexpected local changes as another operator's work.
- File tracker changes as candidates under `tests/logs/runs/<UTC-id>/<source>/`.
- Let the master-filer path consolidate tracker mutations.
- Update runbooks after deployment or operational behavior changes.
- Run link and tracker checks before commit.

## Commit Attribution

AI-assisted commits must include an explicit `Co-Authored-By` trailer naming the
tool identity and approved project email.

