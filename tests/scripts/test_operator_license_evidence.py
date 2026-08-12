from __future__ import annotations

import importlib.util
import io
import json
from datetime import datetime, timezone
from pathlib import Path
import sys
import types
import unittest


REPO_ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = REPO_ROOT / "scheduler" / "xyz_pilot_user_mgmt.py"
DASHBOARD_PATH = REPO_ROOT / "scheduler" / "status_dashboard" / "dashboard.html"


class _FakeS3:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload

    def get_object(self, Bucket: str, Key: str):  # noqa: N803 - boto3 shape
        assert Bucket == "xyz-pilot-status-<AWS_ACCOUNT_ID>"
        assert Key == "license/evidence.json"
        return {
            "Body": io.BytesIO(json.dumps(self.payload).encode("utf-8")),
            "LastModified": datetime(2026, 6, 23, 20, 30, tzinfo=timezone.utc),
        }


def _load_user_mgmt(payload: dict[str, object]):
    clients = {
        "s3": _FakeS3(payload),
        "cognito-idp": object(),
        "ses": object(),
        "secretsmanager": object(),
        "ssm": object(),
    }
    sys.modules["boto3"] = types.SimpleNamespace(client=lambda name, **_kwargs: clients[name])
    spec = importlib.util.spec_from_file_location("xyz_pilot_user_mgmt_license_test", MODULE_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class OperatorLicenseEvidenceTests(unittest.TestCase):
    def test_license_evidence_action_strips_secret_like_extra_fields(self) -> None:
        module = _load_user_mgmt({
            "published_at_utc": "2026-06-23T20:30:00Z",
            "operation": "exasol-personal-license-rotation",
            "license_id": "2606002028",
            "company_name": "<CUSTOMER_NAME>",
            "expiration_date": "2026-09-30",
            "max_session_limit": "Unlimited",
            "license_run_check": "OK",
            "db_state": "running",
            "replacement_license_sha256": "d398fe384caa19539004e7c0fbc56870b2b0d2dcb3e0b00b23d136368f564d5a",
            "raw_license_body": "must-not-leak",
            "password": "must-not-leak",
        })

        result = module._action_license_evidence({})

        self.assertTrue(result["ok"])
        self.assertTrue(result["available"])
        self.assertEqual(result["evidence"]["license_id"], "2606002028")
        self.assertEqual(result["evidence"]["license_run_check"], "OK")
        self.assertNotIn("raw_license_body", result["evidence"])
        self.assertNotIn("password", result["evidence"])

    def test_dashboard_polls_and_logs_license_evidence(self) -> None:
        html = DASHBOARD_PATH.read_text(encoding="utf-8")

        self.assertIn('action: "license_evidence"', html)
        self.assertIn("function pollLicenseEvidence", html)
        self.assertIn("License evidence · ", html)
        self.assertIn("replacement_license_sha256", html)
        self.assertIn("XYZ-PILOT-289", html)


if __name__ == "__main__":
    unittest.main()
