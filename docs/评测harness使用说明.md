# 评测 harness 使用说明

`zouwucode eval` 是任务级评测套件——用**真实 agent 栈**（与生产完全一致的
引擎/沙箱/工具接线）跑一组带**确定性行为断言**的任务，输出通过率与成本。

## 为什么需要它

2026 年行业共识：同一个模型，不同脚手架在 SWE-bench Verified 上可差 20 分。
**差距来自脚手架（循环、验证节奏、行动面），不来自模型**——而改进脚手架的
前提是能度量它。没有评测基线，每次改 prompt / 验证层 / 引擎都是盲调。

## 命令

```bash
zouwucode eval                 # 跑内置示例任务
zouwucode eval --list          # 列出可用任务
zouwucode eval --task fix-off-by-one   # 只跑一个
zouwucode eval --tasks my_tasks/       # 跑自定义任务目录
```

输出示例：

```
  ▶ eval：3 个任务，工作区 zouwucode_data/eval_runs/20261008-120000

  ✅ fix-off-by-one            12.3s  $0.0210
  ❌ handle-empty-input         30.1s  $0.0402  failed: python_eval
  ✅ add-edge-tests              8.7s  $0.0155

  通过率 2/3 = 67%   总成本 $0.0767
```

## 编写评测任务（YAML）

每个任务一个 `.yaml` 文件，放在任务目录下：

```yaml
name: my-task                    # 唯一名（也是工作区目录名）
prompt: |                        # 给 agent 的任务描述
  data.py 的 parse_ints("1,x,3") 会崩溃。让它跳过非数字项，
  返回 [1, 3]。直接修改 data.py。
setup:                           # 任务开始前写入工作区的文件
  data.py: |
      def parse_ints(s):
          return [int(x) for x in s.split(",")]
checks:                          # 全部通过 = 任务通过；判分不经过 LLM
  - type: python_eval            # 表达式求值为真
    expr: "__import__('data').parse_ints('1,x,3') == [1, 3]"
  - type: python_eval
    expr: "__import__('data').parse_ints('7') == [7]"
  - type: file_exists            # 工作区文件断言
    path: data.py
  - type: command_pass           # shell 退出码 0
    command: python -m pytest -q
timeout: 300                     # 单任务 agent 运行超时（秒）
```

### 检查类型

| type | 判定 | 说明 |
|------|------|------|
| `file_exists` / `file_absent` | 路径存在性 | `path` 相对工作区 |
| `file_contains` | 文本包含 | 慎用——精确匹配源码格式会误伤合法修复 |
| `python_eval` | 表达式为真 | 在工作区内运行，`sys.path` 含工作区；**推荐**：测行为不测写法 |
| `command_pass` | 命令退出码 0 | 跑项目自己的测试套件等 |

## 最佳实践

1. **行为断言优先**：`python_eval` 验证"功能对不对"，别用 `file_contains`
   验证"代码长什么样"——后者会把风格不同的正确修复误判为失败。
2. **任务小而确定**：一个任务只考一个能力（修 bug / 补边界 / 写测试），
   断言必须可判定，不留歧义。
3. **改前跑基线，改后对比**：调整 prompt、验证层、引擎参数前后各跑一次，
   通过率与成本的变化就是这次改动的收益证明。
4. **把踩过的坑固化为任务**：线上出过一个边界 bug，就写一个复现它的评测
   任务——评测集随项目一起变强。

## 实现位置

| 模块 | 文件 |
|------|------|
| 任务模型/运行器 | `zouwucode/eval/runner.py` |
| 确定性判分 | `zouwucode/eval/checks.py` |
| CLI 路由 | `zouwucode/eval/cli.py` |
| 内置示例任务 | `zouwucode/eval/tasks/*.yaml` |
| 共享栈工厂 | `zouwucode/runtime.py` `build_agent_engine()`（eval 与 dev 管线同源） |
