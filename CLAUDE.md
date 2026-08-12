# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Behaviour Rules

1. Don't assume. Don't hide confusion. Surface tradeoffs.
2. Minimum code that solves the problem. Nothing speculative.
3. Touch only what you must. Clean up only your own mess.
4. Define success criteria. Loop until verified.

**Parallel local-session / dirty-worktree guardrail (2026-05-29):** Assume another local Claude Code, Codex, or human session may be editing this same checkout at any time. Before treating the repository as current, before file edits, before staging, before committing, and before pushing, check `git status --short --branch` and inspect `git diff --name-only`. Treat unexpected uncommitted changes as another operator's work: do not overwrite, format, stage, reset, clean, or "fix" them unless the user explicitly asks. If those changes touch files needed for your task, inspect the diff and either work around it or pause with a concise conflict note; if they are unrelated, stage only your own paths explicitly. Avoid broad `git add .` / `git commit -a` while parallel sessions are active. If a `docs/XYZ_PILOT_ISSUE_TRACKER.md` diff appears outside the `xyz_master` flow, leave it untouched and report it.

5. **Editing `docs/XYZ_PILOT_ISSUE_TRACKER.md` — pre-edit + post-edit discipline (`XYZ-PILOT-071` + `XYZ-PILOT-108`):**
   - **Single-writer gatekeeper — `xyz_master` ONLY (2026-05-29 directive).** From 2026-05-29 onward, the tracker has exactly one authorised writer: the **`xyz_master` agent**. Every other potential source — Claude Code, codex, the <PILOT_NAME> Chatbot's `_log_to_tracker`, ad-hoc human edits, swarm tester agents — MUST produce an issue-candidate file under `tests/logs/runs/<UTC-run-id>/<source>/issue-candidates.md` and hand it off to `xyz_master` for sequential filing. The reason is the multi-writer race surfaced repeatedly through 2026-05-26..29 (XYZ-PILOT-103/-111/-116/-131/-140/-148 ID collisions + Full-register ordering corruption); funneling all writes through a single coordinator removes that whole class of incident. Operationally this means: (a) when you (Claude Code) identify a tracker change, write a candidate file, do NOT edit `docs/XYZ_PILOT_ISSUE_TRACKER.md` directly; (b) invoke `xyz_master` via `tests/scripts/run_xyz_master_filing.sh <run_id>` (or whatever wrapper the test runbook prescribes) to consolidate + write; (c) the chatbot's `_log_to_tracker` path must be reworked to queue candidates instead of writing inline (filed as a follow-up). Until the chatbot rework lands, treat any concurrent chatbot write as a recoverable race per the archive + verifier flow below.
   - **One-ticket-per-invocation (2026-05-29 directive).** Every `xyz_master` invocation files exactly ONE `XYZ-PILOT-NNN` mutation per pass — one new ticket, OR one status transition, OR one detail-section append. Batched filings (multiple tickets in one xyz_master pass, like the 2-ticket XYZ-PILOT-157 + XYZ-PILOT-158 pass on 2026-05-29) are explicitly discontinued. Reason: (a) smaller blast radius — if a single filing introduces verifier failure or duplicate-content drift, rollback affects one ticket not many; (b) tighter race window with concurrent writers — each xyz_master pass is shorter, less wall-clock for codex / chatbot / daily-status to interleave; (c) cleaner audit trail — `git log -- docs/XYZ_PILOT_ISSUE_TRACKER.md` shows one xyz_master commit per ticket, trivially blame-able. Operationally: if a Claude Code or codex session has N candidates queued, file N separate candidate files under N separate `tests/logs/runs/<UTC-run-id>/<source>/` folders and invoke xyz_master N times. Cost is negligible — verifier runs in ~50 ms, archive_tracker.sh in <1 s, xyz_master subagent invocation in ~1-2 min, all dwarfed by the cost of a single bad batched commit needing dedupe (see xyz_master run `2026-05-29T093225Z` → commits `0141307` + `1190976` for the cautionary tale). Explicit exception: the daily-status Lambda's hygiene pass (prune multiple expired Resolved rows in one transaction) stays batched until XYZ-PILOT-157 reroutes it through xyz_master candidates; once rerouted, each pruned row becomes its own one-ticket xyz_master pass.
   - **Severity defaults (2026-05-30 directive).** The standard severity for any newly-filed `XYZ-PILOT-NNN` ticket is **`minor`** or **`feature`** — operator-confirmation NOT required. Severity **`major`** or **`critical`** requires explicit operator approval before filing; any source (Claude Code, codex, chatbot, swarm tester agents) that wants to assign `major`/`critical` to a new ticket MUST pause and ask <OPERATOR_NAME> first. The point is to keep the high-severity buckets credible — they should only flag work that genuinely needs urgent attention, not work that "felt important when filed." Existing tickets retain their assigned severity unless explicitly downgraded by the operator (XYZ-PILOT-162 and XYZ-PILOT-163 were downgraded `major` → `minor` 2026-05-30 as the seed examples for this rule).
   - **Pre-edit (a)** — run `./scripts/archive_tracker.sh "<reason>"` to snapshot the current `origin/main` version into `docs/archive/XYZ_PILOT_ISSUE_TRACKER_<UTC-timestamp>_<reason>.md`. The tracker has three concurrent writers (humans + chatbot's `_log_to_tracker` + daily-status Lambda's `_trim_tracker_top_table`); the archive is the recovery copy when a rebase / merge silently loses entries (`XYZ-PILOT-071`).
   - **Pre-edit (b)** — run `./scripts/verify_tracker_completeness.sh` to confirm the **incoming** state is sound (no gaps in sequential IDs XYZ-PILOT-001..N, every detail section has a matching `<a id>` anchor, every top-table row references an existing detail section, struck-through Resolved rows fall within the 1-business-day window, top-table strike-through styling matches the Full-register `**Status:**` line, resolved top-table Summary cells remain plain with exactly one compact `(Resolved YYYY-MM-DD)` suffix and no resolution prose, and Full-register detail sections contain no Markdown strike markup). If it returns non-zero, fix what you find BEFORE making your planned change.
   - **Post-edit** — re-run `./scripts/verify_tracker_completeness.sh` after every update / addition / deletion / clean-up to confirm your change didn't introduce new gaps. Whenever you add a top-table row for a new ticket, you MUST also add a matching `### XYZ-PILOT-NNN` detail section in the "Full register (newest first)" — both writer paths together, never just one (`XYZ-PILOT-108`).
   - **xyz_master approval gate before stage/commit/push (2026-06-02 directive; `XYZ-PILOT-157` follow-up).** After an `xyz_master` tracker mutation reaches post-edit verifier clean and `git diff -- docs/XYZ_PILOT_ISSUE_TRACKER.md` sanity has been shown, the writer MUST STOP before `git add`, `git commit`, or `git push`. <OPERATOR_NAME> or another operator must explicitly approve the staged paths and continuation. The intent is to keep verifier-clean tracker edits reviewable before they become immutable central-register commits.
   - **Strict descending order — Full register (`XYZ-PILOT-148`).** The "## Full register (newest first)" section MUST be ordered **strict descending by `XYZ-PILOT-NNN`** — highest ID at the top, lowest at the bottom. Every new detail section is inserted directly below the `## Full register (newest first)` header (i.e. as the new top entry). **When adding a batch of reserved IDs** (e.g. claiming a range like `XYZ-PILOT-141…XYZ-PILOT-146` as placeholders): insert them **one by one in DESCENDING numerical order** (highest first), never as a batch in ascending order — batch ascending corrupts the chronology and the verifier catches it via `(A.1)` Full-register order and `(A.2)` top-table order checks.
   - **Duplicate-row + duplicate-detail detection** (`XYZ-PILOT-140`) — the verifier now flags any XYZ-PILOT-NNN that appears twice as a `### XYZ-PILOT-NNN` detail-section heading OR as a top-table `| [XYZ-PILOT-NNN]` row. If it fires, do NOT just delete a row — first determine if the duplicates are the same ticket (lifecycle drift: prune the obsolete one) or different tickets (concurrent writer race: renumber the newer one to the next free ID).
   - **Semantic status parity — Active table vs Full register.** The top-table visual state must match the detail-section `**Status:**` line: terminal detail statuses (`Resolved`, `Wontfix`) must strike through exactly the ID, Severity, and Area cells in the Active issues table. Resolved rows keep the Summary cell plain, carry only the short original issue statement, and end with exactly one compact `(Resolved YYYY-MM-DD)` marker matching the Full-register `**Status:**` date; never add the resolution narrative there. Non-terminal detail statuses (`Open`, `Workaround`, backlog variants, shipped-but-not-closed notes) must not be struck-through. The verifier fails lifecycle drift as `(E)` and top-table Summary hygiene as `(F)` so both are caught before commit.
   - **Full-register plain-prose rule** (`XYZ-PILOT-148`) — do not use Markdown strike markup inside the "## Full register (newest first)" detail sections. Strike styling is reserved only for the ID, Severity, and Area cells in the short-lived Active issues top table's one-business-day resolved-row visibility window. The verifier fails Full-register strike markup as `(G)`.
   - **Why two checks**: `XYZ-PILOT-108` surfaced 17 silently-missing detail sections from local-development writer-path discipline gaps. The verifier catches these at edit time, not weeks later when `grep` falls off a cliff. Verifier runs in ~50 ms — friction is near-zero.
   - **Candidate-file placeholder format (2026-06-08 directive — `XYZ-PILOT-215` follow-up).** When authoring a candidate under `tests/logs/runs/<UTC-run-id>/<source>/issue-candidates.md`, the `### Top-table row` must use the **raw template form** `| [XYZ-PILOT-NNN](#xyz-pilot-NNN) | <severity> | <area> | <summary> |` (pipe-delimited row, no blockquote `>` prefix, no `~~` strike-through) and the `### Full-register detail section` fenced ` ```markdown ``` ` block must contain raw `<a id="xyz-pilot-NNN"></a>` + `### XYZ-PILOT-NNN — <title>` placeholders. The deterministic filer `scripts/xyz_master_background_filing.py` assigns `NNN` at file-time and applies strike-through automatically when `**Proposed status:**` is terminal (`Resolved`/`Wontfix`) — authors don't write the strike themselves. Reason: 4 CI dispatches failed 2026-06-08 with parser error `top-table row section does not contain a placeholder row` before the format requirement was diagnosed. Canonical template + worked examples live in [`tests/skills/xyz_master.md`](tests/skills/xyz_master.md) under "Candidate-file placeholder format" — copy-paste from there into every new candidate file.
