#!/usr/bin/env python3
"""Fast static regression checks for the <PILOT_NAME> repository."""

from __future__ import annotations

from pathlib import Path
import py_compile
import re
import stat
import subprocess
import sys
import tempfile


SECRET_PATTERNS = {
    "aws_access_key_id": re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
    "private_key_block": re.compile(r"-----BEGIN (?:RSA |OPENSSH |EC |DSA )?PRIVATE KEY-----"),
    "github_token": re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9_]{36,}\b"),
}

REQUIRED_TEST_ASSETS = (
    Path("tests/scripts/run_e2e_swarm.sh"),
    Path("tests/scripts/init_swarm_run.sh"),
    Path("tests/scripts/probe_ec2_host_aws_cli.sh"),
)


def git_files(pattern: str | None = None) -> list[Path]:
    cmd = ["git", "ls-files", "-z"]
    if pattern:
        cmd.append(pattern)
    out = subprocess.check_output(cmd)
    return [Path(item.decode()) for item in out.split(b"\0") if item]


def check_python_compile(errors: list[str]) -> None:
    cache_root = Path(tempfile.gettempdir()) / "xyz-regression-static-pycache"
    for path in git_files("*.py"):
        cfile = cache_root / path.with_suffix(".pyc")
        cfile.parent.mkdir(parents=True, exist_ok=True)
        try:
            py_compile.compile(str(path), cfile=str(cfile), doraise=True)
        except py_compile.PyCompileError as exc:
            errors.append(f"{path}: Python compile failed: {exc.msg}")


def check_shell_syntax(errors: list[str]) -> None:
    for path in git_files("*.sh"):
        result = subprocess.run(["bash", "-n", str(path)], text=True, capture_output=True)
        if result.returncode:
            detail = result.stderr.strip() or result.stdout.strip()
            errors.append(f"{path}: shell syntax failed: {detail}")


def check_dockerfiles(errors: list[str]) -> None:
    for path in git_files("Dockerfile"):
        lines = path.read_text(encoding="utf-8").splitlines()
        first_instruction = next(
            (line.strip() for line in lines if line.strip() and not line.lstrip().startswith("#")),
            "",
        )
        if not first_instruction.upper().startswith("FROM "):
            errors.append(f"{path}: first non-comment instruction must be FROM")
        for lineno, line in enumerate(lines, start=1):
            if re.search(r"\bADD\s+https?://", line, re.I):
                errors.append(f"{path}:{lineno}: remote ADD is not allowed; use curl/wget with checksum validation")


def check_test_assets(errors: list[str]) -> None:
    for path in REQUIRED_TEST_ASSETS:
        if not path.exists():
            errors.append(f"{path}: required regression/test catalog asset is missing")
            continue
        if path.suffix == ".sh":
            mode = path.stat().st_mode
            if not mode & stat.S_IXUSR:
                errors.append(f"{path}: test script is not executable")


def check_scheduler_warmup_gate(errors: list[str]) -> None:
    """XYZ-PILOT-156: start readiness must stay gated on Exasol warm-up."""
    path = Path("scheduler/lambda_function.py")
    text = path.read_text(encoding="utf-8")
    required = {
        "ensure_exasol_warmup function": "def ensure_exasol_warmup()" in text,
        "SSM warm-up dispatch": "ssm.send_command(" in text and "xyz-exasol-warmup.service" in text,
        "start response reports warmup": '"warmup": warmup' in text,
        "email reports warmup": "Warm-up:" in text and "_format_warmup_for_email" in text,
    }
    for label, ok in required.items():
        if not ok:
            errors.append(f"{path}: missing XYZ-PILOT-156 {label}")
    try:
        start_idx = text.index("def handle_start(")
        gate_idx = text.index("ensure_exasol_warmup()", start_idx)
        email_idx = text.index('send_email(state="running"', start_idx)
    except ValueError as exc:
        errors.append(f"{path}: cannot verify XYZ-PILOT-156 warm-up gate order: {exc}")
        return
    if gate_idx > email_idx:
        errors.append(f"{path}: XYZ-PILOT-156 warm-up gate must run before start email")


