# 三端回归验证流程

> 本 skill 记录 ZOUWUCODE CLI/TUI/Web 三端的自动化回归验证方法，用于确认模式切换（Ctrl+S 循环）、Agent 交互（权限审批）、模块派发等核心链路稳定。

## 一、TUI 验证（Textual Pilot）

用 `run_test` 无头驱动真实组件，验证 Ctrl+S 循环与斜杠命令：

```python
app = ZOUWUCODETUI()
async with app.run_test(size=(120, 40)) as pilot:
    await asyncio.sleep(0.3)
    assert app.current_mode == "agent"          # 默认模式
    seq = []
    for _ in range(3):                          # 3 次 = 1 个完整循环
        await pilot.press("ctrl+s")
        await asyncio.sleep(0.05)
        seq.append(app.current_mode)
    assert seq == ["yolo", "plan", "agent"]     # agent → yolo → plan → agent

    # 斜杠命令切换（防 AttributeError 回归）
    app.query_one("#input-box").value = "/plan"
    await pilot.press("enter")
    await asyncio.sleep(0.1)
    assert app.current_mode == "plan"
```

关键点：
- 起始模式为 agent，3 次按键回 agent；9 次按键 = 3 个完整循环（别把"9 次后"误算成 yolo）。
- TUI 模式切换方法名是 `action_set_mode()`，**不是** `set_mode()`（曾因误用 `set_mode` 抛 AttributeError）。
- 断言注意异步时序：按键后需 `await asyncio.sleep` 让事件循环处理。

## 二、Web 验证

1. **静态**：前端常量 `const MODES = ['plan', 'agent', 'yolo']` 与 TUI 顺序一致；`e.ctrlKey && e.key === 's'` 处理器存在。
2. **API**：`POST /api/mode {"mode": m}` 后断言 `app.engine.mode == m`；循环 plan/agent/yolo 各一次。
3. **模块**：`POST /api/hello-my-zouwucode {"action":"status"}` 返回 agents=11。
4. 用真实 HTTP 客户端（禁代理：`urllib.request.ProxyHandler({})`）避免本地代理干扰。

## 三、CLI 验证（input mock）

`run_interactive` 用 `input()` 读命令，mock 时必须**回显 prompt** 到 stdout，否则 prompt 断言永远失败：

```python
def fake_input(prompt="", _inputs=inputs, _buf=buf):
    sys.stdout.write(prompt)   # 模拟 input() 回显
    sys.stdout.flush()
    return next(_inputs)

with patch("builtins.input", fake_input), redirect_stdout(buf):
    await app.run_interactive()
out = buf.getvalue()
assert "z a >" in out and "z p >" in out and "z y >" in out  # prompt 随模式变化
assert out.count("Switched to PLAN mode") == 2               # 两次切换回显
```

## 四、Agent 交互验证（权限队列）

```python
# agent 模式：工具调用前权限请求入队
task = asyncio.create_task(coord.execute_tool(FakeCall(), TurnContext([], [], mode="agent")))
await asyncio.sleep(0.05)
pending = await coord.get_pending_permissions()
assert len(pending) == 1 and pending[0]["tool"] == "ls"

# yolo 模式：直接执行，无权限请求
await coord2.execute_tool(FakeCall(), TurnContext([], [], mode="yolo"))
assert await coord2.get_pending_permissions() == []
```

## 五、稳定性复测

- 修复类验证至少连续跑 3 次，确认无时序抖动（Pilot 的 sleep 阈值放宽到 0.03~0.1s 可容忍）。
- 全量回归 `python -m pytest -q`，确认总用例数只增不减。

## 六、回归测试落库

验证脚本（临时 .py）结论稳定后，提炼为 `tests/test_tui.py` / `tests/test_webui.py` 的正式用例，防止复发；临时脚本用后即删。
