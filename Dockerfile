# syntax=docker/dockerfile:1
# CI resolves this once, shares runtime-deps.json across both architectures, and
# passes its base_image as BASE_IMAGE. The installer rejects a mismatched FROM.
ARG BASE_IMAGE=debian:stable-slim@sha256:5bc3287b25407c965a30f38e32603dc253a3869e1b12a21ac09bfc27fd8b13ce
FROM ${BASE_IMAGE} AS core
ARG BASE_IMAGE
ARG TARGETARCH
# Supply the UTC build day to refresh signed Debian security packages daily.
ARG APT_REFRESH=manual
USER root
ENV DEBIAN_FRONTEND=noninteractive PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1
RUN test -n "${APT_REFRESH}" \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
      bash ca-certificates curl jq git openssh-client ripgrep rsync zip unzip \
      build-essential pkg-config python3 python3-venv tini util-linux bubblewrap \
      xz-utils libatomic1 \
    && rm -rf /var/lib/apt/lists/* /var/cache/apt/archives/*

# Only explicitly enumerated runtime inputs enter this public image. No project
# source, user HOME, credentials or full third-party root filesystem is copied.
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

# CLI stages inherit the core toolchains, not one another's inputs or outputs.
# Build-only scripts, input locks and npm caches never enter the final image.
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
# --link makes each copied CLI blob independent of the preceding CLI layer.
# Both destinations are real paths (never a symlink traversal), and neither
# COPY duplicates the core filesystem or any unrelated /usr/local/bin tools.
COPY --link --from=codex-tool /opt/codex /opt/codex
COPY --link --from=multica-tool /opt/multica/bin/multica /usr/local/bin/multica
# The complete lock is metadata only; it cannot invalidate tool installation.
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
      org.opencontainers.image.description="Multica + Codex on Debian slim; persistent HOME; verified official tools; no bundled browsers or startup installation"