def check_scheduler_ops_only_label(errors: list[str]) -> None:
    """Restricted routing must be explicit without polluting email subjects."""
    lambda_path = Path("scheduler/lambda_function.py")
    lambda_text = lambda_path.read_text(encoding="utf-8")
    required_lambda = {
        "empty recipient-scope subject prefix": 'ops_prefix = ""' in lambda_text,
        "neutral restricted-routing footer": "This real operational notification was restricted to the configured" in lambda_text,
        "not a test environment explanation": "It is not a dry-run," in lambda_text and "test environment" in lambda_text,
        "OPS-ONLY CloudWatch label": "[OPS-ONLY MODE]" in lambda_text,
        "warm-up warning OPS_ONLY group": 'WARMUP_WARNING_GROUP = "OPS_ONLY"' in lambda_text,
        "warm-up warning OPS_ONLY resolver": "def _warmup_warning_recipients()" in lambda_text,
        "restricted include groups": 'RESTRICTED_NOTIFY_INCLUDE_GROUPS = ("OPS_ONLY", "XYZ_OPS")' in lambda_text,
        "restricted group resolver": "def _restricted_notification_recipients()" in lambda_text,
    }
    for label, ok in required_lambda.items():
        if not ok:
            errors.append(f"{lambda_path}: missing scheduler restricted-notification {label}")
    stale_lambda = (
        '"[TEST] "' in lambda_text
        or "[TEST MODE]" in lambda_text
        or '"[OPS-ONLY] "' in lambda_text
        or "[OPS-ONLY] means this real operational notification" in lambda_text
    )
    if stale_lambda:
        errors.append(f"{lambda_path}: restricted notification must not add TEST/OPS-ONLY labels to email subjects")

    dashboard_path = Path("scheduler/status_dashboard/dashboard.html")
    dashboard_text = dashboard_path.read_text(encoding="utf-8")
    required_dashboard = {
        "ops-only checkbox label": "Ops-only notification" in dashboard_text,
        "OPS_ONLY and XYZ_OPS scope": "OPS_ONLY &amp; XYZ_OPS only" in dashboard_text,
        "default ON checkbox label": "Default ON" in dashboard_text,
        "default checked checkbox": 'id="testModeCb" checked' in dashboard_text,
        "operator log label": "Ops-only notification enabled for this START: OPS_ONLY + XYZ_OPS only" in dashboard_text,
    }
    for label, ok in required_dashboard.items():
        if not ok:
            errors.append(f"{dashboard_path}: missing dashboard restricted-notification {label}")
    stale_dashboard = (
        "TEST MODE — email recipients restricted" in dashboard_text
        or "OPS-ONLY — email recipients restricted" in dashboard_text
        or "<OPS_USER_1>/<OPS_USER_2>/<OPS_USER_3>" in dashboard_text
        or "Default OFF" in dashboard_text
    )
    if stale_dashboard:
        errors.append(f"{dashboard_path}: restricted notification default/log wording is stale")


def check_dashboard_approve_selection_snapshot(errors: list[str]) -> None:
    """XYZ-PILOT-222: Approve handler must not dereference mutable current after async refreshes."""
    path = Path("scheduler/status_dashboard/dashboard.html")
    text = path.read_text(encoding="utf-8")
    try:
        start = text.index("approveBtn.onclick = async () => {")
        end = text.index("};", start)
    except ValueError as exc:
        errors.append(f"{path}: cannot verify XYZ-PILOT-222 approve handler: {exc}")
        return
    handler = text[start:end]
    required = {
        "selection snapshot": "const selected = current;" in handler,
        "null guard uses snapshot": "if (!selected)" in handler,
        "dispatch uses snapshot run_id": "run_id: selected.run_id" in handler,
        "dispatch uses snapshot source": "source: selected.source" in handler,
        "dispatch uses snapshot candidate_index": "candidate_index: selected.candidate_index" in handler,
        "log uses snapshot": "selected.run_id" in handler and "selected.source" in handler,
    }
    for label, ok in required.items():
        if not ok:
            errors.append(f"{path}: missing XYZ-PILOT-222 {label}")