6. **Pre-deploy operational-hours sanity check** — before any `aws ssm send-command`, `docker compose build`, `aws lambda update-function-code`, or other deployment action that needs the live EC2, **first** verify the environment is up. Three parallel checks:
   0. **AWS profile selection (MUST come first)**: every `aws` invocation against <PILOT_NAME> resources must be prefixed `AWS_PROFILE=<AWS_PROFILE> aws …` (or run after `export AWS_PROFILE=<AWS_PROFILE>`). The unset/default profile resolves to the operator's personal account `<AWS_ACCOUNT_ID>` and any Pilot resource lookup will fail with `InvalidInstanceID.NotFound` / `AccessDenied` even though the resource exists. Confirm the binding once per session with `AWS_PROFILE=<AWS_PROFILE> aws sts get-caller-identity` → `Account: <AWS_ACCOUNT_ID>`. If any `aws` command returns a NotFound/AccessDenied surprise, re-run `aws sts get-caller-identity` BEFORE further debugging. Reason: 2026-05-29 — XYZ-PILOT-113/-118 deploy started against the personal account and would have skipped the Pilot EC2 entirely had the lookup not failed loudly.
   1. **EC2 state**: `AWS_PROFILE=<AWS_PROFILE> aws ec2 describe-instances --region eu-central-1 --instance-ids <EC2_INSTANCE_ID> --query 'Reservations[0].Instances[0].State.Name' --output text` — must return `running`. If it returns `stopped` / `stopping` / `pending`, **abort the deploy and tell the user** rather than queue commands against a dead SSM agent (queued SendCommands return InvalidInstanceId, waste minutes, and confuse the deploy log).
   2. **Operational-hours window**: standard hours are **Mon-Fri 12:00 → 24:00 CEST** (start cron `cron(0 10 ? * MON-FRI *)` + stop cron `cron(0 22 * * ? *)`). Outside that window — weekends + Mon-Fri 00:00-12:00 CEST — the env is normally OFF. If a deploy is requested during off-hours, ask the user whether to (a) wait for the next scheduled 12:00 CEST start, (b) trigger a manual ad-hoc start now (fires the XYZ-PILOT-077 [AD-HOC START] email to the team), or (c) commit local changes only and defer the deploy.
   Reason: `XYZ-PILOT-055` deploy on 2026-05-26 07:48 CEST hit `InvalidInstanceId` repeatedly because EC2 was still in its overnight-stopped window — wasted ~3 min of polling before the operator manually launched the env.
