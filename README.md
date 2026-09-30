# Agent Runtime

基于 [sapk/multica-docker-env](https://github.com/sapk/multica-docker-env) 的 Codex 镜像，补充 `lark-cli`、面向 Agent 的 `playwright-cli` 和系统版 `bubblewrap`（`/usr/bin/bwrap`）。启动时不安装软件。

`bubblewrap` 在构建时通过系统软件源安装，两种架构的发布检查均确认其路径和版本。预装该工具不等于解除宿主机或容器的 namespace、seccomp、AppArmor 限制；仍需使用与 Linux 沙箱兼容的运行配置。

## 使用

管理员先按下文准备共享 Codex 目录；每个用户只需要 `compose.yaml` 和 `runtime.env`：

```bash
curl -fLO https://raw.githubusercontent.com/creekxi2026/agent-runtime/main/compose.yaml
curl -fL https://raw.githubusercontent.com/creekxi2026/agent-runtime/main/runtime.env.example -o runtime.env
chmod 600 runtime.env
# 编辑 runtime.env：唯一实例名、镜像版本、两个共享目录、必要凭据。
docker compose --env-file runtime.env up -d
```

不要覆盖已有的 `runtime.env`；新增用户复制干净模板，不复制其他用户的数据卷或凭据。不同主机可复用同一镜像；同一 Docker 主机上实例名必须唯一。

## 用户独立登录（Multica／飞书）

```bash
docker compose --env-file runtime.env run --rm runtime multica login --token
docker compose --env-file runtime.env run --rm runtime lark-cli config init
# 需要访问飞书用户个人资源时，另做用户 OAuth 授权：
docker compose --env-file runtime.env run --rm runtime lark-cli auth login --domain docs --domain drive
docker compose --env-file runtime.env up -d
```

`runtime.env` 是明文配置，不是加密凭据库。环境变量方式初始化后可清空 Token/Secret，再 `up -d --force-recreate`；保存的认证状态仍在独立数据卷中。飞书应用仅在配置文件不存在时初始化，重启不会覆盖已有用户授权；更换飞书应用请显式使用 CLI 配置命令。Multica Agent 的工具环境变量和 MCP 配置可在 Multica 中管理。应用身份和用户 OAuth 是不同权限。

## 共享 Codex 默认配置与 API Key

管理员只维护专用目录中的两个文件：`config.toml` 和 API-key 模式的 `auth.json`。从 [config.toml.example](shared-codex/config.toml.example) 与 [auth.json.example](shared-codex/auth.json.example) 建立这两个文件，再由管理员在宿主机安全填写 API Key；模板 Key 为空，不能用于认证。不要从个人账户复制完整 `.codex`，也不要把个人 `.codex` 作为 `SHARED_CODEX_DIR`。

每份 `runtime.env` 设置相同宿主机目录 `SHARED_CODEX_DIR=../shared-codex`，或使用同一个绝对路径。该目录与下文 `shared-skills/` 并列；相对路径以各自 `compose.yaml` 所在目录为基准。目录需事先存在，两个文件及目录访问权限须允许容器 UID 1000 读取；建议通过所有者／组／ACL 控制，勿为方便给真实凭据开放全员读写。Compose 不自动创建该目录；文件缺失或不可读时，入口明确报路径并失败。

- 整个**专用目录**只读挂到 `/shared/codex`，容器设置 `CODEX_SHARED_DIR=/shared/codex`。仅 `/data/.codex/config.toml` 和 `auth.json` 链接到共享文件；其余 `.codex` 会话、缓存及 `multica-sessions` 保留在各用户私有卷内。
- 首次切换如已有私有文件，将其保留为同目录 `config.toml.before-shared`／`auth.json.before-shared`，不删除、不覆盖；需要迁移但备份名已存在则直接失败，由管理员确认处理后重试。正确链接重复启动不改动。旧凭据备份仍需按私有凭据保护。
- 挂目录而非单文件：管理员编辑器原子替换文件后，链接仍能读取更新。新任务读取最新默认配置；Multica 为每任务复制配置并链接认证，已运行任务不承诺热更新。不同宿主机需自行同步。
- 只支持 API Key 文件认证，不适用需要刷新写回的 ChatGPT OAuth。不要在共享模式执行 `codex login`／`logout`；Compose 不再传入每用户 `CODEX_BOOTSTRAP_API_KEY`，入口在共享模式也不会执行旧 bootstrap 写回。脱离此 Compose、未设置 `CODEX_SHARED_DIR` 时仍保留原 API Key bootstrap 兼容。
- 所有人使用同一个管理员维护的 Key，不按用户分配额度。只读挂载只防止容器修改宿主文件，**不能对容器用户隐藏 Key**；能运行容器内代码的人就能读取它。Multica／飞书的用户 Token 和 OAuth 不在此共享范围内。
- 默认模板只设置 `cli_auth_credentials_store = "file"`，不猜测模型、服务地址或权限策略。管理员可维护默认参数，但 Multica 可能覆盖 sandbox、memory、multiagent 等字段；共享配置不是强制安全策略。

## 共享 skills

同一 Docker 宿主机上的用户将 `SHARED_SKILLS_DIR` 指向同一个管理员维护的目录，所有容器只读挂载到 `/data/.agents/skills`。默认布局：

```text
部署根目录/
├── shared-skills/
│   └── 技能名/SKILL.md
├── user01/compose.yaml + runtime.env
└── user02/compose.yaml + runtime.env
```

每份 `runtime.env` 保留独立的 `COMPOSE_PROJECT_NAME`，共享目录填 `../shared-skills`，也可填宿主机绝对路径。目录不存在时 Compose 会创建空目录；管理员在宿主机放入技能，确保容器 UID 1000 可读取。更新技能内容只改这一个目录，不需要逐用户更新或重建镜像。不同宿主机需自行同步这份目录，不会跨主机自动共享。

Multica 的 Agent → Skills 可请求在线 runtime 扫描本地技能并控制是否禁用；本地技能默认继承，不是勾选前不可见。新任务读取最新技能，已运行任务不保证热更新。不要再在各用户 `.codex/skills` 保留同名旧副本，它们会优先于共享目录。共享目录不放 Token、私有数据或只应对部分用户开放的技能；技能中的脚本应把输出和缓存写到用户自己的工作目录，不能写回只读技能目录。

**不要为保持同步而点“Copy from a runtime”**：那会生成中心库快照，不自动跟随宿主文件变化。同一个 Multica workspace 也可以直接维护其内置 Skills 库并分配给多个 Agent；这种方式不依赖宿主目录。两种来源避免同名冲突。

除了上述专用 Codex 默认配置与 API Key，只共享 skills；不共享 `.codex`／`.agents` 整目录、其他用户凭据、会话和任务工作区。技能开关不是文件访问安全边界。

## 浏览器（可选）

Mac 上另行部署每用户独立的 Playwright 服务，将其 WebSocket 地址填入 `PLAYWRIGHT_WS_ENDPOINT`：

```bash
docker compose --env-file runtime.env exec runtime sh -c 'playwright-cli attach --endpoint="$PLAYWRIGHT_WS_ENDPOINT"'
```

这不是 MCP 或 CDP 地址。客户端与远端的 Playwright 协议版本必须兼容。Agent 任务也可以直接调用 `playwright-cli attach`。上游自带的 `@playwright/test` 与 `@playwright/cli` 是两个不同工具；后者不重复下载浏览器，主要用于连接远程浏览器。

## 更新

`runtime.env` 的 `IMAGE_TAG` 使用 `latest`。镜像仓库只保留这一个公开标签，不再发布版本、构建或架构标签；历史标签会自动清理，不再作为固定版本或回滚入口。旧模板升级时，保留原文件和用户凭据，按需补齐以下设置再运行更新命令：

1. 将完整 `IMAGE` 字段改为 `IMAGE_TAG=latest`；原来固定到历史标签的用户也需改为 `latest`。
2. 追加 `SHARED_SKILLS_DIR=../shared-skills`，或填入所有用户共用的宿主机目录。该字段是必填项，不会静默选择未知的共享目录。
3. 升级到 0.3.0 时追加 `SHARED_CODEX_DIR=../shared-codex`，先由管理员准备上述两个共享文件。保留已有 `runtime.env` 和其中其他凭据，不用新模板覆盖；旧 `CODEX_BOOTSTRAP_API_KEY` 不再被 Compose 传入，确认迁移后可删除这个旧字段。数据卷内原 Codex 配置与认证会按上述规则保留备份，不能用空模板覆盖现有凭据。

然后执行：

```bash
docker compose --env-file runtime.env pull
docker compose --env-file runtime.env up -d
```

每天北京时间 **09:23**，以及推送 main／手动触发时，检查上游 `ghcr.io/sapk/multica-agent-codex:latest`。上游 digest 和本仓库提交均未变化则跳过构建；有变化时先解析为固定 digest，让两个架构使用同一基础镜像。amd64／arm64 在原生 runner 上分别构建并通过真实容器测试后，只按 digest 上传；确认上传的镜像配置 digest 与受测镜像相同，才合并更新 `latest`。构建、测试或该一致性检查失败不会更新 `latest`。构建编号仅保留在镜像标签元数据中，不产生公开 tag。补充的 lark-cli／playwright-cli 版本仍在 Dockerfile 中固定，由维护者更新。

发布后自动清理历史版本，包括旧 `0.1.0`、构建／架构标签及无引用 manifest；保留 `latest` 和它递归依赖的全部 manifest。清理前匿名读取最新索引、确认两个架构，并完整校验所依赖的 manifest 和 blob；删除前再次检查 `latest` 未改变。依赖缺失、保留项仍有其他标签或清理权限不足时，工作流会明确失败，不绕过检查或删除受保护依赖。无变化的定时／手动运行也会重试清理。脚本 `scripts/cleanup_ghcr.py` 默认仅预览，Actions 使用本仓库的 `GITHUB_TOKEN` 和显式 `--apply` 执行，仅允许操作 `creekxi2026/agent-runtime` 包；该包须向本仓库授予 admin 权限，`packages: write` 本身不能绕过包的权限设置。

发布与清理必须统一通过此工作流的同一个并发锁；维护期间不要在外部手动推送或删除该包。GitHub 不提供原子“检查 latest 后删除”接口，脚本不能保证与绕过工作流的并发写入安全共存。`latest` 切换后的网络核验或清理失败会保留已切换的新镜像并明确报错，不会自动回滚；修复后重跑工作流完成验收和清理。

只自动更新镜像仓库，**不自动升级运行中的容器**。公开仓库使用标准 GitHub-hosted runners，GHCR 存储／流量按 GitHub 当前政策免费；不使用付费大型 runner。GitHub 定时任务可能延迟，公开仓库连续 60 天无活动会停用定时任务，需要在 Actions 页面重新启用。

## 运行边界

- 非 root、只读镜像根目录；每个实例的数据位于自己的 `/data` 卷。
- 工具留在镜像中，数据卷不覆盖上游的 `/home/agent` 工具目录，升级镜像不会被旧卷内的软件遮住。
- 不运行上游默认 Podman/RTK 初始化，不挂宿主 Docker socket，不提供 Docker-in-Docker。
- 未配置 Multica 认证时启动直接失败；查看 Compose 日志处理，不自动循环重试。
- `down` 保留数据；`down -v` 删除当前用户数据。
- 文件卷分离不替代 Multica 服务端鉴权或远程浏览器权限。不要给多个用户共享管理员 Token；不将此模板宣称为完整恶意多租户安全边界。
- 镜像来自社区上游，包含额外工具和浏览器，体积及依赖风险随之继承。第三方组件遵循各自许可证。

## 维护者本地构建

```bash
docker build -t agent-runtime:test .
IMAGE=agent-runtime:test sh tests/smoke.sh
```

单元／静态验证无需 Docker 引擎：

```bash
python3 -m unittest discover -s tests -v
sh -n entrypoint.sh
sh -n tests/smoke.sh
docker compose --env-file runtime.env.example config --quiet
```

容器 smoke 使用临时共享目录、明显无效的 API Key 和禁用网络的两个用户容器；通过真实 Codex `config/read`、`account/read`（不刷新 Token）及 `skills/list` 检查文件解析、账户类型、共享更新和只读挂载，不发起模型请求。无效 Key 被识别为 API-key 模式不等于认证成功。真实 API 认证、Multica 派发、飞书 OAuth 和远程浏览器连接需独立验收。
