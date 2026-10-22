"""Utilities for normalizing task titles."""


def normalize_title(title: str) -> str:
    """Remove surrounding whitespace from a task title."""
    return title.strip()