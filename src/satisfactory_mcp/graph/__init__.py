"""Compatibility shim: the factory graph now lives at ``domain.factories``.

Nothing here is logic. Aliasing through ``sys.modules`` rather than re-exporting
names keeps module *identity*, so ``satisfactory_mcp.graph.model.FactoryGraph``
and ``satisfactory_mcp.domain.factories.model.FactoryGraph`` are the same class --
isinstance and ``monkeypatch.setattr`` on either path keep working -- and it is
complete by construction, including the privates the tests reach for. New code
imports ``domain.factories`` directly; this exists for the test suite's import
lines, which spell every submodule in this package.

The re-export block below is the old ``__init__``'s own fifteen names, re-run
against the new home so ``from satisfactory_mcp.graph import FactoryGraph`` still
resolves.
"""

from __future__ import annotations

import sys
from importlib import import_module

from ..domain.factories import (
    Candidate,
    Edge,
    FactoryGraph,
    Label,
    LabelStore,
    Proposal,
    SelectorError,
    Slab,
    Structures,
    bases,
    build_graph,
    build_structures,
    describe,
    kind_of,
    lines_within,
    product_clusters,
    propose,
    select_machines,
)

for _name in (
    "build",
    "cohere",
    "health",
    "identity",
    "labels",
    "model",
    "query",
    "resolve",
    "select",
    "structure",
    "trace",
):
    _module = import_module(f"satisfactory_mcp.domain.factories.{_name}")
    # Both halves are needed: the ``sys.modules`` entry satisfies
    # ``import satisfactory_mcp.graph.model``, the attribute satisfies
    # ``from satisfactory_mcp.graph import model`` without a filesystem lookup.
    sys.modules[f"{__name__}.{_name}"] = _module
    globals()[_name] = _module
del _name, _module

__all__ = [
    "Candidate",
    "Edge",
    "FactoryGraph",
    "Label",
    "LabelStore",
    "Proposal",
    "SelectorError",
    "Slab",
    "Structures",
    "bases",
    "build_graph",
    "build_structures",
    "describe",
    "kind_of",
    "lines_within",
    "product_clusters",
    "propose",
    "select_machines",
]
