# Agent Runtime

Codex / Multica 开发容器，支持 `linux/amd64`、`linux/arm64`。每个实例使用私有 HOME；同一可信用户的多个 Agent 可共用一个容器。

镜像：`ghcr.io/creekxi2026/agent-runtime:latest`。内置 Codex、Multica、Lark CLI、miniprogram-ci、Playwright CLI / Chromium、官方 Playwright / Lark skills，以及 Go、Node/npm/pnpm、Python/uv、C/C++、Git、gh、SSH、rsync。项目依赖按锁文件另行安装；不含业务代码、Docker/Podman 或数据库服务端。

## 准备配置

需要 Docker Engine、Compose v2，以及可访问的 Multica 和模型服务。新实例只需两个文件：

```bash
mkdir -p agent-runtime/user01
cd agent-runtime/user01
SOURCE=https://raw.githubusercontent.com/creekxi2026/agent-runtime/main
curl -fL "$SOURCE/compose.yaml" -o compose.yaml
curl -fL "$SOURCE/runtime.env.example" -o .env
chmod 600 .env
```

编辑 `.env`，将 `COMPOSE_PROJECT_NAME` 改为**唯一实例名**；`RUNTIME_IMAGE` 可指定镜像。默认连接 Multica Cloud，自托管填写服务地址。先完成下面的存储选择，再登录启动。

## 先选存储，再启动

默认选 named volume（命名卷），由 Docker 管理；只有需要直接访问宿主机文件时才选 bind（目录挂载）。相对路径以首个 Compose 文件所在目录为基准。

| 数据 | 容器挂载点 | 默认 / 可选来源 |
| --- | --- | --- |
| 私有 HOME | `/home/agent`，可写 | 默认命名卷 `<COMPOSE_PROJECT_NAME>-home`，可用 `HOME_VOLUME` 指定；bind 用 `HOME_DIR` |
| 下载缓存 | `/shared/caches`，可写 | 默认不挂载，缓存留在 HOME；可选命名卷 `agent-runtime-download-cache`，或 bind `SHARED_CACHE_DIR` |
| 共享 Codex 配置 | `/shared/codex`，只读 | 默认不挂载；可选专用目录 `SHARED_CODEX_DIR` |

HOME 保存配置、凭据、sessions、项目 `workspace`、后装工具和自定义 skills。不同实例不要复用 HOME；自定义 `HOME_VOLUME` 也须各不相同。镜像工具位于 `/opt`，HOME 命名卷使用 `nocopy`，不会固化镜像工具。

### 选择 override

从上面的 `SOURCE` 地址下载所需文件，放在 `compose.yaml` 旁，再编辑 `.env` 的 `COMPOSE_FILE`。基础文件必须在前，bind override 必须在对应命名卷配置之后；Windows 用 `;` 代替 `:`。设置后，登录、启动、shell、更新均使用普通 `docker compose` 命令，不要另加一组遗漏 override 的 `-f`。

| 选择 | 下载的 override | `.env` 设置 |
| --- | --- | --- |
| 默认私有 HOME 命名卷 | 无 | 不设置 `COMPOSE_FILE` |
| 私有 HOME 目录 | [compose.bind-home.yaml](compose.bind-home.yaml) | `HOME_DIR=./home`；`COMPOSE_FILE=compose.yaml:compose.bind-home.yaml` |
| 共享下载缓存命名卷 | [compose.shared-cache.yaml](compose.shared-cache.yaml) | `COMPOSE_FILE=compose.yaml:compose.shared-cache.yaml`；按需指定 `SHARED_CACHE_VOLUME` |
| 共享下载缓存目录 | 上一行文件及 [compose.shared-cache.bind.yaml](compose.shared-cache.bind.yaml) | `SHARED_CACHE_DIR=../shared/caches`；`COMPOSE_FILE=compose.yaml:compose.shared-cache.yaml:compose.shared-cache.bind.yaml` |
| 只读共享 Codex | [compose.shared-codex.yaml](compose.shared-codex.yaml) | `SHARED_CODEX_DIR=../shared-codex`；`COMPOSE_FILE=compose.yaml:compose.shared-codex.yaml` |

可组合使用，例如 HOME 和缓存都选 bind，并启用共享 Codex：

```dotenv
COMPOSE_FILE=compose.yaml:compose.shared-cache.yaml:compose.shared-codex.yaml:compose.bind-home.yaml:compose.shared-cache.bind.yaml
HOME_DIR=./home
SHARED_CACHE_DIR=../shared/caches
SHARED_CODEX_DIR=../shared-codex
```

使用 HOME bind 时必须启用 `compose.bind-home.yaml`；仅设置 `HOME_DIR` 不会切换或迁移存储。

### 共享边界

