"""Compatibility shim: the formatting primitives now live behind the presenter seam.

The real module is ``presenters.text.primitives``; ``num`` and ``plural`` are the
two that domain code may also reach, and they live in ``core.text``. Nothing new
should import this path. Kept because 28 test files and every ``tools/`` module
spell it ``render.table`` today, and the output has to stay byte-identical.
"""

from __future__ import annotations

from .presenters.text.primitives import (
    MAX_ROWS,  # noqa: F401  -- __all__ never listed these three, but callers use
    bullets,
    clamp,  # noqa: F401  -- render.clamp and render.flows everywhere in tools/, so
    envelope,
    flows,  # noqa: F401  -- re-exporting them is what keeps the shim complete
    ids_footer,
    kv,
    num,
    plural,
    rate,
    table,
    where_bands,
)

__all__ = [
    "bullets",
    "envelope",
    "ids_footer",
    "kv",
    "num",
    "plural",
    "rate",
    "table",
    "where_bands",
]
