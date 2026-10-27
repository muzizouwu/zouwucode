# Changelog

本项目遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/) 格式，
版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。


## [1.10.1] — 2026-10-28

修掉 v1.10.0 发布后 CI 暴露的 TUI 贴底竞态（Windows / Python 3.12）。

### Fixed
- **贴底需要多个布局帧才能收敛**：聊天区粘性滚动在"用户已在底部、新内容到达"时按帧补位（写入 → 布局 → 重新贴底 → 必要时再校验）。慢速 runner 上单个 `pilot.pause()` 只能推进一帧，断言看到的是中间态（`scroll_y == max_scroll_y - 1`）。现在 `_settle_follow` 的有界重试从 3 次提高到 5 次，覆盖高负载下的多帧收敛
- **测试对单帧快照过敏**：`test_reading_position_survives_new_output` 改为"有界轮询直到收敛"（最多 12 帧，谓词仍要求真正到达底部），既不再假红也不会掩盖真实回归；"向上滚动时不得把视口拽走"的断言保持不变

### Tests
- `tests/test_tui.py` 新增 `_pause_until` 辅助；TUI/打断相关 57 用例连续 5 轮本地全绿，套件 **677 passed**

## [1.10.0] — 2026-10-28

接入 DeepSeek 官方 Harness（dsh）：让任务可以交给官方 Cordis 插件式运行时执行，同时保留 zouwucode 自有引擎。

### Added
- **`zouwucode harness` 子命令（官方 Python SDK 接入）**：DeepSeek Harness（`dsh`）是官方开源的 Agent Harness，基于 Cordis 插件内核（模型/工具/技能/会话/沙箱/存储/循环/UI 全是可插拔插件）。新增 `zouwucode/harness/` 适配层，通过 `deepseek-harness-sdk`（stdio 上的 NDJSON JSON-RPC）驱动它完成一个任务：
  - 前置检查 `--doctor`：SDK 是否安装、DSH_HOME、workspace、profile、provider、model、推理强度、超时一次看全（缺依赖退出码 2）
  - 配置映射：`api_type=deepseek` → dsh 路由 `deepseek-official`；model 取 provider 配置；推理强度空则沿用 `reasoning_intensity`；DSH_HOME 默认 `<data_dir>/dsh_home`（dsh 不会自动发现 `~/.dsh`）；支持 `--patch` 叠加 Cordis patch 层、`--profile sdk|sdk-minimal`、`--session` 续接、`--max-tokens`、`--timeout`
  - 输出：默认打印 `final_response`；`--json` 给出 `session_id/finish_reason/events/notifications/text`；`--verbose` 把子 Agent/事件通知实时写 stderr
- **反向接入**：`zouwucode mcp-serve` 本就是 MCP stdio 服务端，可把全部内置工具挂进 dsh 的 MCP 插件，复用 zouwucode 的工具实现与沙箱策略（接入指南已写明）。
- **配置项 `harness:`**（`HarnessConfig`）：`enabled/profile/dsh_home/provider/model/reasoning_effort/max_tokens/initialize_timeout_seconds/request_timeout_seconds/patches`，默认全关——不装 SDK、不用子命令时零影响、零新依赖。

### Docs
- 新增 [DeepSeek Harness 接入指南](docs/deepseek-harness接入.md)：安装、前置检查、参数表、退出码、patch 层用法、反向接入、**两套运行时的对标映射表**、故障排查与边界说明
- README 特性表/目录树/近期更新、USAGE 新增 §21、技术规格文档新增 §5.21 与目录树同步

### Tests
- 新增 `tests/test_v1100_harness.py`（18 用例）：配置默认关闭、provider 路由映射、DSH_HOME 默认与覆盖、OpenAI provider 需显式路由、SDK 缺失时的可操作提示、结果与通知映射、`finish_reason=error` 视为失败、空 prompt 拒绝、CLI 的 doctor/文本/JSON/verbose/退出码分支（用假 SDK 注入，不依赖网络与真实运行时）

