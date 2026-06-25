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

The fourth is the frontend, and it is a different shape of rule because it is a different
language. ``interfaces/web/frontend`` is an npm project and ``interfaces/web/static`` is
what ``npm run build`` writes; the built files are committed so that a fresh clone with no
Node still serves the map. Two things have to stay true for that arrangement to be honest:
the served files must be recognisable as OUTPUT (they carry a banner the build injects, and
a hand edit to them is lost on the next build, silently, which is the failure this catches),
and no Python may reach into the sources next door -- the seam is the built directory. Both
are read off the filesystem here, with no Node executed: pytest must keep running on a
machine that has none.

The fifth is the ``gen`` extra, and it is the ``_SDK_ROOTS`` rule again with different
names. ``ooz``, ``texture2ddecoder`` and Pillow are what ``core/gameassets`` needs to read
the installed game's container, they are an optional extra, and optional has to mean
optional *at import time*: a clone with none of them installed imports every module, runs
this suite and serves the map, and only ``tools/gen_*.py`` ever finds out they are missing.
That is checkable by AST only while nothing reaches for them dynamically, which is why the
second half of the rule -- no ``importlib``, no ``__import__``, no ``sys.path`` mutation
inside that package -- is not a separate preference but the thing that makes the first half
mean anything.

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
import sys
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

#: The ``gen`` extra: what the ``tools/gen_*.py`` generators need to read the installed
#: game's own container, pinned exactly in ``pyproject.toml`` because these decide the BYTES
#: an artifact is cut with. ``ooz`` (from pyooz) decompresses a container block,
#: ``texture2ddecoder`` unpacks a BC1 block and Pillow writes the PNG.
#:
#: The property below is that ``optional`` means optional AT IMPORT TIME -- the same posture
#: ``_SDK_ROOTS`` has, and checked the same way, because a machine with none of these
#: installed still has to import the whole package, run this suite and serve the map.
_GEN_EXTRA_ROOTS = frozenset({"ooz", "pyooz", "texture2ddecoder", "PIL"})

#: The one package allowed to name them at all, and only inside a function body.
GAMEASSETS = "satisfactory_mcp.core.gameassets"

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
    "satisfactory_mcp.core.gameassets",
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

#: The frontend's two halves: the npm project, and the directory its build writes -- which
#: is also the directory ``app.py`` mounts at ``/``.
WEB = PKG / "interfaces" / "web"
FRONTEND = WEB / "frontend"
BUILT = WEB / "static"

#: The string ``vite.config.ts`` injects into every file it emits. Asserting the marker
#: rather than the bytes is what keeps this test cheap and stable: the bundle is minified
#: output whose shape is the toolchain's business, and the only claim worth pinning is that
#: it came OUT of the toolchain rather than off someone's keyboard.
BUILD_MARKER = "GENERATED by Vite"

#: Every file the build is responsible for, and the whole contents of ``static/``: a name
#: here that stops being generated, or a file there that no build produced, both mean the
#: served directory has grown a hand-maintained corner again.
BUILT_FILES: tuple[str, ...] = ("index.html", "app.js", "app.css", "vendor/LEAFLET-LICENSE")

#: The one file in ``static/`` the marker cannot be asserted on. It is Leaflet's licence,
#: copied through verbatim by the build, and stamping a banner into a licence text would be
#: modifying the notice it exists to reproduce.
COPIED_VERBATIM = "vendor/LEAFLET-LICENSE"


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


def _sources(root: Path) -> list[Path]:
    """Every Python file under ``root`` that is this project's own.

    ``node_modules`` is skipped rather than trusted to hold no Python: it is an installed
    dependency tree, nothing here chose its contents, and a package that ships a ``.py``
    would otherwise be walked as if it were a module of this application.
    """
    return sorted(
        path
        for path in root.rglob("*.py")
        if "__pycache__" not in path.parts and "node_modules" not in path.parts
    )


def _edges(root: Path = PKG) -> set[tuple[str, str]]:
    """Every (importer, target) module pair in the package, lazy imports included."""
    edges: set[tuple[str, str]] = set()
    for path in _sources(root):
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


