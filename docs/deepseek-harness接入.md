# 接入 DeepSeek Harness（dsh）

> 适用版本：ZOUWUCODE v1.10.0+ ｜ 官方 Harness 处于 developer preview（SDK 仍为 0.1.x 预发布）

## 1. 这是什么

DeepSeek Harness（命令 `dsh`）是 DeepSeek 开源的 Agent Harness，基于 [Cordis](https://github.com/cordiverse/cordis) 插件内核，设计哲学是 **"everything is a plugin"**：

- **Agent = Model + Harness**：模型负责推理，Harness 负责让 Agent 在真实环境里干活（工具、会话、沙箱、上下文、调度）。
- 模型、工具、技能、会话、沙箱、存储、循环、UI **全部是可插拔插件**，通过配置组合，不需要改源码。
- 每一次运行都写入 **append-only 会话日志**，Trajectory 视图可按来源检查，resume / fork / search / replay 都基于同一条事件流。
- 内置多种运行模式：**Standard**（完整工具集）、**Code**（模型用代码编排多步工具调用）、**Minimal**（只有 shell + 文件编辑，用于最小环境评测）、**Creator**（运行时内省 + 插件试验 + 预设编排）。

ZOUWUCODE 有两处可以跟它对接，方向相反：

| 方向 | 谁在执行 | 用到的能力 | 适用场景 |
|------|----------|-----------|----------|
| **A. zouwucode → dsh** | dsh 执行任务 | 官方 Python SDK（`deepseek-harness-sdk`） | 想用官方 harness 的插件组合/轨迹能力跑一个任务 |
| **B. dsh → zouwucode 工具** | dsh 执行任务，调用 zouwucode 的工具 | `zouwucode mcp-serve`（MCP stdio 服务端） | 想在自己的 dsh 组合里复用 zouwucode 的工具与沙箱 |

ZOUWUCODE 自己的引擎（Cache-First loop + 自有工具集）**完全不受影响**，两套运行时可以并存。

---

## 2. 方向 A：从 zouwucode 调官方 Harness

### 2.1 安装

```bash
python -m pip install deepseek-harness-sdk
```

- 该包会同时安装**当前平台对应的 `dsh` 运行时 wheel**（`deepseek-harness-runtime-bin`），**不需要系统 Node.js**。
- SDK 是可选依赖：不安装时 zouwucode 其余功能零影响，`zouwucode harness` 会给出可操作的安装提示而不是 traceback。

### 2.2 前置检查

```bash
zouwucode harness --doctor
```

输出示例：

```
DeepSeek Harness preflight
  sdk        : installed
  dsh_home   : G:\you\zouwucode_data\dsh_home (exists=False)
  workspace  : G:\you\project
  profile    : sdk
  provider   : deepseek-official
  model      : deepseek-flash
  reasoning  : max
  max_tokens : 49152
  timeout    : unbounded
```

`--doctor` 退出码：`0` 可运行，`2` 缺依赖或配置不可映射。

### 2.3 跑一个任务

```bash
# 最简单
zouwucode harness "把 tests/ 下的失败用例修好"

# 会话续接（复用同一 session id，dsh 会继续上次的持久对话）
zouwucode harness --session demo-1 "继续上一次的重构"

# 从管道读任务
echo "跑一遍测试并总结失败原因" | zouwucode harness

# JSON 输出（便于脚本/CI 消费）
zouwucode harness --json --model deepseek-v4-flash "解释这个仓库的结构"

# 实时看子 Agent / 事件通知
zouwucode harness --verbose "调研并对比两个实现方案"

# 换成最小评测 profile
zouwucode harness --profile sdk-minimal "在最小工具集下完成改动"
```

### 2.4 参数

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `prompt`（位置参数） | 要执行的任务；省略时从 stdin 读取 | — |
| `--cwd` | Agent 工作区 | 当前目录 |
| `--dsh-home` | DSH_HOME 目录（dsh **不会**自动发现 `~/.dsh`，必须显式给） | `<data_dir>/dsh_home` |
| `--profile` | dsh profile：`sdk` / `sdk-minimal` | `sdk` |
| `--provider` | dsh 组合中已注册的 provider 路由 | 由 `api_type` 映射（DeepSeek → `deepseek-official`） |
| `--model` | 模型 id（由 dsh 侧适配器解析） | provider 配置的 model，否则 `deepseek-v4-flash` |
| `--reasoning` | 推理强度 `low`/`medium`/`max` | zouwucode `reasoning_intensity` |
| `--max-tokens` | 单次请求输出上限 | `49152` |
| `--timeout` | 单轮请求超时秒数（`0` = 不设限） | `0` |
| `--patch` | 追加 Cordis patch 文件，可多次指定 | 无 |
| `--session` | 会话 id | 新会话 |
| `--json` / `--verbose` / `--doctor` | 见上 | 关闭 |

**退出码**：`0` 成功；`1` 运行时失败（dsh 以 `error` 结束、启动失败）；`2` 前置条件不满足（缺 SDK、缺 prompt、provider 无法映射）。

### 2.5 配置文件

```yaml
# config.yaml
harness:
  enabled: false              # 开关型语义：true 表示默认用 harness 语义运行（当前仍需显式子命令）
  profile: sdk                # sdk | sdk-minimal
  dsh_home: ""                # 空 = <data_dir>/dsh_home
  provider: ""                # 空 = 按 api_type 映射
  model: ""                   # 空 = provider.model → deepseek-v4-flash
  reasoning_effort: ""        # 空 = reasoning_intensity
  max_tokens: 49152
  initialize_timeout_seconds: 30
  request_timeout_seconds: 0  # 0 = 不设限
  patches: []                 # Cordis patch 文件列表（按顺序叠加）
```

### 2.6 用 patch 挂载自定义能力

dsh 的能力全在插件里，用 patch 层可以在**不改官方源码**的前提下替换/追加能力：

```bash
# 初始化 SDK profile（首次）
export DSH_HOME=/abs/path/dsh_home
dsh --profile sdk --dump-default-config >/dev/null

# 安装本地插件包
dsh plugin --profile sdk add file:/abs/path/my-plugin-bundle
```

```bash
# 单次调用叠加 patch 层（会转发给 dsh，排在 profile/home 层之后）
zouwucode harness --patch ./my.patch.yml "用自定义工具链完成任务"
```

> `--provider` 必须指向该组合里**真实注册**的路由。若要用非 DeepSeek 的 provider，需要自己在组合里挂载对应插件（如 `llm-pi-ai` 并配置凭据），这是 dsh 侧的配置工作，不是 zouwucode 的开关。

---

## 3. 方向 B：把 zouwucode 的工具挂进 dsh

`zouwucode mcp-serve` 已经能把**全部内置工具**（read/write/edit/ls/glob/grep/bash/git/web_search/web_fetch/repo_map/python_exec 及扩展工具）通过 MCP stdio 暴露给任意 MCP 客户端：

```bash
zouwucode mcp-serve            # stdio JSON-RPC：initialize / tools/list / tools/call / ping
```

dsh 侧按官方文档把它注册为一个 MCP 服务器插件（profile 配置或 patch 层），即可让 dsh 的 Agent 直接使用 zouwucode 的工具实现与沙箱策略。

> 说明：dsh 仍在 preview，**其 MCP 插件的具体配置键随版本演进**，请以官方 developer docs 为准（<https://deepseek-harness.github.io/deepseek-harness/en/guide/quickstart>）。zouwucode 这一侧只保证 MCP stdio 协议稳定。

---

## 4. 对标映射（两套运行时怎么对应）

| 概念 | ZOUWUCODE | DeepSeek Harness（dsh） |
|------|-----------|------------------------|
| 扩展模型 | `modules/` + `extensions/`（MCP / LSP / hooks） | Cordis 插件：模型、工具、技能、会话、沙箱、存储、循环、UI 均为插件 |
| 运行模式 | Plan / Agent / YOLO；`python_exec` 提供 CodeAct 行动面 | Standard / Code / Minimal / Creator |
| 会话记录 | PrefixCache（append-only 前缀）+ `transcripts/` 全文日志 | append-only 会话日志；Trajectory 视图可 resume/fork/search/replay |
| 上下文治理 | 三级压缩 + 三层上下文窗口 + 工具输出限额 | 由 compaction 插件管理（独立 token 上限） |
| 子 Agent | `task` 工具并行委派，独立引擎/缓存，可选 worktree 隔离 | 子 Agent 调度事件写入同一条轨迹，SDK 可收通知 |
| 沙箱 | 危险命令拦截 + 路径白名单 + 可选容器隔离 | sandbox 插件（由所选组合决定） |
| 评测 | `zouwucode eval`（真实 agent 栈 + 确定性断言） | Minimal profile 用于最小环境基准；轨迹可回放 |

两者不是替代关系：zouwucode 是**自带引擎与工具的终端 Agent**，dsh 是**可组合的 Harness 平台**。需要官方插件生态或官方轨迹/回放能力时走 dsh；需要稳定的终端编码 Agent + 自有成本/缓存策略时走 zouwucode 引擎。

---

## 5. 故障排查

| 现象 | 原因 | 处理 |
|------|------|------|
| `DeepSeek Harness SDK 未安装` | 未安装可选依赖 | `python -m pip install deepseek-harness-sdk` |
| `SDK 版本与当前参数不兼容` | SDK 预发布接口变动 | 升级 SDK；或去掉 `--timeout/--patch` 等新参数 |
| `DeepSeek Harness 启动失败` | DSH_HOME 配置/组合损坏 | 用 `dsh --profile <p> --dump-default-config` 自检；换一个干净的 `--dsh-home` |
| `provider ... api_type='openai' 无法直接映射` | dsh 默认组合只注册 `deepseek-official` | 用 `--provider` 指定组合中已注册的路由，或挂载自定义 provider 插件 |
| 初始化被拒（model/effort 不支持） | dsh 适配器不认识该模型/推理档 | 用 `--doctor` 确认；换成适配器支持的 model / effort |
| 单轮长时间无响应 | SDK 默认不限制单轮时长 | 显式 `--timeout 600` |

日志：zouwucode 侧写 `<data_dir>/logs/zouwucode.log`；dsh 侧日志由其 profile 目录（`$DSH_HOME/profiles/<profile>/`）管理。

---

## 6. 边界与诚实说明

- 官方 Harness 处于 **developer preview**：核心插件与 API 会继续演进，`zouwucode harness` 只承诺"配置映射 + 结果映射 + 友好失败"，不承诺锁定 dsh 内部结构。
- `harness.enabled` 目前是**声明式开关**：真正的行为入口是显式子命令 `zouwucode harness`，不会因为置 true 就悄悄替换主引擎。
- dsh 自己管理它的工具、沙箱与凭据；zouwucode 的沙箱策略**不作用于** dsh 会话。若两者都要严格隔离，请分别配置。
- 成本与缓存：zouwucode 的 `/usage` 与 `engine.max_cost_usd` 统计的是**自有引擎**的请求；dsh 会话的消耗由 dsh / DeepSeek 平台侧计费，不并入 zouwucode 的成本熔断。
