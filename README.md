# Agent Runtime

基于 Debian slim 的 Codex / Multica 开发环境，支持 `linux/amd64`、`linux/arm64`。同一信任用户共用一个开发容器，多个 Agent 使用独立任务凭据；HOME 持久化，保留共享 Codex 配置。

**镜像：** `ghcr.io/creekxi2026/agent-runtime:latest`

## 功能

- **Agent 与集成：** Codex、Multica、Lark CLI、微信小程序 CLI、官方 Playwright CLI、Chromium，以及完整官方 Playwright / Lark skills。
- **开发工具：** Go、Node/npm/pnpm、Python/uv、C/C++ 编译链，以及 Git、gh、SSH、rsync 等常用命令。
- **按需安装：** 项目依赖遵守项目锁文件，不预装到镜像。
- **不包含：** Google Chrome、Firefox/WebKit、Docker/Podman、数据库服务端及业务代码。

## 快速开始

需要 Docker Engine、Compose v2，以及可访问的 Multica 和模型服务。默认限制为 2 CPU、4 GiB 内存。

在新建目录执行，不要覆盖已有配置：

```bash
mkdir -p agent-runtime/user01 agent-runtime/shared-codex
cd agent-runtime/user01
SOURCE=https://raw.githubusercontent.com/creekxi2026/agent-runtime/main
curl -fL "$SOURCE/compose.yaml" -o compose.yaml
curl -fL "$SOURCE/runtime.env.example" -o .env
curl -fL "$SOURCE/shared-codex/config.toml.example" -o ../shared-codex/config.toml
curl -fL "$SOURCE/shared-codex/auth.json.example" -o ../shared-codex/auth.json
chmod 600 .env
```

1. 编辑 `.env`：填写唯一实例名、Multica 服务地址及该用户的 `MULTICA_BOOTSTRAP_TOKEN`。使用 Multica Cloud 时地址可留空。
2. 编辑共享 `config.toml`、`auth.json`，填写模型配置和 API Key；空模板不能用于认证。
3. 确保宿主共享目录及文件允许 UID/GID `1000:1000` 读写。按需配置飞书应用。
4. 启动并检查日志：

```bash
docker compose config --quiet
docker compose up -d
docker compose logs --tail=100 runtime
```

新增用户使用独立目录和干净模板，不复制他人的数据或登录凭据。

## 配置

完整配置见 [runtime.env.example](runtime.env.example) 和 [compose.yaml](compose.yaml)。相对路径以 Compose 所在目录为基准。

| 配置 | 用途 / 默认值 |
| --- | --- |
| `COMPOSE_PROJECT_NAME` | 每个实例唯一 |
| `IMAGE_TAG` | `latest` |
| `DATA_DIR` | 私有 HOME，`./data` |
| `SHARED_CODEX_DIR` | 共享配置，`../shared-codex` |
| `CPU_LIMIT` / `MEMORY_LIMIT` | `2` / `4g` |
| `MULTICA_DAEMON_MAX_CONCURRENT_TASKS` | 可选；不设置则使用 Multica 原生默认值 |

并发默认不覆盖 Multica 的设置。需要指定整个 daemon 的并发上限时，在 `.env` 设置 `MULTICA_DAEMON_MAX_CONCURRENT_TASKS`；页面上的 Agent 并发限制仍独立生效。旧模板的 `MAX_CONCURRENT_TASKS` 不再使用：若原来是主动设置的限制，请将变量改为上述原生名称并保留原值；若只是复制了旧默认值 `1`，删除该行即可。替换 Compose 并修改 `.env` 后，等任务结束再执行 `docker compose up -d` 重建容器；无需重建镜像，单纯重启容器不会更新环境变量。

共享 Codex 目录仅存放 `config.toml`、API-key 模式的 `auth.json`，不共享整个 `.codex`。此模式不支持 ChatGPT OAuth，不要执行 `codex login/logout`。

官方技能安装在只读的 `/opt/agent-skills`，启动时链接到 `~/.agents/skills`，空 HOME 挂载不会遮掉预装技能。同名自定义目录或链接保留，不覆盖；官方链接在重建后读取新镜像内容。自定义技能放在 `~/.agents/skills/技能名/SKILL.md`。旧共享挂载取消后不会自动复制原文件，需保留原目录并自行复制所需技能，勿覆盖已有名字。

Multica 默认发现并继承用户级技能；提供方私有同名技能优先，Agent 可禁用运行时技能。`Copy from a runtime` 是快照，不会自动同步。

## 数据与工具

