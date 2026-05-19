"""Resource nodes: the static table, and which ones the save shows as tapped."""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache

from .. import config
from ..docs.model import GameData
from . import geo

__all__ = [
    "EXTRACTOR_FOR_KIND",
    "NodeTable",
    "blocking_buildings",
    "load_nodes",
    "occupancy",
]

#: Extractor class -> what it can tap. A well satellite needs a Well Extractor AND
#: a Pressurizer on its parent core, so it is not interchangeable with a plain node.
EXTRACTOR_FOR_KIND = {
    "node": ("Build_MinerMk1_C", "Build_MinerMk2_C", "Build_MinerMk3_C", "Build_OilPump_C"),
    "well_sat": ("Build_FrackingExtractor_C",),
    "geyser": (),
}

#: What a node needs BESIDES an extractor, and cannot work without. The Pressurizer
#: produces nothing itself, so it never appears in EXTRACTOR_FOR_KIND -- but with no
#: Pressurizer on the core every satellite of that well yields exactly zero.
EXTRA_FOR_KIND = {"well_sat": ("Build_FrackingSmasher_C",)}

#: A geyser is not extracted at all; it is a placement target for this generator.
GEYSER_CONSUMER = "Build_GeneratorGeoThermal_C"


@dataclass
class NodeTable:
    nodes: list[dict]
    meta: dict

    def __len__(self) -> int:
        return len(self.nodes)

    def by_resource(self, resource: str) -> list[dict]:
        return [n for n in self.nodes if n["resource"] == resource]

    def by_instance(self) -> dict[str, dict]:
        return {n["instance"]: n for n in self.nodes}

    def filter(
        self,
        resource: str | None = None,
        kind: str | None = None,
        purity: str | None = None,
        direction: str | None = None,
        origin: tuple[float, float] | None = None,
        half_angle: float = 60.0,
        center: tuple[float, float] | None = None,
        radius_m: float | None = None,
    ) -> list[dict]:
        out = self.nodes
        if resource:
            out = [n for n in out if n["resource"] == resource]
        if kind:
            out = [n for n in out if n["kind"] == kind]
        if purity:
            out = [n for n in out if n["purity"] == purity]
        if direction:
            out = [
                n for n in out if geo.in_direction(n["x"], n["y"], direction, origin, half_angle)
            ]
        if center is not None and radius_m is not None:
            out = [n for n in out if geo.distance_m((n["x"], n["y"]), center) <= radius_m]
        return list(out)


@lru_cache(maxsize=1)
def load_nodes() -> NodeTable:
    path = config.data_dir() / "resource_nodes.json"
    if not path.is_file():
        raise FileNotFoundError(f"{path} missing -- run: uv run python tools/gen_resource_nodes.py")
    payload = json.loads(path.read_text(encoding="utf-8"))
    return NodeTable(nodes=payload["nodes"], meta=payload.get("_meta", {}))


def node_rate(node: dict, game: GameData, extractor_cls: str | None = None) -> float:
    """Extraction rate for one node at 100% clock, in items/min or m3/min.

    Uses the best extractor the player could place unless one is named. Geysers have
    no extractor -- they are consumed by a Geothermal Generator instead.
    """
    kind = node["kind"]
    if kind == "geyser":
        return 0.0
    candidates = (extractor_cls,) if extractor_cls else EXTRACTOR_FOR_KIND.get(kind, ())
    best = 0.0
    for cls in candidates:
        b = game.buildings.get(cls)
        if b is None or not b.base_extract_rate:
            continue
        # FORM first. mAllowedResources is only populated when
        # mOnlyAllowCertainResources is True, which is False on every miner -- so
        # filtering on it alone left miners unrestricted and a Miner Mk.3 (base 240)
        # out-bid the Oil Extractor (base 120) on crude. Every oil node was reported at
        # DOUBLE its real rate: a pure node read 480/min where the pump gives 240.
        # mAllowedResourceForms is the field that actually encodes it: RF_SOLID on
        # miners, RF_LIQUID on the pumps.
        item = game.items.get(node["resource"])
        if b.allowed_forms and item is not None and item.form not in b.allowed_forms:
            continue
        if b.allowed_resources and node["resource"] not in b.allowed_resources:
            continue
        best = max(best, b.extract_rate(node["purity"]))
    return best


