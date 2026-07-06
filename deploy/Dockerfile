# syntax=docker/dockerfile:1
# Lullabeast single-container image (DS-3).
#
# One container, multiple supervised processes (OpenClaw gateway, UI server,
# heartbeat loop, session-cleanup loop); see deploy/entrypoint.sh. The
# orchestrator is NOT a supervised service: the UI server spawns it per run,
# exactly as on a host.
#
# Base: the official Playwright image supplies Node 22+, Chromium, and every
# browser system dependency; noble (Ubuntu 24.04) carries Python 3.12. The tag
# pins the Playwright release line that @playwright/mcp@0.0.40 (the
# visual-review MCP registered in the golden config) depends on, so the baked
# browser build matches what the MCP drives. Never float this tag.
FROM mcr.microsoft.com/playwright:v1.56.0-noble

# OpenClaw pin (exact version, never "latest"). OpenClaw is MIT-licensed
# (Task 0 decision, recorded in deploy/README.md), so the default image bakes
# it at build. Build with --build-arg OPENCLAW_VERSION= (empty) for the
# no-bake variant: the entrypoint then installs this same pin into the /data
# volume on first boot (slower first start, no OpenClaw bits in the image).
ARG OPENCLAW_VERSION=2026.6.11
ENV OPENCLAW_VERSION=${OPENCLAW_VERSION}

# DS-4: no sudo in the image (nothing needs it; the runtime user is non-root
# and compose sets no-new-privileges, so a leftover setuid sudo would be inert
# anyway, but it does not belong in the blast-radius boundary at all).
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        python3 python3-venv python3-pip git \
    && (apt-get purge -y --auto-remove sudo || true) \
    && rm -rf /var/lib/apt/lists/* /etc/sudoers /etc/sudoers.d

RUN if [ -n "$OPENCLAW_VERSION" ]; then \
        npm install -g "openclaw@${OPENCLAW_VERSION}"; \
    fi

# Ensure the exact Chromium build @playwright/mcp@0.0.40 drives is baked.
# No-op when the base image already carries that build; downloads the missing
# revision otherwise (covers version skew between the base tag and the MCP's
# own playwright-core dependency). Runs as root so it can write /ms-playwright.
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
RUN npx -y --package=@playwright/mcp@0.0.40 -- playwright-core install chromium

# Non-root user (DS-4: every runtime process runs as it). uid 1000 by default
# so files in the ./projects bind mount come out owned by the typical host
# user; the base image's pwuser (also uid 1000) is removed to free the uid.
ARG LULLABEAST_UID=1000
RUN (userdel -r pwuser 2>/dev/null || true) \
    && useradd --create-home --shell /bin/bash --uid "${LULLABEAST_UID}" lullabeast \
    && mkdir -p /data \
    && chown lullabeast:lullabeast /data

# DS-4: /app is root-owned and read-only to the runtime user. The pipeline
# executes LLM-generated code as lullabeast (the executor has exec, and there
# is a documented model bug that deletes files), so the orchestrator, gate
# scripts, UI server, installer, and agent identity sources must not be
# writable by that user. Three narrow write islands are the per-boot contract
# install.sh (the single provisioning brain) and the entrypoint depend on:
#   /app and /app/ui   group-writable with the sticky bit (like /tmp):
#     lullabeast can create .env, .autodev/, ui/config.json and their
#     atomic-write temp files, but cannot remove or replace root-owned code.
#   /app/autodev/plugin   fully lullabeast-owned: install.sh rebuilds the
#     signals plugin bundle here on every boot.
COPY . /app
WORKDIR /app
RUN chown -R lullabeast:lullabeast /app/autodev/plugin \
    && chgrp lullabeast /app /app/ui \
    && chmod 1775 /app /app/ui
USER lullabeast

# Python deps live in a user-owned venv (noble's system Python is externally
# managed, PEP 668), so install.sh's per-boot pip run works unprivileged and
# is a fast no-op against the baked pins.
ENV VIRTUAL_ENV=/home/lullabeast/venv
ENV PATH="$VIRTUAL_ENV/bin:$PATH"
RUN python3 -m venv "$VIRTUAL_ENV" \
    && pip install --no-cache-dir -r requirements.txt

# Build the pipeline-signals plugin bundle. install.sh rebuilds it at every
# boot (it is the provisioning brain); baking node_modules + dist here makes
# boots fast and independent of the npm registry.
RUN cd autodev/plugin && npm install && npm run build

# Pre-fetch the visual-review MCP package so the first agent use needs no
# network, and point the doctor's ~/.cache/ms-playwright probe at the baked
# browser tree.
RUN npx -y --package=@playwright/mcp@0.0.40 -- node -e "process.exit(0)" \
    && mkdir -p /home/lullabeast/.cache \
    && ln -sfn /ms-playwright /home/lullabeast/.cache/ms-playwright

# Container layout contract (deploy/EVAL-MIGRATION.md): the repo is
# read-mostly at /app; ALL state lives under the /data volume.
# OPENCLAW_STATE_DIR is OpenClaw's own state-location env (the openclaw CLI
# ignores Lullabeast's OPENCLAW_ROOT); the entrypoint also symlinks
# ~/.openclaw at /data/openclaw for config values written as literal
# "~/.openclaw/..." paths.
ENV OPENCLAW_ROOT=/data/openclaw \
    OPENCLAW_STATE_DIR=/data/openclaw \
    AUTODEV_REPO_PATH=/app \
    AUTODEV_PIPELINE_ROOT=/data/pipeline-state \
    UI_PORT=18790

# Python bytecode caches would otherwise try to land next to the root-owned
# sources in /app (Python silently skips caching when the directory is not
# writable); keep them under /tmp instead, where writes are allowed by the
# DS-4 posture.
ENV PYTHONPYCACHEPREFIX=/tmp/pycache

EXPOSE 18790
ENTRYPOINT ["/app/deploy/entrypoint.sh"]
