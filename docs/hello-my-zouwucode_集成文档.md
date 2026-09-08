# hello-my-zouwucode 集成文档（ZOUWUCODE）

> 本文件记录 hello-my-zouwucode 多智能体编排模块（复刻自 OpenCode 生态的 oh-my-opencode）如何被一比一复刻并集成到 ZOUWUCODE 项目。

---

## 1. 概述

hello-my-zouwucode（复刻自 OpenCode 生态的 oh-my-opencode，原名 oh-my-openagent）是 ZOUWUCODE 中的多智能体编排模块：11 个专职 Agent 通过 IntentGate（意图门控）分类用户请求后互相委托，配合 Boulder 跨会话状态、Notepad 智慧积累和 Category 语义路由，将"一个全能助手"变成"一支开发团队"。

本模块在 ZOUWUCODE 中完整复刻了该系统的**功能语义、交互流程与用户界面**，同时根据 ZOUWUCODE 的单 Provider（DeepSeek）架构做了**等价适配**（见 §3）。hello-my-zouwucode 作为可扩展模块按需加载，CLI/TUI/Web 三端不再硬编码其内部实现，而是通过 `ModuleManager` 统一派发（见 §5）。

## 2. 复刻范围（与原版对照）

| hello-my-zouwucode 能力 | ZOUWUCODE 复刻状态 | 说明 |
|---|---|---|
| 11 个专职 Agent | ✅ 完整 | `hello_my_zouwucode/agents.py`，Sisyphus / Hephaestus / Prometheus / Atlas / Oracle / Librarian / Explore / Multimodal-Looker / Metis / Momus / Sisyphus-Junior |
| IntentGate 意图门控 | ✅ 完整 | `hello_my_zouwucode/intent_gate.py`，13 类意图，中英文混合关键词 + 硬规则 |
| Category 语义路由 | ✅ 完整 | `hello_my_zouwucode/categories.py`，11 个 category，映射到 DeepSeek 三档推理强度 |
| Sisyphus 编排（ultrawork/ulw） | ✅ 完整 | `hello_my_zouwucode/orchestrator.py`，全自主管线 |
| Prometheus 规划（@plan） | ✅ 完整 | `hello_my_zouwucode/planner.py`，访谈 → Metis 差距分析 → Momus 高精度评审 → 写入 `.hello-my-zouwucode/plans/*.md` |
| Atlas 执行（/hello-start-work） | ✅ 完整 | `hello_my_zouwucode/atlas.py`，逐任务委托 Sisyphus-Junior，验证并回报 |
| Boulder 跨会话状态 | ✅ 完整 | `hello_my_zouwucode/boulder.py`，`.hello-my-zouwucode/boulder.json`，RESUME/INIT 双模式 |
| Notepad 智慧积累 | ✅ 完整 | `hello_my_zouwucode/notepad.py`，`.hello-my-zouwucode/notepads/{plan}/` 下 5 个学习文件 |
| 模型级差异化（Claude/GPT/Kimi 等） | ⚠️ 等价适配 | ZOUWUCODE 单 DeepSeek Provider，通过 `reasoning_effort` 三档实现语义等价（见 §3） |
| Skills 联动（load_skills） | ✅ 适配 | category 的 `load_skills` 字段可注入 `.zouwucode/skills/` 技能包 |
| 三端 UI（CLI/TUI/Web）命令 | ✅ 完整 | `/hello-ultrawork`、`/hello-plan <task>`、`/hello-start-work`、`/hello-status`、`/hello-agents`、`/hello-categories` |

## 3. 适配决策

### 3.1 Category → DeepSeek 推理强度

原版按 category 选择不同厂商模型；ZOUWUCODE 只有一个 DeepSeek Provider，因此把"选模型"等价转换为"选推理档位"：

| Category | 推理强度 | DeepSeek 参数 | 语义 |
|---|---|---|---|
| `ultrabrain` / `visual-engineering` / `artistry` / `unspecified-high` | `max` | `reasoning_effort=max` | 最难逻辑/架构 |
| `deep` | `medium` | `reasoning_effort=high` | 通用实现 |
| `quick` / `writing` / `git` / `quick-rust` / `quick-zig` / `unspecified-low` | `low` | 不传参数 | 快速检索/写作 |

### 3.2 委托原语（Delegation Semantics）

原版的 `task(category=...)` / `task(subagent_type=...)` / `call_omo_agent(...)` 在本项目中统一通过 `engine.run(messages)` 实现：系统提示词 = Agent 专属提示词 + Category 说明 + （可选）Skills/Rules 上下文 + Notepad 上下文。

