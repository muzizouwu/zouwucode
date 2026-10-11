"""Shared error formatting for tool results.

Root cause this prevents: some exceptions have an EMPTY str() — notably
httpx.ConnectTimeout/ReadTimeout/ConnectError, and asyncio.TimeoutError.
Tools that surface `str(exc)` directly as the model-facing error produced a
BLANK message, so the model saw "failed" with no reason and either retried
identically (looking "stuck") or the user saw an empty error. Every tool
error path should format through describe_exc() so the model always gets an
actionable reason.
"""


_EMPTY_HINTS = {
    "ConnectTimeout": "连接超时（网络不可达或被防火墙拦截）",
    "ReadTimeout": "读取超时",
    "WriteTimeout": "写入超时",
    "PoolTimeout": "连接池耗尽（并发过高）",
    "ConnectError": "无法建立连接",
    "TimeoutError": "操作超时",
    "CancelledError": "操作被取消",
}


def describe_exc(exc: BaseException) -> str:
    """Type name + human-readable reason; never returns an empty string."""
    name = type(exc).__name__
    msg = str(exc).strip()
    if not msg:
        msg = _EMPTY_HINTS.get(name) or "未知错误（无详细信息）"
    return f"{name}: {msg}"
