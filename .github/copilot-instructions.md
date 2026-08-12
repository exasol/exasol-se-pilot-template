# GitHub Copilot — <PILOT_NAME> repo-level instructions

This file is read by **GitHub Copilot Chat** (Ask, Edit, and Agent modes) in VS Code. The canonical, authoritative source of repo behaviour rules is [`../CLAUDE.md`](../CLAUDE.md); this file restates the rules that matter most for a Copilot session and points at CLAUDE.md for the full set.

## Critical for every Copilot commit (agent mode)

**Commit-attribution trailer (CLAUDE.md Rule #11, XYZ-PILOT-168).** Every commit Copilot makes in agent mode (via VS Code's source-control panel or the in-chat commit button) MUST end with:

```
Co-Authored-By: GitHub Copilot <<OPERATOR_EMAIL>>
```

Copilot in VS Code does NOT add this trailer by default — commits show up as authored by the human's `git config user.email` with no trailer, masking AI authorship in `git log` and `git blame`. Two ways to bake it in:

1. **VS Code commit template** (recommended) — `git config commit.template ~/.copilot-commit-template` in this repo, where `~/.copilot-commit-template` is a file ending with:
   ```

   Co-Authored-By: GitHub Copilot <<OPERATOR_EMAIL>>
   ```
2. **Per-commit explicit trailer** — when generating the commit message in agent mode, append the `Co-Authored-By:` line in the message body.
3. **Optional non-blocking hook** — `./scripts/install_attribution_hook.sh` installs a local `commit-msg` hook that warns when the trailer is missing during a detected AI session. Opt-in per workstation; never fails a commit.

The trailer is non-negotiable: human commits in this repo are presumed to lack it; agent commits MUST carry it.

## Other critical rules (read CLAUDE.md for full text)

- **Rule #5 — Tracker writes go through XYZ_MASTER only.** Copilot must NOT edit `docs/XYZ_PILOT_ISSUE_TRACKER.md` directly. File an issue-candidate under `tests/logs/runs/<UTC-id>/VSCODE_COPILOT/issue-candidates.md` and let XYZ_MASTER consolidate. **Cheapest path (2026-06-30):** a background **`XYZ_MASTER_SERVICE`** auto-drains **committed** candidate files deterministically (`scripts/xyz_master_background_filing.py`, commits + pushes, zero LLM tokens). So author candidate → **commit it → let the service file it**; once committed, do NOT also apply it yourself or have an LLM re-read/rewrite the ~10–17K-line tracker (races the service → duplicate commit, and burns ~50K+ tokens). **Token-heavy direct LLM tracker manipulation is prohibited.**
- **Rule #6 — AWS_PROFILE discipline.** Every `aws` invocation against <PILOT_NAME> resources must be `AWS_PROFILE=<OPS_USER_1>Degen aws …`. Unset profile resolves to the personal account and Pilot lookups will fail.
- **Rule #8 — Daily documentation discipline.** After any AWS deploy, verify deployed source = local source, then update the relevant runbook.
- **Rule #10 — Runbook-class document archiving.** Before structural rewrites of any runbook, run `./scripts/archive_runbook.sh <path> "<reason>"`. Do NOT invent ad-hoc archive flows (the c4cb505 stub on 2026-05-30 is the cautionary tale that prompted Rule #10).
- **Rule #12 — Operator disambiguation ("<OPERATOR_NAME>" not "<OPS_USER_1>").** In any prose Copilot authors from 2026-06-01 onward (commit messages, chat answers, code comments, doc edits), reference the operator as **"<OPERATOR_NAME>"** never bare "<OPS_USER_1>". Multiple <OPS_USER_1> colleagues exist on the project. For git author filters use `--author="<OPERATOR_NAME>"` (the exact `git config user.name`). Historical references in `docs/archive/`, consumed candidate files, and committed git messages stay as-is.
- **Rule #14 — Documentation topology discipline.** `docs/XYZ_PILOT_ISSUE_TRACKER.md` is the highest-level project control document and stays at `docs/` root. New docs must be placed by topic/dependency: live status, product/data, architecture, runbooks, migrations, testing/validation, evidence/inventory, generated QA, or archive. When moving docs, update repository-relative links and run the Markdown link/anchor checks before commit. Keep `docs/20-architecture/XYZ_PILOT_PROJECT_DEPENDENCY_TOC.md` accurate whenever docs, runtime modules, GitHub read/write paths, candidate sources, or validation/evidence ownership change; its Mermaid graphs must remain Streamlit-friendly for the <PILOT_NAME> Chatbot.

## Parallel-session guardrail

This checkout is shared with Claude Code, OpenAI Codex, and the human operator. Before any edit, staging, commit, or push: run `git status --short --branch` and `git diff --name-only` to surface other operators' work. Treat unexpected uncommitted changes as someone else's WIP — do not overwrite, format, stage, reset, or "fix" them unless explicitly asked. Stage only your own paths explicitly; avoid `git add .` / `git commit -a` while parallel sessions are active.

## Authoritative source

For any rule not restated here, defer to [`../CLAUDE.md`](../CLAUDE.md). When CLAUDE.md and this file disagree, CLAUDE.md wins.
