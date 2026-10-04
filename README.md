# Agent Runtime

Codex / Multica 开发容器，支持 `linux/amd64`、`linux/arm64`。每个用户拥有独立 HOME、配置和登录状态；同一用户的多个 Agent 可共用一个容器。

默认镜像：`ghcr.io/creekxi2026/agent-runtime:latest`。

## 工具

- Codex、Multica、Lark CLI、miniprogram-ci、官方 Playwright CLI、Chromium，以及完整官方 Playwright / Lark skills。
- Go、Node/npm/pnpm、Python/uv、C/C++ 编译链，Git、gh、SSH、rsync 等常用命令。
- 项目依赖按项目锁文件安装；不包含业务代码、Docker/Podman、数据库服务端或 Firefox/WebKit。

## 快速开始

需要 Docker Engine、Compose v2，以及可访问的 Multica 和模型服务。在新目录准备配置：

```bash
mkdir -p agent-runtime/user01
cd agent-runtime/user01
SOURCE=https://raw.githubusercontent.com/creekxi2026/agent-runtime/main
curl -fL "$SOURCE/compose.yaml" -o compose.yaml
curl -fL "$SOURCE/runtime.env.example" -o .env
chmod 600 .env
```

编辑 `.env`，设置唯一 `COMPOSE_PROJECT_NAME`。默认使用 Multica Cloud；自托管时填写服务地址。可填写该用户的 `MULTICA_BOOTSTRAP_TOKEN`，或先交互登录：

```bash
docker compose run --rm runtime multica login
docker compose run --rm runtime codex login
docker compose up -d
docker compose logs --tail=100 runtime
```

API Key 可使用 `codex login --with-api-key` 的标准输入方式。真实凭据不要提交 Git 或发到聊天。没有 Multica 登录状态时 daemon 会拒绝启动。

## 配置与隔离

完整模板见 [runtime.env.example](runtime.env.example) 和 [compose.yaml](compose.yaml)。相对路径以 Compose 所在目录为基准。

| 配置 | 用途 / 默认值 |
| --- | --- |
| `COMPOSE_PROJECT_NAME` | 每个实例唯一 |
| `RUNTIME_IMAGE` | 完整镜像引用，默认上述 full 开发镜像 |
| `HOME_VOLUME` | 私有命名卷，默认 `<COMPOSE_PROJECT_NAME>-home` |
| `CPU_LIMIT` / `MEMORY_LIMIT` | `2` / `4g` |
| `MULTICA_DAEMON_MAX_CONCURRENT_TASKS` | 可选；未设置时使用 Multica 原生默认值 |

多个用户分别准备 Compose / `.env`，使用不同实例名；默认 HOME 卷名随实例隔离，自定义 `HOME_VOLUME` 时也须各不相同。不要复制他人的 HOME 或凭据。镜像选择只需修改各自 `.env` 的 `RUNTIME_IMAGE`。daemon 并发上限与页面上的 Agent 并发限制独立。

入口初始化私有目录后降为 UID/GID `1000:1000` 并清除 capabilities。Compose 放宽 seccomp/AppArmor 以支持 Codex 内层沙箱，不启用 privileged 或 Docker socket；这不是不可信多租户的强隔离边界。

## 持久化

默认由 Compose 创建私有 HOME 命名卷并挂载到 `/home/agent`，保存配置、凭据、缓存、`workspace` 项目、`.local` 后装工具和自定义 skills。`volume.nocopy` 避免固化镜像中的 HOME 内容；镜像工具仍位于 `/opt`。重建时复用同一卷，其他容器可写层不持久化。

普通 `docker compose down` 保留命名卷；不要对需要保留的数据使用 `down -v` 或删除卷。HOME 卷包含凭据，备份需按敏感数据保管。

需要宿主机可见目录时，另下载 [compose.bind-home.yaml](compose.bind-home.yaml)，设置 `HOME_DIR=./home`，并在 `.env` 设置 `COMPOSE_FILE=compose.yaml:compose.bind-home.yaml`。该显式 override 保留目录挂载方式；已有 bind 部署更新配置时也使用它。

```bash
docker compose exec --user 1000:1000 runtime bash
npm install -g 包名
uv tool install 包名
```

后装工具使用 `~/.local/bin`。Multica 的 Codex 路径由 `MULTICA_CODEX_PATH` 指定。官方 skills 位于 `/opt/agent-skills`，启动时链接到 `~/.agents/skills`；升级会移除失效的官方链接，保留自定义目录和链接。自定义 skills 放在 `~/.agents/skills/技能名/SKILL.md`。Multica 可继承用户级技能，提供方同名技能优先；`Copy from a runtime` 是快照。

## 可选共享 Codex

默认不挂载任何共享凭据。需要共享 API-key 配置时，下载 [compose.shared-codex.yaml](compose.shared-codex.yaml)，在 `.env` 设置 `SHARED_CODEX_DIR`，并准备专用目录中的 `config.toml` 和 `auth.json`（[配置模板](shared-codex/config.toml.example)、[认证模板](shared-codex/auth.json.example)）。目录及文件需允许 UID/GID `1000:1000` 读取。

