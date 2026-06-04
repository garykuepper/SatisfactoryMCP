"""The layering rules, enforced by reading the source rather than trusting review.

The refactor's whole point is a one-way import graph: core knows nothing, domain
knows core, presenters know domain, interfaces know everything. Nothing in that
list is checkable at runtime -- a lazy ``import`` three frames deep inside a
method body loads fine and violates the architecture silently. So this test
parses every module with ``ast`` and looks at *every* import node at *any*
depth, which is why ``domain/world/state.py``'s deliberate lazy imports of the
factories and planning packages are visible here instead of hiding.

It runs with no game install and no save file: stdlib only.

``WHITELIST`` is a ratchet. It held the violations that existed at the start,
each one dying in a named phase, and ``test_whitelist_is_not_stale`` fails once a
fix lands and the entry is left behind. Entries only ever got deleted, and the
last one is gone: the refactor finished with an empty whitelist, which
``test_whitelist_is_empty`` now states outright.

The second ratchet is ``SHIM_MODULES``. Every pre-refactor import path still
resolves, because 28 test files and a decade of muscle memory spell the old
names -- but a shim is an alias and nothing else, and the danger is that an old
path quietly becomes a place code lives again. ``test_shims_are_frozen`` pins
each one to its known file set and proves it declares no function and no class.
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
#: only after it. ``save.projection`` needed a line of its own until it became
#: ``core/saveio`` for real; now the literal prefix covers it and the entry is gone.
#:
#: The migration is over, so the transitional names below are now exactly the
#: ``SHIM_MODULES`` set: each is an alias for its new home and is classified as the
#: layer it forwards to, which is what makes a shim's own imports legal. A line
#: stays until its old path is deleted outright, because the shim is still a
#: module the walker sees.
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

#: The pre-refactor import paths, frozen as compatibility shims.
#:
#: Six are packages holding nothing but an alias ``__init__`` that re-registers
#: each old submodule name against the module object at its new home; ``app`` and
#: ``render`` are single files holding an explicit re-import list. All eight are
#: aliases, so module *identity* survives and the monkeypatching in the test suite
#: still bites the module the server actually calls.
#:
#: They exist for callers that already spell the old name. Nothing new imports
#: them, and nothing may ever live *in* them again -- which is the whole point of
#: pinning the file set rather than trusting the docstrings that say so.
SHIM_MODULES: frozenset[str] = frozenset(
    {
        "satisfactory_mcp.docs",  # -> core.gamedata
        "satisfactory_mcp.save",  # -> core.saveio + domain.world.state
        "satisfactory_mcp.graph",  # -> domain.factories
        "satisfactory_mcp.spatial",  # -> domain.spatial
        "satisfactory_mcp.planning",  # -> domain.planning
        "satisfactory_mcp.tools",  # -> interfaces.mcp.tools
        "satisfactory_mcp.app",  # -> interfaces.mcp.app
        "satisfactory_mcp.render",  # -> presenters.text.primitives
    }
)

#: The homes the refactor moved things to, asserted to exist by literal name.
#:
#: Cheap, and it catches the one failure mode the edge walker cannot: a package
#: renamed or folded away leaves no violating edge behind, it just leaves the
#: architecture undescribed. ``core.text`` is a module rather than a package on
#: purpose -- it holds ``num`` and ``plural`` and nothing else, because those two
#: are the only formatting helpers the domain layer is allowed to reach.
LAYERED_HOMES: tuple[str, ...] = (
    "satisfactory_mcp.core.gamedata",
    "satisfactory_mcp.core.saveio",
    "satisfactory_mcp.core.text",
    "satisfactory_mcp.domain.world",
    "satisfactory_mcp.domain.progression",
    "satisfactory_mcp.domain.power",
    "satisfactory_mcp.domain.factories",
    "satisfactory_mcp.domain.spatial",
    "satisfactory_mcp.domain.collectibles",
    "satisfactory_mcp.domain.planning",
    "satisfactory_mcp.presenters.text",
    "satisfactory_mcp.interfaces.mcp",
    "satisfactory_mcp.interfaces.web",
)

#: Everything the package root is allowed to contain: the four layers, the two
#: real modules that stay at the top by decision (``server.py`` because the
#: console script names it, ``config.py`` because it is read from every layer),
#: the package marker, and the eight shims. A new name here means someone started
#: a ninth top-level home instead of picking a layer.
ROOT_ENTRIES: frozenset[str] = frozenset(
    {
        "__init__.py",
        "config.py",
        "server.py",
        "core",
        "domain",
        "presenters",
        "interfaces",
        "app.py",
        "render.py",
        "docs",
        "save",
        "graph",
        "spatial",
        "planning",
        "tools",
    }
)

#: The presenter layer, by literal name rather than by layer lookup.
_PRESENTER_ROOTS = ("satisfactory_mcp.presenters", "satisfactory_mcp.render")


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


def _path_of(dotted: str) -> Path:
    """Where a dotted name sits on disk, package directory or single file."""
    base = SRC.joinpath(*dotted.split("."))
    return base if (base / "__init__.py").is_file() else base.with_suffix(".py")


def _defined_names(path: Path) -> list[str]:
    """Every function and class a file declares, at any nesting depth."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return [
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    ]


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


