# image a card runs inside - one throwaway container per card, never the board itself (see
# docker/Dockerfile for that). Ships the claude CLI and the toolchain a repo's test_command needs.
#
# What is deliberately NOT here: any GitHub credential, any card token baked into the image, any
# copy of a repo. The clone and the token are bind-mounted at `docker run` time by
# smortboard/exec/backends.py::ContainerBackend - see docs/PLAN.md "The containment decision".
FROM node:20-slim

# git: the card's whole job is a local commit. curl: fetches the claude CLI installer.
# python3: runs the lease and bash guard hooks - without it they exit 127, which does not block
RUN apt-get update \
    && apt-get install -y --no-install-recommends git curl ca-certificates python3 \
    && rm -rf /var/lib/apt/lists/*

# uv: the repo's own toolchain (project.CLAUDE.md: `uv run` for every python invocation)
RUN curl -LsSf https://astral.sh/uv/install.sh | sh \
    && mv /root/.local/bin/uv /root/.local/bin/uvx /usr/local/bin/

# the claude CLI itself
RUN npm install -g @anthropic-ai/claude-code

WORKDIR /workspace
