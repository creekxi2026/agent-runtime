# Agent Runtime

基于 [sapk/multica-docker-env](https://github.com/sapk/multica-docker-env) 的 Codex 镜像，仅补充 `lark-cli` 和面向 Agent 的 `playwright-cli`。启动时不安装软件。

## 使用

每个用户只需要 `compose.yaml` 和 `runtime.env`：

```bash
curl -fLO https://raw.githubusercontent.com/creekxi2026/agent-runtime/main/compose.yaml
curl -fL https://raw.githubusercontent.com/creekxi2026/agent-runtime/main/runtime.env.example -o runtime.env
chmod 600 runtime.env
# 编辑 runtime.env：唯一实例名、镜像版本、必要凭据。
docker compose --env-file runtime.env up -d
```

不要覆盖已有的 `runtime.env`；新增用户复制干净模板，不复制其他用户的数据卷或凭据。不同主机可复用同一镜像；同一 Docker 主机上实例名必须唯一。

## 不把凭据写入配置文件

```bash
docker compose --env-file runtime.env run --rm runtime multica login --token
docker compose --env-file runtime.env run --rm runtime codex -c 'cli_auth_credentials_store="file"' login --device-auth
docker compose --env-file runtime.env run --rm runtime lark-cli config init
# 需要访问飞书用户个人资源时，另做用户 OAuth 授权：
docker compose --env-file runtime.env run --rm runtime lark-cli auth login --domain docs --domain drive
docker compose --env-file runtime.env up -d
```

`runtime.env` 是明文配置，不是加密凭据库。环境变量方式初始化后可清空 Token/Secret，再 `up -d --force-recreate`；保存的认证状态仍在独立数据卷中。飞书应用仅在配置文件不存在时初始化，重启不会覆盖已有用户授权；更换飞书应用请显式使用 CLI 配置命令。Multica Agent 的工具环境变量和 MCP 配置可在 Multica 中管理。应用身份和用户 OAuth 是不同权限。

## 浏览器（可选）

Mac 上另行部署每用户独立的 Playwright 服务，将其 WebSocket 地址填入 `PLAYWRIGHT_WS_ENDPOINT`：

```bash
docker compose --env-file runtime.env exec runtime sh -c 'playwright-cli attach --endpoint="$PLAYWRIGHT_WS_ENDPOINT"'
```

这不是 MCP 或 CDP 地址。客户端与远端的 Playwright 协议版本必须兼容。Agent 任务也可以直接调用 `playwright-cli attach`。上游自带的 `@playwright/test` 与 `@playwright/cli` 是两个不同工具；后者不重复下载浏览器，主要用于连接远程浏览器。

## 更新

修改 `runtime.env` 中的 `IMAGE`，再执行：

```bash
docker compose --env-file runtime.env pull
docker compose --env-file runtime.env up -d
```

上游基础镜像固定 digest；补充工具版本在 Dockerfile 中指定。修改后由公开仓库的标准 GitHub-hosted runners 构建、测试两个架构，全部通过后发布版本标签和 latest。没有自动定时升级部署，用户自行选择镜像版本。

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

测试不使用真实用户凭据、不调用付费模型；真实 Multica 派发、飞书 OAuth 和远程浏览器连接需独立验收。
