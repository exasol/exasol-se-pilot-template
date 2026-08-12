from __future__ import annotations

import importlib.util
import base64
from pathlib import Path
import sys
import types
import unittest
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = REPO_ROOT / "scheduler" / "xyz_pilot_user_mgmt.py"


def load_module():
    sys.modules["boto3"] = types.SimpleNamespace(client=lambda *args, **kwargs: object())
    spec = importlib.util.spec_from_file_location("xyz_pilot_user_mgmt_test", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class HpcMasterCandidateBacklogTests(unittest.TestCase):
    def setUp(self):
        self.module = load_module()
        self.module.XYZ_MASTER_BACKLOG_MIN_RUN_ID = "2026-06-03T000000Z"
        self.module.XYZ_MASTER_BACKLOG_LIMIT = 50

    def _run_candidates(self, files, blobs, decided=None, tracker_titles=None, tracker_statuses=None):
        with (
            mock.patch.object(self.module, "_operator_context", return_value={
                "email": "<OPERATOR_EMAIL>",
                "elevated_operator": True,
                "full_operator": True,
                "dashboard_access": True,
                "allowed_operator_count": 3,
            }),
            mock.patch.object(self.module, "_list_candidate_tree", return_value=(files, decided or set())),
            mock.patch.object(self.module, "_gh_blob_text", side_effect=lambda sha: blobs[sha]),
            mock.patch.object(
                self.module,
                "_tracker_index",
                return_value=(tracker_titles or set(), tracker_statuses or {}),
            ),
        ):
            return self.module._action_xyz_master_candidates({})

    def test_hides_pre_marker_candidate_when_detail_title_already_in_tracker(self):
        path = "tests/logs/runs/2026-06-03T120000Z/OPENAI_CODEX/issue-candidates.md"
        files = {path: {"path": path, "run_id": "2026-06-03T120000Z", "source": "OPENAI_CODEX", "sha": "sha1"}}
        blobs = {
            "sha1": """# Candidate handoff

## Candidate - File resolved ticket

- **Proposed severity:** `minor`
- **Proposed status:** `Resolved 2026-06-03`

### Full-register detail section

```markdown
### XYZ-PILOT-NNN — Candidate-drain digest reported already-decided candidates
- **Status:** Resolved 2026-06-03
```
"""
        }

        result = self._run_candidates(
            files,
            blobs,
            tracker_titles={
                self.module._normalize_tracker_title(
                    "Candidate-drain digest reported already-decided candidates"
                )
            },
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["count"], 0)
        self.assertEqual(result["suppressed"]["already_tracked"], 1)

    def test_lists_new_candidate_after_cutoff(self):
        path = "tests/logs/runs/2026-06-03T130000Z/xyz_stress/issue-candidates.md"
        files = {path: {"path": path, "run_id": "2026-06-03T130000Z", "source": "xyz_stress", "sha": "sha2"}}
        blobs = {
            "sha2": """# xyz_stress Issue Candidates

## Candidate: Concurrent QBR/chatbot stress threshold exceeded

- **Severity:** minor pending operator approval to promote to major
- **Area:** Streamlit QBR · Chatbot · concurrent UAT readiness
"""
        }

        result = self._run_candidates(files, blobs)

        self.assertTrue(result["ok"])
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["pending"][0]["source"], "xyz_stress")
        self.assertEqual(result["pending"][0]["mutation_kind"], "new-ticket")
        self.assertEqual(result["pending"][0]["template_kind"], "normalized-lightweight")
        self.assertEqual(result["pending"][0]["proposed_area"], "Streamlit QBR · Chatbot · concurrent UAT readiness")

    def test_hides_lightweight_candidate_when_heading_already_in_tracker(self):
        path = "tests/logs/runs/2026-06-05T210337Z/xyz_stress/issue-candidates.md"
        files = {path: {"path": path, "run_id": "2026-06-05T210337Z", "source": "xyz_stress", "sha": "sha-dupe"}}
        title = "Concurrent QBR/chatbot/Exasol stress threshold exceeded"
        blobs = {
            "sha-dupe": f"""# xyz_stress Issue Candidates

## Candidate: {title}

- **Severity:** minor
- **Area:** Streamlit QBR · Chatbot · Exasol Personal · concurrent UAT readiness
"""
        }

        result = self._run_candidates(
            files,
            blobs,
            tracker_titles={self.module._normalize_tracker_title(title)},
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["count"], 0)
        self.assertEqual(result["suppressed"]["already_tracked"], 1)

    def test_suppresses_empty_agent_placeholder_files(self):
        path = "tests/logs/runs/2026-06-04T121444Z/xyz_smoke/issue-candidates.md"
        files = {path: {"path": path, "run_id": "2026-06-04T121444Z", "source": "xyz_smoke", "sha": "sha-empty"}}
        blobs = {
            "sha-empty": """# xyz_smoke Issue Candidates

This file is evidence for `XYZ_MASTER`; it is not a tracker edit.
"""
        }

        result = self._run_candidates(files, blobs)

        self.assertTrue(result["ok"])
        self.assertEqual(result["count"], 0)
        self.assertEqual(result["suppressed"]["empty"], 1)

    def test_lists_status_transition_candidate_for_operator_approval(self):
        path = "tests/logs/runs/2026-06-03T131000Z/OPENAI_CODEX/issue-candidates.md"
        files = {path: {"path": path, "run_id": "2026-06-03T131000Z", "source": "OPENAI_CODEX", "sha": "sha-status"}}
        blobs = {
            "sha-status": """# Candidate handoff

## Candidate - Status transition: XYZ-PILOT-210 -> Resolved 2026-06-03

- **Existing ticket:** [XYZ-PILOT-210](docs/XYZ_PILOT_ISSUE_TRACKER.md#xyz-pilot-210)
- **Requested status:** Resolved 2026-06-03
- **Severity:** major
"""
        }

        result = self._run_candidates(files, blobs, tracker_statuses={"XYZ-PILOT-210": "Open"})

        self.assertTrue(result["ok"])
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["pending"][0]["mutation_kind"], "status-transition")
        self.assertEqual(result["pending"][0]["proposed_severity"], "major")

    def test_scan_window_prefers_real_timestamp_over_named_run_prefix(self):
        self.module.XYZ_MASTER_CANDIDATE_FILE_SCAN_LIMIT = 1
        fresh_path = "tests/logs/runs/2026-06-23T203307Z/OPENAI_CODEX/issue-candidates.md"
        older_named_path = "tests/logs/runs/uat-<AWS_REGION>-2026-06-17T100705Z/xyz_stress/issue-candidates.md"
        files = {
            older_named_path: {
                "path": older_named_path,
                "run_id": "uat-<AWS_REGION>-2026-06-17T100705Z",
                "source": "xyz_stress",
                "sha": "sha-old-empty",
            },
            fresh_path: {
                "path": fresh_path,
                "run_id": "2026-06-23T203307Z",
                "source": "OPENAI_CODEX",
                "sha": "sha-fresh-status",
            },
        }
        blobs = {
            "sha-old-empty": "# xyz_stress Issue Candidates\n\nNo candidates.\n",
            "sha-fresh-status": """# Candidate handoff

## Candidate - Status transition: XYZ-PILOT-289 -> Resolved 2026-06-23

- **Existing ticket:** [XYZ-PILOT-289](docs/XYZ_PILOT_ISSUE_TRACKER.md#xyz-pilot-289)
- **Requested status:** Resolved 2026-06-23
- **Severity:** minor
""",
        }

        result = self._run_candidates(files, blobs, tracker_statuses={"XYZ-PILOT-289": "Open"})

        self.assertTrue(result["ok"])
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["pending"][0]["run_id"], "2026-06-23T203307Z")
        self.assertEqual(result["pending"][0]["mutation_kind"], "status-transition")
        self.assertEqual(result["suppressed"]["scan_limit"], 1)

    def test_lists_severity_transition_candidate_for_operator_approval(self):
        path = "tests/logs/runs/2026-06-07T090000Z/codex/issue-candidates.md"
        files = {path: {"path": path, "run_id": "2026-06-07T090000Z", "source": "codex", "sha": "sha-elevate"}}
        blobs = {
            "sha-elevate": """# Candidate handoff

## Candidate - Severity transition: XYZ-PILOT-219 -> major

- **Existing ticket:** [XYZ-PILOT-219](docs/XYZ_PILOT_ISSUE_TRACKER.md#xyz-pilot-219)
- **Requested severity:** major
- **Severity rationale:** Repeated operator-visible approval workflow failures require major-defect handling.
"""
        }

        result = self._run_candidates(files, blobs)

        self.assertTrue(result["ok"])
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["pending"][0]["mutation_kind"], "severity-transition")
        self.assertEqual(result["pending"][0]["proposed_severity"], "major")

    def test_hides_historical_files_before_cutoff(self):
        path = "tests/logs/runs/2026-06-02T235959Z/xyz_stress/issue-candidates.md"
        files = {path: {"path": path, "run_id": "2026-06-02T235959Z", "source": "xyz_stress", "sha": "sha3"}}

        result = self._run_candidates(files, {"sha3": "## Candidate - old\n\n- **Severity:** minor\n"})

        self.assertTrue(result["ok"])
        self.assertEqual(result["count"], 0)
        self.assertEqual(result["suppressed"]["historical"], 1)

    def test_partially_decided_file_still_lists_undecided_second_candidate(self):
        path = "tests/logs/runs/2026-06-03T140000Z/CODEX/issue-candidates.md"
        files = {path: {"path": path, "run_id": "2026-06-03T140000Z", "source": "CODEX", "sha": "sha4"}}
        blobs = {
            "sha4": """## Candidate - already decided

- **Severity:** minor

## Candidate - still pending

- **Severity:** feature
- **Area:** Automation
"""
        }

        result = self._run_candidates(files, blobs, decided={("2026-06-03T140000Z", "CODEX", 1)})

        self.assertTrue(result["ok"])
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["pending"][0]["candidate_index"], 2)
        self.assertEqual(result["pending"][0]["proposed_severity"], "feature")

    def test_hides_daily_status_prune_housekeeping_candidates(self):
        path = "tests/logs/runs/2026-06-04T040036Z/XYZ_PILOT_DAILY_STATUS/issue-candidates.md"
        files = {path: {"path": path, "run_id": "2026-06-04T040036Z", "source": "XYZ_PILOT_DAILY_STATUS", "sha": "sha5"}}
        blobs = {
            "sha5": """# Candidate handoff to XYZ_MASTER — daily-status prune

## Candidate 1 — Prune top-table row XYZ-PILOT-207

- **Existing ticket:** [XYZ-PILOT-207](docs/XYZ_PILOT_ISSUE_TRACKER.md#xyz-pilot-207)

### Mutation

Remove the single struck-through `| ~~[XYZ-PILOT-207](#xyz-pilot-207)~~ ... |` row from the **Active issues** top table only.
"""
        }

        result = self._run_candidates(files, blobs)

        self.assertTrue(result["ok"])
        self.assertEqual(result["count"], 0)
        self.assertEqual(result["suppressed"]["housekeeping"], 1)

    def test_hides_bursty_candidate_file_from_picker(self):
        path = "tests/logs/runs/2026-06-04T130000Z/xyz_stress/issue-candidates.md"
        files = {path: {"path": path, "run_id": "2026-06-04T130000Z", "source": "xyz_stress", "sha": "sha6"}}
        body = "\n\n".join(
            f"""## Candidate {idx} - Stress finding

- **Severity:** minor
- **Area:** Stress test
"""
            for idx in range(1, self.module.XYZ_MASTER_CANDIDATE_FILE_VISIBLE_LIMIT + 2)
        )

        result = self._run_candidates(files, {"sha6": body})

        self.assertTrue(result["ok"])
        self.assertEqual(result["count"], 0)
        self.assertEqual(result["suppressed"]["burst"], self.module.XYZ_MASTER_CANDIDATE_FILE_VISIBLE_LIMIT + 1)
        self.assertEqual(result["burst_files"][0]["path"], path)

    def test_dispatch_rejects_high_severity_direct_main_before_github_run(self):
        candidate = """# xyz_integration Issue Candidates

## Candidate: GitHub E2E swarm role lacks Resource Groups Tagging read permission

- **Severity:** minor
- **Status:** Open
"""
        encoded = base64.b64encode(candidate.encode("utf-8")).decode("ascii")

        with (
            mock.patch.object(
                self.module,
                "_require_xyz_master_operator",
                return_value="<OPERATOR_EMAIL>",
            ),
            mock.patch.object(
                self.module,
                "_gh_get",
                return_value={"content": encoded},
            ),
            mock.patch.object(self.module, "_gh_post") as dispatch,
        ):
            result = self.module._action_xyz_master_dispatch_candidate({
                "run_id": "2026-06-07T064851Z",
                "source": "codex",
                "candidate_index": 1,
                "classification_override": "major",
                "mode": "direct-main",
            })

        self.assertFalse(result["ok"])
        self.assertIn("direct-main is restricted", result["error"])
        self.assertEqual(result["candidate_classification"], "minor")
        self.assertEqual(result["requested_classification"], "major")
        dispatch.assert_not_called()

    def test_dispatch_writes_approved_marker_for_backlog_drain(self):
        candidate = """# Candidate handoff

## Candidate - Fix dashboard typo

- **Severity:** minor
- **Status:** Open
"""
        encoded = base64.b64encode(candidate.encode("utf-8")).decode("ascii")

        with (
            mock.patch.object(
                self.module,
                "_require_xyz_master_operator",
                return_value="<OPERATOR_EMAIL>",
            ),
            mock.patch.object(
                self.module,
                "_gh_get",
                return_value={"content": encoded},
            ),
            mock.patch.object(self.module, "_gh_post") as dispatch,
            mock.patch.object(self.module, "_gh_put") as put,
        ):
            result = self.module._action_xyz_master_dispatch_candidate({
                "run_id": "2026-06-22T191723Z",
                "source": "codex",
                "candidate_index": 1,
                "classification_override": "minor",
                "mode": "direct-main",
            })

        self.assertTrue(result["ok"])
        self.assertIn("marker_path", result)
        self.assertIn("APPROVED_DISPATCHED.DIRECT_MAIN", result["marker_path"])
        dispatch.assert_called_once()
        put.assert_called_once()

    def test_open_priority_issues_lists_current_open_changeable_tracker_items(self):
        tracker = """# Tracker

<a id="xyz-pilot-219"></a>

### XYZ-PILOT-219 - GitHub E2E swarm role lacks Resource Groups Tagging read permission
- **Status:** Open
- **Severity:** major
- **Area:** GitHub Actions OIDC IAM

<a id="xyz-pilot-218"></a>

### XYZ-PILOT-218 - Feature request
- **Status:** Open
- **Severity:** feature
- **Area:** UI

<a id="xyz-pilot-217"></a>

### XYZ-PILOT-217 - Already fixed
- **Status:** Resolved 2026-06-07
- **Severity:** critical
- **Area:** Runtime
"""

        with (
            mock.patch.object(
                self.module,
                "_operator_context",
                return_value={
                    "email": "<OPERATOR_EMAIL>",
                    "elevated_operator": True,
                    "full_operator": True,
                    "dashboard_access": True,
                    "allowed_operator_count": 3,
                },
            ),
            mock.patch.object(self.module, "_tracker_source", return_value=tracker),
        ):
            result = self.module._action_xyz_master_open_issues({})

        self.assertTrue(result["ok"])
        self.assertEqual(result["count"], 2)
        self.assertEqual(result["issues"][0]["id"], "XYZ-PILOT-219")
        self.assertEqual(result["issues"][0]["severity"], "major")
        self.assertEqual(result["issues"][1]["id"], "XYZ-PILOT-218")
        self.assertEqual(result["issues"][1]["severity"], "feature")

    def test_priority_change_writes_transition_candidate_and_dispatches(self):
        tracker = """# Tracker

<a id="xyz-pilot-219"></a>

### XYZ-PILOT-219 - GitHub E2E swarm role lacks Resource Groups Tagging read permission
- **Status:** Open
- **Severity:** minor
- **Area:** GitHub Actions OIDC IAM
"""

        with (
            mock.patch.object(
                self.module,
                "_require_xyz_master_operator",
                return_value="<OPERATOR_EMAIL>",
            ),
            mock.patch.object(self.module, "_tracker_source", return_value=tracker),
            mock.patch.object(self.module, "_gh_put") as put,
            mock.patch.object(self.module, "_gh_post") as post,
        ):
            result = self.module._action_xyz_master_request_priority_change({
                "issue_id": "XYZ-PILOT-219",
                "target_severity": "major",
                "mode": "direct-main",
                "rationale": "Repeated E2E swarm warning affects release confidence.",
            })

        self.assertTrue(result["ok"])
        self.assertEqual(result["previous_severity"], "minor")
        self.assertEqual(result["target_severity"], "major")
        self.assertEqual(result["inputs"]["classification_override"], "major")
        self.assertEqual(result["inputs"]["operator_approved_high_severity"], "true")
        put.assert_called_once()
        put_path, put_body = put.call_args.args
        self.assertIn("tests/logs/runs/", put_path)
        candidate = base64.b64decode(put_body["content"]).decode("utf-8")
        self.assertIn("Severity transition: XYZ-PILOT-219 -> major", candidate)
        self.assertIn("- **Current severity:** minor", candidate)
        self.assertIn("- **Requested severity:** major", candidate)
        post.assert_called_once()