- **下载缓存：** 仅共享 npm、Go module、uv 缓存；全局工具、`node_modules`、venv、Go build cache 和凭据仍私有。入口只初始化 `npm`、`go-mod`、`uv` 三个目录为 `1000:1000` / `0750`，不递归修改内容；非目录或符号链接会拒绝启动。uv 使用 `copy` 避免跨文件系统硬链接，清理 uv 缓存时不要同时安装依赖。
- **可信范围：** 同一缓存卷名或 bind 来源的容器可读取、修改彼此缓存，依赖包可能含私有源码。仅用于相互信任、相同 UID 的 Linux 容器；不要挂载或复制 Mac 缓存，不要在缓存根放凭据。
- **Codex：** 先创建专用目录，放入 `config.toml` 和 `auth.json`（[配置模板](shared-codex/config.toml.example)、[认证模板](shared-codex/auth.json.example)），允许 UID/GID `1000:1000` 读取。缺失目录不会自动创建。只链接这两个文件，**不要共享整个 `.codex`**；sessions/cache 仍在私有 HOME。原私有文件备份为 `.before-shared`，备份冲突时拒绝覆盖。
- **共享 Key：** 所有参与实例都能读取共同 Key，只读不代表保密。仅支持 API Key，不支持需刷新写入的 ChatGPT OAuth；共享模式不执行 `codex login/logout`。原子替换共享文件可供后续读取，不保证在途任务热更新，共享默认配置也不是强制策略。

## 登录与启动

仅自托管实例：首次交互登录前，先将地址替换为 `.env` 的对应值执行；未配置页面地址时跳过第二行。启动 daemon 才会自动写入 `.env` 地址。

```bash
docker compose run --rm runtime multica config set server_url '你的服务地址'
docker compose run --rm runtime multica config set app_url '你的页面地址'
```

未填写 `MULTICA_BOOTSTRAP_TOKEN` 时，交互登录 Multica；已填写则跳过：

```bash
docker compose run --rm runtime multica login
```

仅私有 Codex 认证模式执行以下登录；**共享 Codex 模式跳过**。API Key 可用 `codex login --with-api-key` 从标准输入读取，真实凭据不要提交 Git 或发到聊天。

```bash
docker compose run --rm runtime codex login
```

最后启动；没有 Multica 登录状态时 daemon 会拒绝启动：

```bash
docker compose up -d
docker compose logs --tail=100 runtime
```

## 日常使用与权限

```bash
docker compose exec --user 1000:1000 runtime bash
```

应用以 UID/GID `1000:1000` 运行；启动入口短暂使用 root 初始化私有目录，然后降权并清除 capabilities。控制台和 `docker compose exec` 可能默认 root，诊断和安装后装工具时显式使用上面的 UID，避免留下 root 所有的私有文件。

- 后装工具可用 `npm install -g 包名`、`uv tool install 包名`，保存在 HOME；用户命令目录为 `~/.local/bin`。Multica 的 Codex 路径由 `MULTICA_CODEX_PATH` 指定。
- 官方 skills 从 `/opt/agent-skills` 链接到 `~/.agents/skills`；自定义放在 `~/.agents/skills/技能名/SKILL.md`。升级保留自定义内容。Multica 的 `Copy from a runtime` 是快照。
- 浏览器使用 `playwright-cli -s=任务名 open about:blank`。并发任务各用独立 session，结束只关闭自己的 session，不用 `close-all` / `kill-all`。默认空闲超时 1 小时；`state-save` / `state-load` 的状态文件含敏感会话数据，应留在私有 HOME。
- 飞书应用配置可通过 `.env` 初始化；用户资源另需 `docker compose run --rm runtime lark-cli auth login --domain docs --domain drive`。小程序预览/上传另需项目 AppID、上传私钥及微信侧配置，不支持只读根文件系统。

资源默认 `CPU_LIMIT=2`、`MEMORY_LIMIT=4g`。`MULTICA_DAEMON_MAX_CONCURRENT_TASKS` 未设置时使用 Multica 原生默认值，与页面上的 Agent 并发限制独立。完整配置见 [runtime.env.example](runtime.env.example) 和 [compose.yaml](compose.yaml)。Compose 放宽 seccomp/AppArmor 以支持 Codex 内层沙箱，不启用 privileged 或 Docker socket；不适合不可信多租户强隔离。

## 更新与保留数据

避开运行任务，备份 HOME 和 `.env`，保留相同实例名、HOME 来源及 `COMPOSE_FILE` 后执行：

```bash
docker compose pull
docker compose up -d
```

重建复用 HOME，容器其他可写层不持久化。普通 `docker compose down` 保留卷；**`down -v` 或删除卷会删除数据，也可能影响共同使用缓存卷的其他实例**。HOME 备份含凭据，按敏感数据保管。

[发布工作流](.github/workflows/publish.yml)每日检查基础镜像、工具、官方 skills 和 Chromium / zipalign 依赖，变化经双架构验证后发布 `latest`；不会自动更新运行中的容器。只有 `latest`，无历史标签或自动回滚。镜像依赖清单：`/usr/share/agent-runtime/dependencies.json`。

## 开发验证

```bash
python3 -m unittest discover -s tests -v
python3 scripts/split_dependencies.py --manifest runtime-deps.json --output .build-inputs
BASE_IMAGE=$(python3 -c 'import json; print(json.load(open("runtime-deps.json"))["base_image"])')
docker build --build-arg "BASE_IMAGE=$BASE_IMAGE" -t agent-runtime:test .
IMAGE=agent-runtime:test sh tests/smoke.sh
```

共享缓存测试：`SHARED_CACHE_DOCKER_TEST=1 python3 -m unittest discover -s tests -p test_shared_cache.py -v`；npm/Go/uv fixture：`python3 tests/shared_cache_smoke.py`（需 Docker、当前镜像及 `host.docker.internal`）。[验证工作流](.github/workflows/verify.yml)覆盖工具、挂载、私有状态、共享配置、技能和浏览器。离线 fixture 不使用真实凭据，不能证明模型鉴权、Multica 派发、飞书授权或小程序上传成功。