## [1.9.1] — 2026-10-28

修掉 v1.9.0 发布后 CI 暴露的 TUI 滚动竞态（Windows / Python 3.12 上间歇性失败）。

### Fixed
- **流式输出时"贴底"偶尔差一行**：聊天视图只在用户位于底部时跟随新内容，但 `RichLog` 自带的 `scroll_end` 是对**写入前**的虚拟高度求值、刷新后才落位——新行（尤其滚动条出现导致重新折行）布局完成后，视口会停在末尾前一行（CI 上表现为 `scroll_y == max_scroll_y - 1`）。现在不再让 `RichLog` 自行滚动，而是记录写入时的位置作为锚点，在下一帧布局完成后自行贴底；锚点同时用来判定"用户是否已向上滚动"，一旦发现就放弃补位，绝不把正在读历史的视口拽回底部
- **补位滚动的二次排队**：贴底调用改为 `immediate=True`，避免它再排一次"刷新后滚动"，否则会在用户紧接着向上滚动之后才执行，反而造成跳底
- **CI 偶发红灯的同类测试**：`test_subagent` / `test_v106_concurrency` 的并行用例改用"串行基线 + 起始先于结束"的结构性断言，不再依赖写死的绝对耗时；`test_v190` 的 ripgrep 用例固定引擎与顺序，避免"本机没装 rg / CI 装了 rg"导致的环境差异

## [1.9.0] — 2026-10-28

检索能力补齐 + 上下文预算 + Windows 文件保真：让 Agent 能"按内容找代码"，并堵住单次工具调用撑爆上下文、改写文件编码/换行这两个真实生产痛点。

### Added
- **`grep` 内容检索工具（此前是真实缺口）**：`glob` 只能按文件名找，而 hello-my-zouwucode 的专职 Agent 提示词却写着"用 grep / glob / read 工具"——模型请求了一个并不存在的工具，检索只能退化成逐个 `read`，既慢又烧 token。新增 `GrepTool`：正则搜索文件内容，返回 `相对路径:行号:内容`；支持 `include`（如 `*.py`）、`ignore_case`、`files_only`、`max_results`。实现上 ripgrep 在 PATH 上时优先（`-e` 防 `-` 开头被当参数、限制单行列宽与单文件命中数），否则回落到纯 Python 扫描（在线程池中执行，不阻塞事件循环）；两条路径共用跳过列表（`.git`/`node_modules`/`dist`/`__pycache__` 等）与输出契约，二进制与 >2 MB 文件一律跳过，路径仍受沙箱白名单约束。内置工具 **11 → 12**。
- **工具输出统一限额**（上下文预算是长任务质量的第一杠杆）：`read` 默认 2000 行 / 10 万字符并提示用 `offset`/`limit` 翻页；`bash` 每路输出 3 万字符（首 2 万 + 尾 1 万，中间标记 omitted）；`glob` 200 条；`grep` 默认 100 条；子 Agent 合并报告 1.2 万字符（按 Agent 均分、每人至少 600 字符），全文落盘到 `zouwucode_data/agent_reports/` 供 `read` 取回。**所有截断都显式告知**，不再让模型误以为已看到全部内容。