def test_whitelist_is_empty():
    """The refactor ended with nothing exempted, and that is the resting state.

    Moving the shims into place needed no new entry -- a shim's only edges point
    at the package it forwards to, which ``_LAYERS`` classifies as the same layer
    the shim is mapped to, so every one of them is a legal same-layer import.
    Adding a line back here is allowed, but it now has to be argued for in a diff
    that touches this file, which is exactly the friction it is meant to have.
    """
    assert WHITELIST == frozenset(), (
        "the whitelist is meant to stay empty -- a new exemption needs a reason "
        f"written next to it:\n{_describe(set(WHITELIST))}"
    )


def test_domain_and_core_never_import_a_presenter():
    """The seam phase 1 built, named outright instead of implied by the table.

    ``test_no_new_violations`` already covers this, but only through ``_LAYERS``:
    re-pointing ``render`` at the domain layer in that table would silence it
    while the leak came straight back. This test spells the presenter modules
    literally, so the one rule the refactor existed for cannot be dissolved by
    editing a mapping.
    """
    leaks = {
        (importer, target)
        for importer, target in _edges()
        if _layer(importer) in {"core", "domain"}
        and any(target == root or target.startswith(root + ".") for root in _PRESENTER_ROOTS)
    }
    assert not leaks, (
        "domain and core must return data, never formatted text -- move the "
        f"formatting into presenters/text instead:\n{_describe(leaks)}"
    )


def test_the_layered_homes_exist():
    """Every destination the refactor names is a real package on disk."""
    missing = [name for name in LAYERED_HOMES if not _is_module(name)]
    assert not missing, (
        "the layered tree is incomplete -- these are the agreed homes and they "
        "have to exist:\n" + "\n".join(f"  {name}" for name in missing)
    )


def test_the_package_root_holds_only_the_layers_and_the_shims():
    """No ninth top-level home. Pick a layer, or say why in ``ROOT_ENTRIES``."""
    found = {p.name for p in PKG.iterdir() if p.name != "__pycache__"}
    assert found == ROOT_ENTRIES, (
        "the package root drifted -- everything belongs in core, domain, "
        "presenters or interfaces:\n"
        f"  unexpected: {sorted(found - ROOT_ENTRIES)}\n"
        f"  missing:    {sorted(ROOT_ENTRIES - found)}"
    )


def test_shims_are_frozen():
    """An old path is an alias and nothing more -- no file, no def, no class.

    A package shim is one ``__init__.py`` holding an alias loop; ``app`` and
    ``render`` are one file each holding a re-import list. Both shapes are pure
    name-binding, so a function or a class appearing in one means logic came back
    to a path the architecture says is dead. The file-set check catches the other
    half of the same mistake: a new module dropped into the old directory, where
    the layer rules would classify it by its forwarding destination and wave it
    through.
    """
    for dotted in sorted(SHIM_MODULES):
        path = _path_of(dotted)
        assert path.exists(), (
            f"{dotted}: the shim is gone -- delete its _LAYERS line and its "
            "SHIM_MODULES entry in the same commit that removes the old path"
        )
        if path.is_dir():
            found = sorted(
                p.relative_to(path).as_posix()
                for p in path.rglob("*.py")
                if "__pycache__" not in p.parts
            )
            assert found == ["__init__.py"], (
                f"{dotted}: new code at an old path -- a shim package is an alias "
                f"__init__ and nothing else, found {found}"
            )
            files = [path / "__init__.py"]
        else:
            files = [path]

        for file in files:
            declared = _defined_names(file)
            assert not declared, (
                f"{dotted}: a shim contains zero logic, but this one declares "
                f"{declared} -- move it to the real module and re-bind the name"
            )
