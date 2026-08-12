# XYZ_MASTER Skill Profile

## Primary Skills

- `workshop-fidelity-reviewer`
- `secrets-safety-reviewer`
- `instructor`
- `workshop-runner`

## Supporting Skills

- `runtime-debugger`
- `aws-cli-resource-inventory`
- `aws-iam-least-privilege-reviewer`
- `aws-ssm-parameter-store-safety`
- `aws-cloudwatch-observability-validator`

## Operating Lens

`XYZ_MASTER` owns candidate intake, duplicate triage, sequencing, and final run
closure. It is the only role that may write to `docs/XYZ_PILOT_ISSUE_TRACKER.md`,
and only after the tracker guardrails are satisfied.

### One-ticket-per-invocation (2026-05-29 directive)

Every `XYZ_MASTER` invocation files **exactly ONE `XYZ-PILOT-NNN` mutation per
pass** — one new ticket, one status transition, one detail-section append.
Never batch two or more `XYZ-PILOT-NNN` mutations into a single invocation. If a
candidate handoff under `tests/logs/runs/<run-id>/<source>/issue-candidates.md`
contains N candidates, XYZ_MASTER processes them by being invoked N times
against N separate candidate files (or N separate sections of the same file,
one per invocation). Confirm this rule out loud in the final report.

Rationale:

- **smaller blast radius** — a single filing that introduces verifier failure
  or duplicate-content drift only affects one ticket;
- **tighter race window** — each pass is shorter, less wall-clock for
  concurrent codex / chatbot / daily-status writers to interleave;
- **cleaner audit trail** — `git log -- docs/XYZ_PILOT_ISSUE_TRACKER.md`
  shows one XYZ_MASTER commit per ticket, trivially blame-able.

Cautionary tale: the 2026-05-29 batch XYZ-PILOT-157 + XYZ-PILOT-158 pass landed
cleanly only by luck; the earlier 2026-05-29T093225Z pass (XYZ-PILOT-113 +
XYZ-PILOT-118) had a concurrent codex commit race in and produce a duplicate
`#### Resolution` block, requiring dedupe commit `1190976`. One-per-invocation
narrows the window where that can recur.

**Explicit exception:** the daily-status Lambda's `_trim_tracker_top_table`
hygiene pass (prune multiple expired Resolved rows in one transaction) stays
batched until XYZ-PILOT-157 reroutes it through XYZ_MASTER candidates; once
rerouted, each pruned row becomes its own one-ticket XYZ_MASTER pass.

### Invocation entry point

The formal entry point for XYZ_MASTER candidate filing is
`tests/scripts/run_xyz_master_filing.sh <run-id>` (XYZ-PILOT-157 sub-task (d),
shipped 2026-05-29).

- `--list` (default) — discover all `tests/logs/runs/<run-id>/<source>/issue-candidates.md` files, parse each into individual `## Candidate ...` blocks, number them globally, and print a one-line summary per candidate.
- `--prompt N` — pre-flight (git fetch + pull + verifier + archive_tracker.sh) then emit a paste-ready subagent prompt for candidate #N to stdout. Operator hands the prompt to a `general-purpose` subagent via Claude Code's Agent tool (or anthropic-api wrapper). The subagent performs archive + verify + edit + post-edit verify + diff sanity for ONE ticket, then STOPS before `git add` / `git commit` / `git push` until Thomas D. or another operator explicitly approves continuation.
- `--all-prompts` — same but emits prompts for every candidate with `=== PROMPT N ===` markers.

The script itself never mutates `docs/XYZ_PILOT_ISSUE_TRACKER.md` — only the
subagent does, in line with the single-writer gatekeeper directive. Pre-flight
steps are read + snapshot only, safe to re-run.

### Candidate outcome markers (2026-06-03 directive)

Every accepted or denied candidate gets a durable marker written by
`XYZ_MASTER` under the source folder:

```
tests/logs/runs/<run-id>/<source>/candidate-outcomes/
  candidate-001.ACCEPTED.MINOR.XYZ-PILOT-211.md
  candidate-002.ACCEPTED.FEATURE.XYZ-PILOT-212.md
  candidate-003.DENIED.DUPLICATE.XYZ-PILOT-189.md
  candidate-004.DENIED.WONTFIX.md
```

The marker is separate from `issue-candidates.md`; never edit the candidate
body to record outcome state. Accepted markers cite the assigned
`XYZ-PILOT-NNN`, classification, tracker status, decision timestamp, and commit
reference (`same-commit` if staged with the tracker mutation). Denied markers
record the denial reason and, for duplicates, the existing tracker ID.

Use `scripts/write_candidate_outcome.py` to create markers. The formal filing
entry point detects these files: `--list` displays candidates as `PENDING` or
`DECIDED`, and `--prompt N` refuses to re-drain a candidate that already has an
outcome marker. This is the immediate visual/auditable signal that a candidate
was accepted, duplicated, wontfixed, merged, or otherwise denied.

