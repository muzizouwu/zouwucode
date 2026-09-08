# hello-my-zouwucode 复刻 · 功能测试报告（ZOUWUCODE）

> 本报告记录 hello-my-zouwucode 多智能体编排模块（复刻自 OpenCode 生态的 oh-my-opencode）集成进 ZOUWUCODE 后的全部功能测试：单元测试、集成回归、端到端冒烟验证，以及过程中修复的既有缺陷。

---

## 1. 测试环境

| 项 | 值 |
|---|---|
| 操作系统 | Windows 10+（PowerShell 环境） |
| Python | ≥ 3.10 |
| 测试框架 | pytest 8.x + pytest-asyncio（`asyncio_mode = auto`） |
| 项目根目录 | 仓库克隆目录 |
| 被测模块 | `hello_my_zouwucode/`（9 个模块文件：agents / categories / intent_gate / boulder / notepad / planner / atlas / orchestrator / module）+ 三端集成（CLI / TUI / Web） |
| 测试命令 | `python -m pytest -q` |

## 2. 测试结论总览

| 指标 | 结果 |
|---|---|
| 新增单元测试 | **53 项，全部通过**（`tests/test_hello_my_zouwucode.py`） |
| 全量回归测试 | **167 项，全部通过**（原 104 + 新增 53 + 回归新增 10） |
| 端到端冒烟（Web `/api/hello-my-zouwucode`） | 通过（status / ultrawork / plan / start-work / 错误路径） |
| 集成过程中修复的既有缺陷 | 1 处（Web 服务器 POST body 截断，详见 §5） |
| 结论 | ✅ 复刻功能实现完整、可运行、无回归 |

```bash
$ python -m pytest -q
........................................................................ [ 43%]
........................................................................ [ 86%]
.......................                                                      [100%]
167 passed in 2.75s
```

## 3. 单元测试明细（tests/test_hello_my_zouwucode.py）

### 3.1 Category 语义路由（4 项）

| 用例 | 验证点 | 结果 |
|---|---|---|
| `test_inventory_size` | 11 个 category，`CATEGORY_DEFAULTS` 数量一致 | ✅ |
| `test_get_category_known` | `ultrabrain → max`、`quick → low` 推理档位映射 | ✅ |
| `test_get_category_fallback` | 未知 category 回退到 `unspecified-high` | ✅ |
| `test_list_and_table` | 列表与表格渲染包含全部字段 | ✅ |

### 3.2 IntentGate 意图门控（9 项）

| 用例 | 验证点 | 结果 |
|---|---|---|
| `test_quick_question` | "What is Python?" → QUICK_QUESTION | ✅ |
| `test_fix` | "please fix the bug" → FIX | ✅ |
| `test_implementation` | "implement a new feature" → IMPLEMENTATION | ✅ |
| `test_explore_hard_rule` | grep 硬规则 → EXPLORE | ✅ |
| `test_planning` | "make a plan..." → PLANNING | ✅ |
| `test_chinese_fix` | 中文"帮我修复这个报错" → FIX | ✅ |
| `test_confidence_range` | 置信度 ∈ [0,1]，命中关键词列表非空 | ✅ |
| `test_label_and_prompt` | 中文标签与路由表渲染 | ✅ |
| `test_classify_async` | 异步分类接口 | ✅ |

### 3.3 Agent 清单（4 项）

| 用例 | 验证点 | 结果 |
|---|---|---|
| `test_eleven_agents` | 11 个专职 Agent | ✅ |
| `test_primary_vs_subagent_count` | 4 个 PRIMARY + 7 个 SUBAGENT | ✅ |
| `test_get_agent_fallback` | 未知名称回退 `sisyphus-junior` | ✅ |
| `test_inventory_table` | 清单表格渲染 | ✅ |

### 3.4 Boulder 跨会话状态（5 项）

| 用例 | 验证点 | 结果 |
|---|---|---|
| `test_empty_state` | 空状态"No active plan" | ✅ |
| `test_start_and_progress` | 启动 → 取任务 → 完成/失败，进度 1/2，失败可重试 | ✅ |
| `test_persistence` | 磁盘持久化与重载 | ✅ |
| `test_session_tracking` | 多会话追加 | ✅ |
| `test_clear` | 清除状态 | ✅ |

### 3.5 Notepad 智慧积累（5 项）

