# Candidate handoff

Source: Claude Code session, PR #13 (`feat/brand-skill-bootstrap`).
Scope: exactly one new ticket. Severity `minor` per CLAUDE.md Rule #5 default.

## Candidate - File new ticket: Brand document skill installed at runtime, kept out of the public template

- **Proposed severity:** `minor`
- **Proposed area:** `Template · brand assets`
- **Proposed status:** `Open`

### Top-table row

| [XYZ-PILOT-NNN](#xyz-pilot-NNN) | minor | Template · brand assets | Keep Exasol brand assets out of the public template and generated repos; install the branded-document skill at runtime from an internal repository. |

### Full-register detail section

```markdown
<a id="xyz-pilot-NNN"></a>

### XYZ-PILOT-NNN - Brand document skill installed at runtime, kept out of the public template
- **Status:** Open
- **Severity:** minor
- **Area:** Template · brand assets
- **Surfaced:** 2026-11-05 by Claude Code while adding the `exasol-branded-document` skill to the template (PR #13).

#### Problem

The `exasol-branded-document` skill renders Exasol house-style PDF and DOCX. It bundles the official letterhead, logo extracts and the brand slide template, which have no distribution licence. This template repository is public, and repositories generated from it are not necessarily private, so the skill cannot be committed here. A second leak path existed: `generate.sh` copies the working copy with `rsync -a`, so a skill installed locally under the gitignored `.claude/` would have been copied into every generated repository.

#### Proposed resolution paths

- PR #13 adds `scripts/install_brand_skill.sh`, which clones the skill from the internal repository `exasol/exasol-branded-document` into the gitignored `.claude/skills/` and refuses to install if that path stops being ignored. Non-members get a clear message and exit 1.
- `generate.sh` excludes `.claude/` from the generator rsync.
- `scripts/check_no_brand_assets.py` (with `--self-test`) fails the repo-hygiene workflow when a brand asset or the skill reaches a tracked path.
- `CLAUDE.md` documents the install path and the reason.
- `LICENSE` is rewritten as a source-available notice that matches the repository's public visibility.

#### Validation

- Blocked: `exasol/exasol-branded-document` does not exist yet; creating an internal repository in the `exasol` org needs an org owner.
- After it exists: run `./scripts/install_brand_skill.sh`, then `python3 .claude/skills/exasol-branded-document/scripts/test_identity.py`, and confirm `./generate --dry-run` leaves `.claude/` out of the target.

#### Recurrence guard

`scripts/check_no_brand_assets.py` in `.github/workflows/repo-hygiene.yml`.

#### Dependencies / cross-links

- PR #13 `feat/brand-skill-bootstrap`.
- Follow-up: reword `ACCESS.md`, `NOTICE`, `COPYRIGHT.md` and `clients/XYZ/README.md`, which still describe repository contents as confidential; `LICENSE` section 8 gives `LICENSE` precedence until then.
- Exasol Legal review of the rewritten `LICENSE` before customer-facing reliance.
```
