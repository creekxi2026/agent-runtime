# Both architectures use the same lock; the installer rejects a base-image mismatch.
ARG BASE_IMAGE=debian:stable-slim@sha256:5bc3287b25407c965a30f38e32603dc253a3869e1b12a21ac09bfc27fd8b13ce
FROM ${BASE_IMAGE} AS core
ARG BASE_IMAGE
ARG TARGETARCH

USER root
ENV DEBIAN_FRONTEND=noninteractive PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
      bash ca-certificates curl jq git openssh-client ripgrep rsync zip unzip \
      build-essential pkg-config python3 python3-venv tini util-linux bubblewrap \
      xz-utils libatomic1 \
    && rm -rf /var/lib/apt/lists/* /var/cache/apt/archives/*

# Only declared runtime inputs enter the image, not project source, HOME or credentials.
RUN --mount=type=bind,source=scripts/install_tools.py,target=/tmp/install_tools.py \
    --mount=type=bind,source=.build-inputs/core.json,target=/tmp/core.json \
    PYTHONDONTWRITEBYTECODE=1 python3 /tmp/install_tools.py \
      --component core --manifest /tmp/core.json \
      --arch "${TARGETARCH}" --base-image "${BASE_IMAGE}" \
    && rm -rf /root/.npm /root/.cache /tmp/agent-tools-* \
    && chmod -R go-w /opt/node /opt/go /opt/agent-tools /usr/local/bin \
    && ln -s /opt/codex/bin/codex /opt/agent-tools/bin/codex \
    && groupadd --gid 1000 agent \
    && useradd --uid 1000 --gid 1000 --home-dir /home/agent --create-home --shell /bin/bash agent \
    && mkdir -p /home/agent/.codex /home/agent/.agents /home/agent/.local/bin \
       /home/agent/.cache/go-tmp /home/agent/workspace \
    && chown -R 1000:1000 /home/agent \
    && test ! -e /data && ln -s /home/agent /data
COPY --chmod=644 agent-tools-path.sh /etc/profile.d/agent-tools.sh
COPY --chmod=755 bootstrap.sh /usr/local/bin/agent-bootstrap
COPY --chmod=755 entrypoint.sh /usr/local/bin/agent-entrypoint

# Chromium's locked versions/checksums control this layer. Replace miniprogram-ci's
# zipalign with the native architecture; only that helper is user-owned for signing.
RUN --mount=type=bind,source=scripts,target=/tmp/install-scripts \
    --mount=type=bind,source=.build-inputs/core.json,target=/tmp/core.json \
    PYTHONDONTWRITEBYTECODE=1 python3 /tmp/install-scripts/install_official.py \
      --mode chromium --manifest /tmp/core.json --arch "${TARGETARCH}" \
    && PYTHONDONTWRITEBYTECODE=1 python3 /tmp/install-scripts/install_official.py \
      --mode native-helper --manifest /tmp/core.json --arch "${TARGETARCH}" \
    && PYTHONDONTWRITEBYTECODE=1 python3 /tmp/install-scripts/install_official.py \
      --mode skills --manifest /tmp/core.json --arch "${TARGETARCH}" \
    && chmod -R go-w /opt/agent-skills \
    && rm -rf /var/lib/apt/lists/* /var/cache/apt/archives/*
COPY playwright.json /usr/share/agent-runtime/playwright.json
RUN chmod 755 /usr/share/agent-runtime && chmod 644 /usr/share/agent-runtime/playwright.json

FROM core AS codex-tool
RUN --mount=type=bind,source=scripts/install_tools.py,target=/tmp/install_tools.py \
    --mount=type=bind,source=.build-inputs/codex.json,target=/tmp/codex.json \
    PYTHONDONTWRITEBYTECODE=1 python3 /tmp/install_tools.py \
      --component codex --manifest /tmp/codex.json --arch "${TARGETARCH}" \
    && chmod -R go-w /opt/codex

FROM core AS multica-tool
RUN --mount=type=bind,source=scripts/install_tools.py,target=/tmp/install_tools.py \
    --mount=type=bind,source=.build-inputs/multica.json,target=/tmp/multica.json \
    PYTHONDONTWRITEBYTECODE=1 python3 /tmp/install_tools.py \
      --component multica --manifest /tmp/multica.json --arch "${TARGETARCH}" \
    && chmod -R go-w /opt/multica

FROM core AS runtime
# --link keeps CLI layers independent; destinations must not traverse symlinks.
COPY --link --from=codex-tool /opt/codex /opt/codex
COPY --link --from=multica-tool /opt/multica/bin/multica /usr/local/bin/multica
# The full lock is metadata; component locks control installation layers.
COPY runtime-deps.json /usr/share/agent-runtime/dependencies.json

ENV HOME=/home/agent \
    LANG=C.UTF-8 \
    CODEX_HOME=/home/agent/.codex LARKSUITE_CLI_CONFIG_DIR=/home/agent/.lark-cli \
    XDG_CONFIG_HOME=/home/agent/.config XDG_CACHE_HOME=/home/agent/.cache XDG_DATA_HOME=/home/agent/.local/share \
    BASH_ENV=/etc/profile.d/agent-tools.sh \
    MULTICA_CODEX_PATH=/opt/agent-tools/bin/codex \
    PLAYWRIGHT_BROWSERS_PATH=/home/agent/.cache/ms-playwright PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1 \
    NODE_PATH=/opt/agent-tools/node_modules \
    GOROOT=/opt/go GOPATH=/home/agent/.local/share/go GOBIN=/home/agent/.local/bin \
    GOTMPDIR=/home/agent/.cache/go-tmp \
    NPM_CONFIG_PREFIX=/home/agent/.local PNPM_HOME=/home/agent/.local/share/pnpm \
    UV_TOOL_DIR=/home/agent/.local/share/uv/tools UV_TOOL_BIN_DIR=/home/agent/.local/bin \
    UV_PYTHON_INSTALL_DIR=/home/agent/.local/share/uv/python \
    PATH="/home/agent/.local/bin:/home/agent/.local/share/pnpm:/home/agent/.local/share/pnpm/bin:/opt/agent-tools/bin:/opt/node/bin:/opt/go/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin" \
    LARKSUITE_CLI_NO_UPDATE_NOTIFIER=1 LARKSUITE_CLI_NO_SKILLS_NOTIFIER=1
USER 1000:1000
WORKDIR /home/agent
ENTRYPOINT ["/usr/local/bin/agent-bootstrap"]
CMD ["multica", "daemon", "start", "--foreground", "--no-auto-update", "--no-auto-reload", "--workspaces-root", "/home/agent/workspace"]
LABEL org.opencontainers.image.source="https://github.com/creekxi2026/agent-runtime" \
      org.opencontainers.image.description="Multica + Codex on Debian slim; persistent HOME; verified official tools; bundled Chromium and official skills; no startup downloads"