### 3.3 事件驱动 UI 解耦

编排器通过 `on_event(etype, message, payload)` 事件回调向外输出进度（`intent`/`plan`/`task`/`system`/`warning`/`error`），经 `ModuleManager.dispatch_event` 广播给已加载模块并转发至三端：CLI 打印、TUI RichLog 渲染、Web 前端显示，共享同一套编排核心。

### 3.4 Prometheus 访谈模式

原版 Prometheus 通过 UI 交互访谈用户。本项目在 CLI/TUI/Web 中以 `/hello-plan <task>` 触发的非交互规划为主（`interactive_planning` 配置项预留），规划器自动生成。预留 `ask` 回调接口，需要时可接入对话式访谈。

## 4. 代码结构

模块本体（`hello_my_zouwucode/` 包）：

```
hello_my_zouwucode/
├── __init__.py            # 模块包
├── module.py              # HelloMyZouwucodeModule 模块接口（name/version/commands + handle_*）
├── agents.py              # 11 个 Agent 定义 + 系统提示词（DeepSeek 优化版）
├── categories.py          # Category 语义路由表
├── intent_gate.py         # IntentGate 意图分类器
├── boulder.py             # Boulder 跨会话任务状态
├── notepad.py             # Notepad 智慧积累
├── planner.py             # Prometheus 规划器
├── atlas.py               # Atlas 执行器 + OrchestrationResult
└── orchestrator.py        # Sisyphus 主编排器 + ultrawork 检测
```

主体框架侧（`zouwucode/` 包）：

```
zouwucode/modules/
├── __init__.py            # 模块系统包
└── manager.py             # ModuleManager：注册/注销/状态查询 + 三路派发
```

运行时状态目录（工作目录下，由 `config.hello_my_zouwucode.state_dir` 指定，默认 `.hello-my-zouwucode`）：

```
.hello-my-zouwucode/
├── boulder.json           # 活动计划 + 任务状态（跨会话）
├── plans/
│   └── {plan-name}.md     # Prometheus 生成的执行计划
└── notepads/
    └── {plan-name}/
        ├── learnings.md   # 模式、约定、成功经验
        ├── decisions.md   # 架构决策
        ├── issues.md      # 问题、阻塞、坑
        ├── verification.md # 验证结果
        └── problems.md    # 未解决问题
```

## 5. 三端集成点（模块化架构）

ZOUWUCODE 是**主体框架（host framework）**：CLI/TUI/Web 三端不再硬编码模块内部，而是在初始化时创建 `zouwucode/modules/manager.py` 的 `ModuleManager`，通过其 `dispatch_message` / `dispatch_command` / `dispatch_event` 三路派发与模块交互。模块处理消息/命令后返回**渲染 dict**（`{"title", "content", "details", ...}`）；返回 `None` 表示未处理，主体回退到内建逻辑。

模块接口由 `hello_my_zouwucode/module.py` 的 `HelloMyZouwucodeModule` 提供：`name`、`version`、`commands` 属性 + `handle_message` / `handle_command` / `on_event` 方法。

| 端 | 文件 | 集成内容 |
|---|---|---|
| 公共 | `zouwucode/config.py` | 新增 `HelloMyZouwucodeConfig`（enabled / state_dir / default_category / max_review_rounds / interactive_planning） |
| 公共 | `zouwucode/modules/manager.py` | `ModuleManager`：注册/注销/状态查询 + `dispatch_message`/`dispatch_command`/`dispatch_event` 三路派发 |
| 模块 | `hello_my_zouwucode/module.py` | `HelloMyZouwucodeModule`：`name`/`version`/`commands` + `handle_message`/`handle_command`/`on_event`，内部持有 `SisyphusOrchestrator` |
| CLI | `zouwucode/tui/app.py` | `__init__` 创建 `ModuleManager`，读取 `config.hello_my_zouwucode.enabled` 为 True 时注册模块（try/except 包裹，加载失败不阻塞启动）；`run_interactive`/`run_single` 经 `dispatch_message`/`dispatch_command` 派发；`_on_module_event` 终端渲染 |
| TUI | `zouwucode/tui/textual_app.py` | 同上，命令分支位于 `_handle_command`，事件渲染为 RichLog 彩色消息 |
| Web | `zouwucode/webui/server.py` | `WebUIServer` 接收 `modules` 参数；新增 `/api/hello-my-zouwucode`（action=ultrawork/plan/start-work/status）；`/api/status` 返回 `modules` 列表；`/api/chat` 经 `dispatch_message` 拦截 `ultrawork`/`ulw` 前缀 |
| Web 启动 | `zouwucode/tui/app.py::run_web` | 将 `ModuleManager`（`modules` 参数）注入 `WebUIServer` |

