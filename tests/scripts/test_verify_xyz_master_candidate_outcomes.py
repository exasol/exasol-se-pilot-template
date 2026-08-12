#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "verify_xyz_master_candidate_outcomes",
    ROOT / "scripts" / "verify_xyz_master_candidate_outcomes.py",
)
assert SPEC and SPEC.loader
mod = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = mod
SPEC.loader.exec_module(mod)


TRACKER = """# Tracker

<a id="xyz-pilot-001"></a>

### XYZ-PILOT-001 - First
- **Status:** Resolved 2026-06-20

<a id="xyz-pilot-002"></a>

### XYZ-PILOT-002 - Second
**Status:** Open
"""


CANDIDATES = """# Candidate handoff

## Candidate - Status transition: XYZ-PILOT-001 -> Resolved 2026-06-20

- **Existing ticket:** [XYZ-PILOT-001](../../../../docs/XYZ_PILOT_ISSUE_TRACKER.md#xyz-pilot-001)
- **Requested status:** Resolved 2026-06-20
- **Severity:** minor

### Resolution evidence

Done.

## Candidate - Status transition: XYZ-PILOT-002 -> Resolved 2026-06-20

- **Existing ticket:** [XYZ-PILOT-002](../../../../docs/XYZ_PILOT_ISSUE_TRACKER.md#xyz-pilot-002)
- **Requested status:** Resolved 2026-06-20
- **Severity:** minor

### Resolution evidence

Done.
"""


def accepted_marker(index: int, tracker_id: str, status: str) -> str:
    return f"""---
candidate_index: {index}
source_file: "tests/logs/runs/example/OPENAI_CODEX/issue-candidates.md"
outcome: "accepted"
decision_by: "XYZ_MASTER"
decision_at_utc: "2026-06-20T13:55:31Z"
commit: "same-commit"
title: "Example"
classification: "minor"
tracker_id: "{tracker_id}"
tracker_status: "{status}"
---

# Candidate outcome: ACCEPTED -> {tracker_id}
"""


class VerifyHpcMasterCandidateOutcomesTests(unittest.TestCase):
    def _fixture(self) -> tuple[tempfile.TemporaryDirectory[str], Path, Path]:
        tmp = tempfile.TemporaryDirectory()
        root = Path(tmp.name)
        candidate_dir = root / "tests/logs/runs/example/OPENAI_CODEX"
        candidate_dir.mkdir(parents=True)
        candidate_file = candidate_dir / "issue-candidates.md"
        candidate_file.write_text(CANDIDATES, encoding="utf-8")
        tracker = root / "docs/XYZ_PILOT_ISSUE_TRACKER.md"
        tracker.parent.mkdir(parents=True)
        tracker.write_text(TRACKER, encoding="utf-8")
        return tmp, candidate_file, tracker

    def test_current_candidate_requires_own_outcome_marker(self) -> None:
        tmp, candidate_file, tracker = self._fixture()
        self.addCleanup(tmp.cleanup)

        result = mod.validate_outcomes(candidate_file, tracker, candidate_index=2)

        self.assertEqual((2,), result.checked_indexes)
        self.assertIn("candidate 002 expected exactly one outcome marker, found 0", result.errors[0])

    def test_missing_sibling_is_reported_as_warning_not_current_failure(self) -> None:
        tmp, candidate_file, tracker = self._fixture()
        self.addCleanup(tmp.cleanup)
        marker_dir = candidate_file.parent / "candidate-outcomes"
        marker_dir.mkdir()
        (marker_dir / "candidate-001.ACCEPTED.MINOR.XYZ-PILOT-001.md").write_text(
            accepted_marker(1, "XYZ-PILOT-001", "Resolved 2026-06-20"),
            encoding="utf-8",
        )

        result = mod.validate_outcomes(
            candidate_file,
            tracker,
            candidate_index=1,
            warn_missing_siblings=True,
        )

        self.assertEqual((), result.errors)
        self.assertEqual((2,), result.missing_indexes)
        self.assertIn("candidate 002 has no durable outcome marker", result.warnings[0])

    def test_status_transition_marker_must_match_tracker_status(self) -> None:
        tmp, candidate_file, tracker = self._fixture()
        self.addCleanup(tmp.cleanup)
        marker_dir = candidate_file.parent / "candidate-outcomes"
        marker_dir.mkdir()
        (marker_dir / "candidate-001.ACCEPTED.MINOR.XYZ-PILOT-001.md").write_text(
            accepted_marker(1, "XYZ-PILOT-001", "Open"),
            encoding="utf-8",
        )

        result = mod.validate_outcomes(candidate_file, tracker, candidate_index=1)

        self.assertEqual(1, len(result.errors))
        self.assertIn("tracker status mismatch for XYZ-PILOT-001", result.errors[0])

    def test_require_all_fails_when_any_candidate_has_no_marker(self) -> None:
        tmp, candidate_file, tracker = self._fixture()
        self.addCleanup(tmp.cleanup)
        marker_dir = candidate_file.parent / "candidate-outcomes"
        marker_dir.mkdir()
        (marker_dir / "candidate-001.ACCEPTED.MINOR.XYZ-PILOT-001.md").write_text(
            accepted_marker(1, "XYZ-PILOT-001", "Resolved 2026-06-20"),
            encoding="utf-8",
        )

        result = mod.validate_outcomes(candidate_file, tracker, require_all=True)

        self.assertEqual((1, 2), result.checked_indexes)
        self.assertEqual(1, len(result.errors))
        self.assertIn("candidate 002 expected exactly one outcome marker, found 0", result.errors[0])


if __name__ == "__main__":
    unittest.main()
