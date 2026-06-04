"""Compatibility shim: the tool modules now live at ``interfaces.mcp.tools``.

An alias, not a re-implementation. Each old name is bound to the *same* module
object as the new one, which is what keeps two things working that a copy would
break: the decorators run exactly once, so importing both paths cannot register a
tool twice; and ``monkeypatch.setattr(tools.spatial, "_state", ...)`` still bites
the module the server actually calls.

Both halves of each alias are load-bearing. The ``sys.modules`` entry serves
``import satisfactory_mcp.tools.spatial``; the package attribute serves
``from satisfactory_mcp.tools import spatial``, which otherwise falls through to a
filesystem lookup in this now-empty directory and raises.

``__path__`` is redirected for the same reason: ``tests/test_surface.py`` walks it
with ``pkgutil.iter_modules`` to prove every module on disk is imported, and that
check has to see the nine real files rather than this stub.
"""

from __future__ import annotations

import sys
from importlib import import_module

_REAL = "satisfactory_mcp.interfaces.mcp.tools"

__all__ = [
    "factories",
    "gamedata",
    "harddrives",
    "planning",
    "progression",
    "prompts",
    "resources",
    "spatial",
    "world",
]

__path__ = list(import_module(_REAL).__path__)

for _name in __all__:
    _module = import_module(f"{_REAL}.{_name}")
    sys.modules[f"{__name__}.{_name}"] = _module
    globals()[_name] = _module

del _name, _module
