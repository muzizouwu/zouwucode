# 贡献指南

感谢你有兴趣为 ZOUWUCODE 做贡献！本文说明开发环境、代码规范与提交流程。

## 开发环境

```bash
# Python 3.10+
git clone https://github.com/muzizouwu/zouwucode.git
cd zouwucode
pip install -e ".[tui,dev]"
```

## 运行测试

```bash
python -m pytest tests/ -q              # 全量单元测试（无需 API Key）
python scripts/smoke_test.py            # 冒烟测试（无需 API Key）
```

所有 PR 必须保持 CI 绿色（GitHub Actions 会在 Python 3.10/3.12 × Ubuntu/Windows 矩阵上运行以上两项）。

## 项目结构速览

| 目录 | 职责 |
|------|------|
| `zouwucode/engine/` | Cache-First 引擎（迭代循环、安全限制、打断、重试） |
| `zouwucode/tools/` | 内置工具与 `task` 委派工具 |
| `zouwucode/agent/` | 工具协调器 + 子 Agent 系统（隔离引擎并行） |
| `zouwucode/extensions/` | MCP/LSP 扩展层（预留接口） |
| `zouwucode/tui/` `webui/` | CLI/TUI/Web 三端界面 |
| `hello_my_zouwucode/` | 多智能体编排模块 |
| `tests/` | pytest 测试（新功能必须附带测试） |

## 代码规范

- 遵循现有风格：类型注解、docstring、模块级 `logger = logging.getLogger(...)`
- 新增配置项一律进 `zouwucode/config.py` 的 Pydantic 模型（带默认值与注释），并同步 `config.example.yaml`
- 引擎层（`engine/`）禁止引入 UI 依赖；UI 层不得直接绕过 `EngineLoop` 调 provider
- 注释与文档以中文为主（与现有代码一致）

## 提交流程

1. 从 `main` 切出分支：`git checkout -b feat/<scope>`
2. 小步提交，commit message 用 `feat|fix|docs|test|refactor: <摘要>`
3. 本地跑 `python -m pytest tests/ -q` 确认全绿
4. 开 PR 并填写模板中的 checklist

## 报告 Bug

使用 GitHub Issues 并附：复现步骤、`zouwucode_data/logs/zouwucode.log` 相关片段（注意先脱敏，删除 API Key）、Python 与 OS 版本。

## 安全漏洞

**不要开公开 Issue**。请按 [SECURITY.md](SECURITY.md) 中的方式私下报告。
