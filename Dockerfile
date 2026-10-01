# syntax=docker/dockerfile:1
ARG BASE_IMAGE=ghcr.io/sapk/multica-agent-codex@sha256:03e09343455eb659789af481dfef0546532fd7302e390a2dbe50ea6f8527ab9f
FROM ${BASE_IMAGE} AS prepared
USER root
ARG LARK_CLI_VERSION=1.0.97
ARG PLAYWRIGHT_CLI_VERSION=0.1.22
ARG MINIPROGRAM_CI_VERSION=2.1.31
ENV PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1
RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends bubblewrap \
    && rm -rf /var/lib/apt/lists/*
RUN npm install -g --prefix /opt/agent-tools --no-audit --no-fund \
      "@larksuite/cli@${LARK_CLI_VERSION}" "@playwright/cli@${PLAYWRIGHT_CLI_VERSION}" \
      "miniprogram-ci@${MINIPROGRAM_CI_VERSION}" \
    && rm -rf /root/.npm
COPY scripts/relocate_upstream.py /tmp/relocate_upstream.py
RUN PYTHONDONTWRITEBYTECODE=1 python3 /tmp/relocate_upstream.py \
    && rm /tmp/relocate_upstream.py \
    && mkdir -p /home/agent/.codex /home/agent/.agents /home/agent/.local/bin /home/agent/workspace \
    && chown -R 1000:1000 /home/agent \
    && test ! -e /data && ln -s /home/agent /data
COPY --chmod=644 agent-tools-path.sh /etc/profile.d/agent-tools.sh
COPY --chmod=755 bootstrap.sh /usr/local/bin/agent-bootstrap
COPY --chmod=755 entrypoint.sh /usr/local/bin/agent-entrypoint

# Flatten after relocation so the final image does not retain a hidden second
# copy of the upstream tools in the old /home/agent layers.
FROM scratch
COPY --from=prepared / /
SHELL ["/bin/bash", "-o", "pipefail", "-c"]
ENV HOME=/home/agent \
    CODEX_HOME=/home/agent/.codex LARKSUITE_CLI_CONFIG_DIR=/home/agent/.lark-cli \
    XDG_CONFIG_HOME=/home/agent/.config XDG_CACHE_HOME=/home/agent/.cache XDG_DATA_HOME=/home/agent/.local/share \
    NVM_DIR=/opt/agent-upstream/.nvm BASH_ENV=/etc/profile.d/agent-tools.sh \
    MULTICA_CODEX_PATH=/opt/agent-upstream/.local/node-active/codex \
    PLAYWRIGHT_BROWSERS_PATH=/opt/agent-upstream/.cache/ms-playwright PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1 \
    GOROOT=/opt/agent-upstream/.local/go GOPATH=/home/agent/.local/share/go GOBIN=/home/agent/.local/bin \
    GOTMPDIR=/home/agent/.cache/go-tmp \
    NPM_CONFIG_PREFIX=/home/agent/.local PNPM_HOME=/home/agent/.local/share/pnpm \
    UV_TOOL_DIR=/home/agent/.local/share/uv/tools UV_TOOL_BIN_DIR=/home/agent/.local/bin \
    UV_PYTHON_INSTALL_DIR=/home/agent/.local/share/uv/python \
    PATH="/home/agent/.local/bin:/home/agent/.local/share/pnpm:/home/agent/.local/share/pnpm/bin:/opt/agent-tools/bin:/opt/agent-upstream/.local/go/bin:/opt/agent-upstream/.local/share/pnpm/bin:/opt/agent-upstream/.local/node-active:/opt/agent-upstream/.local/bin:/app:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin" \
    DOCKER_HOST="" TESTCONTAINERS_DOCKER_SOCKET_OVERRIDE="" \
    TESTCONTAINERS_HOST_OVERRIDE=localhost TESTCONTAINERS_RYUK_DISABLED=true AARDVARK_BINARY=/usr/bin/aardvark-dns \
    LARKSUITE_CLI_NO_UPDATE_NOTIFIER=1 LARKSUITE_CLI_NO_SKILLS_NOTIFIER=1
USER 1000:1000
WORKDIR /home/agent
ENTRYPOINT ["/usr/local/bin/agent-bootstrap"]
CMD ["multica", "daemon", "start", "--foreground", "--no-auto-update", "--no-auto-reload", "--workspaces-root", "/home/agent/workspace"]
LABEL org.opencontainers.image.source="https://github.com/creekxi2026/agent-runtime" \
      org.opencontainers.image.description="Multica + Codex; persistent /home/agent, preinstalled tools in /opt; no startup installation"
