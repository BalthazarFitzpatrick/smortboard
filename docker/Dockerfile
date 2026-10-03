# multi-platform base and uv digests resolved 2026-10-03
FROM python:3.13-slim@sha256:bb2988715db2cf7ace7b53f38f3cffbef7c7046a656bee66245eb0ed386e2e81

# git is needed at build time: ui_base is a git dependency and python:3.13-slim ships without it,
# which fails as an opaque "Git operation failed" from uv rather than a missing-binary error
RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.12.15@sha256:62f8c047d0a0e9ece6b53fc63df902585a67a47a7f318ddec4a37db586edc8e3 /uv /uvx /usr/local/bin/

WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY smortboard ./smortboard

RUN uv sync --locked --no-dev --no-editable

ENV SMORTBOARD_DB=/data/smortboard.db
ENV SMORTBOARD_PORT=8000
# every interface inside the container, or the published port reaches nothing - the compose file
# publishes it on the host's loopback only
ENV SMORTBOARD_HOST=0.0.0.0
EXPOSE 8000

CMD ["uv", "run", "--no-sync", "python", "-m", "smortboard.server"]
