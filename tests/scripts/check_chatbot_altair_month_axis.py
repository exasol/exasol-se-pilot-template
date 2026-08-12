"""Regression check for XYZ-PILOT-187 Altair month-axis formatting.

The chatbot app is SSO-gated at import time, so this script parses
`services/chatbot/app.py`, extracts only the pure month-axis helper functions,
and validates their behavior with fake dataframe and Altair objects.
"""
from __future__ import annotations

import ast
import json
import re
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
APP_PATH = ROOT / "services/chatbot" / "app.py"

CONSTANTS = {
    "_MONTH_AXIS_LABELS",
    "_MONTH_AXIS_LABEL_EXPR",
    "_MONTH_AXIS_NAME_ALLOWLIST",
}
FUNCTIONS = {
    "_altair_field_name",
    "_looks_like_month_axis_name",
    "_coerce_month_number",
    "_is_month_ordinal_column",
    "_altair_x_encoding",
}


class FakeDF:
    def __init__(self, data: dict[str, list[Any]]) -> None:
        self._data = data
        self.columns = list(data)

    def __getitem__(self, key: str) -> list[Any]:
        return self._data[key]


class FakeAxis:
    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs


class FakeAlt:
    Axis = FakeAxis

    @staticmethod
    def X(field: str, **kwargs: Any) -> dict[str, Any]:
        return {"field": field, "kwargs": kwargs}


def _load_helpers() -> dict[str, Any]:
    tree = ast.parse(APP_PATH.read_text(encoding="utf-8"), filename=str(APP_PATH))
    nodes: list[ast.stmt] = []
    for node in tree.body:
        if isinstance(node, ast.Assign):
            names = {target.id for target in node.targets if isinstance(target, ast.Name)}
            if names & CONSTANTS:
                nodes.append(node)
        elif isinstance(node, ast.FunctionDef) and node.name in FUNCTIONS:
            nodes.append(node)

    ns: dict[str, Any] = {"Any": Any, "re": re}
    ast.fix_missing_locations(tree)
    helper_module = ast.Module(body=nodes, type_ignores=[])
    ast.fix_missing_locations(helper_module)
    exec(compile(helper_module, str(APP_PATH), "exec"), ns)  # noqa: S102

    missing = sorted((CONSTANTS | FUNCTIONS) - ns.keys())
    if missing:
        raise AssertionError(f"missing helper symbols: {missing}")
    return ns


def main() -> int:
    ns = _load_helpers()
    month_df = FakeDF({
        "PERIOD_MONTH": list(range(1, 13)),
        "PERIOD_YEAR": [2024] * 12,
        "REVENUE": [1000 * month for month in range(1, 13)],
    })
    enc = ns["_altair_x_encoding"]({"x": "PERIOD_MONTH"}, month_df, FakeAlt)
    assert enc["field"] == "PERIOD_MONTH:O", enc
    assert enc["kwargs"]["sort"] == list(range(1, 13)), enc
    assert enc["kwargs"]["axis"].kwargs["values"] == list(range(1, 13)), enc
    assert "Jan" in enc["kwargs"]["axis"].kwargs["labelExpr"], enc
    assert "Dec" in enc["kwargs"]["axis"].kwargs["labelExpr"], enc

    period_df = FakeDF({"FISCAL_PERIOD": ["1", "2", "12"], "REVENUE": [1, 2, 3]})
    enc = ns["_altair_x_encoding"]({"x": "FISCAL_PERIOD:Q"}, period_df, FakeAlt)
    assert enc["field"] == "FISCAL_PERIOD:O", enc

    measure_df = FakeDF({"MONTHLY_VALUE": [100000, 120000], "SEGMENT": ["A", "B"]})
    enc = ns["_altair_x_encoding"]({"x": "MONTHLY_VALUE"}, measure_df, FakeAlt)
    assert enc["field"] == "MONTHLY_VALUE", enc
    assert "axis" not in enc["kwargs"], enc

    bad_month_df = FakeDF({"MONTH": [1, 13], "REVENUE": [1, 2]})
    assert ns["_is_month_ordinal_column"](bad_month_df, "MONTH") is False

    print(json.dumps({
        "ok": True,
        "xyz_pilot": "XYZ-PILOT-187",
        "checks": [
            "PERIOD_MONTH renders as ordered ordinal Jan..Dec axis",
            "typed FISCAL_PERIOD:Q is overridden to ordinal month axis",
            "MONTHLY_VALUE measure is not treated as month axis",
            "out-of-range month values do not get month labels",
        ],
    }, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({
            "ok": False,
            "xyz_pilot": "XYZ-PILOT-187",
            "error": f"{type(exc).__name__}: {exc}",
        }, indent=2), file=sys.stderr)
        raise SystemExit(1)