def check_chatbot_region_chart_contract(errors: list[str]) -> None:
    """XYZ-PILOT-105: chatbot chart SQL must not invent REGION on MV snapshots."""
    app_path = Path("services/chatbot/app.py")
    app_text = app_path.read_text(encoding="utf-8")
    required_app = {
        "system prompt names XYZ_REGION_1": "XYZ_REGION_1" in app_text
        and "Never emit bare `REGION`" in app_text,
        "runtime REGION rewrite": "def _rewrite_chart_region_dimension(" in app_text
        and "_CHART_BARE_REGION_RE" in app_text,
        "chart spec field normalizer": "def _normalise_chart_spec_fields(" in app_text,
        "chart runner calls REGION rewrite": "_rewrite_chart_region_dimension(s)" in app_text,
    }
    for label, ok in required_app.items():
        if not ok:
            errors.append(f"{app_path}: missing chatbot regional chart contract {label}")
    if "`XYZ_DASH` is the read-only database user, NOT a schema" not in app_text:
        errors.append(f"{app_path}: missing XYZ_DASH user-not-schema SQL contract")

    visuals_path = Path("docs/30-runbooks/ui/XYZ_PILOT_STREAMLIT_VISUALS.md")
    if not visuals_path.exists():
        return
    visuals_text = visuals_path.read_text(encoding="utf-8")
    try:
        tc5 = visuals_text.split("### TC-5 —", 1)[1].split("### TC-5A —", 1)[0]
        tc5a = visuals_text.split("### TC-5A —", 1)[1].split("\n---", 1)[0]
    except IndexError as exc:
        errors.append(f"{visuals_path}: cannot locate TC-5/TC-5A regional chart sections: {exc}")
        return
    if "XYZ_REGION_1 = 'North America'" not in tc5:
        errors.append(f"{visuals_path}: TC-5 must pin North America to XYZ_REGION_1")
    if "object REGION not found" not in tc5:
        errors.append(f"{visuals_path}: TC-5 must document the REGION-not-found regression guard")
    if "XYZ_REGION_1" not in tc5a:
        errors.append(f"{visuals_path}: TC-5A must name XYZ_REGION_1 as the region/color dimension")
    stale_region = re.compile(
        r"\b(?:WHERE|AND|OR)\s+REGION\s*(?:=|IN\b|LIKE\b|BETWEEN\b)"
        r"|\b(?:GROUP BY|ORDER BY)\s+REGION\b",
        re.IGNORECASE,
    )
    for label, section in (("TC-5", tc5), ("TC-5A", tc5a)):
        if stale_region.search(section):
            errors.append(f"{visuals_path}: {label} contains a stale bare REGION SQL predicate/grouping")


def check_chatbot_tool_loop_contract(errors: list[str]) -> None:
    """XYZ-PILOT-270: multi-hop chatbot turns need checkpointed, actionable caps."""
    app_path = Path("services/chatbot/app.py")
    app_text = app_path.read_text(encoding="utf-8")
    required_app = {
        "configurable ceiling env": "XYZ_CHATBOT_MAX_TOOL_ITERATIONS" in app_text
        and "MAX_TOOL_ITERATIONS = _bounded_int_env" in app_text,
        "checkpoint env": "XYZ_CHATBOT_TOOL_LOOP_CHECKPOINT_AFTER" in app_text
        and "TOOL_LOOP_CHECKPOINT_AFTER" in app_text,
        "partial-result formatter": "def _format_tool_loop_ceiling_answer(" in app_text
        and "Partial progress:" in app_text,
        "mid-loop checkpoint": "Stop exploring and answer from the evidence already collected" in app_text,
        "visual testcase shortcut": (
            "docs/30-runbooks/ui/XYZ_PILOT_STREAMLIT_VISUALS.md" in app_text
            or "docs/30-runbooks/ui/XYZ_PILOT_UI_RUNBOOK.md" in app_text
        )
        and "TC-5/TC-5A" in app_text,
    }
    for label, ok in required_app.items():
        if not ok:
            errors.append(f"{app_path}: missing XYZ-PILOT-270 chatbot tool-loop contract {label}")
    if "Hard ceiling of 8 tool calls" in app_text or "hard ceiling of 8 tool calls" in app_text:
        errors.append(f"{app_path}: stale hard-coded 8-call ceiling text remains")

    readme_path = Path("services/chatbot/README.md")
    readme_text = readme_path.read_text(encoding="utf-8")
    required_readme = {
        "default 14-call ceiling": "defaults to 14" in readme_text
        and "XYZ_CHATBOT_MAX_TOOL_ITERATIONS" in readme_text,
        "checkpoint": "XYZ_CHATBOT_TOOL_LOOP_CHECKPOINT_AFTER" in readme_text
        and "partial progress" in readme_text,
        "TC-5 shortcut": "TC-5 / TC-5A" in readme_text
        and "XYZ-PILOT-270" in readme_text,
    }
    for label, ok in required_readme.items():
        if not ok:
            errors.append(f"{readme_path}: missing XYZ-PILOT-270 docs {label}")


