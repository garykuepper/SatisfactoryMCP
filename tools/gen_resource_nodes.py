"""Generate data/resource_nodes.json from the first-party world node table.

    uv run python tools/gen_resource_nodes.py

Node resource type and purity are NOT serialized in save files -- node actors carry
only ``mResourcesLeft`` and a transform -- so this static table is the only source.
It is map data, not save data, which is why it lives with the server rather than
coming out of the sidecar.

One source: ``data/world_resource_nodes.json``, the game's own node actors read out of
the installed build's ``Persistent_Level.umap`` by ``tools/gen_world_resource_nodes.py``.
It carries the node set, resource, purity, position AND the satellite -> fracking-core
link -- the ``mCore`` ObjectProperty on every ``BP_FrackingSatellite`` export, a
reference in the shipped data rather than an inference -- so the two-source merge this
file used to perform is gone. The MIT-licensed table that supplied the node set is
retired and deleted; the record of what it was and how parity was proven when it went is
``_meta.retired_mit_table`` in the source file, and it stays there so the change is on
the record rather than tidied away. (Before that, the well grouping came from a GPL SCIM
table via a vendored parser; that is longer gone still. Nothing third-party reaches this
file any more, from either direction.)

This generator is a projection -- filter the classes, rename the fields, attach the well
link -- and a projection can rot in only two ways, so ``main`` measures both on every
run and dies rather than writing:

* **the well grouping**: re-derived geometrically from the positions and compared with
  the ``mCore`` link. Satellites sit in a tight ring around their core (median 34 m, max
  66 m) while the next-nearest core is at least 3.8x further, so the assignment is
  unambiguous -- and a satellite whose nearest core is not its recorded core means the
  table is stale or the world moved, both of which need a human;
* **the projection itself**: every emitted row is compared back to the source file --
  identity, resource, purity, and position to within this file's own 2-decimal rounding.
  The comparison is recorded under ``_meta.cross_validation.positions`` in the same
  block shape the skew gate in ``domain/spatial/nodes.py`` reads, and it shows this
  table exactly matching the installed build, which is what keeps that gate silent. When
  a game update lands, ``tools/gen_world_resource_nodes.py`` is the refresh, and its own
  build pin plus the per-kind save counts pinned in tests are what make skipping the
  refresh loud.

Geysers are labelled ``Desc_Geyser_C`` here. That name exists nowhere in Docs.json -- a
geyser is not an item, it is a placement target for the Geothermal Generator -- so the
label is synthetic and says so.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

#: Save actors are keyed by this prefix; the world table stores bare ids.
INSTANCE_PREFIX = "Persistent_Level:PersistentLevel."

#: class -> our node kind.
#:
#: A well satellite yields half a plain node's rate AND needs a Pressurizer on its
#: parent core, so conflating the two overstates a field by 2x.
_KINDS = {
    "BP_ResourceNode_C": "node",
    "BP_FrackingSatellite_C": "well_sat",
    "BP_ResourceNodeGeyser_C": "geyser",
}

#: Excluded on purpose: a fracking core produces nothing itself and is referenced as
#: ``well_core`` on its satellites. (Resource DEPOSITS never reach this generator at all:
#: the world table emits none, for the reason its ``_meta.deposits`` states -- they are
#: hand-mineable only, so a row would advertise capacity that cannot be built.)
_EXCLUDED = {"BP_FrackingCore_C"}

#: Geysers have no real item class; keep the label stable for downstream code.
_GEYSER_RESOURCE = "Desc_Geyser_C"

#: Purity vocabulary, asserted against the source rows so a renamed value cannot pass.
_PURITIES = {"impure", "normal", "pure"}

#: This file emits whole-hundredth centimetres -- ``round(v, 2)`` -- so a comparison
#: against the source's finer floats can never read below half a hundredth per axis.
#: Any delta at or under this is that rounding and nothing else.
ROUNDING_FLOOR_CM = math.sqrt(3) / 2 * 0.01


def load_world() -> tuple[list[dict], dict]:
    path = ROOT / "data" / "world_resource_nodes.json"
    if not path.is_file():
        raise SystemExit(
            f"{path} missing -- run: uv run --extra gen python tools/gen_world_resource_nodes.py"
        )
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload["nodes"], payload["_meta"]


def check_geometry(world: list[dict]) -> dict:
    """Re-derive the well grouping from positions and compare with the ``mCore`` link.

    Returns the distance distribution and the ambiguity margin. A satellite whose
    nearest core is not its recorded core is a hard error: either the table is stale
    or the world moved, and both need a human.
    """
    pos = {e["id"]: (e["x"], e["y"], e["z"]) for e in world}
    cores = [e["id"] for e in world if e["class"] == "BP_FrackingCore_C"]
    sat_core = {e["id"]: e["core"] for e in world if e["class"] == "BP_FrackingSatellite_C"}
    missing = sorted(s for s, c in sat_core.items() if not c)
    if missing:
        raise SystemExit(f"{len(missing)} satellite(s) carry no core link: {missing}")
    disagree: list[str] = []
    own: list[float] = []
    margins: list[float] = []

    for sat, core in sat_core.items():
        ranked = sorted(cores, key=lambda c: math.dist(pos[sat], pos[c]))
        d_own = math.dist(pos[sat], pos[core])
        own.append(d_own)
        rival = ranked[1] if ranked[0] == core else ranked[0]
        margins.append(math.dist(pos[sat], pos[rival]) / d_own)
        if ranked[0] != core:
            disagree.append(f"{sat}: mCore says {core}, nearest is {ranked[0]}")

    if disagree:
        for d in disagree:
            print("  GEOMETRY DISAGREES:", d)
        raise SystemExit(f"{len(disagree)} satellite(s) contradict the shipped mCore link")

    own.sort()
    return {
        "method": "nearest fracking core by position, vs the mCore link the game ships",
        # Equal by construction -- any disagreement raised above.
        "satellites_checked": len(own),
        "nearest_core_agrees": len(own),
        "distance_to_own_core_cm": {
            "min": round(own[0], 1),
            "median": round(own[len(own) // 2], 1),
            "max": round(own[-1], 1),
        },
        "runner_up_core_distance_ratio_min": round(min(margins), 2),
    }


def check_projection(nodes: list[dict], world: list[dict], world_meta: dict) -> dict:
    """Every emitted row against the source file: the projection cannot drift silently.

    The block is shaped the way ``domain/spatial/nodes.py`` reads a positions comparison,
    and what it records is the refreshed state that keeps the skew gate silent: this
    table IS the installed build, to within its own rounding, with no row missing on
    either side. The gate fires again the day a comparison block appears whose deltas
    are past its floor -- which is what a future re-measure against a newer build would
    add -- not from anything here.
    """
    by_id = {e["id"]: e for e in world if e["class"] not in _EXCLUDED}
    emitted = {n["instance"].removeprefix(INSTANCE_PREFIX): n for n in nodes}
    only_here = sorted(set(emitted) - set(by_id))
    only_source = sorted(set(by_id) - set(emitted))
    if only_here or only_source:
        raise SystemExit(
            f"projection and source disagree on the row set: {only_here} vs {only_source}"
        )
    deltas = []
    for name, row in emitted.items():
        src = by_id[name]
        if row["purity"] != src["purity"]:
            raise SystemExit(f"{name}: purity changed in projection")
        if row["resource"] != (src["resource"] or _GEYSER_RESOURCE):
            raise SystemExit(f"{name}: resource changed in projection")
        deltas.append(math.dist((row["x"], row["y"], row["z"]), (src["x"], src["y"], src["z"])))
    worst = max(deltas)
    if worst > ROUNDING_FLOOR_CM:
        raise SystemExit(f"projection moved a position by {worst} cm, past its own rounding")
    return {
        "measured": world_meta.get("generated"),
        "build": world_meta.get("game_version_pinned"),
        "method": (
            "every emitted row compared back to data/world_resource_nodes.json -- the "
            "installed build's own Persistent_Level.umap, read out of the IoStore "
            "container -- on identity, resource, purity and position, recomputed on "
            "every run of this generator"
        ),
        "rows_compared": len(deltas),
        "rows_only_in_this_table": [],
        "rows_only_in_the_installed_build": [],
        "max_position_delta_cm": round(worst, 4),
        "rounding_floor_cm": round(ROUNDING_FLOOR_CM, 4),
        "rows_past_the_rounding_floor": [],
    }


def main() -> int:
    world, world_meta = load_world()

    nodes: list[dict] = []
    cores: dict[str, dict] = {}
    unknown_purity: list[str] = []

    for entry in world:
        if entry["class"] == "BP_FrackingCore_C":
            cores[entry["id"]] = entry
        kind = _KINDS.get(entry["class"])
        if kind is None:
            if entry["class"] not in _EXCLUDED:
                raise SystemExit(f"{entry['id']}: unexpected class {entry['class']}")
            continue

        resource = _GEYSER_RESOURCE if kind == "geyser" else entry["resource"]
        purity = entry["purity"]
        if purity not in _PURITIES:
            unknown_purity.append(f"{entry['id']}: {purity!r}")

        nodes.append(
            {
                "instance": INSTANCE_PREFIX + entry["id"],
                "resource": resource,
                "purity": purity,
                "kind": kind,
                "x": round(float(entry["x"]), 2),
                "y": round(float(entry["y"]), 2),
                "z": round(float(entry["z"]), 2),
                "well_core": INSTANCE_PREFIX + entry["core"] if kind == "well_sat" else None,
            }
        )

    if unknown_purity:
        for u in unknown_purity:
            print("  UNKNOWN PURITY:", u)
        raise SystemExit(f"{len(unknown_purity)} row(s) carry a purity this table cannot speak")

    geometry = check_geometry(world)
    projection = check_projection(nodes, world, world_meta)

    nodes.sort(key=lambda n: n["instance"])
    by_kind: dict[str, int] = {}
    for n in nodes:
        by_kind[n["kind"]] = by_kind.get(n["kind"], 0) + 1

    out = {
        "_meta": {
            "description": "Static resource node table: type, purity, world position.",
            "why": (
                "Save files serialize only mResourcesLeft for node actors; resource "
                "type and purity are level data and must come from a static table."
            ),
            "sources": {
                "primary": {
                    "name": "data/world_resource_nodes.json",
                    "licence": (
                        "first-party; read from the installed game's own packaged map "
                        "data by tools/gen_world_resource_nodes.py -- no third-party "
                        "table, no external licence, no attribution obligation"
                    ),
                    "derivation": (
                        "node exports of the installed build's Persistent_Level.umap, "
                        "read out of the IoStore container: mResourceClass, mPurity, "
                        "mCore and the composed root-component transform"
                    ),
                    "role": (
                        "authoritative node set, resource, purity, position, and the "
                        "satellite -> fracking-core link"
                    ),
                    # Every source records which game build it was read at, so a reader
                    # can tell whether this table predates the build they are running.
                    "game_version_pinned": world_meta.get("game_version_pinned"),
                    "generated": world_meta.get("generated"),
                },
                "retired": (
                    "the MIT-licensed node table this file used to merge from, and the "
                    "GPL SCIM grouping before it, are both gone; the retirement record "
                    "and the parity that justified it are _meta.retired_mit_table in "
                    "data/world_resource_nodes.json"
                ),
            },
            "cross_validation": {
                "satellites": by_kind.get("well_sat", 0),
                "fracking_cores_referenced": len(cores),
                "geometry": geometry,
                # One comparison, because there is only one build in play: this table is
                # cut from the installed build, and the block records the projection
                # matching it exactly. The skew gate reads this shape and stays silent on
                # a table that matches; a re-measure against some future build would add
                # a second block here, and THAT is what would make it speak.
                "positions": {"against_the_installed_build": projection},
            },
            "excluded": {
                "BP_ResourceDeposit_C": (
                    "hand-mineable only, no extractor can be placed; the world table "
                    "emits no deposit rows in the first place"
                ),
                "BP_FrackingCore_C": (
                    "produces nothing itself; referenced as well_core on satellites"
                ),
            },
            "geyser_note": (
                "Desc_Geyser_C is a synthetic label. A geyser is not an item in "
                "Docs.json -- it is a placement target for the Geothermal Generator."
            ),
            "join_key": "instance (matches save actor instanceName exactly)",
            "count": len(nodes),
            "by_kind": by_kind,
            "fracking_cores": len(cores),
            "purity_multiplier": {"impure": 0.5, "normal": 1.0, "pure": 2.0},
            "units": "centimetres; north is -Y, east is +X, up is +Z",
        },
        "nodes": nodes,
    }

    dest = ROOT / "data" / "resource_nodes.json"
    dest.write_text(json.dumps(out, indent=1), encoding="utf-8")

    print(f"wrote {dest.relative_to(ROOT)}  {len(nodes)} nodes  {dest.stat().st_size} B")
    print("by kind:", by_kind)
    d = geometry["distance_to_own_core_cm"]
    print(
        f"well links: {geometry['satellites_checked']} satellites over {len(cores)} cores, "
        f"nearest-core agrees {geometry['nearest_core_agrees']}/{geometry['satellites_checked']}, "
        f"distance {d['min']:.0f}-{d['max']:.0f} cm (median {d['median']:.0f}), "
        f"runner-up at least {geometry['runner_up_core_distance_ratio_min']:.2f}x further"
    )
    print(
        f"projection: {projection['rows_compared']} rows match the installed build "
        f"({projection['build']}), max delta {projection['max_position_delta_cm']} cm, "
        f"floor {projection['rounding_floor_cm']} cm"
    )

    oil = [n for n in nodes if n["resource"] == "Desc_LiquidOil_C"]
    pump = [n for n in oil if n["kind"] == "node"]
    print(
        f"crude oil: {len(oil)} total, {len(pump)} pumpable nodes, "
        f"{len(oil) - len(pump)} well satellites"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
