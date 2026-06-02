"""Compatibility shim: the sidecar seam now lives at ``core.saveio``.

Nothing here is logic. Aliasing through ``sys.modules`` rather than re-exporting
names keeps module *identity*, so ``satisfactory_mcp.save.projection`` and
``satisfactory_mcp.core.saveio.projection`` are the same module object -- which
matters because ``tests/test_sidecar_spawn.py`` reaches for the private
``_run_sidecar`` and monkeypatches ``proj.subprocess`` on it. New code imports
``core.saveio`` directly.

Only ``projection`` is aliased: ``state`` and ``collect`` still physically live
here until they move to ``domain/``.
"""

from __future__ import annotations

import sys
from importlib import import_module

_module = import_module("satisfactory_mcp.core.saveio.projection")
# Both halves are needed: the ``sys.modules`` entry satisfies
# ``import satisfactory_mcp.save.projection``, the attribute satisfies
# ``from satisfactory_mcp.save import projection`` without a filesystem lookup.
sys.modules[f"{__name__}.projection"] = _module
projection = _module
del _module
