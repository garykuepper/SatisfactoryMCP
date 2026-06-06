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

The second ratchet is ``FORBIDDEN_PATHS``, and it has inverted. For one commit the
eight pre-refactor import paths survived as alias shims so the test suite could be
moved over gradually; they are gone, every caller spells the layered home, and the
rule is now that those names must never come back. A folder whose only job is to
forward an import is a second place to look for the same code, and it re-attracts
logic the moment someone is in a hurry -- so ``test_the_old_paths_stay_deleted``
asserts absence rather than shape.

The third ratchet is the parser. ``src/pioneersav`` is a standalone library that
happens to live in this repository, and the subprocess boundary in front of it is
load-bearing for reasons that have nothing to do with layering -- crash isolation,
memory return, and a projection small enough to commit as the suite's fixture. Two
tests keep that true by import graph rather than by intention: the parser may not
know the application exists, and inside the application exactly one module may name
the parser, because everything else reaches it through the subprocess.
"""

from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
PKG = SRC / "satisfactory_mcp"

#: The parser package, and the one module in the application allowed to import it.
PARSER = "pioneersav"
PARSER_PKG = SRC / PARSER
PARSER_IMPORTER = "satisfactory_mcp.core.saveio.extract"

#: Longest-prefix-first layer map, applied to importer *and* target alike.
#:
#: Through the migration this also carried the pre-refactor paths, each classified
#: as the layer it was moving *to*, so the rules bit while things were still in
#: motion. Those entries are gone with the paths they described: an entry for a
#: name that no longer exists can never match an edge, so it says nothing and
#: quietly invites the next reader to believe the old layout is still around.
_LAYERS: tuple[tuple[str, str], ...] = (
    ("satisfactory_mcp.core", "core"),
    ("satisfactory_mcp.domain", "domain"),
    ("satisfactory_mcp.presenters", "presenters"),
    ("satisfactory_mcp.interfaces", "interfaces"),
    ("satisfactory_mcp.config", "core"),
    ("satisfactory_mcp.server", "interfaces"),
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

#: The pre-refactor import paths, deleted, with where each one went.
#:
#: They lived one commit longer than the code did, as alias shims, so that ~28 test
#: files could be moved over without a 2,000-line diff landing in the same change as
#: the moves. That job is done. Re-creating any of them -- as a package, as a module,
#: or as a directory with anything at all in it -- means the tree has two names for
#: one thing again, and the second name is always the one that goes stale.
FORBIDDEN_PATHS: dict[str, str] = {
    "docs": "core.gamedata",
    "save": "core.saveio + domain.world.state",
    "graph": "domain.factories",
    "spatial": "domain.spatial",
    "planning": "domain.planning",
    "tools": "interfaces.mcp.tools",
    "app": "interfaces.mcp.app",
    "render": "presenters.text.primitives",
}

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
#: and the package marker. Seven names, and a new one means someone started a
#: fifth top-level home instead of picking a layer.
ROOT_ENTRIES: frozenset[str] = frozenset(
    {
        "__init__.py",
        "config.py",
        "server.py",
        "core",
        "domain",
        "presenters",
        "interfaces",
    }
)

#: The presenter layer, by literal name rather than by layer lookup.
_PRESENTER_ROOTS = ("satisfactory_mcp.presenters",)


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


def _edges(root: Path = PKG) -> set[tuple[str, str]]:
    """Every (importer, target) module pair in the package, lazy imports included."""
    edges: set[tuple[str, str]] = set()
    for path in sorted(root.rglob("*.py")):
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

    Neither adding the shims nor deleting them needed an entry: a shim's only edges
    pointed at the package it forwarded to, and removing it removed both ends at
    once. Adding a line back here is allowed, but it now has to be argued for in a
    diff that touches this file, which is exactly the friction it is meant to have.
    """
    assert WHITELIST == frozenset(), (
        "the whitelist is meant to stay empty -- a new exemption needs a reason "
        f"written next to it:\n{_describe(set(WHITELIST))}"
    )


def test_domain_and_core_never_import_a_presenter():
    """The seam phase 1 built, named outright instead of implied by the table.

    ``test_no_new_violations`` already covers this, but only through ``_LAYERS``:
    re-pointing ``presenters`` at the domain layer in that table would silence it
    while the leak came straight back. This test spells the presenter package
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


def test_the_package_root_holds_only_the_layers():
    """No fifth top-level home. Pick a layer, or say why in ``ROOT_ENTRIES``."""
    found = {p.name for p in PKG.iterdir() if p.name != "__pycache__"}
    assert found == ROOT_ENTRIES, (
        "the package root drifted -- everything belongs in core, domain, "
        "presenters or interfaces:\n"
        f"  unexpected: {sorted(found - ROOT_ENTRIES)}\n"
        f"  missing:    {sorted(ROOT_ENTRIES - found)}"
    )


def test_the_parser_knows_nothing_about_the_application():
    """``pioneersav`` is a library, not a layer of this project.

    It reads a file format. Anything it learned about factories, plans or the MCP surface
    would be an import that has to be untangled again before it can be published or reused,
    so the rule is the strongest one available: zero edges pointing back this way.
    """
    leaks = {
        (importer, target)
        for importer, target in _edges(PARSER_PKG)
        if target == "satisfactory_mcp" or target.startswith("satisfactory_mcp.")
    }
    assert not leaks, (
        f"{PARSER} is a standalone library and must not import this application -- move "
        "whatever it needs into the caller:\n" + _describe(leaks)
    )


def test_only_the_extractor_imports_the_parser():
    """The subprocess boundary, stated as an import rule.

    The seam exists for crash isolation, for getting a 2.9 MB parse's memory back from the
    OS, and for a projection small enough to commit as the test fixture. None of that
    survives a convenience import: the first ``from pioneersav import ...`` anywhere else
    loads the parser into the server process and quietly deletes all three properties, with
    no test failing to say so. Exactly one module names it, and it is the one that runs in
    the child.
    """
    wrong = {
        (importer, target)
        for importer, target in _edges()
        if (target == PARSER or target.startswith(PARSER + ".")) and importer != PARSER_IMPORTER
    }
    assert not wrong, (
        f"the parser lives behind a subprocess -- only {PARSER_IMPORTER} may import "
        f"{PARSER}, everything else goes through core.saveio.projection:\n" + _describe(wrong)
    )


def test_the_old_paths_stay_deleted():
    """The inverted ratchet: a forwarding path is a defect, not a courtesy.

    While the shims stood, this test pinned their *shape* -- one alias ``__init__``,
    no def, no class -- because the failure to catch was logic creeping back into a
    path the architecture called dead. The paths are gone now, so the check gets to
    be the blunt one: neither ``docs/`` nor ``docs.py`` may exist at the package root
    under any of the eight old names. Re-adding one to spare a caller an import edit
    puts the tree back to having two names for one module, and the forwarding one is
    the one that stops being updated.
    """
    for name, moved_to in sorted(FORBIDDEN_PATHS.items()):
        for candidate in (PKG / name, PKG / f"{name}.py"):
            assert not candidate.exists(), (
                f"satisfactory_mcp.{name} is back -- it moved to {moved_to} and the old "
                "path is not a place code may live again; fix the caller's import instead"
            )
