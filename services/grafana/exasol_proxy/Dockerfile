FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim

WORKDIR /app
COPY pyproject.toml uv.lock* ./
RUN apt-get update \
    && apt-get install -y --no-install-recommends jq openssh-client \
    && rm -rf /var/lib/apt/lists/*
RUN uv sync --frozen --no-dev || uv sync --no-dev

COPY main.py ./
COPY exasol-personal-license-introspect.sh ./
RUN chmod 755 /app/exasol-personal-license-introspect.sh

ENV UV_PROJECT_ENVIRONMENT=/app/.venv
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python -c "import urllib.request,sys; r=urllib.request.urlopen('http://localhost:8000/health',timeout=3); sys.exit(0 if r.status==200 else 1)"

CMD ["uv", "run", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