def _gameassets_sources() -> list[Path]:
    """Every module of the one package allowed to name the ``gen`` extra."""
    return _sources(PKG / "core" / "gameassets")


def _import_nodes(node: ast.AST, in_function: bool = False):
    """Every import in the tree, paired with whether a function body encloses it.

    ``ast.walk`` cannot answer that -- it flattens the tree -- and the whole distinction
    this file draws about the ``gen`` extra is between an import that runs at import time
    and one that runs when a generator calls the function.
    """
    for child in ast.iter_child_nodes(node):
        if isinstance(child, (ast.Import, ast.ImportFrom)):
            yield child, in_function
        deeper = in_function or isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
        yield from _import_nodes(child, deeper)


def _root(dotted: str) -> str:
    return dotted.split(".", 1)[0]


def test_the_gen_extra_is_optional_at_import_time():
    """``ooz``, ``texture2ddecoder`` and Pillow: named in one package, and only lazily.

    Two halves of one rule. Outside ``core/gameassets`` nothing in the application or the
    parser may name them at all -- that is what makes ``uv run satisfactory-mcp`` work on a
    machine that has never installed the extra. Inside it, they may be named only from a
    function body, because a module-scope ``import ooz`` turns a missing OPTIONAL dependency
    into an ``ImportError`` at collection time for every test that so much as touches the
    package, which is the failure this exists to prevent and not a theoretical one.
    """
    outside = {
        (importer, target)
        for importer, target in _edges() | _edges(PARSER_PKG)
        if _root(target) in _GEN_EXTRA_ROOTS
        and importer != GAMEASSETS
        and not importer.startswith(GAMEASSETS + ".")
    }
    assert not outside, (
        "the `gen` extra is generation-time only -- these modules would stop importing on a "
        f"machine that has not installed it, and only {GAMEASSETS} may name it:\n"
        + _describe(outside)
    )

    eager = []
    for path in _gameassets_sources():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node, in_function in _import_nodes(tree):
            if in_function:
                continue
            for target in _targets(node, _package_of(path)):
                if _root(target) in _GEN_EXTRA_ROOTS:
                    eager.append(f"  {_module_name(path)}:{node.lineno} imports {target}")
    assert not eager, (
        "these imports of the `gen` extra run at import time -- move them inside the "
        "function that needs them, the way `iostore.oodle_decompress` does:\n"
        + "\n".join(sorted(eager))
    )


def test_gameassets_never_imports_dynamically():
    """No ``importlib``, no ``__import__``, no ``sys.path`` -- so the rule above is readable.

    This is not a second preference dressed up as a test. ``test_the_gen_extra_is_optional``
    proves its point by parsing import statements, and every dynamic import is a hole in
    that proof: ``importlib.import_module("ooz")`` is invisible to it, and a ``sys.path``
    insert is how the decoders used to be reached -- out of a throwaway venv, at runtime,
    with the import graph saying nothing about it. Keeping all three out is what lets the
    AST be believed.
    """
    found = []
    for path in _gameassets_sources():
        name = _module_name(path)
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node, _in_function in _import_nodes(tree):
            for target in _targets(node, _package_of(path)):
                if _root(target) == "importlib":
                    found.append(f"  {name}:{node.lineno} imports {target}")
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and node.id == "__import__":
                found.append(f"  {name}:{node.lineno} calls __import__")
            if (
                isinstance(node, ast.Attribute)
                and node.attr == "path"
                and isinstance(node.value, ast.Name)
                and node.value.id == "sys"
            ):
                found.append(f"  {name}:{node.lineno} touches sys.path")
    assert not found, (
        "this package reaches its decoders through a parameter and its own imports, and "
        "nothing else -- a dynamic import here makes the layering unreadable rather than "
        "merely unusual:\n" + "\n".join(sorted(found))
    )


