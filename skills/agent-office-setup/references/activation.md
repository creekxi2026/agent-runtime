# 管理员接入：现有镜像，无需重建

这是每个私有 HOME 一次的办公初始化。一个实例只承载同一可信用户；若多个用户共享 HOME，这份完成记录和认证就不再按用户隔离。

## 对话开始前

按仓库 `README.zh-CN.md` 的快速开始完成 Docker/Compose、独立 HOME、Multica 和模型认证。确认 Runtime 已能执行对话；首次对话不能替自己安装尚未运行的 Runtime。

在该实例的 Compose 目录操作，并沿用已有的 `.env` / `COMPOSE_FILE`。假定解压后得到 `./agent-office-setup` 文件夹（仓库源文件在 `skills/agent-office-setup`，使用仓库时替换下面的本地来源路径）。对 `agent-calder` 设置独立项目名及 HOME；建议初始 Agent 并发为 1。

先确认未安装过，避免覆盖自定义技能或重跑已完成的引导：

```bash
docker compose exec --user 1000:1000 runtime sh -c '
  test ! -e "$HOME/.agents/skills/agent-office-setup" &&
  test ! -L "$HOME/.agents/skills/agent-office-setup" &&
  test ! -e "$HOME/.local/share/agent-office-setup/state.json" &&
  mkdir -p "$HOME/.agents/skills"
'
```

仅在上述命令成功后复制。若有已有状态，先检查并续接，不删除已有记录来强行重装。

```bash
docker compose cp ./agent-office-setup runtime:/home/agent/.agents/skills/agent-office-setup
docker compose exec --user 0:0 runtime chown -R 1000:1000 /home/agent/.agents/skills/agent-office-setup
docker compose exec --user 1000:1000 runtime python3 /home/agent/.agents/skills/agent-office-setup/scripts/setup.py status
```

**只复制到该用户 HOME。** 不放入 `/opt/agent-skills`，不创建指向共享模板的链接，不作为工作区共享技能反复下发；这些方式会让自删失效或在后续任务中重新出现。当前镜像入口会保留用户自定义技能；它仅重建镜像内置技能链接，不会重新生成本技能。

## 首次对话触发

技能描述本身不能保证“首次对话一定执行”。需要在这个办公 Agent 的持久 `instructions` 中加入以下约定，保留其他现有指令；不要只写在 `description` 或对话开场建议里：

```text
本 Agent 使用独立 HOME。每次开始处理用户消息，先只读检查
$HOME/.local/share/agent-office-setup/state.json。
若 JSON 中 phase 为 complete，按正常办公流程处理，不再初始化。
否则，若 $HOME/.agents/skills/agent-office-setup/SKILL.md 存在，
先读取它，向用户说明首次办公配置，并执行或续接该技能；
若用户已明确要求稍后初始化，则本轮跳过，不记录完成。
若既无完成记录也无技能，或状态 JSON 损坏，报告管理员接入未完成，
不要自行猜测安装成功、重新下载模板或重装运行环境。
需要本地文档库时，使用
$HOME/.local/share/agent-office-setup/venv/bin/python。
```

这是 Agent 行为约定，不是服务器事件钩子。管理员在修改前确认正确的 Agent / Runtime / 用户身份和授权范围；部署后用用户首次消息实测它是否加载技能。没有配置此约定时，可显式调用 `$agent-office-setup`，但不能声称已实现自动首聊触发。

## 完成、续接和恢复

- 失败或等待用户授权：保留技能，状态继续可读，下次对话续接。
- 成功：`finish` 写入完成记录并移除私有副本。以后每轮只检查完成记录，容器重建仍沿用 HOME。
- 记录已完成但技能尚在：通常是清理中断。确认目录是私有副本后，可再次执行 `finish`；不会再安装依赖。
- 要重新启用引导：须由用户或管理员明确要求，先备份完成记录，再恢复私有技能副本；不能把普通启动或镜像更新当作重新引导的理由。
- 交付前验证：首聊触发、真实模型回复、所选办公服务授权、结果可下载、自删后继续对话、容器重启后不再引导。

本技能的本地测试不能代替用户认证与真实 Multica 首聊验收。
