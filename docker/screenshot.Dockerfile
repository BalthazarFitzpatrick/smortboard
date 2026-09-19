# provision dependencies separately; the screenshot check runs with network disabled
#
# CI-only image for smortboard's own screenshot test, but also the reference example for any repo
# maintainer whose own tests need a real browser in their gate - copy the playwright install line
# below into your repo's own docker/smortboard-repo.Dockerfile (see repo_image.py's docstring)
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