```bash
docker compose -f compose.yaml -f compose.shared-codex.yaml up -d
```

该目录只读挂载到 `/shared/codex`，仅链接配置和认证文件，sessions/cache 仍在私有 HOME。原有私有文件保留为 `.before-shared`，备份冲突时拒绝覆盖。共享目录中的原子文件替换可被运行中的实例读取；所有参与实例都能读取共享 Key。此模式只支持 API Key，不支持 ChatGPT OAuth，不执行 `codex login/logout`。共享模式后续命令也需使用上述两个 `-f` 参数。

## 可选共享下载缓存

默认缓存仍在私有 HOME。可信 Linux 容器可下载 [compose.shared-cache.yaml](compose.shared-cache.yaml)，在 `.env` 设置 `COMPOSE_FILE=compose.yaml:compose.shared-cache.yaml`（Windows 使用 `;` 分隔；共享 Codex 时也加入对应 override）。此后普通 `docker compose` 命令会自动使用 override。

缓存使用命名卷 `agent-runtime-download-cache`，可通过 `SHARED_CACHE_VOLUME` 自定义；只有选择同一卷名的实例才共享。HOME 始终独立。此配置不共享 Codex 凭据。

如需宿主机缓存目录，再下载 [compose.shared-cache.bind.yaml](compose.shared-cache.bind.yaml)，设置 `SHARED_CACHE_DIR=../shared/caches`，并将该 override 追加到 `COMPOSE_FILE` 最后；HOME 目录挂载可同时加入 `compose.bind-home.yaml`。

只挂载专用缓存存储到 `/shared/caches`，初始化 `npm`、`go-mod`、`uv` 三个子目录为 UID/GID `1000:1000`、权限 `0750`。仅修改这三个目录本身，不递归修改内容或共享 Codex；非目录/符号链接会拒绝启动。入口保留已发布镜像的 daemon CMD，经原 bootstrap 降权并清除 capabilities，无需重建镜像。

显式配置 `NPM_CONFIG_CACHE`、`GOMODCACHE`、`UV_CACHE_DIR`；`UV_LINK_MODE=copy` 避免跨文件系统硬链接。全局工具、项目 `node_modules`、venv、Go build cache 和凭据仍私有。不要挂载或复制 Mac 缓存，不要把凭据放入缓存根。共享缓存可被其他参与容器读取/修改，只用于相互信任、相同 UID 的容器；依赖包缓存可能包含私有源码。清理 uv 缓存时不要同时安装依赖。

验证：`SHARED_CACHE_DOCKER_TEST=1 python3 -m unittest discover -s tests -p test_shared_cache.py -v`；本机真实 npm/Go/uv fixture 与离线并发复用：`python3 tests/shared_cache_smoke.py`（需 Docker、当前镜像及 OrbStack/Docker 的 `host.docker.internal`）。测试不启动 Multica，不使用真实凭据。

## 浏览器与集成

- **浏览器：** `playwright-cli -s=任务名 open about:blank` 使用容器内无头 Chromium，原生全局配置无需额外浏览器参数。每个并发任务使用独立 session；结束时关闭自己的 session，不使用 `close-all` / `kill-all`。原生空闲超时默认 1 小时，可用 `open --idle-timeout=毫秒` 覆盖。
- **浏览器状态：** 用同一 session 的 `state-save ~/.local/share/浏览器状态.json` / `state-load` 保存和恢复；文件含敏感会话数据，不提交或共享。
- **飞书：** `.env` 可初始化应用配置；用户资源另需 OAuth：`docker compose run --rm runtime lark-cli auth login --domain docs --domain drive`。
- **小程序：** 两个架构使用锁定的 Debian 原生 zipalign 和系统库，替换 npm 包的 x86 helper/库。仅该 helper 由 UID 1000 持有以支持调用方每次 chmod；小程序签名不支持只读根文件系统。预览/上传需要项目 AppID、上传私钥及微信侧配置，镜像不包含这些凭据。

## 更新

```bash
docker compose pull
docker compose up -d
```

[发布工作流](.github/workflows/publish.yml)每日检查基础镜像、工具、官方 skills 和 Debian 签名 metadata 中的 Chromium / zipalign 依赖；有变化时验证两个架构后发布 `latest`。运行中的容器不会自动更新。避开运行任务并备份 HOME 后重建。只保留 `latest`，无历史标签或自动回滚；实际依赖清单在镜像内 `/usr/share/agent-runtime/dependencies.json`。

## 开发验证

```bash
python3 -m unittest discover -s tests -v
python3 scripts/split_dependencies.py --manifest runtime-deps.json --output .build-inputs
BASE_IMAGE=$(python3 -c 'import json; print(json.load(open("runtime-deps.json"))["base_image"])')
docker build --build-arg "BASE_IMAGE=$BASE_IMAGE" -t agent-runtime:test .
IMAGE=agent-runtime:test sh tests/smoke.sh
```

[验证工作流](.github/workflows/verify.yml)覆盖工具、私有 HOME/认证、可选只读共享配置、技能发现、Chromium、持久化及并发回收。离线 fixture 不是模型鉴权、Multica 派发、飞书授权或小程序上传验收。
