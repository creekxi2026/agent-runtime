# Agent Runtime

基于官方 Debian stable-slim 自建的多用户开发工具镜像，不再继承第三方全量开发环境。预装 Multica、Codex、Lark CLI、微信小程序 miniprogram-ci、Playwright 客户端、Node/npm/pnpm、Go、Python/venv/uv、Git/GitHub CLI、SSH 客户端、rsync、编译工具链及系统 bubblewrap。启动时不安装软件。

不预装浏览器、Docker/Podman、数据库服务端或浏览器专用图形库。TypeScript、Jest、Biome、MorJS 等项目库按项目锁文件安装，不另装全局副本。

`bubblewrap` 在构建时通过系统软件源安装，两种架构的发布检查均确认其路径和版本。预装该工具不等于解除宿主机或容器的 namespace、seccomp、AppArmor 限制；仍需使用与 Linux 沙箱兼容的运行配置。

## 统一 HOME 与持久化

容器内账号登记的家目录、`HOME` 和默认工作目录统一为 `/home/agent`，任务工作区为 `/home/agent/workspace`。Codex、Multica、Lark、XDG 配置和缓存均位于该 HOME。预装工具直接安装在 `/opt` 或系统命令目录，不依赖 HOME，也不再做旧上游目录迁移或整个 rootfs 平铺。每个直接管理的工具只保留一个版本；npm 间接依赖遵守各工具的兼容约束。

Compose 将 `${DATA_DIR:-./data}` 绑定到 `/home/agent`。**升级时保持原 DATA_DIR 和宿主机目录，不会自动搬动 NAS 文件。** `/data` 仅保留为指向 `/home/agent` 的旧路径兼容别名，用于已有会话中的绝对路径，不是第二份存储。新配置只使用规范 HOME 路径。

用户后装工具默认持久化：

- `npm install -g 包名`：prefix 为 `~/.local`，命令在 `~/.local/bin`。
- `uv tool install 包名`：环境在 `~/.local/share/uv/tools`，命令在 `~/.local/bin`。
- Go：`GOPATH=~/.local/share/go`、`GOBIN=~/.local/bin`、`GOTMPDIR=~/.cache/go-tmp`（避免依赖 `/tmp` 可执行）；pnpm 用户目录在 `~/.local/share/pnpm`。
- 使用 UID/GID `1000:1000` 的 agent 身份安装。`apt` 等系统包仍通过镜像构建维护；不要向共享配置目录或预装工具目录安装用户软件。
- 持久化以重建后复用同一宿主目录为前提；不保证跨 CPU 架构或系统版本复用已有原生扩展。

旧版升级须**同时替换 Compose 并重建容器**，不能沿用包含 `/data` 初始化逻辑的旧自定义入口。新镜像已内置一次性目录初始化与降权，无需在 Compose 重复嵌入脚本。已有 `.env` 不要覆盖；默认 `./data` 与原可见数据版一致。原先使用命名卷的部署应保留原卷的类型和 source，仅调整挂载目标与启动配置，不能直接换成空的 bind 目录。

## 命令 PATH 与小程序 CLI

Node 放在 `/opt/node`，Go 放在 `/opt/go`，npm CLI 放在 `/opt/agent-tools`，独立 CLI 放在 `/usr/local/bin`，均由 root 所有。镜像通过 `ENV PATH` 与统一 `/etc/profile.d/agent-tools.sh`（同时作为 `BASH_ENV`）保持命令路径，不再自动加载上游 nvm.sh。用户的 `~/.local/bin` 优先于预装工具；挂载空 HOME 不会遮住工具链。发布检查覆盖普通进程以及 `bash -lc`、`bash -ic`、`bash -ilc`。

`miniprogram-ci` 在每次构建解析时选定 npm latest 正式版本并固定，命令为 `miniprogram-ci`，不是 Mac 微信开发者工具的 GUI/CLI。使用 `miniprogram-ci --help` 查看参数；预览、上传仍需项目 AppID、上传私钥及微信侧相应配置。镜像不含这些凭据，发布检查只验证离线 CLI 可用，不执行真实上传。

## 使用

管理员先按下文准备共享 Codex 目录；每个用户只需要 `compose.yaml` 和 `.env`：

```bash
curl -fLO https://raw.githubusercontent.com/creekxi2026/agent-runtime/main/compose.yaml
curl -fL https://raw.githubusercontent.com/creekxi2026/agent-runtime/main/runtime.env.example -o .env
chmod 600 .env
# 编辑 .env：唯一实例名、镜像版本、两个共享目录、必要凭据。
docker compose up -d
```

不要覆盖已有的 `.env`；新增用户复制干净模板，不复制其他用户的数据卷或凭据。不同主机可复用同一镜像；同一 Docker 主机上实例名必须唯一。

