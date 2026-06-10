"""Tiny shared helpers used across the assistant package."""

from __future__ import annotations

from typing import Any


def text_result(message: str, is_error: bool = False) -> dict[str, Any]:
    """Build an MCP tool text response (optionally an error)."""
    result: dict[str, Any] = {"content": [{"type": "text", "text": message}]}
    if is_error:
        result["is_error"] = True
    return result
