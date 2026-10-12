"""Regression: DevQueue timeout is configurable, not hardcoded.

Defect pinned: config.dev.task_timeout_seconds existed but nothing read
it — queue.submit() fell back to a hardcoded 3600 forever.
"""

import inspect

from zouwucode.config import ZOUWUCODEConfig
from zouwucode.dev.queue import DevQueue


def test_submit_stores_configured_timeout(tmp_path):
    cfg = ZOUWUCODEConfig()
    cfg.dev.task_timeout_seconds = 1234.0
    queue = DevQueue(tmp_path / "q.db")
    task_id = queue.submit("do something",
                           timeout=cfg.dev.task_timeout_seconds)
    row = queue._conn.execute(
        "SELECT timeout FROM dev_tasks WHERE id=?", (task_id,)).fetchone()
    assert float(row["timeout"]) == 1234.0


def test_default_timeout_unchanged_when_not_given(tmp_path):
    queue = DevQueue(tmp_path / "q.db")
    task_id = queue.submit("do something")
    row = queue._conn.execute(
        "SELECT timeout FROM dev_tasks WHERE id=?", (task_id,)).fetchone()
    assert float(row["timeout"]) == 3600.0


def test_cli_actually_passes_the_config_value():
    from zouwucode.dev import cli
    src = inspect.getsource(cli)
    assert src.count("timeout=config.dev.task_timeout_seconds") >= 2
