"""Shared helpers for exposing functions as LLM "brain" tools.

Kept free of heavy imports (no llama_cpp, no device libraries) so that any
module can import it without pulling in optional dependencies.
"""

from __future__ import annotations

from typing import Any, Callable, Iterable


def brain_tool(func: Callable[..., object]) -> Callable[..., object]:
    """Mark a function as eligible for brain tool registration."""
    setattr(func, "__brain_tool__", True)
    return func


def mark_tool_functions(namespace: dict[str, Any], names: Iterable[str]) -> None:
    """Mark the named functions in a module namespace as brain tools."""
    for name in names:
        func = namespace.get(name)
        if callable(func):
            setattr(func, "__brain_tool__", True)