| 用例 | 验证点 | 结果 |
|---|---|---|
| `test_write_read` | 读写 5 类学习文件 | ✅ |
| `test_append_and_helpers` | learnings/decisions/issues/verification/problems 辅助方法 | ✅ |
| `test_context_block` | 注入模型前的上下文块（含 section 标签） | ✅ |
| `test_safe_name` | 非法字符安全化（`Plan / With / Slashes!` → `Plan---With---Slashes`） | ✅ |
| `test_summary` | 摘要与 5 个文件常量 | ✅ |

### 3.6 Planner 规划器（7 项）

| 用例 | 验证点 | 结果 |
|---|---|---|
| `test_slugify` | 标题安全化（保留 CJK；空串回退 `plan-{timestamp}`） | ✅ |
| `test_parse_plan_todos` | `- [ ] / 1. [ ] / * [ ]` 任务正则解析，忽略普通行 | ✅ |
| `test_parse_plan_todos_missing_file` | 缺失文件返回空列表 | ✅ |
| `test_latest_plan` | `.hello-my-zouwucode/plans/` 下按修改时间取最新计划 | ✅ |
| `test_extract_plan_markdown` | 从模型回复提取 ```markdown``` 代码块 | ✅ |
| `test_create_plan_and_write` | 全流程：Prometheus 起草 → Metis 差距分析 → 写 `.hello-my-zouwucode/plans/{slug}.md` | ✅ |
| `test_create_plan_empty_returns_none` | 空回复不落盘 | ✅ |

### 3.7 Atlas 执行器（4 项）

| 用例 | 验证点 | 结果 |
|---|---|---|
| `test_result_defaults` | `OrchestrationResult` 默认字段 | ✅ |
| `test_execute_completes_all_tasks` | 2 个任务逐次执行、验证、标记完成，进度 2/2 | ✅ |
| `test_execute_failed_worker` | 空回复视为失败，进度不回退 | ✅ |
| `test_execute_no_plan` | 无活动计划 → "No active plan" | ✅ |

### 3.8 Sisyphus 编排器（7 项）

| 用例 | 验证点 | 结果 |
|---|---|---|
| `test_detect_ultrawork` | `ultrawork …` / `ulw: …` 关键词提取，普通消息返回 None | ✅ |
| `test_ultrawork_quick_question` | 快速问答直接回答（不走重管线） | ✅ |
| `test_ultrawork_implementation_pipeline` | 完整管线：分类 → 规划 → 执行，2/2 完成，计划 + boulder 落盘 | ✅ |
| `test_run_plan` | 非交互规划 + 自动执行 | ✅ |
| `test_start_work_resume` | INIT 全量执行 → RESUME 复用状态、会话追加 | ✅ |
| `test_start_work_no_plan` | 无计划报错 | ✅ |
| `test_module_config_defaults` | `HelloMyZouwucodeConfig` 默认值（enabled/state_dir/default_category/interactive_planning） | ✅ |

### 3.9 模块集成（8 项，模块化架构新增）

| 用例 | 验证点 | 结果 |
|---|---|---|
| `test_manager_register_dispatch` | `ModuleManager` 注册/状态查询，`dispatch_command` 未处理命令返回 None，`dispatch_event` 广播 | ✅ |
| `test_manager_unregister` | 注销模块后 `is_loaded` 为 False | ✅ |
| `test_module_handle_message_ultrawork` | `handle_message` 拦截 `ultrawork` 前缀返回渲染 dict，普通消息返回 None | ✅ |
| `test_module_handle_command_inventory` | `/hello-agents`、`/hello-categories`、`/hello-status` 命令输出 | ✅ |
| `test_module_handle_command_unknown` | 未知命令返回 None（回退内建逻辑） | ✅ |
| `test_module_status_payload` | `status_payload` 结构（module/version/agents=11） | ✅ |
| `test_app_loads_module` | `ZOUWUCODEApp` 初始化后 `hello-my-zouwucode` 已注册进 `ModuleManager` | ✅ |
| `test_module_disabled_not_loaded` | `config.hello_my_zouwucode.enabled=false` 契约 | ✅ |

## 4. 端到端冒烟测试（Web / API 层）

启动 `zouwucode --web` 后对新增 `/api/hello-my-zouwucode` 端点验证（真实 DeepSeek API）：

