from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import tempfile
import types
import unittest


REPO_ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = REPO_ROOT / "tests" / "scripts" / "send_e2e_swarm_notification.py"


def load_module():
    sys.modules["boto3"] = types.SimpleNamespace(client=lambda *args, **kwargs: object())
    spec = importlib.util.spec_from_file_location("send_e2e_swarm_notification_test", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class E2eSwarmNotificationTests(unittest.TestCase):
    def setUp(self):
        self.module = load_module()
        self.summary = {
            "health": "green",
            "total_failures": 0,
            "total_warnings": 0,
            "total_candidates_by_sev": {
                "critical": 0,
                "major": 0,
                "minor": 0,
                "feature": 0,
                "external": 0,
            },
            "stress_summary": None,
            "agents": [
                {
                    "agent": "XYZ_STRESS",
                    "exit_code": 0,
                    "failures": 0,
                    "warnings": 0,
                    "candidates_by_sev": {
                        "critical": 0,
                        "major": 0,
                        "minor": 0,
                        "feature": 0,
                        "external": 0,
                    },
                }
            ],
        }

    def test_archive_failed_subject_and_body_omit_artifact_links(self):
        subject = self.module.render_subject(
            "2026-06-04T110618Z",
            self.summary,
            archive_failed=True,
        )
        html = self.module.render_email_html(
            "2026-06-04T110618Z",
            self.summary,
            github_ref="fb16eef",
            archive_failed=True,
            actions_url="https://github.com/<GITHUB_OWNER>/<PROJECT_SLUG>/actions/runs/123456789",
        )

        self.assertIn("ARCHIVE FAILED", subject)
        self.assertIn("Run archive failed", html)
        self.assertIn("actions/runs/26947879823", html)
        self.assertNotIn("/tree/fb16eef/tests/logs/runs/2026-06-04T110618Z", html)

    def test_warning_only_subject_and_body_include_warning_details(self):
        summary = dict(self.summary)
        summary["health"] = "yellow"
        summary["total_warnings"] = 1
        summary["agents"] = [
            {
                "agent": "XYZ_INTEGRATION",
                "exit_code": 0,
                "failures": 0,
                "warnings": 1,
                "interesting_rows": [
                    {
                        "probe": "AWS tag-index inventory",
                        "expected": "read-only resource index query succeeds",
                        "actual": "AccessDeniedException: tag:GetResources",
                        "result": "WARN",
                        "notes": "",
                    }
                ],
                "candidates_by_sev": {
                    "critical": 0,
                    "major": 0,
                    "minor": 0,
                    "feature": 0,
                    "external": 0,
                },
            }
        ]

        subject = self.module.render_subject("2026-06-04T112659Z", summary)
        html = self.module.render_email_html("2026-06-04T112659Z", summary, github_ref="abc123")

        self.assertIn("PASS · 1 warning(s)", subject)
        self.assertIn("AWS tag-index inventory", html)
        self.assertIn("tag:GetResources", html)

    def test_major_candidate_without_failures_is_yellow_not_red(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            agent_dir = run_dir / "XYZ_EXPERT"
            agent_dir.mkdir()
            (agent_dir / "agent.exit").write_text("0\n", encoding="utf-8")
            (agent_dir / "results.md").write_text(
                """# XYZ_EXPERT Results

- Failures: `0`
- Warnings/candidates: `1`
- Finished UTC: `2026-06-19T15:48:00Z`
""",
                encoding="utf-8",
            )
            (agent_dir / "issue-candidates.md").write_text(
                """# XYZ_EXPERT Issue Candidates

## Candidate: Tracked sensitive path review

- **Severity:** major
- **Area:** repository hygiene
""",
                encoding="utf-8",
            )

            summary = self.module.aggregate(run_dir)

        subject = self.module.render_subject("2026-06-19T154619Z", summary)
        html = self.module.render_email_html("2026-06-19T154619Z", summary, github_ref="abc123")

        self.assertEqual(summary["health"], "yellow")
        self.assertIn("PASS · 1 candidate(s) queued", subject)
        self.assertNotIn("🔴", subject)
        self.assertIn("Yellow", html)
        self.assertNotIn("Red — failures present", html)

    def test_parse_agent_results_counts_candidate_heading_blocks(self):
        with tempfile.TemporaryDirectory() as tmp:
            agent_dir = Path(tmp) / "XYZ_STRESS"
            agent_dir.mkdir()
            (agent_dir / "agent.exit").write_text("1\n", encoding="utf-8")
            (agent_dir / "results.md").write_text(
                """# Results

- Failures: `1`
- Warnings/candidates: `0`
- Finished UTC: `2026-06-04T12:30:00Z`
""",
                encoding="utf-8",
            )
            (agent_dir / "issue-candidates.md").write_text(
                """# XYZ_STRESS Issue Candidates

## Candidate: Concurrent QBR/chatbot/Exasol stress threshold exceeded

- **Severity:** minor pending operator approval to promote to major
- **Area:** Streamlit QBR
""",
                encoding="utf-8",
            )

            info = self.module.parse_agent_results(agent_dir)

        self.assertEqual(info["candidates_by_sev"]["minor"], 1)
        self.assertEqual(info["candidate_titles"], ["Concurrent QBR/chatbot/Exasol stress threshold exceeded"])


if __name__ == "__main__":
    unittest.main()
