#!/usr/bin/env python3
"""Regression tests for candidate outcome marker generation."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import tempfile
import unittest


REPO_ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = REPO_ROOT / "scripts" / "write_candidate_outcome.py"

spec = importlib.util.spec_from_file_location("write_candidate_outcome", MODULE_PATH)
assert spec and spec.loader
write_candidate_outcome = importlib.util.module_from_spec(spec)
spec.loader.exec_module(write_candidate_outcome)


class Args:
    def __init__(self, **kwargs: object) -> None:
        defaults = {
            "candidate_file": "",
            "candidate_index": 1,
            "outcome": "accepted",
            "classification": None,
            "tracker_id": None,
            "tracker_status": None,
            "denial_reason": None,
            "duplicate_of": None,
            "decision_by": "xyz_master",
            "decision_at_utc": "2026-06-03T04:35:00Z",
            "commit": "abc1234",
            "title": None,
            "notes": None,
        }
        defaults.update(kwargs)
        self.__dict__.update(defaults)


class CandidateOutcomeTest(unittest.TestCase):
    def test_accepted_marker_filename_and_frontmatter(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            candidate = Path(tmp) / "issue-candidates.md"
            candidate.write_text("# candidate\n", encoding="utf-8")
            args = Args(
                candidate_file=str(candidate),
                candidate_index=2,
                outcome="accepted",
                classification="minor",
                tracker_id="XYZ-PILOT-211",
                tracker_status="Resolved 2026-06-03",
                title="Chatbot submitter acknowledgement skipped",
            )

            marker_path, content = write_candidate_outcome.build_marker(args)

            self.assertEqual(
                marker_path.name,
                "candidate-002.ACCEPTED.MINOR.XYZ-PILOT-211.md",
            )
            self.assertIn('outcome: "accepted"', content)
            self.assertIn('classification: "minor"', content)
            self.assertIn('tracker_id: "XYZ-PILOT-211"', content)
            self.assertIn("# Candidate outcome: ACCEPTED -> XYZ-PILOT-211", content)

    def test_duplicate_denial_marker_requires_duplicate_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            candidate = Path(tmp) / "issue-candidates.md"
            candidate.write_text("# candidate\n", encoding="utf-8")
            args = Args(
                candidate_file=str(candidate),
                candidate_index=3,
                outcome="denied",
                denial_reason="duplicate",
                duplicate_of="XYZ-PILOT-189",
            )

            marker_path, content = write_candidate_outcome.build_marker(args)

            self.assertEqual(
                marker_path.name,
                "candidate-003.DENIED.DUPLICATE.XYZ-PILOT-189.md",
            )
            self.assertIn('denial_reason: "duplicate"', content)
            self.assertIn('duplicate_of: "XYZ-PILOT-189"', content)

    def test_acceptance_requires_tracker_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            candidate = Path(tmp) / "issue-candidates.md"
            candidate.write_text("# candidate\n", encoding="utf-8")
            args = Args(
                candidate_file=str(candidate),
                outcome="accepted",
                classification="minor",
            )

            with self.assertRaisesRegex(ValueError, "--tracker-id is required"):
                write_candidate_outcome.build_marker(args)


if __name__ == "__main__":
    unittest.main()
