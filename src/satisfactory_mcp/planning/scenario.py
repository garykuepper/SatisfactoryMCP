"""One construction path from tool arguments to a solvable Scenario.

plan_factory, plan_layout and diff_vs_save must all describe the SAME factory for a
given set of arguments. Three copies of this translation would drift, and the drift
would be invisible: each tool would return a self-consistent answer about a slightly
different plant. So the translation lives here once and every planning tool calls it.

It also mints the ``plan_id``. The server keeps no state, so diff_vs_save re-solves
rather than taking a plan handle; the id is what makes that safe. It hashes the
arguments TOGETHER WITH the save-derived solve inputs -- the unlocked recipe set, the
extractor node census, the buildable set -- so two responses carrying the same id are
provably about the same plan, and a save that rotated and changed something relevant
shows up as a different id rather than as a silently different answer.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from ..docs.model import GameData
from ..spatial import nodes as nodes_mod
from ..spatial.select import Selection, select_nodes
from .optimize import MW, Scenario

if TYPE_CHECKING:  # pragma: no cover - import cycle only matters for type checkers
    from ..save.state import WorldState

__all__ = [
    "EXPORT_HELP",
    "PlanRequest",
    "build_scenario",
    "match_recipes",
    "resolve_item",
]

#: Quoted verbatim whenever an export token is refused. Both facts here cost a real
#: session four INFEASIBLE calls: the caller wrote "Power" expecting an alias (it is
#: one, and always was) and then assumed listing an item ADDED to the default.
EXPORT_HELP = (
    "exports takes item names or class ids, plus MW/mw/power/Power for grid output. "
    "It REPLACES the default [MW] rather than extending it -- list MW yourself to "
    "export power as well as items."
)

#: Extractors are tried best-first: the first one that can tap a node wins it.
_EXTRACTOR_PREFERENCE = (
    "Build_OilPump_C",
    "Build_MinerMk3_C",
    "Build_MinerMk2_C",
    "Build_MinerMk1_C",
)

from ..docs.constants import WATER_EXTRACTOR_CAP_ASSUMED


def resolve_item(game: GameData, query: str) -> str | None:
    """Resolve a display name or class id to an item id."""
    if query in game.items:
        return query
    q = query.casefold()
    exact = [c for c, i in game.items.items() if i.name.casefold() == q]
    if exact:
        return exact[0]
    partial = [c for c, i in game.items.items() if q in i.name.casefold()]
    return partial[0] if partial else None


def _export_token(game: GameData, name: str) -> tuple[str | None, str | None]:
    """Resolve one export token to an item id, or say why it cannot be.

    Returns ``(id, None)`` or ``(None, error)``. MW, mw, power and Power all mean the
    grid pseudo-item: both words turn up in the same conversation and neither is more
    correct.

    An UNRESOLVABLE token used to become the raw string, which then entered the LP as
    an item id that no process ever produces and no balance row can satisfy -- so the
    plan came back as a bare INFEASIBLE with nothing pointing at the typo. Naming the
    token is the same call as `recipe_errors`: a silently mangled export whitelist
    describes a different factory from the one that was asked for.
    """
    if name == MW or str(name).strip().casefold() in ("mw", "power"):
        return MW, None
    resolved = resolve_item(game, name)
    if resolved is None:
        return None, f"no item matches {name!r}"
    return resolved, None


def match_recipes(game: GameData, pattern: str, pool: list[str]) -> list[str]:
    """Resolve one recipe pattern against a pool of recipe ids.

    Matching is deliberately widening, in this order: exact class id, exact display
    name, then case-insensitive substring returning EVERY match. That is what makes
    "Recycled" drop both Recycled Plastic and Recycled Rubber in one go -- banning
    half a two-recipe loop would leave the loop intact and the ban useless.
    """
    if pattern in pool:
        return [pattern]
    q = pattern.strip().casefold()
    exact = [rid for rid in pool if game.recipes[rid].name.casefold() == q]
    if exact:
        return exact
    return [rid for rid in pool if q in game.recipes[rid].name.casefold()]


@dataclass
class PlanRequest:
    """Everything a planning tool needs, and everything diff_vs_save needs to match."""

    scenario: Scenario
    selection: Selection
    #: In-scope nodes annotated with tapped/tapped_by/reachable. The diff joins its
    #: extractor rows against these, which is the one exact machine match available.
    node_rows: list[dict]
    plan_id: str
    #: Recipes removed by exclude_recipes, and patterns that matched nothing. A
    #: silently ignored ban would produce a plan using the very recipe the user
    #: forbade, which is worse than refusing.
    excluded: list[str] = field(default_factory=list)
    recipe_errors: list[str] = field(default_factory=list)
    #: Export / export_minimum tokens that resolve to no item, same call as
    #: recipe_errors. The token is dropped rather than passed through, so the
    #: scenario stays solvable and the caller is told what was ignored.
    export_errors: list[str] = field(default_factory=list)
    #: Every in-scope node BEFORE the reachable/tapped filters, which is what makes
    #: "why can this plan not get Nitrogen Gas" answerable. `node_rows` cannot: it is
    #: the post-filter set the diff joins against, so an unreachable node is gone
    #: from it precisely when it is the interesting one.
    scoped_nodes: list[dict] = field(default_factory=list)
    only_free_nodes: bool = False


def build_scenario(
    game: GameData,
    state: WorldState,
    objective: str = "max_mw",
    target_item: str | None = None,
    sources: list[str] | None = None,
    exports: list[str] | None = None,
    export_minimums: dict[str, float] | None = None,
    only_free_nodes: bool = False,
    allow_sinks: bool = True,
    clocks: list[float] | None = None,
    extractor_clocks: list[float] | None = None,
    machine_cost_mw: float = 5.0,
    belt_ipm: float = 780.0,
    pipe_m3min: float = 600.0,
    exclude_recipes: list[str] | None = None,
    only_recipes: list[str] | None = None,
    water_extractors: int | None = None,
) -> PlanRequest:
    """Translate tool arguments into a Scenario, its node scope and a plan id.

    ``exports`` REPLACES the default ``[MW]``; it does not extend it. That is
    deliberate and load-bearing: `grid_import_mw` below is derived from the export
    set, so exporting MW also forbids drawing from the existing grid (a power plant
    that imports power to export it is unbounded). Auto-appending MW would therefore
    silently force every item plan to be self-powered, which is a different question
    from the one asked. `EXPORT_HELP` says so to the caller.
    """
    export_ids: list[str] = []
    export_errors: list[str] = []
    for name in exports or [MW]:
        resolved, err = _export_token(game, name)
        if resolved is None:
            export_errors.append(f"exports: {err}")
            continue
        export_ids.append(resolved)
    minimums = {}
    for name, value in (export_minimums or {}).items():
        # Same resolution as exports, aliases included: a minimum keyed "MW" that
        # never matched the power pseudo-item was a floor the LP silently ignored.
        resolved, err = _export_token(game, name)
        if resolved is None:
            export_errors.append(f"export_minimums: {err}")
            continue
        minimums[resolved] = float(value)

    table = nodes_mod.load_nodes()
    # The player position goes in as `player`, NOT as `origin`. origin would also
    # turn every direction selector into a cone from the player, so "north" would
    # quietly stop meaning the northern half of the map and start meaning "north of
    # where I am standing" -- a different question, and one that silently changed
    # every plan scoped by direction.
    here = state.player_position()
    sel = select_nodes(
        sources,
        table.nodes,
        resolve_resource=lambda q: resolve_item(game, q),
        player=(here[0], here[1]) if here else None,
    )
    scoped = nodes_mod.annotate(sel.nodes, game, state.projection, state.unlocked_building_ids)
    # Keep the pre-filter set: what got dropped here, and why, is the whole answer to
    # "this plan cannot get Nitrogen Gas".
    rows = [r for r in scoped if r["reachable"]]
    if only_free_nodes:
        rows = [r for r in rows if not r["tapped"]]

    ext: dict[tuple[str, str, str], int] = {}
    for r in rows:
        if r["kind"] != "node" or r["rate"] <= 0:
            continue
        for cls in _EXTRACTOR_PREFERENCE:
            b = game.buildings.get(cls)
            if b is None or cls not in state.unlocked_building_ids:
                continue
            if b.allowed_resources and r["resource"] not in b.allowed_resources:
                continue
            if not b.allowed_resources and game.items[r["resource"]].is_fluid:
                continue
            key = (cls, r["resource"], r["purity"])
            ext[key] = ext.get(key, 0) + 1
            break
    if "Build_WaterPump_C" in state.unlocked_building_ids:
        # Water has no nodes to count, so this is an ASSUMPTION standing in for
        # shoreline the model cannot see. A caller who has measured their platform
        # should override it; see WATER_EXTRACTOR_CAP_ASSUMED.
        ext[("Build_WaterPump_C", "Desc_Water_C", "normal")] = (
            int(water_extractors) if water_extractors else WATER_EXTRACTOR_CAP_ASSUMED
        )

    recipes = [r.cls for r in state.unlocked_recipes("part")]
    #: Kept for the miss check: a pattern that banned a recipe is not a miss even
    #: though `recipes` no longer contains it by the time processes are matched.
    all_recipes = list(recipes)
    excluded: list[str] = []
    recipe_errors: list[str] = []

    if only_recipes:
        keep: set[str] = set()
        for pattern in only_recipes:
            hits = match_recipes(game, pattern, recipes)
            if not hits:
                recipe_errors.append(f"only_recipes: nothing matches {pattern!r}")
            keep.update(hits)
        if keep:
            recipes = [rid for rid in recipes if rid in keep]

    # Patterns are matched against RECIPES first, then against the synthesised
    # processes. Generator burn and extraction are built from building data, not from
    # Docs.json, so they have no recipe to match -- "Coal-Powered Generator on Coal"
    # printed in the build table hit nothing at all, and the only recourse was dropping
    # rows by hand and hoping the subgraph was isolated.
    # EVERY pattern is offered to both. Recipe-first precedence looked tidier and was
    # wrong: "Coal" matches Biocoal/Charcoal/Compacted Coal, so under it the pattern
    # never reached the generators and "do not burn coal here" silently did the
    # opposite of what it said. Whatever is banned is listed back, so an over-broad
    # pattern is visible rather than surprising.
    pending_process_bans: list[str] = []
    for pattern in exclude_recipes or []:
        hits = match_recipes(game, pattern, recipes)
        pending_process_bans.append(pattern)
        if not hits:
            continue
        excluded.extend(game.recipes[rid].name for rid in hits)
        banned = set(hits)
        recipes = [rid for rid in recipes if rid not in banned]

    buildings = state.unlocked_building_ids
    sc = Scenario(
        game=game,
        recipes=recipes,
        objective=objective,
        target_item=resolve_item(game, target_item) if target_item else None,
        exports=tuple(export_ids),
        export_minimums=minimums,
        extractor_nodes=ext,
        allow_sinks=allow_sinks,
        clocks=tuple(clocks) if clocks else (1.0,),
        extractor_clocks=tuple(extractor_clocks) if extractor_clocks else None,
        machine_cost_mw=machine_cost_mw,
        belt_ipm=belt_ipm,
        pipe_m3min=pipe_m3min,
        buildings_available=buildings,
        # Without this the power row forces generation == consumption. Ignored when MW
        # is exported, since a power plant that imports power to export it is unbounded.
        grid_import_mw=None if MW in export_ids else 1e6,
    )

    if pending_process_bans:
        sc, process_hits, misses = _ban_processes(sc, pending_process_bans)
        excluded.extend(process_hits)
        matched_a_recipe = {
            pattern for pattern in pending_process_bans if match_recipes(game, pattern, all_recipes)
        }
        for pattern in [m for m in misses if m not in matched_a_recipe]:
            # Still refuse quietly-wrong answers: a ban matching neither a recipe nor a
            # process would return a plan using the very thing the user forbade.
            recipe_errors.append(f"exclude_recipes: nothing matches {pattern!r}")

    return PlanRequest(
        scenario=sc,
        selection=sel,
        node_rows=rows,
        plan_id=_plan_id(sc, only_free_nodes),
        excluded=sorted(set(excluded)),
        recipe_errors=recipe_errors,
        export_errors=export_errors,
        scoped_nodes=scoped,
        only_free_nodes=only_free_nodes,
    )


def _plan_id(sc: Scenario, only_free_nodes: bool) -> str:
    """Short hash over everything that can change the solve.

    Deliberately excludes the save's mtime: a rotating autosave that changed nothing
    relevant must yield the SAME id, or the id stops meaning "same plan" and starts
    meaning "same second".
    """
    payload = json.dumps(
        {
            "objective": sc.objective,
            "target_item": sc.target_item,
            "exports": sorted(sc.exports),
            "export_minimums": dict(sorted(sc.export_minimums.items())),
            "only_free_nodes": only_free_nodes,
            "allow_sinks": sc.allow_sinks,
            "clocks": list(sc.clocks),
            "extractor_clocks": list(sc.extractor_clocks or ()),
            "machine_cost_mw": sc.machine_cost_mw,
            "belt_ipm": sc.belt_ipm,
            "pipe_m3min": sc.pipe_m3min,
            "grid_import_mw": sc.grid_import_mw,
            "extractors": sorted(
                f"{k[0]}|{k[1]}|{k[2]}={v}" for k, v in sc.extractor_nodes.items()
            ),
            "recipes": sorted(sc.recipes),
            "buildings": sorted(sc.buildings_available or ()),
        },
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:8]


def _ban_processes(sc: Scenario, patterns: list[str]) -> tuple[Scenario, list[str], list[str]]:
    """Remove synthesised processes by name, and report which patterns hit nothing.

    Matched case-insensitively against the process LABEL exactly as the build table
    prints it ("Coal-Powered Generator on Coal"), its building name, and the item it
    consumes -- so "Coal-Powered Generator", "coal-powered generator on coal" and
    "Coal" all work, the last banning every generator that burns it.
    """
    from dataclasses import replace

    from .optimize import build_processes

    candidates = [p for p in build_processes(sc) if p.kind in ("generator", "extractor")]
    banned: set[str] = set()
    labels: list[str] = []
    misses: list[str] = []
    for pattern in patterns:
        needle = pattern.strip().casefold()
        hits = [
            p
            for p in candidates
            if needle in p.label.casefold()
            or needle == (sc.game.buildings[p.building].name.casefold()
                          if p.building in sc.game.buildings else "")
            or any(
                needle == sc.game.item_name(item).casefold()
                for item, rate in p.rates.items()
                if rate < 0
            )
        ]
        if not hits:
            misses.append(pattern)
            continue
        banned.update(p.pid for p in hits)
        labels.extend(sorted({p.label for p in hits}))
    return replace(sc, excluded_pids=frozenset(banned)), labels, misses
