#!/usr/bin/env python3
"""Tests for report-only repository retention scanner."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import unittest


SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "check_repo_retention_candidates.py"
spec = importlib.util.spec_from_file_location("check_repo_retention_candidates", SCRIPT)
assert spec and spec.loader
scanner = importlib.util.module_from_spec(spec)
sys.modules["check_repo_retention_candidates"] = scanner
spec.loader.exec_module(scanner)


class RepoRetentionScannerTests(unittest.TestCase):
    def test_classifies_disposable_and_audit_files(self) -> None:
        self.assertEqual(scanner.classify("clients/XYZ/.DS_Store"), "disposable_tracked_junk")
        self.assertEqual(scanner.classify("scripts/__pycache__/x.cpython-314.pyc"), "disposable_tracked_junk")
        self.assertEqual(
            scanner.classify("docs/archive/XYZ_PILOT_ISSUE_TRACKER_2026-06-03T1340_reason.md"),
            "tracker_guardrail_archive",
        )
        self.assertEqual(
            scanner.classify("tests/logs/runs/2026-06-02T205200Z/xyz_stress/candidate-outcomes/candidate-001.ACCEPTED.md"),
            "e2e_candidate_outcome_audit",
        )
        self.assertEqual(
            scanner.classify("tests/logs/runs/2026-06-02T205200Z/xyz_stress/artifacts/stress_results.jsonl"),
            "e2e_raw_runtime_artifact",
        )

    def test_report_contains_file_and_directory_sizes(self) -> None:
        records = [
            scanner.FileRecord("docs/archive/XYZ_PILOT_ISSUE_TRACKER_a.md", 100),
            scanner.FileRecord("tests/logs/runs/R1/xyz_stress/artifacts/stress_results.jsonl", 70),
            scanner.FileRecord("tests/logs/runs/R1/manifest.md", 30),
            scanner.FileRecord("clients/XYZ/.DS_Store", 12),
        ]
        report = scanner.build_report(records)
        self.assertEqual(report["mode"], "report-only")
        self.assertEqual(report["tracked_file_count"], 4)
        self.assertEqual(report["tracked_bytes"], 212)
        self.assertEqual(report["categories"]["tracker_guardrail_archive"]["bytes"], 100)
        self.assertEqual(report["categories"]["e2e_raw_runtime_artifact"]["bytes"], 70)
        self.assertEqual(report["directories"][0], {"path": "docs/archive", "bytes": 100})

    def test_markdown_states_report_only(self) -> None:
        report = scanner.build_report([scanner.FileRecord("foo.tmp", 5)])
        markdown = scanner.render_markdown(report, max_files=5, max_dirs=5)
        self.assertIn("**Mode:** report-only", markdown)
        self.assertIn("`disposable_tracked_junk`", markdown)
        self.assertIn("5 B", markdown)


if __name__ == "__main__":
    unittest.main()
