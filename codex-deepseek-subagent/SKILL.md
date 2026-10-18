---
name: codex-deepseek-subagent
description: "检查和维护 Codex 桌面应用中的 DeepSeek Flash 历史原生子 Agent 集成，支持路由验收、修复、停用和卸载。当前 Codex 的子 Agent 不能覆盖父 Provider，不保证该历史方案可用。仅在用户要求配置或管理该集成时使用。"
---

# Codex DeepSeek 子 Agent

本 Skill 只维护配置，不承接日常用户任务。确定性的文件、模型目录和凭据操作交给 `scripts/codex_deepseek.py`；不要手动改 TOML、JSON、Agent 文件或系统凭据库。

## 当前兼容性限制

当前 [Codex 官方规则](https://learn.chatgpt.com/docs/agent-configuration/subagents)不允许角色文件覆盖父 Provider。先说明这一限制，不要承诺原生跨 Provider 派发，也不要自动安装后反复重试。仅在用户明确要求验收历史兼容方案时继续配置流程。更换模型名或关闭 v2 不是绕过方式；直连成功、静态文件完整均不能替代原生路由验收。

## API Key 配置入口

需要外部服务凭据时先读[API Key 配置与业务读取](references/api-key-setup.md)：复用已有安全入口；本机缺少 Key 时使用随附固定页面，保存后通过业务包装入口读取。内置能力与纯本地流程不要求配置 Key。

## 关键契约

- 只使用桌面应用内置的 Codex 运行时；版本仅用于诊断，兼容性以真实派发验收为准。
- 从桌面配置读取父模型，并由管理程序应用 v1 明文派发设置；不要硬编码父模型或手改配置。技术原因见 [references/compatibility.md](references/compatibility.md)。
- 父模型变化后必须运行 `repair`，再重新验收。
- 仅支持并默认使用 `deepseek-flash`，目前对应 DeepSeek-V4.1-Flash-1017，思考程度为 `high`。旧名称 `deepseek-v4-flash` 只用于受管配置迁移，不注册第二个模型。
- 本集成仅派发文本，不代表 Flash 模型本身没有视觉能力。父 Agent 先识别视觉内容，再传入文字事实。
- 日常任务只能直接调用：

  ```text
  spawn_agent(agent_type="DeepSeek", fork_turns="none", ...)
  ```

  不要为日常任务运行本 Skill、管理脚本或 `codex exec`。
- 当前工具若不认识 `DeepSeek` 角色，只提示用户打开新任务或重启 Codex；不得改用默认角色、脚本或 `codex exec` 代做当前任务。

## 触发后的流程

1. 运行 `status --json`，根据结构化状态继续，不靠文件名猜测。
2. 首次配置默认使用 `deepseek-flash`，不询问模型选择。
3. 配置请求运行 `setup --json` 或显式指定 `--model deepseek-flash`；修复时运行 `repair --json`，自动迁移受管旧名称。无法识别的旧模型应报告不支持，不擅自替换。
4. 缺少凭据时按配置说明展示固定页面，由用户保存后，经 run 入口运行 setup 并传 `--api-key-env`，接入运行时原生凭据；不在聊天中索要 API Key。`--api-key-stdin` 仅供可信凭据程序传递，不拼接密钥命令。
5. `setup`、`repair` 或 `test` 使用桌面内置运行时创建隔离验收会话。若返回 `new_task_required` 或 `restart_required`，提示用户重启桌面应用并打开新任务。
6. 验收必须检查子线程数据库 `threads` 表的实际元数据，并确认子 Agent 返回口令 `NATIVE_DEEPSEEK_OK`。实际 `model` 必须等于用户选择的模型，两者缺一不可。
7. 最终只汇报状态、实际 Provider、模型、思考程度、角色和备份位置；不要输出密钥或原始事件日志。

## 管理命令

入口。macOS 使用 `python3`，Windows 使用 `py -3`：

```text
python3 <skill-dir>/scripts/codex_deepseek.py <command> --json
```

- `status`：只读检查桌面内置运行时、配置、模型目录、凭据和客户端能力。
- `setup`：默认使用 `deepseek-flash` 写入配置并验收，`--model` 可省略。
- `test`：通过桌面内置运行时执行一次直连测试和一次原生 `spawn_agent(agent_type="DeepSeek")` 验收。
- `repair`：按当前父模型重新应用配置并验收；传 `--model` 可切换模型，不传则保留当前模型。
- `disable`：停用本 Skill 创建的角色，保留 Provider、模型目录和凭据。
- `uninstall`：移除本 Skill 管理的配置；只有用户明确要求删除凭据时才传 `--remove-credential`。

默认使用当前 `CODEX_HOME`；仅在用户明确指定其他 Codex Home 时传 `--codex-home`。

## 状态处理

- `ready`：直连、原生路由、数据库元数据和返回口令均通过。
- `configured`：静态配置完整，但尚未完成实时验收，`native_routing_verified` 为 `false`，不能宣称可用。
- `credential_missing`：引导用户通过固定页面保存，再经包装器完成运行时配置后继续原流程。
- `model_selection_required`：现有安装记录未选择受支持模型；提示本仓库只支持 Flash，不读取受管记录之外的角色文件来猜测选择。
- `native_provider_inherited`：子线程实际继承了父 Provider，当前运行时不能使用该历史路由；停止并说明限制。`setup` / `repair` 失败会按原备份恢复文件，不改走脚本或默认角色代做任务。
- `operation_in_progress`：已有配置操作正在运行，稍后重试，不并发修改。
- `conflict`：报告冲突文件和字段，等待用户决定是否替换。
- `unsupported`：报告缺少的系统能力，不按固定版本号猜测兼容性，也不手工绕过。
- `failed`：读取结构化 `errors`；若程序已回滚，明确说明，不再手改配置。

更详细的路径、版本和安全边界见 [references/compatibility.md](references/compatibility.md)。
