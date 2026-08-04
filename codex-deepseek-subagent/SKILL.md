---
name: codex-deepseek-subagent
description: 仅在用户要求配置、检查、测试、修复、停用或卸载 Codex 的 DeepSeek 原生子 Agent 时使用；普通 DeepSeek API 问题和已配置后的日常编码任务不要触发。
---

# Codex DeepSeek 子 Agent

本 Skill 只维护配置，不承接日常用户任务。确定性的文件、模型目录和凭据操作交给 `scripts/codex_deepseek.py`；不要手动改 TOML、JSON、Agent 文件或钥匙串。

## 关键契约

- 自定义角色来自 `~/.codex/agents/DeepSeek.toml`；若设置了 `CODEX_HOME`，使用 `$CODEX_HOME/agents/DeepSeek.toml`。
- 管理目录必须把当前父模型的 `multi_agent_version` 固定为 `v1`。在当前已验证的 Codex 版本中，`v2` 跨 Provider 派发会让 DeepSeek 收不到明文任务。
- 父模型变化后必须运行 `repair`，再重新验收。
- DeepSeek 是纯文本 Agent，不处理图片、视频、截图或其他视觉输入。父 Agent 先识别视觉内容，再传入文字事实。
- 日常任务只能直接调用：

  ```text
  spawn_agent(agent_type="DeepSeek", fork_turns="none", ...)
  ```

  不要为日常任务运行本 Skill、管理脚本或 `codex exec`。
- 当前工具若不认识 `DeepSeek` 角色，只提示用户打开新任务或重启 Codex；不得改用默认角色、脚本或 `codex exec` 代做当前任务。

## 触发后的流程

1. 运行 `status --json`，根据结构化状态继续，不靠文件名猜测。
2. 配置请求运行 `setup --json`；父模型已变化或配置损坏时运行 `repair --json`。
3. 缺少凭据时简洁索要 API Key。收到后不要复述、回显或写入临时文件，只通过 `--api-key-stdin` 的标准输入传递。
4. `setup` 或 `test` 可以启动一个新的 Codex 任务做一次配置验收。若返回 `new_task_required` 或 `restart_required`，说明当前任务不会热加载新角色；提示用户打开新任务或重启后再使用 `DeepSeek`。
5. 验收必须检查子线程数据库 `threads` 表的实际元数据，并确认子 Agent 返回口令 `NATIVE_DEEPSEEK_OK`。两者缺一不可，不能以子 Agent 自述代替。
6. 最终只汇报状态、实际 Provider、模型、思考程度、角色和备份位置；不要输出密钥或原始事件日志。

## 管理命令

入口：

```text
python3 <skill-dir>/scripts/codex_deepseek.py <command> --json
```

- `status`：只读检查配置、模型目录、凭据和客户端能力。
- `setup`：写入配置并验收；缺少密钥时返回 `credential_missing`。
- `test`：执行一次直连测试和一次原生 `spawn_agent(agent_type="DeepSeek")` 验收。
- `repair`：按当前父模型重新应用配置并验收。
- `disable`：停用本 Skill 创建的角色，保留 Provider、模型目录和凭据。
- `uninstall`：移除本 Skill 管理的配置；只有用户明确要求删除凭据时才传 `--remove-credential`。

默认使用当前 `CODEX_HOME`；仅在用户明确指定其他 Codex Home 时传 `--codex-home`。

## 状态处理

- `ready`：直连、原生路由、数据库元数据和返回口令均通过。
- `configured`：静态配置完整，但尚未完成实时验收。
- `credential_missing`：索要 API Key 后继续原流程。
- `operation_in_progress`：已有配置操作正在运行，稍后重试，不并发修改。
- `conflict`：报告冲突文件和字段，等待用户决定是否替换。
- `unsupported`：报告缺少的系统能力或最低版本，不手工绕过。
- `failed`：读取结构化 `errors`；若程序已回滚，明确说明，不再手改配置。

结果字段 `new_task_required: true` 或 `restart_required: true` 表示当前任务不会热加载角色，需要打开新任务或重启。

更详细的路径、版本和安全边界见 [references/compatibility.md](references/compatibility.md)。