### Fixed
- **非 UTF-8 文件被静默改写**：`edit`/`write` 只按 UTF-8 读写——zh-CN Windows 常见的 GBK 源文件会直接抛解码错误，或（`write`）被整体转成 UTF-8。现在读文件时检测并保留原编码（UTF-8 → GB18030 → UTF-16 依次判定），写回时沿用原编码；仅当新内容含原代码页无法表示的字符时才降级为 UTF-8，且不会丢内容
- **CRLF/LF 被互相转换（编辑一行 → 全文件 diff）**：Windows 上 `Path.write_text` 会把 `\n` 翻成 `\r\n`，而模型习惯输出 `\n`。现在写入禁用平台换行转换、沿用文件原有换行风格；`edit` 在 LF 空间匹配（模型写的 `\n` 能命中 CRLF 文件里的文本），CRLF 文件编辑后仍是 CRLF；`diff` 比较前先归一化换行，纯换行差异不再渲染成"每一行都改了"。幂等写入返回"no change"且不改动字节
- **`task` 子 Agent 报告无上限（成本尖峰）**：8 个 worker 的输出会被原样拼进父上下文，单次可注入上万 token。现在合并报告按上述 1.2 万字符预算截断，完整报告落盘可回溯
- **`read` 读到超大文件**：>10 MB 直接拒绝并给出替代方案（`offset`/`limit` 或 `grep`），不再把整个生成文件塞进 prompt。
- **`glob` 静默截断**：超过 200 条时补上"还有 N 条未显示"的提示。

### Changed
- **三端文档与代码对齐**：README 特性表/目录树、USAGE §12 工具系统、技术规格文档 §3.3 与目录树全部由"11 个内置工具"更新为 12 个，并补 `GrepTool` 参数契约与 `search_tools.py` 说明；USAGE 新增「工具输出限额」表，用户能直接查到每类工具的上限与超限行为
- **版本号**：`pyproject.toml` 与 `zouwucode.__version__` 升至 `1.9.0`
- 修正 USAGE §12 的过时表述（原文称"当前版本没有交互式审批弹窗"，实际 Agent 模式三端均已接入审批回调）

### Tests
- 新增 `tests/test_v190_search_context.py`（23 用例）：grep 命中/过滤/忽略大小写/仅文件列表/非法正则/二进制跳过/结果上限/单文件目标/沙箱拒绝/无 ripgrep 兜底路径，read 分页与超大文件，glob 截断提示，bash 截断首尾保留，GBK 与 UTF-16 读写编辑保真，内置工具集含 grep
- `tests/test_v180_upgrade.py` 追加 CRLF 保真/LF 匹配/幂等写入/报告预算/头尾裁剪 5 用例。套件 **631 → 659 全绿**（有/无 config.yaml 两种环境均已验证）

## [1.8.0] — 2026-10-28

全网调研后的能力校准：对齐 DeepSeek V4/V4.1 API 最新契约、MCP 2026-07-28 规范与 2026 年编码 Agent 的 UX 共识。

### Fixed
- **推理强度 `low` 名不副实（真 bug）**：DeepSeek V4 默认开启思考，此前 `low` 只是"不传 reasoning_effort"，实际仍按 high/max 思考，温度参数也不生效。现在 `low` 显式发送 `{"thinking": {"type": "disabled"}}`，真正关闭思考并恢复 temperature 确定性输出
- **思考模式误发 temperature**：官方契约中思考模式忽略 temperature/top_p（传了不报错但无效）。现在 thinking 开启时不发送 temperature，避免误导
- **工具调用轮丢失 `reasoning_content`（真 bug）**：官方要求带工具调用的 assistant 消息把 reasoning_content 随工具结果回传，否则模型的思维链在工具轮之间断裂。现在按规范回传（仅工具轮；无工具轮不发送）
- **成本统计全面失真（真 bug）**：价格表停留在旧价——缓存命中价高约 5 倍、输出价低 2-4 倍、Pro 全错；且未区分峰谷。现按官方 2026-10 价目表重建（Flash 命中 $0.006/未命中 $0.30/输出 $1.20 每 1M，Pro $0.044/$1.32/$3.96），并实现峰谷折半（UTC 01-04、06-10 工作日为峰时）。`/usage` 与 `engine.max_cost_usd` 成本熔断从此可信
- **OpenAI 模型被错误应用峰谷折扣**：峰谷仅适用于 DeepSeek，OpenAI 为固定价——已在价格匹配中分离

