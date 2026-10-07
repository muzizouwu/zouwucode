# ZOUWUCODE 完整技术规格文档

> 本档记录了 ZOUWUCODE 项目的完整设计思想、架构细节、技术实现与开发历程，供在新目录更换技术栈重制时参考。

---

## 目录

1. [项目概述](#1-项目概述)
2. [核心设计哲学](#2-核心设计哲学)
3. [功能特性总览](#3-功能特性总览)
4. [架构设计](#4-架构设计)
5. [模块详解](#5-模块详解)
6. [文件结构](#6-文件结构)
7. [配置系统](#7-配置系统)
8. [开发历程与关键决策](#8-开发历程与关键决策)
9. [已解决的 Bug 与修复方案](#9-已解决的-bug-与修复方案)
10. [技术栈与依赖](#10-技术栈与依赖)
11. [测试体系](#11-测试体系)
12. [移植指南](#12-移植指南)

---

## 1. 项目概述

**ZOUWUCODE** 是一款 DeepSeek-native 的终端 AI 编程 Agent，整合了以下开源项目的设计理念：

| 灵感来源 | 贡献 |
|----------|------|
| [Reasonix](https://github.com/esengine/DeepSeek-Reasonix) | Cache-First 引擎循环设计，append-only 对话模式 |
| [DeepSeek-TUI](https://github.com/Hmbown/DeepSeek-TUI) | 多模式工作流（Plan/Agent/YOLO） |
| [Deep Code](https://github.com/HKUDS/DeepCode) | 多智能体编排、三档推理强度 |
| [Claude Code](https://anthropic.com/claude-code) | 架构设计参考、权限沙箱、子 Agent 系统 |

### 核心定位

- **目标用户**：开发者，用于辅助编程、代码审查、项目管理和自动化任务
- **核心优势**：围绕 DeepSeek prefix-cache 深度优化，长会话缓存命中率 90%+，成本降至约 1/5
- **交互方式**：CLI / TUI / Web UI 三种界面，适配不同场景
- **运行环境**：Windows 优先，Python 3.10+，支持 PyInstaller 打包为独立 exe

---

## 2. 核心设计哲学

### 2.1 Cache-First（缓存优先）

这是项目的核心设计原则，确保 DeepSeek 服务端 prefix-cache 的高命中率，从而大幅降低成本。

**关键规则**：
1. **Append-only**：对话历史永不重排、永不插入、永不删除 — 只追加
2. **冻结前缀**：系统提示词和工具 Schema 在会话开始时一次性冻结，之后不变
3. **字节稳定性**：任何动态内容（如时间戳、项目记忆）必须在冻结前缀中，或在用户消息中
4. **无随机内容**：避免在系统提示词中包含随机子或动态内容

### 2.2 三层上下文管理

参考 Claude Code 的上下文管理策略，将上下文分为三个层次：

| 层级 | 存储位置 | 特点 | 加载策略 |
|------|----------|------|----------|
| 轻量级索引 | MEMORY.md | 每行 ~150 字符 | 始终加载到提示词中 |
| 项目知识 | `.zouwucode/topics/` | 按需获取的专题文件 | 模型请求时才加载 |
| 原始对话 | `zouwucode_data/transcripts/` | 完整对话日志 | 全文 grep，永不完整加载 |

### 2.3 多模式安全体系

三种工作模式对应不同的安全级别：

| 模式 | 工具调用 | 适用场景 |
|------|----------|----------|
| Plan | 不允许修改 | 只读代码浏览、方案分析 |
| Agent | 需用户确认 | 日常开发，安全可控 |
| YOLO | 自动执行 | 明确任务、批量操作 |

### 2.4 便携式设计

- **不写 C 盘**：所有数据文件存储在 `zouwucode_data/` 同级目录
- **不依赖系统 Python**：可打包为独立 exe（PyInstaller）
- **便携式**：可复制到 U 盘或任何位置使用

---

## 3. 功能特性总览

### 3.1 核心引擎特性

| 特性 | 说明 | 实现位置 |
|------|------|----------|
| Cache-First 引擎 | Append-only 对话循环，缓存命中率 90%+ | `engine/loop.py`, `engine/cache.py` |
| DeepSeek V4 优化 | 专用适配器，prefix-cache 感知 | `engine/providers/deepseek.py` |
| 三档推理强度 | low / medium / max 映射 reason_effort | `deepseek.py` + `config.py` |
| 流式思考显示 | 思考过程增量实时推送（opencode 风格），`/thinking` 三端开关 | `loop.py` + CLI/TUI/Web |
| 任务打断 | Esc（TUI/Web）/ Ctrl+C（CLI）请求打断，在安全点（流 chunk / 轮边界 / 工具执行前）生效，抛出 `TaskInterrupted` | `engine/loop.py` |
| 引擎安全限制 | 工具轮数上限、LLM/任务超时、连续工具错误熔断、单任务成本熔断（`max_cost_usd`）、卡死检测（动作指纹滑窗），超限抛出 `TurnLimitExceeded` | `engine/loop.py` + `config.py`（EngineConfig） |
| 多模型支持 | DeepSeek + OpenAI 兼容 | `engine/providers/` |

### 3.2 界面特性

| 界面 | 启动方式 | 风格参考 | 特点 |
|------|----------|----------|------|
| CLI | 默认 | OpenAI Code 风格 | 轻量，提示符 `z a >` |
| TUI | `--tui` | OpenAI Code 风格 | Textual 框架，彩色消息 |
| Web UI | `--web` | DeepCode 暗色风格 | 浏览器界面，API 端点 |

### 3.3 工具系统（10 个内置工具 + task 委派工具）

| 工具 | 类别 | 说明 |
|------|------|------|
| Read | 文件 | 读取文件内容（带行号） |
| Write | 文件 | 写入/创建文件 |
| Edit | 文件 | 精确字符串替换编辑 |
| Ls | 文件 | 列出目录内容 |
| Glob | 文件 | 通配符文件搜索 |
| Shell | 执行 | Shell 命令执行（沙箱保护） |
| python_exec | 执行 | 持久 Python REPL（CodeAct 可执行动作，跨调用保留变量/导入） |
| Git | 执行 | Git 操作（status/diff/commit/log） |
| WebSearch | 网络 | 联网搜索 |
| WebFetch | 网络 | 获取网页内容 |
| task | 委派 | 将子任务并行委派给隔离子 Agent（见 5.3） |

### 3.4 高级特性

| 特性 | 说明 | 实现位置 |
|------|------|----------|
| 持久项目记忆 | 跨会话保存目标、决策、状态、摘要 | `project_memory.py` |
| 对话压缩 | 三级压缩（lossless / balanced / aggressive） | `context/compressor.py` |
| 滑动窗口 | Hot / Warm / Cold / Archive 四层上下文管理 | `context/sliding_window.py` |
| 权限沙箱 | 危险命令检测、零宽字符防护、路径白名单 | `sandbox/permission.py` |
| 会话管理 | 自动保存、恢复、回滚 | `session/manager.py` |
| 子 Agent 系统 | 主 Agent + 隔离子 Agent 并行执行（task 工具委派） | `agent/subagent.py`, `tools/agent_tools.py` |
| 扩展层 | ExtensionHost 统一宿主，MCP / LSP 扩展默认不激活 | `extensions/` |
| Devin 式 dev 模式 | `zouwucode dev`：规划→实现→多层验证→独立审查→Draft PR→CI 联动；SQLite 队列 + 并行 worker + watch 标签轮询 | `dev/` |
| 评测 harness | `zouwucode eval`：真实 agent 栈 + 确定性行为断言，通过率/成本度量 | `eval/` |
| 生命周期钩子 | pre_tool（可拦截）/ post_tool（自动化）shell 钩子，配置驱动 | `agent/hooks.py` |
| Rules & Skills | `.zouwucode/rules.md` 项目规则 + `.zouwucode/skills/*.md` 技能包 | `skills/manager.py` |
| 模块系统 | 可加载扩展模块，内置 hello-my-zouwucode 多智能体编排 | `modules/manager.py`, `hello_my_zouwucode/` |
| MCP 协议 | 支持 Model Context Protocol 扩展 | `mcp/client.py` |
| LSP 集成 | 编辑后自动诊断 | `lsp/client.py` |
| 便捷启动 | `z` 命令一键启动，PowerShell 安装脚本 | `z.bat`, `install.ps1` |
| 流式思考 | 思考过程实时逐字显示，`show_thinking` 默认开启，`/thinking` 三端统一开关 | `loop.py` + CLI/TUI/Web |

---

## 4. 架构设计

### 4.1 整体架构图

```
┌─────────────────────────────────────────────────────────────────────┐
│                         用户界面层                                    │
│  ┌──────────┐  ┌──────────────────┐  ┌──────────────────────────┐   │
│  │  CLI     │  │  TUI (Textual)   │  │  Web UI (HTTP Server)   │   │
│  │ app.py   │  │  textual_app.py  │  │  server.py              │   │
│  └────┬─────┘  └────────┬─────────┘  └────────────┬─────────────┘   │
│       │                 │                          │                  │
├───────┴─────────────────┴──────────────────────────┴────────────────┤
│                         应用层                                        │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │  ZOUWUCODEApp (tui/app.py)                                  │   │
│  │  负责组件编排、会话生命周期、消息处理管道                       │   │
│  └──────────────────────────────────────────────────────────────┘   │
├─────────────────────────────────────────────────────────────────────┤
│                         核心引擎层                                    │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │  EngineLoop (engine/loop.py)                                │   │
│  │  Append-only 对话循环，工具调用调度，缓存管理                    │   │
│  └──────────┬───────────────────────────────────────────────────┘   │
│             │                                                        │
│  ┌──────────┴──────────┐  ┌──────────────────────────────────────┐  │
│  │  PrefixCache        │  │  BaseProvider / DeepSeekProvider    │  │
│  │  (engine/cache.py)  │  │  (engine/providers/)                │  │
│  └─────────────────────┘  └──────────────────────────────────────┘  │
├─────────────────────────────────────────────────────────────────────┤
│                         支撑服务层                                    │
│  ┌───────────┐ ┌──────────┐ ┌───────────┐ ┌──────────┐ ┌───────┐  │
│  │  Tools    │ │ SubAgent │ │ Context   │ │ Sandbox  │ │Session│  │
│  │  Registry │ │ Manager  │ │ Manager   │ │ Manager  │ │Manager│  │
│  └───────────┘ └──────────┘ └───────────┘ └──────────┘ └───────┘  │
│  ┌───────────┐ ┌──────────┐ ┌───────────┐ ┌──────────┐ ┌───────┐  │
│  │Extensions │ │ Modules  │ │ Skills/   │ │ Project  │ │ MCP/  │  │
│  │ Host      │ │ Manager  │ │ Rules     │ │ Memory   │ │ LSP   │  │
│  └───────────┘ └──────────┘ └───────────┘ └──────────┘ └───────┘  │
└─────────────────────────────────────────────────────────────────────┘
```

### 4.2 消息处理管道

当用户发送一条消息时，经过以下管道：

```
用户输入
    │
    ▼
[1] 读取项目记忆上下文（内容变化时才作为 system 消息随本轮发送）
    │
    ▼
[2] 构建本轮新增消息（变更的项目记忆 system 消息 + 用户消息；
    历史由引擎 PrefixCache 持有，UI 每轮只发新增消息，避免重复上下文）
    │
    ▼
[3] 添加到上下文窗口 (context_window.add_to_warm，仅统计)
    │
    ▼
[4] 引擎执行 (engine.run，迭代式多轮循环)
    │
    ├──[4a] 追加本轮新增消息到缓存 (cache.append)
    │
    ├──[4b] 调用 LLM 获取响应 (provider.chat_stream，受 turn_timeout 限制)
    │   └── 增量流式：思考/正文 delta 经 on_thinking_delta / on_content_delta
    │       回调实时推送到 CLI/TUI/Web 三端渲染（show_thinking 控制）
    │
    ├──[4c] 追加助手响应到缓存 (cache.append，含 tool_calls)
    │
    ├──[4d] 处理工具调用 (如果存在且非 plan 模式)
    │   ├── 执行工具 (coordinator.execute_tool)
    │   ├── 追加工具结果到缓存 (cache.append)
    │   └── 进入下一轮迭代（模型经 cache.get_prefix() 看到工具结果；
    │       受 max_tool_rounds / task_timeout / 连续错误熔断约束）
    │
    ├──[4e] 打断：request_interrupt() 在安全点（流 chunk / 轮边界 /
    │       工具执行前）抛出 TaskInterrupted，未执行的 tool_calls
    │       以 synthetic "[interrupted]" 结果补齐，保持前缀 API 有效
    │
    ▼
[5] 记录会话日志 (sessions.log_turn)
    │
    ▼
[6] 添加到上下文窗口 (context_window.add_to_warm)
    │
    ▼
[7] 返回响应给用户界面
```

### 4.3 缓存生命周期

```
会话开始
    │
    ▼
[1] freeze_session(system_prompt, tool_schemas)
    ├── 冻结系统提示词 (provider.freeze_system_prompt)
    ├── 冻结工具 Schema (provider.freeze_tool_schemas)
    └── 重置本地缓存追踪器并冻结系统消息 (cache.reset + cache.freeze)
    │
    ▼
[2] 每轮对话
    ├── cache.append(user_msg)
    ├── 调用 LLM (发送 cache.get_prefix())
    ├── 记录缓存统计 (stats.record_turn)
    └── cache.append(assistant_msg)
    │
    ▼
[3] 会话结束
    └── 保存统计数据
```

---

## 5. 模块详解

### 5.1 `engine/` — 核心引擎

#### `engine/loop.py` — EngineLoop

**职责**：Cache-First 的 append-only 对话循环，是项目的核心执行引擎。

**关键类**：
- `TurnContext`：单轮对话的上下文，包含消息、工具、模式、响应时间等
- `EngineLoop`：主引擎类，管理缓存、调用 LLM、执行工具调用
- `TurnLimitExceeded`：触发安全限制（轮数 / 超时 / 连续工具错误）时抛出，防止无限工具循环
- `TaskInterrupted`：用户打断任务时抛出，与安全限制区分以便调用方分别处理

**关键方法**：
- `set_reasoning_intensity(level)`：将三档推理强度传递给 provider
- `freeze_session(system_prompt, tool_schemas)`：冻结系统提示词和工具 Schema，重置缓存
- `run(messages, tools, temperature, max_tokens)`：执行单个任务（迭代式、有界）；每轮 = 一次 LLM 请求 + 其工具调用，工具结果追加进缓存后进入下一轮迭代；聚合 Provider 增量 delta，经 `on_thinking_delta` / `on_content_delta` 回调实时派发给 UI 层（可为 None）
- `request_interrupt(reason)`：请求打断当前任务（任意线程/异步上下文安全），在下一个安全点生效；级联触发 `on_interrupt` 钩子
- `cancel_pending_io()`：取消仍在进行的 LLM 流任务（KeyboardInterrupt / CancelledError 突然展开时使用）
- `plan(messages)`：只读模式执行
- `get_cache_summary()`：返回缓存性能统计

**可选回调**：
- `on_tool_event`：工具执行前推送 ToolCall（opencode 风格 `→ Read file …` 行渲染）
- `on_thinking_delta` / `on_content_delta`：流式思考/正文增量回调
- `on_interrupt`：本引擎被打断时级联调用（SubAgentManager 用于停止所有运行中的子 Agent）

**重要设计决策**：
- `run()` 采用迭代循环而非递归：每轮一次 LLM 请求 + 其工具调用，多重安全限制（`max_tool_rounds` / `task_timeout_seconds` / `max_consecutive_tool_errors` / 成本熔断 / **卡死检测**）防止死循环与停顿
- **卡死检测（stuck detection）**：滑动窗口记录每步动作指纹 `(tool, arguments, result)` 的 SHA1；同一指纹在窗口内重复达 `stuck_threshold` → 先注入一条 user 提醒（换思路/重规划/总结止损，措辞为信息而非禁令），再犯则抛 `TurnLimitExceeded`。补齐"工具全部成功但模型原地打转"这一连续错误熔断无法覆盖的死循环形态；提醒注入点在本轮工具结果之后，保持 append-only 前缀 API 合法
- 助手响应必须在缓存中追加（含 `tool_calls`），否则后续的 `tool` 角色消息会报 400 错误
- `freeze_session` 中调用 `cache.reset()` 确保跨会话缓存不累积
- 打断后前缀清理：若带 `tool_calls` 的助手消息已追加，为每个未执行的调用补齐 synthetic "[interrupted]" 工具结果，保持前缀 API 有效
- Provider 流式约定：增量 yield 思考/正文 delta，末帧只携带元数据（`content=""`/`thinking=""`），避免引擎聚合时内容翻倍

#### `engine/cache.py` — PrefixCache & CacheStats

**职责**：管理前缀缓存和统计，确保 DeepSeek 服务端缓存命中率。

**关键类**：
- `PrefixCache`：前缀缓存管理器，维护 append-only 消息列表
- `CacheStats`：缓存性能统计，计算命中率、成本节省等

**关键方法**：
- `freeze(messages)`：冻结初始消息前缀（仅执行一次）
- `append(message)`：追加消息（永不 prepend 或 reorder）
- `get_prefix()`：返回完整的 append-only 消息列表
- `reset()`：重置缓存（新会话时调用）

**成本计算**（基于 DeepSeek V4-Flash 定价）：
- 未缓存输入：$0.14 / 1M tokens
- 缓存输入：$0.028 / 1M tokens（约 1/5）
- 输出：$0.28 / 1M tokens

#### `engine/providers/base.py` — 基础抽象

**关键类**：
- `Message`：单条消息
- `ToolCall`：工具调用请求
- `ToolResult`：工具执行结果
- `ModelResponse`：模型响应（含 content、tool_calls、thinking、usage、cache_hit）
- `BaseProvider`：LLM 提供商抽象基类

**抽象方法**：
- `chat_stream()`：流式聊天补全
- `chat()`：非流式聊天补全

#### `engine/providers/deepseek.py` — DeepSeekProvider

**职责**：针对 DeepSeek API 的优化适配器。

**关键设计**：
- `base_url` 为空时默认使用 `https://api.deepseek.com`
- 系统提示词在 `__init__` 中处理，避免重复发送
- `_build_messages()` 检查是否已有 system 角色，避免重复
- `_build_tools()` 使用冻结的 Schema
- 推理强度映射：low → 不发送 reasoning_effort，medium → "high"，max → "max"
- 流式解析处理 `reasoning_content` 和 `tool_calls`
- 缓存命中检测：`prompt_cache_hit_tokens / prompt_tokens > 0.5`

#### `engine/providers/openai.py` — OpenAIProvider

**职责**：通用 OpenAI 兼容提供商（可适配 OpenAI、Ollama 等）。

### 5.2 `tools/` — 工具系统

#### `tools/base.py` — 基础定义

- `ToolSpec`：工具的规范定义（name、description、parameters、required）
- `ToolResult`：工具执行结果（success、output、error）
- `BaseTool`：工具抽象基类

#### `tools/registry.py` — ToolRegistry

**职责**：中央工具注册中心，管理所有工具及其 Schema。

**关键设计**：
- 工具注册后冻结 Schema，确保字节稳定性
- `get_schemas()` 返回缓存的冻结 Schema

#### `tools/file_tools.py` — 文件操作工具

| 工具 | 功能 | 关键参数 |
|------|------|----------|
| ReadTool | 读取文件内容 | file_path, offset, limit |
| WriteTool | 写入/创建文件 | file_path, content |
| EditTool | 精确字符串替换编辑 | file_path, old_string, new_string |
| LsTool | 列出目录 | path |
| GlobTool | 通配符搜索 | pattern, path |

#### `tools/shell_tools.py` — ShellTool

**职责**：执行 Shell 命令，集成沙箱安全检查。

#### `tools/code_exec_tool.py` — PythonExecTool（python_exec，CodeAct 行动面）

**职责**：持久 Python REPL 作为 agent 的第二行动面——模型用代码组合多步操作（循环、条件、自写验证脚本），复杂任务上优于逐条 JSON 工具调用（OpenHands CodeAct 的核心洞察）。

**关键设计**：
- 每工具实例一个长驻 `python -u` 子进程，跑自驱动 REPL 驱动脚本（readline 循环，不等 EOF——普通 `python -u` 读管道 stdin 会挂起）
- 线协议：行1=帧长，行2=`<sentinel> <base64(code)>`（纯 ASCII，免疫编码/换行问题）；驱动执行后打印 sentinel 行，父端按行收集到 sentinel 为止，stdout 切分精确
- 命名空间跨调用持久（变量/导入保留）——这是 REPL 的意义
- 安全：复用 PermissionManager 命令筛查 + 破坏性模式正则（`rm -rf /`、`shutil.rmtree('/')`、`os.system("rm …")` 等）；单调用硬超时 → kill 并重启会话；输出截断 30k
- 进程级防护而非容器隔离——与项目本地优先姿态一致的文档化取舍（OpenHands 靠 Docker workspace 拿容器隔离）

#### `tools/git_tools.py` — GitTool

**职责**：执行 Git 操作，支持 status、diff、commit、log 等。

#### `tools/web_tools.py` — Web 工具

| 工具 | 功能 |
|------|------|
| WebSearchTool | 联网搜索（使用 httpx） |
| WebFetchTool | 获取网页内容（使用 BeautifulSoup 解析） |

#### `tools/agent_tools.py` — TaskTool（task 委派工具）

**职责**：让主模型分解并委派工作。模型调用 `task` 工具传入子任务列表（每项含 role / instructions / task，可选 tools 白名单），每个子任务由隔离子 Agent 并行执行，所有子 Agent 的报告合并为一个工具结果返回。

### 5.3 `agent/` — 多 Agent 编排

#### `agent/coordinator.py` — AgentCoordinator

**职责**：引擎的工具执行回调，将 ToolCall 转为 ToolResult。

**关键设计**：
- `execute_tool()` 方法作为回调传递给 EngineLoop
- **生命周期钩子**：执行前跑 pre_tool 钩子（`agent/hooks.py` HookRunner），非零退出或 stdout `{"block": true, "reason": ...}` 即拦截调用并把原因作为错误结果回传模型（模型能看到 WHY 并调整）；执行后跑 post_tool 钩子（观察/自动化，失败绝不影响工具结果）。钩子配置 `extensions.hooks` 为空时零开销
- 工具参数是流式返回的 JSON 字符串，先 `json.loads` 解析再解包（防 `**` 解包 TypeError），非 dict 返回错误结果
- 派发到 ToolRegistry 执行，并将工具层 `ToolResult(success/output/error)` 转为引擎层 `ToolResult(tool_call_id/content/is_error)`
- 模式差异（plan 只读跳过工具、YOLO 自动执行）由引擎层处理，协调器不做权限审批

#### `agent/hooks.py` — HookRunner（生命周期钩子）

**职责**：Claude Code 风格的确定性自动化/治理扩展点（其 27 事件钩子体系中最有价值的两个）。

- `HookConfig(event, command, tool_pattern)`：`pre_tool` 可拦截、`post_tool` 观察；`tool_pattern` 按工具名子串过滤
- 命令模板支持 `{tool}` `{args}` `{result}` 占位符
- 单钩子 10s 超时：pre_tool 超时按拦截处理（fail-closed），post_tool 超时仅告警（fail-open）——挂死的钩子永远不会卡住主循环

#### `agent/subagent.py` — SubAgent & SubAgentManager

**职责**：子 Agent 系统，为每个子 Agent 提供独立引擎，实现安全并行。

**关键设计**：
- `SubAgent` 持有私有 EngineLoop（共享 provider 与工具注册表，隔离 PrefixCache / 缓存统计 / 打断状态），与主 Agent 的缓存/上下文互不交错
- 工具白名单端到端生效：既过滤传给模型的 Schema 列表，也在执行器层拦截越权调用
- `SubAgentManager` 统一创建与调度子 Agent（受 `subagent.max_agents` 上限约束），`run_parallel()` 按 spec 并行执行并支持每 Agent 超时
- 状态机 `AgentStatus`：pending / running / completed / failed / cancelled
- 主任务打断时通过引擎 `on_interrupt` 钩子级联停止所有运行中的子 Agent（`bind_main_engine`）
- `/agents` 命令通过 `status_summary()` 查看子 Agent 状态

### 5.4 `context/` — 上下文管理

#### `context/memory.py` — MemoryManager

**职责**：管理 MEMORY.md 轻量级索引文件。

**关键设计**：
- 始终加载到 LLM 上下文
- 添加条目时超过 200 字符自动截断（197 字符 + `...`）
- 提供 add/update/remove/get_context 等操作

#### `context/topics.py` — TopicManager

**职责**：管理按需加载的专题知识文件。

#### `context/transcript.py` — TranscriptManager

**职责**：管理原始对话日志，支持 grep 搜索。

#### `context/compressor.py` — DialogueCompressor

**职责**：智能对话压缩引擎。

**三级压缩策略**：
| 级别 | 行为 |
|------|------|
| lossless | 仅移除空消息，保留所有内容 |
| balanced | 保持系统提示、决策、文件内容，压缩长对话 |
| aggressive | 对长助手回复进行摘要，大幅压缩上下文 |

**压缩算法**：
1. 识别并移除冗余/重复信息
2. 将工具调用序列总结为紧凑描述
3. 保留关键决策、代码变更和错误状态
4. 将消息分类为：keep-full、keep-summary、discard
5. 当上下文接近 token 限制时自动运行

**关键方法**：
- `compress_conversation(messages, keep_last_n, max_context_tokens)`：主入口
- `_compress_block(messages)`：根据级别压缩块
- `_group_interactions(messages)`：将消息分组为交互单元
- `_summarize_interaction_group(group)`：总结交互组

#### `context/sliding_window.py` — ContextWindow

**职责**：滑动窗口上下文管理器。

**四层设计**：
| 层级 | 说明 | 策略 |
|------|------|------|
| Hot | 始终保留（系统提示、当前任务） | 永不删除 |
| Warm | 最近 N 轮对话 | 保留不压缩，默认 50 轮 |
| Cold | 历史对话 | 自动滑动归档，压缩后保留 |
| Archive | 参考材料 | 按需加载 |

**滑动机制**：
- 当 Warm 超过轮次上限的 2 倍或 token 限制时，将较旧的一半 Warm 消息移到 Cold
- Cold 中的消息经过压缩后再添加
- 通过 `_estimate_tokens()` 粗略估算 token 数

### 5.5 `sandbox/` — 权限沙箱

#### `sandbox/permission.py` — PermissionManager

**职责**：多层安全沙箱，用于工具执行权限控制。

**安全检查层**（Claude Code 风格）：
1. 零宽字符注入检测（`\u200b`、`\u200c` 等）
2. 危险命令模式匹配（rm -rf /、sudo、format 等）
3. 命令黑名单检查
4. 命令白名单检查（如果设置了白名单，仅允许白名单中的命令）
5. 文件路径白名单检查（仅允许在工作区目录内操作）
6. 网络访问控制
7. Git 操作控制

**危险命令模式**（11 个正则模式）：
- 根文件系统删除：`rm -rf /`、`del /` 等
- 通配符删除：`rm -rf *`
- 文件系统格式化：`mkfs`、`format`、`dd`、`fdisk`
- 权限提升：`sudo`、`runas`、`su`
- 权限滥用：`chmod 777`、`chown`、`attrib`
- 设备覆盖：`> /dev/`
- Pipe-to-shell：`curl | bash`、`wget | powershell`
- 编码命令执行：`-EncodedCommand`
- PowerShell IEX：`Invoke-Expression`
- PowerShell 进程启动：`Start-Process`、`Invoke-Item`
- 注册表修改：`reg`、`regedit`

### 5.6 `session/` — 会话管理

#### `session/manager.py` — SessionManager

**职责**：管理会话的持久化、恢复和回滚。

**关键设计**：
- 会话数据存储为 JSON 文件，位于 `zouwucode_data/sessions/`
- 自动保存机制
- 支持会话列表查看和删除
- 每个会话包含：session_id、started_at、updated_at、turns

### 5.7 `mcp/` — MCP 协议支持

#### `mcp/client.py` — MCPClient

**职责**：连接外部 MCP（Model Context Protocol）服务器。

**支持的传输方式**：
- stdio：子进程通信（当前已实现，`connect_stdio`）
- SSE：Server-Sent Events（预留声明，尚未接线）
- Streamable HTTP：HTTP 流式传输（预留声明，尚未接线）

#### `extensions/mcp_ext.py` — McpExtension

**职责**：MCP 扩展适配器（预留，默认不激活）。配置 `extensions.mcp_servers` 后，`start()` 通过 MCPClient 连接各服务器（stdio），并把每个服务器工具包装为 `MCPTool`（命名 `{server}__{tool}`）合并进 ToolRegistry，走常规沙箱/协调器流程。

### 5.8 `lsp/` — LSP 集成

#### `lsp/client.py` — LSPClient

**职责**：编辑后自动获取语言服务器诊断信息。

**支持的语言**：
- Python → pyright-langserver
- JavaScript/TypeScript → typescript-language-server
- Rust → rust-analyzer
- Go → gopls
- Java → eclipse-jdtls
- C/C++ → clangd

#### `extensions/lsp_ext.py` — LspExtension

**职责**：LSP 诊断扩展（预留，`extensions.lsp_enabled` 默认 false）。启用后注册 `check_diagnostics` 工具，模型可在编辑后主动查询诊断（自修复）。

### 5.9 `project_memory.py` — 持久项目记忆

**职责**：跨会话的项目状态持久化。

**存储结构**（位于 `.zouwucode/memory/`）：
```
.zouwucode/memory/
├── project_state.json   # 项目状态（名称、描述、目标等）
├── decisions.json       # 架构决策记录
├── goals.json           # 项目目标列表
└── summaries.json       # 会话摘要
```

**关键类**：`ProjectMemory`

**关键方法**：
- `load_all()` / `save_all()`：从磁盘加载/保存所有状态
- `get_state(key)` / `set_state(key, value)`：读写项目状态
- `add_decision(title, detail, category)`：记录架构决策
- `add_summary(session_id, summary, turn_count)`：记录会话摘要
- `set_goal(goal)` / `complete_goal(goal)`：管理项目目标
- `snapshot_project()`：拍摄项目快照
- `get_full_context()`：获取所有上下文块（提供给 LLM）

### 5.10 `tui/app.py` — CLI 主应用 & ZOUWUCODEApp

**职责**：应用主类，将所有组件串联在一起，提供 CLI、Web UI、单消息三种运行模式。

**关键组件初始化**：
1. `ZOUWUCODEConfig.load()` 加载配置
2. `ProjectMemory` 加载项目记忆
3. `DeepSeekProvider` / `OpenAIProvider` 创建 LLM 提供商（经 `runtime.create_provider` 工厂）
4. `EngineLoop` 创建引擎
5. `PermissionManager` 创建沙箱
6. `ToolRegistry` 注册 10 个内置工具 + `task` 委派工具
7. `AgentCoordinator` 创建协调器，`SubAgentManager` 创建子 Agent 管理器并绑定主引擎级联打断
8. `ExtensionHost` 注册 `McpExtension` / `LspExtension`（默认不激活）
9. `MemoryManager`、`TopicManager`、`TranscriptManager` 创建上下文管理
10. `DialogueCompressor`、`ContextWindow` 创建压缩和窗口
11. `SessionManager`、`RuleLoader` / `SkillsManager`、`ModuleManager`（加载 hello-my-zouwucode 模块）创建支撑服务

**关键方法**：
- `initialize_session()`：初始化会话，冻结系统提示词和工具 Schema
- `process_message(message)`：核心消息处理管道（每轮只发新增消息给引擎）
- `run_interactive()`：CLI 交互循环（先启动扩展层，再初始化会话）
- `run_web(port)`：启动 Web UI 服务器
- `run_single(message)`：单消息模式
- `set_mode(mode)` / `set_reasoning_intensity(level)`：运行时切换

**打断处理**：CLI 运行中按 Ctrl+C 调用 `engine.request_interrupt(reason="CLI Ctrl+C")`；引擎抛出 `TaskInterrupted` 后提示「任务已打断。输入「继续」从断点恢复，或输入 /clear 放弃本次任务」。

**流式思考接线**：CLI 在 `run_interactive()` / `run_single()` 中按 `config.show_thinking` 动态挂载 `engine.on_thinking_delta → _stream_thinking`（思考增量实时打印，结束时解绑），并初始化 `_thinking_streaming` 状态；`/thinking` 命令切换 `config.show_thinking`。思考开关关闭时不显示任何思考块。

**CLI 提示符格式**：`z {mode_initial} > `（如 `z a > `、`z p > `、`z y > `）

### 5.11 `tui/textual_app.py` — Textual TUI

**职责**：基于 Textual 框架的终端图形界面。

**功能**：
- 聊天视图（RichLog 组件，支持 Rich 标记）
- 文本输入框
- 状态栏（模式、推理强度、缓存统计、上下文窗口信息）+ 底部进度条（opencode 风格方块 + 百分比）
- 模式切换快捷键（Ctrl+S 循环 plan→agent→yolo）
- 推理强度循环（Ctrl+R：low→medium→max→low）
- 清屏（Ctrl+L）
- 打断（Esc）：任务运行中弹出 `InterruptConfirmScreen` 确认对话框（Enter 确认 / Esc 取消），确认后调用 `engine.request_interrupt(reason="TUI Esc")`；空闲时 Esc 聚焦输入框
- 工具调用行渲染（`engine.on_tool_event`，opencode 风格 `→ Read file …`）
- 流式思考显示（`_on_thinking_delta` 行缓冲实时写入 RichLog，`💭` 前缀；`/thinking` 开关并同步 `config.show_thinking`；关闭时仅显示"Thinking..."占位）
- 斜杠命令：与 CLI 一致（含 `/agents`、`/rules`、`/skill` 与 hello-my-zouwucode 模块命令）

**CSS 样式**：`styles.tcss`

### 5.12 `webui/server.py` — Web UI 服务器

**职责**：基于 Python asyncio 的本地 HTTP 服务器，提供 DeepCode 暗色风格的浏览器界面。

**HTML 模板**：内嵌在 `server.py` 中的完整 HTML 页面（`HTML_TEMPLATE`）

**API 端点**：
| 端点 | 方法 | 说明 |
|------|------|------|
| `/` | GET | HTML 页面 |
| `/api/chat` | POST | 发送消息（JSON: `{"message":"..."}`，模块/兼容路径；同一时刻仅允许一个任务） |
| `/api/chat/stream` | POST | **SSE 流式聊天**：思考增量 `{"type":"thinking","delta":...}` 实时推送，`{"type":"interrupted",...}` 打断收尾，`{"type":"done",...}` 正常收尾 |
| `/api/interrupt` | POST | 打断当前运行中的任务（Web Esc / ⏹ 按钮，与 TUI 对齐） |
| `/api/thinking` | POST | 切换思考显示（返回 `{"show_thinking": bool}`） |
| `/api/mode` | POST | 切换模式（JSON: `{"mode":"plan"}`） |
| `/api/reasoning` | POST | 设置推理强度（JSON: `{"reasoning":"max"}`） |
| `/api/status` | GET | 获取运行状态（含 `show_thinking`、`running`、`context_tokens` 等） |
| `/api/cache` | GET | 获取缓存统计 |
| `/api/sessions` | GET | 会话列表 |
| `/api/rules` | GET | 项目规则（`.zouwucode/rules.md`） |
| `/api/skills` | GET/POST | 技能列表 / 加载技能 |
| `/api/hello-my-zouwucode` | POST | hello-my-zouwucode 模块命令（规划/执行/状态） |

**Web UI 界面布局**：
- 顶部标题栏：应用名称 + 版本号
- 模式切换选项卡：Agent / Plan / YOLO
- 推理强度选择器（下拉框）
- 状态栏：连接状态、模式、推理强度、思考显示（Thinking ON/OFF）、缓存命中率、成本、请求数
- 侧边栏：会话列表 + 新建会话按钮 + API 配置状态
- 聊天区域：消息显示，支持用户/助手/思考/系统四种消息类型
- 输入区域：textarea + 发送按钮（Ctrl+Enter 发送）+ 打断按钮（⏹ 打断，Esc 触发，运行中显示）

**流式实现**：`_handle_chat_stream()` 将 `engine.on_thinking_delta` 桥接到 `asyncio.Queue`，逐条以 SSE `data:` 事件写出；前端用 `fetch` + `ReadableStream` 消费，`addThinkingStream()` 创建可折叠的「💭 Thinking」块实时追加增量文本；`/thinking` 命令与状态栏 Thinking 状态经 `/api/thinking` 同步。

### 5.13 `config.py` — 配置管理

**职责**：使用 Pydantic BaseModel 管理应用配置。

**配置模型**：
- `ProviderConfig`：LLM 提供商配置（api_key、base_url、model、api_type）
- `EngineConfig`：引擎安全限制（max_tool_rounds=25、turn_timeout_seconds=300、task_timeout_seconds=1800、max_consecutive_tool_errors=3、max_llm_retries=2、max_cost_usd=0 成本熔断、stuck_detection_enabled/stuck_window/stuck_threshold 卡死检测）
- `CacheConfig`：缓存配置（enabled、max_prefix_tokens、append_only、stats_window）
- `SandboxConfig`：沙箱配置（enabled、default_mode、allow_*、allowed_paths）
- `SessionConfig`：会话配置（save_enabled、auto_save_interval、max_sessions、rollback_enabled）
- `SubAgentConfig`：子 Agent 配置（max_agents=8、default_timeout=600）
- `McpServerConfig`：单个 MCP 服务器连接（name、command、args）
- `ExtensionsConfig`：扩展层配置（mcp_servers=[]、lsp_enabled=false、hooks=[] 生命周期钩子，默认不激活）
- `HelloMyZouwucodeConfig`：hello-my-zouwucode 模块配置（enabled、state_dir、default_category、max_review_rounds、interactive_planning）
- `GithubConfig`：GitHub 集成（token、api_base；GITHUB_TOKEN 环境变量优先）
- `DevConfig`：dev 模式（branch_prefix="dev"、worktree_dir、test_command、verify_retries=2、max_concurrent_tasks=3、task_timeout_seconds=3600、watch_label="zouwucode:do"、draft_pr=true；质量门禁：lint/typecheck/security_command、coverage_min、review_enabled+review_max_rounds、ci_check_enabled+ci_wait_seconds+ci_poll_interval、adaptive_budget+task_cost_budget_usd+budget_escalations、plan_enabled 显式规划）
- `ZOUWUCODEConfig`：根配置（providers、default_provider、engine、cache、sandbox、session、subagent、extensions、hello_my_zouwucode、github、dev、reasoning_intensity、show_thinking、data_dir、theme、language）

**配置加载优先级**：
1. 当前工作目录的 `config.yaml`
2. `zouwucode_data/config.yaml`（CWD 同级数据目录）
3. 安装目录的 `config.yaml`（pip -e 安装场景）
4. 都不存在则返回默认配置

### 5.14 `__main__.py` — 入口点

**职责**：CLI 入口，解析命令行参数，启动对应界面。

**dev 子命令前置路由**：`main()` 开头检查 `sys.argv[1] == "dev"`，命中则把 `sys.argv[2:]` 交给 `dev.cli.dev_main()` 并 `sys.exit()`，不进入主 argparse——dev 模式因此拥有完全独立的参数空间（`--queue/--workers/--watch/--status` 等，见 5.19）。

**支持的参数**（完整列表）：
- `--config` / `-c`：配置文件路径
- `--version` / `-v`：显示版本
- `--mode` / `-m`：工作模式（plan/agent/yolo）
- `--model`：指定模型名称
- `--provider`：LLM 提供商
- `--api-key`：API Key
- `--tui`：启动 Textual TUI
- `--web`：启动 Web UI
- `--port`：Web UI 端口（默认 8080）
- `--init`：初始化项目记忆
- `--forget`：清除项目记忆
- `--goal`：设置持久目标
- `--compress`：压缩级别（lossless/balanced/aggressive/off）
- `--max-tokens`：最大上下文 token 数（默认 1048576，支持 1M 上下文）
- `--keep-turns`：保留最近对话轮次（默认 50）
- `--reasoning`：推理强度（low/medium/max）
- `--skill`：启动时预加载技能（可多次使用，来自 `.zouwucode/skills/<name>.md`）
- `--init-config`：生成默认配置文件
- `--show-config`：显示当前配置
- `message`（位置参数）：单条消息（非交互模式）

### 5.15 `runtime.py` — 共享运行时工厂

**职责**：CLI / TUI / Web 三端共享的工厂函数，保证三端行为一致（历史上三端各自复制 provider 工厂与内置工具清单，已出现漂移）。

- `create_provider(config)`：根据 `default_provider` 与 `api_type` 构建 DeepSeekProvider / OpenAIProvider（缺失时注入默认配置）
- `create_builtin_tools(sandbox, config)`：返回 10 个内置工具实例清单（Read/Write/Edit/Ls/Glob/Shell/python_exec/Git/WebSearch/WebFetch；`python_exec` 在传入 config 且允许 shell 时加入）
- `build_agent_engine(config, sandbox_root, mode, with_subagents)`：一站式装配完整 agent 栈（引擎+沙箱+注册表+协调器+子 Agent），dev 管线与 eval harness 共用同一工厂——评测度量的就是生产跑的栈

### 5.16 `extensions/` — 扩展层

**职责**：MCP / LSP 等集成的预留扩展空间。`Extension` 为惰性启动的插件基类，可向运行时贡献两类能力：工具（BaseTool 实例并入 ToolRegistry）与原始 Schema（配 `on_dynamic_tool` 异步派发器）。`ExtensionHost` 负责生命周期（start/stop）与聚合；`extensions.mcp_servers` 为空且 `lsp_enabled=false`（默认）时为 no-op，运行时零开销。

- `host.py`：`Extension` 抽象基类 + `ExtensionHost`（注册 / 启动 / 停止 / 工具聚合 / 状态汇总；必须在 freeze_session 之前启动以注册扩展工具）
- `mcp_ext.py`：`McpExtension` + `MCPTool`（MCP 服务器工具 → 标准 BaseTool 适配）
- `lsp_ext.py`：`LspExtension` + `CheckDiagnosticsTool`（`check_diagnostics` 诊断工具）

### 5.17 `skills/` — Rules & Skills

**职责**：加载项目规则与技能包。

- `RuleLoader`：管理 `.zouwucode/rules.md`（始终注入系统提示词，`<project_rules>` 块）
- `SkillsManager`：管理 `.zouwucode/skills/*.md` 技能包（一个 .md 文件 = 一个技能），运行时经 `/skill load|unload|list` 加载/卸载

### 5.18 `modules/` — 模块系统

**职责**：`ModuleManager` 注册并派发可加载扩展模块。核心（CLI/TUI/Web）不硬编码模块内部，而是通过 `dispatch_message` / `dispatch_command` / 事件回调委托给模块；模块返回可渲染结果 dict（title/content/details），返回 None 则回落到内置处理。内置模块为 `hello_my_zouwucode/`（多智能体编排：Prometheus 规划、Atlas 执行、Momus 审查、11 个内置 Agent 等），由 `config.hello_my_zouwucode.enabled` 控制。

### 5.19 `dev/` — Devin 式自主开发工作流

**职责**：把 Devin（Cognition AI 的自主 AI 工程师）的「issue → 自主编码 → PR」概念落地为本地命令 `zouwucode dev`。一次任务 = 一条端到端闭环：

```
issue URL / owner/repo#N / 自由文本任务
    → 隔离 git worktree（dev/* 分支，绝不触碰主检出）
    → 显式 PLAN（同一会话产出有序计划，进 append-only 前缀）
    → 自主 agent 会话（EngineLoop，yolo 模式，成本/轮数/超时/卡死熔断全生效）
    → 多层验证：lint → typecheck → test(+覆盖率) → security，逐层失败回灌（有界重试，带 REFLECT）
    → 独立 AI 审查：全新只读会话审 diff，request_changes 回灌修复（有界轮数）
    → 成功：commit + push dev/* + Draft PR（永不自动合并，人工 review 边界）
      CI 联动：轮询真实 GitHub Checks，结果回写 PR（不阻塞，仅提示人工）
      失败：不建 PR；以 issue 评论回帖（本地任务则输出到控制台）
```

**三大痛点的对应解法**（对标成熟 dev agent 的核心）：
- **边界遗漏** → 实现者提示词内置边界自检清单（认知层）+ 多层静态门禁（执行层）+ 独立审查清单
- **约束过多降性能** → 约束放在认知层（自检、分层反馈）而非堆砌硬禁令；自适应成本预算避免一刀切掐死复杂任务
- **自主验收 ≠ 生产可用** → 独立 reviewer 打破自写自测偏差 + CI 联动把验证延伸到真实多平台矩阵

#### `dev/pipeline.py` — DevPipeline

核心编排器，`run(task_ref)` 执行单任务全流程。

**关键设计**：
- 任务引用三态解析（`resolve_task`）：`https://github.com/o/r/issues/N` → API 拉取标题+正文；`o/r#N` → 同 API；自由文本 → 本地任务不关联 GitHub
- **chdir 顺序安全**：先 `os.chdir(worktree)` 再 `_build_engine()`，使沙箱 workspace 根 = worktree 本身，agent 被路径白名单锁在该检出内
- 每任务全新 EngineLoop（独立缓存、yolo 模式），子 Agent 系统对 dev worker 同样可用
- **多层验证循环** `for iteration in range(verify_retries + 1)`：调 `verifiers.run_verification`，失败按层标注（`[lint] FAIL` 等）回灌 `_implement_with_budget`
- **独立审查循环** `for review_round in range(review_max_rounds + 1)`：本地先 commit（拿到 base...HEAD diff）→ `_build_reviewer_engine`（plan 模式 + 只读白名单）→ `reviewer.review_diff`；request_changes 回灌实现者后重新 commit 复审
- **自适应成本预算** `_implement_with_budget`：捕获含 "cost" 的 `TurnLimitExceeded`，按 `budget_escalations` 上限翻倍预算并续跑同一会话（非成本类限制照常抛出）
- 成功后 `push_branch → create_pr_from_issue`（body 追加 `Closes #N` + 审查结论 + CI 状态），再 `_await_ci` 轮询真实 Checks（无 CI 早退，不空耗预算）
- 失败路径 `_report_failure` 回帖 issue；`_report_ci` 在本地过但 CI 挂时回帖；`_harvest_learning` 把经验沉淀进 ProjectMemory

#### `dev/verifiers.py` — 多层验证管线

**职责**：把单层 `pytest` 升级为独立分层门禁，覆盖边界问题与静态缺陷。

- 四层：`lint`（ruff/eslint）→ `typecheck`（mypy/tsc）→ `test`（pytest+覆盖率/npm test）→ `security`（bandit）
- **自动探测 + 优雅跳过**：每层检测配置文件标记与工具可用性；未配置/未安装 → 该层 SKIPPED，既不误伤也不假装通过（`VerificationReport.passed` 只统计非 skipped 层）
- bandit 需 `[tool.bandit]` 显式标记才启用（避免对未选型仓库产生噪音）
- 覆盖率：`coverage_min > 0` 时给 pytest 追加 `--cov --cov-fail-under=N`
- 输出按层标注，回灌时实现者能看到具体是哪一层破坏（信息性反馈优于笼统"测试失败"）

#### `dev/reviewer.py` — 独立 AI 审查

**职责**：用全新只读会话打破"自写自测自验"的系统性偏差。

- `REVIEWER_TOOL_WHITELIST = [read, ls, glob, git]`（**排除 bash**——shell 会破坏只读保证）；引擎跑 plan 模式，审查基于传入的完整 diff
- 审查清单（优先级）：正确性、边界处理、错误路径、资源管理、并发、跨平台（Windows+POSIX）、向后兼容、安全
- `parse_review`：解析末尾 fenced JSON 结论；仅 critical/major 计为 blocking，minor 不阻塞；**无法解析时保守放行**（解析器故障不能卡死管线）但保留原文附进 PR 供人工参考
- `format_review_for_pr` / `format_review_feedback`：分别渲染 PR 正文块与回灌实现者的定向反馈（只列 blocking 项）

#### `dev/workspace.py` — WorktreeManager（安全关键层）

**职责**：git worktree 的创建/提交/推送/清理，独立于 LLM 决策的分支安全白名单。

**安全规则（`validate_branch`）**：
- 拒绝 `main`/`master`/`develop`/`release`/`stable` 等保护分支（即使带前缀）
- 分支必须以 `<branch_prefix>/`（默认 `dev/`）开头
- 字符白名单正则 `[\w./@-]+`，拒绝空格/分号等破坏 shell 引号假设的字符
- `push_branch` 在 `--force` 前再次校验分支——非 dev/* 分支的强推在该层被拒绝

**worktree 语义**：`create(task_id, branch)` 分支已存在则复用（幂等重跑/断点续作）；`_safe_task_dir` 清洗 task_id 作目录名；`commit_all` 通过 `git -c user.name/-c user.email` 注入 dev 机器人身份（不依赖用户级 git 配置——CI runner 无全局身份时 `git commit` 会报 "Author identity unknown"）。

#### `dev/queue.py` — DevQueue（SQLite 持久队列）

**职责**：异步托管场景的任务账本（Devin 式 backlog），每个工作区一个 SQLite 文件。

**关键设计**：
- 状态机：`pending → running → done | failed`
- 原子认领：单条 `UPDATE ... WHERE id=(SELECT ... WHERE status='pending' ORDER BY created_at LIMIT 1) RETURNING ...`，并行 worker 进程永不抢到同一任务
- 崩溃恢复：`requeue_stale()` 把超时未完成的 running 行重置为 pending
- autocommit + 30s busy timeout，多进程共享安全

#### `dev/github.py` — GitHubClient

**职责**：GitHub REST API 轻封装（httpx），无第三方 SDK 依赖。

- `from_config`：`GITHUB_TOKEN` 环境变量优先于 `config.github.token`
- `get_issue` / `list_issues_by_label`（watch 轮询）/ `create_pr_from_issue`（Draft PR + `Closes #N`）/ `comment_issue`（失败回帖）/ `remove_label`
- `parse_issue_url` / `parse_repo_slug` 纯函数解析任务引用

#### `dev/cli.py` — 命令入口

`zouwucode/__main__.py` 前置路由：`sys.argv[1] == "dev"` 时交给 `dev_main`，不进入主 argparse。

| 命令 | 说明 |
|------|------|
| `zouwucode dev <issue-url\|task-text>` | 前台执行单任务 |
| `zouwucode dev --queue <ref>` | 任务入队 |
| `zouwucode dev --workers N` | N 个 worker 子进程排空队列 |
| `zouwucode dev --watch [owner/repo]` | 轮询带 `dev.watch_label` 标签的 open issue，自动入队并执行（缺省从 origin 远程推断仓库） |
| `zouwucode dev --status` | 队列统计 + 最近任务 |

**并行模型**：每个 worker 是独立 `python -m zouwucode dev --worker` 子进程——进程隔离天然带来每任务独立 CWD（worktree）、崩溃隔离、干净的 Ctrl+C 语义。认领后 `asyncio.wait_for(pipeline.run(ref), timeout)` 强制任务级超时；worker 在任务崩溃时仍存活并继续下一个。

**显式 PLAN/REFLECT**（对标规范控制循环 PLAN→ACT→OBSERVE→REFLECT）：`plan_enabled` 时实现前先跑一轮规划（产出 3-8 步有序清单，**同一引擎会话** → 计划进入 append-only 前缀，后续所有轮次免费可见）；验证失败回灌文本要求先 REFLECT（计划哪步失效、是否重规划）再修复。

### 5.20 `eval/` — 任务级评测 harness

**职责**：度量脚手架质量。行业共识：同模型不同脚手架在 SWE-bench Verified 可差 20 分——改进脚手架的前提是可度量。

```
zouwucode eval                    # 真实 agent 栈跑内置任务 → 通过率 + 成本
zouwucode eval --list / --task X / --tasks dir/
```

- `runner.py`：`EvalTask(name/prompt/setup/checks/timeout)` YAML 加载；`run_task` 在隔离临时工作区写入 setup 文件 → 用 **`runtime.build_agent_engine`（与 dev 管线同源的完整生产栈）** 跑 agent → `run_checks` 判分。`engine_factory` 可注入（测试用假 provider）
- `checks.py`：确定性判分，**判分不经过 LLM**——`file_exists/absent/contains`、`python_eval`（表达式为真，推荐：测行为不测写法）、`command_pass`（退出码 0）
- `tasks/*.yaml`：内置示例（修 off-by-one / 处理空输入边界 / 补边界测试），随包分发（pyproject package-data）
- 用法：调 prompt/验证层/引擎前后各跑一次对比通过率与成本；线上踩过的边界 bug 固化为新任务

---

## 6. 文件结构

```
zouwucode/                             # 仓库克隆目录（项目根）
├── zouwucode/                          # 主包
│   ├── __init__.py                     # 版本信息（v1.0.0）
│   ├── __main__.py                     # CLI 入口、参数解析
│   ├── config.py                       # 配置管理（Pydantic）
│   ├── project_memory.py               # 持久项目记忆系统
│   ├── runtime.py                      # 共享运行时工厂（provider + 内置工具清单）
│   │
│   ├── engine/                         # Cache-First LLM 引擎
│   │   ├── __init__.py
│   │   ├── loop.py                     # EngineLoop：append-only 迭代对话循环（打断 + 安全限制）
│   │   ├── cache.py                    # PrefixCache + CacheStats
│   │   └── providers/
│   │       ├── __init__.py
│   │       ├── base.py                 # BaseProvider 抽象基类
│   │       ├── deepseek.py             # DeepSeekProvider 优化适配器
│   │       └── openai.py               # OpenAIProvider 通用适配器
│   │
│   ├── tools/                          # 工具系统
│   │   ├── __init__.py
│   │   ├── base.py                     # BaseTool + ToolSpec + ToolResult
│   │   ├── registry.py                 # ToolRegistry 注册中心
│   │   ├── file_tools.py               # Read/Write/Edit/Ls/Glob
│   │   ├── shell_tools.py              # Shell 命令执行
│   │   ├── code_exec_tool.py           # python_exec 持久 REPL（CodeAct 行动面）
│   │   ├── git_tools.py                # Git 操作
│   │   ├── web_tools.py                # WebSearch + WebFetch
│   │   └── agent_tools.py              # TaskTool：task 委派工具（子 Agent 并行）
│   │
│   ├── agent/                          # 多 Agent 编排
│   │   ├── __init__.py
│   │   ├── coordinator.py              # AgentCoordinator 工具执行回调（含钩子）
│   │   ├── hooks.py                    # HookRunner：pre_tool/post_tool 生命周期钩子
│   │   └── subagent.py                 # SubAgent + SubAgentManager 子 Agent 系统
│   │
│   ├── extensions/                     # 扩展层（MCP/LSP 预留接口，默认不激活）
│   │   ├── __init__.py
│   │   ├── host.py                     # 扩展宿主：加载与生命周期管理
│   │   ├── mcp_ext.py                  # MCP 服务器接入（stdio）
│   │   └── lsp_ext.py                  # LSP 诊断扩展（check_diagnostics）
│   │
│   ├── context/                        # 三层上下文管理
│   │   ├── __init__.py
│   │   ├── memory.py                   # MEMORY.md 轻量级索引
│   │   ├── topics.py                   # Topic 文件管理
│   │   ├── transcript.py               # 对话日志管理
│   │   ├── compressor.py               # DialogueCompressor 压缩算法
│   │   └── sliding_window.py           # ContextWindow 滑动窗口
│   │
│   ├── sandbox/                        # 权限沙箱
│   │   ├── __init__.py
│   │   └── permission.py               # PermissionManager 多层安全
│   │
│   ├── session/                        # 会话管理
│   │   ├── __init__.py
│   │   └── manager.py                  # SessionManager 持久化
│   │
│   ├── skills/                         # Rules & Skills
│   │   ├── __init__.py
│   │   └── manager.py                  # RuleLoader + SkillsManager
│   │
│   ├── modules/                        # 可加载扩展模块
│   │   ├── __init__.py
│   │   └── manager.py                  # ModuleManager 注册与派发
│   │
│   ├── mcp/                            # MCP 协议支持
│   │   ├── __init__.py
│   │   └── client.py                   # MCPClient
│   │
│   ├── dev/                            # Devin 式自主开发工作流（zouwucode dev）
│   │   ├── __init__.py
│   │   ├── cli.py                      # 命令入口：单任务/队列/worker/watch/status
│   │   ├── pipeline.py                 # DevPipeline：issue→worktree→agent→多层验证→审查→Draft PR→CI
│   │   ├── workspace.py                # WorktreeManager + 分支安全白名单
│   │   ├── verifiers.py                # 多层验证管线（lint/typecheck/test/security 自动探测）
│   │   ├── reviewer.py                 # 独立只读 AI 审查（打破自写自测偏差）
│   │   ├── queue.py                    # DevQueue：SQLite 持久任务队列（原子认领）
│   │   └── github.py                   # GitHubClient：REST 轻封装 + URL 解析 + Checks 轮询
│   │
│   ├── eval/                           # 任务级评测 harness（zouwucode eval）
│   │   ├── __init__.py
│   │   ├── cli.py                      # eval 子命令路由
│   │   ├── runner.py                   # 真实 agent 栈跑任务（build_agent_engine 同源）
│   │   ├── checks.py                   # 确定性判分（file/python_eval/command_pass）
│   │   └── tasks/                      # 内置示例评测任务（*.yaml，随包分发）
│   │
│   ├── lsp/                            # LSP 诊断集成
│   │   ├── __init__.py
│   │   └── client.py                   # LSPClient
│   │
│   ├── tui/                            # 终端界面
│   │   ├── __init__.py
│   │   ├── app.py                      # CLI 主应用 + ZOUWUCODEApp
│   │   ├── textual_app.py              # Textual TUI 界面
│   │   └── styles.tcss                 # TUI 样式表
│   │
│   └── webui/                          # Web UI
│       ├── __init__.py
│       └── server.py                   # WebUIServer（HTTP + 内嵌 HTML）
│
├── hello_my_zouwucode/                 # 内置模块：多智能体编排
│   ├── __init__.py
│   ├── module.py                       # 模块入口（注册到 ModuleManager）
│   ├── orchestrator.py                 # 全自动流水线（ultrawork）
│   ├── agents.py                       # 11 个内置 Agent 定义
│   ├── planner.py                      # Prometheus 规划器
│   ├── atlas.py                        # Atlas 执行器
│   ├── boulder.py                      # Boulder 任务石
│   ├── notepad.py                      # Notepad 记事本
│   ├── intent_gate.py                  # 意图门控
│   └── categories.py                   # 任务分类
│
├── tests/                              # 测试套件（324 个测试用例）
│   ├── __init__.py
│   ├── test_code_exec.py              # python_exec 持久 REPL（命名空间/超时重启/破坏性拦截）
│   ├── test_compressor.py
│   ├── test_config.py
│   ├── test_context.py
│   ├── test_context_window.py
│   ├── test_dev.py                    # dev 模式（解析/分支安全/真实 worktree/队列/管线端到端/质量门禁）
│   ├── test_engine.py
│   ├── test_eval.py                   # 评测 harness（判分类型/任务加载/端到端判对错）
│   ├── test_hello_my_zouwucode.py
│   ├── test_hooks.py                  # 生命周期钩子（pre_tool 拦截/post_tool/协调器集成）
│   ├── test_interrupt.py               # 任务打断（CLI/TUI/Web、级联、前缀清理）
│   ├── test_project_memory.py
│   ├── test_resilience.py             # 韧性（LLM 重试退避、日志落盘、密钥校验）
│   ├── test_sandbox.py
│   ├── test_session.py
│   ├── test_stuck.py                  # 卡死检测（指纹滑窗提醒/熔断/恢复/豁免）
│   ├── test_subagent.py                # 子 Agent 系统（隔离引擎、白名单、并行）
│   ├── test_tools.py
│   ├── test_tui.py
│   └── test_webui.py
│
├── docs/                               # 模块与功能说明文档
│
├── zouwucode_data/                     # 运行时数据（便携式）
│   ├── config.yaml                     # 配置文件
│   ├── sessions/                       # 会话数据
│   └── transcripts/                    # 原始对话日志
│
├── .zouwucode/                         # 项目记忆
│   ├── memory/
│   │   ├── project_state.json
│   │   ├── decisions.json
│   │   ├── goals.json
│   │   └── summaries.json
│   ├── skills/                         # 技能包（*.md）
│   └── rules.md                        # 项目规则（可选）
│
├── config.yaml                         # 当前工作目录的配置
├── config.example.yaml                 # 配置模板（不含密钥）
├── requirements.txt                    # 依赖清单
├── pyproject.toml                      # 项目元数据
├── build.py                            # PyInstaller 打包脚本
├── z.bat                               # 快速启动批处理
├── install.ps1                         # PowerShell 安装脚本
├── README.md                           # 项目说明
└── USAGE.md                            # 使用手册
```

---

## 7. 配置系统

### 7.1 完整配置示例

```yaml
# ZOUWUCODE 配置
data_dir: ""                              # 数据目录（留空使用默认 zouwucode_data/）
default_provider: deepseek                 # 默认 LLM 提供商
language: zh                               # 语言
theme: dark                                # 主题

# LLM 提供商
providers:
  deepseek:
    api_key: "sk-your-key-here"            # API Key
    model: "deepseek-v4-flash"             # 模型名称
    base_url: "https://api.deepseek.com"   # API 地址
    api_type: deepseek                     # 提供商类型

# 引擎安全限制（防止死循环与执行停顿）
engine:
  max_tool_rounds: 25                      # 单次任务最多工具轮数
  turn_timeout_seconds: 300                # 单次 LLM 流式请求超时（秒）
  task_timeout_seconds: 1800               # 单次任务总耗时上限（秒）
  max_consecutive_tool_errors: 3           # 工具连续失败次数达到该值即终止任务
  max_llm_retries: 2                       # LLM 请求失败重试（429/5xx/传输错误指数退避）
  max_cost_usd: 0.0                        # 单任务成本熔断（美元，0=不限制）
  stuck_detection_enabled: true            # 卡死检测（工具成功但原地打转）
  stuck_window: 10                         # 动作指纹滑窗长度
  stuck_threshold: 3                       # 窗口内同一动作重复达此数即提醒/熔断

# 缓存配置
cache:
  enabled: true                            # 启用缓存
  append_only: true                        # 只追加模式
  max_prefix_tokens: 128000                # 最大前缀 token 数
  stats_window: 50                         # 统计窗口大小

# 沙箱配置
sandbox:
  enabled: true                            # 启用沙箱
  default_mode: agent                      # 默认模式
  allow_shell: true                        # 允许 shell
  allow_file_write: true                   # 允许写文件
  allow_network: true                      # 允许网络
  allow_git: true                          # 允许 git
  allowed_paths:                           # 允许路径
    - .

# 会话配置
session:
  save_enabled: true                       # 启用保存
  auto_save_interval: 60                   # 自动保存间隔（秒）
  max_sessions: 50                         # 最大会话数
  rollback_enabled: true                   # 启用回滚

# 子 Agent 系统
subagent:
  max_agents: 8                            # 并行子 Agent 数量上限
  default_timeout: 600                     # 单个子 Agent 默认超时（秒）

# 扩展层（MCP/LSP — 默认不激活，配置后自动启用）
extensions:
  mcp_servers: []                          # 示例：
  #  - name: filesystem
  #    command: npx
  #    args: ["-y", "@modelcontextprotocol/server-filesystem", "."]
  lsp_enabled: false                       # 启用后注册 check_diagnostics 诊断工具
  hooks: []                                # 生命周期钩子：pre_tool（可拦截）/ post_tool
  #  - event: pre_tool
  #    tool_pattern: bash
  #    command: "python check_cmd.py {args}"   # 非零退出或 {"block":true} 即拦截

# 多智能体编排模块（hello-my-zouwucode）
hello_my_zouwucode:
  enabled: true                            # 模块总开关
  state_dir: ".hello-my-zouwucode"         # 状态目录（boulder/plans/notepads）
  default_category: deep                   # 未指定时的任务分类
  max_review_rounds: 2                     # Momus 审查循环上限（0 = 不限）
  interactive_planning: true               # 允许 Prometheus 访谈式规划

# GitHub 集成（dev 模式 issue → PR 工作流）
github:
  token: ""                                # 也可用 GITHUB_TOKEN 环境变量（优先）
  api_base: "https://api.github.com"

# Devin 式自主开发工作流（zouwucode dev）
dev:
  branch_prefix: dev                       # 只允许操作 dev/* 分支（保护分支拒绝）
  worktree_dir: ".zouwucode_worktrees"     # worktree 检出目录（已 gitignore）
  test_command: ""                         # 空 = 自动探测（pytest / npm test）
  verify_retries: 2                        # 验证失败后回灌迭代修复次数
  max_concurrent_tasks: 3                  # 队列模式并行 worker 上限
  task_timeout_seconds: 3600               # 单个 dev 任务总超时
  watch_label: "zouwucode:do"              # watch 模式认领的 issue 标签
  draft_pr: true                           # PR 始终为 Draft，人工 review 后合并
  # 多层验证管线（未探测到的层自动跳过）
  lint_command: ""                         # 空 = 自动探测 ruff/eslint
  typecheck_command: ""                    # 空 = 自动探测 mypy/tsc
  security_command: ""                     # 空 = 自动探测 bandit（需 [tool.bandit]）
  coverage_min: 0.0                        # >0 时 pytest 加 --cov-fail-under
  # 独立审查 + CI 联动 + 自适应预算
  review_enabled: true
  review_max_rounds: 1
  ci_check_enabled: true
  ci_wait_seconds: 300
  ci_poll_interval: 15
  adaptive_budget: true
  task_cost_budget_usd: 0.0                # 0 = 沿用 engine.max_cost_usd
  budget_escalations: 1
  plan_enabled: true                       # 实现前显式 PLAN 轮（同一会话进前缀）

# 推理强度
reasoning_intensity: medium                # low | medium | max

# 思考显示（流式思考过程开关，三端统一生效）
show_thinking: true                        # true | false，默认 true 实时显示思考过程
```

### 7.2 配置文件加载优先级

1. 命令行指定的路径：`--config /path/to/config.yaml`
2. 当前工作目录的 `config.yaml`
3. `zouwucode_data/config.yaml`（CWD 同级数据目录）
4. 安装目录的 `config.yaml`（pip -e 安装场景）
5. 如果都不存在，返回默认配置

---

## 8. 开发历程与关键决策

### 8.1 开发阶段

| 阶段 | 内容 | 主要成果 |
|------|------|----------|
| 第一阶段 | 核心引擎搭建 | EngineLoop、PrefixCache、DeepSeekProvider |
| 第二阶段 | 工具系统集成 | 9 个内置工具、ToolRegistry、AgentCoordinator |
| 第三阶段 | 多界面支持 | CLI、TUI（Textual）、Web UI 三种界面 |
| 第四阶段 | 高级特性 | 项目记忆、对话压缩、滑动窗口、权限沙箱 |
| 第五阶段 | 推理强度 | 三档 reasoning_effort 映射 DeepSeek V4 |
| 第六阶段 | Bug 修复与优化 | 配置加载修复、Web UI 对话修复、缓存优化 |
| 第七阶段 | 流式思考显示 | 三端流式思考（opencode 风格）、`/thinking` 统一开关、SSE 流式接口 |
| 第八阶段 | 子 Agent 与扩展层 | SubAgent/SubAgentManager（独立引擎+白名单+级联打断）、task 委派工具、extensions/（ExtensionHost/McpExtension/LspExtension）、runtime.py 工厂 |
| 第九阶段 | 打断与安全限制 | Esc/Ctrl+C 任务打断（TaskInterrupted）、EngineConfig 安全限制（TurnLimitExceeded）、`/agents` 状态命令、Web `/api/interrupt` |
| 第十阶段 | 开源发布与韧性 | LLM 指数退避重试、日志轮转落盘、GitHub Actions CI（Ubuntu/Windows 矩阵）、Windows cp437 编码修复、密钥防泄露验证 |
| 第十一阶段 | Devin 式 dev 模式 | `zouwucode dev` 子命令：issue→worktree→自主管线→验证→Draft PR；SQLite 队列+并行 worker+watch；成本熔断+经验沉淀 |
| 第十二阶段 | dev 质量门禁 | 多层验证（lint/typecheck/test/security）+ 独立只读 AI 审查 + CI 联动 + 自适应成本预算 + 边界自检清单 |
| 第十三阶段 | 对标成熟 agent 升级 | 卡死检测（动作指纹滑窗）、评测 harness（zouwucode eval）、CodeAct python_exec 持久 REPL、显式 PLAN/REFLECT、生命周期钩子（pre/post_tool） |

### 8.2 关键设计决策

#### 决策 1：Append-only 缓存策略
- **背景**：DeepSeek prefix-cache 要求对话前缀字节稳定
- **方案**：永不重排、永不插入、永不删除消息，只追加
- **影响**：缓存命中率 90%+，但无法压缩历史前缀

#### 决策 2：系统提示词冻结
- **背景**：每次工具调用后系统提示词可能变化，破坏缓存
- **方案**：会话开始时一次性冻结系统提示词和工具 Schema
- **影响**：需要将所有动态内容放在用户消息中

#### 决策 3：本地缓存 + 服务端缓存双统计
- **背景**：需要了解缓存命中率以优化使用
- **方案**：本地跟踪缓存统计（PrefixCache + CacheStats），同时解析 API 响应的 usage 字段
- **影响**：提供实时的缓存命中率和成本数据

#### 决策 4：便携式数据目录
- **背景**：避免写 C 盘，支持 U 盘便携使用
- **方案**：所有数据文件存储在 `zouwucode_data/` 同级目录
- **影响**：无需管理员权限，绿色便携

#### 决策 5：三档推理强度映射
- **背景**：DeepSeek V4 引入 reasoning_effort 参数，DeepCode 实现三档优化
- **方案**：low → 不发送 reasoning_effort，medium → "high"，max → "max"
- **影响**：用户可根据任务复杂度灵活选择推理深度

#### 决策 6：思考过程流式化（opencode 风格）
- **背景**：回复生成期间用户看不到思考过程，长推理时体验空白
- **方案**：Provider 增量 yield 思考/正文 delta → 引擎聚合并回调 `on_thinking_delta`/`on_content_delta` → CLI/TUI/Web 三端实时渲染；`show_thinking` 全局开关（默认开），`/thinking` 运行时切换
- **影响**：思考实时可见可折叠（Web）或逐行输出（CLI/TUI）；关闭后完全不渲染思考块，正文不受影响

#### 决策 7：子 Agent 独立引擎 + 级联打断
- **背景**：子 Agent 与主 Agent 共享单个引擎会导致子 Agent 流量交错主引擎的 append-only 前缀，破坏缓存稳定性
- **方案**：每个 SubAgent 持有私有 EngineLoop（共享 provider 与工具注册表，隔离缓存/统计/打断状态）；主引擎 `on_interrupt` 钩子级联停止所有运行中的子 Agent
- **影响**：并行执行安全且互不干扰；打断从 UI 一路传播到所有子 Agent

#### 决策 8：引擎迭代循环 + 三重安全限制
- **背景**：模型可能反复调用失败工具形成死循环，或因连接挂起无限等待
- **方案**：`run()` 用迭代循环替代递归，配 `max_tool_rounds`（25）、`turn_timeout_seconds`（300）、`task_timeout_seconds`（1800）、`max_consecutive_tool_errors`（3）四项 EngineConfig 限制
- **影响**：超限抛 `TurnLimitExceeded` 终止任务；用户打断（`TaskInterrupted`）与安全熔断可区分处理

#### 决策 9：dev 模式 worktree 隔离 + Draft PR 人工边界
- **背景**：Devin 式自主工作流若直接在用户主检出上改代码，会污染工作区、并行任务互相冲突；全自动合并 PR 风险不可控
- **方案**：每个任务在独立 `git worktree`（`dev/*` 分支）中执行，agent 沙箱根锁定 worktree；分支白名单在 WorktreeManager 层强制（拒绝保护分支、拒绝非法字符、force-push 前二次校验）；产出永远是 Draft PR，合并决定权留给人工 review
- **影响**：主检出零污染；并行任务天然隔离；自主性与安全性兼得

#### 决策 10：dev worker 进程隔离模型
- **背景**：dev 任务需要并行执行且各自持有独立 CWD（worktree），而 asyncio 任务共享进程 CWD，线程方案有状态串扰风险
- **方案**：每个 worker 为独立 `python -m zouwucode dev --worker` 子进程，SQLite 队列（`UPDATE ... RETURNING` 原子认领）作为进程间协调点；崩溃恢复靠 `requeue_stale()` 超时重入队
- **影响**：进程隔离天然带来 CWD 独立、崩溃隔离、干净 Ctrl+C 语义；队列持久化，重启不丢任务

#### 决策 11：多层验证 + 独立审查取代自写自测
- **背景**：单层 pytest 只验证"我写的测试还过"，漏掉静态缺陷与边界问题；实现者自验存在系统性偏差，验收通过 ≠ 生产可用
- **方案**：lint→typecheck→test(+覆盖率)→security 四层独立门禁（未探测到的层自动跳过，不误伤不假过）；提交前由全新只读会话（plan 模式 + 只读白名单 + 零共享上下文）审查 diff，仅 critical/major 阻塞；push 后轮询真实 GitHub Checks 回写 PR
- **影响**：本地验证延伸到真实 CI 矩阵；审查结论透明写入 PR；解析失败保守放行但保留原文供人工参考

#### 决策 12：认知层约束优于执行层禁令（自适应预算）
- **背景**：给 agent 堆砌"禁止 X"式硬约束会显著降低模型在复杂任务上的表现；一刀切成本限制掐死复杂任务、又对简单任务过松
- **方案**：边界意识写进实现者自检清单（认知层）；成本触顶不直接失败而是翻倍预算续跑同一会话（`budget_escalations` 有上限）；反馈按层标注（信息性），而非笼统报错
- **影响**：约束保持"信息性"而非"限制性"，模型性能不受损的同时边界覆盖率提升；复杂任务获得弹性预算

#### 决策 13：卡死检测——补齐"成功但不推进"的死循环形态
- **背景**：连续工具错误熔断只能抓"一直报错"的循环；模型也可能反复用相同参数调同一工具拿到相同结果（全部"成功"）却毫无进展，直到烧满轮数上限
- **方案**：滑动窗口记录每步动作指纹 `(tool, args, result)` 哈希，同一指纹重复达阈值先注入一条"换思路/重规划/止损"的 user 提醒（措辞为信息非禁令），再犯抛 `TurnLimitExceeded`；提醒注入在本轮工具结果之后保持前缀合法
- **影响**：无进展循环被提前掐断，省下大量 token；模型收到提醒后改变策略则任务照常完成

#### 决策 14：评测 harness——改进脚手架的前提是可度量
- **背景**：行业共识"循环即产品，模型只是引擎"，同模型不同脚手架 SWE-bench 可差 20 分；但改 prompt/验证层/引擎若无基线就是盲调
- **方案**：`zouwucode eval` 用 `build_agent_engine`（与 dev 管线同源的完整生产栈）跑 YAML 定义的确定性行为断言任务，判分不经过 LLM；内置示例 + package-data 随包分发
- **影响**：每次脚手架改动有通过率/成本对比；线上边界 bug 可固化为回归任务

#### 决策 15：CodeAct 持久 REPL 作为第二行动面
- **背景**：纯 JSON 工具调用无法让模型组合循环/条件/自写验证脚本；OpenHands 的可执行动作面在复杂多步任务上显著占优
- **方案**：`python_exec` 长驻子进程跑自驱动 REPL 驱动（readline 循环不等 EOF），base64 帧协议免疫编码问题，命名空间跨调用持久；复用沙箱筛查 + 破坏性模式拦截 + 超时重启
- **影响**：批量处理/程序化验证成为单次调用；进程级隔离（非容器）是与本地优先姿态一致的取舍

#### 决策 16：生命周期钩子的 fail 语义
- **背景**：钩子是模型外的确定性治理（拦截危险命令、编辑后自动格式化），但挂死的钩子绝不能卡住主循环
- **方案**：pre_tool 超时/失败按拦截处理（fail-closed，安全优先），post_tool 超时/失败仅告警（fail-open，观察性质不影响结果）；拦截原因作为错误结果回传模型使其可自适应
- **影响**：治理确定性与主循环可用性兼得

### 8.3 重大 Bug 修复记录

| Bug | 原因 | 修复方案 |
|-----|------|----------|
| `Request URL is missing an 'http://' or 'https://' protocol` | 配置加载路径错误，base_url 为空 | 修复 config.py 加载路径，优先检查当前目录 |
| `Messages with role 'tool' must be a response to a preceding message with 'tool_calls'` | 缓存中未追加助理的 tool_calls 消息 | 在 EngineLoop.run 中追加带 tool_calls 的助理消息到缓存 |
| 系统提示词重复发送 | _build_messages 未检查已有 system 消息 | 添加 has_system 检查，避免重复 |
| `AttributeError: type object 'Input' has no attribute 'Key'` | Textual Input 类没有 Key 属性 | 改用 `events.Key` |
| 回复内容翻倍 | Provider 末帧重复携带完整文本 + 引擎增量聚合 | 末帧只携带 tool_calls/usage，正文/思考仅增量推送 |
| 所有工具调用失败 `argument after ** must be a mapping, not str` | coordinator 直接 `**` 解包 JSON 字符串 | 先 `json.loads` 解析再解包，非 dict 返回错误 |
| CI Windows 全部失败 `UnicodeEncodeError` | Windows runner 默认 cp437 代码页打印中文 | smoke_test.py reconfigure UTF-8 + CI 环境变量 PYTHONUTF8=1/PYTHONIOENCODING=utf-8 |
| CI dev 测试全挂 `Author identity unknown` | CI runner 无全局 git 身份，`git commit` 失败 | `commit_all` 用 `git -c user.name/-c user.email` 注入 dev 机器人身份 |

---

## 9. 已解决的 Bug 与修复方案

### Bug 1：Web UI 对话无回复 — URL 协议错误

**错误信息**：`⚠️ Error: Request URL is missing an 'http://' or 'https://' protocol.`

**根因分析**：
1. 配置加载路径错误：`config.py` 首先检查默认数据目录，但用户可能在工作目录放 `config.yaml`
2. 用户配置的 `base_url` 为空或未正确读取
3. 系统提示词重复发送导致 API 400

**修复方案**：
1. **config.py**：优先检查当前工作目录的 `config.yaml`
2. **deepseek.py**：`__init__` 中添加 `base_url` 协议检查和默认值
3. **loop.py**：在 `run()` 中追加助理响应（含 `tool_calls`）到缓存
4. **deepseek.py**：`_build_messages()` 中避免系统提示词重复

**涉及的代码变更**：
```python
# config.py - 配置加载优先级
@classmethod
def load(cls, path=None):
    if path is None:
        cwd_path = Path.cwd() / "config.yaml"
        # ... 优先检查 cwd_path

# deepseek.py - base_url 默认值
raw_url = config.get("base_url") or ""
self.base_url = raw_url if raw_url.startswith("http") else "https://api.deepseek.com"

# loop.py - 追加助理响应到缓存
assistant_msg = {"role": "assistant", "content": response.content or ""}
if response.tool_calls:
    assistant_msg["tool_calls"] = [...]
self.cache.append(assistant_msg)

# deepseek.py - 避免系统提示词重复
has_system = any(m.get("role") == "system" for m in messages)
if self._frozen_system_prompt and not has_system:
    result.append({"role": "system", "content": self._frozen_system_prompt})
```

### Bug 2：工具调用 400 错误

**错误信息**：`Messages with role 'tool' must be a response to a preceding message with 'tool_calls'`

**根因分析**：
- 缓存中未正确追加助理的 `tool_calls` 消息
- 工具结果缺少前置上下文

**修复方案**：
- 在 `EngineLoop.run` 中构建包含 `tool_calls` 的助理消息并追加到缓存

### Bug 3：Textual TUI 键盘事件错误

**错误信息**：`AttributeError: type object 'Input' has no attribute 'Key'`

**根因分析**：
- Textual 的 `Input` 类没有 `Key` 属性

**修复方案**：
- 将 `def on_input_key(self, event: Input.Key)` 改为 `def on_input_key(self, event: events.Key)`
- 导入 `from textual import events`

### Bug 4：回复内容翻倍

**错误信息**：Web UI / CLI 中每条回复的正文和思考内容都重复出现两次。

**根因分析**：
- Provider 的 `chat_stream` 先增量 yield 正文/思考 delta，末尾又 yield 一个携带完整 `content_buf` / `thinking_buf` 的汇总末帧
- 引擎 `run()` 对每个 chunk 的 content/thinking 做追加聚合，末帧的完整文本被再次计入 → 内容翻倍

**修复方案**：
- `deepseek.py` / `openai.py` 末帧改为 `content=""`、`thinking=""`，只携带 `tool_calls` / `usage` / `cache_hit` 元数据
- 正文与思考只通过增量 delta 传递，保证恰好一次

### Bug 5：所有工具调用失败 — JSON 参数解包错误

**错误信息**：`TypeError: ToolRegistry.execute() argument after ** must be a mapping, not str`，agent 的每次工具调用都以失败告终。

**根因分析**：
- `AgentCoordinator.execute_tool()` 中 `self.tools.execute(tool_call.name, **tool_call.arguments)` 直接对 **JSON 字符串** 做 `**` 解包
- 该路径无单测覆盖（此前仅直接测试 ToolRegistry），真实模型调用时暴露

**修复方案**：
- `coordinator.py`：先 `json.loads` 解析字符串参数，非 dict 时返回友好错误 `ToolResult(is_error=True)`
- 新增 `TestCoordinatorToolDispatch` 覆盖字符串参数/非法 JSON/空参数三种场景

### Bug 6：CI Windows 编码失败 — cp437 代码页

**错误信息**：`UnicodeEncodeError: 'charmap' codec can't encode characters`（仅 Windows job 失败，Ubuntu 全绿）

**根因分析**：
- Windows GitHub Actions runner 默认代码页 cp437，smoke test 打印中文时 stdout 编码失败

**修复方案**（双保险）：
- `scripts/smoke_test.py`：`sys.stdout.reconfigure(encoding="utf-8", errors="replace")`
- `.github/workflows/ci.yml`：job 级 `env: PYTHONUTF8=1 / PYTHONIOENCODING=utf-8`
- 本地用 `$env:PYTHONIOENCODING='cp437'` 对照复现验证

### Bug 7：CI dev 测试全挂 — git 身份缺失

**错误信息**：`git commit failed: Author identity unknown`（4 个矩阵 job 全挂在 "Run tests"，本地 278 测试却全过）

**根因分析**：
- 本地机器有全局 git `user.name/email`，CI runner 没有；`test_dev.py` 真实 worktree 测试走到 `git commit` 即失败

**修复方案**：
- `dev/workspace.py` `commit_all`：`git -c user.name="ZOUWUCODE Dev" -c user.email="dev@zouwucode.local" commit`，自主提交归属 dev 机器人且零依赖用户级配置
- 本地以 `GIT_CONFIG_GLOBAL`（空文件）+ `GIT_CONFIG_NOSYSTEM=1` 模拟 CI 环境复现并验证

---

## 10. 技术栈与依赖

### 10.1 当前技术栈

| 技术 | 用途 | 版本 |
|------|------|------|
| Python | 开发语言 | 3.10+ |
| httpx | HTTP 客户端（调用 API、Web 搜索） | >=0.27.0 |
| Pydantic | 数据模型与配置验证 | >=2.0.0 |
| PyYAML | YAML 配置文件解析 | >=6.0 |
| BeautifulSoup4 | HTML 解析（Web 搜索工具） | >=4.12.0 |
| Textual | TUI 终端 UI 框架 | >=1.0.0（可选） |
| Rich | 终端富文本 | >=13.0.0（可选） |
| pytest | 单元测试框架 | >=8.0.0（开发） |
| pytest-asyncio | 异步测试支持 | >=0.24.0（开发） |
| pytest-cov | 测试覆盖率 | >=5.0.0（开发） |
| PyInstaller | 打包为独立 exe | 最新版 |

### 10.2 依赖清单

```
# 核心依赖（必需）
httpx>=0.27.0
pydantic>=2.0.0
pyyaml>=6.0
beautifulsoup4>=4.12.0

# TUI 依赖（可选，通过 --tui 启动）
# textual>=1.0.0
# rich>=13.0.0

# 开发测试依赖（可选）
# pytest>=8.0.0
# pytest-asyncio>=0.24.0
# pytest-cov>=5.0.0
```

### 10.3 移植建议技术栈

如果要在新目录更换技术栈重制，以下是一些建议：

| 组件 | 当前实现 | 可替换方案 |
|------|----------|------------|
| 语言 | Python | TypeScript/Node.js、Go、Rust |
| HTTP 客户端 | httpx | fetch/axios（Node.js）、reqwest（Rust） |
| 配置验证 | Pydantic | Zod（TypeScript）、serde（Rust） |
| 配置文件 | YAML | JSON、TOML、环境变量 |
| TUI 框架 | Textual | Ink（React 终端）、Bubble Tea（Go） |
| 打包 | PyInstaller | pkg（Node.js）、Go 原生编译 |
| 测试 | pytest | vitest（TypeScript）、cargo test（Rust） |
| 异步 | asyncio | async/await（TypeScript）、tokio（Rust） |

---

## 11. 测试体系

### 11.1 测试概览

- 共计 **324 个测试用例**
- 覆盖所有核心模块
- 使用 pytest + pytest-asyncio
- 全部通过（`pytest tests/` 绿色），GitHub Actions CI 在 Python 3.10/3.12 × Ubuntu/Windows 矩阵验证

### 11.2 测试模块

| 测试文件 | 测试内容 | 测试用例数 |
|----------|----------|------------|
| test_code_exec.py | python_exec 持久 REPL（命名空间/异常捕获/CJK/破坏性拦截/超时重启/沙箱） | 9 |
| test_compressor.py | 对话压缩算法 | 10 |
| test_config.py | 配置加载/保存/验证 | 5 |
| test_context.py | 上下文管理 | 10 |
| test_context_window.py | 滑动窗口 | 11 |
| test_dev.py | dev 模式（URL 解析、分支安全白名单、真实 git worktree、SQLite 队列原子认领、pipeline 端到端、成本熔断、多层验证探测、reviewer 解析、CI 联动、自适应预算、PLAN 阶段） | 45 |
| test_engine.py | 引擎循环、流式思考聚合/回调、Provider 无重复流式、coordinator 工具分发 | 30 |
| test_eval.py | 评测 harness（判分类型、任务加载、端到端判对错——假 provider 验证"修对了过、没修不过"） | 8 |
| test_hello_my_zouwucode.py | hello-my-zouwucode 模块 | 53 |
| test_hooks.py | 生命周期钩子（pre_tool 拦截/JSON block/工具过滤/协调器集成） | 7 |
| test_interrupt.py | 任务打断（CLI/TUI/Web、级联打断、打断后前缀清理） | 22 |
| test_project_memory.py | 项目记忆 | 11 |
| test_resilience.py | 韧性（LLM 重试退避分类、日志落盘、API Key 校验） | 11 |
| test_sandbox.py | 权限沙箱 | 6 |
| test_session.py | 会话管理 | 5 |
| test_stuck.py | 卡死检测（指纹滑窗提醒→熔断、提醒后恢复、禁用回落、变化动作豁免） | 4 |
| test_subagent.py | 子 Agent 系统（隔离引擎、工具白名单、并行、级联打断） | 22 |
| test_tools.py | 工具系统 | 9 |
| test_tui.py | TUI/CLI 界面、思考开关与行缓冲 | 30 |
| test_webui.py | Web 界面、SSE 流式协议、/api/thinking | 16 |

### 11.3 运行测试

```bash
# 运行全部测试
pytest tests/ -v

# 运行特定模块
pytest tests/test_compressor.py -v
pytest tests/test_project_memory.py -v

# 生成覆盖率报告
pytest tests/ --cov=zouwucode --cov-report=term
```

---

## 12. 移植指南

### 12.1 核心架构移植要点

在更换技术栈时，以下架构设计是必须保留的：

1. **Cache-First 引擎设计**
   - Append-only 对话模式
   - 系统提示词和工具 Schema 冻结
   - 字节稳定的前缀维护

2. **三层上下文管理**
   - 轻量级索引（始终加载）
   - 按需获取的项目知识
   - 全文 grep 的原始对话

3. **多模式安全体系**
   - Plan（只读）/ Agent（交互）/ YOLO（自动）
   - 权限沙箱多层检查

4. **三档推理强度**
   - low（无推理）/ medium（均衡）/ max（深度推理）
   - 映射到模型的 reasoning_effort 参数

5. **流式思考显示**
   - Provider 增量 yield 思考/正文 delta，末帧只携带元数据（防内容翻倍）
   - 引擎通过 `on_thinking_delta` / `on_content_delta` 回调推送增量
   - `show_thinking` 全局开关（默认开），三端 `/thinking` 统一切换

6. **任务打断与引擎安全限制**
   - `request_interrupt()` + 安全点检查（流 chunk / 轮边界 / 工具执行前），打断后为未执行 tool_calls 补齐 synthetic 结果保持前缀有效
   - `max_tool_rounds` / `turn_timeout_seconds` / `task_timeout_seconds` / `max_consecutive_tool_errors` 四重限制

7. **子 Agent 独立引擎**
   - 每个子 Agent 私有 EngineLoop（隔离缓存/统计/打断状态，共享 provider）
   - 工具白名单双重生效（Schema 过滤 + 执行器拦截），主引擎打断级联传播

8. **Devin 式 dev 工作流**
   - git worktree 隔离 + `dev/*` 分支白名单（保护分支拒绝、force-push 前二次校验）
   - 自主 agent 会话跑在 worktree 内（沙箱根 = worktree），验证失败回灌迭代（有界）
   - 产出永远是 Draft PR（人工 review 边界）；失败回帖 issue，不产生 PR
   - SQLite 队列原子认领（`UPDATE ... RETURNING`）+ worker 进程隔离 + 超时重入队恢复

9. **质量门禁与脚手架度量**（对标成熟 agent 的共识模式）
   - 多层验证管线（lint/typecheck/test/security，缺工具自动跳过不假过）
   - 独立只读 reviewer（plan 模式 + 只读白名单 + 零共享上下文）打破自写自测偏差
   - CI 联动：验证延伸到真实多平台矩阵
   - 卡死检测：动作指纹滑窗抓"成功但不推进"的死循环
   - CodeAct `python_exec`：可执行动作面（持久 REPL，base64 帧协议）
   - 评测 harness：确定性行为断言 + 真实栈，改进可量化
   - 生命周期钩子：pre_tool fail-closed / post_tool fail-open

### 12.2 模块移植优先级

| 优先级 | 模块 | 原因 |
|--------|------|------|
| P0 | 核心引擎（EngineLoop + Cache） | 项目核心，必须最先实现 |
| P0 | Provider 适配器 | 与 LLM 通信的基础 |
| P0 | 工具系统 | 核心功能依赖 |
| P1 | 权限沙箱 | 安全必要 |
| P1 | 会话管理 | 用户体验 |
| P1 | 项目记忆 | 核心特性 |
| P2 | 多界面（CLI/TUI/Web UI） | 用户体验增强 |
| P2 | 对话压缩 | 长会话优化 |
| P2 | 滑动窗口 | 上下文管理 |
| P2 | dev 模式（worktree 管线 + 队列） | 差异化能力，依赖引擎/沙箱/记忆全部就绪 |
| P3 | MCP 协议 | 扩展性 |
| P3 | LSP 集成 | 诊断增强 |
| P3 | 多 Agent 编排 | 高级特性 |

### 12.3 关键注意事项

1. **API 兼容性**：确保新实现支持 DeepSeek V4 的 reasoning_effort 参数和 prefix-cache 响应头
2. **成本控制**：保留缓存命中率统计，监控使用成本
3. **错误处理**：特别关注 `tool` 角色消息必须有前置 `tool_calls` 的限制
4. **配置管理**：支持 YAML/JSON 格式，提供 --init-config 自动生成
5. **便携式部署**：数据文件与可执行文件同级，不写系统目录

### 12.4 接口规范

如果需要在新项目中保持兼容，以下是关键接口规范：

**LLM 提供商接口**：
```python
class BaseProvider:
    # stream_thinking 控制是否增量推送思考（reasoning_content）delta
    async def chat_stream(messages, tools, temperature, max_tokens,
                          stream_thinking=True) -> AsyncIterator[ModelResponse]
    async def chat(messages, tools, temperature, max_tokens) -> ModelResponse
    # 流式约束：增量 yield content/thinking delta；末帧只携带
    # tool_calls/usage/cache_hit（content=""、thinking=""），否则内容会翻倍
```

**工具接口**：
```python
class BaseTool:
    def get_spec() -> ToolSpec
    async def execute(**kwargs) -> ToolResult
```

**引擎接口**：
```python
class EngineLoop:
    def set_reasoning_intensity(level)
    def freeze_session(system_prompt, tool_schemas)
    # 流式回调（可为 None）：Provider 增量 delta 实时派发到 UI 层
    on_thinking_delta: Callable[[str], None] | None
    on_content_delta: Callable[[str], None] | None
    # 工具执行前回调（opencode 风格工具行渲染）
    on_tool_event: Callable[[ToolCall], None] | None
    # 打断 API：request_interrupt 任意线程安全；on_interrupt 级联钩子
    # （子 Agent 管理器用它停止所有运行中的子 Agent）
    def request_interrupt(reason: str = "") -> bool
    on_interrupt: Callable[[], None] | None
    is_running: bool
    # 迭代式有界循环：超限抛 TurnLimitExceeded，打断抛 TaskInterrupted
    async def run(messages, tools, temperature, max_tokens) -> ModelResponse
    def get_cache_summary() -> dict
```

**Web UI API 端点**：
```
GET  /api/status   → {mode, running, reasoning, show_thinking, engine, cache_hit_rate, total_requests, total_cost, context_tokens, ...}
POST /api/chat     → {message: string} → {content, thinking, tools, cache_hit, stats}（模块/兼容路径）
POST /api/chat/stream → {message: string} → SSE 流式事件
     data: {"type":"thinking","delta":"..."}       思考增量（show_thinking 关闭时不推送）
     data: {"type":"interrupted",...}              任务被打断（用户 Esc / ⏹）
     data: {"type":"done","content":"...","thinking":"...","tools":[...],"cache_hit":bool,"stats":"..."}
     data: {"type":"error","error":"..."}
POST /api/interrupt → 打断当前运行中的任务（无任务时返回 409）
POST /api/thinking → {} → {show_thinking: bool}（切换思考显示）
POST /api/mode     → {mode: string} → {mode}
POST /api/reasoning → {reasoning: string} → {reasoning}
GET  /api/cache    → {hit_rate, total_cost, ...}
GET  /api/sessions → 会话列表
GET  /api/rules    → 项目规则内容
GET/POST /api/skills → 技能列表 / 加载技能
POST /api/hello-my-zouwucode → 模块命令（规划/执行/状态）
```

---

## 附录 A：项目元数据

| 项目 | 值 |
|------|-----|
| 名称 | ZOUWUCODE |
| 版本 | 1.0.0 |
| 描述 | DeepSeek-native AI Coding Agent |
| 许可证 | MIT |
| Python 版本 | >=3.10 |
| 作者 | 走戊工作室 (Zouwu Studio) |

## 附录 B：CLI 命令参考

| 命令 | 说明 |
|------|------|
| `/help` | 显示帮助信息 |
| `/exit` | 退出程序 |
| `/clear` | 清屏（CLI 清终端；TUI/Web 清聊天视图） |
| `/plan` | 切换到 Plan 模式（只读）；带参数时转为 hello-my-zouwucode 规划（`/plan <task>`） |
| `/agent` | 切换到 Agent 模式（交互审批） |
| `/yolo` | 切换到 YOLO 模式（自动执行） |
| `/mode` | 显示当前模式 |
| `/cache` | 显示缓存性能统计 |
| `/sessions` | 列出已保存的会话 |
| `/memory` | 显示项目记忆上下文 |
| `/goal` | 显示当前目标 |
| `/goal set <目标>` | 设置新目标 |
| `/compress` | 显示压缩器状态 |
| `/context` | 显示上下文窗口统计 |
| `/reasoning` | 显示/设置推理强度 |
| `/reasoning <level>` | 设置推理强度（low/medium/max） |
| `/thinking` | 切换流式思考显示（ON/OFF，三端统一） |
| `/decide <决策>` | 记录架构决策 |
| `/rules` | 显示项目规则（`.zouwucode/rules.md`） |
| `/rules edit` | 在编辑器中打开项目规则 |
| `/skill` | 列出可用与已加载技能 |
| `/skill load <name>` | 加载技能 |
| `/skill unload <name>` | 卸载技能 |
| `/agents` | 显示子 Agent 系统状态与扩展状态 |
| `/hello-plan <task>` | hello-my-zouwucode：Prometheus 规划 |
| `/hello-start-work` | hello-my-zouwucode：Atlas 执行当前计划 |
| `/hello-status` | hello-my-zouwucode：Boulder/记事本/计数状态 |
| `/hello-agents` | hello-my-zouwucode：列出 11 个内置 Agent |
| `/hello-categories` | hello-my-zouwucode：列出任务分类 |
| `/hello-ultrawork <task>` | hello-my-zouwucode：全自动流水线（或 `ultrawork`/`ulw` 前缀） |

## 附录 B2：dev 模式命令参考

| 命令 | 说明 |
|------|------|
| `zouwucode dev <issue-url>` | 前台执行：拉取 issue → worktree → 自主编码 → 验证 → Draft PR |
| `zouwucode dev <owner/repo#N>` | 同上（slug 形式引用 issue） |
| `zouwucode dev "<自由文本任务>"` | 本地任务：仅建 dev/* 分支与 worktree，不关联 GitHub |
| `zouwucode dev --queue <ref>` | 任务入队（SQLite，`zouwucode_data/dev_queue.sqlite`） |
| `zouwucode dev --workers N` | N 个 worker 子进程排空队列 |
| `zouwucode dev --watch [owner/repo]` | 轮询带 `zouwucode:do` 标签的 open issue，自动入队执行 |
| `zouwucode dev --interval <s>` | watch 轮询间隔（默认 60s） |
| `zouwucode dev --status` | 队列统计 + 最近任务 |

## 附录 B3：eval 评测命令参考

| 命令 | 说明 |
|------|------|
| `zouwucode eval` | 跑内置评测任务（真实 agent 栈 + 确定性判分） |
| `zouwucode eval --list` | 列出可用任务 |
| `zouwucode eval --task <name>` | 只跑指定任务 |
| `zouwucode eval --tasks <dir>` | 跑自定义 YAML 任务目录 |

## 附录 C：快捷键

| 快捷键 | 功能 |
|--------|------|
| Ctrl+S | 循环切换模式（plan → agent → yolo → plan）（TUI / Web UI） |
| Ctrl+R | 循环切换推理强度（low → medium → max → low）（TUI / Web UI） |
| Ctrl+L | 清屏（TUI / Web UI；CLI 用 `/clear` 命令） |
| Esc | 打断当前运行中的任务（TUI 弹确认对话框；Web UI 确认后调 `/api/interrupt`）；空闲时 TUI 聚焦输入框 |
| Ctrl+C | CLI 运行中打断当前任务（空闲时提示 `/exit` 退出） |
| Ctrl+Enter | 发送消息（Web UI；TUI 中为换行） |

---

*文档版本：v1.4.0 · 最后更新：2026-10-08 · 基于 ZOUWUCODE 项目完整源码分析（含 dev 质量门禁与对标成熟 agent 升级：卡死检测/评测 harness/CodeAct/PLAN-REFLECT/钩子）*