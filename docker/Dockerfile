FROM python:3.13-slim

RUN pip install --no-cache-dir uv

WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY smortboard ./smortboard

RUN uv sync --no-dev --no-editable

ENV SMORTBOARD_DB=/data/smortboard.db
ENV SMORTBOARD_PORT=8000
EXPOSE 8000

CMD ["uv", "run", "python", "-m", "smortboard.server"]
