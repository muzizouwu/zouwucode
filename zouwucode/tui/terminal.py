"""Terminal restore helpers for the TUI.

Kept in its own module so ``run_tui`` can always restore the console, even
when Textual's own teardown did not run.
"""

import logging
import sys

logger = logging.getLogger("zouwucode.tui.textual_app")


def _restore_terminal() -> None:
    """Best-effort terminal restore on TUI exit.

    Textual enables SGR mouse reporting (DECSET ?1000/?1002/?1003/?1006)
    and leaves the cursor hidden while running. If the process dies before
    its own teardown (crash, OS kill, closed window), those modes stay ON
    and the shell then echoes raw mouse-move sequences (looks like a wall
    of `^[[<35;199;4M` garbage). Emitting the disable sequences + a reset
    in a finally-block guarantees the console is usable no matter how the
    TUI exits.
    """
    try:
        sys.stdout.write(
            "\x1b[?1000l\x1b[?1002l\x1b[?1003l\x1b[?1006l"  # mouse off
            "\x1b[?25h"                                      # cursor on
            "\x1b[0m"                                        # SGR reset
            "\x1b[?2004l"                                    # bracketed paste off
        )
        sys.stdout.flush()
    except Exception as _exc:  # noqa: BLE001 — restore must never itself raise
        logger.debug("swallowed %s: %s", type(_exc).__name__, _exc)