def occupancy(projection: dict) -> dict[str, dict]:
    """node instanceName -> the extractor sitting on it.

    Resolution is PARTIAL by design: across all extractors in the reference save
    only 40 of 66 resolve. Oil pumps are 13/13, but all water pumps point at
    FGWaterVolume objects which are not node keys, and three miners carry no
    mExtractableResource property at all. Callers must treat an absent link as
    unknown, never as free.
    """
    out: dict[str, dict] = {}
    for e in projection.get("extractors", ()):
        node = e.get("node")
        if not node:
            continue
        out[node] = {
            "extractor": e["cls"],
            "instance": e.get("instance"),
            "clock": e.get("clock", 1.0),
            "paused": e.get("paused", False),
            "pos": e.get("pos"),
        }
    return out


def unresolved_extractors(projection: dict) -> list[dict]:
    """Extractors whose node could not be resolved, so capacity is uncertain."""
    table = load_nodes().by_instance()
    out = []
    for e in projection.get("extractors", ()):
        node = e.get("node")
        if node is None:
            out.append({**e, "reason": "no mExtractableResource property"})
        elif node not in table:
            out.append({**e, "reason": f"target not a node ({node.rsplit('.', 1)[-1]})"})
    return out


def reachable(node: dict, unlocked_buildings: set[str] | None) -> bool:
    """Whether the player can actually exploit this node yet.

    Without this check a node table overstates available capacity by counting
    resource-well satellites that need a Pressurizer the player has not unlocked --
    1,080 of 5,040 m3/min of crude on the reference save.
    """
    if unlocked_buildings is None:
        return True
    kind = node["kind"]
    if kind == "geyser":
        return GEYSER_CONSUMER in unlocked_buildings
    if not set(EXTRA_FOR_KIND.get(kind, ())) <= unlocked_buildings:
        return False
    return any(cls in unlocked_buildings for cls in EXTRACTOR_FOR_KIND.get(kind, ()))


def _can_tap(building, resource: str, game: GameData) -> bool:
    """Whether this extractor could tap this resource, unlocks aside.

    Mirrors the rule build_scenario applies when it turns nodes into extractor
    columns, and must keep mirroring it: a building with no `mAllowedResourceForms`
    of its own is a solid miner, so a fluid node is not its to take.
    """
    if building is None or not building.base_extract_rate:
        return False
    if building.allowed_resources:
        return resource in building.allowed_resources
    item = game.items.get(resource)
    return item is not None and not item.is_fluid


def blocking_buildings(
    node: dict, game: GameData, unlocked_buildings: set[str] | None
) -> tuple[str, ...]:
    """Building classes that must be unlocked before this node yields anything.

    Empty when nothing is in the way. `reachable` is a bool, so it can only say a
    node is out of reach -- and "unreachable" is not an answer a player can act on,
    whereas "build a Resource Well Pressurizer" is.

    It is also strictly sharper than `reachable`, in one direction that matters:
    `reachable` asks whether any extractor of the right KIND is unlocked and never
    whether that extractor can tap this particular RESOURCE, so an unlocked Miner
    Mk2 makes a crude oil node read as reachable while nothing on the map can pump
    it. That gap is exactly where a plan goes infeasible with no explanation.
    """
    if unlocked_buildings is None:
        return ()
    kind = node["kind"]
    if kind == "geyser":
        return () if GEYSER_CONSUMER in unlocked_buildings else (GEYSER_CONSUMER,)
    options = tuple(
        cls
        for cls in EXTRACTOR_FOR_KIND.get(kind, ())
        if _can_tap(game.buildings.get(cls), node["resource"], game)
    )
    missing = () if any(cls in unlocked_buildings for cls in options) else options
    extra = tuple(c for c in EXTRA_FOR_KIND.get(kind, ()) if c not in unlocked_buildings)
    return missing + extra


def annotate(
    nodes: list[dict],
    game: GameData,
    projection: dict | None = None,
    unlocked_buildings: set[str] | None = None,
) -> list[dict]:
    """Attach rate, grid cell, tapped status and reachability to node rows."""
    occ = occupancy(projection) if projection else {}
    out = []
    for n in nodes:
        taken = occ.get(n["instance"])
        out.append(
            {
                **n,
                "rate": node_rate(n, game),
                "grid": geo.grid_cell(n["x"], n["y"]),
                "tapped": taken is not None,
                "tapped_by": taken["extractor"] if taken else None,
                "tapped_clock": taken["clock"] if taken else None,
                "reachable": reachable(n, unlocked_buildings),
            }
        )
    return out


def capacity(rows: list[dict], only_free: bool = False, only_reachable: bool = True) -> float:
    """Total rate across rows.

    Defaults to reachable nodes only, because unreachable capacity is not a plan.
    """
    total = 0.0
    for r in rows:
        if only_free and r.get("tapped"):
            continue
        if only_reachable and not r.get("reachable", True):
            continue
        total += r["rate"]
    return total
