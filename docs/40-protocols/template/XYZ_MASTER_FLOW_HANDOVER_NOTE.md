# Handover note — the `xyz_master` single-writer tracker-filing flow

**To:** operators adopting `exasol-se-pilot-template`
**From:** operator of the reference implementation this flow was extracted from
**Date:** 2026-08-07
**Status:** the flow is **already templatized in this repo** as `xyz_master`. This note is the take-over guide — what it is, the moving parts, how they connect, how to instantiate it, and the hard-won lessons.

> Provenance: the practices below come from a live pilot where this flow ran in anger. The template equivalents use the neutral placeholders (`xyz_master`, `XYZ-PILOT-NNN`, `<TRACKER_FILE>`, `<PILOT_NAME>`, `<OPERATOR_USER>`, …) that `generate.sh` / `scripts/render_template_placeholders.py` render into a concrete project.

---

## 1. What the flow is (and the problem it solves)

The central register (`docs/XYZ_PILOT_ISSUE_TRACKER.md`) is a high-value, frequently-mutated Markdown file with an **Active-issues top table** + a **Full register (newest-first)**. Multiple actors want to write it: Claude Code, Codex, the chatbot's `_log_to_tracker`, swarm test agents, the daily-status hygiene pass, and humans.

Letting them all edit it directly produced a recurring class of incident — **ID collisions and Full-register ordering corruption** under concurrent writes (observed repeatedly in the reference pilot). The fix is a **single-writer coordinator**: exactly one authorized writer (`xyz_master`); everyone else emits a **committed issue-candidate file** and lets the coordinator file it **deterministically** (zero LLM tokens) with archive + verify gates around every mutation.

**Golden rule:** never edit `docs/XYZ_PILOT_ISSUE_TRACKER.md` directly. Emit a candidate; let `xyz_master` file it. (The only sanctioned exception is an explicit operator directive during an outage of the flow itself — see §6.)

---

## 2. Component inventory (all already present in this repo)

| Concern | Template file (this repo) | Role |
|---|---|---|
| Central register | `docs/XYZ_PILOT_ISSUE_TRACKER.md` | Active top table + Full register |
| Deterministic filer | `scripts/xyz_master_background_filing.py` | Parse 1 candidate → 1 mutation; assign `NNN` at file-time; apply strike for terminal status; **0 LLM tokens** |
| Completeness gate | `scripts/verify_tracker_completeness.sh` | Checks **A–K** (see §4); ~50 ms; run pre **and** post every edit |
| Pre-edit snapshot | `scripts/archive_tracker.sh` | Snapshot `origin/main` tracker → `docs/archive/` before mutating (recovery copy) |
| Size mitigation | `scripts/split_tracker_archive.py` | Splits the register when it nears GitHub's ~1 MiB rich-render ceiling (verifier check **K**) |
| Outcome markers | `scripts/write_candidate_outcome.py`, `scripts/verify_xyz_master_candidate_outcomes.py` | Write/verify `candidate-outcomes/` markers so a drained candidate is never re-picked |
| CI filer | `.github/workflows/xyz-master-background-filing.yml` | `workflow_dispatch` entry point; runs the filer; commits/PRs |
| Background drain service | `scheduler/xyz_master_candidate_drain.py` + `scheduler/deploy_candidate_drain.sh` | Auto-drains **committed** candidates (dispatches the workflow); zero-token |
| Operator dispatch | `scheduler/xyz_pilot_user_mgmt.py` → `_action_xyz_master_dispatch_candidate` | Operator-Dashboard button → GitHub API `workflow_dispatch`; elevated-operator gate; writes `APPROVED_DISPATCHED` marker |
| Notifications | `.github/workflows/notify-on-tracker-mutation.yml`, `tracker-major-defect-heads-up.yml`, `tracker-major-defect-resolved-sns.yml` | Fire on tracker mutations (email/SNS on major defects) |
| Canonical format + examples | `tests/skills/xyz_master.md` | Copy-paste candidate templates |
| Discipline | `CLAUDE.md` Rule #5 (+ mirror in `AGENTS.md`, `.github/copilot-instructions.md`) | The operator contract, already templatized |

---

## 3. Data flow (candidate → filed)

```
any source (Claude/Codex/chatbot/swarm/human)
      │  identifies a tracker change
      ▼
tests/logs/runs/<UTC-run-id>/<source>/issue-candidates.md   ← author + COMMIT (force-add if tests/logs is git-excluded)
      │
      ├─(a) background service  scheduler/xyz_master_candidate_drain.py   ← auto, zero-token (preferred/cheapest)
      ├─(b) operator dashboard  _action_xyz_master_dispatch_candidate      ← human-approved dispatch
      └─(c) interactive         tests/scripts/run_xyz_master_filing.sh <run-id>
      ▼
.github/workflows/xyz-master-background-filing.yml  (workflow_dispatch)
   checkout → py_compile → authorize elevated operator → prepare args →
   dry-run (always) → [dry-run stops here]
                    → [direct-main: enforce low-severity scope → refresh main → apply → commit+push]
                    → [pr: create branch → apply → open PR]
      │  each apply is: archive → verify(pre) → mutate ONE ticket → verify(post)
      ▼
docs/XYZ_PILOT_ISSUE_TRACKER.md updated on main   +   candidate-outcomes/<...>.md marker
```

**Filer CLI** (`scripts/xyz_master_background_filing.py`): `--mode {dry-run,apply}`, `--run-id`, `--source`, `--candidate-index`, `--classification-override`, `--operator-approved-high-severity`, `--diff-file`, `--summary-file`, `--github-output`. The **workflow** exposes the operator-facing trilogy **`dry-run` | `pr` | `direct-main`** (direct-main is gated to low-severity/feature or status/severity transitions; major/critical must go via `pr` + `--operator-approved-high-severity`).

