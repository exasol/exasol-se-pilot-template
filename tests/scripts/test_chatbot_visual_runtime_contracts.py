#!/usr/bin/env python3
"""Focused chatbot visual/runtime regression checks."""

from __future__ import annotations

import ast
import base64
from datetime import datetime, timezone
import hashlib
import html
import json
import os
from pathlib import Path
import re
from typing import Any
import unittest
import urllib
import urllib.parse


ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "services/chatbot" / "app.py"
COMPOSE = ROOT / "services/chatbot" / "docker-compose.yml"


def _load_chart_helpers() -> dict[str, Any]:
    """Execute only the pure chart helper definitions from services/chatbot/app.py."""
    wanted = {
        "_CHART_VIEW_TO_MV_REWRITES",
        "_CHART_XYZ_REGION_1_OBJECTS",
        "_CHART_XYZ_REGION_1_OBJECT_RE",
        "_CHART_BARE_REGION_RE",
        "_CHART_PERIOD_QUARTER_RE",
        "_CHART_PERIOD_QUARTER_PERIOD_RE",
        "_CHART_PERIOD_QUARTER_CONCAT_RE",
        "_rewrite_chart_views_to_mv",
        "_rewrite_chart_region_dimension",
        "_rewrite_chart_yoy_quarter_dimension",
        "_normalise_chart_spec_fields",
        "_INLINE_VISUAL_FENCE_RE",
        "_VISUAL_INCIDENT_OPERATOR_PREFIX",
        "_tc5_complex_visualization_answer",
        "_customer_yoy_retention_answer",
        "_DOT_START_RE",
        "_extract_unfenced_dot_block",
        "_normalize_diagram_fences",
        "_operator_visual_incident_probe_answer",
        "_archive_qa",
        "_slugify",
        "_mermaid_component_height",
        "_mermaid_component_html",
        "_dot_component_height",
        "_dot_svg_component_html",
        "_DIAGRAMS_NODE_REGISTRY",
        "_diagrams_type_key",
        "_PLOTLY_COMBO_TYPES",
        "_PLOTLY_SECONDARY_SPEC_KEYS",
        "_PLOTLY_PRIMARY_SPEC_KEYS",
        "XYZ_PLOTLY_EVENTS_ENABLED",
        "_chart_field_names",
        "_chart_spec_existing_fields",
        "_is_rate_or_percent_field",
        "_infer_plotly_combo_fields",
        "_plotly_is_combo_spec",
        "_plotly_should_capture_events",
        "_next_chart_render_key",
    }
    module = ast.parse(APP.read_text(encoding="utf-8"), filename=str(APP))
    selected: list[ast.stmt] = []
    for node in module.body:
        if isinstance(node, ast.Assign):
            names = {target.id for target in node.targets if isinstance(target, ast.Name)}
            if names & wanted:
                selected.append(node)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            if node.target.id in wanted:
                selected.append(node)
        elif isinstance(node, ast.FunctionDef) and node.name in wanted:
            selected.append(node)

    namespace: dict[str, Any] = {
        "Any": Any,
        "hashlib": hashlib,
        "html": html,
        "json": json,
        "os": os,
        "_re": re,
        "re": re,
        "base64": base64,
        "datetime": datetime,
        "timezone": timezone,
        "urllib": urllib,
        "GITHUB_REPO": "<GITHUB_OWNER>/<PROJECT_SLUG>",
    }
    helper_module = ast.Module(body=selected, type_ignores=[])
    ast.fix_missing_locations(helper_module)
    exec(compile(helper_module, str(APP), "exec"), namespace)
    return namespace


class ChatbotVisualRuntimeContractsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.helpers = _load_chart_helpers()

    def test_chatbot_container_has_host_gateway_for_exasol_chart_sql(self) -> None:
        text = COMPOSE.read_text(encoding="utf-8")
        chatbot_block = text.split("  xyz-chatbot:", 1)[1].split("  mcp-internal:", 1)[0]

        self.assertIn("extra_hosts:", chatbot_block)
        self.assertIn('"host.docker.internal:host-gateway"', chatbot_block)
        self.assertIn("Could not resolve IP address", chatbot_block)

    def test_region_filter_is_rewritten_for_mv_snapshot_chart_sql(self) -> None:
        rewrite = self.helpers["_rewrite_chart_region_dimension"]
        sql = (
            "SELECT QUARTER_BY_YEAR AS QUARTER_LBL, SUM(SALES_CY) AS SALES_CY "
            "FROM <PILOT_SCHEMA>.MV_YOY_METRICS "
            "WHERE REGION = 'North America' "
            "GROUP BY QUARTER_BY_YEAR, REGION ORDER BY REGION"
        )

        out = rewrite(sql)

        self.assertIn("WHERE XYZ_REGION_1 = 'North America'", out)
        self.assertIn("GROUP BY QUARTER_BY_YEAR, XYZ_REGION_1", out)
        self.assertIn("ORDER BY XYZ_REGION_1", out)
        self.assertNotRegex(out, r"\bWHERE\s+REGION\b")

    def test_region_rewrite_ignores_string_literals_and_dotted_columns(self) -> None:
        rewrite = self.helpers["_rewrite_chart_region_dimension"]
        sql = (
            "SELECT dim.REGION AS REGION_NAME, 'REGION' AS TOKEN "
            "FROM <PILOT_SCHEMA>.MV_YOY_METRICS y JOIN DIM dim ON dim.ID = y.ID "
            "WHERE dim.REGION = 'North America'"
        )

        self.assertEqual(sql, rewrite(sql))

    def test_view_rewrite_then_region_rewrite_matches_tc5_regression(self) -> None:
        view_rewrite = self.helpers["_rewrite_chart_views_to_mv"]
        region_rewrite = self.helpers["_rewrite_chart_region_dimension"]
        sql = (
            "SELECT QUARTER_BY_YEAR, SUM(SALES_CY) "
            "FROM <PILOT_SCHEMA>.V_YOY_METRICS WHERE REGION = 'North America' "
            "GROUP BY QUARTER_BY_YEAR"
        )

        out = region_rewrite(view_rewrite(sql))

        self.assertIn("<PILOT_SCHEMA>.MV_YOY_METRICS", out)
        self.assertIn("WHERE XYZ_REGION_1 = 'North America'", out)

    def test_direct_tc5_prompt_sql_rewrites_invented_invoice_quarter(self) -> None:
        rewrite = self.helpers["_rewrite_chart_yoy_quarter_dimension"]
        sql = (
            "SELECT PERIOD_YEAR AS YEAR_LBL, PERIOD_QUARTER AS QTR_LBL, "
            "ROUND(SUM(SALES_CY)/1e6, 2) AS VALUE_CY_M, "
            "ROUND(SUM(SALES_PY)/1e6, 2) AS VALUE_PY_M "
            "FROM <PILOT_SCHEMA>.MV_YOY_METRICS "
            "WHERE XYZ_REGION_1 = 'North America' AND PERIOD_YEAR IN (2024, 2025) "
            "GROUP BY PERIOD_YEAR, PERIOD_QUARTER "
            "ORDER BY PERIOD_YEAR, PERIOD_QUARTER"
        )

        out = rewrite(sql)

        self.assertIn("QUARTER_BY_YEAR AS QTR_LBL", out)
        self.assertIn("GROUP BY PERIOD_YEAR, QUARTER_BY_YEAR", out)
        self.assertIn("ORDER BY PERIOD_YEAR, QUARTER_BY_YEAR", out)
        self.assertNotIn("PERIOD_QUARTER", out)

    def test_direct_tc5_prompt_sql_rewrites_period_concat_shape(self) -> None:
        rewrite = self.helpers["_rewrite_chart_yoy_quarter_dimension"]
        sql = (
            "SELECT PERIOD_YEAR || ' Q' || PERIOD_QUARTER AS PERIOD_LBL, "
            "SUM(SALES_CY) AS SALES_CY "
            "FROM <PILOT_SCHEMA>.MV_YOY_METRICS "
            "GROUP BY PERIOD_YEAR || ' Q' || PERIOD_QUARTER"
        )

        out = rewrite(sql)

        self.assertIn("QUARTER_BY_YEAR AS PERIOD_LBL", out)
        self.assertIn("GROUP BY QUARTER_BY_YEAR", out)
        self.assertNotIn("PERIOD_QUARTER", out)

    def test_invoice_quarter_rewrite_is_scoped_to_yoy_snapshot(self) -> None:
        rewrite = self.helpers["_rewrite_chart_yoy_quarter_dimension"]
        sql = "SELECT PERIOD_QUARTER FROM OTHER_SCHEMA.OTHER_TABLE"

        self.assertEqual(sql, rewrite(sql))

    def test_region_chart_fields_normalise_to_actual_xyz_region_column(self) -> None:
        normalise = self.helpers["_normalise_chart_spec_fields"]
        spec = {"x": "SEGMENT", "color": "REGION", "tooltip": ["SEGMENT", "REGION"]}

        out = normalise(spec, ["SEGMENT", "XYZ_REGION_1", "SALES_M_USD"])

        self.assertEqual("XYZ_REGION_1", out["color"])
        self.assertEqual(["SEGMENT", "XYZ_REGION_1"], out["tooltip"])

    def test_plotly_combo_trace_plan_matches_customer_yoy_retention_shape(self) -> None:
        infer = self.helpers["_infer_plotly_combo_fields"]
        is_combo = self.helpers["_plotly_is_combo_spec"]
        spec = {
            "type": "combo",
            "engine": "plotly",
            "x": "PERIOD_YEAR",
            "y": ["SALES_CY", "SALES_PY"],
            "secondary_y": "SALES_YOY_PCT_AVG",
            "title": "<CUSTOMER_NAME> — YoY Sales Retention",
            "y_label": "Sales",
            "y2_label": "YoY %",
        }
        columns = [
            "PERIOD_YEAR",
            "SALES_CY",
            "SALES_PY",
            "SALES_YOY_VAR",
            "SALES_YOY_PCT_AVG",
            "QTY_CY",
            "QTY_PY",
        ]

        primary, secondary = infer(spec, columns)

        self.assertTrue(is_combo(spec))
        self.assertEqual(["SALES_CY", "SALES_PY"], primary)
        self.assertEqual(["SALES_YOY_PCT_AVG"], secondary)

    def test_plotly_combo_trace_plan_accepts_llm_bar_line_synonyms(self) -> None:
        infer = self.helpers["_infer_plotly_combo_fields"]
        is_combo = self.helpers["_plotly_is_combo_spec"]
        spec = {
            "type": "bar_line",
            "x": "PERIOD_YEAR",
            "bars": [{"field": "SALES_CY"}, {"field": "SALES_PY"}],
            "lines": [{"field": "YOY_PCT"}],
        }
        columns = ["PERIOD_YEAR", "SALES_CY", "SALES_PY", "YOY_PCT"]

        primary, secondary = infer(spec, columns)

        self.assertTrue(is_combo(spec))
        self.assertEqual(["SALES_CY", "SALES_PY"], primary)
        self.assertEqual(["YOY_PCT"], secondary)

    def test_plotly_combo_trace_plan_infers_rate_columns_as_secondary(self) -> None:
        infer = self.helpers["_infer_plotly_combo_fields"]
        spec = {
            "type": "combo",
            "x": "PERIOD_YEAR",
            "tooltip": ["PERIOD_YEAR", "SALES_CY", "SALES_PY", "RETENTION_PCT"],
        }
        columns = ["PERIOD_YEAR", "SALES_CY", "SALES_PY", "RETENTION_PCT"]

        primary, secondary = infer(spec, columns)

        self.assertEqual(["SALES_CY", "SALES_PY"], primary)
        self.assertEqual(["RETENTION_PCT"], secondary)

    def test_plotly_event_capture_is_opt_in_to_prevent_empty_frames(self) -> None:
        should_capture = self.helpers["_plotly_should_capture_events"]

        self.assertFalse(should_capture({"type": "bar", "engine": "plotly"}, combo_rendered=False))
        self.assertFalse(should_capture({"type": "line", "y": ["VALUE_CY_M", "VALUE_PY_M"]}, combo_rendered=False))
        self.assertFalse(should_capture({"type": "combo", "capture_events": True}, combo_rendered=True))
        self.assertTrue(should_capture({"type": "bar", "capture_events": True}, combo_rendered=False))
        self.assertTrue(should_capture({"type": "bar", "click_events": "yes"}, combo_rendered=False))

    def test_plotly_render_keys_are_unique_for_repeated_tc5_charts(self) -> None:
        next_key = self.helpers["_next_chart_render_key"]
        if hasattr(next_key, "_fallback_sequence"):
            delattr(next_key, "_fallback_sequence")

        class FakeDf:
            columns = ["QUARTER_LBL", "VALUE_CY_M", "VALUE_PY_M"]
            shape = (8, 3)

        spec = {
            "type": "line",
            "engine": "plotly",
            "x": "QUARTER_LBL",
            "y": ["VALUE_CY_M", "VALUE_PY_M"],
            "title": "North America — YoY Sales CY vs PY by Quarter (2024-2025)",
        }

        key1 = next_key("plotly_chart", spec, FakeDf(), "chart_container_native")
        key2 = next_key("plotly_chart", spec, FakeDf(), "chart_container_native")
        event_key = next_key("plotly_evt", spec, FakeDf(), "chart_container_events")

        self.assertRegex(key1, r"^plotly_chart_[0-9a-f]{12}_1$")
        self.assertRegex(key2, r"^plotly_chart_[0-9a-f]{12}_2$")
        self.assertRegex(event_key, r"^plotly_evt_[0-9a-f]{12}_3$")
        self.assertNotEqual(key1, key2)
        self.assertNotEqual(key1, event_key)

    def test_plotly_renderer_passes_explicit_keys_to_streamlit_surfaces(self) -> None:
        text = APP.read_text(encoding="utf-8")

        self.assertNotIn("st.plotly_chart(fig, use_container_width=True)", text)
        self.assertIn('key=_next_chart_render_key("plotly_chart"', text)
        self.assertIn('key=_next_chart_render_key("plotly_evt"', text)

    def test_architecture_doc_does_not_route_secret_lambdas_from_eventbridge(self) -> None:
        c4_doc = (ROOT / "docs" / "20-architecture" / "XYZ_PILOT_AWS_C4_CONTAINER.md").read_text(
            encoding="utf-8"
        )

        self.assertNotIn("eb --> lam_secret", c4_doc)
        self.assertNotIn("eb --> lam_syspw", c4_doc)
        self.assertIn("s3 --> lam_secret", c4_doc)
        self.assertIn("s3 --> lam_syspw", c4_doc)

    def test_prompt_contract_pins_xyz_pilot_275_trigger_mapping(self) -> None:
        text = APP.read_text(encoding="utf-8")

        self.assertIn("<PILOT_NAME> trigger-label truth source (XYZ-PILOT-275)", text)
        self.assertIn("xyz-pilot-get-secret", text)
        self.assertIn("not EventBridge cron targets", text)

    def test_mermaid_component_has_visible_error_surface_for_tc1a(self) -> None:
        render_html = self.helpers["_mermaid_component_html"]
        component_height = self.helpers["_mermaid_component_height"]
        code = """flowchart LR
    subgraph Browser
        U[Subscriber]
    end
    subgraph Chatbot
        ST[Streamlit App]
    end
    subgraph MCP
        MCP_INT[mcp-internal sidecar]
    end
    subgraph Exasol
        DB[(<PILOT_SCHEMA>)]
    end
    subgraph GitHub
        REPO[Private Repo]
    end
    U --> ST
    ST --> MCP_INT
    MCP_INT --> DB
    ST --> REPO"""

        out = render_html(code)

        self.assertIn("https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.min.js", out)
        self.assertIn("renderMermaidDiagram()", out)
        self.assertIn("showMermaidError", out)
        self.assertIn("Mermaid renderer failed.", out)
        self.assertIn("flowchart LR", out)
        self.assertGreaterEqual(component_height(code), 420)
        self.assertLessEqual(component_height(code), 760)

    def test_mermaid_component_escapes_script_terminators(self) -> None:
        render_html = self.helpers["_mermaid_component_html"]
        code = "flowchart LR\n  A[</script><script>alert(1)</script>] --> B"

        out = render_html(code)

        self.assertNotIn("</script><script>alert(1)</script>", out)
        self.assertIn("&lt;/script&gt;&lt;script&gt;alert(1)&lt;/script&gt;", out)

    def test_inline_chart_fence_is_normalised_for_tc2a(self) -> None:
        normalise = self.helpers["_normalize_diagram_fences"]
        text = (
            'For visual-regression testing only, return exactly this invalid chart block '
            'and no explanation: ```chart {"engine":"altair","type":"bar","sql": } ```'
        )

        out = normalise(text)

        self.assertIn('```chart\n{"engine":"altair","type":"bar","sql": }\n```', out)

    def test_operator_visual_incident_probe_bypasses_bedrock(self) -> None:
        probe = self.helpers["_operator_visual_incident_probe_answer"]
        text = (
            'For visual-regression testing only, return exactly this invalid chart block '
            'and no explanation: ```chart {"engine":"altair","type":"bar","x":"SEGMENT","y":"SALES","sql": } ```'
        )

        out = probe(text)

        self.assertEqual(
            '```chart\n{"engine":"altair","type":"bar","x":"SEGMENT","y":"SALES","sql": }\n```',
            out,
        )

    def test_customer_yoy_retention_prompt_gets_three_deterministic_panels(self) -> None:
        answer_for = self.helpers["_customer_yoy_retention_answer"]

        out = answer_for("What is the <CUSTOMER_NAME>s YoY retention during last four years ? Provide rich visualization")

        self.assertIsNotNone(out)
        assert out is not None
        self.assertEqual(3, out.count("```chart"))
        self.assertIn("Panel 1 — CY vs PY Sales Trend", out)
        self.assertIn("Panel 2 — Sales Volume + YoY Growth %", out)
        self.assertIn("Panel 3 — YoY Delta", out)
        self.assertIn('"type": "line"', out)
        self.assertIn('"type": "combo"', out)
        self.assertIn('"type": "bar"', out)
        self.assertIn('"y": ["VALUE_CY_M", "VALUE_PY_M"]', out)
        self.assertIn('"secondary_y": "YOY_PCT"', out)
        self.assertIn('"y": "YOY_DELTA_M"', out)
        self.assertIn("<PILOT_SCHEMA>.MV_YOY_METRICS", out)

    def test_customer_yoy_retention_shortcut_does_not_catch_unrelated_prompts(self) -> None:
        answer_for = self.helpers["_customer_yoy_retention_answer"]

        self.assertIsNone(answer_for("Show <CUSTOMER_NAME> inventory as a table"))
        self.assertIsNone(answer_for("What is Helical Solutions YoY retention? Provide rich visualization."))

    def test_tc5_complex_visualization_meta_prompt_uses_valid_quarter_contract(self) -> None:
        answer_for = self.helpers["_tc5_complex_visualization_answer"]

        out = answer_for("Lookup the most complex chatbot visualization testcase and execute this one here at that prompt !")

        self.assertIsNotNone(out)
        assert out is not None
        self.assertEqual(1, out.count("```chart"))
        self.assertIn("TC-5", out)
        self.assertIn("<PILOT_SCHEMA>.MV_YOY_METRICS", out)
        self.assertIn("QUARTER_BY_YEAR AS QUARTER_LBL", out)
        self.assertIn("XYZ_REGION_1 = 'North America'", out)
        self.assertIn('"x": "QUARTER_LBL"', out)
        self.assertIn('"y": ["VALUE_CY_M", "VALUE_PY_M"]', out)
        self.assertNotIn("PERIOD_QUARTER", out)
        self.assertNotIn("REGION = 'North America'", out)

    def test_tc5_complex_visualization_shortcut_does_not_catch_general_chart_prompts(self) -> None:
        answer_for = self.helpers["_tc5_complex_visualization_answer"]

        self.assertIsNone(answer_for("Show the most complex sales chart by brand."))
        self.assertIsNone(answer_for("Execute TC-5"))

    def test_qa_archive_supplies_sha_when_same_day_prompt_report_exists(self) -> None:
        archive_qa = self.helpers["_archive_qa"]

        class Response:
            def __init__(self, status_code: int, payload: dict[str, Any] | None = None, text: str = "") -> None:
                self.status_code = status_code
                self._payload = payload or {}
                self.text = text

            def json(self) -> dict[str, Any]:
                return self._payload

        class Requests:
            put_payload: dict[str, Any] | None = None

            @staticmethod
            def get(*_args: Any, **_kwargs: Any) -> Response:
                return Response(200, {"sha": "existing-archive-sha"})

            @classmethod
            def put(cls, *_args: Any, json: dict[str, Any], **_kwargs: Any) -> Response:
                cls.put_payload = json
                return Response(200, {"content": {"html_url": "https://github.example/report.md"}})

        old_requests = self.helpers.get("requests")
        old_get_pat = self.helpers.get("_get_pat")
        try:
            self.helpers["requests"] = Requests
            self.helpers["_get_pat"] = lambda: "token"

            ok, url = archive_qa(
                "subscriber@example.invalid",
                "What is the <CUSTOMER_NAME>s YoY retention during last four years ? Provide rich visualization",
                "three rendered chart panels",
            )

            self.assertTrue(ok)
            self.assertEqual("https://github.example/report.md", url)
            self.assertIsNotNone(Requests.put_payload)
            assert Requests.put_payload is not None
            self.assertEqual("existing-archive-sha", Requests.put_payload["sha"])
        finally:
            if old_requests is None:
                self.helpers.pop("requests", None)
            else:
                self.helpers["requests"] = old_requests
            if old_get_pat is None:
                self.helpers.pop("_get_pat", None)
            else:
                self.helpers["_get_pat"] = old_get_pat

    def test_qa_archive_creates_without_sha_when_report_is_new(self) -> None:
        archive_qa = self.helpers["_archive_qa"]

        class Response:
            def __init__(self, status_code: int, payload: dict[str, Any] | None = None, text: str = "") -> None:
                self.status_code = status_code
                self._payload = payload or {}
                self.text = text

            def json(self) -> dict[str, Any]:
                return self._payload

        class Requests:
            put_payload: dict[str, Any] | None = None

            @staticmethod
            def get(*_args: Any, **_kwargs: Any) -> Response:
                return Response(404, text="not found")

            @classmethod
            def put(cls, *_args: Any, json: dict[str, Any], **_kwargs: Any) -> Response:
                cls.put_payload = json
                return Response(201, {"content": {"html_url": "https://github.example/report.md"}})

        old_requests = self.helpers.get("requests")
        old_get_pat = self.helpers.get("_get_pat")
        try:
            self.helpers["requests"] = Requests
            self.helpers["_get_pat"] = lambda: "token"

            ok, url = archive_qa("subscriber@example.invalid", "Brand new QA prompt", "answer")

            self.assertTrue(ok)
            self.assertEqual("https://github.example/report.md", url)
            self.assertIsNotNone(Requests.put_payload)
            assert Requests.put_payload is not None
            self.assertNotIn("sha", Requests.put_payload)
        finally:
            if old_requests is None:
                self.helpers.pop("requests", None)
            else:
                self.helpers["requests"] = old_requests
            if old_get_pat is None:
                self.helpers.pop("_get_pat", None)
            else:
                self.helpers["_get_pat"] = old_get_pat

    def test_dot_svg_component_wraps_server_side_graphviz_output(self) -> None:
        wrap_svg = self.helpers["_dot_svg_component_html"]
        component_height = self.helpers["_dot_component_height"]
        svg = '<svg><g><title>G</title><text>FACT_PRIMARY</text></g></svg>'

        out = wrap_svg(svg)

        self.assertIn("dot-frame", out)
        self.assertIn("FACT_PRIMARY", out)
        self.assertGreaterEqual(component_height("digraph G { A -> B }"), 420)
        self.assertLessEqual(component_height("digraph G { A -> B }"), 760)

    def test_unfenced_dot_reply_is_extractable_for_tc2_regression(self) -> None:
        extract = self.helpers["_extract_unfenced_dot_block"]
        text = """Here is the <PILOT_SCHEMA> relationship diagram:

digraph <PILOT_SCHEMA> {
    rankdir=LR
    FACT_PRIMARY [label="{FACT_PRIMARY (FACT)|ACCOUNT_KEY\\lENDUSERACCTKEY\\l}"]
    DIM_ACCOUNT -> FACT_PRIMARY [label="FK_IS_ACCOUNT_KEY"]
}

Use the DOT source only for debugging."""

        hit = extract(text)

        self.assertIsNotNone(hit)
        start, end, code = hit
        self.assertGreater(start, 0)
        self.assertLess(end, len(text))
        self.assertTrue(code.startswith("digraph <PILOT_SCHEMA> {"))
        self.assertIn("DIM_ACCOUNT -> FACT_PRIMARY", code)
        self.assertNotIn("Use the DOT source", code)

    def test_unfenced_truncated_dot_reply_still_extracts_error_surface_input(self) -> None:
        extract = self.helpers["_extract_unfenced_dot_block"]
        text = "Schema graph:\n\ndigraph <PILOT_SCHEMA> {\n  A -> B\n"

        hit = extract(text)

        self.assertIsNotNone(hit)
        _start, end, code = hit
        self.assertEqual(len(text), end)
        self.assertIn("A -> B", code)

    def test_diagrams_dsl_cloudflare_keys_use_installed_saas_cdn_icon_path(self) -> None:
        registry = self.helpers["_DIAGRAMS_NODE_REGISTRY"]
        normalise_type = self.helpers["_diagrams_type_key"]

        self.assertEqual("cloudflared", normalise_type(" CloudFlared "))
        self.assertEqual("diagrams.saas.cdn.Cloudflare", registry["cloudflare"])
        self.assertEqual("diagrams.saas.cdn.Cloudflare", registry["cloudflared"])
        self.assertNotIn("diagrams.onprem.network.Cloudflare", registry.values())


if __name__ == "__main__":
    unittest.main()