| 场景 | 请求 | 预期 | 结果 |
|---|---|---|---|
| 状态查询 | `POST /api/hello-my-zouwucode {"action":"status"}` | 200，返回 11 agents / 11 categories / boulder 状态 | ✅ |
| Ultrawork 快速问答 | `POST /api/hello-my-zouwucode {"action":"ultrawork","task":"1+1=?"}` | 200，返回答案 | ✅ |
| 完整实现管线 | `POST /api/hello-my-zouwucode {"action":"ultrawork","task":"…实现任务…"}` | 200，11/11 tasks complete，生成计划 + boulder + notepad | ✅ |
| 缺失 task 参数 | `POST /api/hello-my-zouwucode {"action":"ultrawork"}` | 400 参数校验错误 | ✅ |
| 未知 action | `POST /api/hello-my-zouwucode {"action":"nope"}` | 400 未知操作 | ✅ |
| 普通聊天 ultrawork 前缀 | `POST /api/chat {"message":"ultrawork 修复xx"}` | 命中模块派发（`dispatch_message`）而非普通对话 | ✅ |

TUI / CLI 同路径已通过 `tests/test_hello_my_zouwucode.py::TestOrchestrator` / `TestModuleIntegration` 覆盖（同一编排核心 + 同一 ModuleManager 派发），命令入口见使用说明。

## 5. 集成过程中修复的既有缺陷

**Web 服务器 POST body 截断**（[server.py](file:///g:/zouwucode/zouwucode/webui/server.py) `handle_request`）

- **现象**：所有带 JSON body 的 POST 接口（`/api/mode`、`/api/reasoning`、`/api/skills`、`/api/chat`、新增 `/api/hello-my-zouwucode`）偶发 `Expecting value: line 1 column 1 (char 0)`。
- **根因**：原实现仅 `reader.read()` 一次；当 body 与请求头分属不同 TCP 包时 body 被截断丢弃。
- **修复**：按 `Content-Length` 循环读取直至完整请求体。
- **回归**：修复后 `/api/hello-my-zouwucode` 全部场景 200，其余 POST 端点正常。

## 6. 回归范围与风险

- **未改动原功能**：CLI / TUI / Web 的既有对话、模式切换、推理强度、规则/技能等 104 项原测试全部保持通过。
- **回归新增 10 项**（模块化重构后补充，防复发）：
  - `tests/test_tui.py` +2：`test_ctrl_s_cycles_modes`（Ctrl+S 三键循环序列 plan→agent→yolo）、`test_slash_commands_switch_mode`（`/plan`/`/agent`/`/yolo` 经 `action_set_mode` 切换）。
  - `tests/test_webui.py` +8：`TestWelcomeArt` 4 项（`white-space: pre` 保留换行、横幅恰好 6 行、字母完整、窄屏溢出规则）与 `TestResponsiveLayout` 4 项（`@media(max-width:560px)` header 换行、rules-content 长 token 折行、session-item 长名折行、thinking-body 内部滚动）。
- **过程中修复的回归缺陷**：
  - TUI `/plan`、`/agent`、`/yolo` 命令在模块化改造后误调不存在的 `self.set_mode()`（正确为 `action_set_mode()`），会抛 `AttributeError` —— 已修复并加回归测试。
  - WebUI 欢迎横幅因 CSS 缺 `white-space: pre`，ASCII 艺术字换行被折叠成 2 行导致"纵向压缩" —— 已修复并加回归测试。
- **新增耦合面**：`config.py`（`HelloMyZouwucodeConfig`）、`zouwucode/modules/manager.py`（`ModuleManager`）、`hello_my_zouwucode/module.py`（`HelloMyZouwucodeModule`）、三端命令入口、`/api/hello-my-zouwucode`。均有对应测试或冒烟覆盖。
- **已知差异**：与原版的多模型 fallback 链相比，本项目以 DeepSeek 三档 `reasoning_effort` 做语义等价适配（详见集成文档 §3），不属于缺陷。

## 7. 测试命令速查

```bash
# 仅运行 hello-my-zouwucode 新增测试
python -m pytest tests/test_hello_my_zouwucode.py -q   # 53 passed

# 全量回归
python -m pytest -q                                    # 167 passed

# 详细输出
python -m pytest tests/test_hello_my_zouwucode.py -v
```

---

*报告版本: v1.0.0 · 最后更新: 2026-08-04*
