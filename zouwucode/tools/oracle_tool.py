"""oracle tool — consult a (typically stronger) second model for a review.

This is the "cheap model works, smart model reviews" half of model routing
(Amp's oracle, Aider's architect/editor). The oracle is READ-ONLY: it is given
text (a question, a diff, a plan, some code) and returns a verdict; it can
never run tools or touch the workspace. It runs on the provider named by
``router.oracle_provider`` — so the main loop can stay on the fast/cheap model
while a single high-stakes decision is escalated.
"""

from .base import BaseTool, ToolSpec, ToolResult
from ..errors import describe_exc

ORACLE_SYSTEM = (
    "You are the ORACLE: a senior engineer consulted for a second opinion. "
    "You have NO tools and cannot run or read anything — reason ONLY from the "
    "material provided. Be concise and direct. Begin with a one-line verdict "
    "(APPROVE / CONCERN / REJECT), then at most 5 bullet points of concrete, "
    "actionable feedback. If it is fine, say so briefly; do NOT invent "
    "problems. Weigh correctness, edge cases, security and simplicity."
)


class OracleTool(BaseTool):
    """Ask the configured oracle model for a read-only second opinion."""

    def __init__(self, provider, max_tokens: int = 1024, temperature: float = 0.0):
        super().__init__(None)  # oracle has no sandbox: it never touches files
        self.provider = provider
        self.max_tokens = int(max_tokens or 1024)
        self.temperature = float(temperature or 0.0)

    def get_spec(self) -> ToolSpec:
        return ToolSpec(
            name="oracle",
            description=(
                "Consult a stronger 'oracle' model for a READ-ONLY second opinion "
                "on a plan, a diff, or a tricky decision. It cannot run tools or "
                "touch files — it only reasons over the text you pass. Use it "
                "before high-risk or irreversible changes, or when you are unsure."
            ),
            parameters={
                "question": {"type": "string",
                             "description": "What you want reviewed / answered."},
                "context": {"type": "string",
                            "description": "Optional material to review (diff, plan, code, ...)."},
            },
            required=["question"],
        )

    async def execute(self, question: str = "", context: str = "") -> ToolResult:
        if not str(question).strip():
            return ToolResult(success=False, output="", error="`question` is required.")
        user = str(question)
        if context:
            user = (f"Material under review:\n\n{context}\n\n---\n"
                    f"Question / request:\n{question}")
        try:
            resp = await self.provider.chat(
                [{"role": "system", "content": ORACLE_SYSTEM},
                 {"role": "user", "content": user}],
                max_tokens=self.max_tokens,
                temperature=self.temperature,
            )
        except Exception as e:  # noqa: BLE001 — surface as a tool error, do not crash
            return ToolResult(success=False, output="", error=describe_exc(e))
        model = getattr(self.provider, "model", "") or "oracle"
        body = (resp.content or "(empty response)") if resp else "(empty response)"
        return ToolResult(success=True, output=f"[oracle:{model}]\n{body}")