Operator workflow per filing:
1. `tests/scripts/run_xyz_master_filing.sh <run-id> --list` to confirm count.
2. For each candidate: `--prompt N` → paste output into the Agent tool.
3. Review the subagent's verifier output, tracker diff, archive path, and
   proposed outcome marker. Approve continuation only when all paths are
   correct.
4. After the approved commit/push, re-run `--list` to confirm the candidate is
   marked `DECIDED` and no duplicate prompt can be generated.

### Severity defaults (2026-05-30 directive)

Standard severity for any newly-filed `XYZ-PILOT-NNN` is **`minor`** or
**`feature`** — file directly. Severity **`major`** or **`critical`**
requires explicit operator (Thomas D.) confirmation BEFORE the XYZ_MASTER
filing pass runs. If a candidate file proposes `major`/`critical` without
documented operator approval, XYZ_MASTER should REFUSE the filing,
report back, and ask the operator to confirm or downgrade. The goal is
to keep `major`/`critical` credible — only true urgent items.

Existing-ticket severity stays as filed unless the operator explicitly
downgrades.

### Candidate-file placeholder format (2026-06-08 directive — XYZ-PILOT-215 follow-up)

The deterministic filer `scripts/xyz_master_background_filing.py` and the
interactive XYZ_MASTER subagent both consume candidate files under
`tests/logs/runs/<UTC-run-id>/<source>/issue-candidates.md`. The filer
parses two structured placeholders that MUST be present in their **raw
template form** — strike-through-wrapped or pre-substituted forms are
rejected at parse time with `ERROR: top-table row section does not
contain a placeholder row` or `ERROR: full-register detail section must
contain XYZ-PILOT-NNN placeholders`.

**Status-transition candidates (e.g. → Resolved/Wontfix) — submission discipline
(XYZ-PILOT-259).** A status-transition candidate MUST carry: (a) `**Mutation kind:**
status-transition`; (b) `**Existing ID:** XYZ-PILOT-NNN` (the parsers also accept the
alias `**Existing issue:**`, but `Existing ID` is canonical); (c) `**Proposed
status:** Resolved` (or `Wontfix`); and (d) a **`### Resolution evidence`** section
(`###`-level; the filer also accepts `### Resolution details` / `### Implementation
and validation`, and tolerates Codex's `#### Summary` / `#### Implementation` /
`#### Validation`) carrying the closure prose that becomes the tracker
`**Status:**` evidence. Omitting the evidence section ⇒ `ERROR: status-transition
candidate is missing a resolution evidence/details section`; a missing/garbled
`Existing ID` ⇒ the candidate is mis-classified as a major **new-ticket** and the
dashboard direct-main gate wrongly demands PR mode. Status/severity transitions are
accepted via `direct-main` regardless of severity — only **new** major/critical
tickets require PR mode.

**Required placeholder forms:**

| Section | Required | Rejected |
|---|---|---|
| `### Top-table row` | `\| [XYZ-PILOT-NNN](#xyz-pilot-NNN) \| <severity> \| <area> \| <summary> \|` (pipe-delimited row, raw `XYZ-PILOT-NNN` literal, NO strike-through, NO blockquote `>` prefix) | `> ~~[XYZ-PILOT-NNN]~~ \| ~~minor~~ \| ...` (strike-wrapped, blockquoted) |
| `### Full-register detail section` | Fenced ` ```markdown ... ``` ` block containing `<a id="xyz-pilot-NNN"></a>` anchor + `### XYZ-PILOT-NNN — <title>` heading | Pre-substituted concrete IDs like `id="xyz-pilot-221"` |

**Why raw form, not strike form:** the filer assigns the next-free
`XYZ-PILOT-NNN` integer at file-time AND applies strike-through styling
automatically when the proposed status is terminal (`Resolved
YYYY-MM-DD` / `Wontfix YYYY-MM-DD`). The strike form is the OUTPUT
convention, not the input. Writing strike-wrapped placeholders in the
candidate file is a category error — you're declaring presentation
before the filer has assigned the ID.

**Status-handling reminder.** The candidate's `**Proposed status:**`
field determines whether the filer applies the strike-through styling
to ID + Severity + Area cells in the top-table row and appends the
`(Resolved YYYY-MM-DD)` suffix to the Summary cell. Authors don't write
the strike themselves; they declare `Proposed status: Resolved
2026-06-08` and let the filer do the styling. Same for `Wontfix`.

**Canonical placeholder snippet** to copy-paste into new candidate
files:

```markdown
### Top-table row

| [XYZ-PILOT-NNN](#xyz-pilot-NNN) | <severity> | <area> | <summary including any (Resolved YYYY-MM-DD) marker for terminal-status filings> |

### Full-register detail section

​```markdown
<a id="xyz-pilot-NNN"></a>

