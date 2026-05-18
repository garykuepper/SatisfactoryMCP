"""Answer questions about a named factory, or about any machine set.

One entry point rather than eight tools, because every question shares the same two
steps: resolve a set of machines, then read something off it. What differs is only the
projection taken.

Rates are **nameplate at the machine's saved clock**, never measured. A Foundry running
Solid Steel Ingot at 150% is reported at 1.5x its recipe rate whether or not it has ever
had iron. Uptime is a separate question that needs the productivity fields, and conflating
the two would make a starved factory look healthy.

The one derived view worth more than the rest is ``balance``: per-item production minus
consumption across the set. Its sign is the interesting part --

* **positive** -- surplus, so it leaves the factory or backs up
* **negative** -- has to be fed in from outside
* **zero** -- made and consumed internally, which is what a self-contained line looks like

That single table answers "what does the steel factory need and what does it give me",
which no per-machine listing does.
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from ..docs.model import GameData
from ..spatial import nodes as nodes_mod
from .model import FactoryGraph

__all__ = ["ASPECTS", "FactoryView", "build_view"]

#: What can be asked for. Kept explicit so an unknown aspect is an error with a list
#: rather than a silently empty answer.
ASPECTS = (
    "summary",
    "machines",
    "recipes",
    "buildings",
    "balance",
    "inputs",
    "outputs",
    "power",
    "nodes",
    "links",
    "issues",
)


@dataclass
class MachineRow:
    instance: str
    building: str
    recipe: str
    clock: float
    paused: bool
    pos: tuple[float, float, float]


@dataclass
class FactoryView:
    """Everything derivable from one machine set, computed once."""

    name: str
    machines: list[MachineRow] = field(default_factory=list)
    recipes: Counter = field(default_factory=Counter)
    buildings: Counter = field(default_factory=Counter)
    #: item -> {"produced": ipm, "consumed": ipm}. Net is produced - consumed.
    flows: dict[str, dict[str, float]] = field(default_factory=dict)
    draw_mw: float = 0.0
    generation_mw: float = 0.0
    #: (node instance, resource, purity, extractor class, clock, resources_left)
    nodes: list[tuple] = field(default_factory=list)
    #: other factory/label name -> how many of ITS machines this set can reach without
    #: passing through a third machine. Asymmetric on purpose: from a 15-machine copper
    #: setup you reach 16 tor-factory machines on the shared belt web, but walking back
    #: from the tor factory the first copper machine blocks the rest.
    links: Counter = field(default_factory=Counter)
    issues: list[str] = field(default_factory=list)
    centroid: tuple[float, float] = (0.0, 0.0)
    spread_m: float = 0.0

    @property
    def size(self) -> int:
        return len(self.machines)

    def net(self, item: str) -> float:
        f = self.flows.get(item, {})
        return f.get("produced", 0.0) - f.get("consumed", 0.0)

    def outputs(self, tol: float = 1e-6) -> list[tuple[str, float]]:
        """Items with a surplus: they leave, or they back up."""
        out = [(k, self.net(k)) for k in self.flows]
        return sorted([(k, v) for k, v in out if v > tol], key=lambda kv: -kv[1])

    def inputs(self, tol: float = 1e-6) -> list[tuple[str, float]]:
        """Items in deficit: they must be fed in from outside."""
        out = [(k, -self.net(k)) for k in self.flows]
        return sorted([(k, v) for k, v in out if v > tol], key=lambda kv: -kv[1])

    def internal(self, tol: float = 1e-6) -> list[tuple[str, float]]:
        """Made and consumed within the set -- the mark of a self-contained line."""
        out = []
        for k, f in self.flows.items():
            if abs(self.net(k)) <= tol and f.get("produced", 0.0) > tol:
                out.append((k, f["produced"]))
        return sorted(out, key=lambda kv: -kv[1])


def _short(instance: str) -> str:
    return instance.rsplit(".", 1)[-1]


def build_view(
    name: str,
    machines: list[str],
    graph: FactoryGraph,
    game: GameData,
    projection: dict,
    labels=None,
) -> FactoryView:
    """Compute every aspect of one machine set in a single pass over the projection."""
    wanted = set(machines)
    view = FactoryView(name=name)
    flows: dict[str, dict[str, float]] = defaultdict(lambda: {"produced": 0.0, "consumed": 0.0})

    # resources_left is all the SAVE knows about a node. Resource and purity come from
    # the node table, which is why an unresolved extractor reports "?" rather than
    # silently defaulting to normal purity and inflating its extraction rate.
    node_state = projection.get("node_state", {})
    try:
        node_table = nodes_mod.load_nodes().by_instance()
    except Exception:
        node_table = {}

    points: list[tuple[float, float]] = []

    for record in projection.get("machines", ()):
        short = _short(record["instance"])
        if short not in wanted:
            continue
        clock = float(record.get("clock") or 1.0)
        paused = bool(record.get("paused"))
        rid = record.get("recipe")
        recipe = game.recipes.get(rid or "")
        view.machines.append(
            MachineRow(
                instance=short,
                building=record.get("cls", "?"),
                recipe=recipe.name if recipe else "",
                clock=clock,
                paused=paused,
                pos=tuple(record.get("pos") or (0.0, 0.0, 0.0)),
            )
        )
        view.buildings[record.get("cls", "?")] += 1
        if record.get("pos"):
            points.append((record["pos"][0], record["pos"][1]))
        if recipe is None:
            if rid is None:
                view.issues.append(f"{short}: no recipe set, produces nothing")
            continue
        view.recipes[recipe.name] += 1
        if paused:
            view.issues.append(f"{short}: paused ({recipe.name})")
            continue
        for flow in recipe.products:
            flows[game.item_name(flow.item)]["produced"] += flow.per_min * clock
        for flow in recipe.ingredients:
            flows[game.item_name(flow.item)]["consumed"] += flow.per_min * clock
        if game.buildings.get(record.get("cls", "")) is None:
            # Silently contributing 0 MW would understate the whole factory's draw.
            view.issues.append(
                f"{short}: unknown building {record.get('cls')!r}, power not counted"
            )
        else:
            view.draw_mw += game.recipe_power_mw(recipe, clock)

    for record in projection.get("extractors", ()):
        short = _short(record["instance"])
        if short not in wanted:
            continue
        clock = float(record.get("clock") or 1.0)
        paused = bool(record.get("paused"))
        view.buildings[record.get("cls", "?")] += 1
        view.machines.append(
            MachineRow(
                instance=short,
                building=record.get("cls", "?"),
                recipe="",
                clock=clock,
                paused=paused,
                pos=tuple(record.get("pos") or (0.0, 0.0, 0.0)),
            )
        )
        if record.get("pos"):
            points.append((record["pos"][0], record["pos"][1]))
        building = game.buildings.get(record.get("cls", ""))
        node = record.get("node")
        state = node_state.get(node) or {}
        meta = node_table.get(node) or node_table.get(_short(node) if node else "") or {}
        purity = meta.get("purity") or ""
        resource = meta.get("resource") or ""
        view.nodes.append(
            (
                _short(node) if node else "(unresolved)",
                game.item_name(resource) if resource else "?",
                purity or "?",
                record.get("cls", "?"),
                clock,
                state.get("resources_left"),
            )
        )
        if paused:
            view.issues.append(f"{short}: paused extractor")
            continue
        if building is not None:
            view.draw_mw += building.power_at(clock)
            if resource and purity:
                flows[game.item_name(resource)]["produced"] += building.extract_rate(purity, clock)
            elif not node:
                view.issues.append(f"{short}: extractor bound to no node, output unknown")
            else:
                view.issues.append(
                    f"{short}: node {_short(node)} not in the node table, output unknown"
                )

    for record in projection.get("generators", ()):
        short = _short(record["instance"])
        if short not in wanted:
            continue
        clock = float(record.get("clock") or 1.0)
        view.buildings[record.get("cls", "?")] += 1
        view.machines.append(
            MachineRow(
                instance=short,
                building=record.get("cls", "?"),
                recipe="",
                clock=clock,
                paused=bool(record.get("paused")),
                pos=tuple(record.get("pos") or (0.0, 0.0, 0.0)),
            )
        )
        if record.get("pos"):
            points.append((record["pos"][0], record["pos"][1]))
        if record.get("paused"):
            view.issues.append(f"{short}: paused generator")
            continue
        building = game.buildings.get(record.get("cls", ""))
        if building is None:
            continue
        view.generation_mw += building.power_production_mw * clock
        fuel = game.items.get(record.get("fuel") or "")
        if fuel is not None:
            flows[fuel.name]["consumed"] += building.fuel_rate_per_min(fuel) * clock

    view.flows = dict(flows)

    if points:
        cx = sum(p[0] for p in points) / len(points)
        cy = sum(p[1] for p in points) / len(points)
        view.centroid = (cx, cy)
        view.spread_m = max((math.dist(a, b) for a in points for b in points), default=0.0) / 100.0

    # The factory's boundary. A material edge runs machine -> belt -> ... -> machine, so
    # walking only direct edges finds nothing; the walk has to pass THROUGH logistics and
    # stop at the first machine outside the set. Naming what sits there is what turns an
    # instance id into "feeds the steel factory".
    adjacency = graph.adjacency("material")
    seen: set[str] = set(wanted)
    frontier = [m for m in wanted if m in adjacency]
    while frontier:
        nxt: list[str] = []
        for node in frontier:
            for edge in adjacency.get(node, ()):
                other = edge.other(node)
                if other in seen:
                    continue
                seen.add(other)
                if graph.is_machine(other):
                    label = labels.label_for(other) if labels else None
                    view.links[label.name if label else "(unlabelled)"] += 1
                else:
                    nxt.append(other)  # keep going through belts, pipes and containers
        frontier = nxt

    return view
