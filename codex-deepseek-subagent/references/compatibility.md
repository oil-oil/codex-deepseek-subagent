# 兼容性与安全边界

## 支持范围

- macOS
- Windows
- Python 3.11+
- ChatGPT/Codex 桌面应用至少启动过一次
- DeepSeek 官方 Responses API
- `deepseek-v4-flash`
- `deepseek-v4-pro`（包含 DeepSeek 官方在 8·13 更新的当前服务版本，API slug 不变）
- 思考程度 `high`

## 配置位置

默认 `CODEX_HOME` 为 `~/.codex`：

- Codex 配置：`$CODEX_HOME/config.toml`
- 合并模型目录：`$CODEX_HOME/models-with-deepseek.json`
- 自定义角色：`$CODEX_HOME/agents/DeepSeek.toml`（默认即 `~/.codex/agents/DeepSeek.toml`）
- 管理状态与备份：`$CODEX_HOME/codex-deepseek-subagent/`
- 系统凭据目标：`codex-deepseek-api-key`
  - macOS：Keychain
  - Windows：Credential Manager

程序不修改顶层 `model` 或顶层 `model_provider`，主任务仍使用用户原来的模型和登录方式。

## 原生派发兼容性

检查和验收优先使用桌面应用内置运行时。Windows 无法从常见安装位置发现运行时时，会尝试 PATH 中的 `codex.exe`；仍无法发现时需要通过 `CODEX_DESKTOP_BIN` 明确指定。版本号仅作诊断；兼容性由模型目录解析、DeepSeek 直连、原生派发、返回口令和数据库元数据共同决定。

父模型从桌面当前配置动态读取。管理程序会把 `features.multi_agent_v2` 设为 `false`，并把该父模型的 `multi_agent_version` 固定为 `v1`，避免桌面端 v2 加密跨 Provider 子任务正文。父模型变化后运行 `repair`。

日常任务只允许主 Agent 直接调用：

```text
spawn_agent(agent_type="DeepSeek", fork_turns="none", ...)
```

如果当前工具 schema 不认识 `DeepSeek`，只提示打开新任务或重启 Codex。不要用管理脚本或 `codex exec` 代做用户任务。

`setup` 或 `test` 会通过桌面内置运行时创建隔离验收会话。验收证据必须来自两处：

1. `$CODEX_HOME/state_*.sqlite` 中包含对应子线程的 `threads` 表元数据：

   ```text
   model_provider = deepseek
   model = deepseek-v4-flash 或 deepseek-v4-pro
   reasoning_effort = high
   agent_role = DeepSeek
   ```

2. 子 Agent 返回精确口令：`NATIVE_DEEPSEEK_OK`。

只有元数据和口令同时匹配，才能称为真实的 DeepSeek 原生子 Agent。子 Agent 的自述不能替代数据库记录。

## API Key

API Key 可由用户在聊天中提供。管理程序从标准输入读取，不写入命令参数、临时文件、配置文件或测试结果。macOS 将密钥保存到 Keychain，Windows 将密钥保存到 Credential Manager。

不要在最终回复、日志摘要、异常信息或测试夹具中重复 API Key。

## 配置事务

写入前创建带时间戳的备份。程序使用进程锁避免并发修改，先生成候选配置并用 TOML、JSON 解析验证，再原子替换目标文件。写入、卸载或实时测试失败时，恢复本次事务开始前的文件。

已存在但不属于本 Skill 的冲突配置不会被静默覆盖；完全兼容的现有配置可以被采用，并在结果中标记 `adopted_existing`。

## 视觉输入

DeepSeek V4 Flash 与 V4 Pro 当前都只接受文本。父 Agent 必须先检查图片、视频和截图，把必要事实写成文字任务包；子 Agent 不应声称自己看过视觉材料。

## 模型选择

- `deepseek-v4-flash`：响应更快、成本更低，适合日常编码和高频任务。
- `deepseek-v4-pro`：能力更强，适合复杂编码、架构分析和高难度 Agent 任务。

首次配置必须明确选择模型。模型目录同时注册两个官方模型，Agent 文件只绑定当前选择。切换模型时管理程序会更新 Agent 文件并重新执行直连与原生派发验收。DeepSeek 官方对 V4 Pro 的服务端版本升级不改变 API slug，仍使用 `deepseek-v4-pro`。
