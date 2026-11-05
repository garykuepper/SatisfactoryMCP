#!/usr/bin/env python3
"""Show non-base (subassembly / composite) parts and their production targets vs live save metrics.

Filters out basic 1-ingredient Constructor parts (Plates, Wire, Cable, Concrete, Pipes, Beams)
and focuses on multi-input assembled components (Reinforced Iron Plates, Rotors, Stators,
Modular Frames, Motors, Encased Industrial Beams, Heavy Modular Frames, etc.) sized for
whole-machine 100% nominal clocking + natural Dimensional Depot surplus according to the
Satisfactory 1.0 Factory Design Doctrine.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

# Running from a checkout: put the repository's src/ on the path.
_src = Path(__file__).resolve().parents[3] / "src"
if _src.exists():
    sys.path.insert(0, str(_src))

try:
    from satisfactory_mcp.interfaces.mcp import app
except ImportError:
    app = None

# Default combined targets (Whole-machine 100% clocking + natural depot surplus)
# as established in the LFG Phase 4 integrated architecture:
CORE_TARGETS = {
    "Reinforced Iron Plate": {
        "target": 28.125,
        "machines": "5× Assembler",
        "recipe": "Stitched Iron Plate",
        "inputs": "Iron Plate + Wire",
        "building_type": "Assembler",
    },
    "Rotor": {
        "target": 30.0,
        "machines": "6× Assembler",
        "recipe": "Steel Rotor",
        "inputs": "Steel Pipe + Wire",
        "building_type": "Assembler",
    },
    "Stator": {
        "target": 30.0,
        "machines": "6× Assembler",
        "recipe": "Standard Stator",
        "inputs": "Steel Pipe + Wire",
        "building_type": "Assembler",
    },
    "Modular Frame": {
        "target": 21.0,
        "machines": "7× Assembler",
        "recipe": "Steeled Frame",
        "inputs": "Reinforced Iron Plate + Steel Pipe",
        "building_type": "Assembler",
    },
    "Motor": {
        "target": 15.0,
        "machines": "3× Assembler",
        "recipe": "Standard Motor",
        "inputs": "Rotor + Stator",
        "building_type": "Assembler",
    },
    "Encased Industrial Beam": {
        "target": 20.0,
        "machines": "5× Assembler",
        "recipe": "Encased Industrial Pipe",
        "inputs": "Steel Pipe + Concrete",
        "building_type": "Assembler",
    },
    "Heavy Modular Frame": {
        "target": 4.0,
        "machines": "2× Manufacturer",
        "recipe": "Heavy Modular Frame",
        "inputs": "Modular Frame + Encased Beam + Pipe + Concrete",
        "building_type": "Manufacturer",
    },
}

# Single-input basic items to exclude when auto-discovering items
BASE_ITEMS = {
    "Iron Ingot", "Copper Ingot", "Caterium Ingot", "Steel Ingot", "Aluminum Ingot",
    "Iron Plate", "Iron Rod", "Wire", "Cable", "Concrete", "Screws",
    "Steel Pipe", "Steel Beam", "Copper Sheet", "Quickwire", "Silica", "Quartz Crystal",
    "Limestone", "Iron Ore", "Copper Ore", "Coal", "Raw Quartz", "Caterium Ore", "Bauxite",
    "Sulfur", "Uranium", "Water", "Crude Oil", "Heavy Oil Residue", "Petroleum Coke", "Polymer Resin",
}


def analyze_world(world_name: str = "LFG", include_all: bool = False):
    if app is None:
        raise RuntimeError("satisfactory_mcp package could not be imported.")

    st = app.load_world(world=world_name)
    g = st.game

    nameplate = defaultdict(float)
    measured = defaultdict(float)
    machine_counts = defaultdict(int)
    idle_machine_counts = defaultdict(int)

    for m in st.projection.get("machines", []):
        rid = m.get("recipe")
        r = g.recipes.get(rid or "")
        if not r:
            continue
        factor = float(m.get("clock") or 1.0)
        up = m.get("uptime") or {}
        window = float(up.get("window_s") or 0)
        produced = float(up.get("produce_s") or 0)
        is_producing = bool(up.get("producing", False))
        util = (produced / window) if window > 0 else (1.0 if is_producing else 0.0)

        for prod in r.products:
            item_name = g.item_name(prod.item)
            rate = prod.per_min * factor
            nameplate[item_name] += rate
            measured[item_name] += rate * util
            machine_counts[item_name] += 1
            if util < 0.01:
                idle_machine_counts[item_name] += 1

    results = []

    # Decide which items to report
    items_to_check = set(CORE_TARGETS.keys())
    if include_all:
        for m in st.projection.get("machines", []):
            rid = m.get("recipe")
            r = g.recipes.get(rid or "")
            if not r:
                continue
            # A non-base item is anything produced by a recipe requiring >= 2 ingredients
            # or made in Assembler/Manufacturer/Blender
            if len(r.ingredients) >= 2:
                for prod in r.products:
                    inm = g.item_name(prod.item)
                    if inm not in BASE_ITEMS:
                        items_to_check.add(inm)

    for item in sorted(items_to_check):
        info = CORE_TARGETS.get(item, {})
        tgt = info.get("target")
        cur = nameplate.get(item, 0.0)
        act = measured.get(item, 0.0)
        gap = (cur - tgt) if tgt is not None else None
        machines = machine_counts.get(item, 0)
        idle = idle_machine_counts.get(item, 0)

        status = "OK"
        if tgt is not None and cur < tgt:
            status = "BELOW_TARGET"
        if cur > 0 and act < 0.01:
            status = "STARVED_OR_IDLE"
        elif cur == 0:
            status = "NOT_AUTOMATED"

        results.append({
            "item": item,
            "target": tgt,
            "configured_rate": round(cur, 3),
            "measured_5m_rate": round(act, 3),
            "gap": round(gap, 3) if gap is not None else None,
            "machines_configured": machines,
            "machines_idle": idle,
            "target_recipe": info.get("recipe", "Custom / Unspecified"),
            "target_machines": info.get("machines", "-"),
            "inputs": info.get("inputs", "-"),
            "status": status,
        })

    return results


def main():
    parser = argparse.ArgumentParser(
        description="Report non-base (subassembly) production targets vs actual save state."
    )
    parser.add_argument("--world", default="LFG", help="World / save name (default: LFG)")
    parser.add_argument("--all", action="store_true", help="Include all non-base items found in the world")
    parser.add_argument("--json", action="store_true", help="Output results in JSON format")

    args = parser.parse_args()

    try:
        data = analyze_world(world_name=args.world, include_all=args.all)
    except Exception as e:
        print(f"Error analyzing save '{args.world}': {e}", file=sys.stderr)
        sys.exit(1)

    if args.json:
        print(json.dumps(data, indent=2))
        return

    # Print markdown table
    print(f"# Non-Base (Subassembly) Production Targets — World: {args.world}\n")
    print("| Item | Target/min | Current/min | Last 5 min | Gap vs Target | Machines | Status | Inputs |")
    print("|---|---:|---:|---:|---:|---|---|---|")
    for row in data:
        tgt_str = f"{row['target']:.3f}" if row['target'] is not None else "-"
        gap_str = f"{row['gap']:+.3f}" if row['gap'] is not None else "-"
        m_str = f"{row['machines_configured']} configured"
        if row['machines_idle'] > 0:
            m_str += f" ({row['machines_idle']} idle)"

        print(
            f"| **{row['item']}** | {tgt_str} | {row['configured_rate']:.3f} | "
            f"{row['measured_5m_rate']:.3f} | {gap_str} | {m_str} | `{row['status']}` | {row['inputs']} |"
        )


if __name__ == "__main__":
    main()
