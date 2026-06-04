"""Compatibility shim: the sidecar seam and the world state have both moved.

Nothing here is logic. ``projection`` is now ``core.saveio.projection`` and
``state`` is now ``domain.world.state``. Aliasing through ``sys.modules`` rather
than re-exporting names keeps module *identity*, so the old and new dotted paths
are the same module object -- which matters because ``tests/test_sidecar_spawn.py``
monkeypatches ``proj.subprocess`` on the private ``_run_sidecar``, and
``tests/test_collected_from_world.py`` monkeypatches ``load_collectibles`` on the
state module to stand in a clone that has no placement table. New code imports the
real paths directly.

Nothing physically lives here any more: ``collect`` became
``domain.collectibles.service``, and nothing outside this package ever imported it
by the old name, so it gets no alias.
"""

from __future__ import annotations

import sys
from importlib import import_module

for _name, _target in (
    ("projection", "satisfactory_mcp.core.saveio.projection"),
    ("state", "satisfactory_mcp.domain.world.state"),
):
    _module = import_module(_target)
    # Both halves are needed: the ``sys.modules`` entry satisfies
    # ``import satisfactory_mcp.save.state``, the attribute satisfies
    # ``from satisfactory_mcp.save import state`` without a filesystem lookup.
    sys.modules[f"{__name__}.{_name}"] = _module
    globals()[_name] = _module
del _name, _target, _module