### Added
- **AGENTS.md 自动加载**：项目根的 `AGENTS.md`（Codex/Cursor/Gemini/Copilot 共用的开放格式）现与 `.zouwucode/rules.md` 一并注入系统提示，`<project_rules>` 与 `<agents_md>` 分标签呈现；32 KiB 上限防止前缀膨胀
- **write/edit 输出 unified diff**：此前只回"写入了 N 字节 / 行数变化"，模型与用户都看不到实际改动。现在返回真实 unified diff（4k 字符截断），新建文件给出前 40 行预览——对齐 2026 年业界 "diff before write" 的 UX 共识
- **MCP 协议版本协商**：从硬编码 2025-03-26 升级为「优先声明 2026-07-28（无状态核心）→ 依次降级 2025-11-25 / 2025-03-26」，记录服务端实际协商版本；旧的有状态服务端（Mcp-Session-Id）继续兼容

### Changed
- **默认模型名 `deepseek-v4-flash` → `deepseek-flash`**：官方已将 V4 名称列为 legacy 别名（请求仍可用，路由到 V4.1-Flash 并按 Flash 计费）；代码默认值、`--model` 帮助文案、README/USAGE/config.example/技术规格示例同步更新
- 文档：工具数量/目录树/测试计数与代码对齐

### Tests
- 新增 `tests/test_v180_upgrade.py`（22 用例）：思考开关契约、reasoning_content 回传、峰谷定价、默认模型名、AGENTS.md、diff 输出
- `tests/test_mcp_http.py` 新增版本协商用例；`test_v105_parity.py` 成本用例改为固定峰谷时段断言并补 OpenAI 不打折用例。套件 **606 → 631 全绿**

## [1.7.2] — 2026-10-13

体检修复：文档与代码对齐 + 可观测性补漏（agent 自进化后"改了代码没同步文档"的典型偏差）。

### Fixed
- **文档命令错误**：README 与规格文档写 `zouwucode mcp-server`，实际子命令是 `zouwucode mcp-serve`（[__main__.py](zouwucode/__main__.py)），照文档执行会失败
- **工具数量三处不一致**：README 两处写"12 个内置工具"，实测 `create_builtin_tools` 返回 **11 个**；规格文档 3.3 表头与 USAGE 第 12 章补 `oracle` 工具（11 内置 + oracle 复核 + task 委派）
- **README 目录树缺 v1.1–v1.7 新增文件**：补 `sandbox/container.py`、`session/snapshot.py`、`tools/agent_tools.py`、`oracle_tool.py`、`_encoding.py`
- **USAGE 缺三个已发布特性的使用说明**：补 §13.3 容器隔离（v1.6.0）、§14.4 消息回退 `/rewind`（v1.0.8）、§12 `oracle` 工具行
- **9 处静默异常漏网**（v1.5.0"消灭静默吞异常"的遗漏，静态检查也漏了它们）：
  - `modules/manager.py` 三处模块派发（message/command/event）失败时静默跳过 → 现在记录模块名与可读原因（第三方模块故障从此可诊断）
  - `tui/textual_app.py` 四处 UI 回调容错（模块加载、工具摘要/思考/正文行渲染）`pass` → 改 `logger.debug`（UI 渲染故障不再无痕）
  - `context/transcript.py` 读转录文件失败、`session/manager.py` 读会话文件失败 → 记 warning/debug，带 `describe_exc` 可读原因
- **静态检查本身有盲区**：`test_v150_observability.py` 只匹配裸 `pass`（`pass  # 注释` 与 `continue` 均漏过）。强化为：正则匹配 pass/continue（含注释形式）、新增 `continue` 覆盖、排除三引号字符串体（python_exec 子进程 REPL driver 无 logger 且 stdout 即协议，跳过坏帧是唯一安全动作）；新增模块派发失败的日志行为测试

### Changed
- GitHub 仓库设置 11 个 topics（ai-agent / coding-agent / deepseek / mcp / tui 等），提升可发现性

