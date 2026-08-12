#!/usr/bin/env python3
"""Tests for the AWS cost recovery evidence-pack generator."""

from __future__ import annotations

import csv
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "build_aws_cost_recovery_pack.py"
spec = importlib.util.spec_from_file_location("build_aws_cost_recovery_pack", SCRIPT)
assert spec and spec.loader
cost_pack = importlib.util.module_from_spec(spec)
sys.modules["build_aws_cost_recovery_pack"] = cost_pack
spec.loader.exec_module(cost_pack)


class AwsCostRecoveryPackTests(unittest.TestCase):
    def test_generated_export_uses_pricing_calculator_shape(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            out_dir = Path(raw_tmp)
            rc = cost_pack.main(
                [
                    "--out-dir",
                    str(out_dir),
                    "--generated-at",
                    "2026-06-22T07:00:00Z",
                    "--end-date",
                    "2026-06-23",
                ]
            )
            self.assertEqual(rc, 0)

            csv_path = out_dir / "XYZ_PILOT_AWS_PRICING_CALCULATOR_WORKSHEET_2026-06-22T070000Z.csv"
            json_path = out_dir / "XYZ_PILOT_AWS_PRICING_CALCULATOR_WORKSHEET_2026-06-22T070000Z.json"
            md_path = out_dir / "XYZ_PILOT_AWS_COST_RECOVERY_PACK_2026-06-22T070000Z.md"
            commands_path = out_dir / "XYZ_PILOT_AWS_COST_EXPLORER_COMMANDS_2026-06-22T070000Z.sh"

            self.assertTrue(csv_path.exists())
            self.assertTrue(json_path.exists())
            self.assertTrue(md_path.exists())
            self.assertTrue(commands_path.exists())

            with csv_path.open("r", encoding="utf-8", newline="") as handle:
                reader = csv.reader(handle)
                self.assertEqual(next(reader), cost_pack.CSV_FIELDS)

            data = json.loads(json_path.read_text(encoding="utf-8"))
            self.assertEqual(
                sorted(data.keys()),
                [
                    "createdAt",
                    "currency",
                    "expiresAt",
                    "exportCreatedAt",
                    "id",
                    "items",
                    "name",
                    "rateTimestamp",
                    "rateType",
                    "status",
                    "totalCostEstimated",
                    "totalCostHistorical",
                ],
            )
            self.assertGreaterEqual(len(data["items"]), 10)
            self.assertEqual(data["items"][0]["usageAccountId"], "<AWS_ACCOUNT_ID>")
            self.assertIn("Cost Explorer is authoritative", md_path.read_text(encoding="utf-8"))

    def test_reference_headers_match_known_pricing_calculator_exports(self) -> None:
        repo = Path(__file__).resolve().parents[2]
        ref_csv = repo / "supporting_assets" / "Quadient.csv"
        ref_json = repo / "supporting_assets" / "Quadient.json"
        if not ref_csv.exists() or not ref_json.exists():
            self.skipTest("local AWS Pricing Calculator reference export assets are absent")

        with tempfile.TemporaryDirectory() as raw_tmp:
            out_dir = Path(raw_tmp)
            rc = cost_pack.main(
                [
                    "--out-dir",
                    str(out_dir),
                    "--generated-at",
                    "2026-06-22T07:00:00Z",
                    "--end-date",
                    "2026-06-23",
                    "--reference-csv",
                    str(ref_csv),
                    "--reference-json",
                    str(ref_json),
                ]
            )
            self.assertEqual(rc, 0)


if __name__ == "__main__":
    unittest.main()
