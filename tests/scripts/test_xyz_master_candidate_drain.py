import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = REPO_ROOT / "scheduler" / "xyz_master_candidate_drain.py"


def load_module():
    sys.modules["boto3"] = types.SimpleNamespace(client=lambda *args, **kwargs: object())
    spec = importlib.util.spec_from_file_location("xyz_master_candidate_drain_test", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class CandidateDrainTests(unittest.TestCase):
    def setUp(self):
        self.module = load_module()
        self.saved_states = []

    def _run(self, entries, files, state=None, event=None):
        state = state or {"version": 1, "seen": {"bootstrap:path": {"first_seen": "bootstrap"}}}
        self.saved_states.clear()
        with (
            mock.patch.object(self.module, "_repo_tree_entries", return_value=entries),
            mock.patch.object(self.module, "_read_repo_file", side_effect=lambda path: files[path]),
            mock.patch.object(self.module, "_load_state", return_value=state),
            mock.patch.object(self.module, "_save_state", side_effect=lambda value: self.saved_states.append(value)),
            mock.patch.object(self.module, "_send_digest", return_value="msg-123") as send_digest,
        ):
            result = self.module.lambda_handler(event or {}, None)
        return result, send_digest

    def test_suppresses_new_file_when_all_candidates_have_outcome_markers(self):
        path = "tests/logs/runs/2026-06-03T080944Z/OPENAI_CODEX/issue-candidates.md"
        entries = [
            {"type": "blob", "path": path, "sha": "candidate-sha"},
            {
                "type": "blob",
                "path": "tests/logs/runs/2026-06-03T080944Z/OPENAI_CODEX/"
                "candidate-outcomes/candidate-001.ACCEPTED.STATUS.XYZ-PILOT-210.md",
                "sha": "marker-sha",
            },
        ]
        result, send_digest = self._run(entries, {path: "## Candidate — resolved\n\nBody"})

        self.assertEqual(result["new_candidate_files"], 0)
        self.assertEqual(result["ignored_decided_candidate_files"], 1)
        self.assertFalse(result["email_sent"])
        send_digest.assert_not_called()
        seen = self.saved_states[0]["seen"]["candidate-sha:" + path]
        self.assertTrue(seen["ignored_all_decided"])
        self.assertEqual(seen["decided_count"], 1)

    def test_sends_digest_for_new_pending_candidate_file(self):
        path = "tests/logs/runs/2026-06-03T120000Z/xyz_stress/issue-candidates.md"
        entries = [{"type": "blob", "path": path, "sha": "candidate-sha"}]
        result, send_digest = self._run(entries, {path: "## Candidate — stress finding\n\nBody"})

        self.assertEqual(result["new_candidate_files"], 1)
        self.assertEqual(result["ignored_decided_candidate_files"], 0)
        self.assertTrue(result["email_sent"])
        send_digest.assert_called_once()
        seen = self.saved_states[0]["seen"]["candidate-sha:" + path]
        self.assertFalse(seen["ignored_all_decided"])
        self.assertEqual(seen["decided_count"], 0)

    def test_ignores_empty_agent_placeholder_file(self):
        path = "tests/logs/runs/2026-06-04T121444Z/xyz_smoke/issue-candidates.md"
        entries = [{"type": "blob", "path": path, "sha": "candidate-sha"}]
        result, send_digest = self._run(
            entries,
            {
                path: """# xyz_smoke Issue Candidates

This file is evidence for `XYZ_MASTER`; it is not a tracker edit.
"""
            },
        )

        self.assertEqual(result["new_candidate_files"], 0)
        self.assertEqual(result["ignored_empty_candidate_files"], 1)
        self.assertFalse(result["email_sent"])
        send_digest.assert_not_called()
        seen = self.saved_states[0]["seen"]["candidate-sha:" + path]
        self.assertTrue(seen["ignored_empty"])

    def test_sends_digest_when_multi_candidate_file_is_only_partially_decided(self):
        path = "tests/logs/runs/2026-06-03T120100Z/CODEX/issue-candidates.md"
        entries = [
            {"type": "blob", "path": path, "sha": "candidate-sha"},
            {
                "type": "blob",
                "path": "tests/logs/runs/2026-06-03T120100Z/CODEX/"
                "candidate-outcomes/candidate-001.DENIED.DUPLICATE.XYZ-PILOT-208.md",
                "sha": "marker-sha",
            },
        ]
        result, send_digest = self._run(
            entries,
            {path: "## Candidate — old duplicate\n\nBody\n\n## Candidate — new pending\n\nBody"},
        )

        self.assertEqual(result["new_candidate_files"], 1)
        self.assertEqual(result["ignored_decided_candidate_files"], 0)
        self.assertTrue(result["email_sent"])
        send_digest.assert_called_once()
        seen = self.saved_states[0]["seen"]["candidate-sha:" + path]
        self.assertFalse(seen["ignored_all_decided"])
        self.assertEqual(seen["candidate_count"], 2)
        self.assertEqual(seen["decided_count"], 1)

    def test_auto_dispatches_minor_candidate_without_operator_digest(self):
        path = "tests/logs/runs/2026-06-03T130000Z/xyz_stress/issue-candidates.md"
        entries = [{"type": "blob", "path": path, "sha": "candidate-sha"}]
        body = """## Candidate - Minor defect

- **Proposed severity:** `minor`
- **Proposed type:** `defect`
"""
        with mock.patch.object(self.module, "_dispatch_background_filing", return_value={"dispatch": "ok"}) as dispatch:
            result, send_digest = self._run(
                entries,
                {path: body},
                event={"auto_file_low_severity": True},
            )

        self.assertEqual(result["new_candidate_files"], 1)
        self.assertEqual(result["manual_digest_candidate_files"], 0)
        self.assertEqual(len(result["auto_dispatches"]), 1)
        self.assertFalse(result["email_sent"])
        send_digest.assert_not_called()
        dispatch.assert_called_once()
        dispatch_args = dispatch.call_args.args
        self.assertEqual(dispatch_args[1], 1)
        self.assertEqual(dispatch.call_args.kwargs["mode"], "direct-main")
        seen = self.saved_states[0]["seen"]["candidate-sha:" + path]
        self.assertTrue(seen["auto_file_low_severity"])
        self.assertEqual(seen["auto_dispatches"][0]["candidate_index"], 1)

    def test_chatbot_candidate_stays_manual_digest_when_auto_enabled(self):
        path = "tests/logs/runs/2026-06-03T130050Z/XYZ_PILOT_CHATBOT/issue-candidates.md"
        entries = [{"type": "blob", "path": path, "sha": "candidate-sha"}]
        body = """## Candidate - Minor chatbot finding

- **Proposed severity:** `minor`
- **Proposed type:** `defect`
"""
        with mock.patch.object(self.module, "_dispatch_background_filing") as dispatch:
            result, send_digest = self._run(
                entries,
                {path: body},
                event={"auto_file_low_severity": True},
            )

        self.assertEqual(result["new_candidate_files"], 1)
        self.assertEqual(result["manual_digest_candidate_files"], 1)
        self.assertEqual(result["auto_dispatches"], [])
        self.assertTrue(result["email_sent"])
        send_digest.assert_called_once()
        dispatch.assert_not_called()
        seen = self.saved_states[0]["seen"]["candidate-sha:" + path]
        self.assertTrue(seen["auto_file_low_severity"])
        self.assertTrue(seen["manual_review_source"])
        self.assertEqual(seen["auto_dispatches"], [])

    def test_major_candidate_remains_digest_only_when_auto_enabled(self):
        path = "tests/logs/runs/2026-06-03T130100Z/xyz_stress/issue-candidates.md"
        entries = [{"type": "blob", "path": path, "sha": "candidate-sha"}]
        body = """## Candidate - Major defect

- **Proposed severity:** `major`
- **Proposed type:** `defect`
"""
        with mock.patch.object(self.module, "_dispatch_background_filing") as dispatch:
            result, send_digest = self._run(
                entries,
                {path: body},
                event={"auto_file_low_severity": True},
            )

        self.assertEqual(result["new_candidate_files"], 1)
        self.assertEqual(result["manual_digest_candidate_files"], 1)
        self.assertEqual(result["auto_dispatches"], [])
        self.assertTrue(result["email_sent"])
        send_digest.assert_called_once()
        dispatch.assert_not_called()

    def test_status_transition_is_not_auto_dispatched_even_when_minor(self):
        path = "tests/logs/runs/2026-06-03T130200Z/OPENAI_CODEX/issue-candidates.md"
        entries = [{"type": "blob", "path": path, "sha": "candidate-sha"}]
        body = """## Candidate - Status transition: XYZ-PILOT-001 -> Resolved 2026-06-03

- **Existing ticket:** [XYZ-PILOT-001](docs/XYZ_PILOT_ISSUE_TRACKER.md#xyz-pilot-001)
- **Requested status:** Resolved 2026-06-03
- **Severity:** minor
"""
        with mock.patch.object(self.module, "_dispatch_background_filing") as dispatch:
            result, send_digest = self._run(
                entries,
                {path: body},
                event={"auto_file_low_severity": True},
            )

        self.assertEqual(result["new_candidate_files"], 1)
        self.assertEqual(result["manual_digest_candidate_files"], 1)
        self.assertEqual(result["auto_dispatches"], [])
        self.assertTrue(result["email_sent"])
        send_digest.assert_called_once()
        dispatch.assert_not_called()

    def test_suppresses_daily_status_prune_housekeeping_from_digest(self):
        path = "tests/logs/runs/2026-06-04T040036Z/XYZ_PILOT_DAILY_STATUS/issue-candidates.md"
        entries = [{"type": "blob", "path": path, "sha": "candidate-sha"}]
        body = """# Candidate handoff to XYZ_MASTER — daily-status prune

## Candidate 1 — Prune top-table row XYZ-PILOT-207

- **Existing ticket:** [XYZ-PILOT-207](docs/XYZ_PILOT_ISSUE_TRACKER.md#xyz-pilot-207)

### Mutation

Remove the single struck-through `| ~~[XYZ-PILOT-207](#xyz-pilot-207)~~ ... |` row from the **Active issues** top table only.
"""
        result, send_digest = self._run(entries, {path: body})

        self.assertEqual(result["new_candidate_files"], 0)
        self.assertEqual(result["manual_digest_candidate_files"], 0)
        self.assertEqual(result["ignored_housekeeping_candidate_files"], 1)
        self.assertFalse(result["email_sent"])
        send_digest.assert_not_called()
        seen = self.saved_states[0]["seen"]["candidate-sha:" + path]
        self.assertTrue(seen["ignored_housekeeping"])

    def test_burst_guard_prevents_many_auto_dispatches_from_one_file(self):
        path = "tests/logs/runs/2026-06-04T130000Z/xyz_stress/issue-candidates.md"
        entries = [{"type": "blob", "path": path, "sha": "candidate-sha"}]
        body = "\n\n".join(
            f"""## Candidate {idx} - Minor stress finding

- **Proposed severity:** `minor`
- **Proposed type:** `defect`
"""
            for idx in range(1, self.module.MAX_AUTO_DISPATCH_PER_FILE + 2)
        )
        with mock.patch.object(self.module, "_dispatch_background_filing") as dispatch:
            result, send_digest = self._run(
                entries,
                {path: body},
                event={"auto_file_low_severity": True},
            )

        self.assertEqual(result["new_candidate_files"], 1)
        self.assertEqual(result["manual_digest_candidate_files"], 1)
        self.assertEqual(result["burst_guarded_candidate_files"], 1)
        self.assertEqual(result["auto_dispatches"], [])
        self.assertTrue(result["email_sent"])
        send_digest.assert_called_once()
        dispatch.assert_not_called()
        seen = self.saved_states[0]["seen"]["candidate-sha:" + path]
        self.assertTrue(seen["burst_guarded"])

    def test_targeted_file_candidate_dispatches_pr_with_operator_approval(self):
        with mock.patch.object(
            self.module, "_dispatch_background_filing", return_value={"dispatch": "ok"}
        ) as dispatch:
            result, send_digest = self._run(
                [],
                {},
                event={
                    "file_candidate": {
                        "run_id": "2026-07-01T075506Z",
                        "source": "claude-code",
                        "candidate_index": 1,
                        "mode": "pr",
                        "operator_approved_high_severity": True,
                    }
                },
            )

        self.assertEqual(result["status"], "dispatched")
        dispatch.assert_called_once()
        self.assertEqual(dispatch.call_args.kwargs["mode"], "pr")
        self.assertTrue(dispatch.call_args.kwargs["operator_approved_high_severity"])
        send_digest.assert_not_called()

    def test_targeted_file_candidate_dispatches_preview_in_dry_run_mode(self):
        # mode="dry-run" is a REAL dispatch — the workflow previews the diff, so the
        # panel can render it. The Lambda still calls GitHub (status "dispatched").
        with mock.patch.object(
            self.module, "_dispatch_background_filing", return_value={"dispatch": "ok"}
        ) as dispatch:
            result, _ = self._run(
                [],
                {},
                event={
                    "file_candidate": {
                        "run_id": "r",
                        "source": "s",
                        "candidate_index": 2,
                        "mode": "dry-run",
                    }
                },
            )

        self.assertEqual(result["status"], "dispatched")
        self.assertEqual(dispatch.call_args.kwargs["mode"], "dry-run")

    def test_targeted_file_candidate_event_dry_run_does_not_dispatch(self):
        # Top-level dry_run=True is the Lambda-level no-side-effect gate: return the
        # planned dispatch WITHOUT calling GitHub.
        with mock.patch.object(self.module, "_dispatch_background_filing") as dispatch:
            result, send_digest = self._run(
                [],
                {},
                event={
                    "dry_run": True,
                    "file_candidate": {
                        "run_id": "r",
                        "source": "s",
                        "candidate_index": 2,
                        "mode": "direct-main",
                    },
                },
            )

        self.assertEqual(result["status"], "dry-run")
        dispatch.assert_not_called()
        send_digest.assert_not_called()

    def test_targeted_file_candidate_rejects_unknown_mode(self):
        with self.assertRaises(RuntimeError):
            self._run(
                [],
                {},
                event={
                    "file_candidate": {
                        "run_id": "r",
                        "source": "s",
                        "candidate_index": 1,
                        "mode": "force-push",
                    }
                },
            )

    def test_pr_is_a_valid_auto_file_mode(self):
        self.assertIn("pr", self.module.VALID_AUTO_FILE_MODES)


if __name__ == "__main__":
    unittest.main()
