"""Compatibility shim: the game-data package now lives at ``core.gamedata``.

Nothing here is logic. Aliasing through ``sys.modules`` rather than re-exporting
names keeps module *identity*, so ``satisfactory_mcp.docs.model.Recipe`` and
``satisfactory_mcp.core.gamedata.model.Recipe`` are the same class -- isinstance
and ``monkeypatch.setattr`` on either path keep working -- and it is complete by
construction, including the privates the tests reach for. New code imports
``core.gamedata`` directly; this exists for the test suite's import lines.
"""

from __future__ import annotations

import sys
from importlib import import_module

for _name in (
    "constants",
    "footprint",
    "loader",
    "model",
    "normalize",
    "search",
    "uestruct",
):
    _module = import_module(f"satisfactory_mcp.core.gamedata.{_name}")
    # Both halves are needed: the ``sys.modules`` entry satisfies
    # ``import satisfactory_mcp.docs.model``, the attribute satisfies
    # ``from satisfactory_mcp.docs import model`` without a filesystem lookup.
    sys.modules[f"{__name__}.{_name}"] = _module
    globals()[_name] = _module
del _name, _module
