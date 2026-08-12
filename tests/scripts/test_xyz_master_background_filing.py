from __future__ import annotations

import datetime as dt
import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "xyz_master_background_filing",
    ROOT / "scripts" / "xyz_master_background_filing.py",
)
assert SPEC and SPEC.loader
mod = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = mod
SPEC.loader.exec_module(mod)


TRACKER_FIXTURE = """# XYZ Pilot - Issue / Defect Tracker / Backlog

**Last refresh:** 2026-06-03 (fixture).

## Active issues - one-line lookup, newest-ID first

| ID | Severity | Area | Summary |
|---|---|---|---|
| [XYZ-PILOT-001](#xyz-pilot-001) | minor | Existing | Existing issue. |

---

## Full register (newest first)

<a id="xyz-pilot-001"></a>

### XYZ-PILOT-001 - Existing issue
- **Status:** Open
- **Severity:** minor
- **Area:** Existing
- **Surfaced:** Test.

#### Problem

Existing.
"""


CANDIDATE_FIXTURE = """# Candidate handoff

## Candidate - File new feature: Background service

- **Proposed severity:** `feature`
- **Proposed area:** `Automation`
- **Proposed status:** `Open`

### Top-table row

> [XYZ-PILOT-NNN](#xyz-pilot-NNN) | feature | Automation | Add a background service.

### Full-register detail section

```markdown
<a id="xyz-pilot-NNN"></a>

### XYZ-PILOT-NNN - Background service
- **Status:** Open
- **Severity:** feature
- **Area:** Automation
- **Surfaced:** Test.

#### Problem

Manual filing is expensive.
```
"""


# The terminal date must sit INSIDE the one-business-day visibility window or
# the filer legitimately prunes the row instead of striking it (XYZ-PILOT-338),
# and this test can never exercise the strike path. A hardcoded date goes stale.
_TERM = dt.date.today().isoformat()

STATUS_TRANSITION_FIXTURE = f"""# Candidate handoff

## Candidate - Status transition: XYZ-PILOT-001 -> Resolved {_TERM}

- **Existing ticket:** [XYZ-PILOT-001](../../../../docs/XYZ_PILOT_ISSUE_TRACKER.md#xyz-pilot-001)
- **Requested status:** Resolved {_TERM}
- **Severity:** minor

### Resolution evidence

Implemented the fix and validated the result.

- `python3 -m unittest`
- deployed smoke test passed

### Validation after XYZ_MASTER files

- verifier exits 0
"""


WONTFIX_TRANSITION_FIXTURE = STATUS_TRANSITION_FIXTURE.replace(
    f"Resolved {_TERM}",
    f"Wontfix {_TERM}",
).replace(
    "Implemented the fix and validated the result.",
    "Operator accepted the Wontfix decision after duplicate triage.",
)


SEVERITY_TRANSITION_FIXTURE = """# Candidate handoff

## Candidate - Severity transition: XYZ-PILOT-001 -> major

- **Existing ticket:** [XYZ-PILOT-001](../../../../docs/XYZ_PILOT_ISSUE_TRACKER.md#xyz-pilot-001)
- **Requested severity:** major
- **Severity rationale:** Recurring operator-visible workflow failure should page the operator-only major-defect notification lane.
"""


EVIDENCE_UPDATE_FIXTURE = """# Candidate handoff

## Candidate - Evidence update: XYZ-PILOT-001

- **Existing ticket:** [XYZ-PILOT-001](../../../../docs/XYZ_PILOT_ISSUE_TRACKER.md#xyz-pilot-001)
- **Severity:** minor
- **Evidence date:** 2026-06-20

### Evidence update

Fresh validation showed the same runtime class still needs attention.

- `V_MONTHLY_METRICS` timed out after 120 seconds.
"""


LIGHTWEIGHT_FIXTURE = """# xyz_stress Issue Candidates

This file is evidence for `XYZ_MASTER`; it is not a tracker edit.

## Candidate: Concurrent QBR/chatbot/Exasol stress threshold exceeded

- **Agent:** `xyz_stress`
- **Run:** `2026-06-04T121444Z`
- **Type:** defect
- **Severity:** minor pending operator approval to promote to major
- **Area:** Streamlit QBR · Chatbot · Exasol Personal · concurrent UAT readiness
- **Observed:** Bounded stress probe exceeded the error-rate threshold.
- **Expected:** The approved twenty-user stress run should remain below the error-rate threshold.
- **Evidence:** `tests/logs/runs/2026-06-04T121444Z/xyz_stress/artifacts/stress_summary.md`.
"""


