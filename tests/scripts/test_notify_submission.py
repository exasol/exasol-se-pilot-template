#!/usr/bin/env python3
"""Regression tests for chatbot submission notification parsing."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import unittest


REPO_ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = REPO_ROOT / "scripts" / "notify_submission.py"

spec = importlib.util.spec_from_file_location("notify_submission", MODULE_PATH)
assert spec and spec.loader
notify_submission = importlib.util.module_from_spec(spec)
spec.loader.exec_module(notify_submission)


class NotifySubmissionParsingTest(unittest.TestCase):
    def test_acceptance_subject_accepts_current_unparenthesized_pilot_master_form(self) -> None:
        parsed = notify_submission.parse_acceptance_subject(
            "xyz_master: file XYZ-PILOT-210 GitHub API 403 defect"
        )

        self.assertEqual(parsed, ("210", "GitHub API 403 defect"))

    def test_acceptance_subject_keeps_legacy_parenthesized_form(self) -> None:
        parsed = notify_submission.parse_acceptance_subject(
            "xyz_master: file XYZ-PILOT-195 (chatbot-surfaced transient URL failure, Resolved at file-time)"
        )

        self.assertEqual(
            parsed,
            ("195", "chatbot-surfaced transient URL failure, Resolved at file-time"),
        )

    def test_candidate_path_accepts_handoff_from_body_wording(self) -> None:
        body = (
            "Consolidates xyz_PILOT_CHATBOT candidate handoff from "
            "tests/logs/runs/2026-06-03T040818Z/xyz_PILOT_CHATBOT/issue-candidates.md.\n"
        )

        self.assertEqual(
            notify_submission.extract_chatbot_candidate_path(body),
            Path("tests/logs/runs/2026-06-03T040818Z/xyz_PILOT_CHATBOT/issue-candidates.md"),
        )

    def test_candidate_path_keeps_legacy_candidate_source_wording(self) -> None:
        body = (
            "Candidate source: "
            "tests/logs/runs/2026-06-01T105543Z/xyz_PILOT_CHATBOT/issue-candidates.md\n"
        )

        self.assertEqual(
            notify_submission.extract_chatbot_candidate_path(body),
            Path("tests/logs/runs/2026-06-01T105543Z/xyz_PILOT_CHATBOT/issue-candidates.md"),
        )

    def test_non_chatbot_candidate_is_not_matched(self) -> None:
        body = (
            "Candidate source: "
            "tests/logs/runs/2026-06-03T041339Z/OPENAI_CODEX/issue-candidates.md\n"
        )

        self.assertIsNone(notify_submission.extract_chatbot_candidate_path(body))


if __name__ == "__main__":
    unittest.main()
