"""Console/subprocess encoding helpers for Windows (GBK vs UTF-8).

Windows child processes write their stdout/stderr in the *console/locale*
code page (cp936/GBK on zh-CN systems, cp1252 on Western ones), not UTF-8.
Decoding those bytes as UTF-8 with errors="replace" silently turns every
non-ASCII character into U+FFFD — Chinese output from python_exec, PowerShell
and git came back as mojibake (regression-pinned by tests/test_code_exec.py
::test_cjk_output and tests/test_encoding.py).

Rules of engagement:
- read side:   decode_console() — strict UTF-8 first, locale fallback.
- spawn side:  utf8_env() — force child Python interpreters to emit UTF-8.
- PowerShell:  PS_UTF8_PREFIX — force [Console]::OutputEncoding to UTF-8 so
  redirected stdout stays UTF-8 even under Windows PowerShell 5.x.
"""
from __future__ import annotations

import locale
import os

__all__ = ["decode_console", "utf8_env", "PS_UTF8_PREFIX"]

# Prepended to Windows PowerShell invocations so redirected stdout is UTF-8
# even when the console code page is GBK.
PS_UTF8_PREFIX = (
    "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8; "
    "$OutputEncoding=[System.Text.Encoding]::UTF8; "
)


def decode_console(b: bytes) -> str:
    """Decode subprocess output: strict UTF-8 first, then the locale code page.

    Strict UTF-8 first means correctly-encoded output is never mangled; the
    locale fallback keeps legacy GBK/cp1252 output readable instead of
    turning it into U+FFFD soup.
    """
    try:
        return b.decode("utf-8")
    except UnicodeDecodeError:
        enc = locale.getpreferredencoding(False) or "utf-8"
        return b.decode(enc, errors="replace")


def utf8_env() -> dict[str, str]:
    """Environment for child processes: force Python children to speak UTF-8."""
    return {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}