7. **Performance regression discipline (`XYZ-PILOT-109` / `XYZ-PILOT-112`)** — after any change that touches an SQL view, a chart-generating SYSTEM_PROMPT path, a <PILOT_SCHEMA> schema element, the warm-up script, the chatbot's `_run_sql_for_chart` / `_tool_exasol_query` paths, the Streamlit QBR tabs, the Grafana dashboards, or `sample_schema_assets/*.sql`:
   1. **Baseline before the change.** From a fresh exapump session: `ALTER SESSION SET QUERY_CACHE='OFF'; <representative query>` — capture `EXA_SQL_LAST_DAY.DURATION` for cold-run + warm-run.
   2. **Verify warm-up coverage.** If your change introduces a new query shape, add the matching `chatbot_chart_*` entry to `scripts/exasol_warmup/warmup.py` `WARMUP_QUERIES` and redeploy the warmup script. The warmup is the **canonical regression test suite**.
   3. **Re-benchmark.** Cold-run latency must be **≤ 1.5× baseline**; warm-run latency must be **≤ 0.5 s** (per `docs/10-product-data/XYZ_PILOT_EXASOL_PERFORMANCE_ANALYSIS.md` §5).
   4. **Document.** Meaningful regressions or improvements are appended to `docs/10-product-data/XYZ_PILOT_EXASOL_PERFORMANCE_ANALYSIS.md` as a new "## Case study" section. Non-trivial changes also file an XYZ-PILOT-NNN ticket cross-linking the perf numbers.
   5. **Streamlit visual regression.** After chatbot/QBR visual dependency bumps, chart-rendering prompt edits, or container rebuilds, run `docs/30-runbooks/ui/XYZ_PILOT_UI.md` (`XYZ-PILOT-149`).
   The discipline applies whenever your change could plausibly impact query latency or memory usage. Skip it only for pure-doc / pure-tracker / non-Exasol-touching changes.

