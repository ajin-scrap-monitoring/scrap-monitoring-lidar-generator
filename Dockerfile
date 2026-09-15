ARG PYTHON_IMAGE=python:3.14.4-slim-bookworm@sha256:fc74d22ffd0d5ac395a4b7bdda75a4539758862c49ebf3005647084631e63789
ARG UV_IMAGE=ghcr.io/astral-sh/uv:0.12.15@sha256:62f8c047d0a0e9ece6b53fc63df902585a67a47a7f318ddec4a37db586edc8e3

FROM ${UV_IMAGE} AS uv

FROM ${PYTHON_IMAGE} AS builder

COPY --from=uv /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /app

COPY pyproject.toml uv.lock README.md .python-version ./
COPY src/ src/

RUN uv sync --locked --no-dev --no-editable

FROM ${PYTHON_IMAGE}

LABEL org.opencontainers.image.source="https://github.com/ajin-scrap-monitoring/scrap-monitoring-lidar-generator" \
      org.opencontainers.image.title="Scrap Monitoring LiDAR Simulator"

RUN groupadd --gid 10001 generator \
    && useradd --uid 10001 --gid generator --no-create-home \
        --home-dir /nonexistent --shell /usr/sbin/nologin generator \
    && install -d -o generator -g generator /data

WORKDIR /app

COPY --from=builder --chown=generator:generator /app/.venv /app/.venv
COPY --chown=generator:generator docs/dependencies.md /app/docs/dependencies.md

ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1

USER 10001:10001
WORKDIR /data
STOPSIGNAL SIGTERM

ENTRYPOINT ["scrap-monitoring-lidar-simulator"]