### Tests
- 强化 `tests/test_v150_observability.py`（+1 用例：模块派发失败必须落日志；静态检查改为正则匹配 pass/continue 含注释形式并排除三引号字符串体），锁定上述 9 处静默点不再复发。套件 **605 → 606 全绿**

## [1.7.1] — 2026-10-13

体检修复：CI 红灯根治 + 文档/徽章同步。

### Fixed
- **`--model` / `--api-key` 在无 config.yaml 时被静默丢弃**（v1.0.4 起 CI 全矩阵红灯的根因）：TUI/CLI 的 override 守卫要求 provider 已存在于 `providers:` 字典，而全新安装（含 CI）该字典为空——override 不生效、测试断言失败。新增 `runtime.ensure_provider_config()` 统一引导（推断 api_type/model，与 `create_provider_for` 同源），TUI/CLI 两处 override 改为先引导再写入。这同时修复了真实用户场景：`zouwucode --tui --model xxx` 在未配置机器上此前无效
- 测试不再依赖仓库外的本地 `config.yaml`：新增无配置文件场景的回归用例（`test_overrides_land_without_config_file`）

### Docs
- README 测试徽章修正（文本与 URL 数字不一致且过时 → 605 passed）
- README 特性表补齐 v1.1–v1.7：容器沙箱、LSP hover/go-to-definition、MCP Streamable HTTP + `mcp-server` 模式、repo-map、模型路由/oracle、worktree 隔离、/share 与跨会话消息
- 技术规格文档同步：特性表新增上述条目、工具表补 oracle、测试计数 344 → 605、Bug 表补 CI 红灯条目

### Tests
- 新增 2 用例（605 全绿）：无 config.yaml 时 override 落地、`ensure_provider_config` openai/deepseek 推断

## [1.7.0] — 2026-10-13

LSP 加深：从"只有诊断"到"能看定义、能查类型"。

### Added — LSP hover + go-to-definition
- `lsp/client.py`：新增 `hover()`（某位置符号的类型/签名/文档）与 `definition()`（符号在哪里声明），
  带正确的 `Content-Length` 分帧、按 id 匹配响应、5s 超时、`file://` URI → 本地路径转换。
- 扩展层（`extensions.lsp_enabled: true`）新增两个工具：
  - `lsp_hover(file_path, line, column)` — 1-based 行/列；理解陌生代码再动手。
  - `lsp_definition(file_path, line, column)` — 定位符号声明处，找代码最快的方式。
- 原来只有 `check_diagnostics`；现共 3 个 LSP 工具（仍按需启用、缺服务器时优雅返回空）。

### Tests
- 新增 `tests/test_v170_lsp_hover_def.py`（+12）：hover 结果归一化、URI→路径、1-based↔0-based 转换、工具错误语义、扩展注册 3 工具。套件 **591 → 603 全绿**。

## [1.6.0] — 2026-10-13

容器沙箱（可选）：把"正则防呆"升级为**真隔离**。

### Added — 容器沙箱（`sandbox.container`，默认关）
- 新增 `zouwucode/sandbox/container.py`：`ContainerSandbox` 用一次性容器执行命令——
  **仅挂载工作区**（`-v <workspace>:/workspace`）、**默认断网**（`--network none`）、`--rm` 无残留。
- `ShellTool` 接线：开启且运行时可用时，命令在容器内执行；**运行时缺失/守护进程出错自动回退主机 shell**（并记日志、停用损坏运行时，不再每次重试）。
- 配置：`sandbox.container` / `container_runtime`（留空自动探测 docker|podman）/
  `container_image`（默认 `python:3.12-slim`）/ `container_network`（默认 false）/ `container_mount`。
- 语义：这是**隔离**补充，不替代既有黑名单——两者叠加（黑名单先拦，容器再兜底）。

