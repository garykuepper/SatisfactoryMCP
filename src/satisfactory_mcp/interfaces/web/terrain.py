"""The heightfield seam: one loader, so a test can replace the terrain for the whole app.

A module of its own rather than a private function inside a router, because two endpoints
read the field -- ``/api/inspect`` measures a point against it and ``/api/floors`` decides
whether a placement is standing on ground -- and they now live in different files. A seam
that only one file can reach is not a seam.

**Call it through the module.** ``terrain.field()``, never ``from .terrain import field``:
a ``from`` import binds the function object into the caller's namespace at import time, so
``monkeypatch.setattr`` on this module would be invisible to it and the test would silently
measure the reader's own machine instead of its synthetic field.
"""

from __future__ import annotations

from ...domain.spatial import heightfield as spatial_heightfield

__all__ = ["field"]


def field():
    """The extracted 1 m heightfield, or ``None`` on a machine that has none.

    A loader and only a loader, exactly like ``/api/mapimage``: the raster is derived from
    the game's cooked assets, so this repository ships none and most installs have none.
    Wrapped in a function of its own rather than called inline so a test can replace it
    with a synthetic field and get a deterministic answer without a game install.
    """
    return spatial_heightfield.load_field()
