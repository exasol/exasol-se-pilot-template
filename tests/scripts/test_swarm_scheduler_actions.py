"""Unit tests for swarm_schedule helpers and action (XYZ-PILOT-250 Task 3).

boto3 is stubbed at import so the module loads without AWS credentials.
"""
import datetime as dt
import importlib.util
import sys
import types
from pathlib import Path

import pytest

# Stub boto3 before the module is loaded so module-level client() calls
# don't attempt real AWS connections.
sys.modules.setdefault("boto3", types.SimpleNamespace(client=lambda *a, **k: None))

SPEC = Path(__file__).resolve().parents[2] / "scheduler" / "xyz_pilot_user_mgmt.py"
spec = importlib.util.spec_from_file_location("umgmt", SPEC)
umgmt = importlib.util.module_from_spec(spec)
spec.loader.exec_module(umgmt)


def test_cest_to_utc():
    assert umgmt._cest_to_utc("2026-06-20", "22:30") == "2026-06-20T20:30:00Z"


def test_validate_rejects_past():
    now = dt.datetime(2026, 6, 19, 12, 0, tzinfo=dt.timezone.utc)
    with pytest.raises(umgmt.SwarmScheduleError):
        umgmt._validate_fire("2026-06-19T10:00:00Z", now=now)


def test_validate_rejects_far_future():
    now = dt.datetime(2026, 6, 19, 12, 0, tzinfo=dt.timezone.utc)
    with pytest.raises(umgmt.SwarmScheduleError):
        umgmt._validate_fire("2026-08-30T10:00:00Z", now=now)


def test_validate_stress_range():
    with pytest.raises(umgmt.SwarmScheduleError):
        umgmt._validate_stress(51, allow_higher=True)
    with pytest.raises(umgmt.SwarmScheduleError):
        umgmt._validate_stress(15, allow_higher=False)
    assert umgmt._validate_stress(10, allow_higher=False) == 10
    assert umgmt._validate_stress(50, allow_higher=True) == 50


def test_advanced_overrides_allowlist():
    assert umgmt._parse_advanced("XYZ_STRESS_THINK_MS=500") == {"XYZ_STRESS_THINK_MS": "500"}
    with pytest.raises(umgmt.SwarmScheduleError):
        umgmt._parse_advanced("GITHUB_TOKEN=abc")
    with pytest.raises(umgmt.SwarmScheduleError):
        umgmt._parse_advanced("XYZ_FAKE_PASSWORD=x")


def test_build_workflow_inputs_maps_config():
    cfg = {
        "agents": ["XYZ_SMOKE", "XYZ_E2E", "XYZ_STRESS"],
        "stress_users": 10,
        "session_hold_seconds": 20,
        "dry_run": False,
        "force_run": False,
        "notify": True,
        "notify_recipients": ["<OPERATOR_EMAIL>"],
    }
    wi = umgmt._build_workflow_inputs(cfg)
    assert wi["tester_agents"] == "XYZ_SMOKE XYZ_E2E"
    assert wi["include_stress"] is True
    assert wi["stress_users"] == "10"
    assert wi["notify"] is True
    assert wi["notify_recipients"] == "<OPERATOR_EMAIL>"


# ---- T4: swarm_dispatch ----
def test_swarm_dispatch_posts_workflow(monkeypatch):
    calls = {}
    monkeypatch.setattr(umgmt, "_gh_request",
        lambda method, path, body=None, **k: calls.update(method=method, path=path, body=body) or {})
    cfg = {"agents": ["XYZ_SMOKE"], "stress_users": 10, "session_hold_seconds": 20,
           "notify": True, "notify_recipients": ["x@exasol.com"]}
    out = umgmt._swarm_dispatch(cfg, schedule_name=None)
    assert calls["method"] == "POST"
    assert "actions/workflows/e2e-swarm-nightly.yml/dispatches" in calls["path"]
    assert calls["body"]["ref"] == "main"
    assert calls["body"]["inputs"]["tester_agents"] == "XYZ_SMOKE"
    assert calls["body"]["inputs"]["include_stress"] in ("true", "false")
    assert out["dispatched"] is True


# ---- T5: list + cancel ----
def test_schedules_list(monkeypatch):
    fake = {"Schedules": [{"Name": "xyz-pilot-swarm-20260620T203000Z"}]}
    monkeypatch.setattr(umgmt, "_scheduler_client",
        lambda: types.SimpleNamespace(
            list_schedules=lambda **k: fake,
            get_schedule=lambda **k: {"ScheduleExpression": "at(2026-06-20T20:30:00)",
                "Target": {"Input": '{"config":{"agents":["XYZ_SMOKE"]}}'}}))
    out = umgmt._swarm_schedules_list()
    assert out["schedules"][0]["name"].startswith("xyz-pilot-swarm-")
    assert out["schedules"][0]["agents"] == ["XYZ_SMOKE"]


def test_cancel_idempotent(monkeypatch):
    class NF(Exception):
        pass
    def deleter(**k):
        raise NF()
    monkeypatch.setattr(umgmt, "_scheduler_client",
        lambda: types.SimpleNamespace(delete_schedule=deleter,
            exceptions=types.SimpleNamespace(ResourceNotFoundException=NF)))
    assert umgmt._swarm_schedule_cancel("missing")["ok"] is True


# ---- T10: results retrieval ----
def test_parse_run_results():
    md = "| x | PASS | |\n| y | FAIL |\n| z | WARN |"
    r = umgmt._parse_run_results(md)
    assert r["pass"] == 1 and r["fail"] == 1 and r["warn"] == 1


def test_swarm_runs_list(monkeypatch):
    # git-trees recursive response: only run-ids with a manifest.md are real swarm runs.
    monkeypatch.setattr(umgmt, "_gh_request",
        lambda m, p, body=None, **k: {"truncated": False, "tree": [
            {"path": "tests/logs/runs/2026-06-19T010000Z/manifest.md", "type": "blob"},
            {"path": "tests/logs/runs/2026-06-18T010000Z/manifest.md", "type": "blob"},
            {"path": "tests/logs/runs/2026-06-17T010000Z/codex/issue-candidates.md", "type": "blob"},
            {"path": "tests/logs/runs/2026-06-19T010000Z/XYZ_SMOKE/results.md", "type": "blob"},
        ]})
    out = umgmt._swarm_runs_list()
    assert out["runs"][0] == "2026-06-19T010000Z"      # newest first, deduped
    assert "2026-06-17T010000Z" not in out["runs"]     # candidate folder (no manifest) excluded
    assert out["total_swarm_runs"] == 2


def test_swarm_run_results(monkeypatch):
    def fake_gh(m, p, body=None, **k):
        if p.endswith("/contents/tests/logs/runs/RID"):
            return [{"name": "XYZ_SMOKE", "type": "dir"},
                    {"name": "candidate-outcomes", "type": "dir"}]
        if p.endswith("/XYZ_SMOKE/results.md"):
            return {"encoding": "base64",
                    "content": umgmt.base64.b64encode(b"PASS PASS FAIL").decode()}
        if p.endswith("/candidate-outcomes"):
            return [{"name": "c1.md", "type": "file"}]
        return []
    monkeypatch.setattr(umgmt, "_gh_request", fake_gh)
    out = umgmt._swarm_run_results("RID")
    assert out["run_id"] == "RID"
    assert out["agents"][0]["name"] == "XYZ_SMOKE"
    assert out["agents"][0]["pass"] == 2 and out["agents"][0]["fail"] == 1
    assert out["candidates"] == 1
    assert "tree/main/tests/logs/runs/RID" in out["github_url"]
