# Agent Runtime

[English](README.md) | [简体中文](README.zh-CN.md)

基于 Docker 的 Codex 和 Multica 开发运行环境，每个实例都有独立、持久化的私有 HOME。同一可信用户的多个 Agent 可以在一个容器中协作。

- **开箱即用的工具：** Codex、Multica、Lark CLI、miniprogram-ci、Playwright CLI 和 Chromium，以及官方 Playwright 和 Lark skills。
- **开发工具链：** Go、Node.js/npm/pnpm、Python/uv、C/C++、Git、gh、SSH 和 rsync。
- **持久化用户数据：** 项目、凭据、会话、用户自行安装的工具和自定义 skills 都保存在 HOME 中，重建容器后仍然保留。
- **可选共享：** 共享下载缓存和只读 Codex API-key 配置，不共享 HOME。

镜像：`ghcr.io/creekxi2026/agent-runtime:latest`，支持 `linux/amd64` 和 `linux/arm64`。项目依赖需按各自的锁文件另行安装。镜像不包含应用代码、Docker/Podman 或数据库服务。

[快速开始](#快速开始) · [存储](#存储) · [配置](#配置) · [使用](#使用) · [更新](#更新) · [开发](#开发)

## 快速开始

需要 Docker Engine、Docker Compose v2，以及可访问的 Multica 和模型服务。

### 1. 准备实例

新实例只需下载 Compose 文件和环境变量文件：

```bash
mkdir -p agent-runtime/user01
cd agent-runtime/user01
SOURCE=https://raw.githubusercontent.com/creekxi2026/agent-runtime/main
curl -fL "$SOURCE/compose.yaml" -o compose.yaml
curl -fL "$SOURCE/runtime.env.example" -o .env
chmod 600 .env
```

编辑 `.env`，为 `COMPOSE_PROJECT_NAME` 设置**唯一实例名**。不要把凭据提交到 Git 或发到聊天中；在 `.env` 中用单引号包围真实密钥值。不要覆盖已有的凭据环境文件。

### 2. 先选存储，再登录

默认使用 Docker 管理的私有 HOME 卷，不挂载任何共享目录。如果适合你的实例，可继续下一步。若要使用宿主机目录作为 HOME、共享缓存或共享 Codex 凭据，**请先完成[存储](#存储)配置**，包括下载 override 文件和设置 `COMPOSE_FILE`。登录命令会初始化所选 HOME；之后更换存储不会迁移这些文件。

### 3. 认证

**Multica：** 默认连接 Multica Cloud。设置了 `MULTICA_BOOTSTRAP_TOKEN` 时，跳过 Multica 交互式登录；守护进程启动时会应用该 token 和配置的 URL。

没有 bootstrap token 时，自托管实例需要在**首次交互式登录前**设置服务地址。将以下示例 URL 替换为 `.env` 中的对应值；未配置应用地址时，省略 `app_url` 命令。`.env` 中的 URL 只会在启动守护进程时自动应用，交互式登录命令不会应用它们。

```bash
docker compose run --rm runtime multica config set server_url 'https://multica-api.example.com'
docker compose run --rm runtime multica config set app_url 'https://multica.example.com'
```

没有 bootstrap token 时，登录 Multica：

```bash
docker compose run --rm runtime multica login
```

**Codex：** 仅在使用私有 Codex 凭据时登录。**共享 Codex 模式下跳过此命令**，不要对共享凭据执行 `codex login` 或 `codex logout`。使用私有 API key 认证时，`codex login --with-api-key` 从标准输入读取密钥。

```bash
docker compose run --rm runtime codex login
```

### 4. 启动运行环境

```bash
docker compose up -d
docker compose logs --tail=100 runtime
```

守护进程必须有 Multica 认证，否则拒绝启动。还需为计划执行的任务配置模型访问；仅启动容器并不能验证模型认证是否有效。

## 存储

相对 bind 路径以第一个 Compose 文件所在目录为基准。默认建议使用命名卷；需要直接访问宿主机文件时，可选择 bind 挂载。

| 数据 | 来源 | 容器路径 | 访问权限 |
| --- | --- | --- | --- |
| 私有 HOME | 命名卷 `<COMPOSE_PROJECT_NAME>-home`，或 `HOME_VOLUME`；也可通过 `HOME_DIR` 使用 bind 挂载 | `/home/agent` | 读写 |
| 下载缓存 | 默认保存在私有 HOME；可选卷 `agent-runtime-download-cache`，或通过 `SHARED_CACHE_DIR` 使用 bind 挂载 | 启用后为 `/shared/caches` | 读写 |
| 共享 Codex 配置 | 可选，通过 `SHARED_CODEX_DIR` 指定专用目录 | `/shared/codex` | 只读 |

HOME 保存配置、凭据、会话、`workspace`、用户自行安装的工具和自定义 skills。**独立实例之间绝不能共用 HOME**，设置 `HOME_VOLUME` 时也一样。镜像管理的工具位于 `/opt`；HOME 卷使用 `nocopy`，不会把镜像内 HOME 的内容复制到持久化存储。

### 选择 override 文件

从快速开始中使用的同一 `SOURCE` URL 下载所需 override 文件，放在 `compose.yaml` 旁边。例如：

```bash
curl -fL "$SOURCE/compose.bind-home.yaml" -o compose.bind-home.yaml
```

然后在 `.env` 中设置对应值：

| 存储方案 | 所需 override 文件 | 环境变量设置 |
| --- | --- | --- |
| 默认私有 HOME 卷 | 无 | 不设置 `COMPOSE_FILE` |
| 私有 HOME bind 挂载 | [compose.bind-home.yaml](compose.bind-home.yaml) | `HOME_DIR=./home`; `COMPOSE_FILE=compose.yaml:compose.bind-home.yaml` |
| 共享缓存卷 | [compose.shared-cache.yaml](compose.shared-cache.yaml) | `COMPOSE_FILE=compose.yaml:compose.shared-cache.yaml`; 可选设置 `SHARED_CACHE_VOLUME` |
| 共享缓存 bind 挂载 | [compose.shared-cache.yaml](compose.shared-cache.yaml)，然后是 [compose.shared-cache.bind.yaml](compose.shared-cache.bind.yaml) | `SHARED_CACHE_DIR=../shared/caches`; `COMPOSE_FILE=compose.yaml:compose.shared-cache.yaml:compose.shared-cache.bind.yaml` |
| 只读共享 Codex | [compose.shared-codex.yaml](compose.shared-codex.yaml) | `SHARED_CODEX_DIR=../shared-codex`; `COMPOSE_FILE=compose.yaml:compose.shared-codex.yaml` |

**文件顺序很重要：** `compose.yaml` 必须排在最前，每个 bind override 必须放在对应的命名卷配置之后。Windows 上，`COMPOSE_FILE` 用 `;` 分隔，而不是 `:`。设置后，登录、启动、进入 shell 和更新都使用普通 `docker compose` 命令；不要另传一份遗漏 override 的 `-f` 列表。

这些方案可以组合。若同时使用 HOME bind 挂载、缓存 bind 挂载和共享 Codex 目录，下载全部四个 override 文件，并使用：

```dotenv
COMPOSE_FILE=compose.yaml:compose.shared-cache.yaml:compose.shared-codex.yaml:compose.bind-home.yaml:compose.shared-cache.bind.yaml
HOME_DIR=./home
SHARED_CACHE_DIR=../shared/caches
SHARED_CODEX_DIR=../shared-codex
```

只设置 `HOME_DIR` 不会切换存储，必须启用 `compose.bind-home.yaml`。修改变量或添加 override 都不会迁移已有数据。

### 共享下载缓存

只共享 npm、Go module 和 uv 下载缓存。全局工具、`node_modules`、虚拟环境、Go 构建缓存和凭据仍保持私有。启动时仅将 `npm`、`go-mod` 和 `uv` 目录初始化为 UID/GID `1000:1000`、权限 `0750`；不会递归修改目录内容，并会拒绝符号链接或非目录项。uv 使用 `copy` 模式，避免跨文件系统硬链接。安装依赖时不要清理 uv 缓存。

仅在使用相同 UID、彼此信任的 Linux 容器之间共享缓存。使用同一缓存卷名或 bind 来源的容器可以读取和修改彼此的缓存，其中可能包含私有源码。不要挂载或复制 macOS 缓存，也不要在缓存根目录存放凭据。

### 共享 Codex 凭据

在执行任何登录或启动命令之前，创建一个**专用目录**，其中包含 `config.toml` 和 `auth.json`，且 UID/GID `1000:1000` 可读。可参考[配置模板](shared-codex/config.toml.example)和[认证模板](shared-codex/auth.json.example)。来源目录不存在时不会自动创建。

只有这两个文件会链接到私有 Codex 状态目录。**不要共享整个 `.codex` 目录**；会话和缓存仍保存在私有 HOME。已有私有文件会以 `.before-shared` 后缀备份；若备份冲突，启动会拒绝覆盖。

此模式**仅支持 API key**，不支持需要刷新写入的 ChatGPT OAuth 凭据。所有参与共享的实例都能读取密钥；只读不等于保密。跳过 `codex login/logout`。原子替换共享文件后，后续读取可以获得新内容，但不能保证正在执行的任务实时更新。共享默认配置不是强制策略。

## 配置

可用设置见 [runtime.env.example](runtime.env.example)，基础部署契约见 [compose.yaml](compose.yaml)。

| 设置 | 用途 / 默认值 |
| --- | --- |
| `COMPOSE_PROJECT_NAME` | 必填，唯一实例标识 |
| `RUNTIME_IMAGE` | 选择镜像；默认 `ghcr.io/creekxi2026/agent-runtime:latest` |
| `HOME_VOLUME` | 私有卷名；默认 `<COMPOSE_PROJECT_NAME>-home` |
| `CPU_LIMIT`, `MEMORY_LIMIT` | 默认：`2` 个 CPU、`4g` 内存 |
| `GOPROXY` | Go module 代理；默认 `https://proxy.golang.org,direct` |
| `GOSUMDB` | Go 校验和数据库；默认 `sum.golang.org` |
| `GOPRIVATE` | 可选的私有 module 路径匹配模式；默认留空 |
| `MULTICA_SERVER_URL`, `MULTICA_APP_URL` | 留空连接 Multica Cloud；自托管时设置 |
| `MULTICA_BOOTSTRAP_TOKEN` | 可选，用于替代 Multica 交互式登录 |
| `MULTICA_DAEMON_MAX_CONCURRENT_TASKS` | 可选，守护进程级并发上限；不设置时使用 Multica 原生默认值，与 UI 中每个 Agent 的并发限制独立 |
| `LARK_APP_ID`, `LARK_APP_SECRET`, `LARK_BRAND` | 可选，用于初始化 Lark 应用；brand 默认 `feishu` |

Go module 下载在通用模板中默认使用官方代理。需要时可在实例的 `.env` 中设置 `GOPROXY=https://goproxy.cn,direct`；保留 `GOSUMDB=sum.golang.org`，继续校验公共 module 的校验和。逗号分隔的代理列表仅在收到 HTTP `404` 或 `410` 时回退，超时和其他错误不会触发回退。`GOPRIVATE` 仅填写自己的私有 module 路径匹配模式；匹配的 module 会绕过代理和校验和数据库。这些设置留空或未设置时，Compose 使用上述默认值。修改后需重建容器，重启不会重新加载 `.env`。

启动入口会短暂使用 root 初始化私有目录，随后清除 capabilities，以 UID/GID `1000:1000` 运行应用。Compose 为兼容 Codex 内层沙箱而放宽 seccomp/AppArmor，但不会启用 privileged 模式或挂载 Docker socket。这**不是面向不可信租户的强隔离边界**。

## 使用

### 办公用户首次对话

仓库提供一次性引导技能 [`agent-office-setup`](skills/agent-office-setup/SKILL.md)。管理员先完成容器、Multica 和模型认证，再按[接入手册](skills/agent-office-setup/references/activation.md)把技能复制到该用户的私有 HOME，并配置办公 Agent 的首次对话指令。现有镜像可直接使用，无需重建。

技能根据需求安装私有 Python 文档处理环境，引导本人授权，验证一项实际办公任务。成功后记录完成状态并删除该 HOME 中的技能副本；失败保留以便续接。源码模板与办公环境保留。它不随镜像内置技能分发，也不自动创建 Agent；仅复制文件不能保证首次对话触发，必须完成指令配置和真实首聊验收。

### 容器内工具

以应用用户身份进入 shell：

```bash
docker compose exec --user 1000:1000 runtime bash
```

未指定用户时，容器控制台和 `docker compose exec` 可能默认使用 root。诊断和安装用户工具时显式指定 UID，避免在 HOME 中产生 root 所有的文件。

- **用户工具：** 用 `npm install -g package-name` 或 `uv tool install package-name` 安装，内容会持久化在 HOME 中。用户命令路径包括 `~/.local/bin`。`MULTICA_CODEX_PATH` 指定 Multica 使用的 Codex 可执行文件。
- **Skills：** 官方 skills 从 `/opt/agent-skills` 链接到 `~/.agents/skills`。自定义 skills 放在 `~/.agents/skills/skill-name/SKILL.md`，更新时会保留自定义内容。Multica 的 `Copy from a runtime` 创建的是快照，不是实时链接。
- **浏览器会话：** 使用 `playwright-cli -s=task-name open about:blank`。并发任务应使用不同会话，只关闭自己的会话，不要使用 `close-all` 或 `kill-all`。默认空闲超时为一小时。`state-save` / `state-load` 文件包含敏感会话数据，应保存在私有 HOME。
- **Lark 用户资源：** `.env` 可初始化应用凭据，但用户授权还需执行 `docker compose run --rm runtime lark-cli auth login --domain docs --domain drive`。
- **微信小程序：** 预览和上传需要项目 AppID、上传私钥及微信侧对应配置。不支持只读根文件系统。

## 更新

等待运行中的任务结束，并备份 HOME 和 `.env`。更新时保持**实例名、HOME 存储和 `COMPOSE_FILE` 不变**：

```bash
docker compose pull
docker compose up -d
```

重建会复用 HOME；容器可写层中的其他文件不会保留。普通 `docker compose down` 会保留卷。**`docker compose down -v` 或删除卷会销毁数据**；删除共享缓存卷还可能影响其他实例。HOME 备份包含凭据，必须按敏感数据保护。

[发布工作流](.github/workflows/publish.yml)每天检查基础镜像、工具、官方 skills 及 Chromium/zipalign 依赖。变更通过两种架构的验证后发布为 `latest`，**不会自动更新运行中的容器**。仅保留 `latest`，没有历史标签或自动回滚。镜像内的依赖清单位于 `/usr/share/agent-runtime/dependencies.json`。

## 开发

运行单元测试，并根据仓库中的依赖清单构建：

```bash
python3 -m unittest discover -s tests -v
python3 scripts/split_dependencies.py --manifest runtime-deps.json --output .build-inputs
BASE_IMAGE=$(python3 -c 'import json; print(json.load(open("runtime-deps.json"))["base_image"])')
docker build --build-arg "BASE_IMAGE=$BASE_IMAGE" -t agent-runtime:test .
```

容器测试需要 Docker 和可丢弃的测试资源。请在隔离测试宿主机上执行：smoke 脚本使用固定资源名和清理命令。

```bash
IMAGE=agent-runtime:test sh tests/smoke.sh
SHARED_CACHE_DOCKER_TEST=1 python3 -m unittest discover -s tests -p test_shared_cache.py -v
python3 tests/shared_cache_smoke.py
```

npm/Go/uv fixture 测试需要当前镜像和 `host.docker.internal`。[验证工作流](.github/workflows/verify.yml)覆盖工具、挂载、私有状态、共享配置、skills 和浏览器。离线 fixture 不使用真实凭据，**不能证明**模型认证、Multica 任务派发、Lark 授权或小程序上传成功。
