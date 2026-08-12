from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import sys
import types
import unittest


ROOT = Path(__file__).resolve().parents[2]
os.environ.setdefault("EXA_PASSWORD", "dummy")
sys.modules.setdefault("pyexasol", types.ModuleType("pyexasol"))
SPEC = importlib.util.spec_from_file_location(
    "exasol_warmup",
    ROOT / "scripts" / "exasol_warmup" / "warmup.py",
)
assert SPEC and SPEC.loader
warmup = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = warmup
SPEC.loader.exec_module(warmup)


class _Rows:
    def __init__(self, rows: list[tuple[object, ...]]) -> None:
        self._rows = rows

    def fetchall(self) -> list[tuple[object, ...]]:
        return self._rows


class _FakeConn:
    def execute(self, sql: str) -> _Rows:
        if "EXA_ALL_VIEWS" in sql:
            return _Rows([("V_FAST",), ("V_FAIL",)])
        if "V_FAIL" in sql:
            raise RuntimeError("synthetic view failure")
        return _Rows([(1,)])


class _RecordingConn:
    def __init__(self) -> None:
        self.sqls: list[str] = []

    def execute(self, sql: str) -> _Rows:
        self.sqls.append(sql)
        if "EXA_ALL_VIEWS" in sql:
            return _Rows([("V_MONTHLY_METRICS",)])
        return _Rows([(240,)])


class ExasolWarmupTests(unittest.TestCase):
    # XYZ-PILOT-292: the quoted/aliased variants are now gated behind
    # WARMUP_VVIEW_ALL_VARIANTS (default off = cold-start trim). These tests
    # exercise the full-variant path, so enable it here and restore after.
    def setUp(self) -> None:
        self._orig_all_variants = warmup.WARMUP_VVIEW_ALL_VARIANTS
        warmup.WARMUP_VVIEW_ALL_VARIANTS = True

    def tearDown(self) -> None:
        warmup.WARMUP_VVIEW_ALL_VARIANTS = self._orig_all_variants

    def test_cold_start_trims_v_view_to_base_variant_only(self) -> None:
        # XYZ-PILOT-292: with the default (all-variants OFF), a semantic V_* view
        # primes ONLY the base COUNT — not the 4-variant explosion that timed
        # out the cold warm-up.
        warmup.WARMUP_VVIEW_ALL_VARIANTS = False
        self.assertEqual(
            warmup._view_count_sql_variants("V_MONTHLY_METRICS"),
            [("view_count_V_MONTHLY_METRICS", "SELECT COUNT(*) FROM <PILOT_SCHEMA>.V_MONTHLY_METRICS")],
        )

    def test_non_semantic_views_only_get_base_count_shape(self) -> None:
        self.assertEqual(
            warmup._view_count_sql_variants("FACT_PRIMARY_SOURCE"),
            [
                (
                    "view_count_FACT_PRIMARY_SOURCE",
                    "SELECT COUNT(*) FROM <PILOT_SCHEMA>.FACT_PRIMARY_SOURCE",
                )
            ],
        )

    def test_dynamic_view_failures_are_returned_to_caller(self) -> None:
        queries, rows, _elapsed_ms, failures = warmup._warmup_v_views(_FakeConn())

        self.assertEqual(queries, 4)
        self.assertEqual(rows, 4)
        self.assertEqual(len(failures), 5)
        self.assertEqual(
            [name for name, _err in failures],
            [
                "view_count_V_FAIL",
                "view_count_alias_V_FAIL",
                "view_count_quoted_V_FAIL",
                "view_count_quoted_alias_V_FAIL",
                "view_count_union_all",
            ],
        )

    def test_dynamic_view_warmup_primes_external_diagnostic_sql_shapes(self) -> None:
        conn = _RecordingConn()

        queries, rows, _elapsed_ms, failures = warmup._warmup_v_views(conn)

        self.assertEqual(queries, 5)
        self.assertEqual(rows, 5)
        self.assertEqual(failures, [])
        self.assertIn(
            "SELECT COUNT(*) FROM <PILOT_SCHEMA>.V_MONTHLY_METRICS",
            conn.sqls,
        )
        self.assertIn(
            "SELECT COUNT(*) AS ROW_COUNT FROM <PILOT_SCHEMA>.V_MONTHLY_METRICS",
            conn.sqls,
        )
        self.assertIn(
            'SELECT COUNT(*) FROM "<PILOT_SCHEMA>"."V_MONTHLY_METRICS"',
            conn.sqls,
        )
        self.assertIn(
            'SELECT COUNT(*) AS "ROW_COUNT" FROM "<PILOT_SCHEMA>"."V_MONTHLY_METRICS"',
            conn.sqls,
        )


