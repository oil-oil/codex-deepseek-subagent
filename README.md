<p align="center">
  <img src="./assets/readme/hero.svg" width="100%" alt="codex-deepseek-subagent：把 DeepSeek 配置成 Codex 原生子 Agent">
</p>

<p align="center">
  <a href="#安装">安装</a> ·
  <a href="#使用">使用</a> ·
  <a href="#配置内容">配置内容</a> ·
  <a href="#安全限制">安全限制</a>
</p>

## 功能

`codex-deepseek-subagent` 将 `deepseek-v4-flash` 注册为 Codex 原生自定义子 Agent，并通过子任务会话元数据验证实际使用的 Provider、模型、思考程度和角色。

Python 管理程序负责凭据保存、配置修改、测试和回滚。Agent 不直接编辑配置文件或钥匙串。

<p align="center">
  <img src="./assets/readme/workflow.svg" width="100%" alt="从环境检查到元数据验收的自动配置流程">
</p>

## 安装

```bash
npx skills add oil-oil/codex-deepseek-subagent -g -y
```

运行环境：

- macOS
- Python 3.11+
- Codex CLI 或 ChatGPT 桌面应用已至少启动一次
- DeepSeek 官方 API Key

## 使用

首次配置时告诉 Agent：

```text
帮我把 DeepSeek 配置成 Codex 的原生子 Agent。
```

如果本机没有 DeepSeek 凭据，Agent 会索要 API Key，并通过标准输入传给管理程序。

配置完成后，可以直接指定 DeepSeek 子 Agent：

```text
用 DeepSeek 子 Agent 检查这个模块的错误处理。
```

这个 Skill 只处理首次配置、检查、修复、停用和卸载。配置验收成功后，Codex 会保留原生 `DeepSeek` 角色；日常任务由主 Agent 直接调用，不会重复运行配置流程。

## 配置内容

管理程序执行以下操作：

1. 检查 Codex 版本、配置和当前模型目录。
2. 把 API Key 写入 macOS Keychain。
3. 从 DeepSeek 官方安装脚本读取模型元数据，但不执行远程脚本。
4. 合并模型目录，保留原有 OpenAI 模型。
5. 注册 `deepseek` Provider 和 `DeepSeek` 自定义 Agent。
6. 运行 DeepSeek 直连测试。
7. 运行原生 `spawn_agent(agent_type="DeepSeek")` 测试。
8. 从 Codex 会话数据库确认实际 Provider、模型、思考程度和角色。

配置通过以下会话元数据验收：

```text
model_provider = deepseek
model = deepseek-v4-flash
reasoning_effort = high
agent_role = DeepSeek
```

## 管理命令

以下命令供 Skill 调用：

```bash
python3 codex-deepseek-subagent/scripts/codex_deepseek.py status --json
python3 codex-deepseek-subagent/scripts/codex_deepseek.py setup --api-key-stdin --json
python3 codex-deepseek-subagent/scripts/codex_deepseek.py test --json
python3 codex-deepseek-subagent/scripts/codex_deepseek.py repair --json
python3 codex-deepseek-subagent/scripts/codex_deepseek.py disable --json
python3 codex-deepseek-subagent/scripts/codex_deepseek.py uninstall --json
```

## 安全限制

- API Key 只通过标准输入传入，并保存到 macOS Keychain；不会写入 `config.toml`、模型目录、临时文件或测试结果。
- 修改配置前会创建备份。新配置和模型目录通过解析验证后才会替换原文件。
- 实时测试失败时，程序会回滚本次修改。
- 不修改 Codex 主任务的顶层模型或登录方式。
- DeepSeek V4 Flash 当前只接受文本；图片、视频和截图由父 Agent 识别并整理成文字后再派发。
- 如果发现不兼容的现有配置，程序会停止并报告冲突。

## 品牌素材

Codex 图标来自官方 ChatGPT 应用资源，DeepSeek 图标来自 DeepSeek 官方 CDN。

相关商标与品牌素材归各自权利人所有，本项目与 OpenAI 或 DeepSeek 无隶属或官方背书关系。

## 开发与验证

```bash
python3 scripts/test_manager.py
python3 scripts/build_readme_assets.py
```

## License

[MIT](./LICENSE)