### Tests
- 新增 `tests/test_v160_container_sandbox.py`（+13）：运行时探测、argv 隔离参数（挂载/断网/工作目录不泄漏主机路径）、路由到容器、非零退出、超时、运行时失败回退并停用、接线。套件 **578 → 591 全绿**。

## [1.5.0] — 2026-10-13

可观测性加固：消灭"静默失败"。

### Fixed — 23 处静默吞异常（`except Exception: pass`）
- 项目自审把"失败却像成功 / 吞掉异常"列为一类最危险的缺陷。全仓 **23 处** `except Exception: pass` 全部改为**记录后放行**（`logger.debug("swallowed ...")`），并为缺 logger 的 8 个模块补上模块级 logger。
- 涉及引擎主循环回调（`engine/loop.py` ×3）、压缩器、MCP/LSP 客户端、REPL 关闭、TUI/Web 回调等。
- 语义不变（仍是尽力而为、不抛出），差别只在**可观测**：出问题时日志能看到，而非凭空消失。

### Tests
- 新增 `tests/test_v150_observability.py`（+4）：静态守卫"**禁止** `except Exception: pass`"、"禁止裸 `except:`"、"每个吞异常点必须 `logger.debug`"，外加一条行为测试（注入失败确实进了日志）。套件 **574 → 578 全绿**。

## [1.4.0] — 2026-10-13

子代理 worktree 隔离：并行 worker 各改各的，互不打架。

### Added — 子代理 worktree 隔离（`subagent.worktree_isolation`，默认关）
- 开启后，每个子 Agent 在自己的 **git worktree**（`dev/sub-<id>` 分支）里运行，
  工具（read/write/edit/bash/git…）rooted 在该 worktree；并行 worker 改文件不再互相覆盖。
- `SubAgentManager._maybe_isolate()`：为 worker 建 worktree + 隔离沙箱/注册表/协调器；
  隔离注册表**天然不含 `task`**（无法再生成子代理）。
- **尽力而为**：非 git 工作区 / 缺 git 时自动回退到共享工作区，绝不阻断 worker。
- 结果可见：`agent.summary()` 与 `task` 工具输出都会带上 `↳ worktree: <path> (branch dev/sub-<id>)`，
  改动留在该 worktree 里供检查（不自动合并）。

### Tests
- 新增 `tests/test_v140_worktree_isolation.py`（+6，含真实 git worktree；无 git 自动跳过）。套件 **568 → 574 全绿**。

## [1.3.0] — 2026-10-13

会话共享 + 跨会话消息：从"单机单人"走向"能分享、能互相协作"。

### Added — `/share`（会话导出，本地优先）
- 新增 `zouwucode/share.py`：把会话渲染成**自包含 Markdown** 并写入 `<data_dir>/shares/`。
- **本地导出、不上传**（无账号、无网络）：可贴进 issue / PR / 聊天，或随代码提交。
- 三端命令：CLI/TUI `/share`；Web `POST /api/share` + `/share`。

### Added — 跨会话消息（本地 SQLite 总线）
- 新增 `zouwucode/messaging.py`：`MessageBus`（stdlib `sqlite3`）——**同一台机器**上多个
  ZOUWUCODE 会话（任意前端/终端）互通；靠数据库自身加锁协调，**无常驻进程、无端口**。
- `config.messaging`（默认开）：`enabled` / `name`（本会话地址，空 = 自动 `<workspace>-<short>`）。
- 命令：`/msg <to|*> <text>`（`*` 广播）、`/inbox`（读取并标记已读）、`/peers`（对端 + 未读数）。
- Web：`GET /api/inbox`、`GET /api/peers`、`POST /api/msg`。

### Fixed — `data_dir` 为空时数据泄漏到当前目录
- `config.load()`：配置文件 `data_dir: ""` 时 `Path("")` 等于 `"."`，会让会话/日志/导出/消息
  全落进当前目录而非 `zouwucode_data/`。现空值回落到默认数据目录。

