# Changelog

本项目遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/) 格式，
版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## [Unreleased]

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

[Unreleased]: https://github.com/zouwustudio/zouwucode/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/zouwustudio/zouwucode/releases/tag/v1.0.0
