# Devin 式 dev 模式使用说明

`zouwucode dev` 把 ZOUWUCODE 从"交互式结对编程"升级为"**自主异步软件工程师**"——
对标 Devin / OpenHands 的核心工作流：给定一个 GitHub issue，AI 独立完成
实现 → 验证 → 提交 Draft PR，人类只做最终 review。

## 一、工作流总览

```
zouwucode dev <issue-url | owner/repo#N | 任务描述>
   │
   ├─ 1. resolve_task      解析任务（GitHub API 拉取 issue 标题+正文）
   ├─ 2. worktree create   git worktree add dev/issue-N（隔离检出）
   ├─ 3. agent session     自主编码会话（yolo 模式，沙箱限定在 worktree 内）
   │                        · 边界自检清单（空值/错误路径/资源/并发/跨平台）
   │                        · 自适应成本预算（触顶自动升级续跑，而非直接失败）
   ├─ 4. 多层验证          lint → typecheck → test(+覆盖率) → security
   │      任一层失败 → 分层标注回灌同一会话 → 修复 → 重验（≤ verify_retries 轮）
   ├─ 5. 独立 AI 审查      全新只读会话审查 diff（与实现者零共享上下文）
   │      request_changes → 回灌修复 → 复审（≤ review_max_rounds 轮）
   ├─ 6. commit + push     提交并推送 dev/* 分支
   ├─ 7. Draft PR          创建关联 issue 的 Draft PR（"Closes #N"，含审查结论）
   └─ 8. CI 联动           轮询真实 GitHub Checks，结果回写 PR（不阻塞，仅提示）
          失败 → 不建 PR，把失败详情回帖到 issue 请求人工介入
```

**人工介入点只有一个**：review Draft PR 后手动合并。AI 永远不直接碰 main。

## 二、命令参考

| 命令 | 说明 |
|------|------|
| `zouwucode dev <url>` | 前台执行单个任务（最常用） |
| `zouwucode dev <text>` | 自由文本任务：本地 worktree+分支，不建 PR |
| `zouwucode dev --queue <url>` | 提交任务到 SQLite 队列（异步） |
| `zouwucode dev --workers N` | 启动 N 个 worker 子进程排空队列 |
| `zouwucode dev --watch owner/repo` | 轮询带 `zouwucode:do` 标签的 issue，自动认领执行 |
| `zouwucode dev --status` | 查看队列统计与最近任务 |

## 三、前置条件

1. **GitHub token**（issue→PR 流程必需；本地任务不需要）：
   ```powershell
   $env:GITHUB_TOKEN = "ghp_xxx"        # 或 config.yaml github.token
   ```
   权限最小化：fine-grained token 只授予目标仓库的 `Contents: Read and write`
   + `Pull requests: Read and write` + `Issues: Read and write`。
2. **仓库是 git repo 且有 origin 远程**（watch 模式从 origin 自动解析 owner/repo）。
3. **建议在 config.yaml 设置成本熔断**：`engine.max_cost_usd: 2.0`——
   防止复杂任务失控烧掉 API 额度。

## 四、并行与异步托管（Devin 式清 backlog）

```bash
# 早上：把一批 issue 排队
zouwucode dev --queue https://github.com/you/repo/issues/42
zouwucode dev --queue https://github.com/you/repo/issues/43

# 3 个 worker 并行消化（各自独立 worktree，互不冲突）
zouwucode dev --workers 3
```

或全自动：在 GitHub 上给 issue 打 `zouwucode:do` 标签，然后：

```bash
zouwucode dev --watch you/repo --interval 60
```

watch 每 60 秒轮询一次，认领新标签 issue → 入队 → 起 worker → 完成后移除标签。

## 五、质量门禁（对标成熟 agent 的核心）

针对"编程 agent 容易忽略边界、约束过多又降性能、自主验收的代码上线仍出问题"三大痛点，
dev 模式用**多层独立验证**取代"自写自测自验"的单层循环：

| 门禁 | 解决什么 | 机制 |
|------|----------|------|
| 边界自检清单 | agent 忽略边界 | 实现者提示词内置自检（空值/None/极值、错误路径、资源释放、并发、Windows/POSIX 跨平台、向后兼容、安全） |
| 多层验证管线 | 单层测试覆盖不到静态缺陷 | `lint → typecheck → test(+覆盖率) → security`，逐层自动探测，未装工具则该层跳过（不误伤），失败按层标注回灌 |
| 独立 AI 审查 | 自写自测的系统性偏差 | 全新只读会话、零共享上下文，只看 diff 按审查清单判定 approve/request_changes；结论写入 PR |
| CI 联动 | 本地验收 ≠ 生产环境 | push 后轮询真实 GitHub Checks（多 OS/Python 矩阵），结果回写 PR；本地过但 CI 挂会明确提示 |
| 自适应预算 | 约束过死掐死复杂任务 / 过松烧钱 | 成本触顶自动升级预算（×2，有上限）续跑同一会话，而非直接失败；简单任务不受影响 |

