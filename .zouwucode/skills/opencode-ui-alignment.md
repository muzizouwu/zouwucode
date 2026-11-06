# opencode 风格 UI 对齐指南

> 本 skill 记录 ZOUWUCODE 的 CLI/TUI/Web 界面如何对齐 opencode 的视觉与交互风格（欢迎屏、思考模式、工作流呈现、对话布局），以及三端快捷键一致性约定。

## 一、三端统一的快捷键

| 快捷键 | 功能 | 循环顺序 |
|---|---|---|
| Ctrl+S | 切换工作模式 | plan → agent → yolo → plan |
| Ctrl+R | 切换推理强度 | low → medium → max → low |
| Ctrl+L | 清空聊天 | — |

- TUI：`textual_app.py` 的 `BINDINGS` + `action_cycle_mode`；Web：前端 JS 常量 `const MODES = ['plan', 'agent', 'yolo']`（**顺序必须与 TUI 完全一致**）。
- 三端循环顺序不一致会直接破坏用户预期（曾因此修复过 Web 端顺序 bug）。

## 二、欢迎屏（CLI/TUI/Web）

- **TUI**：启动时在 RichLog 输出 ASCII art 徽标 + `┌ session ┐` 模块信息面板（模块名、版本、agents/categories 数量）。
- **Web**：`.welcome-art` 渲染 ASCII 艺术字横幅 + `.welcome-modules` 徽章行；发送任意消息后 `hideWelcome()` 移除。
- **关键坑**：Web 端 ASCII 艺术字必须 `white-space: pre`（见 webui-layout-fixes skill），否则被折叠成 1~2 行。

## 三、思考模式的展示

- opencode 用 `/thinking` 命令控制思考块显隐；ZOUWUCODE 的 TUI 支持 `/thinking` 切换，Web 端思考块始终为可折叠 `<details>`（`summary` 显示 `💭 Thinking`）。
- 思考块样式：斜体、左侧 cyan 边框、`max-height: 240px + overflow-y: auto`（防长思考撑破布局）。

## 四、工具调用/工作流程呈现

- 工具调用实时呈现为 `→ ToolName args` 前缀行（opencode 风格）：Web 由 `/api/chat` 响应的 `tools` 字段驱动 `addToolLines()`；TUI 由 engine 的 `on_tool_event` 钩子写入聊天区。
- 消息框架：TUI 用 `┃` 边框行前缀区分 `┃ You` / `┃ assistant` / `┃ 💭 thinking` / `┃ → tool`。
- footer 上下文进度条：`■■⬝⬝⬝⬝⬝⬝⬝⬝ 32%` 表示上下文占用（TUI 与 Web 状态栏均有）。

## 五、对话交互布局

- **TUI**：header（标题+模式徽标）→ 聊天区（RichLog）→ 输入框 → 状态栏 → footer 快捷键提示。
- **Web**：header（Logo+模式标签+推理强度下拉）→ 状态栏（Mode/Reasoning/Cache/Cost/Req/Context/Rules/Skills/Modules/进度条）→ 侧边栏（会话/规则/技能）+ 聊天区 → 输入框。
- 消息对齐：用户消息右对齐蓝色、助手左对齐深色、系统居中。

## 六、新增界面元素时的检查清单

1. 三端快捷键映射一致（顺序、语义）。
2. 多行/预格式化文本使用 `pre`/`pre-wrap`，杜绝 `\n` 折叠。
3. 长内容限高 + 内部滚动，不撑破容器。
4. 窄屏（<560px）header/状态栏不裁剪。
5. 新增 CSS 后更新 `tests/test_webui.py` 的 CSS 断言。
