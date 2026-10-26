# Runtime dependencies come from uv.lock. uv 0.12.0 matches the lockfile
# written for this repository. Python stays on the existing 3.10 slim image.
FROM python:3.10-slim AS build

COPY --from=ghcr.io/astral-sh/uv:0.12.0 /uv /usr/local/bin/uv

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY README.md ./
COPY apps ./apps
RUN uv sync --frozen --no-dev --no-editable

FROM python:3.10-slim AS runtime

RUN groupadd --system --gid 10001 urumi \
    && useradd --system --uid 10001 --gid 10001 --home-dir /home/urumi --create-home --shell /bin/false urumi \
    && install -d -m 1777 /tmp

COPY --from=build /app/.venv /app/.venv

ENV PATH="/app/.venv/bin:$PATH" \
    HOME=/home/urumi \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
USER urumi
EXPOSE 8000

# No image HEALTHCHECK. Process liveness is not the same thing as upstream
# listing success. Compose and the Helm chart already probe GET /health.
CMD ["uvicorn", "apps.utility_mcp.app.main:app", "--host", "0.0.0.0", "--port", "8000"]

# Application source identity for this final stage. unknown is not HEAD.
ARG URUMI_SOURCE_REVISION=unknown
ARG URUMI_SOURCE_DIRTY=unknown
LABEL io.urumi.source.revision="${URUMI_SOURCE_REVISION}" \
      io.urumi.source.dirty="${URUMI_SOURCE_DIRTY}"