**关键设计原则**：约束放在**认知层**（自检清单、分层反馈）而非堆砌硬性禁令——
信息化的反馈比 blanket 限制更能保持模型性能，真正的"硬门禁"交给下游的
多层验证 + 独立审查 + CI，而不是靠提示词里一堆"禁止"。

## 六、安全边界（设计即如此，不可绕过）

| 防线 | 机制 |
|------|------|
| 分支隔离 | `validate_branch`：只允许 `dev/*`；main/master/develop 等硬拒绝；禁 force push 到保护分支 |
| 工作区隔离 | 每个任务独立 `git worktree`，你的主检出永不被触碰 |
| 文件沙箱 | agent 的 read/write/edit 路径白名单限定在 worktree 内（构建引擎前 chdir，沙箱根=worktree） |
| 审查只读 | 独立审查会话引擎跑 plan 模式 + 只读工具白名单（read/ls/glob/git），无法改动被审代码 |
| PR 审批 | PR 永远是 **Draft**，`Closes #N` 只在合并时生效——人不做最终确认，代码进不了主干 |
| 失控保护 | 引擎全部安全限制生效：轮数上限、任务超时、连续工具错误熔断、成本熔断（可自适应升级） |
| 失败透明 | 验证/审查/CI 任一异常 → 自动回帖 issue（含分层日志），不静默吞掉 |

## 七、配置项（config.yaml）

```yaml
github:
  token: ""                          # 推荐用 GITHUB_TOKEN 环境变量
  api_base: "https://api.github.com"

dev:
  branch_prefix: dev                 # 分支白名单前缀
  worktree_dir: ".zouwucode_worktrees"
  test_command: ""                   # 空=自动探测（pytest / npm test）
  verify_retries: 2                  # 验证失败自动迭代修复次数
  max_concurrent_tasks: 3            # 队列并行上限
  task_timeout_seconds: 3600         # 单任务超时
  watch_label: "zouwucode:do"        # watch 认领标签
  draft_pr: true                     # 保持 true！false 会创建非 Draft PR
  # 多层验证（未探测到的层自动跳过）
  lint_command: ""                   # 空=自动探测 ruff/eslint
  typecheck_command: ""              # 空=自动探测 mypy/tsc
  security_command: ""               # 空=自动探测 bandit（需 [tool.bandit]）
  coverage_min: 0.0                  # >0 时 pytest 加 --cov-fail-under
  # 独立审查 + CI 联动 + 自适应预算
  review_enabled: true
  review_max_rounds: 1
  ci_check_enabled: true
  adaptive_budget: true
  task_cost_budget_usd: 0.0          # 0=沿用 engine.max_cost_usd
  budget_escalations: 1

engine:
  max_cost_usd: 2.0                  # dev 模式强烈建议设置
```

## 八、经验沉淀

成功的任务会把「实现摘要 + 验证轮数 + 改动文件数」写入项目记忆
（`.zouwucode/memory/decisions.json`），后续同类任务可复用上下文。

## 九、已知限制

- 验证层依赖工具已安装：未装 ruff/mypy/bandit 时对应层跳过，不会误伤也不会假装通过
- 独立审查以 diff 为准（plan 模式不执行工具），超大 diff 会被截断到 60k 字符
- watch 模式按标签认领，不做依赖排序（关联 issue 建议合并提交）
- 单任务成本统计基于引擎累计估算，跨重试/升级可能略有偏差

## 十、代码位置

| 模块 | 文件 |
|------|------|
| GitHub API 客户端 | `zouwucode/dev/github.py` |
| worktree 隔离 | `zouwucode/dev/workspace.py` |
| 端到端管线 | `zouwucode/dev/pipeline.py` |
| 多层验证管线 | `zouwucode/dev/verifiers.py` |
| 独立 AI 审查 | `zouwucode/dev/reviewer.py` |
| SQLite 任务队列 | `zouwucode/dev/queue.py` |
| CLI 路由/worker/watch | `zouwucode/dev/cli.py` |
| 测试 | `tests/test_dev.py`（44 用例） |
