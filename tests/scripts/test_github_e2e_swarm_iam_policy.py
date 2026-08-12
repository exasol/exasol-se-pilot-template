#!/usr/bin/env python3
"""Regression checks for the GitHub E2E swarm OIDC IAM policy."""
from __future__ import annotations

import json
from pathlib import Path
import unittest


REPO_ROOT = Path(__file__).resolve().parents[2]
POLICY_PATH = REPO_ROOT / "scheduler" / "iam" / "github-e2e-swarm-policy.json"


class GitHubE2eSwarmIamPolicyTest(unittest.TestCase):
    def test_tag_index_probe_uses_iam_action_namespace(self) -> None:
        policy = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
        actions = {
            action
            for statement in policy["Statement"]
            for action in (
                statement["Action"]
                if isinstance(statement["Action"], list)
                else [statement["Action"]]
            )
        }

        self.assertIn("tag:GetResources", actions)
        self.assertNotIn("resourcegroupstaggingapi:GetResources", actions)


if __name__ == "__main__":
    unittest.main()
