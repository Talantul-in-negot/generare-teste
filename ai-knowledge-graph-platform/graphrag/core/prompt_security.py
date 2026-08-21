"""Small, deterministic boundaries for interpolating untrusted prompt data."""

from __future__ import annotations

from html import escape


def escape_prompt_data(value: str) -> str:
    """Escape markup so source text cannot close the prompt's data element."""
    return escape(value, quote=False)
