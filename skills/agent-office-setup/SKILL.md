---
name: agent-office-setup
description: 为 agent-runtime 独立办公实例执行首次对话初始化或继续未完成的初始化，按需安装办公依赖、引导本人授权并验收，成功后移除当前 HOME 中的引导技能。容器和模型须已可用；不用于日常办公或部署宿主机。
---

# 办公实例首次使用

管理员接入方式见 [首次对话触发与部署](references/activation.md)。本技能只初始化当前私有 HOME，不创建容器、重启 Multica、切换工作区或复制其他人的认证。Docker、Runtime、Multica 和模型登录必须在用户能发起对话之前完成。

## 进入与续接

1. 运行 `python3 "$HOME/.agents/skills/agent-office-setup/scripts/setup.py" status`。状态和环境位于 `$HOME/.local/share/agent-office-setup`，不要使用任务临时目录或任务专属 `CODEX_HOME` 保存完成状态。
2. 已是 `complete` 时直接处理用户当前任务，不再安装。完成标记不会随容器重建消失。未完成时根据记录续接；不得把一次失败标记为完成。
3. 向用户简短说明这次会完成办公环境初始化。先利用已知需求；需要时只问主要办公场景：飞书在线文档、网页、还是本地 Excel/Word/PDF。用户选择以后再做时保留技能和状态，不强行安装。

## 按需安装

镜像已有 Codex、Multica、Lark CLI、Playwright 和 Chromium；先检查，不重装这些工具。不要升级镜像自带工具或在这里安装系统级套件。

从下表选择需要的能力；只使用在线服务时选择 `basic`：

| feature | 安装及验证范围 |
| --- | --- |
| `basic` | Python 标准库 CSV/JSON，无额外包 |
| `xlsx` | openpyxl：Excel 文件读写，不保证公式重算或渲染 |
| `docx` | python-docx：Word 文件读写，不保证排版渲染 |
| `pdf` | pypdf：基本 PDF 读取/合并，不含 OCR、页面渲染或排版生成 |

例如用户需要 Excel 和 Word：

```bash
python3 "$HOME/.agents/skills/agent-office-setup/scripts/setup.py" install --features xlsx docx
python3 "$HOME/.agents/skills/agent-office-setup/scripts/setup.py" verify
```

脚本把环境装入 `$HOME/.local/share/agent-office-setup/venv`，不修改系统 Python。后续使用其中的 `bin/python`；所装版本写入状态，依赖范围不是完整可复现锁文件。再次运行会保留此前选中的能力。安装或验证失败时，报告具体失败并保留进度；不要关闭 TLS 验证、改用陌生源或提高系统权限来绕过失败。

安装范围获得当前用户请求授权后直接执行，不重复索取同一授权。若用户提出 OCR、PDF 排版、幻灯片或 Office 转换，明确当前范围，按具体需求另行处理；不要将这些能力宣称为已安装。

## 用户授权与真实验收

- 飞书：使用已安装的 `lark-shared` 及对应 `lark-*` 技能读取当前 CLI 的认证指引。检查已授权身份，只为用户明确需要的域发起本人授权。没有这些技能时先查 `lark-cli auth --help`。不读取或回显 token 文件，不让用户把密钥贴入聊天。
- 授权要求打开链接或设备确认时，交给用户完成。缺少应用配置则交回管理员；不能拿别人的登录态代替。只验证已选择的服务；用户不需要飞书时无需强制授权。
- 网页：需要时读取 `playwright-cli` 技能，用当前任务独有的会话做一次页面访问；只关闭自己的会话。
- 完成用户需要的一项代表性任务，例如读取其授权的测试文档并生成可下载摘要/表格。仅 CLI `--version` 或本地依赖测试通过，不等于飞书权限、浏览器或结果交付通过。
- 按当前界面的附件/文件交付机制交付实际结果，不把容器内部路径当作用户可下载链接。若外部授权或文件交付失败，保留本技能，不执行完成命令。

## 完成与移除

所选依赖、所需服务和代表性任务均成功后：

```bash
python3 "$HOME/.agents/skills/agent-office-setup/scripts/setup.py" finish \
  --verified-task "已完成授权测试文档摘要并通过当前界面交付结果"
```

用实际完成的任务简述替换示例，不放业务正文或秘密。脚本会重新验证所选本地能力，先原子写入 `complete` 记录，再删除**当前 HOME 的独立技能副本**；保留办公环境和完成记录。它不会删除仓库模板、镜像技能或工作区技能，且拒绝从共享链接或其他路径自删。真实服务验收由你负责，`--verified-task` 是验收记录，不是脚本自动证明外部任务成功。

删除后当前对话可能仍缓存本技能，按完成记录直接继续正常工作。不要为消除这个缓存重启进程。若完成记录已写但删除失败，说明初始化成功、清理待修复，不重复安装。
