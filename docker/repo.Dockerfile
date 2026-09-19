# smortboard's own repo image: the card image plus python and every locked dependency, so the test
# gate can run the suite with --network none. set it as the repo's `image` on the board.
#
# WHY IT EXISTS: the stock card image has no python, and the gate has no network, so `uv run pytest`
# died fetching cpython (measured: `dns error`). rebuild whenever uv.lock OR docker/card.Dockerfile
# changes - smortboard.preflight.check_image_staleness compares this image's own build time against
# uv.lock's last commit and against smortboard-card:latest's build time, and warns in preflight
# before a card ever starts, rather than failing the gate for a reason that has nothing to do with
# the card's work:
#   docker build -f docker/card.Dockerfile -t smortboard-card:latest .
#   docker build -f docker/repo.Dockerfile -t smortboard-repo:latest .
FROM smortboard-card:latest

# root for the build steps below (/opt is root-owned) - card.Dockerfile already dropped to the
# non-root `agent` user, and this stage switches back to it at the end
USER root

# venv and interpreter outside /workspace, which is where the card's clone gets mounted over
ENV UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_PYTHON_INSTALL_DIR=/opt/python \
    UV_LINK_MODE=copy

# AN EDITABLE INSTALL POINTING AT /workspace: the mounted clone is what imports, while the package
# metadata (its version, the smortboard script) exists without the gate needing network - a
# dependencies-only venv let a correct --version pass in the agent's container and fail the gate
WORKDIR /workspace
COPY --chown=agent:agent pyproject.toml uv.lock ./
COPY --chown=agent:agent smortboard ./smortboard
RUN uv sync --frozen && rm -rf /workspace/* \
    && mkdir -p /opt/python \
    && chown -R agent:agent /opt/venv /opt/python /home/agent/.cache

USER agent