8. **Daily documentation discipline** — after any change that ships code to AWS (Lambda deploy, container restart, IAM/role/policy change, new service), before declaring the task done:
   1. Verify the **deployed source matches local** (`./scripts/verify_deployed.sh` — see template below; for Lambdas: download `Code.Location` zip, `diff` extracted `.py` vs local source).
   2. Verify the **change is committed and pushed** to `origin/main` (`git status` clean + `git log origin/main..HEAD` empty).
   3. Verify the **relevant runbook reflects the new implementation**:
      - Implementation/deploy-time details → `docs/30-runbooks/operations/XYZ_PILOT_DEVELOPER_RUNBOOK.md`
      - Demo/PILOT-execution details → `docs/30-runbooks/deployment/XYZ_PILOT_EXASOL_PILOT_RUNBOOK.md`
      - Operational reference / live system → `docs/00-live-status/XYZ_PILOT_LIVE_RUNBOOK.md`
      - User-facing credentials & rotation → `docs/30-runbooks/security/XYZ_PILOT_CREDENTIALS.md`
      - GitHub repository setup, Actions workflows, OIDC roles, branch protection/rulesets, CODEOWNERS, backup/restore → `docs/30-runbooks/github/XYZ_PILOT_GITHUB_RUNBOOK.md`
   4. Tracker entry filed (under `docs/XYZ_PILOT_ISSUE_TRACKER.md` — follow rule #5 first).
   Skipping any of (1–4) leaves orphan code that no one else can reproduce, debug, or recover from. Reason: prior incidents where deployed Lambda code had no committed counterpart, and where runbooks lagged operational reality by weeks (`XYZ-PILOT-046` + `XYZ-PILOT-074` follow-ups).

9. **Issue-reference link discipline** — when adding or touching standard documentation that mentions a known `XYZ-PILOT-NNN` issue, link that issue ID directly to its anchor in the central tracker only after that tracker entry exists. Use a source-relative path that resolves from the file being edited; root-level files point to `docs/XYZ_PILOT_ISSUE_TRACKER.md#XYZ-PILOT-NNN`, one-level docs folders point to `../XYZ_PILOT_ISSUE_TRACKER.md#XYZ-PILOT-NNN`, and deeper runbook folders point to `../../XYZ_PILOT_ISSUE_TRACKER.md#XYZ-PILOT-NNN`. In this neutral template, historical source-project issue IDs stay as plain `XYZ-PILOT-NNN` identifiers unless the generated tracker contains the matching anchor. Avoid broad mechanical rewrites of old references unless the task is specifically a documentation sweep. **CI backstop:** the [`<PILOT_NAME> Markdown Link / Anchor Check (Advisory)`](.github/workflows/markdown-link-anchor-check.yml) workflow validates both intra-repo `(docs/...)` link targets and `#anchor-slug` fragments at PR-time + on a weekly cron — failures surface as PR-page annotations + the `XYZ-PILOT-174` daily-status `workflow_health` rollup but do not block merge (`XYZ-PILOT-198` advisory-failure discipline). The check is the mechanical enforcement layer for this rule; the rule itself remains operator-discipline so future agents see WHY the links matter, not just THAT the CI says so. The check shipped 2026-05-29 as `.github/workflows/markdown-link-anchor-check.yml` (`XYZ-PILOT-193` closure).

10. **Runbook-class document archiving — `./scripts/archive_runbook.sh` (`XYZ-PILOT-167`):** when a structural change is about to land on a runbook-class document (developer runbook, deployment runbook, MCP client guide, PILOT runbook, GitHub runbook, credentials doc), snapshot the pre-change state into `docs/archive/` so reviewers and rollback flows have a single discoverable file rather than chasing a commit SHA in `git log`.
    - **Trigger conditions** — run `./scripts/archive_runbook.sh <doc-path> "<reason>"` BEFORE:
      - Structural rewrites (section reorders, multi-section deletes, TOC reshapes that change anchor slugs — the kind of change that breaks cross-doc links and makes `git show <sha>:<path>` lookups awkward).
      - Phase / appendix / dedicated subsection deletions.
      - `Audience:` / `Outcome:` / `Total wall time:` / `Cost while running:` / similar header-line changes that change the doc's intended reader.
      - Any change that would invalidate cross-doc links from other files in the repo.
      - The c4cb505 → 8f49886 cycle on 2026-05-30 is the canonical incident that surfaced the gap: a parallel session invented an ad-hoc stub-archive flow without policy backing, producing 11 lines of placeholder text that had to be remediated in a follow-up commit.
    - **Out of scope** — incremental edits (a typo fix, a single-paragraph clarification, a link-target update, a new code-block) do NOT require an archive. The git history is sufficient. The trigger is: *"would a future operator reading the runbook a month from now lose meaningful context if they couldn't see the pre-change version as a single side-by-side file?"* If no, skip the archive.
    - **Tracker archives are NOT this script's responsibility** — `docs/XYZ_PILOT_ISSUE_TRACKER.md` snapshots go through `./scripts/archive_tracker.sh` per Rule #5(a). The runbook script refuses the tracker path with a pointer to Rule #5(a).
    - **Output convention** — `docs/archive/<basename-without-md>_<YYYY-MM-DDTHHMM>_<slugified-reason>.md`. The script reads from `origin/main` (not the dirty working tree) so the archive is the canonical pre-edit state visible to the rest of the world; falls back to the working-tree copy only if `origin/main` is unreachable. Output format matches `./scripts/archive_tracker.sh`.
    - **What NOT to do** — do not create ad-hoc `docs/archive/<doc>_*.md` files via `cp`, `git show > file`, or hand-written stub placeholders. The c4cb505 stub-archive incident is the cautionary tale.

11. **AI-agent commit-attribution discipline (`XYZ-PILOT-168`):** every commit made by an AI assistant (Claude Code, OpenAI Codex, GitHub Copilot agent mode in VS Code, Cursor, or any future agent) MUST end with a `Co-Authored-By:` trailer naming the agent. The trailer marks AI authorship; its absence marks human authorship. Anyone reading `git log --author="<OPERATOR_NAME>"` or `git blame` then sees the actual contribution split rather than every agent commit attributed to whichever human's `git config user.email` was active.
    - **Format examples** (use one trailer per commit, matching the agent that actually authored it):
      - Claude Code: `Co-Authored-By: Claude Opus 4.7 (1M context) <<OPERATOR_EMAIL>>` (model-name suffix updates as the active Anthropic model changes)
      - GitHub Copilot in VS Code (agent mode): `Co-Authored-By: GitHub Copilot <<OPERATOR_EMAIL>>`
      - OpenAI Codex: `Co-Authored-By: OpenAI Codex <<OPERATOR_EMAIL>>`
      - Cursor / Cody / other agents: follow `<Agent Name> <noreply@<provider>>`.
    - **Human-authored commits** are presumed without trailer — no extra burden on humans typing into a terminal. The presence of a `Co-Authored-By:` trailer means "an AI agent wrote or substantially co-wrote this commit"; its absence means "a human typed this".
    - **Retroactive correction:** if an agent's harness commits without the trailer, BEFORE pushing run `git commit --amend --trailer "Co-Authored-By: <agent> <email>"`. If already pushed, file a follow-up commit whose body says `Note: <prior-sha> was authored by <agent>; trailer was omitted in error.` Do NOT force-push to add a trailer post-hoc on `main`.
    - **Per-IDE setup** — concrete steps to bake the trailer in by default for each of the four supported IDEs are in [`XYZ_PILOT_DEVELOPER_RUNBOOK.md` § 0.4 / 0.5 / 0.7](docs/30-runbooks/operations/XYZ_PILOT_DEVELOPER_RUNBOOK.md). Claude Code's CLI template already adds the trailer; Codex + VS Code Copilot need explicit `git config commit.template` configuration.
    - **Optional hook** — `./scripts/install_attribution_hook.sh` installs a non-blocking `commit-msg` hook that warns when a Claude Code session is active (env `CLAUDECODE=1`) and the trailer is missing. Opt-in per workstation; never fails a commit.
    - **Why this matters** — audit / blame ambiguity (`git blame` lands on the human even when the bug is agent-introduced), compliance / IP attribution (AI-generated code may be subject to upstream model-provider licensing terms; the trailer is the evidence for any future IP audit), onboarding clarity (<OPS_USER_2> / Jamie / SI partners reading the commit log should see the actual contribution split, not `<OPERATOR_NAME> 90%+`). The c4cb505 → 8f49886 attribution drift on 2026-05-30 surfaced this as a recurring pattern; codify-once-here closes it. Mirror files at the repo root: [`AGENTS.md`](AGENTS.md) for OpenAI Codex, [`.github/copilot-instructions.md`](.github/copilot-instructions.md) for VS Code Copilot. Each restates the trailer rule + points back at this Rule #11 as canonical.

12. **Operator disambiguation — operator disambiguation (`XYZ-PILOT-190`):** when referencing the operator in prose authored from 2026-06-01 onward — CLAUDE.md / AGENTS.md / .github/copilot-instructions.md / runbooks / tracker detail sections / code comments / Lambda email bodies / dashboard UI strings — use **"<OPERATOR_NAME>"** never an ambiguous short name. Multiple same-name colleagues exist on the <PILOT_NAME> project (<CUSTOMER_NAME> and Exasol operators may share names); ambiguous short names risk miscommunication. For `git` author filters specifically, use `--author="<OPERATOR_NAME>"` (the exact `git config user.name`) because git matches by substring against the full author name, not by display alias. Applies to: new prose only. Historical references in immutable `docs/archive/` snapshots, `tests/logs/` candidate files, and committed git-history commit messages stay as-is — modifying them would corrupt the recovery-copy purpose (`XYZ-PILOT-071` + `XYZ-PILOT-167`). Tracker detail sections for tickets RESOLVED before 2026-06-01 may be left as-is; new tracker entries written from 2026-06-01 onward MUST use "<OPERATOR_NAME>". Mirror restatements live in [`AGENTS.md`](AGENTS.md) + [`.github/copilot-instructions.md`](.github/copilot-instructions.md) so OpenAI Codex + VSCode Copilot honour the discipline too.

13. **Optional third-party semantic-layer lane — operator-coordinated DDL snapshots (`XYZ-PILOT-220` pattern):** a generated pilot may later host a separately licensed semantic-layer or partner deployment that writes DDL directly into Exasol Personal outside this repository. If the operator accepts such a lane, create a dedicated deployment runbook under `docs/30-runbooks/deployment/`, an extract tree under a project-specific folder, and an idempotent extraction script before treating the external DDL as recoverable. The template does not include source-project partner artifacts by default.
    - **Discovery doc:** create a project-specific runbook that captures inventory, architecture, ownership, and integration points.
    - **Extract tree:** create a project-specific folder for DDL, UDF, grant, seed, and inventory extracts. Treat it as a disaster-recovery copy, not as a hand-edited source tree.
    - **Re-extract cadence:** refresh immediately after observed partner deploy bursts, before migration/DR work that touches the footprint, and at a regular cadence while the partner is active.
    - **Tracker discipline applies:** when an audit changes the picture, file an `xyz_master` candidate per Rule #5 and commit the refreshed extract with the matching evidence.
    - **What not to do:** do not attempt to rebuild a licensed third-party product from a partial extract unless the partner's deployment process explicitly supports it.

14. **Documentation topology discipline — tracker-first, topic/dependency folders (2026-06-13 directive):** `docs/XYZ_PILOT_ISSUE_TRACKER.md` is the highest-level project control document. It stays at `docs/` root and remains the canonical coordination layer for priorities, defects, backlog, decisions, status transitions, and cross-links into supporting documents. `docs/README.md` is the navigation index and must point first to the tracker; it is not a replacement control plane.
    - **Dependency order:** organize docs by topic and dependency direction. Higher-numbered/supporting areas may depend on lower-numbered/control areas, but not the reverse: tracker → live status → product/data → architecture → runbooks → migrations → testing/validation → evidence/inventory → generated QA/transcripts → archive.
    - **Future folder taxonomy:** new or relocated docs should use this shape unless an operator explicitly chooses otherwise: `docs/00-live-status/`, `docs/10-product-data/`, `docs/20-architecture/`, `docs/30-runbooks/`, `docs/40-migrations/`, `docs/50-testing-validation/`, `docs/60-evidence-inventory/`, `docs/70-generated-qa/`, and `docs/archive/`. The current flat root is historical debt; do not add more root-level timestamped reports/checklists when a topical folder exists or should be created.
    - **Placement rules:** live operational state and notification history belong under `00-live-status/`; requirements, schema mapping, source-model review, and performance analysis under `10-product-data/`; curated diagrams under `20-architecture/`; stable operator/developer/security/MCP/GitHub/data runbooks under `30-runbooks/`; bounded migration programs such as the AWS account+region move under `40-migrations/<program>/`; test plans, UAT baselines, screenshots, and swarm results under `50-testing-validation/`; AWS tag inventories and audit snapshots under `60-evidence-inventory/`; chatbot-generated Q&A and prompt-derived architecture transcripts under `70-generated-qa/`; immutable historical snapshots under `archive/`.
    - **Migration-program bundles:** active migration programs get a self-contained folder, for example `docs/40-migrations/aws-account-region-2026-06/`, with `README.md`, `runbook.md`, `execution-log.md`, phase assessments, gap checklists, and handover summaries together. Do not scatter dated migration reports across `docs/` root.
    - **Archive grouping:** future archive cleanup should group high-volume snapshots by source document type, especially `docs/archive/issue-tracker/` for `XYZ_PILOT_ISSUE_TRACKER_*` snapshots. Do not move archives mechanically unless links are updated and checked.
    - **Before adding a doc:** decide whether it is control, status, product/data, architecture, runbook, migration execution, validation, evidence, generated Q&A, or archive. Put it in the matching folder first; only keep it at root if it is the tracker or the docs index.
    - **Before moving docs:** update all repository-relative links, run the Markdown link/anchor checker, and preserve history with `./scripts/archive_runbook.sh` where Rule #10 applies. Bulk reorganizations should be small, reviewable commits by topic group, not one giant move.
    - **Dependency graph + TOC upkeep:** `docs/20-architecture/XYZ_PILOT_PROJECT_DOCUMENTATION_TOC.md` is the dependency-aware table of contents and renderable Mermaid dependency graph for the <PILOT_NAME> project. Update it in the same commit whenever you add or relocate a documentation family, add a runtime module, change a module's GitHub read/write path, add a new candidate source under `tests/logs/runs/`, or change validation/evidence ownership. The graphs must stay Streamlit-friendly for the <PILOT_NAME> Chatbot: prefer short node labels and multiple focused Mermaid graphs over one dense diagram.

15. **Exasol IT AWS Resource Tagging Policy gate — mandatory before every AWS deployment:** no generated pilot deployment may create, update, or migrate AWS resources until the Exasol IT AWS Resource Tagging Policy values are confirmed for the target AWS account, region, and availability zone. Every taggable resource must carry the mandatory `exa:*` tags at create time; if a specific AWS API does not support create-time tagging, tag immediately after creation before the workflow continues and record the exception in the runbook or tracker.
    - **Mandatory tag set:** `exa:Project`, `exa:Environment`, `exa:Department`, `exa:Workload`, `exa:CostCenter`, `exa:Owner`, `exa:DataClassification`, and `exa:customer`. `Name`, `ManagedBy`, deployment/run identifiers, and tracker references may be added as supporting tags, but they do not replace the mandatory `exa:*` set.
    - **Configuration source:** generated projects source the policy values from `config/project.env` and document the active values in `docs/60-evidence-inventory/aws-tagging/AWS_RESOURCE_TAGGING_RUNBOOK.md`.
    - **Deployment scripts:** any script that runs `aws ... create-*`, `run-instances`, `allocate-address`, `create-function`, `create-bucket`, `create-role`, `create-schedule`, `create-topic`, or equivalent resource-creation commands must either pass tags directly or call the service-specific tagging API in the same controlled step. Do not leave resources temporarily untagged as a cleanup task for later.
    - **Evidence gate:** after deployment or migration, run `scripts/aws_tag_inventory.sh` for the target account/region and commit or attach the generated evidence when resource state has changed. Tagging gaps are defects, not documentation nits.

## What This Repository Is

A **vendor-neutral pilot template** for standing up an Exasol-on-AWS analytics pilot.
`./generate` renders it into a per-project repository, substituting the project
slug, pilot name, customer name, AWS account/region/AZ, domain, and analytical
schema name.

It is deliberately domain-agnostic. The same template backs pilots in defence,
industry, retail, finance, automotive, energy & utilities, and any other
vertical: the schema, dimensions, and measures ship as neutral placeholders that
the generated project replaces with its own model.

There are no build, test, or lint commands at template level. Work is analytical
and operational: generating and running SQL DDL/DML, querying the live Exasol
instance via MCP tools, and validating deployment artifacts.

## Repository Structure

```
generate, generate.sh            ← renders this template into a project repo
config/
  template.defaults.env          ← generator defaults copied to config/project.env
  placeholders.yml               ← documented placeholder inventory
  recipients.example.yml         ← notification recipient list example

docs/                            ← tracker-first documentation tree (Rule #14)
  XYZ_PILOT_ISSUE_TRACKER.md     ← highest-level control document
  00-live-status/                ← live runbook, daily status, progress log
  10-product-data/               ← requirements, schema mapping, performance
  20-architecture/               ← dependency graph, documentation TOC
  30-runbooks/                   ← deployment, github, mcp, operations, security, ui
  40-migrations/, 40-protocols/  ← bounded programs and template protocols
  50-testing-validation/         ← test plans and runbooks
  60-evidence-inventory/         ← AWS tagging evidence
  70-generated-qa/               ← chatbot-generated Q&A
  archive/                       ← immutable snapshots

services/
  streamlit-insights/            ← DASHBOARD 2 (Analyst): Streamlit QBR over Exasol
  chatbot/                       ← DASHBOARD 3 (Chatbot): Bedrock + Cognito SSO
  exasol-mcp/                    ← public Cognito-OAuth MCP endpoint
  grafana/                       ← Grafana + Prometheus + Exasol proxy

scheduler/                       ← start/stop, daily status, user-management Lambdas
  status_dashboard/              ← DASHBOARD 1 (Operator): static prod page (S3+CloudFront)
  dev_operator_dashboard/        ← Operator dashboard, Flask dev parity
  xyz_pilot_user_mgmt.py         ← Operator dashboard backend Lambda
scripts/                         ← deploy, verification, warmup, tracker tooling
tests/                           ← probes, swarm agents, regression suites
.agents/skills/                  ← pre-built + project-authored agent skills
```

The template ships **three operator-facing dashboards** — Operator, Analyst and
Chatbot. Note the placement asymmetry: Analyst and Chatbot live under `services/`,
while the Operator dashboard lives under `scheduler/` because it is a static page
plus a Lambda backend rather than a container. There is deliberately no
`services/operator-dashboard/`.

The analytical SQL assets (`ddl.sql`, `views.sql`, seed data, measures) are
**not** shipped by the template — a generated project creates them for its own
domain against the `<PILOT_SCHEMA>` placeholder described below.

## Live Exasol Personal Instance

**Status:** Running on AWS eu-central-1  
**Connection details:** see `config/project.env`

| Parameter | Value |
|-----------|-------|
| Host | `<EXASOL_HOSTNAME>` |
| DB port | `8563` |
| Admin UI | `https://...compute.amazonaws.com:8443` (NOT port 443) |
| Schema | `<PILOT_SCHEMA>` |
| User | `xyz_dash` for routine read-only checks via `xyz-pilot-ro`; `sys` only for explicit admin/bootstrap tasks |

Routine local SQL validation uses the least-privilege `xyz-pilot-ro` exapump profile. The local `exasol_personal` MCP server is a developer-only FastMCP/pyexasol wrapper; do not confuse its helper tools with the official Exasol MCP image, which remains metadata-only unless wrapped by the Pilot public endpoint.

To use exapump directly for routine validation: `exapump sql --profile xyz-pilot-ro "SELECT ..."` (`XYZ-PILOT-131`). Use `xyz-pilot` / `SYS` only for explicit admin operations such as schema bootstrap, MV refresh, or password rotation.
Deployment directory (for `exasol info`, `exasol connect`): `exasol-deployment/` (gitignored)

## <PILOT_SCHEMA> Schema

`<PILOT_SCHEMA>` is a **placeholder**, rendered by `./generate` from
`PILOT_SCHEMA` in `config/project.env` (default `PILOT_DW`). Every table, view
and column name below is likewise a domain-neutral placeholder: a generated
pilot renames them to its own vertical's model (orders, claims, sorties,
meters, work orders, …). Nothing here implies a retail or sales domain.