**Candidate file shape** (canonical copy in `tests/skills/xyz_master.md`):
- New ticket: `**Proposed severity:**`, `**Proposed area:**`, `**Proposed status:**`, `**Surfaced:**`.
- Status transition: `**Existing ID:**` + `**Proposed status:**` + a `### Resolution evidence` block.
- Evidence update: `**Existing ID:**` + `**Evidence date:**` + a `### Evidence update` block.
- `### Top-table row` = raw placeholder form `| [XYZ-PILOT-NNN](#xyz-pilot-NNN) | <sev> | <area> | <summary> |` — **no** `~~strike~~`, **no** `>` prefix.
- `### Full-register detail section` = a fenced ` ```markdown ``` ` block with `<a id="xyz-pilot-NNN"></a>` + `### XYZ-PILOT-NNN — <title>`.
- The filer assigns the real `NNN` at file-time and applies strike-through automatically when the proposed status is terminal. **Titles must contain no double-quotes** (they break the `git commit -m` path).

---

## 4. The verifier gate (checks A–K)

`verify_tracker_completeness.sh` is the guardrail that makes deterministic filing safe. It fails (non-zero, blocks the filer) on: sequential-ID gaps; missing `<a id>` anchors; **(A.1/A.2)** Full-register / top-table ordering not strict-descending; **(D)** struck Resolved/Wontfix rows past the one-business-day visibility window; **(E)** lifecycle drift (top-table strike vs detail `**Status:**`); **(F)** terminal top-table Summary hygiene; **(G)** strike markup inside the Full register; **(J)** the "Today is …" visibility-rule date being stale; **(K)** register size nearing the ~1 MiB render ceiling; plus duplicate row/detail detection. Run it **before and after** every mutation.

---

## 5. Taking it over — instantiation checklist

1. **Render placeholders.** Run `generate.sh` (wraps `scripts/render_template_placeholders.py`) to substitute `XYZ` → your prefix, `XYZ-PILOT`/`XYZ_PILOT` → `<TRACKER_ID_PREFIX>`/`<TRACKER_PREFIX>`, `xyz_master` → your coordinator name, and `<PILOT_NAME>`/`<OPERATOR_USER>`/`<OPERATOR_EMAIL>`/`<GITHUB_OWNER>`/… → concrete values. Then run `scripts/validate_template_neutrality.sh` to confirm no placeholders leaked.
2. **Seed the register.** Keep `docs/<TRACKER_FILE>`'s skeleton: the Active-issues top table (`| ID | Severity | Area | Summary |`), the `> *Today is …*` visibility line, and `## Full register (newest first)`.
3. **Ship the workflow.** `.github/workflows/<prefix>-master-background-filing.yml` needs a PAT/OIDC identity with **Actions read/write + contents write** (and pull-requests write for `pr` mode). Confirm the `workflow_dispatch` inputs match the filer args.
4. **Deploy the drain service.** `scheduler/deploy_candidate_drain.sh` installs `xyz_master_candidate_drain.py` and its trigger (a schedule / Lambda that scans for committed candidates and dispatches the workflow). This is the cheapest path — author → commit candidate → service drains.
5. **Wire the operator dispatch** (optional) if you use the Operator Dashboard: `_action_xyz_master_dispatch_candidate` + its elevated-operator gate.
6. **Keep the discipline.** Retain `CLAUDE.md` Rule #5 verbatim (mirror in `AGENTS.md` + `.github/copilot-instructions.md`). It is the contract every agent reads.

Validation before first real filing: `python3 -m py_compile scripts/xyz_master_background_filing.py`, `bash -n scripts/*.sh`, then a **`dry-run`** dispatch of a throwaway candidate — confirm the diff/summary render and the verifier passes.

---

## 6. Hard-won lessons (carry these, they are not obvious)

- **The whole flow hinges on `workflow_dispatch`.** A GitHub Actions outage blocks *all* filing — both the operator dispatch and the service auto-drain (observed: a ~13.5 h Actions major outage). **The committed candidate is the durable buffer**: it survives the outage on disk/in-git and drains on recovery, so nothing is lost — filings are *delayed, never dropped*. Build/monitor accordingly; don't panic-edit the tracker directly.
- **Outage cascade into the verifier.** That same outage also blocked the ~06:00 daily hygiene prune, so an expired Resolved row aged past its one-business-day window → verifier check **(D)** then blocked *every* subsequent filing until the row was pruned. After any Actions outage, run/confirm the daily hygiene pass first, or the backlog won't drain.
- **Never let an LLM rewrite the tracker.** Reading + rewriting a ~1 MiB register costs ~50K+ tokens **and** races the service (duplicate commit → non-fast-forward). Use the deterministic filer (~0 tokens). Direct LLM tracker manipulation is explicitly prohibited in Rule #5.
- **One ticket per invocation.** Smaller blast radius, tighter race window, blame-able history.
- **Severity defaults to `minor`/`feature`.** `major`/`critical` require explicit operator approval before filing (`--operator-approved-high-severity`, and `pr` mode).
- **Approval gate before commit/push** for interactive filings: verifier-clean + show the diff, then stop for operator sign-off.
- **Overnight freeze + waiver.** If the adopting project uses an overnight no-commit window, encode both the freeze and an *explicit operator waiver* path (Rule #16 exception) — a central-outage defect sometimes legitimately needs to land inside the freeze to make a morning report; require the waiver to be stated in the commit body.

---

## 7. Ready-to-review

This note is **prepared, not committed** — it's a cross-project artifact for this template's operators to review and land. Suggested home: `docs/40-protocols/template/` (here). No source files were modified; the flow itself is already present and functional in this repo under the `xyz_master` placeholders.
