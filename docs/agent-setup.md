# 为用户配置 Agent Runtime

这份说明供负责部署的 Agent 使用。用户可以直接说：

> 阅读 agent-runtime 仓库的 docs/agent-setup.md，帮我配置一个办公环境。

读完后按下面的步骤操作即可，不用把本文安装成 Skill。先了解需求、给出方案，用户确认后再部署；已经确认过的事项直接继续。

## 开始前问清什么

先看同一版本的 [中文 README](../README.zh-CN.md)、[compose.yaml](../compose.yaml) 和 [环境变量模板](../runtime.env.example)。已有实例可参考时，查看它的 Compose 配置、工作目录和实际挂载。

下面四个问题，只问用户还没说过的部分：

1. **叫什么名字，给谁用？** 例如用户叫 `user01`，实例可命名为 `agent-user01`。默认使用现有的 `ghcr.io/creekxi2026/agent-runtime:latest`，每位用户不必单独构建镜像。
2. **主要做什么？** 了解他要处理哪些材料、希望拿到什么结果。例如写公文、做 PPT、整理表格，或使用飞书。软件由 Agent 根据用途选择。
3. **部署机器上有 Codex 吗，要共享它的模型配置和 API key 吗？** 同意共享后，核对指定目录中的 `config.toml` 和 `auth.json`。没有现成配置，就询问独立登录方式或专用 key 的保存位置。ChatGPT OAuth 登录需要刷新令牌，不能按这里的只读方式共享。
4. **连接哪个 Multica 服务，用谁的账号？** 可以从用户给的登录、部署命令中取得服务地址和凭据来源。登录后能查到账号和工作区，不用提前要求用户找工作区 ID；有多个候选且用途不明确时再问。

用户说“和某个实例部署在一起”时，沿用查到的主机和部署根目录。新部署默认放在目标主机的 `$HOME/agent-runtime/<user>`，每个实例使用独立 Compose 项目和 HOME。主机还不明确时再询问，不默认使用当前 Agent 所在机器，也不为整理目录迁移已有实例。

飞书授权、网络代理和额外存储，只在用到时补问。密钥从用户指定的私密配置中读取，不让用户贴进文档或提交到 Git。共享凭据需要用户同意；未获授权时只检查文件是否存在。

## 安装方案

先检查镜像里已有的工具，再决定补装什么。目标机器已有镜像时可以只读核对版本；没有镜像则先根据仓库清单出方案，部署后再检查。新增软件需核对官方安装说明及 CPU、Python 或 Node 版本兼容性。

| 用途 | 通常使用的工具 |
| --- | --- |
| 飞书文档、云盘、表格 | 镜像已有 Lark CLI 和技能，完成对应账号授权即可 |
| 网页操作 | 镜像已有 Playwright CLI 和 Chromium；网站登录由使用人完成 |
| CSV、JSON | Python 标准库 |
| Excel | 按需安装 openpyxl；公式重算、渲染另行处理 |
| Word | 按需安装 python-docx |
| PPT | 按需安装 python-pptx |
| PDF、OCR、格式转换 | 根据读取、生成、渲染等具体需求选工具 |

Python 依赖装到实例私有 HOME 下的虚拟环境，例如 `$HOME/.local/share/office/venv`，并让办公 Agent 使用它。不修改系统 Python。确实需要系统包时，把重建后的恢复办法列入方案；临时装进容器可写层的包会在重建时丢失。构建或发布派生镜像也需包含在用户确认的范围内。

这些配置可以直接写进方案，不用逐项询问；用户已有要求时以其要求为准：

| 配置 | 默认处理 |
| --- | --- |
| HOME、权限、工具路径 | 镜像入口负责初始化，Compose 提供独立 HOME 卷 |
| 下载缓存 | 默认启用 `compose.shared-cache.yaml`，所有实例共用 Docker/OrbStack 卷 `agent-runtime-download-cache`，挂到 `/shared/caches` |
| 官方技能、浏览器配置 | 镜像入口自动链接 |
| 容器重启策略 | Compose 已设置 `unless-stopped` |
| 资源 | 2 CPU、4 GiB；按用途和主机容量调整 |
| daemon 总并发 | 使用 Multica 自动值，不额外设置 `MULTICA_DAEMON_MAX_CONCURRENT_TASKS` |
| 新 Agent 并发 | 创建时显式传 `--max-concurrent-tasks 50` |
| Agent 调用权限 | 默认私有，归属本次登录账号 |
| 服务地址、凭据、挂载来源 | 写入实例配置，不放进镜像 |

Agent 并发是 Multica 服务端字段，不能通过镜像 ENV 修改。Multica 0.6.1 的平台默认值为 6，接受 1–50；这里主动设为 50，仍是上限。升级 CLI 后核对取值范围，不改其他已有 Agent 的并发。

共享下载缓存适合同一主机上的可信实例，只包含 npm、Go module 和 uv 缓存。用户工具、虚拟环境、凭据和会话仍在各自 HOME 中。需要隔离缓存时，使用单独的 `SHARED_CACHE_VOLUME`，或从 `COMPOSE_FILE` 移除缓存 override。无需再创建宿主机 `shared/caches` 目录。

如果用户要求容器也使用指定名字，准备 `container_name` override。只设置 `COMPOSE_PROJECT_NAME` 时，Compose 通常生成 `<instance-name>-runtime-1`。