## 用户独立登录（Multica／飞书）

```bash
docker compose run --rm runtime multica login --token
docker compose run --rm runtime lark-cli config init
# 需要访问飞书用户个人资源时，另做用户 OAuth 授权：
docker compose run --rm runtime lark-cli auth login --domain docs --domain drive
docker compose up -d
```

`.env` 是明文配置，不是加密凭据库。环境变量方式初始化后可清空 Token/Secret，再 `up -d --force-recreate`；保存的认证状态仍在独立数据卷中。飞书应用仅在配置文件不存在时初始化，重启不会覆盖已有用户授权；更换飞书应用请显式使用 CLI 配置命令。Multica Agent 的工具环境变量和 MCP 配置可在 Multica 中管理。应用身份和用户 OAuth 是不同权限。

## 共享 Codex 默认配置与 API Key

管理员只维护专用目录中的两个文件：`config.toml` 和 API-key 模式的 `auth.json`。从 [config.toml.example](shared-codex/config.toml.example) 与 [auth.json.example](shared-codex/auth.json.example) 建立这两个文件，再由管理员在宿主机安全填写 API Key；模板 Key 为空，不能用于认证。不要从个人账户复制完整 `.codex`，也不要把个人 `.codex` 作为 `SHARED_CODEX_DIR`。

每份 `.env` 设置相同宿主机目录 `SHARED_CODEX_DIR=../shared-codex`，或使用同一个绝对路径。该目录与下文 `shared-skills/` 并列；相对路径以各自 `compose.yaml` 所在目录为基准。目录需事先存在，两个文件及目录访问权限须允许容器 UID/GID 1000:1000 读写；建议通过所有者／组／ACL 控制，勿为方便给真实凭据开放全员读写。Compose 不自动创建该目录；文件缺失或不可读时，入口明确报路径并失败。

- 整个**专用目录**可写挂到 `/shared/codex`，容器设置 `CODEX_SHARED_DIR=/shared/codex`。仅 `/home/agent/.codex/config.toml` 和 `auth.json` 链接到共享文件；其余 `.codex` 会话、缓存及 `multica-sessions` 保留在各用户私有卷内。
- 首次切换如已有私有文件，将其保留为同目录 `config.toml.before-shared`／`auth.json.before-shared`，不删除、不覆盖；需要迁移但备份名已存在则直接失败，由管理员确认处理后重试。正确链接重复启动不改动。旧凭据备份仍需按私有凭据保护。
- 挂目录而非单文件：管理员编辑器原子替换文件后，链接仍能读取更新。新任务读取最新默认配置；Multica 为每任务复制配置并链接认证，已运行任务不承诺热更新。不同宿主机需自行同步。
- 只支持 API Key 文件认证，不适用需要刷新写回的 ChatGPT OAuth。不要在共享模式执行 `codex login`／`logout`；Compose 不再传入每用户 `CODEX_BOOTSTRAP_API_KEY`，入口在共享模式也不会执行旧 bootstrap 写回。脱离此 Compose、未设置 `CODEX_SHARED_DIR` 时仍保留原 API Key bootstrap 兼容。
- 所有人使用同一个管理员维护的 Key，不按用户分配额度。可写共享意味着任一实例都能修改全体用户共用的配置、Key 和 skills，**不能对容器用户隐藏 Key**；能运行容器内代码的人就能读取它。Multica／飞书的用户 Token 和 OAuth 不在此共享范围内。
- 默认模板只设置 `cli_auth_credentials_store = "file"`，不猜测模型、服务地址或权限策略。管理员可维护默认参数，但 Multica 可能覆盖 sandbox、memory、multiagent 等字段；共享配置不是强制安全策略。

## 共享 skills

同一 Docker 宿主机上的用户将 `SHARED_SKILLS_DIR` 指向同一个管理员维护的目录，所有容器可写挂载到 `/home/agent/.agents/skills`。默认布局：

```text
部署根目录/
├── shared-skills/
│   └── 技能名/SKILL.md
├── user01/compose.yaml + .env
└── user02/compose.yaml + .env
```

每份 `.env` 保留独立的 `COMPOSE_PROJECT_NAME`，共享目录填 `../shared-skills`，也可填宿主机绝对路径。目录不存在时 Compose 会创建空目录；管理员在宿主机放入技能，确保容器 UID/GID 1000:1000 可读写。更新技能内容只改这一个目录，不需要逐用户更新或重建镜像。不同宿主机需自行同步这份目录，不会跨主机自动共享。