### 5.1 命令总览（三端一致）

| 命令 | 作用 |
|---|---|
| `ultrawork <task>` / `ulw <task>`（消息前缀） | 全自主管线：意图分类 → 路由 → 规划 → 执行 |
| `/hello-ultrawork <task>` | 同上（显式命令） |
| `/hello-plan <task>` | Prometheus 规划（`/plan <task>` 兼容；无参数时保持原"切换 plan 模式"行为） |
| `/hello-start-work` | Atlas 执行活动计划（有 boulder 则 RESUME，无则 INIT） |
| `/hello-status` | 查看 boulder 进度 + notepad 摘要 + Agent/Category 数量 |
| `/hello-agents` | 列出 11 个内置 Agent |
| `/hello-categories` | 列出 11 个任务分类 |

### 5.2 UI 对齐 opencode

ZOUWUCODE 的 CLI/TUI/Web 界面已按 opencode 风格对齐：

- **TUI**：启动欢迎屏为 ASCII art 徽标 + `┌ session ┐` 会话/模块信息面板；消息以 `┃` 边框框架呈现（`┃ You` / `┃ assistant` / `┃ 💭 thinking`）；工具调用以 `→ Read file …` 前缀实时呈现（engine 新增 `on_tool_event` 钩子）；底部 footer 含 `■■⬝⬝` 上下文进度条 + 快捷键提示；`/thinking` 命令可开关思考块显示。
- **Web**：欢迎屏含 ASCII art 徽标与已加载模块徽章；思考块为可折叠 `<details>` 元素；工具调用渲染为 `→ Read file` 行（由 `/api/chat` 响应的 `tools` 字段驱动）；状态栏含 Modules 计数与 `■■⬝⬝` 进度条。

## 6. 配置

`config.yaml` 新增 `hello_my_zouwucode` 段（均有默认值，可省略）：

```yaml
hello_my_zouwucode:
  enabled: true                # 总开关（false 则三端不注册该模块）
  state_dir: ".hello-my-zouwucode"  # 状态目录（boulder/plans/notepads）
  default_category: "deep"     # 未指定时默认 category
  max_review_rounds: 2         # Momus 评审重试上限（0=无限）
  interactive_planning: true   # 允许 Prometheus 访谈模式
```

## 7. 测试

新增 `tests/test_hello_my_zouwucode.py`（53 项用例），覆盖：Category 路由、IntentGate 分类、Agent 清单、Boulder 状态机、Notepad、Planner（计划解析/生成/最新计划）、Atlas 执行（成功/失败/无计划）、Orchestrator（ultrawork 快速问答、ultrawork 完整管线、run_plan、start-work RESUME、无计划报错）、`HelloMyZouwucodeConfig` 默认值，以及模块化新增的 `ModuleManager` 注册/派发/注销、`HelloMyZouwucodeModule` 消息与命令处理、`status_payload`、App 模块加载/禁用等测试。

```bash
python -m pytest tests/test_hello_my_zouwucode.py -q   # 53 passed
python -m pytest -q                                    # 全量 167 passed
```

> 全量 167 = 原 104 + 新增 53（hello-my-zouwucode）+ 2（TUI 模式切换回归）+ 8（WebUI 布局回归：欢迎横幅 4 + 响应式 4）。

## 8. 集成过程中修复的既有缺陷

**Web 服务器 POST body 截断**（`zouwucode/webui/server.py::handle_request`）：原实现只 `reader.read()` 一次，POST body 若与请求头分属不同 TCP 包会被丢弃，导致所有带 body 的 POST 接口（`/api/mode`、`/api/reasoning`、`/api/skills`、`/api/chat`、新增的 `/api/hello-my-zouwucode`）返回 `Expecting value: line 1 column 1`。已改为按 `Content-Length` 循环读取至完整请求。

## 9. 与原版差异说明

- 模型选择：原版多模型 fallback 链替换为 DeepSeek 单模型 + 三档推理强度（语义等价，非逐项一致）。
- 访谈：原版强制访谈，本项目非交互默认自动规划。
- 背景 Agent / Team Mode / 技能 MCP 隔离：超出本次复刻范围，未实现。