安装前向用户说明：部署位置和名称、用途、需要补装的软件及版本、账号与模型来源、资源和存储配置、是否创建 Agent，以及准备怎样测试。凭据只说明来源。拿到确认后执行；范围有变化时只补充确认变化的部分。

## 部署

1. 检查目标机器的 Docker、Compose、架构和资源，确认目录、容器和数据卷没有重名冲突。已有资源先核对归属，保留其中的数据。
2. 准备独立部署目录，下载 `compose.yaml`、`compose.shared-cache.yaml` 和环境变量模板，保存私有 `.env`，不要覆盖已有配置。先确定 HOME 存储和 `COMPOSE_FILE`，后面的登录、安装和启动都使用这套参数；更换挂载不会自动搬迁数据。
3. 使用已确认的镜像并记录实际 digest。配置模型认证和 Multica 登录，具体方式见下节。
4. 以容器用户 `1000:1000` 安装缺少的工具，保存在私有 HOME 中。记录版本，避免把依赖误装到部署 Agent 的宿主机上。
5. 执行 `docker compose up -d`，检查状态和脱敏日志。需要飞书或网站授权时，由对应用户完成。
6. 方案包含创建 Agent 时，先查是否已有同名对象，再创建或绑定 Runtime，设置职责、工具位置、调用权限和约定的并发值。

创建 Agent 前核对实际登录账号、工作区和 Runtime 归属。实例名不能代表账号，也不要直接使用当前部署任务所在的工作区。实际身份与用户指定的使用人不一致时再澄清。

出错时根据日志区分文件不可读、模型认证失败和 Multica 登录失败。保留已完成的配置和数据，从失败的步骤继续，不靠清空 HOME 或放宽凭据权限解决。

## 登录与共享配置

### Multica

自托管服务需要 `MULTICA_SERVER_URL` 和 `MULTICA_APP_URL`。使用交互登录时，先在新实例内设置地址：

```sh
docker compose run --rm runtime multica config set server_url '<API 地址>'
docker compose run --rm runtime multica config set app_url '<网页地址>'
docker compose run --rm runtime multica login --token
```

最后一条命令通过提示输入 token，避免把密钥写进命令行参数。只填写 `.env` 的 URL 不会让交互登录自动使用这些地址：当前入口是在启动 daemon 时应用它们。使用 `MULTICA_BOOTSTRAP_TOKEN` 时，则由 daemon 启动流程先设置地址、再登录。

Multica 的 `mul_` token 用于平台登录，模型 API key 用于模型调用，两者分别配置。不要使用当前部署任务的 task token，也不要借用宿主机或其他实例的 Multica 登录。所有登录操作都在新实例中进行。

登录后用该版本的 `multica workspace list` 核对账号可访问的工作区，必要时用 `multica workspace switch` 选择默认值。切换默认工作区只影响 CLI 默认操作位置；限制 Runtime 可用范围还需核对账号权限和 Runtime 可见性。

更换 token 后重新核对账号及工作区，用新身份查询 Runtime ID。旧测试 Agent 如果是本次创建且没有任务，可以归档。遇到 `401 invalid token` 时使用用户提供的新凭据重新登录，模型 key 不能解决这个错误。

### Codex

用户同意共享后，按来源选择一个 override：

| 来源 | 使用的文件 |
| --- | --- |
| 专门存放 `config.toml`、`auth.json` 的共享目录 | [compose.shared-codex.yaml](../compose.shared-codex.yaml) |
| 已有 `.codex` 目录，只挂载其中两个文件 | [compose.shared-codex-files.yaml](../compose.shared-codex-files.yaml) |

设置 `SHARED_CODEX_DIR`，把选中的 override 加入现有 `COMPOSE_FILE`，保留其他 override。这两个 Codex override 二选一。入口会将配置链接到实例中，会话和工作文件仍保存在私有 HOME。

来源文件须由容器用户可读，`auth.json` 使用 API key 认证。不要挂载整个 `.codex`，也不要混用下载缓存目录。检查时只输出文件是否存在、认证类型等信息，不打印密钥。用户已指定来源并同意共享时直接操作；找不到来源再询问路径，不从其他用户 HOME、旧备份或临时目录取凭据。

单文件 bind 在宿主机原子替换配置或密钥后可能仍指向旧文件。等实例任务结束，执行 `docker compose up -d --force-recreate`，再验证一次模型调用。只重建本次配置的实例。

## 检查与交付

用一项贴近用途的任务检查结果，例如生成一份 Word 公文和一份 PPT。分别记录：

- 容器运行稳定，正确账号和工作区中能看到 Runtime 在线。
- 目标容器完成了一次真实模型调用，所需工具能生成文件。
- 文件能重新打开，内容和结构正确。最终排版需要渲染检查，未做则说明。
- 实例空闲时重建一次，确认认证、依赖及文件仍在，文件校验和一致。
- 用户有可用的 Agent 入口和文件附件。网页首聊是否测试过也需写明。

Runtime 在线不等于模型可用，脚本样例也不能当作模型生成成果。附件上传成功说明文件已交付，但不能据此说用户已经打开。重建检查不包含备份恢复或版本升级测试。

最后给出使用入口、已装工具、测试结果、停启和更新方法，以及尚未完成的事项。文件通过平台附件交付，不用容器内部路径代替下载入口。仓库改动说明是本地提交、附件补丁，还是已推送的分支或 PR。

真实主机地址、账号和部署路径留在实例私有记录中，仓库文档只用示例值；密钥不进入任何交付文本。交付后结束本次安装，将来新增能力时再按需求处理。