class _LoopConn:
    """Fake connection for _run_warmup; fails any SQL containing `fail_substr`
    for the first `fail_times` calls, then succeeds."""

    def __init__(self, fail_substr: str, fail_times: int = 99) -> None:
        self.fail_substr = fail_substr
        self.fail_times = fail_times
        self.calls: dict[str, int] = {}
        self.closed = False

    def execute(self, sql: str) -> _Rows:
        if self.fail_substr and self.fail_substr in sql:
            self.calls[self.fail_substr] = self.calls.get(self.fail_substr, 0) + 1
            if self.calls[self.fail_substr] <= self.fail_times:
                raise RuntimeError("synthetic warmup failure")
        return _Rows([(1,)])

    def close(self) -> None:
        self.closed = True


class RunWarmupResilienceTests(unittest.TestCase):
    """XYZ-PILOT-285: cold-start warm-up best-effort + retry-once behaviour."""

    def setUp(self) -> None:
        self._orig_dynamic = warmup.WARMUP_DYNAMIC_VIEWS
        self._orig_heavy = warmup.WARMUP_HEAVY_ANALYTICS
        self._orig_connect = getattr(warmup.pyexasol, "connect", None)
        self._orig_sleep = warmup.time.sleep
        warmup.WARMUP_DYNAMIC_VIEWS = False   # skip the live V_view step
        warmup.WARMUP_HEAVY_ANALYTICS = False  # skip perf_* heavy queries
        warmup.time.sleep = lambda *_a, **_k: None  # no real retry pause

    def tearDown(self) -> None:
        warmup.WARMUP_DYNAMIC_VIEWS = self._orig_dynamic
        warmup.WARMUP_HEAVY_ANALYTICS = self._orig_heavy
        warmup.time.sleep = self._orig_sleep
        if self._orig_connect is not None:
            warmup.pyexasol.connect = self._orig_connect

    def _patch_connect(self, conn: _LoopConn) -> None:
        warmup.pyexasol.connect = lambda **_kw: conn

    def test_best_effort_query_failure_does_not_fail_unit(self) -> None:
        # header_metadata (raw 170M FACT_PRIMARY scan) is the only best-effort
        # label; its SQL carries "MIN(INVOICE_DATE) AS MIN_D". Persistent failure
        # must NOT raise SystemExit — the MV_* primers all succeed.
        conn = _LoopConn(fail_substr="MIN(INVOICE_DATE) AS MIN_D")
        self._patch_connect(conn)
        warmup._run_warmup()  # must not raise
        self.assertTrue(conn.closed)

    def test_strict_query_failure_fails_unit(self) -> None:
        conn = _LoopConn(fail_substr="MV_ACTUAL_VS_TARGET")
        self._patch_connect(conn)
        with self.assertRaises(SystemExit) as cm:
            warmup._run_warmup()
        self.assertEqual(cm.exception.code, 1)

    def test_retry_recovers_transient_strict_failure(self) -> None:
        # fail a strict MV query exactly once → the single retry recovers it,
        # so the unit still succeeds (no SystemExit).
        conn = _LoopConn(fail_substr="MV_ACTUAL_VS_TARGET", fail_times=1)
        self._patch_connect(conn)
        warmup._run_warmup()  # retry recovers → no SystemExit raised
        # the first failing primer was attempted twice (fail then retry-success)
        self.assertGreaterEqual(conn.calls["MV_ACTUAL_VS_TARGET"], 2)


class _BizConn:
    """Fake XYZ_BIZ connection. Records SQL run on it and optionally fails any
    SQL containing `fail_substr`, so we can prove BIZ failures stay isolated."""

    def __init__(self, fail_substr: str = "") -> None:
        self.fail_substr = fail_substr
        self.sqls: list[str] = []
        self.closed = False

    def execute(self, sql: str) -> _Rows:
        self.sqls.append(sql)
        if self.fail_substr and self.fail_substr in sql:
            raise RuntimeError("synthetic BIZ failure")
        return _Rows([(1,)])

    def close(self) -> None:
        self.closed = True


