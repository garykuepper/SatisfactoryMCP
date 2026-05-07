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

__all__ = ["PlanRequest", "build_scenario", "match_recipes", "resolve_item"]

#: Extractors are tried best-first: the first one that can tap a node wins it.
_EXTRACTOR_PREFERENCE = (
    "Build_OilPump_C",
    "Build_MinerMk3_C",
    "Build_MinerMk2_C",
    "Build_MinerMk1_C",
)

#: Water comes from water volumes, not from the node table, so it is not node-limited.
#: Cap the extractor COUNT rather than the flow, so the burden stays visible.
_WATER_EXTRACTOR_CAP = 200


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
) -> PlanRequest:
    """Translate tool arguments into a Scenario, its node scope and a plan id."""
    export_ids = []
    for name in exports or [MW]:
        # Accept MW, mw, power, Power interchangeably: both words turn up in the
        # same conversation and neither is more correct.
        export_ids.append(
            MW
            if name == MW or str(name).strip().casefold() in ("mw", "power")
            else (resolve_item(game, name) or name)
        )
    minimums = {}
    for name, value in (export_minimums or {}).items():
        minimums[resolve_item(game, name) or name] = float(value)

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
    rows = nodes_mod.annotate(sel.nodes, game, state.projection, state.unlocked_building_ids)
    rows = [r for r in rows if r["reachable"]]
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
        ext[("Build_WaterPump_C", "Desc_Water_C", "normal")] = _WATER_EXTRACTOR_CAP

    recipes = [r.cls for r in state.unlocked_recipes("part")]
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

    for pattern in exclude_recipes or []:
        hits = match_recipes(game, pattern, recipes)
        if not hits:
            # Refuse quietly-wrong answers: a ban that matched nothing would return a
            # plan happily using the recipe the user meant to forbid.
            recipe_errors.append(f"exclude_recipes: nothing matches {pattern!r}")
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
    return PlanRequest(
        scenario=sc,
        selection=sel,
        node_rows=rows,
        plan_id=_plan_id(sc, only_free_nodes),
        excluded=sorted(set(excluded)),
        recipe_errors=recipe_errors,
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
