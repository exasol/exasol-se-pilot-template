#!/usr/bin/env python3
"""Regression checks for the QBR first-click filter-state fix."""

from __future__ import annotations

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "services/streamlit-insights" / "app.py"
README = ROOT / "services/streamlit-insights" / "README.md"
VISUALS = ROOT / "docs" / "30-runbooks" / "ui" / "XYZ_PILOT_STREAMLIT_VISUALS.md"


class QbrFilterStateContractTest(unittest.TestCase):
    def test_filters_use_session_state_before_cookie_persistence(self) -> None:
        text = APP.read_text(encoding="utf-8")

        ensure_pos = text.index('_ensure_filter_state("years", opts["years"])')
        multiselect_pos = text.index('st.multiselect("Year", opts["years"], key=_filter_state_keys["years"])')
        save_pos = text.index('_save_filter("years", sel_years)')

        self.assertLess(ensure_pos, multiselect_pos)
        self.assertLess(multiselect_pos, save_pos)
        self.assertIn('"years": "qbr_filter_years"', text)
        self.assertIn('"regions": "qbr_filter_regions"', text)
        self.assertIn('"brands": "qbr_filter_brands"', text)
        self.assertIn('st.session_state[state_key] = _load_filter(key, all_values)', text)
        self.assertNotIn('st.multiselect("Year", opts["years"], default=', text)

    def test_clear_saved_filters_resets_session_state_and_cookies(self) -> None:
        text = APP.read_text(encoding="utf-8")

        self.assertIn('st.session_state["qbr_reset_filters"] = True', text)
        self.assertIn('st.session_state[state_key] = list(all_values)', text)
        for cookie in ("xyz_qbr_years", "xyz_qbr_regions", "xyz_qbr_brands"):
            self.assertIn(cookie, text)

    def test_hotfix_id_is_visible_in_runtime_and_docs(self) -> None:
        app_text = APP.read_text(encoding="utf-8")
        readme_text = README.read_text(encoding="utf-8")
        visuals_text = VISUALS.read_text(encoding="utf-8")

        self.assertIn("first-click filter state hotfix XYZ-PILOT-262", app_text)
        self.assertIn("XYZ_LAST_DEPLOYMENT_ISSUE=XYZ-PILOT-262", readme_text)
        self.assertIn("first-click filter-state hotfix `XYZ-PILOT-262`", visuals_text)


if __name__ == "__main__":
    unittest.main()