Multica 的 Agent → Skills 可请求在线 runtime 扫描本地技能并控制是否禁用；本地技能默认继承，不是勾选前不可见。新任务读取最新技能，已运行任务不保证热更新。不要再在各用户 `.codex/skills` 保留同名旧副本，它们会优先于共享目录。共享目录不放 Token、私有数据或只应对部分用户开放的技能；技能中的脚本仍应把输出和缓存写到用户自己的工作目录，避免污染共享技能。若需要只读策略，可把两处共享挂载的 read_only 改为 true；检查同时覆盖两种模式。

**不要为保持同步而点“Copy from a runtime”**：那会生成中心库快照，不自动跟随宿主文件变化。同一个 Multica workspace 也可以直接维护其内置 Skills 库并分配给多个 Agent；这种方式不依赖宿主目录。两种来源避免同名冲突。

除了上述专用 Codex 默认配置与 API Key，只共享 skills；不共享 `.codex`／`.agents` 整目录、其他用户凭据、会话和任务工作区。技能开关不是文件访问安全边界。

## 浏览器（可选）

Mac 上另行部署每用户独立的 Playwright 服务，将其 WebSocket 地址填入 `PLAYWRIGHT_WS_ENDPOINT`：

预装 `playwright` JavaScript API 和 `playwright-cli`；任务通过客户端配置或 `chromium.connect(process.env.PLAYWRIGHT_WS_ENDPOINT)` 连接。该地址不是 MCP 或 CDP 地址。客户端与远端 Playwright 的主、次协议版本须兼容；自动升级客户端不意味着可以自动升级 Mac 服务，版本不匹配时应协调两端。不会在启动时下载浏览器。

## 更新

`.env` 的 `IMAGE_TAG` 使用 `latest`。镜像仓库只保留这一个公开标签，不再发布版本、构建或架构标签；历史标签会自动清理，不再作为固定版本或回滚入口。旧模板升级时，保留原文件和用户凭据，按需补齐以下设置再运行更新命令：

1. 将完整 `IMAGE` 字段改为 `IMAGE_TAG=latest`；原来固定到历史标签的用户也需改为 `latest`。
2. 追加 `SHARED_SKILLS_DIR=../shared-skills`，或填入所有用户共用的宿主机目录。该字段是必填项，不会静默选择未知的共享目录。
3. 升级到 0.3.0 时追加 `SHARED_CODEX_DIR=../shared-codex`，先由管理员准备上述两个共享文件。保留已有 `.env` 和其中其他凭据，不用新模板覆盖；旧 `CODEX_BOOTSTRAP_API_KEY` 不再被 Compose 传入，确认迁移后可删除这个旧字段。数据卷内原 Codex 配置与认证会按上述规则保留备份，不能用空模板覆盖现有凭据。

然后执行：

```bash
docker compose pull
docker compose up -d
```

每天北京时间 **09:23**，以及推送 main／手动触发时，直接检查官方基础镜像和各工具最新稳定发布，不再跟随 sapk 镜像。一次解析生成 `runtime-deps.json`，固定基础镜像 digest、各工具版本和下载校验值；两架构下载同一个解析产物并验证 SHA256 后再构建。已发布的基础 digest、依赖指纹和源码提交都未变化则跳过构建。系统软件包使用构建当时 Debian 稳定仓库的候选版本；没有单独承诺在基础镜像/工具/源码均不变时重建以追踪 apt 仓库变化。

amd64／arm64 在原生 runner 上分别构建并通过真实容器测试后，只按 digest 上传；确认上传的镜像配置 digest 与受测镜像相同，才合并更新 `latest`。测试失败不更新 `latest`。构建编号仅写镜像元数据，不产生额外 tag。镜像 `/usr/share/agent-runtime/dependencies.json` 保存该次实际解析的工具版本；仓库同名文件是本地构建基线，日更解析不自动改写 Git 主分支。

发布后自动清理历史版本，包括旧 `0.1.0`、构建／架构标签及无引用 manifest；保留 `latest` 和它递归依赖的全部 manifest。清理前匿名读取最新索引、确认两个架构，并完整校验所依赖的 manifest 和 blob；删除前再次检查 `latest` 未改变。依赖缺失、保留项仍有其他标签或清理权限不足时，工作流会明确失败，不绕过检查或删除受保护依赖。无变化的定时／手动运行也会重试清理。脚本 `scripts/cleanup_ghcr.py` 默认仅预览，Actions 使用本仓库的 `GITHUB_TOKEN` 和显式 `--apply` 执行，仅允许操作 `creekxi2026/agent-runtime` 包；该包须向本仓库授予 admin 权限，`packages: write` 本身不能绕过包的权限设置。

发布与清理必须统一通过此工作流的同一个并发锁；维护期间不要在外部手动推送或删除该包。GitHub 不提供原子“检查 latest 后删除”接口，脚本不能保证与绕过工作流的并发写入安全共存。`latest` 切换后的网络核验或清理失败会保留已切换的新镜像并明确报错，不会自动回滚；修复后重跑工作流完成验收和清理。

