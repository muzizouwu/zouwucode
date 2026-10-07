# Changelog

本项目遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/) 格式，
版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## [Unreleased]

### Added
- **Devin 式 dev 模式**（`zouwucode dev`）：自主 issue → Draft PR 全流程——GitHub API 拉取 issue、git worktree 隔离工作区、自主编码会话、测试验证（失败自动迭代修复）、commit/push/Draft PR；支持 `--queue` 异步队列、`--workers N` 并行、`--watch` 自动认领 `zouwucode:do` 标签 issue
- **成本熔断**：`engine.max_cost_usd` 单任务美元成本上限（dev 模式防失控烧额度）
- 安全边界：`dev/*` 分支白名单（保护分支硬拒绝）、agent 沙箱限定 worktree 内、PR 永远 Draft 需人工 review、失败自动回帖 issue
- **dev 质量门禁**（对标成熟 dev agent，解决边界遗漏 / 约束降性能 / 自验失真三大痛点）：
  - 多层验证管线 `dev/verifiers.py`：lint → typecheck → test(+覆盖率) → security 四层独立门禁，逐层自动探测、未装工具自动跳过，失败按层标注回灌
  - 独立 AI 审查 `dev/reviewer.py`：全新只读会话（plan 模式 + 只读工具白名单 + 零共享上下文）审查 diff，结构化 JSON 结论，request_changes 有界回灌修复，结论写入 PR 正文
  - CI 联动：push 后轮询真实 GitHub Checks，本地过但 CI 挂自动回帖提示（不阻塞，PR 保持 Draft）
  - 自适应成本预算：成本触顶自动翻倍预算续跑同一会话（`budget_escalations` 上限），替代一刀切失败
  - 实现者提示词内置边界自检清单（空值/错误路径/资源/并发/跨平台/兼容/安全）——约束放认知层而非堆砌禁令
- **对标成熟 agent 架构升级**（调研 Devin/OpenHands/Claude Code/SWE-agent 共识模式后的五项落地）：
  - **卡死检测**：引擎动作指纹滑窗（工具+参数+结果哈希），"工具都成功但原地打转"先提醒换思路、再犯熔断——补齐连续错误熔断管不到的死循环形态
  - **评测 harness**（`zouwucode eval`）：真实 agent 栈跑确定性行为断言任务，输出通过率+成本；内置 3 个示例任务，YAML 自定义任务；改进脚手架从此可量化
  - **CodeAct 行动面**：新增 `python_exec` 持久 Python REPL 工具（跨调用保留变量/导入；长度前缀+base64 帧协议；沙箱筛查+破坏性模式拦截+超时重启），复杂多步任务可用代码组合动作
  - **显式 PLAN/REFLECT**：dev 管线实现前增加规划轮（同一会话进前缀），验证失败回灌带反思指令——对齐 PLAN→ACT→OBSERVE→REFLECT 规范控制循环
  - **生命周期钩子**：`extensions.hooks` 注册 pre_tool（非零退出或 `{"block":true}` 拦截并回传原因）/ post_tool（编辑后自动化）钩子，Claude Code 风格确定性治理

## [1.0.0] - 2026-09-09

### Added
- **任务打断（三端统一）**：TUI/WebUI 按 `Esc`（确认弹窗防误触）、CLI 按 `Ctrl+C`；在流式 chunk / 工具轮次 / 工具执行前等安全点生效，约 0.1 秒内停止；中断后可输入「继续」恢复或 `/clear` 放弃
- **引擎安全限制**：`EngineConfig`（max_tool_rounds / turn_timeout / task_timeout / 连续工具错误熔断），杜绝死循环与不可恢复停顿
- **LLM 弹性重试**：429/5xx/网络错误按指数退避自动重试（`max_llm_retries`，默认 2 次）；认证/参数错误不重试；流已开始后不重试（避免 UI 重复渲染）
- **日志落盘**：轮转文件日志 `<data_dir>/logs/zouwucode.log`（5MB × 5），级别经 `log_level` 配置
- **子 Agent 系统**：`task` 工具分解任务并行委派；每个子 Agent 独立引擎与缓存，支持工具白名单、独立超时、主任务级联打断
- **扩展层（MCP/LSP 预留接口）**：`ExtensionHost` 统一生命周期；MCP 服务器工具桥接、LSP 诊断工具，配置即启用、默认零开销
- **CI**：GitHub Actions 矩阵（Python 3.10/3.12 × Ubuntu/Windows）跑全量测试 + 冒烟测试
- 沙箱真实接线：文件工具路径白名单检查、网络/ git 开关、workspace 根设置
- 项目记忆、会话持久化、对话压缩、三层上下文窗口、Plan/Agent/YOLO 三模式
- 9 个内置工具（read/write/edit/ls/glob/bash/git/web_search/web_fetch）
- hello-my-zouwucode 多智能体编排模块（11 Agent + IntentGate + ultrawork）

### Changed
- 引擎主循环由递归重构为迭代式有界循环
- UI 层每轮仅向引擎发送新增消息（修复历史重复膨胀导致的 token 暴涨）
- Shell 工具修复双重可执行名问题；Git 工具改用 shlex 解析并支持超时配置
- 安装脚本（install.ps1 / z.bat）去除硬编码路径，改为脚本位置推导

### Removed
- 未接线的权限邮箱队列、旧 SubAgentPool（被新隔离架构取代）、恒空桩与全部死代码

## [0.x] - 2026-08

- 初始原型：CLI/TUI/Web 三端、Cache-First 引擎、工具系统

[Unreleased]: https://github.com/muzizouwu/zouwucode/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/muzizouwu/zouwucode/releases/tag/v1.0.0
