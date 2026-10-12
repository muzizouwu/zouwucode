# Changelog

本项目遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/) 格式，
版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。


## [1.0.8] — 2026-10-13

**逐消息 rewind**（对标 Claude Code `/rewind`、Cline checkpoints）：能回退到会话中的**任意消息点**，
并可选恢复**代码 / 对话 / 两者**——"犯错成本趋零"才敢放手让 agent 跑。

### Added
- **`SessionManager` 检查点**：每轮快照绑定一个消息下标（`index -> rev`），随会话持久化；
  新增 `record_checkpoint()` / `list_checkpoints()` / `rewind(index, files, conversation)`。
- **`WorkspaceSnapshotter.reset_to(rev)`**：把工作区还原到**任意**历史快照（不是"N 步前"）；
  历史**向前**追加（checkout + 删除新增 + 新提交），因此旧检查点始终可达、仅回滚代码不会孤立对话检查点。
  空基线（rewind 到 0）也能正确处理。
- **`ZOUWUCODEApp.rewind_to(index, scope)`**：`scope ∈ {files, chat, both}`；
  回滚对话时**重建冻结会话并回放**，保证引擎上下文与日志一致（否则模型还看得见被回退掉的轮次）。
  顺带抽出 `_rebuild_context_from_log()`，`resume_session` 复用之（去重）。
- **三端命令**：CLI/TUI `/rewind [N] [files|chat|both] | list`；Web `/rewind` + 端点
  `POST /api/rewind`、`GET /api/checkpoints`（忙时 409）；浏览器回滚后重渲染历史。

### Changed
- 空白工作区也能拍**基线快照**（首次提交允许空树），`/rewind 0` 因此可回到会话起点。

### Tests
- 新增 `tests/test_v108_rewind.py`（+10：代码-only / 对话-only / 两者、回到 0、越界拒绝、检查点截断与持久化、引擎上下文重建）。套件 **505 → 515 全绿**。

### Known
- TUI 正文流式仍未做。

## [1.0.7] — 2026-10-13

三端同步互通补到 100%：Web 也流式输出正文，且能**在浏览器里续接历史会话**。

### Added — Web 正文流式
- Web 之前只有"思考"流式，正文最后一次性蹦出（感知延迟）。现 SSE 增加 `content` 事件，浏览器边收边渲染（`addAssistantStream`），`done` 用 `streamed` 标记避免正文重复渲染。

### Added — Web 会话续接（`/resume <id>`）
- 此前三端里只有 CLI/TUI 能在启动时 `--resume/--session` 续接；浏览器只能列会话、不能续。现新增：
  - `ZOUWUCODEApp.resume_session(id)`：加载会话并**重建冻结会话 + 回放历史**到运行中的引擎（仅加载文件会让模型看到空对话）。
  - 端点 `GET /api/session/current`、`POST /api/session/resume`（`_chat_busy` 时返回 409，避免与进行中的任务打架）。
  - 浏览器命令 `/resume <id>`：清屏并渲染已续接的历史轮次；`/help` 已列出。

### Tests
- 新增 `tests/test_v107_webstream.py`（+12，含真实 App 的续接-回放功能测试）。套件 **493 → 505 全绿**。

### Known
- TUI 正文流式（需处理 RichLog 增量渲染）仍未做。

## [1.0.6] — 2026-10-13

对标"最强 agent"的第二轮：把引擎的**并发、韧性、流式**这三件最影响"好用"的事做实。

### Changed — 引擎并发（大幅降延迟 = 更省钱）
- **同一轮内的独立工具调用改为并发执行**（`asyncio.gather`，对标 Claude Code / opencode）：
  一轮里 N 个读取/抓取从"N 次等待"降为"1 次等待"。交互式 **agent 模式仍保持串行**——并发审批弹窗会争夺终端。
- 结果**按模型给出的原始顺序**落盘，prefix 的 API 契约不变。

