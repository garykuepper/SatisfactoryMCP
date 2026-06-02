"""The layering rules, enforced by reading the source rather than trusting review.

The refactor's whole point is a one-way import graph: core knows nothing, domain
knows core, presenters know domain, interfaces know everything. Nothing in that
list is checkable at runtime -- a lazy ``import`` three frames deep inside a
method body loads fine and violates the architecture silently. So this test
parses every module with ``ast`` and looks at *every* import node at *any*
depth, which is why ``save/state.py``'s deliberate lazy imports of the factories
and planning packages are visible here instead of hiding.

It runs with no game install and no save file: stdlib only.

``WHITELIST`` is a ratchet. It holds the violations that exist today, each one
dying in a named phase, and ``test_whitelist_is_not_stale`` fails once a fix
lands and the entry is left behind. Entries only ever get deleted.
"""

from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
PKG = SRC / "satisfactory_mcp"

#: Longest-prefix-first layer map, applied to importer *and* target alike.
#:
#: The old paths count as the layer they are moving *to*, not the layer they sit
#: in today -- that is what makes the test useful during the migration instead of
#: only after it. So ``docs`` is already core, the rest of ``save`` is domain, and
#: the three domain packages that still live at the top level count as domain.
#: ``save.projection`` needed a line of its own until it became ``core/saveio``
#: for real; now the literal prefix covers it and the entry is gone.
_LAYERS: tuple[tuple[str, str], ...] = (
    ("satisfactory_mcp.core", "core"),
    ("satisfactory_mcp.domain", "domain"),
    ("satisfactory_mcp.presenters", "presenters"),
    ("satisfactory_mcp.interfaces", "interfaces"),
    # Transitional: pre-refactor homes, mapped to their destination layer.
    ("satisfactory_mcp.docs", "core"),
    ("satisfactory_mcp.save", "domain"),
    ("satisfactory_mcp.graph", "domain"),
    ("satisfactory_mcp.spatial", "domain"),
    ("satisfactory_mcp.planning", "domain"),
    ("satisfactory_mcp.config", "core"),
    ("satisfactory_mcp.render", "presenters"),
    ("satisfactory_mcp.app", "interfaces"),
    ("satisfactory_mcp.server", "interfaces"),
    ("satisfactory_mcp.tools", "interfaces"),
)

#: Third-party packages that belong to the outside world. They form a pseudo-layer
#: so the rule "only the interface layer may see the SDK" is checkable the same way
#: as every other rule.
_SDK_ROOTS = frozenset({"mcp", "pydantic", "fastapi", "uvicorn", "starlette"})

#: Who may import whom. A layer always may import itself.
ALLOWED: dict[str, frozenset[str]] = {
    "core": frozenset({"core"}),
    "domain": frozenset({"domain", "core"}),
    "presenters": frozenset({"presenters", "domain", "core"}),
    "interfaces": frozenset({"interfaces", "presenters", "domain", "core", "sdk"}),
}

#: Empty, and it stays that way. It held five render leakages at the start of the
#: refactor -- the four planning modules the sore-point list named, plus
#: ``docs.search``, which reading the AST found -- and phase 1 moved every one of
#: them behind ``presenters.text``. The ratchet is kept rather than deleted: a new
#: violation now has to be argued for by adding a line here, which is the point.
WHITELIST: frozenset[tuple[str, str]] = frozenset()


def _layer(module: str) -> str | None:
    """The layer a dotted module belongs to, or None if the rules do not cover it.

    Bare ``satisfactory_mcp`` is uncovered on purpose: the top-level ``__init__``
    is a package marker, not a layer, and importing it says nothing.
    """
    if module.split(".", 1)[0] in _SDK_ROOTS:
        return "sdk"
    best: tuple[int, str] | None = None
    for prefix, layer in _LAYERS:
        covers = module == prefix or module.startswith(prefix + ".")
        if covers and (best is None or len(prefix) > best[0]):
            best = (len(prefix), layer)
    return None if best is None else best[1]


def _module_name(path: Path) -> str:
    """The dotted name a file under ``src/`` is imported as."""
    parts = path.relative_to(SRC).with_suffix("").parts
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _package_of(path: Path) -> str:
    """The package an import inside ``path`` is relative to."""
    name = _module_name(path)
    return name if path.name == "__init__.py" else name.rsplit(".", 1)[0]


def _is_module(dotted: str) -> bool:
    """True when the dotted name names a file on disk rather than a symbol.

    ``from .. import config, render`` cannot be told from ``from .. import clamp``
    by the syntax alone; only the filesystem knows. This is what turns the former
    into two module edges and drops the latter as a symbol.
    """
    base = SRC.joinpath(*dotted.split("."))
    return base.with_suffix(".py").is_file() or (base / "__init__.py").is_file()


def _targets(node: ast.Import | ast.ImportFrom, package: str) -> list[str]:
    """Every module this import node reaches, absolute and dotted."""
    if isinstance(node, ast.Import):
        return [alias.name for alias in node.names]

    if node.level:
        parts = package.split(".")
        parts = parts[: len(parts) - (node.level - 1)] if node.level > 1 else parts
        base = ".".join(parts)
        resolved = f"{base}.{node.module}" if node.module else base
    else:
        resolved = node.module or ""
    if not resolved:
        return []

    # The module imported from is itself an edge; each imported name is an edge
    # too, but only if it is a submodule rather than a symbol.
    found = [resolved]
    found.extend(
        f"{resolved}.{alias.name}"
        for alias in node.names
        if alias.name != "*" and _is_module(f"{resolved}.{alias.name}")
    )
    return found


def _edges() -> set[tuple[str, str]]:
    """Every (importer, target) module pair in the package, lazy imports included."""
    edges: set[tuple[str, str]] = set()
    for path in sorted(PKG.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        importer = _module_name(path)
        package = _package_of(path)
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                for target in _targets(node, package):
                    edges.add((importer, target))
    return edges


def _violations() -> set[tuple[str, str]]:
    """Edges that cross a layer boundary the wrong way."""
    bad: set[tuple[str, str]] = set()
    for importer, target in _edges():
        source_layer = _layer(importer)
        target_layer = _layer(target)
        if source_layer is None or target_layer is None:
            continue
        if target_layer not in ALLOWED[source_layer]:
            bad.add((importer, target))
    return bad


def _describe(pairs: set[tuple[str, str]]) -> str:
    return "\n".join(
        f"  {importer} ({_layer(importer)}) -> {target} ({_layer(target)})"
        for importer, target in sorted(pairs)
    )


def test_no_new_violations():
    """No module may reach across a layer boundary that is not already known."""
    new = _violations() - WHITELIST
    assert not new, (
        "import layering violated -- domain and core must not know presenters, "
        f"interfaces or the SDK:\n{_describe(new)}"
    )


def test_whitelist_is_not_stale():
    """A fixed violation must lose its whitelist entry, or the ratchet slips."""
    gone = WHITELIST - _violations()
    assert not gone, (
        f"stale whitelist entry -- the violation is gone, delete the entry:\n{_describe(gone)}"
    )
