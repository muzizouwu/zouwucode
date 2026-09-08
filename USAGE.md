# ZOUWUCODE 使用手册

> DeepSeek-native AI Coding Agent — 完整功能指南

---

## 目录

1. [快速开始](#1-快速开始)
2. [命令行参数](#2-命令行参数)
3. [三种界面模式](#3-三种界面模式)
4. [三种工作模式](#4-三种工作模式)
5. [内置命令](#5-内置命令)
6. [项目记忆系统](#6-项目记忆系统)
7. [项目规则与技能系统](#7-项目规则与技能系统)
8. [对话压缩与上下文管理](#8-对话压缩与上下文管理)
9. [推理强度控制](#9-推理强度控制)
10. [Web UI 使用指南](#10-web-ui-使用指南)
11. [TUI 终端界面使用指南](#11-tui-终端界面使用指南)
12. [工具系统](#12-工具系统)
13. [权限沙箱](#13-权限沙箱)
14. [会话管理](#14-会话管理)
15. [配置详解](#15-配置详解)
16. [便捷启动安装](#16-便捷启动安装)
17. [打包为独立可执行文件](#17-打包为独立可执行文件)
18. [故障排除](#18-故障排除)
19. [多智能体编排（hello-my-zouwucode）](#19-多智能体编排hello-my-zouwucode)

---

## 1. 快速开始

### 1.1 安装（推荐方式 — 全局安装）

```bash
cd zouwucode  # 进入本仓库克隆目录
pip install -e .
```

安装后，在任何目录都可以直接使用 `zouwucode` 命令。

如果使用 TUI 界面，还需安装：

```bash
pip install textual rich
```

如果运行测试，还需安装：

```bash
pip install pytest pytest-asyncio pytest-cov
```

### 1.2 配置 API Key

```bash
# 生成默认配置文件
zouwucode --init-config
```

编辑配置文件（位于 `zouwucode_data/config.yaml`），填入 API Key：

```yaml
providers:
  deepseek:
    api_key: "sk-your-deepseek-api-key"
    model: "deepseek-v4-flash"            # 推荐使用 deepseek-v4-flash
    base_url: "https://api.deepseek.com"
```

### 1.3 启动

```bash
# 交互式 CLI 模式（默认）
zouwucode

# Web UI 模式（浏览器界面）
zouwucode --web

# TUI 模式（终端图形界面）
zouwucode --tui

# 如果未全局安装，使用 python -m
python -m zouwucode --web
```

---

## 2. 命令行参数

### 完整参数列表

| 参数 | 简写 | 说明 | 默认值 |
|------|------|------|--------|
| `--config` | `-c` | 配置文件路径 | — |
| `--version` | `-v` | 显示版本号 | — |
| `--mode` | `-m` | 工作模式：plan/agent/yolo | agent |
| `--model` | — | 指定模型名称 | deepseek-v4-flash |
| `--provider` | — | LLM 提供商 | deepseek |
| `--api-key` | — | API Key | — |
| `--tui` | — | 启动 Textual TUI | — |
| `--web` | — | 启动浏览器 UI | — |
| `--port` | — | Web UI 端口 | 8080 |
| `--init` | — | 初始化项目记忆 | — |
| `--forget` | — | 清除项目记忆 | — |
| `--goal` | — | 设置持久目标 | — |
| `--compress` | — | 压缩级别：lossless/balanced/aggressive/off | balanced |
| `--max-tokens` | — | 最大上下文 token 数 | 1048576 |
| `--keep-turns` | — | 保留最近对话轮次 | 50 |
| `--reasoning` | — | 推理强度：low/medium/max | medium |
| `--skill` | — | 预加载技能（可多次使用，如 `--skill python-dev --skill react`） | — |
| `--init-config` | — | 生成默认配置文件 | — |
| `--show-config` | — | 显示当前配置 | — |

### 示例

```bash
# 以 plan 模式启动
zouwucode --mode plan

# 以 yolo 模式启动（自动执行）
zouwucode --mode yolo

# 启动 Web UI 并指定端口
zouwucode --web --port 3000

# 开启激进压缩
zouwucode --compress aggressive

# 设置推理强度（低/中/高）
zouwucode --reasoning low
zouwucode --reasoning max

# 初始化项目记忆并设置目标
zouwucode --init --goal "开发登录功能"

# 查看当前配置
zouwucode --show-config

# 预加载技能包
zouwucode --skill python-dev --skill security-review

# 使用 z 命令快速启动
z
z --web
z --mode plan
```

---

## 3. 三种界面模式

### 3.1 CLI 模式（默认）

```bash
zouwucode
```

CLI 模式是默认的交互式终端模式。启动后进入命令行提示符：

```
╔══════════════════════════════════════════════════╗
║               Z O U W U C O D E                 ║
║         DeepSeek-native AI Coding Agent         ║
║               Version 1.0.0                     ║
╚══════════════════════════════════════════════════╝

  Commands: /help, /plan, /agent, /yolo, /exit, /cache, /sessions, /mode, /memory, /goal, /compress, /reasoning, /context, /decide, /clear, /hello-ultrawork, /hello-start-work, /hello-status, /hello-agents, /hello-categories
  Shortcuts: Ctrl+S=Cycle Mode  Ctrl+R=Reasoning  Ctrl+L=Clear

ZOUWUCODE > _
```

特点：
- 轻量级，无需额外依赖
- 支持所有命令和快捷键
- 显示缓存命中率和上下文统计

### 3.2 Web UI 模式

```bash
zouwucode --web
# 或指定端口
zouwucode --web --port 8080
```

启动后，在浏览器中打开 `http://127.0.0.1:8080`。

Web UI 界面包括：
- **顶部标题栏**：显示应用名称和版本
- **模式切换栏**：Agent / Plan / YOLO 三个按钮
- **聊天区域**：显示消息历史，支持用户消息、助手回复、思考过程
- **输入框**：底部输入区域，Ctrl+Enter 发送
- **状态栏**：显示缓存命中率、成本统计

API 端点：

| 端点 | 方法 | 说明 |
|------|------|------|
| `/` | GET | HTML 页面 |
| `/api/status` | GET | 运行状态（模式、推理强度、缓存统计、上下文、规则、技能） |
| `/api/mode` | POST | 切换模式（`{"mode":"plan"}`） |
| `/api/reasoning` | POST | 设置推理强度（`{"reasoning":"max"}`） |
| `/api/chat` | POST | 发送消息（`{"message":"你好"}`） |
| `/api/interrupt` | POST | 打断当前执行中的任务 |
| `/api/rules` | GET | 获取项目规则内容 |
| `/api/skills` | GET | 获取可用和已加载的技能列表 |
| `/api/skills` | POST | 加载/卸载技能（`{"action":"load","name":"python-dev"}`） |
| `/api/sessions` | GET | 获取会话列表 |
| `/api/cache` | GET | 获取缓存统计 |
| `/api/hello-my-zouwucode` | POST | hello-my-zouwucode 多智能体编排（`{"action":"ultrawork\|plan\|start-work\|status", ...}`） |

### 3.3 TUI 模式

```bash
zouwucode --tui
```

TUI 模式基于 Textual 框架，提供丰富的终端图形界面：

- 分栏布局：聊天视图 + 输入框 + 状态栏
- 快捷键操作：Ctrl+S 循环切换模式，Ctrl+R 循环推理强度，Ctrl+L 清屏
- 彩色消息：用户消息蓝色，助手回复绿色，思考过程灰色斜体
- 实时状态栏：显示当前模式、目标、缓存命中率、上下文统计
- 完整的命令系统

---

## 4. 三种工作模式

### 4.1 Plan 模式（只读探索）

```bash
# 启动时指定
zouwucode --mode plan

# 运行时切换
/plan
# 或 Ctrl+S 循环
```

特点：
- 只读模式，不能修改任何文件
- 适合浏览代码库、分析问题、制定方案
- 所有工具调用受限

### 4.2 Agent 模式（交互审批，默认）

```bash
# 启动时指定（默认）
zouwucode --mode agent

# 运行时切换
/agent
# 或 Ctrl+S 循环
```

特点：
- 可执行所有操作，但每次工具调用需要用户确认
- 安全可控，适合日常开发

### 4.3 YOLO 模式（自动执行）

```bash
# 启动时指定
zouwucode --mode yolo

# 运行时切换
/yolo
# 或 Ctrl+S 循环
```

特点：
- 所有工具调用自动执行，无需确认
- 适合明确的、可重复的任务
- 高风险操作会被权限沙箱拦截

---

## 5. 内置命令

### 基本命令

| 命令 | 说明 |
|------|------|
| `/help` | 显示帮助信息 |
| `/exit` | 退出 ZOUWUCODE |
| `/clear` | 清屏 |
| `/mode` | 显示当前模式 |

### 模式切换

| 命令 | 快捷键 | 说明 |
|------|--------|------|
| `/plan` | Ctrl+S 循环 | 切换到 Plan 模式 |
| `/agent` | Ctrl+S 循环 | 切换到 Agent 模式 |
| `/yolo` | Ctrl+S 循环 | 切换到 YOLO 模式 |

### 信息查询

| 命令 | 说明 |
|------|------|
| `/cache` | 显示缓存性能统计 |
| `/sessions` | 列出已保存的会话 |
| `/memory` | 显示项目记忆上下文 |
| `/goal` | 显示当前目标 |
| `/compress` | 显示压缩器状态 |
| `/reasoning` | 显示/设置推理强度 |
| `/reasoning <level>` | 设置推理强度：low/medium/max |
| `/context` | 显示上下文窗口统计 |

### 项目管理

| 命令 | 说明 |
|------|------|
| `/goal set <目标>` | 设置新的项目目标 |
| `/decide <决策>` | 记录一条架构决策 |
| `/memory` | 查看项目记忆内容 |
| `/rules` | 查看项目规则 (.zouwucode/rules.md) |
| `/rules edit` | 编辑项目规则 |
| `/skill list` | 列出可用和已加载的技能 |
| `/skill load <name>` | 加载技能 |
| `/skill unload <name>` | 卸载技能 |

### 多智能体编排（hello-my-zouwucode）

| 命令 | 说明 |
|------|------|
| `ultrawork <任务>` / `ulw <任务>` | 消息前缀触发全自主管线（意图分类 → 规划 → 执行） |
| `/hello-ultrawork <任务>` | 显式全自主管线 |
| `/hello-plan <任务>` | Prometheus 规划（`/plan <任务>` 兼容；无参数时保持"切换 plan 模式"） |
| `/hello-start-work` | Atlas 执行活动计划（有 Boulder 状态则 RESUME，否则 INIT） |
| `/hello-status` | 查看 Boulder 进度 + Notepad 摘要 + Agent/Category 数量 |
| `/hello-agents` | 列出 11 个专职 Agent |
| `/hello-categories` | 列出 11 个任务分类 |

---

## 6. 项目记忆系统

项目记忆系统是 ZOUWUCODE 的核心功能之一，它实现跨会话的状态持久化。

### 6.1 初始化项目记忆

```bash
# 在当前目录初始化
zouwucode --init

# 初始化并设置目标
zouwucode --init --goal "开发核心功能"
```

### 6.2 存储结构

初始化后，会在当前目录创建 `.zouwucode/memory/` 目录：

```
.zouwucode/
└── memory/
    ├── project_state.json   # 项目状态（名称、描述等）
    ├── decisions.json       # 架构决策记录
    ├── goals.json           # 项目目标
    ├── summaries.json       # 会话摘要
    └── snapshots/           # 项目快照（可选）
```

### 6.3 管理记忆

```bash
# 查看项目记忆
/memory

# 设置目标
/goal set 开发新功能

# 记录决策
/decide 采用微服务架构

# 查看当前目标
/goal

# 清除所有记忆
zouwucode --forget
```

### 6.4 记忆内容自动加载

每次启动时，项目记忆会自动加载到系统提示词中，包括：
- 项目描述和目标
- 最近的架构决策
- 之前的会话摘要
- 项目文件统计

---

## 7. 项目规则与技能系统

ZOUWUCODE 提供两种方式让用户自定义 AI 的行为：**项目规则**和**技能包**。两者都是纯 `.md` 文件格式，放在 `.zouwucode/` 目录下。

### 7.1 项目规则（.zouwucode/rules.md）

`rules.md` 始终自动加载，注入到系统提示词中，适合定义项目级别的通用约束。

```bash
# 查看当前规则
/rules

# 编辑规则（用记事本打开）
/rules edit
```

**示例 `.zouwucode/rules.md`：**

```markdown
# Project Rules

1. 所有新增 API 端点必须添加 OpenAPI 文档注释。
2. 不允许使用 `any` 类型，必须明确类型定义。
3. 测试覆盖率必须 ≥ 80%。
4. 优先使用 Composition 而非 Inheritance。
```

### 7.2 技能包（.zouwucode/skills/*.md）

技能包是**可插拔的** `.md` 知识包。每个文件定义一个特定的能力、规范或工作流。通过 `/skill` 命令动态加载/卸载。

**目录结构：**

```
.zouwucode/
├── rules.md              # 项目规则（始终加载，可选）
└── skills/
    ├── webui-layout-fixes.md       # WebUI 布局/样式修复经验
    ├── module-development.md       # ZOUWUCODE 可扩展模块开发指南
    ├── opencode-ui-alignment.md    # opencode 风格 UI 对齐指南
    ├── three-end-verification.md   # 三端回归验证流程
    └── rename-migration.md         # 模块重命名与迁移指南
```

**使用方式：**

```bash
# 启动时预加载（可多次使用）
zouwucode --skill webui-layout-fixes --skill module-development

# 运行时管理
/skill list              # 列出可用和已加载的技能
/skill load webui-layout-fixes   # 加载技能
/skill unload react-dev  # 卸载技能
```

**示例技能包 `python-dev.md`：**

```markdown
# Python Development Conventions

- 使用 `ruff` 进行代码格式化，配置见 pyproject.toml
- 类型注解必须完整，使用 `from __future__ import annotations`
- 异常处理使用具体的异常类型，禁止捕获裸 `Exception`
- 所有公共函数必须写 docstring（Google Style）
- 异步函数优先于同步阻塞调用
```

### 7.3 加载机制

规则和技能在会话初始化时（`initialize_session()`）注入到系统提示词中：

```
You are ZOUWUCODE, an AI coding agent running in the terminal.
...
<project_rules>
1. 所有新增 API 端点必须添加 OpenAPI 文档注释。
</project_rules>

=== Loaded Skills ===
<skill name="python-dev">
# Python Development Conventions
- 使用 `ruff` 进行代码格式化
</skill>
```

修改规则或技能后，需要**重启会话**才能生效（新消息自动开始新会话）。

---

## 8. 对话压缩与上下文管理

### 8.1 对话压缩器

支持三级压缩策略：

| 级别 | 说明 | 行为 |
|------|------|------|
| `lossless` | 无损 | 仅移除空消息，保留所有内容 |
| `balanced` | 平衡（默认） | 保留系统提示、决策、文件内容，压缩长对话 |
| `aggressive` | 激进 | 对长助手回复进行摘要，大幅压缩上下文 |
| `off` | 关闭 | 不压缩 |

```bash
# 启动时指定
zouwucode --compress aggressive

# 运行时查看
/compress
```

### 8.2 上下文窗口

三层上下文管理：

- **Hot 层**：始终保留（系统提示词、项目记忆）
- **Warm 层**：最近对话轮次，保留最近 N 轮（默认 50）
- **Cold 层**：历史对话，自动滑动归档

```bash
# 配置
zouwucode --max-tokens 1048576 --keep-turns 50

# 运行时查看
/context
```

---

## 9. 推理强度控制

推理强度（Reasoning Intensity）控制模型在回答问题前的思考深度，灵感来自 DeepSeek V4 的 `reasoning_effort` 参数和 DeepCode 的三档优化策略。

### 9.1 三档说明

| 级别 | DeepSeek 映射 | 特点 | 适用场景 |
|------|---------------|------|----------|
| `low` | 关闭推理 | 快速响应、确定性输出、低延迟、低成本 | 简单问答、文件操作、代码格式化、快速迭代 |
| `medium` | `reasoning_effort="high"` | 均衡思考，默认级别，质量与速度兼顾 | 日常开发、代码审查、常规调试 |
| `max` | `reasoning_effort="max"` | 深度推理，长链思考，可产生 384K 推理 token | 复杂架构设计、算法难题、多步 Agent 任务 |

### 9.2 启动时设置

```bash
# 低强度（快速确定）
zouwucode --reasoning low

# 中强度（均衡思考，默认）
zouwucode --reasoning medium

# 高强度（深度推理）
zouwucode --reasoning max
```

### 9.3 运行时切换

```bash
# 在 CLI/TUI 中设置
/reasoning         # 查看当前强度
/reasoning low     # 切换为低强度
/reasoning max     # 切换为高强度
```

### 9.4 技术实现

推理强度通过 `reasoning_effort` 参数传递给 DeepSeek V4 API：

- **low**：不发送 `reasoning_effort` 参数，关闭 `stream_options`，模型以非思考模式快速响应
- **medium**：发送 `reasoning_effort="high"`，启用标准思考模式
- **max**：发送 `reasoning_effort="max"`，启用最大推理深度，建议同时增加 `max_tokens`（如 384000）

```bash
# 高强度 + 大输出窗口
zouwucode --reasoning max --max-tokens 384000
```

### 9.5 配置持久化

在 `config.yaml` 中设置默认推理强度：

```yaml
reasoning_intensity: max  # low | medium | max
```

---

## 10. Web UI 使用指南

### 10.1 启动

```bash
zouwucode --web
```

浏览器打开 `http://127.0.0.1:8080`

### 10.2 界面布局

```
┌──────────────────────────────────────────────────────────────────────┐
│  ZOUWUCODE [v1.0.0]                            [Plan] [Agent] [YOLO] │
│  Reason: [medium ▾]                                                   │
├──────────────────────────────────────────────────────────────────────┤
│ ● Mode: AGENT | Reasoning: MEDIUM | Cache: 90% | Cost: $0.01 |       │
│   Req: 12 | Context: 3.2k | Rules: No | Skills: 2 | Modules: h-m-z  │
│   Context: ⬛⬛⬛⬝⬝⬝⬝⬝⬝⬝ 32%                                           │
├───────────────────┬──────────────────────────────────────────────────┤
│ ▸ SESSIONS        │   ╔══ welcome 欢迎屏（ASCII 艺术字横幅 + 模块徽章）╗ │
│   ├ 今天 09:12     │   ╚══════════════════════════════════════════════╝ │
│   ├ 昨天 22:40     │                                                    │
│ ▸ RULES           │  You（蓝色，右对齐）                               │
│ ▸ SKILLS          │  ── 帮我读取当前目录的文件列表                      │
│   ● python-dev    │                                                    │
│   ● git-flow      │  💭 Thinking ▾（可折叠思考块）                     │
│ [新会话]           │  → Read file  ls  （工具调用行）                   │
│                   │  ZOUWUCODE（深色，左对齐）                         │
│                   │  当前目录包含以下文件：...                          │
│                   │  ⚡ Cache hit: 90%                                 │
│                   │  ⚠️ Error: ...（红色错误详情）                      │
│                   │  [输入框…]  （Ctrl+Enter 发送）                     │
└───────────────────┴──────────────────────────────────────────────────┘
```

布局要点：

- **Header**：左侧 Logo + 版本徽章，右侧 Plan/Agent/YOLO 模式标签页与推理强度下拉框；`<560px` 窄屏时自动换行不裁剪。
- **状态栏**：实时显示模式、推理强度、缓存命中率、成本、请求数、上下文占用、规则/技能/模块数量与上下文进度条（`■` 填充）；内容超宽时横向滚动。
- **侧边栏**：会话列表、项目规则、已加载技能，均可折叠；长会话名自动折行。
- **欢迎屏**：首次打开显示 ASCII 艺术字横幅（`white-space: pre` 保留换行）+ 已加载模块徽章，发送任意消息后自动隐藏。
- **消息区**：用户右对齐蓝色、助手左对齐深色、系统居中；思考过程为可折叠 `details` 块（超 `240px` 内部滚动）；工具调用以 `→ ToolName args` 前缀行实时呈现。

### 10.3 API 端点

| 端点 | 方法 | 说明 |
|------|------|------|
| `/` | GET | HTML 页面 |
| `/api/status` | GET | 运行状态（模式、推理强度、缓存统计、上下文、规则、技能） |
| `/api/mode` | POST | 切换模式（`{"mode":"plan"}`） |
| `/api/reasoning` | POST | 设置推理强度（`{"reasoning":"max"}`） |
| `/api/chat` | POST | 发送消息（`{"message":"你好"}`） |
| `/api/interrupt` | POST | 打断当前执行中的任务 |
| `/api/rules` | GET | 获取项目规则内容 |
| `/api/skills` | GET | 获取可用和已加载的技能列表 |
| `/api/skills` | POST | 加载/卸载技能（`{"action":"load","name":"python-dev"}`） |
| `/api/sessions` | GET | 获取会话列表 |
| `/api/cache` | GET | 获取缓存统计 |
| `/api/hello-my-zouwucode` | POST | hello-my-zouwucode 多智能体编排（`{"action":"ultrawork\|plan\|start-work\|status", ...}`） |

### 10.4 测试 API

```bash
# 测试状态 API
curl http://127.0.0.1:8080/api/status

# 测试缓存统计
curl http://127.0.0.1:8080/api/cache

# 切换模式
curl -X POST http://127.0.0.1:8080/api/mode ^
  -H "Content-Type: application/json" ^
  -d "{\"mode\":\"plan\"}"

# 设置推理强度
curl -X POST http://127.0.0.1:8080/api/reasoning ^
  -H "Content-Type: application/json" ^
  -d "{\"reasoning\":\"max\"}"

# 获取规则
curl http://127.0.0.1:8080/api/rules

# 获取会话列表
curl http://127.0.0.1:8080/api/sessions

# 多智能体编排：查询状态
curl -X POST http://127.0.0.1:8080/api/hello-my-zouwucode ^
  -H "Content-Type: application/json" ^
  -d "{\"action\":\"status\"}"

# 多智能体编排：全自主管线
curl -X POST http://127.0.0.1:8080/api/hello-my-zouwucode ^
  -H "Content-Type: application/json" ^
  -d "{\"action\":\"ultrawork\",\"task\":\"分析当前项目的日志模块\"}"
```

---

## 11. TUI 终端界面使用指南

### 11.1 启动

```bash
# 需要安装 textual
pip install textual rich

# 启动
zouwucode --tui
```

### 11.2 快捷键

| 快捷键 | 功能 |
|--------|------|
| Ctrl+S | 循环切换模式（plan → agent → yolo） |
| Ctrl+R | 循环切换推理强度（low → medium → max） |
| Ctrl+L | 清理聊天窗口 |
| Escape | 任务执行中：打断当前任务（弹出确认，确认后于安全点停止，可输入「继续」恢复或 /clear 放弃）；空闲时：焦点回到输入框 |

### 11.3 任务打断

ZOUWUCODE 支持在任务执行过程中随时打断：

| 端 | 触发方式 | 确认机制 |
|----|---------|---------|
| TUI | `Esc`（任务执行中） | 确认对话框（Enter=确认打断 / Esc=继续执行） |
| WebUI | `⏹ 打断` 按钮或 `Esc` | 浏览器 confirm 对话框 |
| CLI | `Ctrl+C` | 中断后显示恢复选项 |

打断在最近的安全点（LLM 流式 chunk / 工具轮次边界 / 工具执行前）生效，约 0.1 秒内停止。
中断后可输入「继续」从断点恢复任务，或输入 `/clear` 放弃。
详见 [打断功能使用说明](docs/打断功能使用说明.md)。

### 11.4 子 Agent 系统

主 Agent 可通过内置 `task` 工具将工作分解为多个子任务并行委派：

- 每个子 Agent 拥有独立的引擎与上下文缓存，互不干扰
- 可为子 Agent 指定工具白名单（如只读探索型仅允许 read/ls）
- 每个子 Agent 有独立超时；单个失败不影响兄弟任务
- 打断主任务（Esc / Ctrl+C）会级联停止所有运行中的子 Agent
- `/agents` 命令查看子 Agent 列表、状态与耗时

配置见 15.2 节 `subagent:` 段；详见 [子Agent系统与扩展接口说明](docs/子Agent系统与扩展接口说明.md)。

### 11.5 扩展层（MCP/LSP）

通过 `extensions:` 配置段预留 MCP 与 LSP 集成（默认不激活）：

- `mcp_servers`：配置 MCP 服务器（stdio）后自动连接，服务器工具以 `名称__工具名` 形式进入可用工具列表
- `lsp_enabled: true`：启用 `check_diagnostics` 诊断工具（需对应语言服务器在 PATH 中）

详见 [子Agent系统与扩展接口说明](docs/子Agent系统与扩展接口说明.md)。

### 11.6 界面布局

```
┌────────────────────────────────────────────────────────────┐
│  Z O U W U C O D E                                 AGENT   │
├────────────────────────────────────────────────────────────┤
│  欢迎屏：ASCII 徽标 + 模块信息面板（opencode 风格）          │
│  ╔══════════════════════════════════════════╗              │
│  ║  Z O U W U C O D E  (ASCII 艺术字)      ║              │
│  ║  ┌ session ┐ hello-my-zouwucode v1.0.0  ║              │
│  ║  │ 11 agents · 11 categories · ready    ║              │
│  ║  └─────────┘                            ║              │
│  ╚══════════════════════════════════════════╝              │
│                                                            │
│   You                                                用户   │
│  ┃ 帮我读取当前目录的文件列表                             │
│   ZOUWUCODE                                         助手   │
│  ┃ 💭 思考（/thinking 控制显隐）                          │
│  ┃ → Read file  ls                                    工具  │
│  ┃ 当前目录包含以下文件：...                              │
│  ┃ ⚡ Cache hit: 90%                                      │
├────────────────────────────────────────────────────────────┤
│ Mode: AGENT | Reasoning: MEDIUM | Skills: 2 | Cache 90%   │
│ Context: ■■⬝⬝⬝⬝⬝⬝⬝⬝ 32%                                 │
├────────────────────────────────────────────────────────────┤
│ Ctrl+S:Cycle Mode  Ctrl+R:Reasoning  Ctrl+L:Clear /help    │
└────────────────────────────────────────────────────────────┘
```

---

## 12. 工具系统

ZOUWUCODE 内置 9 个工具 + task 委派工具（子 Agent 并行任务分解），覆盖日常开发需求：

### 文件操作工具

| 工具 | 说明 | 权限 |
|------|------|------|
| `Read` | 读取文件内容 | 只读 |
| `Write` | 写入/创建文件 | 需审批 |
| `Edit` | 编辑文件（精确替换） | 需审批 |
| `Ls` | 列出目录内容 | 只读 |
| `Glob` | 搜索文件（通配符模式） | 只读 |

### 执行工具

| 工具 | 说明 | 权限 |
|------|------|------|
| `Shell` | 执行 shell 命令 | 需审批（沙箱保护） |
| `Git` | Git 操作（状态、差异、提交、日志） | 需审批 |

### 网络工具

| 工具 | 说明 | 权限 |
|------|------|------|
| `WebSearch` | 联网搜索 | 需审批 |
| `WebFetch` | 获取网页内容 | 需审批 |

### 任务委派工具

| 工具 | 说明 | 权限 |
|------|------|------|
| `task` | 将工作分解为子任务，委派子 Agent 并行执行（独立引擎、工具白名单、独立超时） | 子 Agent 内部仍经沙箱审批 |

---

## 13. 权限沙箱

### 13.1 安全特性

- **危险命令检测**：自动拦截 `rm -rf /`、`sudo`、`format` 等危险命令
- **零宽字符注入防护**：防止隐藏恶意代码
- **命令黑名单**：预定义的危险命令列表
- **路径白名单**：只允许在工作区目录内操作文件
- **三种模式**：自动审批、交互审批、禁用沙箱

### 13.2 配置沙箱

```yaml
# config.yaml 中的沙箱配置
sandbox:
  enabled: true           # 启用沙箱
  default_mode: agent     # 默认模式：agent/plan/yolo
  allow_shell: true       # 允许执行 shell 命令
  allow_file_write: true  # 允许写入文件
  allow_network: true     # 允许网络访问
  allow_git: true         # 允许 git 操作
  allowed_paths:          # 允许访问的路径
    - .
```

---

## 14. 会话管理

### 14.1 自动保存

会话数据自动保存到 `zouwucode_data/sessions/` 目录，每 60 秒自动保存一次。

### 14.2 管理会话

```bash
# 在 ZOUWUCODE 中查看会话列表
/sessions

# 每次退出时自动保存会话摘要
```

### 14.3 会话数据

每个会话包含：
- 用户消息和助手回复
- 思考过程
- 缓存命中状态
- Token 使用量

---

## 15. 配置详解

### 15.1 配置文件位置

默认位置：`zouwucode_data/config.yaml`，也支持在当前工作目录放 `config.yaml` 实现项目级配置。

> **安全提示**：`config.yaml` 含 API Key，已被 `.gitignore` 排除，切勿提交到版本库；可参考 `config.example.yaml` 模板。

### 15.2 完整配置项

```yaml
# ZOUWUCODE 配置示例

# 数据目录
data_dir: ""                         # 留空使用默认数据目录（可自定义路径）

# 默认提供商
default_provider: deepseek

# 语言
language: zh

# 主题
theme: dark

show_thinking: true                  # 流式显示思考过程（/thinking 切换）

# LLM 提供商配置
providers:
  deepseek:
    api_key: "sk-your-key-here"      # API Key
    model: "deepseek-v4-flash"        # 模型名称（推荐 deepseek-v4-flash）
    base_url: "https://api.deepseek.com"  # API 地址
    api_type: "deepseek"             # 提供商类型

# 缓存配置
cache:
  enabled: true                      # 启用缓存
  append_only: true                  # 只追加模式（保持前缀一致性）
  max_prefix_tokens: 128000          # 最大前缀 token 数
  stats_window: 50                   # 统计窗口大小

# 引擎安全限制 — 防止死循环与执行停顿
engine:
  max_tool_rounds: 25               # 单次任务最多工具轮数
  turn_timeout_seconds: 300         # 单次 LLM 请求超时（秒）
  task_timeout_seconds: 1800        # 单次任务总耗时上限（秒）
  max_consecutive_tool_errors: 3    # 工具连续失败熔断阈值

# 子 Agent 系统
subagent:
  max_agents: 8                 # 并行子 Agent 数量上限
  default_timeout: 600          # 单个子 Agent 默认超时（秒）

# 扩展层（MCP/LSP — 默认不激活，配置后自动启用）
extensions:
  mcp_servers: []               # MCP 服务器列表（stdio），配置后自动连接
  lsp_enabled: false            # 启用后注册 check_diagnostics 诊断工具

# 沙箱配置
sandbox:
  enabled: true                      # 启用沙箱
  default_mode: agent                # 默认模式
  allow_shell: true                  # 允许 shell
  allow_file_write: true             # 允许写文件
  allow_network: true                # 允许网络
  allow_git: true                    # 允许 git
  allowed_paths:                     # 允许路径
    - .

# 推理强度
reasoning_intensity: medium             # low | medium | max

# 会话配置
session:
  save_enabled: true                 # 启用保存
  auto_save_interval: 60             # 自动保存间隔（秒）
  max_sessions: 50                   # 最大会话数
  rollback_enabled: true             # 启用回滚

# 多智能体编排模块（hello-my-zouwucode，复刻自 oh-my-opencode）
hello_my_zouwucode:
  enabled: true                      # 总开关
  state_dir: ".hello-my-zouwucode"   # 状态目录（boulder/plans/notepads）
  default_category: "deep"           # 未指定时默认 category
  max_review_rounds: 2               # Momus 评审重试上限（0 = 无限）
  interactive_planning: true         # 允许 Prometheus 访谈模式
```

超过任一限制时任务以 `TurnLimitExceeded` 安全终止（Atlas 等调用方会标记失败并继续后续任务），防止模型反复调用失败工具导致的死循环。

LLM 请求遇 429 / 5xx / 网络故障时按指数退避自动重试（`max_llm_retries` 次）；401 / 400 等永久错误立即失败、不重试。

### 15.3 生成配置

```bash
# 生成默认配置
zouwucode --init-config

# 查看当前配置
zouwucode --show-config
```

### 15.4 日志

ZOUWUCODE 将运行日志写入 `<data_dir>/logs/zouwucode.log`（轮转文件，单文件 5MB、保留 5 份）。
日志记录任务起止、每轮 LLM 请求耗时、工具执行结果、打断事件与重试事件，用于事后排障。

- 级别配置：`config.yaml` 根级 `log_level: debug | info | warning | error`（默认 info）
- 终端界面不受影响（日志只写文件，TUI/CLI 的界面输出独立）
- 报告 Bug 时请附上相关日志片段（⚠️ 先删除 API Key 等敏感信息）

---

## 16. 便捷启动安装

### 16.1 pip 全局安装（推荐）

```bash
# 一次安装，全局使用
cd zouwucode  # 进入本仓库克隆目录
pip install -e .

# 之后在任何目录直接使用
zouwucode
zouwucode --web
zouwucode --tui
```

### 16.2 使用 z.bat

`z.bat` 文件位于项目根目录，确保 ZOUWUCODE 可以从任何目录启动。

**使用方法**：

```bash
# 直接运行（需要在项目目录下）
z

# 或复制到系统 PATH 路径
# 以管理员身份运行 cmd，在项目根目录执行：
copy z.bat C:\Windows\System32\z.bat

# 然后就可以在任何目录使用 z 命令
z
z --web
z --mode plan
z --tui
```

### 16.3 使用 install.ps1（自动安装）

以管理员身份运行 PowerShell：

```powershell
powershell -ExecutionPolicy Bypass -File install.ps1
```

安装后，打开新的终端即可使用 `z` 和 `zouwucode` 命令。

### 16.4 手动添加到 PATH

```powershell
# 将项目目录添加到系统 PATH
setx PATH "%PATH%;<ZOUWUCODE 克隆目录>"
```

---

## 17. 打包为独立可执行文件

### 17.1 打包

```bash
python build.py
```

这将在 `dist/` 目录下生成 `ZOUWUCODE.exe`，它是一个独立的可执行文件。

### 17.2 特性

- **不依赖系统 Python**：自带嵌入式 Python 运行时
- **不写 C 盘**：所有数据文件存储在 `zouwucode_data/` 目录
- **便携式**：可复制到 U 盘或任何位置使用

### 17.3 清理构建

```bash
python build.py --clean
```

---

## 18. 故障排除

### 18.1 常见问题

| 问题 | 原因 | 解决 |
|------|------|------|
| `ImportError: attempted relative import` | 直接运行了 `__main__.py` | 使用 `python -m zouwucode` 运行 |
| `No module named 'httpx'` | 依赖未安装 | 运行 `pip install -r requirements.txt` |
| 模式切换失败 | 参数未正确传递 | 使用 `/plan`、`/agent`、`/yolo` 命令 |
| Web UI 无法访问 | 端口被占用 | 使用 `--port` 指定其他端口 |
| 缓存命中率低 | 前缀不一致 | 确保使用 append-only 模式 |
| LLM 请求偶发失败/超时 | 网络抖动、API 限流（429）或 5xx | 引擎会按指数退避自动重试（默认 2 次）；仍失败请检查网络与 API Key，并查看 `zouwucode_data/logs/zouwucode.log` 中的 retry 记录 |
| `zouwucode` 命令找不到 | 未全局安装或未添加到 PATH | 运行 `pip install -e .` 或 `install.ps1` |

### 18.2 调试

```bash
# 查看配置
zouwucode --show-config

# 查看版本
zouwucode --version

# 运行测试
pytest tests/ -v

# 查看运行日志（任务/轮次/工具/打断/重试全程留痕）
Get-Content zouwucode_data/logs/zouwucode.log -Tail 50   # PowerShell
```

### 18.3 重置

```bash
# 清除项目记忆
zouwucode --forget

# 重新初始化配置
zouwucode --init-config

# 删除所有数据
Remove-Item -Recurse -Force zouwucode_data/
Remove-Item -Recurse -Force .zouwucode/
```

---

## 19. 多智能体编排（hello-my-zouwucode）

ZOUWUCODE 一比一复刻了 OpenCode 生态的 oh-my-opencode 多智能体编排系统，并将其封装为 **hello-my-zouwucode 可加载模块**：11 个专职 Agent 通过 IntentGate（意图门控）分类用户请求后互相委托，配合 Boulder 跨会话状态、Notepad 智慧积累和 Category 语义路由，将"一个全能助手"变成"一支开发团队"。CLI/TUI/Web 三端通过 `zouwucode/modules/manager.py` 的 `ModuleManager` 派发消息/命令/事件，模块返回渲染 dict 或 None（未处理则回退内建逻辑）；模块由 `config.hello_my_zouwucode.enabled` 控制按需加载，加载失败不阻塞主体启动。

### 19.1 快速开始

默认已启用。直接在任意界面输入：

```
ultrawork 分析当前项目的日志模块并给出优化方案
```

或使用显式命令 `/hello-ultrawork <任务>`。系统按 **意图分类 → 语义路由 → 规划 → 逐任务执行** 全自主完成。

### 19.2 命令

| 命令 | 作用 |
|------|------|
| `ultrawork <任务>` / `ulw <任务>`（消息前缀） | 全自主管线 |
| `/hello-ultrawork <任务>` | 全自主管线（显式） |
| `/hello-plan <任务>` | Prometheus 规划（写入 `.hello-my-zouwucode/plans/*.md`，`/plan <任务>` 兼容） |
| `/hello-start-work` | Atlas 执行活动计划（有状态则 RESUME，否则 INIT） |
| `/hello-status` | Boulder 进度 + Notepad 摘要 + Agent/Category 数量 |
| `/hello-agents` | 列出 11 个专职 Agent |
| `/hello-categories` | 列出 11 个任务分类 |

### 19.3 工作流

```bash
# 一条龙
ultrawork 为项目添加用户认证模块

# 先规划后执行（大任务推荐）
/hello-plan 实现支持 JWT 的登录与注册
/hello-start-work

# 跨会话续跑（自动 RESUME 剩余任务）
/hello-start-work
```

### 19.4 状态文件

```
.hello-my-zouwucode/
├── boulder.json            # 活动计划 + 任务状态（跨会话）
├── plans/{plan-name}.md    # 执行计划
└── notepads/{plan-name}/   # learnings/decisions/issues/verification/problems
```

### 19.5 Category → 推理档位

路由按任务语义选择执行强度（适配 DeepSeek 三档）：

| Category | 推理档位 | 用途 |
|---|---|---|
| `ultrabrain` / `visual-engineering` / `artistry` / `unspecified-high` | `max` | 最难逻辑、架构、创意 |
| `deep` | `medium` | 通用实现（默认） |
| `quick` / `writing` / `git` / `quick-rust` / `quick-zig` / `unspecified-low` | `low` | 快速检索、写作、小改动 |

### 19.6 配置

```yaml
hello_my_zouwucode:
  enabled: true                # 总开关
  state_dir: ".hello-my-zouwucode"  # 状态目录
  default_category: "deep"     # 默认 category
  max_review_rounds: 2         # Momus 评审重试上限
  interactive_planning: true   # 允许 Prometheus 访谈模式
```

### 19.7 相关文档

- 集成细节见 `docs/hello-my-zouwucode_集成文档.md`
- 测试明细见 `docs/hello-my-zouwucode_功能测试报告.md`
- 使用指引见 `docs/hello-my-zouwucode_使用说明.md`

---

## 附录：项目结构

```
zouwucode/
├── __init__.py          # 包版本信息
├── __main__.py          # CLI 入口、参数解析
├── config.py            # 配置管理（Pydantic 模型）
├── project_memory.py    # 持久项目记忆系统
├── runtime.py           # 跨 UI 共享工厂：provider/内置工具集
│
├── engine/              # Cache-First LLM 引擎
│   ├── __init__.py
│   ├── loop.py          # Append-only 对话循环
│   ├── cache.py         # 前缀缓存统计管理
│   └── providers/
│       ├── base.py      # 提供商抽象基类
│       ├── deepseek.py  # DeepSeek API 适配器
│       └── openai.py    # OpenAI API 适配器
│
├── tools/               # 工具系统
│   ├── __init__.py
│   ├── base.py          # 工具基类
│   ├── registry.py      # 工具注册中心
│   ├── file_tools.py    # 文件读写、编辑、搜索
│   ├── shell_tools.py   # Shell 命令执行
│   ├── git_tools.py     # Git 操作
│   └── web_tools.py     # Web 搜索与抓取
│
├── agent/               # 工具调用协调器
│   ├── __init__.py
│   ├── coordinator.py   # 主协调器
│   └── subagent.py      # 子 Agent 系统：隔离引擎/并行/白名单/级联打断
│
├── extensions/          # 扩展层 MCP/LSP 预留接口
│   ├── __init__.py
│   ├── host.py
│   ├── mcp_ext.py
│   └── lsp_ext.py
│
├── context/             # 三层上下文管理
│   ├── __init__.py
│   ├── memory.py        # MEMORY.md 索引
│   ├── topics.py        # Topic 文件管理
│   ├── transcript.py    # 对话日志管理
│   ├── compressor.py    # 对话压缩算法
│   └── sliding_window.py# 滑动窗口上下文
│
├── sandbox/             # 权限沙箱
│   ├── __init__.py
│   └── permission.py    # 权限门控与管理
│
├── session/             # 会话管理
│   ├── __init__.py
│   └── manager.py       # 会话保存/恢复
│
├── mcp/                 # MCP 协议支持
│   ├── __init__.py
│   └── client.py        # MCP 客户端
│
├── lsp/                 # LSP 诊断集成
│   ├── __init__.py
│   └── client.py        # LSP 客户端
│
├── modules/             # 可扩展模块系统
│   ├── __init__.py
│   └── manager.py       # ModuleManager（注册/注销/状态查询 + 三路派发）
│
├── skills/              # .zouwucode/skills/ 技能加载器
│   ├── __init__.py
│   └── manager.py
│
├── tui/                 # 终端界面
│   ├── __init__.py
│   ├── app.py           # CLI 应用（主应用类）
│   ├── textual_app.py   # Textual TUI 界面
│   └── styles.tcss      # TUI 样式表
│
└── webui/               # Web UI
    ├── __init__.py
    └── server.py        # 本地 HTTP 服务器

hello_my_zouwucode/       # 多智能体编排模块（复刻自 oh-my-opencode）
├── __init__.py
├── module.py             # HelloMyZouwucodeModule 模块接口（name/version/commands + handle_*）
├── agents.py             # 11 个专职 Agent + 系统提示词
├── categories.py         # Category 语义路由表
├── intent_gate.py        # IntentGate 意图分类器
├── boulder.py            # Boulder 跨会话任务状态
├── notepad.py            # Notepad 智慧积累
├── planner.py            # Prometheus 规划器
├── atlas.py              # Atlas 执行器
└── orchestrator.py       # Sisyphus 主编排器
```

---

*文档版本: v1.0.0 · 最后更新: 2026-08-04*