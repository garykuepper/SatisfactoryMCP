"""Compatibility shim: the planner now lives at ``domain.planning``.

Nothing here is logic. Aliasing through ``sys.modules`` rather than re-exporting
names keeps module *identity*, so the old and the new dotted path are the same
module object -- which is what lets ``tests/test_layout.py`` monkeypatch
``planning.report``/``planning.layout_service``/``planning.diff_service`` and
``tests/test_plan_store.py`` monkeypatch ``planning.store`` through either name.
The alias list is name-complete rather than ``__all__``-shaped because the tests
import privates too (``_decks_for``, ``_index``, ``_depths``, ``_split``,
``_cycle_s``, ``_better``). New code imports the real paths directly.

The old ``__init__`` re-exported nothing, so neither does this one.
"""

from __future__ import annotations

import sys
from importlib import import_module

for _name in (
    "advisor",
    "bom",
    "byproducts",
    "carrier",
    "commission",
    "commission_service",
    "compare",
    "diff",
    "diff_service",
    "fit",
    "layout",
    "layout_service",
    "materials",
    "optimize",
    "prepare",
    "recall",
    "report",
    "scenario",
    "sensitivity",
    "sites",
    "slice",
    "store",
    "supply",
    "trunks",
):
    _module = import_module(f"satisfactory_mcp.domain.planning.{_name}")
    # Both halves are needed: the ``sys.modules`` entry satisfies
    # ``import satisfactory_mcp.planning.store``, the attribute satisfies
    # ``from satisfactory_mcp.planning import store`` without a filesystem lookup.
    sys.modules[f"{__name__}.{_name}"] = _module
    globals()[_name] = _module
del _name, _module