只自动更新镜像仓库，**不自动升级运行中的容器**。公开仓库使用标准 GitHub-hosted runners，GHCR 存储／流量按 GitHub 当前政策免费；不使用付费大型 runner。GitHub 定时任务可能延迟，公开仓库连续 60 天无活动会停用定时任务，需要在 Actions 页面重新启用。

## 运行边界

- 单服务启动时短暂以 root 初始化 HOME 目录所有权，随后主进程与 tini 降为 UID/GID 1000，清空 capabilities。NAS 交互终端请显式选择 agent 用户；Docker exec 不会自动经过入口降权。
- 预装工具在 `/opt`，用户 HOME 为独立 bind 目录。允许写容器层，但预装目录归 root 所有；用户持久工具优先，并可能覆盖同名命令，升级时应考虑该优先级。
- 不安装 Docker/Podman，也不运行旧上游初始化，不挂宿主 Docker socket，不提供 Docker-in-Docker。
- 未配置 Multica 认证时启动失败；Compose 使用 `unless-stopped`，应查看日志并修复配置，不能把容器反复重启视为在线可用。
- 为兼容内层 Linux 沙箱，Compose 对该容器使用 seccomp/AppArmor unconfined，保留 no-new-privileges，不启用 privileged 或 SYS_ADMIN。这减少了两层 Docker 防护，不等于完整安全隔离。
- 默认 bind 数据在 `down` 后保留；删除宿主目录才会删除这些文件。若自行使用命名卷，`down -v` 会删除该卷。
- 文件卷分离不替代 Multica 服务端鉴权或远程浏览器权限。不要给多个用户共享管理员 Token；不将此模板宣称为完整恶意多租户安全边界。
- 基础系统和工具从官方源获取；第三方组件遵循各自许可证。

## 更新下载与分层

系统软件包与低频开发工具、Codex、Multica 使用独立的构建输入。Codex 和 Multica 从独立构建阶段通过 COPY --link 进入各自镜像层，完整版本清单只在末尾写入小型元数据层。单独升级其中一个 CLI 不应改变系统、SDK 和另一个 CLI 的压缩层摘要。

CI 使用按架构隔离的 GitHub Actions BuildKit v2 缓存（mode=max），跨运行保存核心工具及中间阶段。缓存不在 GHCR 增加标签，镜像仍只保留 latest。Docker 在宿主保留旧层时只拉取新增 blob，容器仍需重建。缓存可能因配额或长期未使用被驱逐；基础系统/核心工具更新、缓存丢失或 NAS 清理旧层时，下载量仍可能增加。本次从旧的大层迁移到新分层也会有一次较大的下载，不能把它当作后续单 CLI 更新的下载量。

验证分支会用官方旧版 Codex/Multica 分别与当前版本作对照，创建全新的 BuildKit daemon 导入持久缓存，比较真实 OCI 压缩层摘要，要求只有目标 CLI 和版本元数据变化。对照旧版仅在临时 CI 构建中存在，不发布、不保留额外镜像标签。

## 维护者本地构建

```bash
python3 scripts/resolve_tools.py --output runtime-deps.json
BASE_IMAGE=$(python3 -c 'import json; print(json.load(open("runtime-deps.json"))["base_image"])')
python3 scripts/split_dependencies.py --manifest runtime-deps.json --output .build-inputs
docker build --build-arg "BASE_IMAGE=$BASE_IMAGE" -t agent-runtime:test .
IMAGE=agent-runtime:test sh tests/smoke.sh
IMAGE=agent-runtime:test bash tests/remote-browser.sh
```

单元／静态验证无需 Docker 引擎：

```bash
python3 -m unittest discover -s tests -v
sh -n entrypoint.sh
sh -n tests/smoke.sh
docker compose --env-file runtime.env.example config --quiet
```

容器 smoke 使用临时共享目录、明显无效的 API Key 和禁用网络的两个用户容器；通过真实 Codex `config/read`、`account/read`（不刷新 Token）及 `skills/list` 检查文件解析、账户类型、共享更新和只读挂载，不发起模型请求。无效 Key 被识别为 API-key 模式不等于认证成功。另用一次性浏览器服务容器验证精简客户端能远程控制真实页面并截图；测试浏览器不进入发布镜像，不使用用户 Mac、URL 或认证。真实 API 认证、Multica 派发、飞书 OAuth 和用户 NAS→Mac 连通性仍需独立验收。

验证分支 `verify/**` 仅运行双架构构建与检查，不推送镜像、不更新 latest；主分支仍沿现有发布流程运行。
