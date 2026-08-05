<p align="center">
  <img src="./assets/readme/hero.svg" width="100%" alt="codex-deepseek-subagent：将 DeepSeek 注册为 Codex 原生子 Agent">
</p>

把 `deepseek-v4-flash` 注册为 Codex 原生自定义子 Agent，并验证实际派发路由。管理程序只负责配置和验收；配置完成后的普通编码任务由当前主 Agent 直接派发。

## 适用范围

这个 Skill 只用于：

- 首次配置、状态检查和实时测试；
- 父模型变化后的修复；
- 停用或卸载 DeepSeek 配置。

普通的编码、探索、实现、评审和验证任务不应重复运行配置流程。

<p align="center">
  <img src="./assets/readme/workflow.svg" width="100%" alt="DeepSeek 子 Agent 的配置和验证流程">
</p>

## 快速开始

要求：macOS 或 Windows、Python 3.11+、ChatGPT/Codex 桌面应用，以及 DeepSeek 官方 API Key。

1. 全局安装 Skill：

```bash
npx skills add oil-oil/codex-deepseek-subagent -g -y
```

2. 重启桌面应用并新建任务，让 Skill 生效。

3. 在新任务中发出配置请求：

```text
帮我把 DeepSeek 配置成 Codex 的原生子 Agent。
```

4. Codex 会先检查状态；缺少凭据时再索要 API Key，通过标准输入保存到系统凭据库，然后自动配置并验收。

5. 看到 `status: ready` 后，再重启桌面应用并新建任务。此后可直接说：

```text
用 DeepSeek 子 Agent 检查这个项目。
```

配置成功后，角色文件位于 `$CODEX_HOME/agents/DeepSeek.toml`；默认 `CODEX_HOME` 为 `~/.codex`。

## 使用与兼容性

- 日常任务只能由主 Agent 直接调用 `spawn_agent(agent_type="DeepSeek", fork_turns="none")`。
- 配置与验收只使用桌面应用内置运行时；版本仅作诊断，实际能力以真实派发结果为准。
- 父模型从当前配置读取；切换父模型后运行 `repair`。
- DeepSeek 只处理文本。图片、视频、截图等视觉输入必须由父 Agent 先识别并整理成文字。
- 当前工具若不认识 `DeepSeek` 角色，只提示用户打开新任务或重启 Codex；不得用脚本或 `codex exec` 代做用户任务。

v1/v2 路由原因、配置位置和回滚规则见 [兼容性说明](codex-deepseek-subagent/references/compatibility.md)。

## 管理命令

管理命令由 Skill 按需调用：

macOS：

```bash
python3 codex-deepseek-subagent/scripts/codex_deepseek.py status --json
python3 codex-deepseek-subagent/scripts/codex_deepseek.py setup --api-key-stdin --json
python3 codex-deepseek-subagent/scripts/codex_deepseek.py test --json
python3 codex-deepseek-subagent/scripts/codex_deepseek.py repair --json
python3 codex-deepseek-subagent/scripts/codex_deepseek.py disable --json
python3 codex-deepseek-subagent/scripts/codex_deepseek.py uninstall --json
```

Windows：

```powershell
py -3 codex-deepseek-subagent\scripts\codex_deepseek.py status --json
py -3 codex-deepseek-subagent\scripts\codex_deepseek.py setup --api-key-stdin --json
py -3 codex-deepseek-subagent\scripts\codex_deepseek.py test --json
py -3 codex-deepseek-subagent\scripts\codex_deepseek.py repair --json
py -3 codex-deepseek-subagent\scripts\codex_deepseek.py disable --json
py -3 codex-deepseek-subagent\scripts\codex_deepseek.py uninstall --json
```

管理程序会自动寻找桌面应用内置运行时。Windows 自动发现失败时，可以通过 `CODEX_DESKTOP_BIN` 指定 `codex.exe`。

`setup` 或 `test` 会创建隔离验收会话；这不是日常任务的替代入口。验收必须同时满足数据库路由元数据与子 Agent 返回口令：

```text
model_provider = deepseek
model = deepseek-v4-flash
reasoning_effort = high
agent_role = DeepSeek
```

```text
NATIVE_DEEPSEEK_OK
```

不能只相信子 Agent 的自述。

## 安全与回滚

- API Key 只通过标准输入传入；macOS 保存到 Keychain，Windows 保存到 Credential Manager。配置、临时文件和测试输出均不包含密钥。
- 配置和模型目录写入前会创建备份；解析或实时测试失败会恢复本次事务。
- 不修改主任务的顶层模型或登录方式。

Skill 执行规则见 [SKILL.md](codex-deepseek-subagent/SKILL.md)。

## 品牌素材

Codex 图标来自官方 ChatGPT 应用资源，DeepSeek 图标来自 DeepSeek 官方 CDN。相关商标与品牌素材归各自权利人所有，本项目与 OpenAI 或 DeepSeek 无隶属或官方背书关系。

## 开发验证

```bash
python3 scripts/test_manager.py
python3 scripts/build_readme_assets.py
```

## License

[MIT](./LICENSE)
