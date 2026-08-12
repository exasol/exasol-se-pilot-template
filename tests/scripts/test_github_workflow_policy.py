#!/usr/bin/env python3
"""Regression tests for GitHub Actions policy checks."""

from pathlib import Path
import unittest

from scripts import check_github_workflow_policy as policy


class GithubWorkflowPolicyTest(unittest.TestCase):
    def test_xyz_master_background_filing_concurrency_is_accepted(self) -> None:
        workflow = Path(".github/workflows/xyz-master-background-filing.yml")
        text = workflow.read_text(encoding="utf-8")

        self.assertEqual(
            [],
            policy.check_xyz_master_background_filing_policy(workflow, text),
        )

    def test_xyz_master_background_filing_rejects_canceling_runs(self) -> None:
        workflow = Path(".github/workflows/xyz-master-background-filing.yml")
        text = """
name: XYZ_MASTER Background Filing (Manual)
concurrency:
  group: xyz-master-background-filing
  cancel-in-progress: true
jobs:
  background-filing:
    runs-on: ubuntu-latest
"""

        errors = policy.check_xyz_master_background_filing_policy(workflow, text)

        self.assertEqual(1, len(errors))
        self.assertIn("cancel-in-progress: false", errors[0])

    def test_xyz_master_background_filing_rejects_non_global_group(self) -> None:
        workflow = Path(".github/workflows/xyz-master-background-filing.yml")
        text = """
name: XYZ_MASTER Background Filing (Manual)
concurrency:
  group: xyz-master-background-filing-${{ github.ref }}
  cancel-in-progress: false
jobs:
  background-filing:
    runs-on: ubuntu-latest
"""

        errors = policy.check_xyz_master_background_filing_policy(workflow, text)

        self.assertEqual(1, len(errors))
        self.assertIn("global concurrency group", errors[0])

    def test_tracker_notifier_force_push_guards_are_accepted(self) -> None:
        for name in (
            "tracker-major-defect-heads-up.yml",
            "tracker-major-defect-resolved-sns.yml",
        ):
            with self.subTest(name=name):
                workflow = Path(".github/workflows") / name
                text = workflow.read_text(encoding="utf-8")

                self.assertEqual(
                    [],
                    policy.check_tracker_notifier_force_push_guard(workflow, text),
                )

    def test_tracker_notifier_rejects_missing_non_ancestor_guard(self) -> None:
        workflow = Path(".github/workflows/tracker-major-defect-heads-up.yml")
        text = workflow.read_text(encoding="utf-8").replace(
            'if [ "${skip}" = "false" ] && ! git merge-base --is-ancestor "${base}" "${head}" 2>/dev/null; then skip=true; fi',
            "# removed for regression test",
        )

        errors = policy.check_tracker_notifier_force_push_guard(workflow, text)

        self.assertEqual(1, len(errors))
        self.assertIn("non-ancestor base skip", errors[0])

    def test_tracker_notifier_rejects_send_step_without_skip_gate(self) -> None:
        workflow = Path(".github/workflows/tracker-major-defect-resolved-sns.yml")
        text = workflow.read_text(encoding="utf-8").replace(
            "steps.range.outputs.skip != 'true'",
            "always()",
        )

        errors = policy.check_tracker_notifier_force_push_guard(workflow, text)

        self.assertTrue(errors)
        self.assertTrue(any("send gate" in error for error in errors))


if __name__ == "__main__":
    unittest.main()
