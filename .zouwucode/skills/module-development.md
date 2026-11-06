# ZOUWUCODE 可扩展模块开发指南

> 本 skill 说明如何为 ZOUWUCODE 主体框架开发可加载扩展模块（如 hello-my-zouwucode）。ZOUWUCODE 三端（CLI/TUI/Web）不硬编码模块内部，统一通过 `zouwucode/modules/manager.py` 的 `ModuleManager` 派发。

## 一、模块接口协议

模块是一个普通 Python 类，必须提供 `name` 属性，并实现三个可选回调：

```python
class MyModule:
    name = "my-module"                       # 必需：模块唯一标识
    version = "1.0.0"
    commands = ["/my-cmd <args>"]            # 帮助文本用

    async def handle_message(self, text: str):
        """处理普通消息。返回渲染 dict 或 None（未处理，回退内建逻辑）。"""

    async def handle_command(self, command: str, arg: str = "", context: dict | None = None):
        """处理斜杠命令（如 /hello-ultrawork）。返回渲染 dict 或 None。"""

    def on_event(self, etype: str, message: str, payload: dict):  # 或构造时传入宿主 on_event
        """模块内部事件广播给宿主 UI。"""
```

**渲染 dict 协议**（三端通用）：

```python
{
    "title": "章节标题",      # 可选
    "content": "正文（多行），",  # 可选
    "details": [...],        # 可选，明细行列表
}
```

返回 `None` 表示模块未处理该消息/命令，宿主回退到内建逻辑。

## 二、注册与按需加载

宿主初始化时（CLI `zouwucode/tui/app.py`、TUI `textual_app.py`、Web `webui/server.py` 的 `run_web` 注入）：

```python
from ..modules.manager import ModuleManager

self.modules = ModuleManager(self.config, self.engine)
if self.config.my_module.enabled:            # 配置段控制总开关
    try:
        from my_module.module import MyModule
        self.modules.register(MyModule(self.engine, config=self.config,
                                       on_event=self._on_module_event))
    except Exception:
        pass                                 # 模块可选，加载失败绝不阻塞主体启动
```

三端命令派发统一走 `await self.modules.dispatch_command(command, arg, {...})`；消息派发走 `dispatch_message(text)`；事件走 `dispatch_event(etype, message, payload)`。

## 三、配置段

在 `zouwucode/config.py` 用 Pydantic 模型承载模块配置，作为 `ZOUWUCODEConfig` 字段：

```python
class MyModuleConfig(BaseModel):
    enabled: bool = True
    state_dir: str = ".my-module"

class ZOUWUCODEConfig(BaseModel):
    ...
    my_module: MyModuleConfig = Field(default_factory=MyModuleConfig)
```

## 四、独立顶层包 vs 子模块

- **独立顶层包**（推荐，如 `hello_my_zouwucode/`）：可独立导入、独立测试、独立演进。注意包内**禁止使用 `from ..` 相对导入**（相对导入指向宿主包，会 ImportError），一律写 `from zouwucode.xxx import ...`。
- **宿主子模块**（`zouwucode/modules/`）：适合轻量扩展。

## 五、状态目录

模块运行时状态放在工作目录（默认 `.hello-my-zouwucode/`），通过配置 `state_dir` 注入；boulder/plans/notepads 等子目录由模块自行组织。

## 六、验收标准

1. `config.<module>.enabled = false` 时三端不注册、不报错。
2. 模块加载抛异常时主体启动不受影响（打印 Warning 继续）。
3. 未知命令/普通消息时模块返回 `None`，内建逻辑正常工作。
4. 三端同一命令行为一致（CLI/TUI/Web 均派发到同一 `ModuleManager`）。
5. 新增 `tests/test_<module>.py` 覆盖：注册/派发/注销、命令与消息处理、`status_payload`、enabled 开关契约。
