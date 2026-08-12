FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim

WORKDIR /app

COPY pyproject.toml uv.lock* ./
RUN uv sync --frozen --no-dev || uv sync --no-dev

COPY app.py db.py ./
COPY .streamlit/ ./.streamlit/

ARG XYZ_LAST_DEPLOYMENT_ISSUE=XYZ-PILOT-202
ARG XYZ_DEPLOYMENT_REF_URL=
ARG XYZ_BUILD_AT_UTC=not-stamped
ARG XYZ_DEPLOYED_AT_UTC=not-stamped
ENV UV_PROJECT_ENVIRONMENT=/app/.venv
ENV STREAMLIT_SERVER_HEADLESS=true
ENV STREAMLIT_BROWSER_GATHER_USAGE_STATS=false
ENV STREAMLIT_SERVER_FILE_WATCHER_TYPE=none
ENV XYZ_LAST_DEPLOYMENT_ISSUE=${XYZ_LAST_DEPLOYMENT_ISSUE}
ENV XYZ_DEPLOYMENT_REF_URL=${XYZ_DEPLOYMENT_REF_URL}
ENV XYZ_BUILD_AT_UTC=${XYZ_BUILD_AT_UTC}
ENV XYZ_DEPLOYED_AT_UTC=${XYZ_DEPLOYED_AT_UTC}

EXPOSE 8501

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8501/_stcore/health',timeout=3).read()==b'ok' else 1)"

CMD ["uv", "run", "streamlit", "run", "app.py", \
     "--server.headless=true", \
     "--server.port=8501", \
     "--server.address=0.0.0.0", \
     "--browser.gatherUsageStats=false"]
