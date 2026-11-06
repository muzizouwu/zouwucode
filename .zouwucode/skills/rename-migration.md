# 模块重命名与迁移指南

> 本 skill 记录将独立功能包（如复刻的 oh-my-opencode）重命名并迁移为 ZOUWUCODE 扩展模块（hello-my-zouwucode）的完整经验，适用于"换个名字接入主体"类的重构任务。

## 一、重命名映射表（统一改名，不留旧名）

| 维度 | 旧名 | 新名 |
|---|---|---|
| 顶层包目录 | `zouwucode/omo/` | `hello_my_zouwucode/`（项目根） |
| 配置段 | `omo:` | `hello_my_zouwucode:` |
| 配置类 | `OmOConfig` | `HelloMyZouwucodeConfig` |
| 命令前缀 | `/ultrawork` 等 | `/hello-*`（`/hello-ultrawork`、`/hello-plan`、`/hello-start-work`、`/hello-status`、`/hello-agents`、`/hello-categories`） |
| 运行时目录 | `.omo/` | `.hello-my-zouwucode/` |
| 测试文件 | `tests/test_omo.py` | `tests/test_hello_my_zouwucode.py` |

**命名一致性原则**：包名（下划线）、配置段/命令（连字符）、目录名（点开头）三套写法互相对应，文档、测试、CLI 帮助、README 一处不漏。

## 二、独立顶层包的导入改造

包迁到顶层后**所有 `from ..` 相对导入失效**，必须改为绝对导入：

```python
# 迁移前（作为 omo 子模块）
from ..config import ZOUWUCODEConfig
# 迁移后（顶层包）
from zouwucode.config import ZOUWUCODEConfig
```

检查点：`orchestrator.py`、`planner.py`、`atlas.py`、`intent_gate.py` 等文件内的 `from ..engine/...`、`from ..agent/...`。

## 三、宿主代码同步清理

1. `config.py`：删旧配置类、加新配置类（含 `enabled` 总开关）。
2. 三端（CLI/TUI/Web）：删掉对旧包的直接引用（`self.orchestrator`、`..omo.notepad` 等），改为 `ModuleManager` 派发；`/plan` 带参 → 模块 `dispatch_command("/hello-plan", arg)`，无参保留 `set_mode("plan")` 语义。
3. 命名不一致的隐藏引用：模块内部 `self.config.omo.xxx`、状态目录常量、事件类型字符串、文档中的命令名。

## 四、验证清单（迁移后必跑）

1. `python -c "from hello_my_zouwucode import SisyphusOrchestrator, AGENTS, detect_ultrawork"` —— 顶层包可独立导入。
2. 三端 `/hello-*` 命令均能派发到模块；`config.hello_my_zouwucode.enabled=false` 时三端不加载、主体正常。
3. 全量测试迁移：`test_omo.py` 的导入/字段全部改为新名，新增模块加载测试。
4. grep 全仓确认无 `omo` 残留（`Grep pattern "omo"`，注意别误伤 `oh-my-opencode` 等文档专有名词）。

## 五、教训

- 该目录可能不是 git 仓库（`git rev-parse` 返回 128），移动目录用 `Move-Item` 而非 git mv。
- "全面重命名"要一次到位：配置、命令、目录、测试、文档同步改，避免半旧半新。
- 模块化改造后三端命令分支若遗留对已删方法的调用，会抛 AttributeError（如 `set_mode` vs `action_set_mode`），迁移后务必跑三端回归。
