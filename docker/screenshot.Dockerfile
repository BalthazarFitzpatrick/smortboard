# provision dependencies separately; the screenshot check runs with network disabled
ARG CARD_IMAGE=smortboard-card:latest
FROM ${CARD_IMAGE}
USER root
ENV UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_PYTHON_INSTALL_DIR=/opt/python \
    UV_LINK_MODE=copy \
    PLAYWRIGHT_BROWSERS_PATH=/opt/playwright
WORKDIR /workspace
COPY pyproject.toml uv.lock README.md LICENSE ./
COPY smortboard ./smortboard
RUN uv sync --frozen --dev --python 3.11 \
    && uv run --no-sync playwright install --with-deps chromium \
    && chmod -R a+rX /opt
USER agent
