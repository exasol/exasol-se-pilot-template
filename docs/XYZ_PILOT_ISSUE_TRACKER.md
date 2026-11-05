# Pilot Issue Tracker

**Last refresh:** 2026-11-05 (XYZ-PILOT-002 background XYZ_MASTER tracker mutation service filed Open; verifier clean).

This tracker is the single source of truth for project scope, defects, feature
requests, operational status items, and documentation discipline.

## Active issues

> **Visibility rule:** Resolved items remain visible briefly, then are pruned
> from this top table while their full register entries stay permanent.
>
> *Today is <TRACKER_TODAY>; cut-off date is <TRACKER_CUTOFF>.*

| ID | Severity | Area | Summary |
|---|---|---|---|
| [XYZ-PILOT-002](#xyz-pilot-002) | minor | Template · brand assets | Keep Exasol brand assets out of the public template and generated repos; install the branded-document skill at runtime from an internal repository. |
| [XYZ-PILOT-001](#xyz-pilot-001) | feature | Template · project bootstrap | Initialize the generated pilot repository and replace placeholders with project-specific configuration. |

---

## Full register (newest first)

<a id="xyz-pilot-002"></a>

### XYZ-PILOT-002 - Brand document skill installed at runtime, kept out of the public template
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

<a id="xyz-pilot-001"></a>

### XYZ-PILOT-001 - Generated pilot repository bootstrap

- **Status:** Open
- **Severity:** feature
- **Area:** Template · project bootstrap
- **Surfaced:** <YYYY-MM-DD> by template generator

#### Summary

The generated pilot repository has been created from the Exasol SE pilot
template. The operator must provide customer/project-specific values, confirm
the AWS account/region/AZ binding, and decide whether to run the protected
deployment steps.

#### Acceptance

- Placeholder values reviewed.
- `config/project.env` populated from `config/project.env.example`.
- `./scripts/validate_template_neutrality.sh` passes.
- `./scripts/verify_tracker_completeness.sh` passes.
- Deployment remains dry-run until explicitly approved.