### XYZ-PILOT-NNN — <title matching the area>
- **Status:** <Open | Resolved YYYY-MM-DD | Workaround | Wontfix YYYY-MM-DD>
- **Severity:** <minor | major | critical | feature | external>
- **Area:** <area>
- **Surfaced:** <YYYY-MM-DD by <who> at <when/where>>

#### Problem

<...prose...>

#### Resolution      (only for Resolved at file-time)
#### Validation      (REPLACED content for Open→Resolved transitions; NEW for Resolved at file-time)
#### Recurrence guard
#### Dependencies / cross-links
​```
```

(The inner ` ```markdown ``` ` fence is what the filer's
`extract_detail_template()` matches; do not omit it.)

**Provenance.** Operator-codified 2026-06-08 after the XYZ-PILOT-221
filing surfaced the gap — 4 CI dispatches failed before the placeholder
form was diagnosed from the parser error message. Defect candidates
queued same day under run `2026-06-08T150612Z` (Operator Dashboard JS
defect) + `2026-06-08T150613Z` (this documentation gap, which lands
in the tracker as a Resolved-at-file-time once drained because the
docs fix is shipped in the commit that introduces this section).

### Open → Resolved transition discipline (2026-05-30/31 directive)

When a candidate file proposes flipping an existing ticket's status from `Open`
to `Resolved YYYY-MM-DD`, two recurring failure modes have been observed.
Apply both guardrails on every transition pass:

**1. Duplicate `#### Validation` subsection (XYZ-PILOT-167 case study).** Most
Open-state detail sections include a `#### Validation` subsection containing a
**prospective** plan ("Resolution will be validated by: ..."). Resolved
candidates almost always want to record **retrospective evidence** (smoke
tests, deploy verifications, dashboard screenshots). The wrong move is to
**append a second `#### Validation` subsection** — the verifier `(I)`
duplicate-subheading check catches it, but only after the edit has been
applied. The right move is to **REPLACE the existing `#### Validation`
subsection's content** with retrospective evidence while keeping the heading.
If the prospective bullets contain operationally-useful forward-looking
observation (e.g. "monitor X for the next 7 days") absorb them as the trailing
bullets of the retrospective section. XYZ-PILOT-167's 2026-05-30 resolution had
to be remediated mid-pass when its retrospective `#### Validation` collided
with the original prospective one — the lesson is to write candidates
explicitly as REPLACE, not APPEND, and to flag the (I) avoidance instruction
prominently in the candidate file's preamble.

**2. Subsection ordering (XYZ-PILOT-168 case study).** The canonical reading flow
for a Resolved detail section is:

```
#### Problem
#### Why this matters         (optional, common for process tickets)
#### Proposed resolution paths (optional, common for Open-then-resolved)
#### Resolution               (NEW — what was actually shipped)
#### Validation               (REPLACED content — retrospective evidence)
#### Recurrence guard         (NEW or REPLACED — how to prevent re-occurrence)
#### Dependencies / cross-links
```

If a candidate file presents the new `#### Resolution` block with embedded
`#### Recurrence guard` content textually adjacent inside the same narrative,
the XYZ_MASTER subagent may interpret them as "replace the existing
`#### Recurrence guard` in-place" — which puts `#### Recurrence guard`
BEFORE `#### Validation` (the order they appeared in the Open ticket), not
AFTER as the natural reading flow expects. Two ways to prevent this:

- **Candidate-side:** list the subsections in the candidate file with explicit
  positional instructions ("Insert NEW `#### Resolution` immediately AFTER
  `#### Proposed resolution paths`. REPLACE existing `#### Validation` content
  in-place. REPLACE existing `#### Recurrence guard` content in-place, keeping
  its position AFTER `#### Validation`.").
- **XYZ_MASTER-side:** after applying mutations, before committing, verify
  the subsection order against the canonical reading flow. If `#### Validation`
  comes after `#### Recurrence guard`, swap them in a follow-up edit.

XYZ-PILOT-167's resolution lands the canonical order cleanly; XYZ-PILOT-168's
resolution shipped with an inverted Validation/Recurrence guard order and
required a follow-up commit `6c3e6fd` to swap them. The verifier passed in
both cases (subsection order is not semantically checked), but cross-ticket
consistency matters for reading.

### What XYZ_MASTER looks for

- candidate duplication against the central tracker;
- issue severity drift across agents (now stricter: refuse `major`/`critical`
  without documented operator approval);
- evidence quality and reproducibility gaps;
- local/remote tracker state mismatch;
- documentation and runbook gaps that prevent clean handoff.
- unsafe AWS permission, SSM, or CloudWatch evidence handling in candidate
  reports.

No Chatbot candidate-submit path is used by this harness because the current
duplicate-check implementation can proceed to emit a repository candidate file
when a candidate is new.
