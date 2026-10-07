# ZOUWUCODE

> DeepSeek-native AI Coding Agent — 为终端而生的编程智能体
>
> 开发团队：走戊工作室（Zouwu Studio）

[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue)](https://python.org)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Tests: 295 passed](https://img.shields.io/badge/tests-295%20passed-green)](tests/)
[![CI](https://github.com/muzizouwu/zouwucode/actions/workflows/ci.yml/badge.svg)](https://github.com/muzizouwu/zouwucode/actions/workflows/ci.yml)

ZOUWUCODE 是一款基于 DeepSeek 深度优化的终端 AI 编程 Agent，整合了 Reasonix 的 Cache-First 引擎、DeepSeek-TUI 的多模式工作流、Deep Code 的多智能体编排，以及 Claude Code 的架构设计理念。

支持 **三种界面**（CLI / TUI / Web UI）和 **三种工作模式**（Plan / Agent / YOLO），提供完整的项目记忆、对话压缩、上下文管理和安全沙箱功能。

---

## 特性一览

| 特性 | 说明 |
|------|------|
| **Cache-First 引擎** | Append-only 对话循环，DeepSeek 缓存命中率 90%+，成本降至约 1/5 |
| **三种界面** | CLI（终端交互）、TUI（Textual 图形界面）、Web UI（浏览器界面） |
| **三种模式** | Plan（只读）、Agent（交互审批）、YOLO（自动执行） |
| **完整工具集** | 文件读写、Shell 执行、Git 操作、Web 搜索 — 9 个内置工具 |
| **项目记忆** | 跨会话持久化，保存目标、决策、状态、摘要 |
| **项目规则** | `.zouwucode/rules.md` 定义项目专属规则，自动注入系统提示词 |
| **技能系统** | `.zouwucode/skills/` 可插拔知识包，`/skill` 命令动态加载/卸载 |
| **推理强度** | 三档可调：low（快速确定）、medium（均衡思考）、max（深度推理） |
| **对话压缩** | 三级压缩策略（lossless / balanced / aggressive） |
| **上下文窗口** | 三层管理（Hot / Warm / Cold），自动滑动归档 |
| **安全沙箱** | 危险命令检测、零宽字符防护、路径白名单 |
| **任务打断** | 三端统一：TUI/WebUI 按 `Esc`（带确认弹窗）、CLI 按 `Ctrl+C`，打断后可「继续」恢复或放弃 |
| **子 Agent 系统** | 主 Agent 通过 `task` 工具分解任务并并行委派：每个子 Agent 独立引擎/缓存，支持工具白名单、独立超时、级联打断 |
| **Devin 式 dev 模式** | `zouwucode dev <issue>` 自主完成 issue→隔离 worktree→实现→验证→Draft PR 全流程；支持队列并行、watch 自动认领、成本熔断 |
| **扩展层（MCP/LSP 预留）** | 统一 Extension 接口：MCP 服务器工具桥接、LSP 诊断工具，配置即启用，零配置零开销 |
| **引擎安全限制** | 工具轮数上限、单请求/单任务超时、连续工具错误熔断 — 杜绝死循环与执行停顿 |
| **会话管理** | 自动保存、恢复、回滚 |
| **hello-my-zouwucode 多智能体编排模块** | 复刻自 OpenCode 生态的 oh-my-opencode：11 专职 Agent + IntentGate 意图门控 + ultrawork 全自主管线 |
| **MCP 协议** | 支持 Model Context Protocol 扩展（经扩展层接入，配置即启用） |
| **便捷启动** | `z` 命令一键启动，PowerShell 安装脚本 |

---

## 快速开始

### 安装（推荐方式 — 全局安装）

```bash
cd zouwucode          # 进入本仓库克隆目录
pip install -e .
```

安装后，在任何目录都可以直接使用 `zouwucode` 命令。

### 配置 API Key

```bash
# 生成默认配置文件
zouwucode --init-config
```

编辑 `zouwucode_data/config.yaml`（或项目根目录 `config.yaml`），填入 [DeepSeek API Key](https://platform.deepseek.com/api_keys)：

```yaml
providers:
  deepseek:
    api_key: "sk-your-deepseek-api-key"
    model: "deepseek-v4-flash"            # 推荐使用 deepseek-v4-flash
    base_url: "https://api.deepseek.com"
```

> **安全提示**：`config.yaml` 含密钥，已在 [.gitignore](.gitignore) 中排除，切勿提交。
> 配置模板见 [config.example.yaml](config.example.yaml)。若密钥曾泄露，请到
> [DeepSeek 控制台](https://platform.deepseek.com/api_keys) 重置。

### 启动

```bash
# CLI 交互模式（默认）
zouwucode

# Web UI 模式（浏览器）
zouwucode --web

# TUI 模式（终端图形界面）
zouwucode --tui

# 如果未全局安装，使用 python -m
python -m zouwucode --web
```

---

## 使用方式

### 三种界面

| 界面 | 启动命令 | 说明 |
|------|----------|------|
| CLI | `zouwucode` | 轻量级终端交互，无需额外依赖 |
| Web UI | `zouwucode --web` | 浏览器界面，`http://127.0.0.1:8080` |
| TUI | `zouwucode --tui` | Textual 终端图形界面，需安装 `textual` |

### 三种模式

| 模式 | 命令 | 快捷键 | 说明 |
|------|------|--------|------|
| Plan | `/plan` | Ctrl+S 循环 | 只读探索，不能修改代码 |
| Agent | `/agent` | Ctrl+S 循环 | 交互审批，工具调用需确认（默认） |
| YOLO | `/yolo` | Ctrl+S 循环 | 自动执行，工具调用无需确认 |

### Devin 式 dev 模式（issue → Draft PR）

把 ZOUWUCODE 变成自主异步工程师：给它一个 GitHub issue，它在隔离的 git worktree 里独立完成实现、跑测试验证，成功后自动创建 **Draft PR**（永远需人工 review 后合并）。

```bash
# 前置：export GITHUB_TOKEN=ghp_xxx（最小权限 contents:write + pull_requests:write）

zouwucode dev https://github.com/you/repo/issues/42   # 处理一个 issue → Draft PR
zouwucode dev "给 README 加安装说明"                    # 自由文本任务（本地分支，不建 PR）
zouwucode dev --queue <issue-url>                      # 提交到任务队列
zouwucode dev --workers 3                              # 3 个 worker 进程并行排空队列
zouwucode dev --watch you/repo                         # 轮询 `zouwucode:do` 标签 issue 自动认领
zouwucode dev --status                                 # 查看队列状态
```

质量门禁：多层验证（lint→typecheck→test+覆盖率→security）+ 独立只读 AI 审查 diff + CI 联动（本地过但真实 CI 挂会提示）+ 自适应成本预算。安全边界：只操作 `dev/*` 分支（保护分支硬拒绝）、agent 被沙箱限定在 worktree 内、任务超时/成本熔断（`engine.max_cost_usd`）、失败自动回帖 issue 请求人工介入。详见 [dev 模式说明](docs/dev模式使用说明.md)。

### 常用命令

| 命令 | 说明 |
|------|------|
| `/help` | 显示帮助信息 |
| `/exit` | 退出程序 |
| `/mode` | 显示当前模式 |
| `/cache` | 显示缓存性能统计 |
| `/sessions` | 列出已保存的会话 |
| `/memory` | 显示项目记忆内容 |
| `/goal` | 显示当前目标 |
| `/goal set <目标>` | 设置新目标 |
| `/decide <决策>` | 记录架构决策 |
| `/context` | 显示上下文窗口统计 |
| `/compress` | 显示压缩器状态 |
| `/reasoning` | 显示/设置推理强度 |
| `/thinking` | 切换流式思考显示 |
| `/agents` | 查看子 Agent 系统与扩展层状态 |
| `/rules` | 查看项目规则 |
| `/skill list` | 列出可用和已加载的技能 |
| `/hello-ultrawork <任务>` | hello-my-zouwucode 全自主管线（也可用 `ultrawork`/`ulw` 消息前缀） |
| `/hello-plan <任务>` | Prometheus 规划（写入 `.hello-my-zouwucode/plans/`，`/plan <任务>` 兼容） |
| `/hello-start-work` | Atlas 执行活动计划（Boulder RESUME/INIT） |
| `/hello-status` | 查看 hello-my-zouwucode 进度与智慧积累 |
| `/hello-agents` | 列出 11 个内置 Agent |
| `/hello-categories` | 列出任务类别 |

---

## 项目记忆

ZOUWUCODE 支持跨会话的项目状态持久化。

```bash
# 初始化项目记忆
python -m zouwucode --init

# 初始化并设置目标
python -m zouwucode --init --goal "开发登录功能"

# 清除项目记忆
python -m zouwucode --forget
```

初始化后，记忆数据存储在 `.zouwucode/memory/` 目录中，包括：
- 项目状态（名称、描述）
- 架构决策记录
- 项目目标
- 会话摘要
- 项目快照

---

## 配置

```bash
# 生成默认配置
python -m zouwucode --init-config

# 查看当前配置
python -m zouwucode --show-config

# 使用自定义配置
python -m zouwucode --config /path/to/config.yaml
```

### 常用配置选项

```bash
# 指定工作模式
python -m zouwucode --mode plan

# 设置压缩级别
python -m zouwucode --compress aggressive

# 调整上下文窗口
python -m zouwucode --max-tokens 1048576 --keep-turns 50

# 指定模型
python -m zouwucode --model deepseek-v4-flash

# 设置推理强度（低/中/高）
python -m zouwucode --reasoning low
python -m zouwucode --reasoning max
```

### 引擎安全限制（config.yaml）

```yaml
engine:
  max_tool_rounds: 25               # 单次任务最多工具轮数
  turn_timeout_seconds: 300         # 单次 LLM 请求超时（秒）
  task_timeout_seconds: 1800        # 单次任务总耗时上限（秒）
  max_consecutive_tool_errors: 3    # 工具连续失败熔断阈值
  max_llm_retries: 2                # 瞬时故障重试次数（0=关闭）
  retry_base_delay_seconds: 1.0     # 指数退避基数（秒）
```

超过任一限制时任务会以 `TurnLimitExceeded` 安全终止（可恢复），防止模型反复
调用失败工具导致的死循环与执行停顿。

---

## 便捷启动（z 命令）

### 方法一：pip 全局安装（推荐）

```bash
# 一次安装，全局使用
cd zouwucode          # 进入本仓库克隆目录
pip install -e .

# 之后在任何目录直接使用
zouwucode
zouwucode --web
zouwucode --tui
```

### 方法二：使用 z.bat

```bash
# 直接运行
z

# 带参数
z --web
z --mode plan
z --tui
```

### 方法三：安装到系统

以管理员身份运行 PowerShell：

```powershell
powershell -ExecutionPolicy Bypass -File install.ps1
```

安装后，打开新终端即可在任何目录使用 `z` 命令。

---

## Web UI API

启动 `--web` 后，可通过 HTTP API 与 ZOUWUCODE 交互：

| 端点 | 方法 | 说明 |
|------|------|------|
| `/` | GET | HTML 页面 |
| `/api/status` | GET | 运行状态（模式、运行中标志、推理强度、缓存统计、上下文、规则、技能） |
| `/api/mode` | POST | 切换模式（`{"mode":"plan"}`） |
| `/api/reasoning` | POST | 设置推理强度（`{"reasoning":"max"}`） |
| `/api/chat` | POST | 发送消息（`{"message":"你好"}`） |
| `/api/interrupt` | POST | 打断当前执行中的任务（等价于界面 ⏹ 按钮 / Esc） |
| `/api/cache` | GET | 缓存性能统计 |
| `/api/rules` | GET | 获取项目规则内容 |
| `/api/skills` | GET | 获取可用和已加载的技能列表 |
| `/api/skills` | POST | 加载/卸载技能（`{"action":"load","name":"python-dev"}`） |
| `/api/sessions` | GET | 获取会话列表 |
| `/api/hello-my-zouwucode` | POST | hello-my-zouwucode 多智能体编排模块（`{"action":"ultrawork\|plan\|start-work\|status", ...}`） |

---

## 运行测试

```bash
# 运行全部测试
python -m pytest tests/ -v

# 测试特定模块
python -m pytest tests/test_compressor.py -v
python -m pytest tests/test_project_memory.py -v
python -m pytest tests/test_sandbox.py -v
python -m pytest tests/test_interrupt.py -v

# 生成覆盖率报告
python -m pytest tests/ --cov=zouwucode --cov-report=term

# 冒烟测试（需已配置 API Key，验证核心工具链真实运行）
python scripts/smoke_test.py
```

---

## 打包为独立可执行文件

```bash
python build.py
```

生成 `dist/ZOUWUCODE.exe`，不依赖系统 Python 环境。

---

## 项目架构

```
zouwucode/                       # 核心包
├── __init__.py                  # 版本信息
├── __main__.py                  # CLI 入口、参数解析
├── config.py                    # 配置管理（Pydantic）
├── project_memory.py            # 持久项目记忆系统
│
├── modules/                     # 可扩展模块系统
│   └── manager.py               # ModuleManager（注册/注销/状态查询 + 三路派发）
│
├── engine/                      # Cache-First LLM 引擎
│   ├── loop.py                  # Append-only 对话循环
│   ├── cache.py                 # 前缀缓存管理
│   └── providers/               # 模型提供商适配器
│       ├── base.py              # 抽象基类
│       ├── deepseek.py          # DeepSeek 优化适配器
│       └── openai.py            # OpenAI 兼容适配器
│
├── tools/                       # 工具系统（9 个内置工具）
│   ├── base.py                  # 工具基类
│   ├── registry.py              # 工具注册中心
│   ├── file_tools.py            # 文件读写编辑搜索
│   ├── shell_tools.py           # Shell 命令执行
│   ├── git_tools.py             # Git 操作
│   └── web_tools.py             # Web 搜索与抓取
│
├── agent/                       # Agent 层
│   ├── coordinator.py           # 工具调用协调器（引擎 ↔ 工具注册中心）
│   └── subagent.py              # 子 Agent 系统（隔离引擎/并行/白名单/级联打断）
│
├── dev/                         # Devin 式自主开发模式（issue → Draft PR）
│   ├── github.py                # GitHub REST 客户端（issue/PR/评论/标签）
│   ├── workspace.py             # git worktree 隔离 + dev/* 分支白名单
│   ├── pipeline.py              # 端到端管线（实现→验证→PR）
│   ├── queue.py                 # SQLite 任务队列（异步托管）
│   └── cli.py                   # dev 子命令路由/worker/watch
│
├── extensions/                  # 扩展层（MCP/LSP 预留接口）
│   ├── host.py                  # ExtensionHost + Extension 基类
│   ├── mcp_ext.py               # MCP 服务器工具桥接
│   └── lsp_ext.py               # LSP 诊断工具
│
├── skills/                      # 技能系统
│   └── manager.py               # .zouwucode/skills/ 加载器
│
├── context/                     # 三层上下文管理
│   ├── memory.py                # MEMORY.md 索引
│   ├── topics.py                # Topic 文件管理
│   ├── transcript.py            # 对话日志
│   ├── compressor.py            # 对话压缩算法
│   └── sliding_window.py        # 滑动窗口
│
├── sandbox/                     # 权限沙箱
│   └── permission.py            # 多层安全检测
│
├── session/                     # 会话管理
│   └── manager.py               # 会话保存/恢复
│
├── mcp/                         # MCP 协议支持
│   └── client.py                # MCP 客户端
│
├── lsp/                         # LSP 集成
│   └── client.py                # LSP 客户端
│
├── tui/                         # 终端界面
│   ├── app.py                   # CLI 主应用
│   ├── textual_app.py           # Textual TUI
│   └── styles.tcss              # TUI 样式表
│
└── webui/                       # Web UI
    └── server.py                # HTTP 服务器

hello_my_zouwucode/              # 多智能体编排模块（复刻自 oh-my-opencode）
├── module.py                    # HelloMyZouwucodeModule 模块接口
├── agents.py                    # 11 个专职 Agent + 系统提示词
├── categories.py                # Category 语义路由表
├── intent_gate.py               # IntentGate 意图分类器
├── boulder.py                   # Boulder 跨会话任务状态
├── notepad.py                   # Notepad 智慧积累
├── planner.py                   # Prometheus 规划器
├── atlas.py                     # Atlas 执行器
└── orchestrator.py              # Sisyphus 主编排器
```

---

## 技术栈

- **语言**: Python 3.10+
- **HTTP 客户端**: httpx
- **配置管理**: Pydantic + PyYAML
- **TUI 框架**: Textual
- **HTML 解析**: BeautifulSoup4
- **测试**: pytest + pytest-asyncio + pytest-cov
- **打包**: PyInstaller

---

## 近期更新

- **子 Agent 系统与扩展层**：新增子 Agent 系统——主 Agent 通过 `task` 工具分解任务并行委派，每个子 Agent 拥有独立引擎/缓存（隔离 PrefixCache），支持工具白名单、独立超时、主引擎级联打断；新增统一 Extension 扩展层预留 MCP/LSP 集成空间（配置即启用，默认零开销）。详见 [子Agent系统与扩展接口说明](docs/子Agent系统与扩展接口说明.md)。
- **任务打断功能（三端统一）**：TUI/WebUI 按 `Esc`（确认弹窗防误触）、CLI 按 `Ctrl+C`，可在任意安全点（流式 chunk / 工具轮次 / 工具执行前）即时打断任务，中断后可输入「继续」从断点恢复或 `/clear` 放弃。详见 [打断功能使用说明](docs/打断功能使用说明.md)。
- **引擎安全限制**：新增 `EngineConfig`（工具轮数上限 / 单请求与单任务超时 / 连续工具错误熔断），引擎循环由递归重构为迭代，彻底解决任务执行死循环与不可恢复停顿。
- **上下文重复膨胀修复**：UI 层每轮仅向引擎发送新增消息（引擎 PrefixCache 已持有完整历史），项目记忆仅在内容变化时重发。
- **代码清理**：删除未接线的权限队列、空桩方法与全部未使用 import；修复 Shell 工具双重可执行名、`--init-config` 路径拼接、WebUI 状态 token 统计等问题；新增 `.gitignore` 并将 `config.yaml`（含密钥）排除出版本库，提供 `config.example.yaml` 模板。
- **Web UI 欢迎横幅修复**：修复 ASCII 艺术字因 CSS 缺 `white-space: pre` 被折叠导致的纵向压缩变形，横幅现完整显示 6 行并保持比例。
- **Web UI 响应式布局**：新增 `@media (max-width: 560px)` 窄屏断点（header 自动换行），长 URL/代码/会话名折行（`overflow-wrap: anywhere`），思考块内部滚动（`max-height: 240px`）。

## 详细文档

完整的使用手册请参阅 [USAGE.md](USAGE.md)，包含：

- 命令行参数详解
- 三种界面模式完整指南
- 三种工作模式说明
- 内置命令参考
- 项目记忆系统详解
- 对话压缩与上下文管理
- Web UI / TUI 使用指南
- 工具系统说明
- 权限沙箱配置
- 会话管理
- 配置详解
- 便捷启动安装
- 打包说明
- 故障排除

专项功能文档：

- [打断功能使用说明](docs/打断功能使用说明.md) — 触发方式、安全点、恢复选项、接口说明
- [子Agent系统与扩展接口说明](docs/子Agent系统与扩展接口说明.md) — 任务分解与并行委派、MCP/LSP 预留接口
- [dev模式使用说明](docs/dev模式使用说明.md) — Devin 式自主 issue → Draft PR 工作流
- [dev模式快速上手](docs/dev模式快速上手.md) — 5 分钟跑通第一个自主任务

hello-my-zouwucode 多智能体编排模块相关文档：

- [hello-my-zouwucode 集成文档](docs/hello-my-zouwucode_集成文档.md) — 复刻范围、适配决策、代码结构
- [hello-my-zouwucode 功能测试报告](docs/hello-my-zouwucode_功能测试报告.md) — 53 项新测试 + 端到端冒烟明细
- [hello-my-zouwucode 使用说明](docs/hello-my-zouwucode_使用说明.md) — 命令、工作流、状态文件详解

---

## 许可

MIT License — 详见 [LICENSE](LICENSE)。

Copyright © 2026 走戊工作室（Zouwu Studio）

## 参与贡献

- 开发环境与规范见 [CONTRIBUTING.md](CONTRIBUTING.md)
- 版本变更记录见 [CHANGELOG.md](CHANGELOG.md)
- 安全漏洞请**私下**报告，方式见 [SECURITY.md](SECURITY.md)
- 运行日志：`zouwucode_data/logs/zouwucode.log`（轮转 5MB×5，级别经 `log_level` 配置）

## 致谢

- [Reasonix](https://github.com/esengine/DeepSeek-Reasonix) — Cache-First 循环设计
- [DeepSeek-TUI](https://github.com/Hmbown/DeepSeek-TUI) — 多模式工作流
- [Deep Code](https://github.com/HKUDS/DeepCode) — 多智能体编排
- [Claude Code](https://anthropic.com/claude-code) — 架构设计参考