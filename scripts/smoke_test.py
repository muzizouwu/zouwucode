# -*- coding: utf-8 -*-
"""ZOUWUCODE smoke test — exercise core modules with correct APIs."""
import asyncio
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

PASS = 0
FAIL = 0

def report(title, ok, detail=""):
    global PASS, FAIL
    status = "PASS" if ok else "FAIL"
    if ok:
        PASS += 1
    else:
        FAIL += 1
    print(f"[{status}] {title} {detail}")
    return ok

async def main():
    tmp = tempfile.TemporaryDirectory()
    tmp_path = Path(tmp.name)

    # 1. LsTool (the `ls` command) — the user's question
    try:
        from zouwucode.tools.file_tools import LsTool, GlobTool, ReadTool
        ls = LsTool()
        res = await ls.execute(path=str(ROOT))
        report("LsTool lists project root", res.success and "zouwucode" in res.output, f"(output chars={len(res.output)})")
        glob = GlobTool()
        gres = await glob.execute(pattern="*.py", path=str(ROOT / "zouwucode"))
        report("GlobTool finds .py files", gres.success and "config.py" in gres.output)
        read = ReadTool()
        rres = await read.execute(file_path=str(ROOT / "zouwucode" / "__init__.py"))
        report("ReadTool reads file", rres.success and "__version__" in rres.output)
    except Exception as e:
        report("file tools", False, str(e))

    # 2. ToolRegistry
    try:
        from zouwucode.tools.registry import ToolRegistry
        r = ToolRegistry()
        from zouwucode.tools.file_tools import LsTool
        r.register(LsTool())
        report("ToolRegistry register/get_names", r.get("ls") is not None and r.get_names() == ["ls"])
    except Exception as e:
        report("tool registry", False, str(e))

    # 3. Permission sandbox
    try:
        from zouwucode.sandbox.permission import PermissionManager, DANGEROUS_PATTERNS
        from zouwucode.config import SandboxConfig
        pm = PermissionManager(SandboxConfig(enabled=True))
        ok_rm = await pm.check_command("rm -rf /")
        ok_ls = await pm.check_command("ls -la")
        ok_zw = await pm.check_command("ls\u200b -la")
        report("sandbox blocks dangerous cmd", not ok_rm)
        report("sandbox allows safe cmd", ok_ls)
        report("sandbox blocks zero-width injection", not ok_zw)
        report("sandbox has danger patterns", len(DANGEROUS_PATTERNS) >= 10, f"({len(DANGEROUS_PATTERNS)} patterns)")
    except Exception as e:
        report("permission sandbox", False, str(e))

    # 4. ProjectMemory
    try:
        from zouwucode.project_memory import ProjectMemory
        m = ProjectMemory(project_root=tmp_path)
        m.set_goal("全面测试zouwucode功能")
        m.add_decision("测试", "验证记忆系统", category="testing")
        m.save_all()
        m2 = ProjectMemory(project_root=tmp_path)
        m2.load_all()
        goal = m2.get_active_goal()
        decs = m2.get_decisions(category="testing")
        report("project memory persists goal", goal == "全面测试zouwucode功能", f"(goal={goal})")
        report("project memory persists decisions", len(decs) == 1)
        ctx = m2.get_full_context()
        report("project memory builds context", "Current Goal" in ctx and "Previous Decisions" in ctx)
    except Exception as e:
        report("project memory", False, str(e))

    # 5. DialogueCompressor
    try:
        from zouwucode.context.compressor import DialogueCompressor
        c = DialogueCompressor(level="balanced")
        msgs = [
            {"role": "user", "content": "请修复登录模块的 bug"},
            {"role": "assistant", "content": "好的，我们 decided to 使用新的 session 方案。" + "x" * 500},
            {"role": "user", "content": "继续"},
        ]
        out = c.compress_conversation(msgs, keep_last_n=2)
        report("compressor returns messages", isinstance(out, list) and len(out) > 0, f"(out={len(out)} msgs)")
    except Exception as e:
        report("compressor", False, str(e))

    # 6. SessionManager
    try:
        from zouwucode.session.manager import SessionManager
        from zouwucode.config import ZOUWUCODEConfig
        cfg = ZOUWUCODEConfig()
        cfg.data_dir = str(tmp_path / "data")
        sm = SessionManager(cfg)
        sm.start_session("smoke-session")
        sm.log_turn({"role": "user", "content": "hello"})
        sessions = sm.list_sessions()
        report("session start/log/list", len(sessions) == 1, f"(sessions={len(sessions)})")
        data = sm.get_session("smoke-session")
        report("session load", data is not None and len(data.get("turns", [])) == 1)
    except Exception as e:
        report("session manager", False, str(e))

    # 7. hello-my-zouwucode IntentGate
    try:
        from hello_my_zouwucode.intent_gate import IntentGate, Intent
        ig = IntentGate()
        r1 = ig.classify("写一个Python脚本来处理数据")
        r2 = ig.classify("帮我修复这个 bug")
        r3 = ig.classify("重构一下这段代码")
        report("intent gate: implementation", r1.intent == Intent.IMPLEMENTATION, f"(got={r1.intent.value})")
        report("intent gate: fix", r2.intent == Intent.FIX, f"(got={r2.intent.value})")
        report("intent gate: refactor", r3.intent == Intent.REFACTOR, f"(got={r3.intent.value})")
    except Exception as e:
        report("intent gate", False, str(e))

    # 8. PrefixCache (cache-first engine)
    try:
        from zouwucode.engine.cache import PrefixCache, CacheStats
        pc = PrefixCache(max_prefix_tokens=1000)
        pc.freeze([{"role": "system", "content": "You are ZOUWUCODE"}])
        pc.append({"role": "user", "content": "你好" * 100})
        report("cache freeze/append/prefix", pc.is_frozen and len(pc.get_prefix()) == 2 and pc.get_prefix_length() > 0,
               f"(prefix_len={pc.get_prefix_length()})")
        stats = CacheStats()
        stats.record_turn(cache_hit=True, usage={"prompt_tokens": 1000, "prompt_cache_hit_tokens": 800, "completion_tokens": 50})
        stats.record_turn(cache_hit=False, usage={"prompt_tokens": 200, "prompt_cache_hit_tokens": 0, "completion_tokens": 10})
        report("cache stats hit_rate", abs(stats.hit_rate - 0.5) < 1e-6, f"(hit_rate={stats.hit_rate:.2f})")
    except Exception as e:
        report("cache engine", False, str(e))

    tmp.cleanup()
    print("-" * 55)
    print(f"RESULT: {PASS}/{PASS + FAIL} checks passed")
    sys.exit(0 if FAIL == 0 else 1)

if __name__ == "__main__":
    asyncio.run(main())
