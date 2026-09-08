"""Central logging setup — rotating file logs for post-mortem debugging.

Every UI entry point (CLI/TUI/Web) calls :func:`setup_logging` once at
startup. Logs go to ``<data_dir>/logs/zouwucode.log`` (rotating at 5 MB,
5 backups). Console output is intentionally left untouched: the TUI owns
the terminal, and the CLI prints its own UI text.

Level comes from ``config.log_level`` (default: info).
"""

import logging
import logging.handlers
from pathlib import Path

_MAX_BYTES = 5 * 1024 * 1024
_BACKUP_COUNT = 5

_configured = False


def setup_logging(data_dir: str | Path, level: str = "info") -> Path:
    """Attach a rotating file handler to the 'zouwucode' logger.

    Idempotent: safe to call from every entry point; only the first call
    configures handlers. Returns the log file path.
    """
    global _configured
    log_dir = Path(data_dir) / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "zouwucode.log"

    if _configured:
        return log_file

    root = logging.getLogger("zouwucode")
    numeric = getattr(logging, level.upper(), logging.INFO)
    root.setLevel(numeric)

    handler = logging.handlers.RotatingFileHandler(
        log_file, maxBytes=_MAX_BYTES, backupCount=_BACKUP_COUNT,
        encoding="utf-8",
    )
    handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    ))
    root.addHandler(handler)

    # Quiet third-party chattiness unless debugging our own code.
    if numeric == logging.DEBUG:
        logging.getLogger().setLevel(logging.DEBUG)
    else:
        logging.getLogger("httpx").setLevel(logging.WARNING)

    _configured = True
    root.info("Logging initialised | file=%s | level=%s", log_file, level)
    return log_file