### Tests
- 新增 `tests/test_v130_share_messaging.py`（+11）。套件 **557 → 568 全绿**。

## [1.2.0] — 2026-10-13

模型路由 + oracle 复核："便宜模型干活、更强模型复核"。

### Added — 模型路由
- 新增 `config.RouterConfig`（`router:` 段，默认关）。主会话仍是**单** provider（不改动 append-only 前缀缓存），但为两个角色另选 provider：
  - `worker_provider`：子 Agent（`task` 工具）用的模型 → 扇出工作跑在便宜模型上；
  - `oracle_provider`：只读"第二意见"模型。
- `runtime.create_provider_for(config, key)` 泛化 provider 工厂（可按**任意** provider 键构建）；新增 `runtime.ProviderRouter`（惰性构建 + 缓存，`worker_provider()` / `oracle_provider()`）。
- 新增 `oracle` 工具（`tools/oracle_tool.py`）：把问题（+可选上下文/diff）交给 oracle 模型求**只读**复核——无工具、不碰工作区；仅在 `router.enabled` 且有 oracle 时注册（默认工具集不变）。

### Added — `/oracle` 三端命令
- CLI/TUI `/oracle <问题>` 直接问 oracle；Web `POST /api/oracle` + `/oracle <问题>`。

### Tests
- 新增 `tests/test_v120_router_oracle.py`（+13：路由选择/缓存/回退、oracle 工具只读与错误语义、接线与三端命令）。套件 **544 → 557 全绿**。

## [1.1.0] — 2026-10-13

"一步到位"特性轮：把 ZOUWUCODE 从"自身很强"推到"能被别人用、也更省钱"。

### Added — repo-map（Aider 式代码地图）
- 新增 `zouwucode/repo_map.py`：`build_repo_map(root, max_tokens)` 输出**按被引用次数排序**的文件地图（只列 class/函数签名），用一个小 token 预算给模型全仓视角，**少探索轮次 = 直接省钱**。
- 新增 `repo_map` 工具（`tools/repo_map_tool.py`），**始终可用**（模型按需调用，零成本）；配置 `context.repo_map_enabled=true` 时还会自动注入系统提示（默认关）。
- 忽略 `node_modules`/`__pycache__`/`.venv` 等噪声目录。

### Added — 成本透视图 `/usage`
- `engine.get_usage_summary()`：请求数、prompt/cached/output tokens、缓存命中率、**已花费、缓存省下的钱、省了多少百分比**。
- 三端命令：CLI/TUI `/usage`，Web `GET /api/usage` + `/usage`。DeepSeek 缓存命中便宜数倍——把"省了多少"显式呈现，才让"经济实惠"看得见。

### Added — MCP server 模式
- 新增 `zouwucode/mcp/server.py`：`zouwucode mcp-serve` 以 stdio JSON-RPC 暴露**全部内置工具**（initialize/tools/list/tools/call/ping），供 Claude Code / Goose / Cursor 等 MCP 客户端消费。此前 ZOUWUCODE 只是 MCP *客户端*，现在**双向**。

### Tests
- 新增 `tests/test_v110_repomap_usage.py`（+11）与 `tests/test_mcp_server.py`（+5，含真实子进程 MCP 往返）。套件 **528 → 544 全绿**。

## [1.0.9] — 2026-10-13

引擎压缩正确性 + 统一令牌估算 + TUI 正文流式（"经济实惠 + 正确"的硬核一轮）。

### Fixed — 压缩器一组真 bug
- **丢弃 tool 交互**（D10）：`_balanced` / `_lossless` 会丢掉「空 content 的 assistant 消息」——而这正是 `tool_calls` 的载体，导致配对的工具结果变成孤儿。现保留带 `tool_calls` 的消息。
- **会话中插入 system 消息**（D9）：`_aggressive` 用 `role:"system"` 摘要，多数 provider 要求 system 在最前，会拒绝/忽略。改为 `user` 角色的 `[Compressed earlier history]` 便签。
- **文件内容压缩丢全部信息**（D11）：原来 >20 行直接替换成 `"[File Content] N lines (compressed)"`。现保留**前 8 行 + 后 6 行 + 省略行数**，模型仍能看到文件形态。
- **会话中注入 system**（D12）：项目记忆此前作为**会话中的 system 消息**注入（违反 system-first）。改为 `user` 便签（CLI + TUI 两端）。