Reference shape: 15 canonical tables (12 dimension + 3 fact), 4 physical `MV_*`
snapshot tables, 4 base views, 4 analytics views, populated with synthetic data.

**Fact tables:** `FACT_PRIMARY` (170,072,000 rows), `FACT_STOCK` (4,915,200), `FACT_TARGET` (245,760)
**Key joins:** `FACT_PRIMARY.CATEGORY_KEY → DIM_CATEGORY.CATEGORY_KEY`, `FACT_PRIMARY.ACCOUNT_KEY → DIM_ACCOUNT.ACCOUNT_KEY`, `FACT_PRIMARY.REGION_KEY → DIM_REGION.REGION_KEY`
**Relational integrity:** 12 primary keys + 21 foreign keys are declared in the live model (`XYZ-PILOT-041`).

**Analytics views** (use these for dashboard queries):
- `V_MONTHLY_METRICS` — monthly aggregates with YTD, rolling 3M/6M, GM%, segment/region share
- `V_YOY_METRICS` — year-over-year self-join on V_MONTHLY_METRICS
- `V_ACTUAL_VS_TARGET` — actual vs FACT_TARGET with monthly and YTD variance
- `V_STOCK_ANALYTICS` — stock turns and coverage days per segment

## Live Pilot ServiceURIs and AWS Runtime

