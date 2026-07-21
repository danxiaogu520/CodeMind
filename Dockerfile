# syntax=docker/dockerfile:1.7
FROM ghcr.io/astral-sh/uv:0.11.30 AS uv

FROM python:3.12-slim AS builder
COPY --from=uv /uv /usr/local/bin/uv
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --locked --no-dev --no-install-project
COPY src ./src
RUN uv sync --locked --no-dev && \
    XDG_CACHE_HOME=/app/.cache .venv/bin/python -c \
    "from tree_sitter_language_pack import get_parser; [get_parser(name) for name in ('python', 'rust', 'javascript', 'typescript')]"

FROM python:3.12-slim AS runtime
LABEL org.opencontainers.image.title="CodeMind" \
      org.opencontainers.image.description="Repository-level RAG Agent for code understanding" \
      org.opencontainers.image.version="0.1.0" \
      org.opencontainers.image.licenses="MIT"
RUN apt-get update && \
    apt-get install --yes --no-install-recommends ca-certificates git && \
    rm -rf /var/lib/apt/lists/* && \
    groupadd --system codemind && \
    useradd --system --gid codemind --home /app codemind && \
    mkdir -p /app /data/indexes /data/repositories /repositories && \
    chown -R codemind:codemind /app /data /repositories
WORKDIR /app
COPY --from=builder --chown=codemind:codemind /app/.venv /app/.venv
COPY --from=builder --chown=codemind:codemind /app/.cache /app/.cache
COPY --chown=codemind:codemind src ./src
COPY --chown=codemind:codemind alembic alembic
COPY --chown=codemind:codemind alembic.ini pyproject.toml README.md ./
ENV PATH="/app/.venv/bin:$PATH" PYTHONPATH="/app/src" PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 XDG_CACHE_HOME="/app/.cache"
USER codemind
EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=3s --start-period=10s --retries=5 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health/ready', timeout=2)"
CMD ["codemind-api"]