### Fixed — 统一令牌估算（D13）
- 新增 `zouwucode/tokens.py`：`estimate_tokens`（CJK≈1/字、拉丁≈4 字符/token）+ `estimate_messages`（含 `tool_calls`）。
- 四处旧的 `len(content)//2`（对英文**低估约 2×**）全部换用：`engine/cache.py`、`engine/loop.py`（压缩触发）、`context/compressor.py`、`context/sliding_window.py`。此前会**压缩触发过晚、预算告警失真、超大提示有 400 风险**。

### Added — TUI 正文流式
- TUI 此前只有思考流式、正文最后一次性蹦出。现 `engine.on_content_delta` 驱动**逐行实时渲染**助手正文（`_on_content_delta` / `_flush_content_line`），`_content_started` 守卫避免重复渲染。**至此三端正文流式全部具备**。

### Tests
- 新增 `tests/test_v109_compression.py`（+13：估算器精度/接线、保留 tool_calls、无中途 system、文件摘要保留首尾、D12、TUI 流式）。套件 **515 → 528 全绿**。

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

## [1.0.1] — 2026-10-12

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

[1.10.1]: https://github.com/muzizouwu/zouwucode/releases/tag/v1.10.1
[1.10.0]: https://github.com/muzizouwu/zouwucode/releases/tag/v1.10.0
[1.9.1]: https://github.com/muzizouwu/zouwucode/releases/tag/v1.9.1
[1.9.0]: https://github.com/muzizouwu/zouwucode/releases/tag/v1.9.0
[1.8.0]: https://github.com/muzizouwu/zouwucode/releases/tag/v1.8.0
[1.7.2]: https://github.com/muzizouwu/zouwucode/releases/tag/v1.7.2
[1.7.1]: https://github.com/muzizouwu/zouwucode/releases/tag/v1.7.1
[1.7.0]: https://github.com/muzizouwu/zouwucode/releases/tag/v1.7.0
[1.6.0]: https://github.com/muzizouwu/zouwucode/releases/tag/v1.6.0
[1.5.0]: https://github.com/muzizouwu/zouwucode/releases/tag/v1.5.0
[1.4.0]: https://github.com/muzizouwu/zouwucode/releases/tag/v1.4.0
[1.3.0]: https://github.com/muzizouwu/zouwucode/releases/tag/v1.3.0
[1.2.0]: https://github.com/muzizouwu/zouwucode/releases/tag/v1.2.0
[1.1.0]: https://github.com/muzizouwu/zouwucode/releases/tag/v1.1.0
[1.0.9]: https://github.com/muzizouwu/zouwucode/releases/tag/v1.0.9
[1.0.8]: https://github.com/muzizouwu/zouwucode/releases/tag/v1.0.8
[1.0.7]: https://github.com/muzizouwu/zouwucode/releases/tag/v1.0.7
[1.0.6]: https://github.com/muzizouwu/zouwucode/releases/tag/v1.0.6
[1.0.5]: https://github.com/muzizouwu/zouwucode/releases/tag/v1.0.5
[1.0.4]: https://github.com/muzizouwu/zouwucode/releases/tag/v1.0.4
[1.0.2]: https://github.com/muzizouwu/zouwucode/releases/tag/v1.0.2
[1.0.1]: https://github.com/muzizouwu/zouwucode/compare/v1.0.0...v1.0.2
[1.0.0]: https://github.com/muzizouwu/zouwucode/releases/tag/v1.0.0
