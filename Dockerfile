# Build stage: install the locked dependencies and the app into a virtualenv
FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim AS builder
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=0
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --locked --no-dev --no-install-project
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv uv sync --locked --no-dev --no-editable

# Runtime stage: just Python and the virtualenv
FROM python:3.13-slim-bookworm
COPY --from=builder /app/.venv /app/.venv
ENV PATH=/app/.venv/bin:$PATH
# Run from /data: mount your .env and the .mixmansion workspace here
WORKDIR /data
ENTRYPOINT ["mixmansion"]