Stable named endpoints on the `<PILOT_DOMAIN>` zone:

| Surface | URI | Auth |
|---|---|---|
| Streamlit QBR | `https://<PROJECT_SLUG>-qbr.<PILOT_DOMAIN>` | `APP_PASSWORD` |
| Grafana | `https://<PROJECT_SLUG>-grafana.<PILOT_DOMAIN>` | Grafana users |
| <PILOT_NAME> Chatbot | `https://<PROJECT_SLUG>-chat.<PILOT_DOMAIN>` | Cognito SSO |
| Public Exasol MCP | `https://<PROJECT_SLUG>.<PILOT_DOMAIN>/mcp` | Cognito OAuth |

Subscriber identity uses AWS Cognito user pool `<COGNITO_USER_POOL_ID>`; the chatbot and public MCP endpoint use separate app clients on the same pool. Core operational Lambdas include `<PROJECT_SLUG>-start`, `<PROJECT_SLUG>-stop`, `<PROJECT_SLUG>-daily-status`, `<PROJECT_SLUG>-cognito-sync-subscribers`, `<PROJECT_SLUG>-cognito-invite-blast`, `<PROJECT_SLUG>-weekend-recap-oneshot`, `<PROJECT_SLUG>-user-mgmt`, and `<PROJECT_SLUG>-get-secret`.

