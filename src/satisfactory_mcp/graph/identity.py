"""Candidate factories, from three signals that each fail alone.

Measured on a 316-hour save against the player's own list of what they built:

* **material components** -- 35 pieces, 19 of them fragments. Splits one Christmas
  factory into a Tree Branch line and a Candy Cane line. Too fine.
* **power islands** (towers removed) -- 6, one holding 476 machines. Separates
  outposts cleanly, does not subdivide the base at all. Too coarse.
* **spatial clustering alone** -- chains through shared infrastructure and merged an
  oil plant 900 m from the base into it. Wrong shape.

What works is the third signal the first two lack: **what a machine makes**. Steel
(50 machines, 95 m spread) and Tier 1&2 (54 machines, 197 m) are one belt-connected
mass topologically, but they sit 600 m apart and make different things. A grown-together
base defeats topology; it does not defeat geometry plus recipe.

So a *base* comes from power, a *line* from material, and a *cluster* from product and
position -- and they are offered as candidates rather than as an answer. Which grouping
is "a factory" is a naming decision, which is why labels attach to arbitrary machine
sets rather than to any one of these.
"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass, field

from ..docs.model import GameData
from .model import FactoryGraph

__all__ = ["Candidate", "bases", "describe", "lines_within", "product_clusters"]

#: Machines further apart than this were not built as one thing. 150 m comfortably
#: contains the measured steel site (95 m) and tier 1&2 (197 m spans two sub-rows).
CLUSTER_LINK_M = 150.0

#: Below this a "factory" is a stray machine or two, reported as fragments instead.
MIN_MACHINES = 3


@dataclass
class Candidate:
    """A proposed factory, with the evidence that produced it."""

    machines: list[str]
    source: str  # power | material | product
    products: Counter = field(default_factory=Counter)
    recipes: Counter = field(default_factory=Counter)
    buildings: Counter = field(default_factory=Counter)
    centroid: tuple[float, float] = (0.0, 0.0)
    spread_m: float = 0.0
    label: str | None = None

    @property
    def size(self) -> int:
        return len(self.machines)

    def name_hint(self) -> str:
        """A name from what it makes, since that is how a player refers to it.

        Falls back to the building mix for generators, which have no recipe at all --
        a coal plant would otherwise render as a blank.
        """
        if self.products:
            return " + ".join(n for n, _ in self.products.most_common(2))
        if self.buildings:
            top, count = self.buildings.most_common(1)[0]
            return f"{count}x {top.replace('Build_', '').replace('_C', '')}"
        return "unnamed"


def _positions(projection: dict) -> dict[str, tuple[float, float, float]]:
    out: dict[str, tuple[float, float, float]] = {}
    for key in ("machines", "extractors", "generators"):
        for record in projection.get(key, ()):
            pos = record.get("pos")
            if pos:
                out[record["instance"].rsplit(".", 1)[-1]] = tuple(pos)
    return out


def _recipes(projection: dict) -> dict[str, str]:
    return {
        r["instance"].rsplit(".", 1)[-1]: r["recipe"]
        for r in projection.get("machines", ())
        if r.get("recipe")
    }


def describe(
    machines: list[str],
    graph: FactoryGraph,
    game: GameData,
    projection: dict,
    source: str,
) -> Candidate:
    """Attach products, buildings and geometry to a set of machines."""
    pos = _positions(projection)
    rec = _recipes(projection)
    products: Counter = Counter()
    recipes: Counter = Counter()
    buildings: Counter = Counter()

    for m in machines:
        buildings[graph.cls.get(m, "?")] += 1
        rid = rec.get(m)
        recipe = game.recipes.get(rid or "")
        if recipe is None:
            continue
        recipes[recipe.name] += 1
        for flow in recipe.products[:1]:
            products[game.item_name(flow.item)] += 1

    pts = [pos[m][:2] for m in machines if m in pos]
    if pts:
        cx = sum(p[0] for p in pts) / len(pts)
        cy = sum(p[1] for p in pts) / len(pts)
        spread = max(
            (math.dist(a, b) for a in pts for b in pts), default=0.0
        ) / 100.0
    else:
        cx = cy = spread = 0.0

    return Candidate(
        machines=sorted(machines),
        source=source,
        products=products,
        recipes=recipes,
        buildings=buildings,
        centroid=(cx, cy),
        spread_m=spread,
    )


def bases(graph: FactoryGraph) -> list[list[str]]:
    """Power islands with the tower backbone removed.

    Towers carry no machines and their wires are an order of magnitude longer than
    pole wires, so they are transmission rather than structure. Dropping them
    separates outposts from the main base.
    """
    return graph.machine_components("power", skip=graph.towers())


def lines_within(graph: FactoryGraph, machines: list[str]) -> list[list[str]]:
    """Material components restricted to one base."""
    inside = set(machines)
    out = []
    for comp in graph.machine_components("material"):
        members = [m for m in comp if m in inside]
        if members:
            out.append(members)
    out.sort(key=len, reverse=True)
    return out


def _cluster(machines: list[str], pos: dict, link_m: float) -> list[list[str]]:
    """Single-linkage on XY. Cheap, and the sets here are small."""
    remaining = [m for m in machines if m in pos]
    out: list[list[str]] = []
    while remaining:
        group = [remaining.pop()]
        changed = True
        while changed:
            changed = False
            for cand in list(remaining):
                if any(
                    math.dist(pos[cand][:2], pos[m][:2]) <= link_m * 100 for m in group
                ):
                    group.append(cand)
                    remaining.remove(cand)
                    changed = True
        out.append(group)
    out.sort(key=len, reverse=True)
    return out


def product_clusters(
    graph: FactoryGraph,
    game: GameData,
    projection: dict,
    products: list[str],
    link_m: float = CLUSTER_LINK_M,
    within: list[str] | None = None,
) -> list[Candidate]:
    """Machines making any of ``products``, grouped by position.

    This is what recovers a factory buried inside a belt-connected base. Product
    alone over-collects -- 17 machines make Concrete, but 15 of them sit in the steel
    site making it for construction and only one is the player's "concrete setup".
    Position is what separates them.
    """
    pos = _positions(projection)
    rec = _recipes(projection)
    wanted = {p.casefold() for p in products}
    scope = set(within) if within is not None else None

    hits: list[str] = []
    for m, rid in rec.items():
        if scope is not None and m not in scope:
            continue
        recipe = game.recipes.get(rid)
        if recipe is None:
            continue
        if any(game.item_name(f.item).casefold() in wanted for f in recipe.products):
            hits.append(m)

    return [
        describe(group, graph, game, projection, "product")
        for group in _cluster(hits, pos, link_m)
    ]


def candidates(
    graph: FactoryGraph, game: GameData, projection: dict
) -> tuple[list[Candidate], list[Candidate]]:
    """Every base, and the lines inside each. Returns (bases, lines)."""
    base_cands: list[Candidate] = []
    line_cands: list[Candidate] = []
    for base in bases(graph):
        base_cands.append(describe(base, graph, game, projection, "power"))
        for line in lines_within(graph, base):
            if len(line) >= MIN_MACHINES:
                line_cands.append(describe(line, graph, game, projection, "material"))
    base_cands.sort(key=lambda c: -c.size)
    line_cands.sort(key=lambda c: -c.size)
    return base_cands, line_cands


def unassigned(graph: FactoryGraph, assigned: set[str]) -> list[str]:
    """Machines no label covers -- the answer to "what have I forgotten"."""
    return sorted(m for m in graph.machines() if m not in assigned)


def cluster_machines(machines: list[str], projection: dict, link_m: float = CLUSTER_LINK_M):
    """Public spatial grouping, for carving an arbitrary machine set."""
    return _cluster(machines, _positions(projection), link_m)
