"""oh-my-opencode multi-agent orchestration port for ZOUWUCODE.

Replicates the Sisyphus / Atlas / Prometheus orchestration system:
- 11 specialised agents with DeepSeek-optimised system prompts
- IntentGate intent classification (13 intents)
- Category semantic routing (mapped to DeepSeek reasoning tiers)
- Boulder cross-session task state (.omo/boulder.json)
- Notepad wisdom accumulation (.omo/notepads/{plan}/)
- Ultrawork full-autonomy pipeline (ultrawork / ulw keyword)
"""

from .agents import AGENTS, AgentDef, AgentMode, get_agent, list_agents
from .categories import (
    CATEGORY_DEFAULTS,
    CATEGORY_NAMES,
    CategorySpec,
    category_table,
    get_category,
    list_categories,
)
from .intent_gate import Intent, IntentGate, IntentResult, intent_label
from .boulder import BoulderState, make_todo
from .notepad import NOTEPAD_FILES, Notepad
from .planner import Planner
from .atlas import Atlas, OrchestrationResult
from .orchestrator import SisyphusOrchestrator, detect_ultrawork

__all__ = [
    "AGENTS",
    "AgentDef",
    "AgentMode",
    "get_agent",
    "list_agents",
    "CATEGORY_DEFAULTS",
    "CATEGORY_NAMES",
    "CategorySpec",
    "category_table",
    "get_category",
    "list_categories",
    "Intent",
    "IntentGate",
    "IntentResult",
    "intent_label",
    "BoulderState",
    "make_todo",
    "NOTEPAD_FILES",
    "Notepad",
    "Planner",
    "Atlas",
    "OrchestrationResult",
    "SisyphusOrchestrator",
    "detect_ultrawork",
]