## MCP Servers

**Public remote `pilot_exasol`** — `https://<PROJECT_SLUG>.<PILOT_DOMAIN>/mcp`

Cognito-OAuth public endpoint for ChatGPT Web, Claude Desktop, VS Code Copilot, and SDK clients. It layers Pilot guardrails over the official Exasol MCP image: official metadata/search/describe tools plus guarded read-only SQL aliases `exasol_query`, `execute_read_only_sql`, and `run_exasol_query` as `xyz_dash` (`XYZ-PILOT-145`). See `docs/30-runbooks/mcp/XYZ_MCP_CLIENT_GUIDE.md`.

**`exasol_personal`** — local FastMCP server at `/Users/<LOCAL_USER>/fastmcp-v3-demo/server.py`

Developer-only pyexasol helper surface configured in `.claude/settings.json`. Tools include `exasol_query`, `exasol_execute`, `exasol_list_schemas`, `exasol_list_tables`, `exasol_describe_table`, `exasol_row_count`, `exasol_import_csv`, `exasol_explain`, `exasol_session_info`, `exasol_run_script`. Treat write-capable tools as admin-only and prefer `xyz-pilot-ro` for routine checks.

**Chatbot internal MCP sidecar** — `mcp-internal` container on the chatbot Docker bridge

Internal-only official Exasol MCP sidecar for metadata tools. The chatbot's live result retrieval is its own pyexasol `exasol_query` tool using `xyz_dash`, row/char caps, read-only verb checks, and a process lock (`XYZ-PILOT-053`).

## Exasol 2025.x SQL Compatibility — Lessons Learned

These are hard-won fixes from deploying against Exasol 2025.2.0. Apply them proactively:

| Pattern | Problem | Fix |
|---------|---------|-----|
| `SELECT *, extra_col FROM t` | Not valid in views | `WITH c AS (SELECT * FROM t) SELECT c.*, extra_col FROM c` |
| `EXTRACT(WEEK FROM d)` | Not supported | Use `CEIL(TO_NUMBER(TO_CHAR(d,'DDD'))/7.0)` |
| `EXTRACT(DOW FROM d)` | Not supported | Use `TO_CHAR(d,'D')` (returns 1=Sun … 7=Sat) |
| `DAYNAME(d)` | Not found | Use `CASE TO_CHAR(d,'D') WHEN '1' THEN 'Sunday' ...` |
| `LAST_DAY(d)` | Not found | Use `d + 32 - EXTRACT(DAY FROM (d + 32))` |
| `TO_CHAR(d,'YYYYMMDD')` | Format fails | Use arithmetic: `YEAR*10000 + MONTH*100 + DAY` |
| `TO_CHAR(d,'fmDay')` | `fm` modifier unsupported | Use CASE on `TO_CHAR(d,'D')` |
| `FROM DUAL CONNECT BY LEVEL <= n` | LEVEL alias not referenceable directly | Wrap: `FROM (SELECT LEVEL AS r FROM DUAL CONNECT BY LEVEL <= n)` |
| Reserved keywords as aliases | `DATE`, `USER`, `YEAR`, `MONTH`, `STATE`, `PROFILE` all reserved | Always double-quote: `AS "DATE"`, `AS "YEAR"` |
| `NVL2(NULLIFZERO(b), a/b, NULL)` | Null-safe division | Use `CASE WHEN b <> 0 THEN a/b END` or this pattern |

## Agent Skills

Relevant skills in `.agents/skills/`:

- **`exasol-database`** — Exasol SQL dialect, exapump CLI, reserved keywords
- **`exasol-udfs`** — Lua UDF development for edge-case measure logic
- **`setup-personal`** — Exasol Personal Edition AWS deployment/lifecycle
- **`hashicorp-terraform`** — AWS infrastructure changes
- **`secrets-safety-reviewer`** — Credential handling checks
- **`github-actions-governance`** — GitHub Actions trigger, permissions, OIDC, concurrency, cost, and activation review
- **`github-security-supply-chain`** — CodeQL, Dependabot, secret scanning, push protection, action pinning, and ruleset review
- **`github-tracker-notification-automation`** — Tracker diff parsing and major-defect heads-up notification design

## Pilot Architecture

**Source → Target:** the customer's existing data warehouse and OLAP/semantic
layer → Exasol Personal on AWS, re-connected to the customer's BI client of
choice. The template is agnostic about the source platform and the BI front end;
a generated project records its own source/target pair in
`docs/10-product-data/`.

**Reference runtime capability (what a fully deployed pilot provides):**
- Schema plus large-scale fact data deployed and validated
- 4 analytics views + 4 physical `MV_*` snapshot tables for warm dashboard paths
- Streamlit QBR, Grafana, chatbot, and public MCP exposed through stable named Cloudflare tunnels
- Public MCP endpoint with Cognito OAuth, metadata tools, and guarded read-only SQL aliases as `xyz_dash`
- Chatbot with Cognito SSO, Bedrock tool use, repo tools, live SQL, tracker filing, and per-user history
- Scheduler/status/onboarding Lambdas for start/stop, daily status, subscriber sync/invite, user management, and secrets retrieval

**Interim option:** where the existing semantic layer supports a relational
passthrough driver, it can be re-pointed at Exasol while the original model
stays intact. The full migration replaces it with Exasol views plus a thin
direct-query dataset in the BI client.

## Generated Assets — Streamlit QBR Dashboard

A working QBR dashboard lives at `services/streamlit-insights/` and runs anywhere Python runs — no proprietary BI desktop client required. It backs 5 tabs: Executive Summary, Actual vs Target, YoY, Stock, and Data. The runtime dashboard uses `MV_*` snapshot tables for the main analytical tabs plus Exasol catalog views for the Data tab.

**Run:**
```bash
cd services/streamlit-insights
set -a && . ./.env && set +a       # .env is gitignored — see .env.example
uv sync
uv run streamlit run app.py --server.headless true \
    --server.port 8501 --server.address 0.0.0.0
```

**ServiceURIs:**

| Label | URI | Reachable from |
|-------|-----|----------------|
| Local | `http://localhost:8501` | host machine |
| Public Pilot | `https://<PROJECT_SLUG>-qbr.<PILOT_DOMAIN>` | subscribers with `APP_PASSWORD` |

**Health check:** `curl http://localhost:8501/_stcore/health` → `ok`.

**Key Exasol gotcha encoded in `db.py`:** TLS is enforced server-side (`08004 Only TLS connections are allowed`). Set `EXASOL_TLS=true` and pass `websocket_sslopt={'cert_reqs': 0}` to skip CA verification on the self-signed cert.

## Performance Demo Scale

`FACT_PRIMARY` has been scaled from 8,000 → **170,072,000 rows** (FACT_KEY duplicates accepted — no PK constraint). Warm dashboard paths use the physical `MV_*` snapshot tables (`MV_MONTHLY_METRICS`, `MV_YOY_METRICS`, `MV_ACTUAL_VS_TARGET`, `MV_STOCK_ANALYTICS`) so QBR/Grafana interactions stay sub-second at the current scale.

Runtime scale and object-count facts in this context block were refreshed under `XYZ-PILOT-132`.

The 4 analytical views are unchanged in row count after scaling because they aggregate on `PERIOD_YEAR × PERIOD_MONTH × xyz_PILOT_REGION_1 × SEGMENT × SUBCATEGORY` — duplicating fact rows multiplies the SUMs but not the distinct grouping keys.