class OperatorRoleModelTests(unittest.TestCase):
    class _FakeUsernameExists(Exception):
        pass

    class _FakeCognito:
        def __init__(self, parent):
            self.parent = parent
            self.created = []
            self.group_assignments = []
            self.users = {}
            self.by_token = {}
            self.group_map = {}
            self.exceptions = types.SimpleNamespace(
                UsernameExistsException=parent._FakeUsernameExists,
                UserNotFoundException=Exception,
                UserPoolAddOnNotEnabledException=Exception,
            )

        def get_user(self, AccessToken):  # noqa: N802
            user = self.by_token[AccessToken]
            attrs = [{"Name": "email", "Value": user["email"]}]
            return {"Username": user.get("username", user["email"]), "UserAttributes": attrs}

        def admin_list_groups_for_user(self, UserPoolId, Username):  # noqa: N802
            return {"Groups": [{"GroupName": g} for g in self.group_map.get(Username, [])]}

        def admin_create_user(self, **kwargs):
            username = kwargs["Username"]
            if username in self.users:
                raise self.parent._FakeUsernameExists()
            self.users[username] = {"UserStatus": "FORCE_CHANGE_PASSWORD"}
            self.created.append(username)
            return {}

        def admin_get_user(self, UserPoolId, Username):  # noqa: N802
            user = self.users.get(Username)
            if not user:
                raise Exception("missing user")
            return user

        def admin_add_user_to_group(self, UserPoolId, Username, GroupName):  # noqa: N802
            self.group_assignments.append((Username, GroupName))
            return {}

    class _FakeSes:
        def __init__(self):
            self.verified = []

        def verify_email_identity(self, EmailAddress):  # noqa: N802
            self.verified.append(EmailAddress)
            return {}

    def setUp(self):
        self.module = load_module()
        self.fake_cognito = self._FakeCognito(self)
        self.fake_ses = self._FakeSes()
        self.module.cognito = self.fake_cognito
        self.module.ses = self.fake_ses
        self.module.XYZ_MASTER_APPROVER_EMAILS = {"ops@example.com"}
        self.fake_cognito.by_token["ops-token"] = {"email": "ops@example.com", "username": "ops"}
        self.fake_cognito.by_token["pilot-token"] = {"email": "pilot@example.com", "username": "pilot"}
        self.fake_cognito.by_token["xyz-ops-token"] = {"email": "xyzops@example.com", "username": "xyzops"}
        self.fake_cognito.by_token["biz-token"] = {"email": "biz@example.com", "username": "biz"}
        self.fake_cognito.group_map["ops"] = ["OPS_ONLY"]
        self.fake_cognito.group_map["pilot"] = ["XYZ_PILOT"]
        self.fake_cognito.group_map["xyzops"] = ["XYZ_OPS"]
        self.fake_cognito.group_map["biz"] = ["XYZ_BIZ"]

    def _event(self, token, **extra):
        event = {"headers": {"authorization": f"Bearer {token}"}}
        event.update(extra)
        return event

    def test_xyz_biz_has_no_dashboard_access(self):
        ctx = self.module._operator_context(self._event("biz-token"))
        self.assertFalse(ctx["dashboard_access"])
        self.assertFalse(ctx["can_invite"])

    def test_xyz_ops_can_only_invite_xyz_ops_or_xyz_biz(self):
        allowed = self.module._action_invite_user(
            "new.user@example.com",
            self._event("xyz-ops-token", groups=["XYZ_OPS"]),
        )
        self.assertTrue(allowed["ok"])
        self.assertEqual(allowed["groups"], ["XYZ_OPS"])
        denied = self.module._action_invite_user(
            "another.user@example.com",
            self._event("xyz-ops-token", groups=["XYZ_PILOT"]),
        )
        self.assertFalse(denied["ok"])
        self.assertIn("not allowed", denied["error"])

    def test_ops_only_defaults_to_xyz_biz_single_group(self):
        result = self.module._action_invite_user(
            "default.user@example.com",
            self._event("ops-token"),
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["groups"], ["XYZ_BIZ"])
        self.assertIn(("default.user@example.com", "XYZ_BIZ"), self.fake_cognito.group_assignments)

    def test_multiple_selected_groups_rejected(self):
        result = self.module._action_invite_user(
            "bad.user@example.com",
            self._event("ops-token", groups=["XYZ_BIZ", "XYZ_OPS"]),
        )
        self.assertFalse(result["ok"])
        self.assertIn("exactly one target Cognito group", result["error"])


if __name__ == "__main__":
    unittest.main()
