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
   ├─ 4. verification      跑测试（自动探测 pytest / npm test）
   │      失败 → 测试输出回灌同一会话 → 修复 → 重测（≤ verify_retries 轮）
   ├─ 5. commit + push     提交并推送 dev/* 分支
   └─ 6. Draft PR          创建关联 issue 的 Draft PR（"Closes #N"）
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

## 五、安全边界（设计即如此，不可绕过）

| 防线 | 机制 |
|------|------|
| 分支隔离 | `validate_branch`：只允许 `dev/*`；main/master/develop 等硬拒绝；禁 force push 到保护分支 |
| 工作区隔离 | 每个任务独立 `git worktree`，你的主检出永不被触碰 |
| 文件沙箱 | agent 的 read/write/edit 路径白名单限定在 worktree 内（构建引擎前 chdir，沙箱根=worktree） |
| PR 审批 | PR 永远是 **Draft**，`Closes #N` 只在合并时生效——人不做最终确认，代码进不了主干 |
| 失控保护 | 引擎全部安全限制生效：轮数上限、任务超时、连续工具错误熔断、成本熔断 |
| 失败透明 | 测试失败/任务异常 → 自动回帖 issue（含日志），不静默吞掉 |

## 六、配置项（config.yaml）

```yaml
github:
  token: ""                          # 推荐用 GITHUB_TOKEN 环境变量
  api_base: "https://api.github.com"

dev:
  branch_prefix: dev                 # 分支白名单前缀
  worktree_dir: ".zouwucode_worktrees"
  test_command: ""                   # 空=自动探测（pytest / npm test）
  verify_retries: 2                  # 测试失败自动迭代修复次数
  max_concurrent_tasks: 3            # 队列并行上限
  task_timeout_seconds: 3600         # 单任务超时
  watch_label: "zouwucode:do"        # watch 认领标签
  draft_pr: true                     # 保持 true！false 会创建非 Draft PR

engine:
  max_cost_usd: 2.0                  # dev 模式强烈建议设置
```

## 七、经验沉淀

成功的任务会把「实现摘要 + 验证轮数 + 改动文件数」写入项目记忆
（`.zouwucode/memory/decisions.json`），后续同类任务可复用上下文。

## 八、已知限制（1.0）

- 验证以「测试通过」为准；无测试的仓库会跳过验证直接建 PR（PR 正文会注明）
- watch 模式按标签认领，不做依赖排序（关联 issue 建议合并提交）
- 单任务成本统计基于引擎累计估算，跨重试可能略有偏差

## 九、代码位置

| 模块 | 文件 |
|------|------|
| GitHub API 客户端 | `zouwucode/dev/github.py` |
| worktree 隔离 | `zouwucode/dev/workspace.py` |
| 端到端管线 | `zouwucode/dev/pipeline.py` |
| SQLite 任务队列 | `zouwucode/dev/queue.py` |
| CLI 路由/worker/watch | `zouwucode/dev/cli.py` |
| 测试 | `tests/test_dev.py`（27 用例） |
