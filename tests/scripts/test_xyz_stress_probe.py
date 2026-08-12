from __future__ import annotations

import importlib.util
from pathlib import Path
import tempfile
import textwrap
import unittest
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = REPO_ROOT / "tests" / "scripts" / "xyz_stress_probe.py"


def load_module():
    spec = importlib.util.spec_from_file_location("xyz_stress_probe_test", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class HpcStressProbeTests(unittest.TestCase):
    def setUp(self):
        self.module = load_module()

    def test_load_warmup_queries_from_ast_without_import(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "warmup.py"
            path.write_text(
                textwrap.dedent(
                    """
                    import os
                    EXA_PASSWORD = os.environ["EXA_PASSWORD"]
                    WARMUP_QUERIES = [
                        ("a", "SELECT " "1"),
                        ("b", "SELECT 2"),
                    ]
                    """
                ),
                encoding="utf-8",
            )

            queries = self.module.load_warmup_queries(path)

        self.assertEqual(queries, [("a", "SELECT 1"), ("b", "SELECT 2")])

    def test_load_warmup_queries_from_annotated_assignment(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "warmup.py"
            path.write_text(
                textwrap.dedent(
                    """
                    import os
                    EXA_PASSWORD = os.environ["EXA_PASSWORD"]
                    WARMUP_QUERIES: list[tuple[str, str]] = [
                        ("annotated", "SELECT " "42"),
                    ]
                    """
                ),
                encoding="utf-8",
            )

            queries = self.module.load_warmup_queries(path)

        self.assertEqual(queries, [("annotated", "SELECT 42")])

    def test_summarize_mixed_http_and_db_targets(self):
        rows = [
            {"kind": "http", "target": "qbr_shell", "ok": True, "status": 200, "elapsed_ms": 100.0},
            {"kind": "http", "target": "qbr_shell", "ok": True, "status": 200, "elapsed_ms": 120.0},
            {"kind": "db_hold", "target": "session_hold", "ok": True, "status": 0, "elapsed_ms": 15000.0},
            {"kind": "db", "target": "exec_sales_main", "ok": True, "status": 0, "elapsed_ms": 1000.0},
            {"kind": "db", "target": "exec_sales_main", "ok": False, "status": 1, "elapsed_ms": 2000.0},
        ]

        summary = self.module.summarize(
            rows,
            max_p95_ms=5000.0,
            max_db_p95_ms=45000.0,
            max_error_rate=0.50,
        )

        self.assertTrue(summary["passed"])
        self.assertEqual(summary["http_requests"], 2)
        self.assertEqual(summary["db_requests"], 3)
        self.assertEqual(summary["db_failures"], 1)
        self.assertEqual(summary["session_hold_requests"], 1)
        self.assertEqual(summary["session_hold_failures"], 0)
        self.assertEqual(summary["targets"]["qbr_shell"]["kind"], "http")
        self.assertEqual(summary["targets"]["session_hold"]["kind"], "db_hold")
        self.assertEqual(summary["targets"]["exec_sales_main"]["kind"], "db")
        self.assertEqual(summary["targets"]["exec_sales_main"]["max_allowed_p95_ms"], 45000.0)
        self.assertEqual(summary["blocking_failures"], 1)
        self.assertEqual(summary["advisory_failures"], 0)

    def test_heavy_analytics_failures_are_advisory_by_default(self):
        rows = [
            {"kind": "http", "target": "qbr_shell", "ok": True, "status": 200, "elapsed_ms": 100.0},
            {"kind": "db", "target": "exec_sales_main", "ok": True, "status": 0, "elapsed_ms": 2000.0},
            {
                "kind": "db",
                "target": "perf_grouping_sets_rollup",
                "ok": False,
                "status": -1,
                "elapsed_ms": 120000.0,
            },
            {
                "kind": "db",
                "target": "perf_rolling_12m_sales",
                "ok": False,
                "status": -1,
                "elapsed_ms": 120000.0,
            },
        ]

        summary = self.module.summarize(
            rows,
            max_p95_ms=5000.0,
            max_db_p95_ms=45000.0,
            max_error_rate=0.05,
        )

        self.assertTrue(summary["passed"])
        self.assertEqual(summary["total_failures"], 2)
        self.assertEqual(summary["blocking_failures"], 0)
        self.assertEqual(summary["advisory_failures"], 2)
        self.assertEqual(summary["blocking_p95_breaches"], [])
        self.assertEqual(
            sorted(summary["advisory_p95_breaches"]),
            ["perf_grouping_sets_rollup", "perf_rolling_12m_sales"],
        )
        self.assertFalse(summary["targets"]["perf_grouping_sets_rollup"]["blocking"])

    def test_heavy_analytics_can_be_blocking_for_explicit_perf_runs(self):
        rows = [
            {
                "kind": "db",
                "target": "perf_account_penetration",
                "ok": False,
                "status": -1,
                "elapsed_ms": 120000.0,
            },
        ]

        summary = self.module.summarize(
            rows,
            max_p95_ms=5000.0,
            max_db_p95_ms=45000.0,
            max_error_rate=0.05,
            heavy_analytics_mode="blocking",
        )

        self.assertFalse(summary["passed"])
        self.assertEqual(summary["blocking_failures"], 1)
        self.assertEqual(summary["advisory_failures"], 0)
        self.assertEqual(summary["blocking_p95_breaches"], ["perf_account_penetration"])

    def test_dynamic_view_counts_are_schema_inventory_advisory(self):
        rows = [
            {"kind": "db", "target": "exec_sales_main", "ok": True, "status": 0, "elapsed_ms": 1000.0},
            {
                "kind": "db",
                "target": "view_count_V_MONTHLY_METRICS",
                "ok": False,
                "status": -1,
                "elapsed_ms": 120000.0,
            },
            {
                "kind": "db",
                "target": "view_count_union_all",
                "ok": False,
                "status": -1,
                "elapsed_ms": 120000.0,
            },
        ]

        summary = self.module.summarize(
            rows,
            max_p95_ms=5000.0,
            max_db_p95_ms=45000.0,
            max_error_rate=0.05,
        )

        self.assertTrue(summary["passed"])
        self.assertEqual(summary["blocking_failures"], 0)
        self.assertEqual(summary["advisory_failures"], 2)
        self.assertEqual(summary["targets"]["view_count_V_MONTHLY_METRICS"]["class"], "schema_inventory")
        self.assertEqual(summary["targets"]["view_count_union_all"]["class"], "schema_inventory")
        self.assertFalse(summary["targets"]["view_count_V_MONTHLY_METRICS"]["blocking"])
        self.assertEqual(
            sorted(summary["advisory_p95_breaches"]),
            ["view_count_V_MONTHLY_METRICS", "view_count_union_all"],
        )

    def test_core_uat_db_timeout_still_fails(self):
        rows = [
            {"kind": "db", "target": "exec_sales_main", "ok": False, "status": -1, "elapsed_ms": 120000.0},
        ]

        summary = self.module.summarize(
            rows,
            max_p95_ms=5000.0,
            max_db_p95_ms=45000.0,
            max_error_rate=0.05,
        )

        self.assertFalse(summary["passed"])
        self.assertEqual(summary["blocking_failures"], 1)
        self.assertEqual(summary["advisory_failures"], 0)
        self.assertEqual(summary["blocking_p95_breaches"], ["exec_sales_main"])

    def test_parse_single_column_table_skips_exapump_status_lines(self):
        stdout = """
        [1/1] SELECT VIEW_NAME FROM EXA_ALL_VIEWS 2 rows
        VIEW_NAME
        MV_MONTHLY_METRICS
        V_YOY_METRICS
        1 statement executed, 0 failed
        """

        values = self.module._parse_single_column_table(stdout, "VIEW_NAME")

        self.assertEqual(values, ["MV_MONTHLY_METRICS", "V_YOY_METRICS"])

    def test_discover_view_queries_omits_union_all_by_default(self):
        completed = mock.Mock(
            returncode=0,
            stdout="VIEW_NAME\nV_MONTHLY_METRICS\nV_YOY_METRICS\n1 statement executed, 0 failed\n",
            stderr="",
        )
        with mock.patch.object(self.module.subprocess, "run", return_value=completed):
            queries, error = self.module.discover_view_queries("xyz-pilot-ro", 1.0)

        labels = [label for label, _sql in queries]
        self.assertEqual(error, "")
        self.assertEqual(labels, ["view_count_V_MONTHLY_METRICS", "view_count_V_YOY_METRICS"])
        self.assertNotIn("view_count_union_all", labels)

    def test_discover_view_queries_can_opt_into_union_all_diagnostic(self):
        completed = mock.Mock(
            returncode=0,
            stdout="VIEW_NAME\nV_MONTHLY_METRICS\nV_YOY_METRICS\n1 statement executed, 0 failed\n",
            stderr="",
        )
        with mock.patch.object(self.module.subprocess, "run", return_value=completed):
            queries, error = self.module.discover_view_queries(
                "xyz-pilot-ro",
                1.0,
                include_union_all=True,
            )

        labels = [label for label, _sql in queries]
        self.assertEqual(error, "")
        self.assertEqual(
            labels,
            ["view_count_V_MONTHLY_METRICS", "view_count_V_YOY_METRICS", "view_count_union_all"],
        )

    def test_build_db_targets_can_skip_heavy_analytics(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "warmup.py"
            path.write_text(
                textwrap.dedent(
                    """
                    WARMUP_QUERIES = [
                        ("exec_sales_main", "SELECT 1"),
                        ("esl_base_layer_year_sales_dash", "SELECT 5"),
                        ("perf_account_penetration", "SELECT 2"),
                        ("grafana_top_brand_sales_by_year", "SELECT 3"),
                    ]
                    """
                ),
                encoding="utf-8",
            )

            targets, meta = self.module.build_db_targets(
                profile="unused",
                warmup_path=path,
                query_limit=0,
                include_dynamic_views=False,
                discovery_timeout=1,
                include_heavy_analytics=False,
                biz_profile="",
            )

        labels = [label for label, _sql, _profile in targets]
        self.assertIn("session_concurrency_marker", labels)
        self.assertIn("exec_sales_main", labels)
        self.assertNotIn("esl_base_layer_year_sales_dash", labels)
        self.assertNotIn("perf_account_penetration", labels)
        self.assertNotIn("grafana_top_brand_sales_by_year", labels)
        self.assertEqual(meta["heavy_analytics_query_count"], 0)

    def _write_dual_pack_warmup(self, tmp):
        path = Path(tmp) / "warmup.py"
        path.write_text(
            textwrap.dedent(
                """
                WARMUP_QUERIES = [
                    ("exec_sales_main", "SELECT 1"),
                    ("esl_base_layer_year_sales_dash", "SELECT 5"),
                    ("perf_account_penetration", "SELECT 2"),
                ]
                WARMUP_QUERIES_XYZ_BIZ = [
                    ("biz_esl_base_layer_year_sales", "SELECT 3"),
                    ("biz_esl_business_metrics_brand", "SELECT 4"),
                ]
                """
            ),
            encoding="utf-8",
        )
        return path

    def test_load_warmup_queries_named_var_extracts_biz_pack(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write_dual_pack_warmup(tmp)

            dash = self.module.load_warmup_queries(path)
            biz = self.module.load_warmup_queries(path, var_name="WARMUP_QUERIES_XYZ_BIZ")

        self.assertEqual(
            dash,
            [
                ("exec_sales_main", "SELECT 1"),
                ("esl_base_layer_year_sales_dash", "SELECT 5"),
                ("perf_account_penetration", "SELECT 2"),
            ],
        )
        self.assertEqual(
            biz,
            [("biz_esl_base_layer_year_sales", "SELECT 3"), ("biz_esl_business_metrics_brand", "SELECT 4")],
        )

    def test_build_db_targets_attaches_biz_pack_under_biz_profile(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write_dual_pack_warmup(tmp)

            targets, meta = self.module.build_db_targets(
                profile="xyz-pilot-ro",
                warmup_path=path,
                query_limit=0,
                include_dynamic_views=False,
                discovery_timeout=1,
                include_heavy_analytics=True,
                biz_profile="xyz-pilot-biz",
            )

        by_label = {label: (sql, profile) for label, sql, profile in targets}
        # XYZ_DASH pack + marker run under the default exasol profile.
        self.assertEqual(by_label["session_concurrency_marker"][1], "xyz-pilot-ro")
        self.assertEqual(by_label["exec_sales_main"][1], "xyz-pilot-ro")
        self.assertEqual(by_label["esl_base_layer_year_sales_dash"][1], "xyz-pilot-ro")
        self.assertEqual(by_label["perf_account_penetration"][1], "xyz-pilot-ro")
        # BIZ pack carries biz_ labels and runs under the BIZ profile.
        self.assertEqual(by_label["biz_esl_base_layer_year_sales"][1], "xyz-pilot-biz")
        self.assertEqual(by_label["biz_esl_business_metrics_brand"][1], "xyz-pilot-biz")
        self.assertTrue(
            all(
                label.startswith("biz_")
                for label, _sql, profile in targets
                if profile == "xyz-pilot-biz"
            )
        )
        self.assertEqual(meta["biz_query_count"], 2)
        self.assertEqual(meta["biz_profile"], "xyz-pilot-biz")

    def test_build_db_targets_no_biz_when_biz_profile_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write_dual_pack_warmup(tmp)

            targets, meta = self.module.build_db_targets(
                profile="xyz-pilot-ro",
                warmup_path=path,
                query_limit=0,
                include_dynamic_views=False,
                discovery_timeout=1,
                include_heavy_analytics=True,
                biz_profile="",
            )

        labels = [label for label, _sql, _profile in targets]
        self.assertNotIn("biz_esl_base_layer_year_sales", labels)
        self.assertNotIn("biz_esl_business_metrics_brand", labels)
        self.assertTrue(all(profile == "xyz-pilot-ro" for _label, _sql, profile in targets))
        self.assertEqual(meta["biz_query_count"], 0)
        self.assertEqual(meta["biz_profile"], "")

    def test_biz_targets_are_classed_and_summarized_separately(self):
        # Direct BASE_LAYER probes are advisory/heavy_analytics;
        # V_BUSINESS_METRICS is fast/core. The BIZ targets carry biz_ labels
        # and appear as distinct targets.
        self.assertEqual(self.module._target_class("esl_base_layer_year_sales_dash"), "heavy_analytics")
        self.assertEqual(self.module._target_class("biz_esl_base_layer_year_sales"), "heavy_analytics")
        self.assertEqual(self.module._target_class("biz_esl_business_metrics_brand"), "core_uat")

        rows = [
            {"kind": "db", "target": "exec_sales_main", "ok": True, "status": 0, "elapsed_ms": 1000.0, "profile": "xyz-pilot-ro"},
            {
                "kind": "db",
                "target": "esl_base_layer_year_sales_dash",
                "ok": False,
                "status": -1,
                "elapsed_ms": 120000.0,
                "profile": "xyz-pilot-ro",
            },
            {
                "kind": "db",
                "target": "biz_esl_base_layer_year_sales",
                "ok": False,
                "status": -1,
                "elapsed_ms": 120000.0,
                "profile": "xyz-pilot-biz",
            },
            {
                "kind": "db",
                "target": "biz_esl_business_metrics_brand",
                "ok": True,
                "status": 0,
                "elapsed_ms": 300.0,
                "profile": "xyz-pilot-biz",
            },
        ]

        summary = self.module.summarize(
            rows,
            max_p95_ms=5000.0,
            max_db_p95_ms=45000.0,
            max_error_rate=0.05,
        )

        # BIZ targets are summarized as their own per-target entries.
        self.assertIn("esl_base_layer_year_sales_dash", summary["targets"])
        self.assertIn("biz_esl_base_layer_year_sales", summary["targets"])
        self.assertIn("biz_esl_business_metrics_brand", summary["targets"])
        self.assertIn("base_layer_minimal_probe", summary)
        self.assertFalse(summary["base_layer_minimal_probe"]["present"])
        self.assertEqual(summary["base_layer_minimal_probe"]["target"], "biz_esl_base_layer_minimal_year")
        # BASE_LAYER failures are advisory (do not fail the gate).
        self.assertTrue(summary["passed"])
        self.assertFalse(summary["targets"]["esl_base_layer_year_sales_dash"]["blocking"])
        self.assertEqual(summary["targets"]["esl_base_layer_year_sales_dash"]["class"], "heavy_analytics")
        self.assertFalse(summary["targets"]["biz_esl_base_layer_year_sales"]["blocking"])
        self.assertEqual(summary["targets"]["biz_esl_base_layer_year_sales"]["class"], "heavy_analytics")
        # V_BUSINESS_METRICS is fast/core (blocking).
        self.assertTrue(summary["targets"]["biz_esl_business_metrics_brand"]["blocking"])
        self.assertEqual(summary["targets"]["biz_esl_business_metrics_brand"]["class"], "core_uat")

    def test_base_layer_minimal_probe_metrics_are_reported_when_present(self):
        rows = [
            {
                "kind": "db",
                "target": "biz_esl_base_layer_minimal_year",
                "ok": True,
                "status": 0,
                "elapsed_ms": 420.0,
                "profile": "xyz-pilot-biz",
            },
            {
                "kind": "db",
                "target": "biz_esl_base_layer_minimal_year",
                "ok": False,
                "status": -1,
                "elapsed_ms": 120000.0,
                "profile": "xyz-pilot-biz",
            },
        ]
        summary = self.module.summarize(
            rows,
            max_p95_ms=5000.0,
            max_db_p95_ms=45000.0,
            max_error_rate=0.5,
        )
        self.assertIn("base_layer_minimal_probe", summary)
        probe = summary["base_layer_minimal_probe"]
        self.assertTrue(probe["present"])
        self.assertEqual(probe["target"], "biz_esl_base_layer_minimal_year")
        self.assertEqual(probe["requests"], 2)
        self.assertEqual(probe["failures"], 1)
        self.assertGreater(probe["p95_ms"], 0.0)

    def test_run_user_passes_per_target_profile(self):
        captured = []

        def fake_run_sql(*, user_id, iteration, target, sql, profile, timeout):
            captured.append((target, profile))
            return {"kind": "db", "target": target, "profile": profile, "ok": True, "status": 0, "elapsed_ms": 1.0}

        original = self.module.run_sql
        self.module.run_sql = fake_run_sql
        try:
            self.module.run_user(
                user_id=1,
                iterations=0,
                http_targets=[],
                timeout=1.0,
                think_ms=0,
                db_targets=[
                    ("exec_sales_main", "SELECT 1", "xyz-pilot-ro"),
                    ("biz_esl_base_layer_year_sales", "SELECT 2", "xyz-pilot-biz"),
                ],
                db_profile="xyz-pilot-ro",
                db_iterations=1,
                db_timeout=1.0,
                session_hold_enabled=False,
                session_hold_dsn="",
                session_hold_user="",
                session_hold_password="",
                session_hold_schema="",
                session_hold_seconds=0.0,
            )
        finally:
            self.module.run_sql = original

        self.assertIn(("exec_sales_main", "xyz-pilot-ro"), captured)
        self.assertIn(("biz_esl_base_layer_year_sales", "xyz-pilot-biz"), captured)


if __name__ == "__main__":
    unittest.main()