### Fixed — 韧性
- **单工具超时**：此前一个卡住的工具会拖死整个任务（deadline 只在轮边界检查）。现在每个工具有硬超时（`turn_timeout` 与任务剩余时间取小），超时返回**错误结果让模型自行反应**，且不影响同轮其它工具。
- **中断语义保持**：中断在每个工具执行前检查（"工具之间可打断"），且**已执行工具的结果会保留**、只有未执行的才补合成结果——批量执行不再丢失已完成进度。
- **工具失败不再丢错误文本**：coordinator 此前只要 stdout 非空就丢掉 `.error`，模型/用户看不到失败原因。现保留 **stdout + stderr 两路**。

### Added — 正文流式（三端里先落 CLI）
- CLI 现在**流式输出助手正文**（此前只有思考流式、正文最后一次性蹦出——感知延迟是"卡"的主因）；带 `_content_streaming` 守卫避免重复打印。

### Tests
- 新增 `tests/test_v106_concurrency.py`（+6：并发确实快于串行、agent 模式保持串行、超时不拖死、结果顺序、错误文本、CLI 流式接线）。套件 **487 → 493 全绿**。

### Known
- 正文流式尚未接到 TUI / Web（TUI 需处理 RichLog 增量渲染，下轮做）；Web 端 resume 仍未接线。

## [1.0.5] — 2026-10-13

「做大而全、不臃肿、经济实惠、三端同步互通」的一轮自检。四路并行审计（三端一致性 / 引擎成本与工具 / 竞品能力 / 臃肿与死代码）后，按证据修复：

### Fixed — 正确性（会算错 / 会骗人）
- **版本三处不一致**：App 横幅 `APP_VERSION="1.0.0"`、`build.py VERSION="1.0.0"`、包版本 1.0.4 —— 用户看到的版本是假的。现统一为 `zouwucode.__version__` 单一来源。
- **配置被命令行悄悄覆盖**：`--mode` 默认 `"agent"`、`--reasoning` 默认 `"medium"` 都是有值真值，导致 `config.yaml` 里的 `sandbox.default_mode` / `reasoning_intensity` **在正常启动时永远不生效**。现默认改为 `None`（未显式传参就不覆盖）。
- **`--provider` 是死开关**：声明了却从没被读取，`--provider openai` 形同虚设。现已接线（并修 `create_provider`：未配置的 provider key 会按名字推断 api_type，不再一律造 DeepSeek 客户端）。
- **DeepSeek 在 low 推理下成本恒为 $0**：`stream_options` 被 pop，拿不到 usage → 每轮都记 $0，成本熔断失效。现始终请求 usage。
- **成本按单一模型硬编码**：无论用哪个模型都按 V4-Flash 计价，`max_cost_usd` 熔断因此算错。现改为**按模型的价格表**（含 deepseek-v4-pro/chat/reasoner、gpt-4o(-mini)，未知模型回落 V4-Flash），并修掉 `cached>prompt` 时的负数成本。
- **OpenAI 供应商 429/5xx 从不重试**：它抛的是裸 `RuntimeError`，而引擎只重试 `ProviderAPIError`。现抛带 `status_code` 的 `ProviderAPIError`。
- **Web `/api/status` 谎报上下文预算**：写死 `max_context_tokens: 1048576`，无视配置。现读真实配置。

### Added — 三端同步互通（Web 补齐到 CLI/TUI 水平）
- Web 新增端点：`GET /api/memory`、`POST /api/goal`、`POST /api/decide`、`GET /api/context`、`GET /api/compress`、`GET /api/agents`。
- Web 新增斜杠命令：`/plan /agent /yolo`、`/memory`、`/goal [set X]`、`/decide`、`/context`、`/compress`、`/agents`、`/exit`；`/hello-agents` 由硬编码文本改为**真实派发**到模块。
- CLI 补上 TUI/Web 已有的**工具调用显示**（opencode 风格 `→ Read file …`）。
- CLI **Ctrl+C 现在打断运行中的任务**（此前直接 `sys.exit(0)` 把任务连进程一起杀掉，键盘打断分支根本不可达），与 TUI/Web 对齐。