class BizWarmupIsolationTests(unittest.TestCase):
    """Task 5: the XYZ_BIZ leg is best-effort and fully isolated from the
    demo-critical XYZ_DASH path / unit exit semantics."""

    def setUp(self) -> None:
        self._orig_dynamic = warmup.WARMUP_DYNAMIC_VIEWS
        self._orig_heavy = warmup.WARMUP_HEAVY_ANALYTICS
        self._orig_connect = getattr(warmup.pyexasol, "connect", None)
        self._orig_sleep = warmup.time.sleep
        self._orig_biz_pw = warmup.EXA_BIZ_PASSWORD
        warmup.WARMUP_DYNAMIC_VIEWS = False
        warmup.WARMUP_HEAVY_ANALYTICS = False
        warmup.time.sleep = lambda *_a, **_k: None

    def tearDown(self) -> None:
        warmup.WARMUP_DYNAMIC_VIEWS = self._orig_dynamic
        warmup.WARMUP_HEAVY_ANALYTICS = self._orig_heavy
        warmup.time.sleep = self._orig_sleep
        warmup.EXA_BIZ_PASSWORD = self._orig_biz_pw
        if self._orig_connect is not None:
            warmup.pyexasol.connect = self._orig_connect

    def test_biz_leg_skipped_when_password_absent(self) -> None:
        # EXA_BIZ_PASSWORD empty → BIZ leg must skip, no second connection
        # opened, XYZ_DASH path unaffected, no SystemExit.
        warmup.EXA_BIZ_PASSWORD = ""
        dash_conn = _LoopConn(fail_substr="")  # XYZ_DASH all-succeed
        opened: list[str] = []

        def _connect(**kw):
            opened.append(kw.get("user", ""))
            return dash_conn

        warmup.pyexasol.connect = _connect
        warmup._run_warmup()  # must not raise
        self.assertTrue(dash_conn.closed)
        # only the XYZ_DASH connection was opened — BIZ never connected
        self.assertNotIn(warmup.EXA_BIZ_USER, opened)

    def test_biz_leg_runs_pack_on_second_connection_when_creds_present(self) -> None:
        warmup.EXA_BIZ_PASSWORD = "biz-secret"
        dash_conn = _LoopConn(fail_substr="")
        biz_conn = _BizConn()

        def _connect(**kw):
            return biz_conn if kw.get("user") == warmup.EXA_BIZ_USER else dash_conn

        warmup.pyexasol.connect = _connect
        warmup._run_warmup()  # must not raise

        # Every BIZ pack query ran on the second (BIZ) connection, which closed.
        self.assertTrue(biz_conn.closed)
        for _label, sql in warmup.WARMUP_QUERIES_XYZ_BIZ:
            self.assertIn(sql, biz_conn.sqls)

    def test_biz_query_failure_does_not_fail_unit(self) -> None:
        # A BIZ query failure (and even a fully-failing BIZ pack) must NOT add to
        # XYZ_DASH failures or raise SystemExit. XYZ_DASH path all-succeeds here.
        warmup.EXA_BIZ_PASSWORD = "biz-secret"
        dash_conn = _LoopConn(fail_substr="")
        biz_conn = _BizConn(fail_substr="SELECT")  # every BIZ query fails

        def _connect(**kw):
            return biz_conn if kw.get("user") == warmup.EXA_BIZ_USER else dash_conn

        warmup.pyexasol.connect = _connect
        warmup._run_warmup()  # best-effort isolation → no SystemExit
        self.assertTrue(biz_conn.closed)

    def test_biz_connect_failure_does_not_fail_unit(self) -> None:
        # If even opening the BIZ connection raises, the outer wrapper swallows
        # it; XYZ_DASH exit semantics are untouched.
        warmup.EXA_BIZ_PASSWORD = "biz-secret"
        dash_conn = _LoopConn(fail_substr="")

        def _connect(**kw):
            if kw.get("user") == warmup.EXA_BIZ_USER:
                raise RuntimeError("synthetic BIZ connect failure")
            return dash_conn

        warmup.pyexasol.connect = _connect
        warmup._run_warmup()  # must not raise
        self.assertTrue(dash_conn.closed)

    def test_biz_leg_does_not_mask_xyz_dash_failure(self) -> None:
        # Even with BIZ creds present + BIZ succeeding, a strict XYZ_DASH failure
        # must still fail the unit — exit semantics depend ONLY on XYZ_DASH.
        warmup.EXA_BIZ_PASSWORD = "biz-secret"
        dash_conn = _LoopConn(fail_substr="MV_ACTUAL_VS_TARGET")
        biz_conn = _BizConn()

        def _connect(**kw):
            return biz_conn if kw.get("user") == warmup.EXA_BIZ_USER else dash_conn

        warmup.pyexasol.connect = _connect
        with self.assertRaises(SystemExit) as cm:
            warmup._run_warmup()
        self.assertEqual(cm.exception.code, 1)


if __name__ == "__main__":
    unittest.main()
