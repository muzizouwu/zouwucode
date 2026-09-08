"""Context management — three-layer context system + compression + sliding window.

Inspired by Claude Code's context management architecture:
1. MEMORY.md — lightweight index, always loaded into context
2. Topic files — fetched on demand for project knowledge
3. Raw transcripts — never fully read, grepped for specific identifiers
4. DialogueCompressor — intelligent conversation compression
5. ContextWindow — sliding window for ultra-long contexts
"""

from .memory import MemoryManager
from .topics import TopicManager
from .transcript import TranscriptManager
from .compressor import DialogueCompressor, CompressionResult
from .sliding_window import ContextWindow

__all__ = [
    "MemoryManager",
    "TopicManager",
    "TranscriptManager",
    "DialogueCompressor",
    "CompressionResult",
    "ContextWindow",
]