def check_chatbot_target_runtime_baseline(errors: list[str]) -> None:
    """Chatbot target runtime references must match 029/<AWS_REGION> after migration."""
    app_path = Path("services/chatbot/app.py")
    app_text = app_path.read_text(encoding="utf-8")
    required_app = {
        "default AWS region": 'AWS_REGION         = os.environ.get("AWS_REGION", "<AWS_REGION>")' in app_text,
        "default Bedrock model": 'BEDROCK_MODEL_ID", "eu.anthropic.claude-sonnet-4-6"' in app_text,
        "target Cognito pool in self-description": "<COGNITO_USER_POOL_ID>" in app_text,
    }
    for label, ok in required_app.items():
        if not ok:
            errors.append(f"{app_path}: missing target chatbot runtime baseline {label}")

    policy_path = Path("services/chatbot/iam/chatbot-runtime-policy.json")
    policy_text = policy_path.read_text(encoding="utf-8")
    required_policy = {
        "target SSM account/region": "<AWS_ARN><AWS_REGION>:<AWS_ACCOUNT_ID>:parameter/xyz-pilot/*" in policy_text,
        "target Bedrock inference profile": "<AWS_ARN>*:<AWS_ACCOUNT_ID>:inference-profile/eu.anthropic.*" in policy_text,
        "Converse permission": '"bedrock:Converse"' in policy_text,
    }
    for label, ok in required_policy.items():
        if not ok:
            errors.append(f"{policy_path}: missing target chatbot IAM baseline {label}")
    if "<SOURCE_AWS_ACCOUNT_ID>" in policy_text or "eu.anthropic" in policy_text:
        errors.append(f"{policy_path}: stale source account/region/model remains in chatbot runtime policy")

    rp_path = Path("tests/scripts/xyz_regression_probe.py")
    rp_text = rp_path.read_text(encoding="utf-8")
    if "XYZ_DASH is the read-only database user, NOT a schema" not in rp_text:
        errors.append(f"{rp_path}: missing XYZ_DASH user-not-schema SQL contract")


def check_xyz_infrastructure_current_baseline(errors: list[str]) -> None:
    """XYZ-PILOT-271/272: infrastructure agent defaults must match live target."""
    agent_path = Path("tests/agents/XYZ_INFRASTRUCTURE/run.sh")
    agent_text = agent_path.read_text(encoding="utf-8")
    required_agent = {
        "legacy source profile": "source_profile=\"${XYZ_INFRA_SOURCE_PROFILE:-${XYZ_INFRA_LEGACY_PROFILE:-<OPS_USER_1>Degen}}\"" in agent_text,
        "legacy source AZ": "source_az=\"${XYZ_INFRA_SOURCE_AZ:-<AWS_REGION>c}\"" in agent_text,
        "legacy source instance": "source_instance=\"${XYZ_INFRA_SOURCE_EC2_INSTANCE_ID:-<EC2_INSTANCE_ID>}\"" in agent_text,
        "target default profile": "target_profile=\"${XYZ_INFRA_TARGET_PROFILE:-default}\"" in agent_text,
        "target AZ": "target_az=\"${XYZ_INFRA_TARGET_AZ:-<AWS_REGION>f}\"" in agent_text,
        "target instance": "target_instance=\"${XYZ_INFRA_TARGET_EC2_INSTANCE_ID:-<EC2_INSTANCE_ID>}\"" in agent_text,
        "cross-boundary filing disabled by default": "emit_cross_boundary_candidates=\"${XYZ_INFRA_EMIT_CROSS_BOUNDARY_CANDIDATES:-0}\"" in agent_text,
        "concrete stale mapping guard": "only when the run has identified a concrete stale mapping" in agent_text,
    }
    for label, ok in required_agent.items():
        if not ok:
            errors.append(f"{agent_path}: missing current infrastructure baseline {label}")

    workflow_path = Path(".github/workflows/e2e-swarm-nightly.yml")
    workflow_text = workflow_path.read_text(encoding="utf-8")
    required_workflow = {
        "workflow target instance": "EC2_INSTANCE_ID: <EC2_INSTANCE_ID>" in workflow_text
        and "XYZ_EC2_INSTANCE_ID: <EC2_INSTANCE_ID>" in workflow_text,
        "workflow target profile/account role": "<AWS_ARN><AWS_ACCOUNT_ID>:role/xyz-pilot-github-e2e-swarm" in workflow_text,
    }
    for label, ok in required_workflow.items():
        if not ok:
            errors.append(f"{workflow_path}: missing current infrastructure workflow baseline {label}")
    if re.search(r"\bi-[0-9a-f]{8,17}\b", workflow_text):
        errors.append(f"{workflow_path}: concrete EC2 instance ID must not be scheduled in the template")


def check_secret_smoke(errors: list[str]) -> None:
    for path in git_files():
        try:
            if path.stat().st_size > 2_000_000:
                continue
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for name, pattern in SECRET_PATTERNS.items():
            if pattern.search(text):
                errors.append(f"{path}: potential secret match ({name})")


def main() -> int:
    errors: list[str] = []
    check_python_compile(errors)
    check_shell_syntax(errors)
    check_dockerfiles(errors)
    check_test_assets(errors)
    check_scheduler_warmup_gate(errors)
    check_scheduler_ops_only_label(errors)
    check_dashboard_approve_selection_snapshot(errors)
    check_chatbot_region_chart_contract(errors)
    check_chatbot_tool_loop_contract(errors)
    check_chatbot_target_runtime_baseline(errors)
    check_xyz_infrastructure_current_baseline(errors)
    check_secret_smoke(errors)

    for err in errors:
        print(f"ERROR: {err}", file=sys.stderr)
    print(f"regression_static errors={len(errors)}")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