PLACEHOLDER_FIXTURE = """# xyz_smoke Issue Candidates

This file is evidence for `XYZ_MASTER`; it is not a tracker edit.
"""


class BackgroundFilingTests(unittest.TestCase):
    def test_build_mutation_assigns_next_id_and_inserts_sections(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            candidate_file = Path(tmp) / "issue-candidates.md"
            candidate_file.write_text(CANDIDATE_FIXTURE, encoding="utf-8")

            candidate = mod.parse_candidates(candidate_file)[0]
            payload = mod.extract_payload(candidate)
            mutation = mod.build_mutation(TRACKER_FIXTURE, payload)

        self.assertEqual(mutation.tracker_id, "XYZ-PILOT-002")
        self.assertIn("| [XYZ-PILOT-002](#xyz-pilot-002) | feature | Automation |", mutation.tracker_text)
        self.assertIn('<a id="xyz-pilot-002"></a>', mutation.tracker_text)
        self.assertIn("### XYZ-PILOT-002 - Background service", mutation.tracker_text)
        self.assertTrue(
            mutation.tracker_text.index("XYZ-PILOT-002")
            < mutation.tracker_text.index("XYZ-PILOT-001")
        )

    def test_outcome_marker_blocks_reprocessing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            candidate_dir = root / "tests" / "logs" / "runs" / "run" / "SRC"
            marker_dir = candidate_dir / "candidate-outcomes"
            marker_dir.mkdir(parents=True)
            candidate_file = candidate_dir / "issue-candidates.md"
            candidate_file.write_text(CANDIDATE_FIXTURE, encoding="utf-8")
            (marker_dir / "candidate-001.ACCEPTED.FEATURE.XYZ-PILOT-002.md").write_text(
                "decided\n",
                encoding="utf-8",
            )

            args = type(
                "Args",
                (),
                {
                    "run_id": "run",
                    "source": "SRC",
                    "candidate_file": None,
                    "candidate_index": 1,
                },
            )()

            with self.assertRaises(mod.FilingError):
                mod.selected_candidate(args, root)

    def test_pending_dispatch_marker_does_not_block(self) -> None:
        # XYZ-PILOT-286: a non-terminal APPROVED_DISPATCHED marker (written by the
        # Operator Dashboard dispatch BEFORE the filer runs) must NOT trip the
        # double-file guard, otherwise the candidate ends approved-but-unfiled.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            candidate_dir = root / "tests" / "logs" / "runs" / "run" / "SRC"
            marker_dir = candidate_dir / "candidate-outcomes"
            marker_dir.mkdir(parents=True)
            candidate_file = candidate_dir / "issue-candidates.md"
            candidate_file.write_text(CANDIDATE_FIXTURE, encoding="utf-8")
            (marker_dir / "candidate-001.APPROVED_DISPATCHED.DIRECT_MAIN.md").write_text(
                "approved-dispatched\n",
                encoding="utf-8",
            )

            args = type(
                "Args",
                (),
                {
                    "run_id": "run",
                    "source": "SRC",
                    "candidate_file": None,
                    "candidate_index": 1,
                },
            )()

            # must NOT raise — filing proceeds past the pending dispatch marker
            candidate = mod.selected_candidate(args, root)
            self.assertEqual(candidate.local_index, 1)
            # and the marker is correctly classified non-terminal
            self.assertEqual(
                mod.terminal_outcome_markers(candidate_file, 1), []
            )
            self.assertEqual(
                len(mod.pending_dispatch_markers(candidate_file, 1)), 1
            )

    def test_high_severity_requires_operator_approval_in_apply_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            candidate_file = Path(tmp) / "issue-candidates.md"
            candidate_file.write_text(
                CANDIDATE_FIXTURE.replace("`feature`", "`major`", 1),
                encoding="utf-8",
            )
            candidate = mod.parse_candidates(candidate_file)[0]
            payload = mod.extract_payload(candidate)

        self.assertEqual(payload.classification, "major")
        self.assertIn(payload.classification, mod.HIGH_SEVERITY)

    def test_classification_override_updates_top_row_and_detail(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            candidate_file = Path(tmp) / "issue-candidates.md"
            candidate_file.write_text(CANDIDATE_FIXTURE, encoding="utf-8")

            candidate = mod.parse_candidates(candidate_file)[0]
            payload = mod.override_classification(mod.extract_payload(candidate), "major")
            mutation = mod.build_mutation(TRACKER_FIXTURE, payload)

        self.assertEqual(payload.classification, "major")
        self.assertIn("| [XYZ-PILOT-002](#xyz-pilot-002) | major | Automation |", mutation.tracker_text)
        self.assertIn("- **Severity:** major", mutation.tracker_text)

    def test_status_transition_updates_existing_row_and_detail(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            candidate_file = Path(tmp) / "issue-candidates.md"
            candidate_file.write_text(STATUS_TRANSITION_FIXTURE, encoding="utf-8")

            candidate = mod.parse_candidates(candidate_file)[0]
            mutation = mod.build_status_transition_mutation(TRACKER_FIXTURE, candidate, None)

        self.assertEqual(mutation.mutation_kind, "status-transition")
        self.assertEqual(mutation.tracker_id, "XYZ-PILOT-001")
        self.assertIn(
            f"| ~~[XYZ-PILOT-001](#xyz-pilot-001)~~ | ~~minor~~ | ~~Existing~~ | Existing issue. (Resolved {_TERM}) |",
            mutation.tracker_text,
        )
        self.assertIn(f"- **Status:** Resolved {_TERM}", mutation.tracker_text)
        self.assertIn(f"#### Resolution - shipped {_TERM}", mutation.tracker_text)
        self.assertIn("Implemented the fix and validated the result.", mutation.tracker_text)
        self.assertNotIn("Validation after XYZ_MASTER files", mutation.tracker_text)

    def test_wontfix_status_transition_uses_decision_subheading(self) -> None:
        tracker_text = TRACKER_FIXTURE.replace(
            "Existing.\n",
            "Existing.\n\n#### Resolution - shipped 2026-06-03\n\nA prior shipped resolution exists.\n",
        )
        with tempfile.TemporaryDirectory() as tmp:
            candidate_file = Path(tmp) / "issue-candidates.md"
            candidate_file.write_text(WONTFIX_TRANSITION_FIXTURE, encoding="utf-8")

            candidate = mod.parse_candidates(candidate_file)[0]
            mutation = mod.build_status_transition_mutation(tracker_text, candidate, None)

        self.assertEqual(mutation.mutation_kind, "status-transition")
        self.assertIn(f"- **Status:** Wontfix {_TERM}", mutation.tracker_text)
        self.assertIn(f"#### Wontfix - decision {_TERM}", mutation.tracker_text)
        self.assertIn("Operator accepted the Wontfix decision", mutation.tracker_text)
        self.assertEqual(mutation.tracker_text.count("#### Resolution - shipped 2026-06-03"), 1)

    def test_status_transition_accepts_bold_only_status_field(self) -> None:
        tracker_text = TRACKER_FIXTURE.replace("- **Status:** Open", "**Status:** Open")
        with tempfile.TemporaryDirectory() as tmp:
            candidate_file = Path(tmp) / "issue-candidates.md"
            candidate_file.write_text(STATUS_TRANSITION_FIXTURE, encoding="utf-8")

            candidate = mod.parse_candidates(candidate_file)[0]
            mutation = mod.build_status_transition_mutation(tracker_text, candidate, None)

        self.assertEqual(mutation.mutation_kind, "status-transition")
        self.assertIn(f"**Status:** Resolved {_TERM}", mutation.tracker_text)
        self.assertNotIn(f"- **Status:** Resolved {_TERM}", mutation.tracker_text)
        self.assertIn(f"#### Resolution - shipped {_TERM}", mutation.tracker_text)

    def test_severity_transition_updates_existing_row_and_detail(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            candidate_file = Path(tmp) / "issue-candidates.md"
            candidate_file.write_text(SEVERITY_TRANSITION_FIXTURE, encoding="utf-8")

            candidate = mod.parse_candidates(candidate_file)[0]
            mutation = mod.build_severity_transition_mutation(TRACKER_FIXTURE, candidate, None)

        self.assertEqual(mutation.mutation_kind, "severity-transition")
        self.assertEqual(mutation.tracker_id, "XYZ-PILOT-001")
        self.assertEqual(mutation.classification, "major")
        self.assertIn(
            "| [XYZ-PILOT-001](#xyz-pilot-001) | major | Existing | Existing issue. |",
            mutation.tracker_text,
        )
        self.assertIn("- **Severity:** major", mutation.tracker_text)
        self.assertIn("#### Severity transition - approved", mutation.tracker_text)
        self.assertIn("- **Previous severity:** minor", mutation.tracker_text)
        self.assertIn("- **New severity:** major", mutation.tracker_text)

    def test_evidence_update_appends_existing_issue_without_status_change(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            candidate_file = Path(tmp) / "issue-candidates.md"
            candidate_file.write_text(EVIDENCE_UPDATE_FIXTURE, encoding="utf-8")

            candidate = mod.parse_candidates(candidate_file)[0]
            self.assertTrue(mod.is_evidence_update_candidate(candidate))
            mutation = mod.build_evidence_update_mutation(TRACKER_FIXTURE, candidate, None)

        self.assertEqual(mutation.mutation_kind, "evidence-update")
        self.assertEqual(mutation.tracker_id, "XYZ-PILOT-001")
        self.assertIn("- **Status:** Open", mutation.tracker_text)
        self.assertIn("#### Evidence update - 2026-06-20", mutation.tracker_text)
        self.assertIn("`V_MONTHLY_METRICS` timed out after 120 seconds.", mutation.tracker_text)
        self.assertIn("background XYZ_MASTER evidence update", mutation.tracker_text)

    def test_empty_placeholder_file_has_no_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            candidate_file = Path(tmp) / "issue-candidates.md"
            candidate_file.write_text(PLACEHOLDER_FIXTURE, encoding="utf-8")

            self.assertEqual(mod.parse_candidates(candidate_file), [])

    def test_lightweight_swarm_candidate_is_normalized_to_tracker_payload(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            candidate_dir = root / "tests" / "logs" / "runs" / "2026-06-04T121444Z" / "xyz_stress"
            candidate_dir.mkdir(parents=True)
            candidate_file = candidate_dir / "issue-candidates.md"
            candidate_file.write_text(LIGHTWEIGHT_FIXTURE, encoding="utf-8")

            candidate = mod.parse_candidates(candidate_file)[0]
            payload = mod.extract_payload(candidate)
            mutation = mod.build_mutation(TRACKER_FIXTURE, payload)

        self.assertEqual(payload.classification, "minor")
        self.assertEqual(payload.status, "Open")
        self.assertIn("| [XYZ-PILOT-002](#xyz-pilot-002) | minor | Streamlit QBR", mutation.tracker_text)
        self.assertIn("### XYZ-PILOT-002 - Concurrent QBR/chatbot/Exasol stress threshold exceeded", mutation.tracker_text)
        self.assertIn("#### Candidate details", mutation.tracker_text)
        self.assertIn("- **Observed:** Bounded stress probe exceeded the error-rate threshold.", mutation.tracker_text)

    def test_full_template_candidate_accepts_status_field_fallback(self) -> None:
        candidate_text = CANDIDATE_FIXTURE.replace("- **Proposed status:** `Open`\n", "- **Status:** `Open`\n")
        with tempfile.TemporaryDirectory() as tmp:
            candidate_file = Path(tmp) / "issue-candidates.md"
            candidate_file.write_text(candidate_text, encoding="utf-8")

            candidate = mod.parse_candidates(candidate_file)[0]
            payload = mod.extract_payload(candidate)

        self.assertEqual(payload.status, "Open")

    def test_duplicate_title_returns_already_filed_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            candidate_file = Path(tmp) / "issue-candidates.md"
            candidate_file.write_text(CANDIDATE_FIXTURE, encoding="utf-8")

            candidate = mod.parse_candidates(candidate_file)[0]
            payload = mod.extract_payload(candidate)
            tracker_text = TRACKER_FIXTURE.replace(
                "### XYZ-PILOT-001 - Existing issue",
                "### XYZ-PILOT-001 - Background service",
            )
            mutation = mod.build_mutation(tracker_text, payload)

        self.assertEqual(mutation.mutation_kind, "already-filed")
        self.assertEqual(mutation.tracker_id, "XYZ-PILOT-001")
        self.assertEqual(mutation.tracker_text, tracker_text)

    def test_lightweight_resolved_status_generates_terminal_new_ticket_row(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            candidate_file = Path(tmp) / "issue-candidates.md"
            candidate_file.write_text(
                """## Candidate: Dashboard approval parser fixed

- **Agent:** `codex`
- **Run:** `2026-06-04T130000Z`
- **Severity:** minor
- **Area:** Operator Dashboard
- **Status:** resolved by current Codex change set
- **Observed:** Placeholder files appeared as candidates.
""",
                encoding="utf-8",
            )

            candidate = mod.parse_candidates(candidate_file)[0]
            payload = mod.extract_payload(candidate)
            mutation = mod.build_mutation(TRACKER_FIXTURE, payload)

        today = mod.dt.datetime.now(mod.dt.UTC).date().isoformat()
        self.assertEqual(payload.status, f"Resolved {today}")
        self.assertIn(f"(Resolved {today})", mutation.top_row)
        self.assertIn("| ~~[XYZ-PILOT-002](#xyz-pilot-002)~~ | ~~minor~~ | ~~Operator Dashboard~~ |", mutation.top_row)


if __name__ == "__main__":
    unittest.main()
