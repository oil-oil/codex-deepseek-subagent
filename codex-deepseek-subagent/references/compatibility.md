# 兼容性与安全边界

## 支持范围

- macOS
- Python 3.11+
- Codex CLI 或 ChatGPT 桌面应用至少启动过一次
- DeepSeek 官方 Responses API
- `deepseek-v4-flash`
- 思考程度 `high`

## 配置位置

默认 `CODEX_HOME` 为 `~/.codex`：

- Codex 配置：`$CODEX_HOME/config.toml`
- 合并模型目录：`$CODEX_HOME/models-with-deepseek.json`
- 自定义角色：`$CODEX_HOME/agents/DeepSeek.toml`（默认即 `~/.codex/agents/DeepSeek.toml`）
- 管理状态与备份：`$CODEX_HOME/codex-deepseek-subagent/`
- macOS Keychain 服务：`codex-deepseek-api-key`

程序不修改顶层 `model` 或顶层 `model_provider`，主任务仍使用用户原来的模型和登录方式。

## 原生派发兼容性

管理目录会把当前父模型的 `multi_agent_version` 固定为 `v1`。在当前已验证的 Codex 版本中，`v2` 跨 Provider 派发会让 DeepSeek 收不到明文任务，因此不能用 `v2` 验收或日常派发。父模型变化后必须运行 `repair`，让目录中的对应父模型重新标记为 `v1`。

日常任务只允许主 Agent 直接调用：

```text
spawn_agent(agent_type="DeepSeek", fork_turns="none", ...)
```

如果当前工具 schema 不认识 `DeepSeek`，只提示打开新任务或重启 Codex。不要用管理脚本或 `codex exec` 代做用户任务。

`setup` 或 `test` 可以启动一个新的 Codex 任务做一次配置验收。验收证据必须来自两处：

1. `$CODEX_HOME/state_*.sqlite` 中包含对应子线程的 `threads` 表元数据：

   ```text
   model_provider = deepseek
   model = deepseek-v4-flash
   reasoning_effort = high
   agent_role = DeepSeek
   ```

2. 子 Agent 返回精确口令：`NATIVE_DEEPSEEK_OK`。

只有元数据和口令同时匹配，才能称为真实的 DeepSeek 原生子 Agent。子 Agent 的自述不能替代数据库记录。

## API Key

API Key 可由用户在聊天中提供。管理程序从标准输入读取，不写入命令参数、临时文件、配置文件或测试结果，随后保存到 macOS Keychain。

不要在最终回复、日志摘要、异常信息或测试夹具中重复 API Key。

## 配置事务

写入前创建带时间戳的备份。程序使用进程锁避免并发修改，先生成候选配置并用 TOML、JSON 解析验证，再原子替换目标文件。写入、卸载或实时测试失败时，恢复本次事务开始前的文件。

已存在但不属于本 Skill 的冲突配置不会被静默覆盖；完全兼容的现有配置可以被采用，并在结果中标记 `adopted_existing`。

## 视觉输入

DeepSeek V4 Flash 当前只接受文本。父 Agent 必须先检查图片、视频和截图，把必要事实写成文字任务包；子 Agent 不应声称自己看过视觉材料。
