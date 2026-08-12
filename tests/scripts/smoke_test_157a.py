"""XYZ-PILOT-157(a) regression smoke test for the <PILOT_NAME> Chatbot.

Validates that `services/chatbot/app.py::_emit_chatbot_candidate` writes a
well-formed candidate file to
`tests/logs/runs/<UTC>/xyz_PILOT_CHATBOT/issue-candidates.md` via the
GitHub Contents API. Designed to run INSIDE the running
`<PROJECT_SLUG>-chatbot` container so it inherits the live env (SSM-backed
`GITHUB_TOKEN` via `SSM_PAT_NAME`, `GITHUB_REPO`, AWS region/role).

Why exec-slice instead of `import app`:
  Helpers and module constants live before the `email = _require_auth()`
  Cognito SSO gate. Lines after that gate render the Streamlit UI. A direct
  `import app` from a non-browser context therefore hits a hard NameError +
  Streamlit ScriptRunContext failure. Slicing the source before the gate and
  execing only the helpers gives full access to `_emit_chatbot_candidate` +
  `_put_new_repo_file` without ever needing SSO.

Invocation:
  docker cp tests/scripts/smoke_test_157a.py <PROJECT_SLUG>-chatbot:/tmp/
  docker exec <PROJECT_SLUG>-chatbot python /tmp/smoke_test_157a.py

Exit:
  0 + JSON dict on stdout    on success (candidate kind == "candidate")
  1 + JSON error on stderr   on any failure

The candidate file produced is clearly marked DO NOT DRAIN so xyz_master
skips it. First validated 2026-06-01 against deployed app.py SHA-256
b07767167c360f9bdabddd15050614f48921932c91bebee0213c426959ef2e3c
(commit 9a2a793) — see XYZ-PILOT-157(a) detail-section validation row.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone

SOURCE_PATH = "/app/app.py"

with open(SOURCE_PATH, encoding="utf-8") as f:
    src_lines = f.readlines()

gate_index = next(
    (
        idx
        for idx, line in enumerate(src_lines)
        if line.startswith("email = _require_auth()")
    ),
    None,
)
if gate_index is None:
    print(json.dumps({
        "ok": False,
        "phase": "gate-line-check",
        "error": (
            "`email = _require_auth()` SSO gate was not found. "
            "`app.py` was likely restructured — update this smoke test's "
            "helper-slicing logic to match."
        ),
    }), file=sys.stderr)
    sys.exit(1)

src_helpers = "".join(src_lines[:gate_index])

ns: dict = {"__name__": "__chatbot_helpers__"}
try:
    exec(compile(src_helpers, SOURCE_PATH, "exec"), ns)
except Exception as exc:
    print(json.dumps({
        "ok": False,
        "phase": "exec-helpers",
        "error": f"{type(exc).__name__}: {exc}",
    }), file=sys.stderr)
    sys.exit(1)

required = ("_emit_chatbot_candidate", "_put_new_repo_file", "GITHUB_REPO")
missing = [name for name in required if name not in ns]
if missing:
    print(json.dumps({
        "ok": False,
        "phase": "loaded-symbols",
        "error": f"missing: {missing}",
    }), file=sys.stderr)
    sys.exit(1)

now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H%M%SZ")
result = ns["_emit_chatbot_candidate"](
    kind="defect",
    email="<OPERATOR_EMAIL>",
    title=f"SMOKE TEST — XYZ-PILOT-157(a) E2E validation {now_iso} — DO NOT DRAIN",
    severity="minor",
    area="Tests · chatbot E2E validation · XYZ-PILOT-157(a) smoke test · do-not-drain",
    summary_md=(
        "**SMOKE TEST ARTIFACT — DO NOT DRAIN AS A REAL TICKET.**\n\n"
        "Produced by `tests/scripts/smoke_test_157a.py` executed via "
        "`docker exec <PROJECT_SLUG>-chatbot python …` to validate that the "
        "chatbot's `_emit_chatbot_candidate` function successfully writes "
        "a candidate file to "
        "`tests/logs/runs/<UTC>/xyz_PILOT_CHATBOT/issue-candidates.md` "
        "via the GitHub Contents API.\n\n"
        "xyz_master should NOT drain this candidate into a real "
        "`XYZ-PILOT-NNN` ticket — it is purely validation evidence that "
        "the post-XYZ-PILOT-157(a) candidate-emission path is healthy.\n\n"
        "If you are reading this in a xyz_master drain queue: skip it. "
        "The smoke test passed if this file exists at all."
    ),
    question="(synthetic) Validate the chatbot's XYZ-PILOT-157(a) candidate-emission path.",
    answer=(
        "(synthetic) Smoke-test answer body. The presence of this candidate "
        "file in the repo is the validation success criterion."
    ),
)

print(json.dumps(result, indent=2))
sys.exit(0 if result.get("kind") == "candidate" else 1)
