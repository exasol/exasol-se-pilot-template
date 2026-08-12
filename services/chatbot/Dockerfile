FROM python:3.14-slim

WORKDIR /app

# XYZ-PILOT-089 Phase 1: install graphviz binary (required by mingrammer/diagrams
# for layout) — keeps the layer small by avoiding `recommends` packages.
RUN apt-get update \
 && apt-get install -y --no-install-recommends graphviz \
 && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir \
    streamlit==1.40.0 \
    boto3==1.35.* \
    requests==2.32.* \
    "mcp>=1.0" \
    "pyexasol==2.2.1" \
    "plotly>=5.20.0" \
    "diagrams>=0.23.4" \
    "streamlit-aggrid>=1.0.5" \
    "streamlit-plotly-events>=0.0.6" \
    "streamlit-extras>=0.4.0" \
    "streamlit-cookies-controller>=0.0.4"
# XYZ-PILOT-135: streamlit-cookies-controller for persistent UI preferences
# across sessions. Used in QBR sidebar (filter selections survive tab
# close/reopen) and optionally in chatbot (preferred chart engine, etc.).
# MIT, ~50 KB wheel, no native deps. Try-import-guarded at every call
# site so the app keeps working if the lib is absent.
# XYZ-PILOT-134: streamlit-extras for `chart_container` (download CSV /
# view source / view-data buttons on every chatbot chart), `metric_cards`
# (styled st.metric — applied in QBR), and `dataframe_explorer` (auto-
# pivot UI). MIT, additive, ~1 MB wheel, no native deps. Each sub-extra
# is imported only when used so import overhead is minimal.
# XYZ-PILOT-133 (renumbered from local -132 after swarm filed -132): captures
# click events on Plotly charts in the chatbot's chart-renderer. Library last
# released 2021 + import-validated against our Plotly 6.7.0; if click events
# regress against future Plotly versions, the try-import guard in
# `_try_render_chart_plotly` falls back to plain `st.plotly_chart` so charts
# keep rendering (just lose interactivity). MIT licence.
# XYZ-PILOT-140: streamlit-aggrid for the per-tool table in the 🔬 Execution
# statistics expander (XYZ-PILOT-119) — gives sortable, filterable, resizable
# columns, multi-select, CSV export. Same UX uplift will be applied to the
# Streamlit QBR Data tab. Replaces st.dataframe for tables that benefit
# from Excel-like interaction. Pure client-side JS bundle (~3 MB), no
# native deps, MIT licence (AG Grid Community edition).
# XYZ-PILOT-089 Phase 1: mingrammer/diagrams for AWS-icon-rich architecture
# diagrams. Chatbot emits a JSON DSL (NOT raw Python — sandboxed). Engine
# selected via spec["engine"]="diagrams". See _try_render_diagrams_dsl in
# app.py for the constrained renderer and the allowlisted node-type registry.
# XYZ-PILOT-105: plotly for richer interactive analytics charts (Tier 2).
# Altair (Tier 1) is already bundled with streamlit 1.40.0 as a transitive
# dep — verified inside the running container: altair==4.2.2. So we only
# add plotly here; the chart renderer picks the engine from spec["engine"]
# (default = altair).
# XYZ-PILOT-055 / XYZ-PILOT-186: Mermaid fenced blocks are rendered by the
# app's embedded Mermaid.js Streamlit component. Graphviz DOT (```dot blocks)
# is rendered server-side through the `dot -Tsvg` binary installed above, so
# malformed DOT produces a visible server-side error instead of a blank browser
# pane.

COPY app.py /app/app.py

ARG XYZ_LAST_DEPLOYMENT_ISSUE=XYZ-PILOT-288
ARG XYZ_BUILD_AT_UTC=not-stamped
ARG XYZ_DEPLOYED_AT_UTC=not-stamped
ENV XYZ_LAST_DEPLOYMENT_ISSUE=${XYZ_LAST_DEPLOYMENT_ISSUE}
ENV XYZ_BUILD_AT_UTC=${XYZ_BUILD_AT_UTC}
ENV XYZ_DEPLOYED_AT_UTC=${XYZ_DEPLOYED_AT_UTC}

ENV STREAMLIT_SERVER_HEADLESS=true
ENV STREAMLIT_BROWSER_GATHER_USAGE_STATS=false
ENV STREAMLIT_SERVER_FILE_WATCHER_TYPE=none

EXPOSE 8501

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8501/_stcore/health',timeout=3).read()==b'ok' else 1)"

CMD ["streamlit", "run", "app.py", \
     "--server.headless=true", \
     "--server.port=8501", \
     "--server.address=0.0.0.0", \
     "--browser.gatherUsageStats=false"]
