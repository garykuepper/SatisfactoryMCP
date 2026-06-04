"""Compatibility shim: the MCP app object now lives behind the interface seam.

The real module is ``interfaces.mcp.app``. Nothing new should import this path.
Kept because ten test files spell ``from satisfactory_mcp.app import _state``, and
because a star import would not carry the private names they actually reach for --
``__all__`` never listed them. Hence an explicit re-import list, the same shape as
``render.py``.
"""

from __future__ import annotations

from .interfaces.mcp.app import (
    Limit,
    _item_id,  # noqa: F401  -- the five privates are the whole point of the shim:
    _origin_for,  # noqa: F401  -- tests and scripts import them by name, and none of
    _player_xy,  # noqa: F401  -- them was ever public enough to reach via __all__
    _resolve_factory,  # noqa: F401
    _state,  # noqa: F401
    game,
    mcp,
)

__all__ = ["Limit", "game", "mcp"]
