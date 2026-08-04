# 兼容性与安全边界

## 当前支持

- macOS
- Python 3.11+
- Codex CLI 或 ChatGPT 桌面应用已经至少启动一次
- DeepSeek 官方 Responses API
- `deepseek-v4-flash`
- 思考程度 `high`

DeepSeek 官方目前只确认 `deepseek-v4-flash` 支持 Codex。不要提前注册尚未正式支持的型号。

## 配置位置

- Codex 配置：`$CODEX_HOME/config.toml`
- 合并模型目录：`$CODEX_HOME/models-with-deepseek.json`
- 自定义 Agent：`$CODEX_HOME/agents/DeepSeek.toml`
- 管理状态与备份：`$CODEX_HOME/codex-deepseek-subagent/`
- macOS Keychain 服务：`codex-deepseek-api-key`

程序不会修改顶层 `model` 或顶层 `model_provider`，因此 Codex 主任务仍使用用户原来的模型和登录方式。

## API Key

API Key 可以由用户在聊天中提供。管理程序从标准输入读取，不写入命令参数、临时文件、配置文件或测试结果。Key 会立即写入 macOS Keychain，Codex 通过命令型认证读取。

不要在最终回复、日志摘要、异常信息或测试夹具里重复 API Key。

## 配置事务

写入前创建带时间戳的备份。程序先生成候选配置并用 `tomllib` 和 JSON 解析验证，再原子替换目标文件。写入后的 Codex 校验或实时测试失败时，恢复本次事务开始前的文件。

已存在但不属于本 Skill 的冲突配置不会被静默覆盖。完全兼容的现有配置可以被采用，程序会在结果中标记 `adopted_existing`。

## 视觉输入

DeepSeek V4 Flash 当前只接受文本输入。父 Agent 应先检查图片、视频和截图，把必要事实写成文字任务包，再派发给 DeepSeek。子 Agent 不应声称自己看过视觉材料。
