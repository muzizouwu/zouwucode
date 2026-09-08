# hello-my-zouwucode 使用说明（ZOUWUCODE）

> 如何在 ZOUWUCODE 中使用多智能体编排模块（复刻自 OpenCode 生态的 oh-my-opencode）：全自主管线（ultrawork）、Prometheus 规划、Atlas 执行、Boulder 状态与 Notepad 智慧积累。

---

## 1. 快速开始

默认已启用（`config.yaml` 中 `hello_my_zouwucode.enabled: true`）。直接在任意界面输入：

```
ultrawork 分析当前项目的日志模块并给出优化方案
```

或使用显式命令：

```
/hello-ultrawork 分析当前项目的日志模块并给出优化方案
```

系统将按 **意图分类 → 语义路由 → 规划 → 逐任务执行** 全自主完成，并在 `.hello-my-zouwucode/` 目录留下计划、任务状态与学习笔记。

## 2. 核心命令（CLI / TUI / Web 三端一致）

| 命令 | 作用 |
|---|---|
| `ultrawork <任务>` / `ulw <任务>`（消息前缀） | 全自主管线：IntentGate 分类 → 路由 → Prometheus 规划 → Atlas 执行 |
| `/hello-ultrawork <任务>` | 同上（显式命令） |
| `/hello-plan <任务>` | 仅执行 Prometheus 规划（访谈 → 起草 → Metis 差距分析 → Momus 评审），写入 `.hello-my-zouwucode/plans/*.md`（`/plan <任务>` 兼容） |
| `/hello-plan` | 无参数时保留原语义：切换 plan 只读模式 |
| `/hello-start-work` | Atlas 执行活动计划：已有 Boulder 状态则 **RESUME** 续跑，否则 **INIT**（取最新计划全量执行） |
| `/hello-status` | 查看 Boulder 进度 + Notepad 摘要 + Agent/Category 数量 |
| `/hello-agents` | 列出 11 个专职 Agent |
| `/hello-categories` | 列出 11 个任务分类（含推理档位） |

> Web 端另有 `? /help` 内提示；三端均支持以 `ultrawork`/`ulw` 作为**普通消息前缀**触发。

## 3. 三种典型工作流

### 3.1 一条龙：ultrawork 全自主管线

```
ultrawork 为项目添加一个用户认证模块
```

流程：
1. **IntentGate** 分类 → `IMPLEMENTATION`（若为简单问答如"1+1=?"则直接回答，不走重管线）
2. **Prometheus** 起草计划（非交互自动生成）
3. **Metis** 差距分析、**Momus** 高精度评审（最多 `max_review_rounds` 轮）
4. **Atlas** 逐任务委托 **Sisyphus-Junior** 执行，写入 Notepad
5. 产出：`.hello-my-zouwucode/plans/*.md`、`.hello-my-zouwucode/boulder.json`、`.hello-my-zouwucode/notepads/{plan}/`

### 3.2 先规划后执行（推荐用于大任务）

```
# 1) 先生成计划（只规划，不改代码）
/hello-plan 实现支持 JWT 的登录与注册

# 2) 查看计划
/hello-status

# 3) 执行计划（Atlas 逐任务完成并验证）
/hello-start-work
```

### 3.3 跨会话续跑（Boulder RESUME）

执行中途退出后重新进入：

```
/hello-start-work
```

Boulder 自动识别未完成任务并**仅续跑剩余任务**，完成进度跨会话保留。

## 4. 11 个专职 Agent

| Agent | 角色 | 何时被调用 |
|---|---|---|
| **Sisyphus** | 主编排器 | 每条请求的入口 |
| **Hephaestus** | 深度工作 | 重活、长任务 |
| **Prometheus** | 规划师 | `/hello-plan <任务>` |
| **Atlas** | 执行指挥 | `/hello-start-work` |
| **Oracle** | 架构顾问 | architecture / review |
| **Librarian** | 文档搜索 | research |
| **Explore** | 代码检索（grep） | explore |
| **Multimodal-Looker** | 视觉分析 | 图片类任务（占位） |
| **Metis** | 差距分析 | 计划评审环节 |
| **Momus** | 计划评审 | 高精度计划审查 |
| **Sisyphus-Junior** | 任务执行者 | Atlas 委托的具体任务 |

