"""Public package exports loaded only when they are requested.

Keeping the package initializer dependency-free lets the Harness discover skill
metadata without initializing models, credentials, or external tool clients.
"""

from importlib import import_module
from typing import Any


__all__ = [
    "EmotionClass",
    "PromptClass",
    "MemoryClass",
    "AgentClass",
    "search",
    "get_info_from_local",
    "DocumentProcessor",
]

# 保存模块名称，并没有真正导入
_EXPORTS = {
    "EmotionClass": (".Emotion", "EmotionClass"),
    "PromptClass": (".Prompt", "PromptClass"),
    "MemoryClass": (".Memory", "MemoryClass"),
    "AgentClass": (".Agents", "AgentClass"),
    "search": (".Tools", "search"),
    "get_info_from_local": (".Tools", "get_info_from_local"),
    "DocumentProcessor": (".AddDoc", "DocumentProcessor"),
}


def __getattr__(name: str) -> Any:
    try:
        module_name, attribute_name = _EXPORTS[name]
    except KeyError as exc:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from exc

    value = getattr(import_module(module_name, __name__), attribute_name)
    globals()[name] = value
    return value
