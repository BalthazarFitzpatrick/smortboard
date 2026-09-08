FROM python:3.13-slim

# git is needed at build time: ui_base is a git dependency and python:3.13-slim ships without it,
# which fails as an opaque "Git operation failed" from uv rather than a missing-binary error
RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir uv

WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY smortboard ./smortboard

RUN uv sync --no-dev --no-editable

ENV SMORTBOARD_DB=/data/smortboard.db
ENV SMORTBOARD_PORT=8000
EXPOSE 8000

CMD ["uv", "run", "python", "-m", "smortboard.server"]
