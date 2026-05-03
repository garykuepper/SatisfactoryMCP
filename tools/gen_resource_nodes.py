"""Generate data/resource_nodes.json from the vendored purity table.

Node resource type and purity are NOT serialized in save files -- node actors carry
only mResourcesLeft and a transform -- so this static table is the only source. It
is map data, not save data, so it lives with the server rather than coming out of
the sidecar.

Provenance matters here: the upstream table is SCIM-derived and version-pinned, so
the emitted file records that explicitly. Run this again if the table is refreshed.

    uv run python tools/gen_resource_nodes.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "sidecar" / "vendor" / "sat_sav_parse"))

from sav_data.resourcePurity import RESOURCE_PURITY

#: instanceName prefix -> node kind. A well satellite yields half a plain node's
#: rate and additionally needs a Pressurizer, so conflating them overstates a field
#: by 2x. Kind must come from the name, never inferred from a cluster neighbour.
_KINDS = (
    ("BP_FrackingSatellite", "well_sat"),
    ("BP_FrackingCore", "well_core"),
    ("BP_ResourceNodeGeyser", "geyser"),
    ("BP_ResourceNode", "node"),
)


def kind_of(instance_name: str) -> str:
    short = instance_name.rsplit(".", 1)[-1]
    for prefix, kind in _KINDS:
        if short.startswith(prefix):
            return kind
    return "unknown"


def main() -> int:
    nodes = []
    for instance, (resource, purity, pos, parent) in sorted(RESOURCE_PURITY.items()):
        nodes.append(
            {
                "instance": instance,
                "resource": resource,
                "purity": purity.name.lower(),
                "kind": kind_of(instance),
                "x": round(pos[0], 2),
                "y": round(pos[1], 2),
                "z": round(pos[2], 2),
                "well_core": parent or None,
            }
        )

    out = {
        "_meta": {
            "description": "Static resource node table: type, purity, world position.",
            "why": (
                "Save files serialize only mResourcesLeft for node actors; resource "
                "type and purity are level data and must come from a static table."
            ),
            "source": "sat_sav_parse/sav_data/resourcePurity.py",
            "source_provenance": (
                "Upstream header reads '# Extracted from SCIM for Satisfactory "
                "v1.2.0.0' -- data originates from Satisfactory-Calculator "
                "Interactive Map, a third party. No licence header present."
            ),
            "game_version_pinned": "1.2.0.0",
            "join_key": "instance (matches save actor instanceName exactly)",
            "count": len(nodes),
            "purity_multiplier": {"impure": 0.5, "normal": 1.0, "pure": 2.0},
            "units": "centimetres; north is -Y, east is +X, up is +Z",
        },
        "nodes": nodes,
    }

    dest = ROOT / "data" / "resource_nodes.json"
    dest.write_text(json.dumps(out, indent=1), encoding="utf-8")

    by_kind: dict[str, int] = {}
    for n in nodes:
        by_kind[n["kind"]] = by_kind.get(n["kind"], 0) + 1
    print(f"wrote {dest.relative_to(ROOT)}  {len(nodes)} nodes  {dest.stat().st_size} B")
    print("by kind:", by_kind)
    oil = [n for n in nodes if n["resource"] == "Desc_LiquidOil_C"]
    pump = [n for n in oil if n["kind"] == "node"]
    print(
        f"crude oil: {len(oil)} total, {len(pump)} pumpable nodes, {len(oil) - len(pump)} well satellites"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
