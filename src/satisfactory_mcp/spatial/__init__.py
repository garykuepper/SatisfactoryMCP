"""Compatibility shim: the spatial model now lives at ``domain.spatial``.

Nothing here is logic, and the old ``__init__`` re-exported nothing, so the alias
loop is the whole file. Aliasing through ``sys.modules`` rather than re-exporting
names keeps module *identity*, so ``satisfactory_mcp.spatial.nodes`` and
``satisfactory_mcp.domain.spatial.nodes`` are the same module object -- which
matters because several tests monkeypatch the node table on it. New code imports
``domain.spatial`` directly; this exists for the test suite's import lines.
"""

from __future__ import annotations

import sys
from importlib import import_module

for _name in (
    "elevation",
    "geo",
    "maplink",
    "nodes",
    "origin",
    "ranking",
    "regions",
    "select",
):
    _module = import_module(f"satisfactory_mcp.domain.spatial.{_name}")
    # Both halves are needed: the ``sys.modules`` entry satisfies
    # ``import satisfactory_mcp.spatial.nodes``, the attribute satisfies
    # ``from satisfactory_mcp.spatial import nodes`` without a filesystem lookup.
    sys.modules[f"{__name__}.{_name}"] = _module
    globals()[_name] = _module
del _name, _module