## 5. Category 语义路由（→ 推理档位）

路由按**任务语义**而非模型名选择执行强度，适配为 DeepSeek 三档：

| Category | 推理档位 | 用途 |
|---|---|---|
| `ultrabrain` / `visual-engineering` / `artistry` / `unspecified-high` | `max` | 最难逻辑、架构、创意 |
| `deep` | `medium` | 通用实现（默认） |
| `quick` / `writing` / `git` / `quick-rust` / `quick-zig` / `unspecified-low` | `low` | 快速检索、写作、小改动 |

## 6. 状态文件（`.hello-my-zouwucode/` 目录）

```
.hello-my-zouwucode/
├── boulder.json            # 活动计划 + 任务状态（跨会话）
├── plans/{plan-name}.md    # Prometheus 生成的执行计划
└── notepads/{plan-name}/   # Notepad 智慧积累（5 个文件）
    ├── learnings.md        # 模式、约定、成功经验
    ├── decisions.md        # 架构决策
    ├── issues.md           # 问题、阻塞、坑
    ├── verification.md     # 验证结果
    └── problems.md         # 未解决问题
```

Notepad 内容会在后续任务中自动注入 Worker 上下文，实现"越用越聪明"。

## 7. 配置

`config.yaml` 新增 `hello_my_zouwucode` 段（均有默认值，可省略）：

```yaml
hello_my_zouwucode:
  enabled: true                # 总开关（false 则三端不注册该模块）
  state_dir: ".hello-my-zouwucode"  # 状态目录（boulder/plans/notepads）
  default_category: "deep"     # 未指定时默认 category
  max_review_rounds: 2         # Momus 评审重试上限（0 = 无限）
  interactive_planning: true   # 允许 Prometheus 访谈模式
```

## 8. 常见问题

| 问题 | 解决 |
|---|---|
| 输入 `ultrawork ...` 无响应 | 检查 `config.yaml` 中 `hello_my_zouwucode.enabled: true`；确认已配置 API Key |
| `/hello-start-work` 提示"No active plan" | 先执行 `/hello-plan <任务>` 或 `ultrawork <任务>` 生成计划 |
| 任务失败后进度不动 | 失败任务会保留在剩余列表中，`/hello-start-work` 自动重试 |
| 想清空任务状态 | 删除工作目录下的 `.hello-my-zouwucode/boulder.json`（或整个 `.hello-my-zouwucode/`） |
| 只想要普通对话 | 直接输入消息，不带 `ultrawork`/`ulw` 前缀即可 |

## 9. UI 对齐 opencode

ZOUWUCODE 的 CLI/TUI/Web 界面已按 opencode 风格对齐：

- **TUI**：启动欢迎屏为 ASCII art 徽标 + `┌ session ┐` 会话/模块信息面板；消息以 `┃` 边框框架呈现（`┃ You` / `┃ assistant` / `┃ 💭 thinking`）；工具调用以 `→ Read file …` 前缀实时呈现（engine 新增 `on_tool_event` 钩子）；底部 footer 含 `■■⬝⬝` 上下文进度条 + 快捷键提示；`/thinking` 命令可开关思考块显示。
- **Web**：欢迎屏含 ASCII art 徽标与已加载模块徽章；思考块为可折叠 `<details>` 元素；工具调用渲染为 `→ Read file` 行（由 `/api/chat` 响应的 `tools` 字段驱动）；状态栏含 Modules 计数与 `■■⬝⬝` 进度条。

---

*文档版本: v1.0.0 · 最后更新: 2026-08-04*
