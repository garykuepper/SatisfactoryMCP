"""Generate data/resource_nodes.json by merging two independent node datasets.

    uv run python tools/gen_resource_nodes.py

Node resource type and purity are NOT serialized in save files -- node actors carry
only ``mResourcesLeft`` and a transform -- so this static table is the only source.
It is map data, not save data, which is why it lives with the server rather than
coming out of the sidecar.

Two sources, because neither alone is sufficient:

* **MIT** (``data/world_resource_nodes.mit.json``) -- authoritative node SET. Derived
  from an FModel dump of ``Persistent_Level.umap``, i.e. from the game's own packaged
  assets, and MIT-licensed. Complete, but carries no satellite -> fracking-core link.
* **SCIM** (vendored ``sav_data/resourcePurity.py``) -- supplies the satellite -> core
  grouping, which the well-rate calculation needs. Carries no licence header and is
  pinned to game v1.2.0.0.

The merge cross-validates the two and prints the result. They agree on **purity for
every shared node** with a sub-centimetre position delta, which is strong mutual
corroboration. The merge also exists because SCIM is missing ``BP_ResourceNode11``, a
pure Limestone node that the live save proves exists (459 ``BP_ResourceNode_C`` actors
vs SCIM's 458). Konsl's world-generator notes a missing limestone node as a known
1.2-build discrepancy, consistent with SCIM's v1.2.0.0 pin.

Geysers are labelled ``Desc_Geyser_C`` here. Neither that nor the MIT dataset's
``Desc_GeothermalEnergy_C`` exists in Docs.json -- a geyser is not an item, it is a
placement target for the Geothermal Generator -- so the name is a synthetic label on
both sides and the difference is cosmetic.
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "sidecar" / "vendor" / "sat_sav_parse"))

from sav_data.resourcePurity import RESOURCE_PURITY

#: Save actors are keyed by this prefix; the MIT dataset stores bare ids.
INSTANCE_PREFIX = "Persistent_Level:PersistentLevel."

#: classPath -> our node kind.
#:
#: A well satellite yields half a plain node's rate AND needs a Pressurizer on its
#: parent core, so conflating the two overstates a field by 2x.
_KINDS = {
    "BP_ResourceNode_C": "node",
    "BP_FrackingSatellite_C": "well_sat",
    "BP_ResourceNodeGeyser_C": "geyser",
}

#: Excluded on purpose: a resource DEPOSIT is hand-mineable only -- no extractor can
#: be placed on it -- so including it would advertise capacity that cannot be built.
_EXCLUDED = {"BP_ResourceDeposit_C", "BP_FrackingCore_C"}

#: Geysers have no real item class; keep the label stable for downstream code.
_GEYSER_RESOURCE = "Desc_Geyser_C"


def load_mit() -> list[dict]:
    path = ROOT / "data" / "world_resource_nodes.mit.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload["nodes"]


def load_scim() -> dict[str, dict]:
    out: dict[str, dict] = {}
    for instance, (resource, purity, pos, core) in RESOURCE_PURITY.items():
        out[instance.rsplit(".", 1)[-1]] = {
            "resource": resource,
            "purity": purity.name.lower(),
            "pos": pos,
            "well_core": core or None,
        }
    return out


def main() -> int:
    mit = load_mit()
    scim = load_scim()

    nodes: list[dict] = []
    cores: dict[str, dict] = {}
    purity_mismatch: list[str] = []
    resource_mismatch: list[str] = []
    position_deltas: list[float] = []
    missing_core_link = 0

    for entry in mit:
        if entry["classPath"] == "BP_FrackingCore_C":
            cores[entry["id"]] = entry
        if entry["classPath"] in _EXCLUDED:
            continue
        kind = _KINDS.get(entry["classPath"])
        if kind is None:
            continue

        resource = _GEYSER_RESOURCE if kind == "geyser" else entry["resource"]
        purity = entry["purity"]
        ref = scim.get(entry["id"])

        if ref is not None:
            if ref["purity"] != purity:
                purity_mismatch.append(f"{entry['id']}: MIT {purity} vs SCIM {ref['purity']}")
            if kind != "geyser" and ref["resource"] != resource:
                resource_mismatch.append(f"{entry['id']}: MIT {resource} vs SCIM {ref['resource']}")
            position_deltas.append(
                math.dist((entry["x"], entry["y"]), (ref["pos"][0], ref["pos"][1]))
            )

        # The satellite -> core link exists only in SCIM.
        well_core = None
        if kind == "well_sat":
            well_core = ref["well_core"] if ref else None
            if well_core is None:
                missing_core_link += 1

        nodes.append(
            {
                "instance": INSTANCE_PREFIX + entry["id"],
                "resource": resource,
                "purity": purity,
                "kind": kind,
                "x": round(float(entry["x"]), 2),
                "y": round(float(entry["y"]), 2),
                "z": round(float(entry["z"]), 2),
                "well_core": well_core,
            }
        )

    nodes.sort(key=lambda n: n["instance"])
    mit_ids = {e["id"] for e in mit}
    scim_only = sorted(set(scim) - mit_ids)
    mit_only = sorted(
        e["id"] for e in mit if e["id"] not in scim and e["classPath"] not in _EXCLUDED
    )

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
                    "name": "rockfactory/satisfactory-logistics WorldResourceNodes.json",
                    "licence": "MIT",
                    "derivation": "FModel dump of Persistent_Level.umap (game assets)",
                    "role": "authoritative node set, resource, purity, position",
                },
                "secondary": {
                    "name": "sat_sav_parse sav_data/resourcePurity.py",
                    "licence": "none stated; data extracted from SCIM, a third party",
                    "game_version_pinned": "1.2.0.0",
                    "role": "satellite -> fracking core link only",
                },
            },
            "cross_validation": {
                "shared_nodes": len(position_deltas),
                "purity_mismatches": purity_mismatch,
                "resource_mismatches": resource_mismatch,
                "max_position_delta_cm": (
                    round(max(position_deltas), 3) if position_deltas else None
                ),
                "in_scim_not_in_game_assets": scim_only,
                "in_game_assets_not_in_scim": mit_only,
                "satellites_without_core_link": missing_core_link,
            },
            "excluded": {
                "BP_ResourceDeposit_C": "hand-mineable only, no extractor can be placed",
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
    print(
        f"cross-validation: {len(position_deltas)} shared, "
        f"{len(purity_mismatch)} purity mismatches, "
        f"{len(resource_mismatch)} resource mismatches, "
        f"max position delta {max(position_deltas):.2f} cm"
    )
    if mit_only:
        print(f"  recovered from game assets, absent from SCIM: {', '.join(mit_only)}")
    if scim_only:
        print(f"  in SCIM but not in game assets: {', '.join(scim_only)}")
    if missing_core_link:
        print(f"  WARNING: {missing_core_link} satellite(s) have no core link")
    for m in purity_mismatch + resource_mismatch:
        print("  MISMATCH:", m)

    oil = [n for n in nodes if n["resource"] == "Desc_LiquidOil_C"]
    pump = [n for n in oil if n["kind"] == "node"]
    print(
        f"crude oil: {len(oil)} total, {len(pump)} pumpable nodes, "
        f"{len(oil) - len(pump)} well satellites"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
