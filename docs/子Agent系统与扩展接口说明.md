# 子 Agent 系统与扩展接口说明

## 一、子 Agent 系统

### 1.1 设计原则

主 Agent（引擎）在任务执行中可将工作分解为若干子任务，委派给**并行运行的
子 Agent**。核心设计约束与决策：

| 决策 | 说明 |
|------|------|
| **每子 Agent 独立引擎** | 每个 SubAgent 拥有私有的 EngineLoop（独立 PrefixCache、缓存统计、中断状态），共享 LLM provider 与工具注册中心。单共享引擎的并发会交错污染 append-only 前缀，被明确否决。 |
| **工具白名单** | 创建时可限制子 Agent 只能调用指定工具（如只读探索型 Agent 只给 `read`/`ls`），白名单外的调用会收到明确的错误回执。 |
| **级联打断** | 主引擎被打断（Esc / Ctrl+C）时，通过引擎的 `on_interrupt` 钩子自动停止全部运行中的子 Agent。 |
| **独立超时与故障隔离** | 每个子 Agent 有独立超时；一个子 Agent 失败/超时不影响兄弟任务，调用方收到聚合报告。 |

### 1.2 架构图

```
┌─────────────────────── 主 Agent ───────────────────────┐
│  EngineLoop（主 PrefixCache / 中断状态）                │
│        │ task 工具调用（任务分解）                       │
│        ▼                                               │
│  SubAgentManager ──► 并发 create / run / wait          │
│        │ on_interrupt 级联钩子 ◄── 主引擎 request_interrupt │
└────────┼───────────────────────────────────────────────┘
         │ 1:N
   ┌─────┴──────────┬──────────────┐
   ▼                ▼              ▼
 SubAgent-1      SubAgent-2     SubAgent-N
 私有 EngineLoop  私有 EngineLoop  私有 EngineLoop
 工具白名单 ✅     工具白名单 ✅    工具白名单 ✅
   │                │              │
   └──── 共享 provider + 工具注册中心 + 沙箱 ────┘
```

### 1.3 使用方式（模型视角）

主模型通过内置的 **`task` 工具** 进行任务分解与委派：

```json
{
  "name": "task",
  "arguments": {
    "tasks": [
      {"role": "researcher", "instructions": "你是调研专家…", "task": "调研 X 的实现方案"},
      {"role": "reviewer", "instructions": "你是评审专家…", "task": "评审 config.py 的改动",
       "tools": ["read", "ls"]}
    ]
  }
}
```

执行完成后返回所有子 Agent 的报告聚合，主模型据此继续推理。

### 1.4 使用方式（用户视角）

- 无需手动操作——主 Agent 会在合适时机自行分解委派
- `/agents` 命令（CLI/TUI 均可用）查看子 Agent 列表、状态、耗时、错误
- 打断主任务（Esc / Ctrl+C）会同时停止所有子 Agent

### 1.5 配置

```yaml
subagent:
  max_agents: 8            # 并行子 Agent 数量上限
  default_timeout: 600     # 单个子 Agent 默认超时（秒）
```

### 1.6 编程接口（开发者）

```python
from zouwucode.agent.subagent import SubAgentManager, AgentStatus

manager = SubAgentManager(config, provider, coordinator)
manager.bind_main_engine(engine)          # 级联打断

agents = await manager.run_parallel([
    {"role": "worker", "instructions": "...", "task": "..."},
    {"role": "reader", "instructions": "...", "task": "...",
     "tools": ["read", "ls"]},            # 工具白名单
])
for agent in agents:
    print(agent.id, agent.status, agent.result.content)

manager.status_summary()                  # /agents 命令的数据源
```

## 二、扩展层（MCP / LSP 预留接口）

### 2.1 设计

`zouwucode/extensions/` 提供统一的扩展点，**当前默认完全不激活**（零运行时
开销），但集成空间已全部打通——未来接入只需配置或注册，无需改动引擎与 UI 层。

```
ExtensionHost（生命周期 + 工具聚合）
├── McpExtension   ← zouwucode/mcp/client.py（已实现 stdio 连接与工具桥接）
└── LspExtension   ← zouwucode/lsp/client.py（诊断工具 check_diagnostics）
```

| 组件 | 职责 | 状态 |
|------|------|------|
| `Extension`（ABC） | 扩展基类：`start(ctx)` / `stop()` / `get_tools()` / `get_dynamic_schemas()` | 已实现 |
| `ExtensionHost` | 注册、启动/停止、工具合并进 ToolRegistry、状态汇总 | 已实现 |
| `McpExtension` | 按 `extensions.mcp_servers` 配置连接 MCP 服务器，把服务器工具包装为标准 `MCPTool` 混入注册中心 | 已实现，未配置时 inactive |
| `LspExtension` | 启用后贡献 `check_diagnostics` 工具（封装 LSPClient.get_diagnostics） | 已实现，默认关闭 |

### 2.2 启用 MCP（示例）

```yaml
extensions:
  mcp_servers:
    - name: filesystem
      command: npx
      args: ["-y", "@modelcontextprotocol/server-filesystem", "."]
```

配置后启动时自动连接，服务器工具以 `filesystem__read_file` 形式进入
模型可用工具列表。

### 2.3 启用 LSP（示例）

```yaml
extensions:
  lsp_enabled: true
```

启用后模型可调用 `check_diagnostics` 获取语言服务器诊断（需对应语言
服务器在 PATH 中，如 pyright）。

### 2.4 自定义扩展

```python
from zouwucode.extensions import Extension, ExtensionContext

class MyExtension(Extension):
    name = "my-ext"

    async def start(self, ctx: ExtensionContext): ...
    async def stop(self): ...
    def get_tools(self): return [MyTool()]     # 可选：贡献工具

host.register(MyExtension())
```

## 三、相关代码索引

| 模块 | 路径 |
|------|------|
| 子 Agent / 管理器 | `zouwucode/agent/subagent.py` |
| task 委派工具 | `zouwucode/tools/agent_tools.py` |
| 扩展层 | `zouwucode/extensions/`（host / mcp_ext / lsp_ext） |
| 引擎级联钩子 | `zouwucode/engine/loop.py`（`on_interrupt`） |
| 测试 | `tests/test_subagent.py`（20 个用例） |
