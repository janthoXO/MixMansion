# Build stage: install the locked dependencies and the app
FROM ghcr.io/astral-sh/uv:python3.13-trixie-slim AS builder
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=0
WORKDIR /app
COPY pyproject.toml uv.lock README.md LICENSE ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --locked --no-dev --no-install-project
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv uv sync --locked --no-dev --no-editable

# Runtime stage: distroless Python (3.13 on Debian 13), no shell, no package manager.
# Only the installed packages are copied: the venv's interpreter links don't exist here.
# The builder's Python minor version must match the runtime's (python3.13 below).
FROM gcr.io/distroless/python3-debian13
COPY --from=builder /app/.venv/lib/python3.13/site-packages /app/site-packages
ENV PYTHONPATH=/app/site-packages
# Run from /data: mount your .env and the .mixmansion workspace here
WORKDIR /data
ENTRYPOINT ["python3", "-m", "mixmansion"]
