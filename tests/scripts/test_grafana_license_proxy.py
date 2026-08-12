from __future__ import annotations

import importlib.util
import json
import os
import sys
import types
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]


def _ensure_fastapi_stub() -> None:
    try:
        __import__("fastapi")
        __import__("fastapi.responses")
        return
    except ModuleNotFoundError:
        pass

    fake_fastapi = types.ModuleType("fastapi")
    fake_responses = types.ModuleType("fastapi.responses")

    class HTTPException(Exception):
        def __init__(self, status_code: int, detail: str = "") -> None:
            super().__init__(detail)
            self.status_code = status_code
            self.detail = detail

    class FastAPI:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def get(self, *_args, **_kwargs):
            return lambda func: func

        def post(self, *_args, **_kwargs):
            return lambda func: func

    class JSONResponse:
        def __init__(self, content, headers=None) -> None:
            self.content = content
            self.headers = headers or {}

    def Header(default=None, **_kwargs):
        return default

    def Query(default=None, **_kwargs):
        return default

    fake_fastapi.FastAPI = FastAPI
    fake_fastapi.Header = Header
    fake_fastapi.HTTPException = HTTPException
    fake_fastapi.Query = Query
    fake_responses.JSONResponse = JSONResponse
    sys.modules["fastapi"] = fake_fastapi
    sys.modules["fastapi.responses"] = fake_responses


