# 兼容性与安全边界

## 支持范围

- macOS
- Windows
- Python 3.11+
- ChatGPT/Codex 桌面应用至少启动过一次
- DeepSeek 官方 Responses API
- `deepseek-flash`（默认且唯一注册的模型，当前为 DeepSeek-V4.1-Flash-1017）
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

**当前 Codex 官方不支持本方案依赖的角色级 Provider 覆盖。** [官方文档](https://learn.chatgpt.com/docs/agent-configuration/subagents)规定子 Agent 继承父 Provider，角色文件中的 `model_provider` 不会覆盖它。本仓库保留历史兼容流程，不修改 Codex 内核，也不保证新版客户端可用。

`status` 和跳过实时验收的安装结果会返回 `native_routing_verified: false` 以及兼容性提示。只有真实子线程的 Provider、模型、角色、思考程度和返回口令全部匹配，才返回 `ready`；实际继承父 Provider 时返回 `native_provider_inherited`。直连成功不是原生派发成功的证据。

检查和验收优先使用桌面应用内置运行时。Windows 无法从常见安装位置发现运行时时，会尝试 PATH 中的 `codex.exe`；仍无法发现时需要通过 `CODEX_DESKTOP_BIN` 明确指定。版本号仅作诊断；兼容性由模型目录解析、DeepSeek 直连、原生派发、返回口令和数据库元数据共同决定。

父模型从桌面当前配置动态读取。管理程序保留历史 v1 兼容设置：把 `features.multi_agent_v2` 设为 `false`，并把该父模型的 `multi_agent_version` 固定为 `v1`，用于旧运行时的明文任务派发；这些设置不能解除当前客户端的 Provider 继承限制。父模型变化后运行 `repair`。

日常任务只允许主 Agent 直接调用：

```text
spawn_agent(agent_type="DeepSeek", fork_turns="none", ...)
```

如果当前工具 schema 不认识 `DeepSeek`，只提示打开新任务或重启 Codex。不要用管理脚本或 `codex exec` 代做用户任务。

`setup` 或 `test` 会通过桌面内置运行时创建隔离验收会话。验收证据必须来自两处：

1. `$CODEX_HOME/state_*.sqlite` 中包含对应子线程的 `threads` 表元数据：

   ```text
   model_provider = deepseek
   model = deepseek-flash
   reasoning_effort = high
   agent_role = DeepSeek
   ```

2. 子 Agent 返回精确口令：`NATIVE_DEEPSEEK_OK`。

只有元数据和口令同时匹配，才能称为真实的 DeepSeek 原生子 Agent。子 Agent 的自述不能替代数据库记录。

## API Key

API Key 由用户在本机固定页面保存，再由可信包装器注入；不要要求用户发到聊天。标准输入仅供显式选择的兼容入口，不把密钥拼到命令参数、临时文件、配置文件或测试结果。macOS 将密钥保存到 Keychain，Windows 将密钥保存到 Credential Manager。

不要在最终回复、日志摘要、异常信息或测试夹具中重复 API Key。

## 配置事务

写入前创建带时间戳的备份。程序使用进程锁避免并发修改，先生成候选配置并用 TOML、JSON 解析验证，再原子替换目标文件。写入、卸载或实时测试失败时，恢复本次事务开始前的文件。

已存在但不属于本 Skill 的冲突配置不会被静默覆盖；完全兼容的现有配置可以被采用，并在结果中标记 `adopted_existing`。

## 视觉输入

Flash 1017 模型本身支持图像输入，但本集成未对历史跨 Provider 视觉路由进行验收，仍仅派发文本。父 Agent 必须先检查图片、视频和截图，把必要事实写成文字任务包；没有收到图像的子 Agent 不应声称自己看过视觉材料。

## 模型与迁移

- 首次配置不指定 `--model` 时默认使用 `deepseek-flash`，不再展示模型选择菜单。
- 旧名称 `deepseek-v4-flash` 在命令行和受管 manifest 中会归一化为 `deepseek-flash`。迁移前仍检查 Agent 文件的受管哈希，拒绝覆盖用户修改。
- 官方脚本仅下载并解析 JSON heredoc，不执行 shell 代码；兼容旧的 `$TMP_MODELS` 与新的 `$1` 重定向格式。
- 合并目录只注册 Flash，移除旧 Flash 别名以及旧 manifest 声明的受管模型条目，保留其他未受管模型。备份、失败回滚和卸载恢复逻辑保持原样。

实时验收会消耗父模型和 DeepSeek 的额度。离线测试只使用模拟凭据与模拟响应，不能证明实际服务额度或跨 Provider 路由可用。
