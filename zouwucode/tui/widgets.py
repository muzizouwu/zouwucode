"""Reusable Textual widgets and modal screens for the TUI.

Split out of ``textual_app.py`` to keep the application module focused on
orchestration. Everything defined here is re-exported from
``zouwucode.tui.textual_app`` so existing imports keep working.
"""

import logging
from typing import ClassVar

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Label, RichLog

logger = logging.getLogger("zouwucode.tui.textual_app")


# ── ANSI style helpers for RichLog messages ─────────────────────────────────
def _style(msg: str, style: str = "") -> Text:
    """Return chat content as a Rich Text object (no markup parsing).

    The chat view deliberately runs with markup disabled: model and user
    text is arbitrary data, never markup. Previously "[interrupted]" or
    "[dim]" inside a reply was silently swallowed by the markup parser,
    and a stray "[/]" raised and replaced the whole message with an error.
    """
    return Text(str(msg), style=style) if style else Text(str(msg))


class _StickyRichLog(RichLog):
    """Chat log that follows new output only when the user is at the bottom.

    Plain RichLog auto-scrolls on every write, so streaming output (thinking
    deltas, tool lines, replies) yanks the viewport away from whatever the
    user is reading. This keeps the reading position unless the view was
    already at the end — the behaviour every chat UI has.
    """

    def write(self, content, *args, **kwargs):
        # RichLog.write(content, width=None, expand=False, shrink=True,
        # scroll_end=None, animate=False). Textual replays deferred renders
        # with these passed positionally, so mirror the full signature and
        # only fill scroll_end when the caller left it unset.
        scroll_end_given = len(args) > 3 or kwargs.get("scroll_end") is not None
        follow = False
        if not scroll_end_given:
            follow = self._near_bottom()
            # Never let RichLog scroll on its own: its scroll_end is evaluated
            # against the OLD virtual height, so the viewport can settle one
            # line short of the end (intermittently seen on Windows/Python
            # 3.12 CI). We re-pin ourselves once the layout has caught up.
            kwargs["scroll_end"] = False
        if follow:
            # Remember where the viewport was: at settle time scroll_y is
            # still there (RichLog did not scroll), so "did the user scroll
            # up since?" is answered by comparing against this anchor.
            self._follow_anchor = self.scroll_y
        result = super().write(content, *args, **kwargs)
        if follow:
            self._follow_pending = True
            try:
                self.call_after_refresh(self._settle_follow)
            except Exception:  # widget not mounted/running yet
                logger.debug("swallowed sticky-scroll reschedule failure",
                             exc_info=True)
        return result

    def _near_bottom(self) -> bool:
        """True when the viewport is at (or within a line of) the end."""
        try:
            if self.is_vertical_scroll_end:
                return True
            return self.scroll_y >= self.max_scroll_y - 1
        except Exception:  # layout not ready yet
            return True

    def _settle_follow(self, attempt: int = 0) -> None:
        """Re-pin after the new line is laid out — unless the user scrolled up.

        Re-checking here (rather than trusting the pre-write decision) keeps a
        user who scrolls up mid-stream from being yanked to the bottom by a
        pending follow-up scroll. The pin can need more than one refresh on a
        slow runner (the last line's layout lands after the callback), so it
        re-queues a bounded number of times until the viewport really is at
        the end.
        """
        if attempt == 0 and not getattr(self, "_follow_pending", False):
            return
        anchor = getattr(self, "_follow_anchor", 0)
        try:
            if self.scroll_y < anchor - 1:
                self._follow_pending = False
                return  # user moved up after we scheduled the pin
            self.scroll_end(animate=False, immediate=True, x_axis=False)
            if attempt < 5 and self.scroll_y < self.max_scroll_y:
                self.call_after_refresh(
                    lambda: self._settle_follow(attempt + 1))
                return
        except Exception:
            logger.debug("swallowed sticky-scroll pin failure", exc_info=True)
        self._follow_pending = False


class InterruptConfirmScreen(ModalScreen):
    """Modal confirmation before interrupting the running task.

    Prevents accidental interrupts: the task only stops after the user
    explicitly confirms. Result is delivered via app-level attribute
    ``_interrupt_confirmed`` (True=confirm, False=cancel).
    """

    CSS = """
    InterruptConfirmScreen {
        align: center middle;
        background: #00000088;
    }
    #interrupt-dialog {
        width: 60;
        height: auto;
        padding: 1 2;
        border: thick $error;
        background: $surface;
    }
    #interrupt-dialog-title {
        text-style: bold;
        color: $error;
        margin-bottom: 1;
    }
    #interrupt-dialog-buttons {
        height: auto;
        align-horizontal: center;
        margin-top: 1;
    }
    #interrupt-dialog-buttons Button {
        margin: 0 2;
        min-width: 14;
    }
    """

    BINDINGS: ClassVar = [
        Binding("escape", "cancel_interrupt", "Cancel", show=False),
        Binding("enter", "confirm_interrupt", "Confirm", show=False),
    ]

    def __init__(self, task_desc: str = ""):
        super().__init__()
        self._task_desc = task_desc

    def compose(self) -> ComposeResult:
        with Vertical(id="interrupt-dialog"):
            yield Label("⚠ 确认打断当前任务？", id="interrupt-dialog-title")
            desc = self._task_desc or "AI 正在执行任务"
            yield Label(
                f"当前操作：{desc}\n"
                f"打断后可选择「继续」（AI 会接着断点继续）或「放弃」。",
                id="interrupt-dialog-desc",
            )
            with Horizontal(id="interrupt-dialog-buttons"):
                yield Button("确认打断 (Enter)", variant="error", id="btn-confirm-interrupt")
                yield Button("继续执行 (Esc)", variant="default", id="btn-cancel-interrupt")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "btn-confirm-interrupt")

    def action_confirm_interrupt(self) -> None:
        self.dismiss(True)

    def action_cancel_interrupt(self) -> None:
        self.dismiss(False)


class ApprovalScreen(ModalScreen):
    """Modal y/n/a confirmation shown before a tool runs in agent mode.

    Deliberately separate from InterruptConfirmScreen (whose ids and text
    are regression-pinned); this screen only ever answers the approval
    question and dismisses with "y" / "a" / "n".
    """

    BINDINGS = [
        Binding("y", "approve", "允许一次", show=False),
        Binding("a", "always", "始终允许", show=False),
        Binding("n", "deny", "拒绝", show=False),
        Binding("escape", "deny", "拒绝", show=False),
    ]

    def __init__(self, tool_name: str, preview: str) -> None:
        super().__init__()
        self._tool_name = tool_name
        self._preview = preview

    def compose(self) -> ComposeResult:
        with Vertical(id="approval-dialog"):
            yield Label(f"⚠ 允许执行 {self._tool_name} ？", id="approval-title")
            yield Label(self._preview or "（无参数）", id="approval-preview")
            yield Button("允许一次 (y)", variant="success", id="btn-approval-yes")
            yield Button("本会话始终允许 (a)", variant="primary", id="btn-approval-always")
            yield Button("拒绝 (n)", variant="error", id="btn-approval-no")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        mapping = {"btn-approval-yes": "y", "btn-approval-always": "a",
                   "btn-approval-no": "n"}
        self.dismiss(mapping.get(event.button.id, "n"))

    def action_approve(self) -> None:
        self.dismiss("y")

    def action_always(self) -> None:
        self.dismiss("a")

    def action_deny(self) -> None:
        self.dismiss("n")
