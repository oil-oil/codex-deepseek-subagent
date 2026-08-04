---
name: codex-deepseek-subagent
description: 自动把 DeepSeek 配置成 Codex 原生子 Agent，并完成密钥保存、模型目录合并、Provider 与角色注册、直连测试、spawn_agent 路由验收、诊断、修复和卸载。用户提到“在 Codex 里配置 DeepSeek 子 Agent”“让原生子 Agent 使用 DeepSeek”“检查或修复 DeepSeek Agent 配置”时必须使用；普通 DeepSeek API 编程问题或已经配置完成后的日常编码任务不要触发。
compatibility: macOS；需要已安装并至少启动过一次 Codex CLI 或 ChatGPT 桌面应用，Python 3.11+。
---

# Codex DeepSeek Subagent

把 DeepSeek 配置为 Codex 的原生自定义子 Agent。配置是程序化闭环：Agent 负责理解意图、传入凭据和汇报结果，确定性文件操作全部交给管理程序。

## 核心边界

- 始终调用 `scripts/codex_deepseek.py`；不要手动编辑用户的 TOML、JSON、Agent 文件或钥匙串。
- 用户明确要求配置、修复、测试、停用或卸载时，直接执行对应流程，不把终端步骤交给用户。
- 可以在聊天中索要 DeepSeek API Key。收到后不要复述、回显或写入临时文件；通过管理程序的标准输入传入。
- 用户已经在当前消息或上下文提供 Key 时直接使用，不重复索要。
- 程序能安全处理的状态不要让用户选择。只有现有配置冲突、即将覆盖非本 Skill 管理的文件，或删除钥匙串凭据时才暂停。
- DeepSeek V4 Flash 是纯文本模型。不要让它检查图片、视频、截图或其他视觉输入；由父 Agent 完成视觉识别，再把结论作为文字交给子 Agent。
- 配置成功以实际子任务元数据为准，不能以子 Agent 自述模型或返回测试口令代替。

## 程序入口

```text
python3 <skill-dir>/scripts/codex_deepseek.py <command> --json
```

支持的命令：

- `status`：只读检查配置、模型目录、钥匙串和客户端能力。
- `setup`：配置并验证；缺少密钥时返回 `credential_missing`。
- `test`：执行 DeepSeek 直连和原生子 Agent 路由测试。
- `repair`：重新应用本 Skill 管理的配置并验证。
- `disable`：停用本 Skill 创建的 DeepSeek 角色，保留 Provider、模型目录和凭据。
- `uninstall`：移除本 Skill 管理的配置。只有用户明确要求删除凭据时才传 `--remove-credential`。

默认使用当前 `CODEX_HOME`；仅在用户明确指定其他 Codex Home 时传 `--codex-home`。

## 配置流程

1. 先运行 `status --json`，读取程序返回的状态，不靠文件名猜测。
2. 用户要求配置或修复时运行 `setup --json` 或 `repair --json`。
3. 若返回 `credential_missing`，在聊天中简洁索要 DeepSeek API Key。
4. 收到 Key 后启动带 `--api-key-stdin` 的命令，并仅通过标准输入发送 Key。
5. 等待程序完成备份、模型目录合并、Provider 注册、Agent 注册、语法验证和测试。
6. 若返回 `restart_required: true`，说明当前任务不会热加载新角色；新任务会使用它。不要把重启描述成配置失败。
7. 最终只汇报状态、实际 Provider、模型、思考程度、角色和备份位置。不要输出密钥或原始事件日志。

## 状态处理

- `ready`：配置和实际路由均已通过。
- `configured`：静态配置完整，但尚未完成实时路由测试。
- `credential_missing`：索要 API Key 后继续原流程。
- `restart_required`：配置已完成，当前任务未热加载角色。
- `conflict`：报告具体冲突文件和字段，等待用户决定是否替换。
- `unsupported`：报告缺少的系统能力或最低版本，不要尝试手工绕过。
- `failed`：先读取结构化 `errors`；程序已自动回滚时明确说明，不再手改配置。

## 日常调用

这个 Skill 只负责配置和维护。配置完成后的普通编码、探索、实现、评审和验证任务由主 Agent 使用原生：

```text
spawn_agent(agent_type="DeepSeek", fork_turns="none", ...)
```

若当前任务尚未加载 `DeepSeek` 角色，使用默认 Codex 子 Agent，并提醒新建任务后生效。不要为日常任务重复运行 `setup`。

## 验收标准

只有管理程序确认以下元数据时才称为真实 DeepSeek 原生子 Agent：

```text
model_provider = deepseek
model = deepseek-v4-flash
reasoning_effort = high
agent_role = DeepSeek
```

更详细的兼容性和安全说明见 [references/compatibility.md](references/compatibility.md)。
