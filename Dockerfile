# syntax=docker/dockerfile:1
ARG BASE_IMAGE=ghcr.io/sapk/multica-agent-codex@sha256:252dfcd593a274e1fea37eddd79df29d16c0e8127c1be33c2fc99280cb5205ee
FROM ${BASE_IMAGE}
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
    && mkdir -p /data/.codex /data/.agents/skills /data/workspace \
    && chown -R 1000:1000 /data \
    && rm -rf /root/.npm
COPY --chmod=644 agent-tools-path.sh /etc/profile.d/agent-tools.sh
# Login shells reset PATH; non-interactive Bash also loads the upstream BASH_ENV.
RUN printf '\n. /etc/profile.d/agent-tools.sh\n' >> "${BASH_ENV}"
COPY --chmod=755 entrypoint.sh /usr/local/bin/agent-entrypoint
ENV PATH="/opt/agent-tools/bin:${PATH}" \
    HOME=/data CODEX_HOME=/data/.codex LARKSUITE_CLI_CONFIG_DIR=/data/.lark-cli \
    XDG_CONFIG_HOME=/data/.config XDG_CACHE_HOME=/data/.cache XDG_DATA_HOME=/data/.local/share \
    DOCKER_HOST="" TESTCONTAINERS_DOCKER_SOCKET_OVERRIDE="" \
    LARKSUITE_CLI_NO_UPDATE_NOTIFIER=1 LARKSUITE_CLI_NO_SKILLS_NOTIFIER=1
USER 1000:1000
WORKDIR /data/workspace
# Do not run the upstream Podman/RTK bootstrap.
ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/agent-entrypoint"]
CMD ["multica", "daemon", "start", "--foreground", "--no-auto-update", "--no-auto-reload", "--workspaces-root", "/data/workspace"]
LABEL org.opencontainers.image.source="https://github.com/creekxi2026/agent-runtime" \
      org.opencontainers.image.description="Multica + Codex with Lark CLI and Playwright CLI; no startup installation"