def test_gameassets_imports_nothing_but_the_stdlib_and_core():
    """The allowlist, stated positively: stdlib, ``core``/``config``, and the extra lazily.

    ``core`` may already import only ``core``, so most of this is implied -- but only most.
    The package exists to be read by ``tools/gen_*.py``, and the tempting import is the one
    that goes the other way: a generator's helper, a numpy convenience, a third-party format
    library pulled in because it is already installed for something else. Any of those would
    quietly make a *generation-time* dependency into a dependency of the server, which is
    exactly the shape of thing the ``gen`` extra was created to stop.
    """
    stray = []
    for path in _gameassets_sources():
        name = _module_name(path)
        for node, _in_function in _import_nodes(
            ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        ):
            for target in _targets(node, _package_of(path)):
                root = _root(target)
                allowed = (
                    root in sys.stdlib_module_names
                    or root in _GEN_EXTRA_ROOTS
                    or target == "satisfactory_mcp.config"
                    or target == "satisfactory_mcp.core"
                    or target.startswith("satisfactory_mcp.core.")
                )
                if not allowed:
                    stray.append(f"  {name}:{node.lineno} imports {target}")
    assert not stray, (
        "core/gameassets may import the standard library, satisfactory_mcp.core (and "
        "config), and the `gen` extra from inside a function -- nothing else, or reading "
        "the game's assets stops being something the server can be built without:\n"
        + "\n".join(sorted(stray))
    )


def test_the_served_page_is_build_output_and_nothing_else():
    """``static/`` is written by ``npm run build``, in full, every time.

    The bundle is committed so that ``uv run satisfactory-mcp-web`` works from a fresh clone
    on a machine with no Node -- and a committed build artifact is exactly the kind of file
    someone edits in place, because it is right there and the change appears to work. It
    does work, once: the next ``npm run build`` empties this directory and the edit is gone
    with no error anywhere. So every file here has to be one the build produces, and every
    file the build produces says so in its first line.
    """
    missing = [name for name in BUILT_FILES if not (BUILT / name).is_file()]
    assert not missing, (
        "the committed bundle is incomplete -- run `npm ci && npm run build` in "
        f"{FRONTEND.name}/:\n" + "\n".join(f"  static/{name}" for name in missing)
    )

    unmarked = [
        name
        for name in BUILT_FILES
        if name != COPIED_VERBATIM
        and BUILD_MARKER not in (BUILT / name).read_text(encoding="utf-8")[:400]
    ]
    assert not unmarked, (
        "these served files carry no build banner, so they were either hand-edited or "
        "written by something that is not the build -- the source is "
        f"{FRONTEND.name}/src:\n" + "\n".join(f"  static/{name}" for name in unmarked)
    )

    strays = {
        str(path.relative_to(BUILT)).replace("\\", "/")
        for path in BUILT.rglob("*")
        if path.is_file()
    } - set(BUILT_FILES)
    assert not strays, (
        "static/ is build output and holds nothing else -- put the source in "
        f"{FRONTEND.name}/ and let the build emit it, or add it to BUILT_FILES with a "
        "reason:\n" + "\n".join(f"  static/{name}" for name in sorted(strays))
    )


def test_the_frontend_sources_are_not_reachable_from_python():
    """The seam is the built directory, and it is the only seam.

    ``app.py`` mounts ``static/`` and that is the whole contract: whatever is inside came
    out of a build, and the Python has no opinion about how. A module that read a path
    inside ``frontend/`` -- to serve a ``.ts`` file, to parse ``package.json`` for a version,
    to find ``src/main.ts`` -- would make the npm project a runtime dependency of the server
    and put a machine with no ``node_modules`` one import away from a 500.
    """
    named = sorted(
        _module_name(path)
        for path in _sources(PKG)
        if "frontend" in path.read_text(encoding="utf-8")
    )
    assert not named, (
        "these modules name the frontend sources -- the server serves the BUILT directory "
        "and nothing reaches past it:\n" + "\n".join(f"  {name}" for name in named)
    )
    assert not _sources(FRONTEND), (
        "the npm project has grown Python -- it is a TypeScript build, and a .py file in "
        "it is either in the wrong tree or a build step that belongs in package.json:\n"
        + "\n".join(f"  {path}" for path in _sources(FRONTEND))
    )
