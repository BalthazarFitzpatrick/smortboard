# image a card runs inside - one throwaway container per card, never the board itself (see
# docker/Dockerfile for that). Ships the claude CLI and the toolchain a repo's test_command needs.
#
# What is deliberately NOT here: any GitHub credential, any card token baked into the image, any
# copy of a repo. The clone and the token are bind-mounted at `docker run` time by
# smortboard/exec/backends.py::ContainerBackend - see docs/plan.md "The containment decision".
#
# base pinned by digest, resolved 2026-09-17; claude requires node 22
FROM node:22-slim@sha256:83f487e0a63425e5b4d146fb5e5be574bcbe1b7b843d3ebafdd95eaf7767a7e5

# git: the card's whole job is a local commit. python3: runs the lease and bash guard hooks -
# without it they exit 127, which does not block. ca-certificates: TLS for npm/the claude CLI.
RUN apt-get update \
    && apt-get install -y --no-install-recommends git ca-certificates python3 \
    && rm -rf /var/lib/apt/lists/*

# uv: the repo's own toolchain (project.CLAUDE.md: `uv run` for every python invocation).
# pinned release binary copied straight from astral's image, not a `curl | sh` install script
COPY --from=ghcr.io/astral-sh/uv:0.12.15 /uv /uvx /usr/local/bin/

# the claude CLI itself, pinned to the version resolved by `npm view @anthropic-ai/claude-code
# version` at the time this was written - bump deliberately, never track `latest`
RUN npm install -g @anthropic-ai/claude-code@2.1.273 @openai/codex@0.154.0

# a non-root user for the agent process: the card container has no cap-drop-immune reason to run
# as root, and F1 asks for it as defense-in-depth on top of --cap-drop=ALL
RUN usermod --login agent --home /home/agent --move-home node \
    && groupmod --new-name agent node \
    && mkdir -p /workspace \
    && chown -R agent:agent /workspace /home/agent

# the mounted clone belongs to a host uid, so git refused it as "dubious ownership" - measured, 17
# tool results across 4 cards, sending agents into `git -c safe.directory`, which the guard refuses
RUN git config --system --add safe.directory /workspace

ENV HOME=/home/agent
WORKDIR /workspace
USER agent
