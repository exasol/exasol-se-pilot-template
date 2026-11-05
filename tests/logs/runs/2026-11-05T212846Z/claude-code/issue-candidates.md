# Candidate handoff

Source: Claude Code session, PR #13 (`feat/brand-skill-bootstrap`).
Scope: exactly one new ticket, resolved at file time: the fix ships in the same PR.

## Candidate - File new ticket: Generated pilots inherit the template's own tracker

- **Proposed severity:** `minor`
- **Proposed area:** `Template · generator`
- **Proposed status:** `Resolved 2026-11-05`

### Top-table row

| [XYZ-PILOT-NNN](#xyz-pilot-NNN) | minor | Template · generator | Generated pilots inherit the template's own tracker tickets and tracker snapshots. (Resolved 2026-11-05) |

### Full-register detail section

```markdown
<a id="xyz-pilot-NNN"></a>

### XYZ-PILOT-NNN - Generated pilots inherit the template's own tracker
- **Status:** Resolved 2026-11-05
- **Severity:** minor
- **Area:** Template · generator
- **Surfaced:** 2026-11-05 by Claude Code while filing XYZ-PILOT-002 (PR #13).

#### Problem

`generate.sh` copied `docs/XYZ_PILOT_ISSUE_TRACKER.md` and every `docs/archive/XYZ_PILOT_ISSUE_TRACKER_*` snapshot into each new pilot. Once the template's tracker started recording template work (XYZ-PILOT-002), every generated pilot would have opened with template tickets and lost the `<TRACKER_TODAY_DATE>` token that the filer had replaced with a fixed date.

#### Resolution

- New seed file `config/tracker.seed.md`: the bootstrap tracker with only XYZ-PILOT-001, the unrendered date tokens and the canonical `## Full register (newest first)` heading.
- `generate.sh` excludes `config/tracker.seed.md` and `docs/archive/XYZ_PILOT_ISSUE_TRACKER_*` from the rsync and copies the seed over the target's `docs/XYZ_PILOT_ISSUE_TRACKER.md` before placeholders are rendered.
- `README.md` states that the template's tracker records template work and that new pilots start from the seed.

#### Validation

- Fresh `./generate --project-id gcm` into a scratch directory: the generated tracker holds only GCM-PILOT-001, shows the generation date, uses the lowercase Full-register heading; no template tracker snapshots in `docs/archive/`; `verify_tracker_completeness.sh` and `validate_template_neutrality.sh` pass in the generated repository.
- Template repository: verifier and neutrality check pass.

#### Recurrence guard

The seed file is the only tracker source for generated pilots; template tickets can no longer leak through the generator copy.

#### Dependencies / cross-links

- XYZ-PILOT-002 (first template-work ticket, which surfaced this).
- PR #13 `feat/brand-skill-bootstrap`.
```