| 容器路径 | 内容 |
| --- | --- |
| `/home/agent` | 挂载 `DATA_DIR`，保存用户配置、登录状态和缓存 |
| `/home/agent/workspace` | 项目与任务工作区 |
| `/home/agent/.agents/skills` | 官方技能链接与用户自定义 skills |
| `/home/agent/.playwright/cli.config.json` | 原生 CLI 全局浏览器配置链接；已有自定义配置保留 |
| `/shared/codex` | 共享 Codex 配置 |
| `/home/agent/.local` | 用户后装工具 |
| `/opt`、系统命令目录 | 镜像预装工具 |

重建时复用同一 `DATA_DIR` 即可保留用户数据；容器其他可写层不持久化。使用 agent 身份安装工具：

```bash
docker compose exec --user 1000:1000 runtime bash
npm install -g 包名
uv tool install 包名
```

用户命令位于 `~/.local/bin`，优先于预装命令。Multica 使用 `MULTICA_CODEX_PATH` 指定 Codex，不能仅凭 shell PATH 判断其版本。

## 可选集成

- **飞书：** `.env` 可初始化应用配置；访问用户资源另需 OAuth：`docker compose run --rm runtime lark-cli auth login --domain docs --domain drive`。
- **本地浏览器：** 直接运行 `playwright-cli -s=任务名 open about:blank`，无需传浏览器路径；全局配置默认 `/usr/bin/chromium`、无头模式。原生空闲超时默认为 1 小时，`open --idle-timeout=毫秒` 可覆盖；不是任务完成即回收。每个并发任务使用独立命名 session，结束时只关闭自己的 session，不使用 `close-all` / `kill-all`。
- **浏览器状态：** 默认 session 临时隔离。需保留登录状态时，用 `playwright-cli -s=任务名 state-save ~/.local/share/浏览器状态.json` 保存，重建后用同一 session 的 `state-load` 恢复。该文件含敏感会话数据，不要提交或共享。
- **远程浏览器（可选）：** 设置 `PLAYWRIGHT_WS_ENDPOINT`，连接 Playwright 服务而非 MCP/CDP。两端主、次版本须兼容，镜像更新不会更新远端服务。
- **小程序：** `miniprogram-ci` 预览或上传需要项目 AppID、上传私钥及微信侧配置，镜像不包含这些凭据。

## 更新

```bash
docker compose pull
docker compose up -d
```

[发布工作流](.github/workflows/publish.yml)每天北京时间 **09:23** 检查基础镜像、预装工具、独立官方 Lark skills 提交/内容指纹和两个架构的 Chromium；有变化则构建、验证两个架构，通过后发布 `latest`。**不会自动更新运行中的容器。**

- Codex、Multica 独立分层并使用构建缓存；其他依赖不变且旧层可用时，只下载变化的层。
- Chromium、chromium-common、chromium-sandbox 从 Debian 签名 metadata 解析，锁定每架构版本及 deb SHA256；更新会改变构建输入，安装下载包后再校验。获取失败会让检查失败，不会当成无变化。无时间戳每日强制重建；其他 apt 包不独立跟踪，项目依赖不随镜像升级。
- 实际版本见镜像内 `/usr/share/agent-runtime/dependencies.json`，仓库清单仅作本地构建基线。
- 仅保留 `latest`，没有历史版本标签或自动回滚。更新会重建容器，请避开运行中的任务并先备份数据。

## 安全与限制

- 入口初始化后降为 UID/GID `1000:1000`；`docker exec` 须显式选择用户。
- 共享 Codex 配置默认可写：任一实例都能读取共享 Key、修改共享内容。官方 skills 不可写，自定义技能保存在私有 HOME。个人 Token 不应共享，`.env` 和数据目录需限制访问。
- Compose 放宽 seccomp/AppArmor，不挂 Docker socket，不启用 privileged。此环境不作为不可信多租户的强隔离边界。
- 自动重启不代表任务可用。模型鉴权、Multica 派发、飞书授权、小程序上传和实际浏览器连通性需在部署环境验收。

## 开发

在仓库根目录构建和测试：

```bash
python3 -m unittest discover -s tests -v
python3 scripts/resolve_tools.py --output runtime-deps.json
BASE_IMAGE=$(python3 -c 'import json; print(json.load(open("runtime-deps.json"))["base_image"])')
python3 scripts/split_dependencies.py --manifest runtime-deps.json --output .build-inputs
docker build --build-arg "BASE_IMAGE=$BASE_IMAGE" -t agent-runtime:test .
IMAGE=agent-runtime:test sh tests/smoke.sh

# 可选：独立远程浏览器兼容测试
IMAGE=agent-runtime:test bash tests/remote-browser.sh
```

[验证工作流](.github/workflows/verify.yml)检查双架构工具、持久化、共享配置、官方技能发现、本地浏览器并发回收和 CLI 层复用；测试不使用真实业务凭据。
