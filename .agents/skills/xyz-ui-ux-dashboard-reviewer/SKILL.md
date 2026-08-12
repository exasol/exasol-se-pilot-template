---
name: xyz-ui-ux-dashboard-reviewer
description: Use when designing, reviewing, or polishing <PILOT_NAME> Streamlit dashboards, chatbot visual surfaces, BI-style reports, dashboard rebuild screens, KPI layouts, chart hierarchy, filter UX, empty states, and visual regression test cases.
---

# <PILOT_NAME> UI/UX Dashboard Reviewer

Use this skill for visible dashboard and chatbot UX work in the <PILOT_NAME>.

## Review Lens

- Treat the QBR dashboard as an operational BI tool, not a landing page.
- Put the most decision-relevant facts first: KPI strip, scope, date coverage,
  then trends and detail.
- Keep pages dense but scannable: consistent tab structure, predictable metric
  rows, compact captions, and stable chart heights.
- Preserve visual meaning: blue for primary sales, orange for target/contrast,
  green for favourable variance, red for unfavourable variance.
- Do not add decorative cards, gradient backgrounds, or chart junk.
- Use repeated cards only for individual KPI metrics or table rows.
- Prefer built-in Streamlit/Altair/Plotly/AgGrid patterns already present in
  the repo before adding dependencies.
- Never expose credentials, tokens, raw connection strings, or full exception
  traces in user-facing UI.

## Implementation Workflow

1. Read the relevant current surface first:
   - `services/streamlit-insights/app.py` for QBR dashboard changes.
   - `services/chatbot/app.py` for chatbot chart/table/message changes.
   - `docs/30-runbooks/ui/HPC_PILOT_STREAMLIT_VISUALS.md` for visual regression coverage.
2. Identify the data contract before changing visuals:
   - table/view names;
   - filter scope;
   - empty-state behaviour;
   - warm-cache latency expectations.
3. Ship focused changes:
   - one UX problem or visual capability per commit;
   - no unrelated SQL/model refactors;
   - keep layout stable across desktop widths.
4. Update documentation in the same commit:
   - visual test catalog with exact manual test path;
   - affected README/runbook when the user workflow changes;
   - relevant `XYZ-PILOT-NNN` cross-link.
5. Validate:
   - syntax/compile check for edited Python;
   - repository hygiene checks;
   - service health or local Streamlit run when feasible.

## Dashboard Checklist

- KPI rows use consistent card styling and readable number formats.
- Filter state is visible near the data it affects.
- Empty states say what filter/data condition produced them.
- Charts have explicit titles, axes, tooltips, and stable heights.
- Tables use hidden indexes and typed column formatting where practical.
- User-triggered actions show feedback without resetting filters.
- Visual test cases cover every new widget, chart, or control.
