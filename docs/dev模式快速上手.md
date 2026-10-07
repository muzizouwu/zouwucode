# dev 模式快速上手

> 5 分钟跑通第一个 Devin 式自主任务：issue → AI 编码 → 验证 → Draft PR。
> 完整功能说明见 [dev模式使用说明](dev模式使用说明.md)。

## 第 0 步：准备（一次性）

```powershell
# 1. 安装 ZOUWUCODE（含 dev 子命令）
pip install -e .

# 2. 配置 DeepSeek API Key（AI 编码必需）
zouwucode --init-config
#   编辑 zouwucode_data/config.yaml：
#   providers:
#     deepseek:
#       api_key: "sk-your-key"

# 3. 配置 GitHub token（issue→PR 流程必需；纯本地任务可跳过）
$env:GITHUB_TOKEN = "ghp_xxx"
```

GitHub token 权限最小化（fine-grained，只授权目标仓库）：
`Contents: Read and write` + `Pull requests: Read and write` + `Issues: Read and write`。

**强烈建议**同时在 config.yaml 设置成本熔断，防止复杂任务失控烧额度：

```yaml
engine:
  max_cost_usd: 2.0
```

## 第 1 步：跑第一个任务

进入你的 git 仓库目录（需有 origin 远程），二选一：

```bash
# A. GitHub issue 驱动（完整流程：worktree → 编码 → 验证 → Draft PR）
zouwucode dev https://github.com/you/repo/issues/42

# B. 本地自由文本任务（不建 PR，只在 dev/* 分支产出改动）
zouwucode dev "给 utils.py 的 parse_date 函数补充单元测试"
```

你会依次看到：创建 `dev/issue-42` worktree → agent 自主编码 → 自动跑测试（失败会回灌修复，最多 2 轮）→ 提交推送 → 输出 Draft PR 链接。

## 第 2 步：验收

打开 Draft PR，review 改动。满意就 **Ready → Merge**，不满意直接关闭 PR——AI 永远不碰 main。

```bash
# 任务结束后清理 worktree（可选）
git worktree list
git worktree remove .zouwucode_worktrees/issue-42
```

## 进阶：异步清 backlog

```bash
# 批量入队
zouwucode dev --queue https://github.com/you/repo/issues/43
zouwucode dev --queue https://github.com/you/repo/issues/44

# 3 个 worker 并行消化（各自独立 worktree，互不冲突）
zouwucode dev --workers 3

# 随时查看队列
zouwucode dev --status
```

或者全自动托管：在 GitHub 给 issue 打上 `zouwucode:do` 标签，然后挂着：

```bash
zouwucode dev --watch        # 每 60s 轮询，发现新标签 issue 自动认领执行
```

## 常见问题

| 现象 | 原因 / 解决 |
|------|------------|
| `no token is configured` | issue URL 任务需要 `GITHUB_TOKEN` 或 `config.github.token` |
| `Not a git repository` | 当前目录不是 git repo，先 `cd` 进项目 |
| `Refusing to operate on protected branch` | 分支安全白名单生效，属正常拦截 |
| 任务超时/成本熔断终止 | 调大 `dev.task_timeout_seconds` / `engine.max_cost_usd`，或拆小任务 |
| 测试一直失败不收敛 | 查看 `--status` 与日志 `zouwucode_data/logs/zouwucode.log`；可在 config 显式指定 `dev.test_command` |

## 安全边界（放心交给 AI）

- 只操作 `dev/*` 分支，`main`/`master`/`develop`/`release`/`stable` 硬拒绝
- agent 沙箱锁定在 worktree 内，碰不到你的主检出
- PR 永远是 Draft，合并决定权在人类
- 失败不建 PR，自动回帖 issue 请求人工介入