def _load_proxy_module(tmp_path: Path):
    _ensure_fastapi_stub()
    os.environ.setdefault("EXASOL_HOST", "localhost")
    os.environ.setdefault("EXASOL_USER", "XYZ_DASH")
    os.environ.setdefault("EXASOL_PASSWORD", "dummy")
    os.environ.setdefault("PROXY_TOKEN", "dummy-token")
    os.environ["EXASOL_LICENSE_REFRESH_ON_STARTUP"] = "0"
    os.environ["EXASOL_LICENSE_ARTIFACT_DIR"] = str(tmp_path)
    if "pyexasol" not in sys.modules:
        fake_pyexasol = types.ModuleType("pyexasol")

        def _connect(*_args, **_kwargs):
            raise AssertionError("pyexasol.connect should not be called by license parser tests")

        fake_pyexasol.connect = _connect
        sys.modules["pyexasol"] = fake_pyexasol

    module_path = ROOT / "services/grafana" / "exasol_proxy" / "main.py"
    spec = importlib.util.spec_from_file_location("xyz_exasol_proxy_for_test", module_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


SAMPLE_CONFD = """### play_id = 4a452c38
### db_name = Exasol
### timestamp_utc = 2026-06-23T16:56:03Z

===== confd_client license_info =====
Contract:
  comment: License for a limited number of connections for one installation and exclusively
    for single user usage.
  company_name: Exasol
  distributor: Exasol
  distributor_id: 1
  expiration_date: Unlimited
  license_id: 29
Exasol_DB_license:
  schema_version: 2
Limits:
  max_db_mem_size_in_gb: Unlimited
  max_db_raw_data_size_in_gb: Unlimited
  max_nodes_per_cluster: Unlimited
  max_num_clusters: Unlimited
  max_session_limit: 20

===== confd_client license_run_check =====
OK

===== confd_client db_state (db_name=Exasol) =====
running
"""


def test_confd_license_parser_exposes_contract_and_limits(tmp_path: Path) -> None:
    proxy = _load_proxy_module(tmp_path)
    artifact = tmp_path / "confd_license_info.txt"
    artifact.write_text(SAMPLE_CONFD, encoding="utf-8")

    rows = proxy._parse_confd_license(SAMPLE_CONFD, artifact)
    by_key = {row["KEY"]: row for row in rows if row.get("KEY")}

    assert by_key["license_id"]["SECTION"] == "Contract"
    assert by_key["license_id"]["VALUE"] == "29"
    assert by_key["company_name"]["VALUE"] == "Exasol"
    assert by_key["expiration_date"]["VALUE"] == "Unlimited"
    assert by_key["distributor"]["VALUE"] == "Exasol"
    assert by_key["max_session_limit"]["SECTION"] == "Limits"
    assert by_key["max_session_limit"]["VALUE"] == "20"
    assert by_key["max_db_mem_size_in_gb"]["VALUE"] == "Unlimited"
    assert by_key["max_db_raw_data_size_in_gb"]["VALUE"] == "Unlimited"
    assert by_key["max_nodes_per_cluster"]["VALUE"] == "Unlimited"
    assert by_key["max_num_clusters"]["VALUE"] == "Unlimited"


def test_license_export_payload_groups_contract_and_limits(tmp_path: Path) -> None:
    proxy = _load_proxy_module(tmp_path)
    artifact_dir = tmp_path / "exasol-introspect-20260623T165603Z" / "evidence"
    artifact_dir.mkdir(parents=True)
    artifact = artifact_dir / "confd_license_info.txt"
    artifact.write_text(SAMPLE_CONFD, encoding="utf-8")

    rows = proxy._parse_confd_license(SAMPLE_CONFD, artifact)
    payload = proxy._license_export_payload(rows)

    assert payload["contract"]["license_id"] == "29"
    assert payload["contract"]["company_name"] == "Exasol"
    assert payload["limits"]["max_session_limit"] == "20"
    assert payload["limits"]["max_nodes_per_cluster"] == "Unlimited"
    assert "raw_confd_license_info" in payload


def test_license_export_and_import_plan_allow_optional_auth(tmp_path: Path) -> None:
    proxy = _load_proxy_module(tmp_path)
    # No Authorization header should still work for read-only endpoints.
    export_resp = proxy.license_export(refresh=False, authorization=None)
    plan_resp = proxy.license_import_plan(authorization=None)
    assert isinstance(export_resp.content, dict)
    assert isinstance(plan_resp.content, list)
    assert any(row.get("KEY") == "direct_grafana_import" for row in plan_resp.content)
    assert "Content-Disposition" in (plan_resp.headers or {})


def test_grafana_license_controls_panel_links_export_and_import_plan() -> None:
    dashboard = json.loads(
        (ROOT / "services/grafana" / "grafana" / "dashboards" / "xyz_exasol_monitor.json").read_text(
            encoding="utf-8"
        )
    )
    panels = {panel["title"]: panel for panel in dashboard["panels"]}

    assert "Current License (ConfD authoritative)" in panels
    controls = panels["License Export / Import Controls"]["options"]["content"]
    assert "/api/datasources/proxy/1/license/export?refresh=true" in controls
    assert "/api/datasources/proxy/1/license/import-plan" in controls
    assert "scripts/exasol-personal-license-export.sh" in controls
    assert "scripts/exasol-personal-license-rotate.sh" in controls
    assert "--apply --confirm-apply" in controls


def test_grafana_datasource_proxy_forwards_authorization_header() -> None:
    datasource_yaml = (
        ROOT / "services/grafana" / "grafana" / "provisioning" / "datasources" / "datasources.yml"
    ).read_text(encoding="utf-8")

    assert "uid: exasol_xyz" in datasource_yaml
    assert "httpHeaderName1: Authorization" in datasource_yaml
    assert "httpHeaderValue1: Bearer ${PROXY_TOKEN}" in datasource_yaml


def test_license_rotation_script_has_guarded_apply_path() -> None:
    script = (ROOT / "scripts" / "exasol-personal-license-rotate.sh").read_text(encoding="utf-8")

    assert "license_upload" in script
    assert "timeout \"${LICENSE_UPLOAD_TIMEOUT_SECONDS:-180}\"" in script
    assert '"license: \'\\"{< -}\\"\'"' in script
    assert "--apply requires --confirm-apply" in script
    assert "exasol-personal-license-export.sh" in script
    assert "license_run_check" in script
    assert "operator-dashboard-license-evidence.json" in script
    assert "XYZ_LICENSE_EVIDENCE_BUCKET" in script
    assert "license/evidence.json" in script
    assert "AWS_PROFILE=\"${AWS_PROFILE:-default}\" AWS_REGION=\"${AWS_REGION:-<AWS_REGION>}\" aws s3 cp" in script
    assert "Non-secret evidence only; signed license bodies are not included." in script


def test_license_export_script_uses_personal_tag_and_does_not_print_license() -> None:
    script = (ROOT / "scripts" / "exasol-personal-license-export.sh").read_text(encoding="utf-8")

    assert ".registry/tag/license:personal" in script
    assert 'MAPPED_BRANCH="${BRANCH/branchr-/branchr/}"' in script
    assert ".ccc/x/u/$MAPPED_BRANCH/license.xml" in script
    assert "scp -q" in script
    assert "never prints license file contents" in script