### Changed — 不臃肿（去重与清理）
- 抽出 `runtime.create_subagents_and_extensions()`，收敛 CLI 与 TUI **逐字节重复**的子代理/扩展装配代码（原来两处已开始漂移）。
- 删除 5 个无用导入（`dev/pipeline.py`、`extensions/host.py`×3、`tools/agent_tools.py`、`webui/server.py`）。修正确认 `tui/textual_app.py` 的 `Screen` 是误报（有 10 处引用）。

### Tests
- 新增 `tests/test_v105_parity.py`（+35）。套件 **452 → 487 全绿**。

### Known — 仍未做（诚实交代）
- 正文流式（目前只有思考流式）；Web 端**会话续接**（resume）尚未接线（CLI/TUI 已有）；`cache.enabled`/`append_only`/`auto_save_interval`/`theme`/`language` 依旧**不加回**（无真实语义，见 [1.0.4]）。
- 竞品差距清单（repo-map、逐消息 rewind、成本透视图、子代理 worktree 隔离、本地进程外后台代理）见 `G:\261012\docs\` 审计笔记，作为后续路线。

## [1.0.4] — 2026-10-13

把 1.0.3 删掉的两个"死配置"里**真正有需求的那一个**补成功能再加回来，并如实交代其余为什么不能加。

### Added — 工作区回滚（`session.rollback_enabled`）
- `SessionManager` 的 docstring 从第一天就写着 "save, resume, fork, and rollback"，
  但代码里从来没有 rollback——这次把它做实。
- 新建 `zouwucode/session/snapshot.py`：`WorkspaceSnapshotter` 用**侧仓库 git 快照**
  （独立 `GIT_DIR`，work tree 指向工作区）记录工作区状态，**完全不碰项目自己的 `.git`**
  （仿 DeepSeek-TUI 思路；仅调用标准 `git` 可执行文件，缺 git 时降级为安全空操作）。
- 快照时机：会话开始（基线）+ 每完成一轮（`SessionManager.log_turn` 之后），
  因此 `HEAD` 恒等于"最后一轮之后的状态"。
- `/rollback [N|list]` 三端可用（CLI / TUI / Web `POST /api/rollback`、`GET /api/snapshots`）：
  用 `git reset --hard` 回到 `HEAD~N`（撤销最近 N 轮的改动 —— 被重置删除的新增文件一并清除），
  连续 `/rollback` 会继续向更早回退。排除 `node_modules/`、`__pycache__/`、`.venv/`、
  `zouwucode_data/` 等噪声；工作区里从未被快照过的未跟踪文件不会被删。
- 回归测试：`tests/test_rollback.py`（快照/回滚/连续回滚/边界拒绝/禁用空操作/排除项/接线）。

### Added — 缓存命中率统计窗口（`cache.stats_window`）
- `CacheStats(window_size=...)` 的滚动窗口长度现在由 `cache.stats_window` 决定
  （驱动 `/cache` 与状态栏的 `window_hit_rate`），不再是写死的 50。

### Unchanged — 另外两个"死配置"为什么**不**加回来（不是漏做，是刻意）
- `cache.enabled` / `cache.append_only`：缓存在这个引擎里是**结构性不变的**
  （只追加），而 `PrefixCache` 同时充当会话消息存储——把它关掉会让模型看不到工具结果，
  这个开关**没有可实现的真实语义**，留着只会骗人。
- `auto_save_interval`：轮次是同步落盘的，间隔旋钮不可能生效。
- `theme` / `language`：UI 是单主题、zh-first，无第二套实现可切。

## [1.0.2] — 2026-10-12

自检审计修复（原则：只做有回归测试、且不改变既有 UX 的修复）。

### Fixed — 正确性
- **python_exec 假成功**：snippet 抛异常时工具曾返回 `success=True`（traceback 只躺在输出里），模型会把失败步骤当成功继续推理。现由 REPL 驱动输出精确错误标记，工具返回 `is_error=True`，traceback 原样保留。
- **轮数日志失真**：`Task end` 的 `rounds_used<=25` 改为真实值 `rounds_used=N (base cap M, extensions K)`；轮数扩展后轮号显示为 `Round 27 (base cap 25, +1 extension(s))`，不再出现 `Round 26/25`。
- **沙箱误杀正常命令**：`(mkfs|format|dd|fdisk)`、`(reg\s|regedit)` 改为词界/上下文精确匹配。`Format-Table`、路径含 `codebuddy` 等命令此前会被 `Command blocked by sandbox` 拒绝并白丢模型轮次。
- **glob 越权枚举**：`glob` 是唯一没做沙箱路径检查的文件工具，现已补齐（与 read/write/edit/ls 一致）。
- **read 读 UTF-16 乱码**：PowerShell 重定向产物（UTF-16 BOM/无 BOM）不再输出 NUL 乱码。
- **WebUI `/api/sessions` 恒为空**：路由读的是 `engine.sessions`（不存在），改由 `SessionManager` 注入（新增构造参数 `sessions_manager`，向后兼容）。
- **会话摘要无信息**：退出时记录的摘要从纯计数器（经常全是 0）改为「轮数 + 缓存命中率 + 最后一轮用户请求」，`SessionManager.current_log()` 新增；"继续上次任务" 从此有据可依。
- **MCP 客户端健壮性**：请求 id 递增并按 id 匹配应答、跳过通知行、30s 超时；此前硬编码 id 1/2/3 且假设"下一行就是回复"，服务器多发一行就会错乱/挂死。

### Fixed — 安全与诚实
- **子 Agent 递归防护**：子代理不再能看到 `task` 工具（schema 过滤 + executor 双保险），无法再派生孙代理形成失控树。
- **删除"需审批"的不实表述**：agent 与 yolo 在工具执行上本就等价（无审批实现）。系统提示与 USAGE 工具表改为如实描述"沙箱保护"。
- **TLS 可诊断**：证书校验失败给出可操作提示；`ZOUWUCODE_TLS_INSECURE=1` 可显式关闭校验（默认仍然校验，仅本机/离线场景使用）。
- **日志加 pid**：多进程共享同一日志文件时来源可归因（此前出现过两个进程行交错、时间倒流）。

### Fixed — TUI 可读性（用户实测反馈）
- **回复里的 `[...]` 不再被吞**：聊天区以 `markup=True` 渲染，模型文本中的 `[interrupted]`/`[dim]` 会被 Rich 当标记吃掉，`[/]` 更会直接抛错把整条回复替换成 Error 行。现改为 `markup=False` + 全部经 `Text` 对象渲染，任何方括号原样显示。
- **上翻历史不再被拽回底部**：RichLog 默认每次写入都自动滚到底部，流式输出（思考增量/工具行/回复）会把正在阅读的视口扯走。新增 `_StickyRichLog`：只有视口本来就在底部时才自动跟随；发送自己的消息时仍会主动跳到底部。
- **新增键盘滚动**：PgUp/PgDn 翻页、Ctrl+Home/Ctrl+End 跳顶/底（输入框聚焦时同样有效），`/help` 已列出；鼠标滚轮本就可用。此前输入框占着焦点，没有任何键能滚动聊天区，加上 Windows Terminal 里 TUI 处于备用屏幕、窗口滚动条不可用，"看不到上面的内容"因此无解。

### Known — 仍待实现（不假装已修）
- 交互式审批（agent/yolo 语义区分）、CLI/dev 接入上下文压缩、MCP 的 SSE/Streamable HTTP 传输、`rollback_enabled`/`auto_save_interval`/`cache.enabled` 等配置项仍是"声明存在但未接线"。

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

### Fixed
- **并行工具失败即被误杀（"任务卡住不动了"）**：连续工具错误熔断此前按**单个调用**计数——模型一轮并行发出 3 个调用（如 bash + 2×web_search）全部失败时，阈值 3 在同一轮内直接触顶，模型还没看到任何错误反馈就被 abort，表现为"卡住"。现改为按**轮次**计数：一轮内全部失败才记 1 次，保证模型每轮都能收到错误结果并有 `max_consecutive_tool_errors` 轮机会换思路（真死循环仍会熔断）
- **Windows 上 `bash` 工具执行 POSIX 语法即报错**：PowerShell 5.x 不支持 `&&`/`||`/`2>/dev/null`/`$()`，模型按 Linux 习惯写的命令全部失败。现在自动探测 Git for Windows 的 bash.exe，检测到 POSIX 语法时改用它执行；无 bash 时错误信息附带 PowerShell 语法提示（用 `;` 分隔）；工具描述声明当前宿主 shell
- **web_search 静默失败（空错误信息）**：`httpx.ConnectTimeout` 的 `str()` 为空串，导致失败时 error 为空白，模型与用户都看不到原因。现在错误文本带类型与可读原因；搜索改为 Bing 优先 + DuckDuckGo 兜底（DDG 的 html 端点在国内网络常不可达），单引擎挂掉不再导致搜索整体失败；全部失败时建议改用 `web_fetch`
- web_search 的 `count` 参数越界（负数/超大）不再导致异常，钳制到 1..10
- **系统性排查"静默失败"同类问题并根治**（不止截图那两处）：
  - 新增 `zouwucode/errors.py` `describe_exc()`：统一异常格式化，空 `str()` 异常（httpx 超时、asyncio.TimeoutError 等）补可读原因
  - **coordinator 结构性兜底**：任何失败的工具结果若 content 为空，自动回填可读原因（取 output→error→兜底文案）——从架构上杜绝"模型收到空白错误"这一类 bug 再复发
  - 全部 14 处工具/扩展错误路径（file/git/shell/code_exec/task 委派/MCP/LSP 工具）统一改用 `describe_exc`
  - WebUI SSE error 事件与 /api/chat 500 响应不再可能输出空错误（用户侧同样可见）
  - 子 Agent 失败记录、dev worker 队列 error 字段改用 `describe_exc`
  - dev 验证层：命令非零退出且无任何输出时标注 `(no output; exit code N)`，不再给模型空 FAIL
  - `/rules edit` 打开编辑器改为跨平台（Windows `os.startfile` / macOS `open` / Linux `xdg-open`），不再硬编码 notepad
  - 新增 `tests/test_error_feedback.py`（8 用例）锁定三条结构保证
- **TUI 异常退出后终端被鼠标转义序列刷屏**：Textual 运行时会开启 SGR 鼠标上报（DECSET ?1000/1002/1003/1006），若进程在自身 teardown 前死亡（崩溃/被杀/关窗），这些模式残留，回到 PowerShell 后鼠标移动被终端回显成满屏 `^[[<35;…M` 乱码。`run_tui` 现在 finally 中无条件还原（关鼠标上报、恢复光标、SGR reset、关括号粘贴），并把崩溃 traceback 镜像进日志文件——"TUI 无声消失"从此可诊断
- **python_exec 并行子 Agent 互相阻塞 + 变量串扰**（我上一轮引入的回归）：所有调用共享同一个 REPL 子进程、同一把锁、同一份 globals——5 个并行 research worker 实际串行排队（日志实测单次执行被拖到 50-69s），且一个 worker 的变量会泄漏进另一个。现在按 ContextVar 会话隔离：`SubAgent.run` 绑定 `session=self.id`，asyncio.gather 的上下文拷贝语义让每个子 Agent 拥有独立 REPL 与 globals，真正并行
- **python_exec 误杀普通 Python 代码**：旧实现把 shell 危险模式检查（`rm -rf /`、`format`、`reg`、`dd` 等正则）直接扫 Python 源码，含这些子串的合法代码（如 `re.search(r'(reg\s|regedit)')`）被 "Code blocked by sandbox policy" 误杀。现在只保留针对破坏性调用的专用 `_RISKY` 检查，不再复用 shell 正则
- **正常长任务被 `Exceeded max_tool_rounds=25` 拦腰砍断**：轮数上限此前是硬截断——多步任务（如跨文件修编码问题：edit → bash → python_exec 验证，进展完全正常）一旦超过 25 轮即被 abort，前面的工作全部丢失。现改为**软着陆**：预算耗尽时向模型注入一条"收尾或高效继续"提醒并追加一轮预算（`engine.max_round_extensions`，默认 2 次 = 实际最多 75 轮），扩展用尽才熔断；真死循环仍会被兜住（卡死检测、连续错误熔断、总超时、成本熔断均不受影响），`max_round_extensions=0` 恢复旧行为

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
