#!/usr/bin/env python3
"""Satisfactory Production Targets Generator.

Generates production targets for intermediate and tiered items based on Space Elevator
goals (default Phase 4), dynamically adjusting recipes and item demands depending
on unlocked alternate recipes and the 100% whole-machine doctrine.

Usage:
  python3 production_targets.py [--world NAME] [--save PATH] [--no-screws] [--sort {abc,tier}] [--list-unlocked] [--json]
"""

from __future__ import annotations

import argparse
import glob
import json
import math
import os
import re
import sys
from collections import defaultdict
from pathlib import Path

DEFAULT_DB_JSON = "/home/flynn/game-servers/ficsit-auditbot/data/state/db.json"
DEFAULT_HISTORY_DIR = "/home/flynn/game-servers/ficsit-auditbot/data/state/history"
DEFAULT_SERVER_SAVES = "/home/flynn/game-servers/satisfactory-server/config/saved/server"

# Definition of tiered items and their recipes
# (Rates are in items / minute @ 100% machine clock)
RECIPE_REGISTRY = {
    # Tier 8
    "Turbo Motor": {
        "Standard": {
            "machine": "Manufacturer",
            "output_rate": 1.875,
            "inputs": {"Cooling System": 7.5, "Radio Control Unit": 3.75, "Motor": 7.5, "Rubber": 45.0},
        },
        "Turbo Electric Motor": {
            "machine": "Manufacturer",
            "output_rate": 2.8125,
            "inputs": {"Motor": 6.5625, "Radio Control Unit": 8.4375, "Electromagnetic Control Rod": 4.6875, "Rotor": 6.5625},
        },
        "Turbo Pressure Motor": {
            "machine": "Manufacturer",
            "output_rate": 3.75,
            "inputs": {"Motor": 7.5, "Pressure Conversion Cube": 1.875, "Packaged Nitrogen Gas": 45.0, "Stator": 15.0},
        },
    },
    "Supercomputer": {
        "Standard": {
            "machine": "Manufacturer",
            "output_rate": 1.875,
            "inputs": {"Computer": 7.5, "AI Limiter": 3.75, "High-Speed Connector": 5.625, "Plastic": 52.5},
        },
        "Super-State Computer": {
            "machine": "Manufacturer",
            "output_rate": 2.4,
            "inputs": {"Computer": 7.2, "Electromagnetic Control Rod": 2.4, "Battery": 24.0, "Wire": 60.0},
        },
    },
    "Cooling System": {
        "Standard": {
            "machine": "Blender",
            "output_rate": 6.0,
            "inputs": {"Heat Sink": 12.0, "Rubber": 12.0, "Water": 30.0, "Nitrogen Gas": 150.0},
        },
    },
    "Fused Modular Frame": {
        "Standard": {
            "machine": "Blender",
            "output_rate": 1.5,
            "inputs": {"Heavy Modular Frame": 1.5, "Aluminum Casing": 75.0, "Nitrogen Gas": 37.5},
        },
    },
    "Pressure Conversion Cube": {
        "Standard": {
            "machine": "Assembler",
            "output_rate": 1.0,
            "inputs": {"Fused Modular Frame": 1.0, "Radio Control Unit": 2.0},
        },
    },
    "Modular Engine": {
        "Standard": {
            "machine": "Manufacturer",
            "output_rate": 1.0,
            "inputs": {"Motor": 2.0, "Rubber": 15.0, "Smart Plating": 2.0},
        },
    },
    "Adaptive Control Unit": {
        "Standard": {
            "machine": "Manufacturer",
            "output_rate": 1.0,
            "inputs": {"Automated Wiring": 5.0, "Circuit Board": 5.0, "Heavy Modular Frame": 1.0, "Computer": 2.0},
        },
    },

    # Tier 7
    "Heavy Modular Frame": {
        "Heavy Encased Frame": {
            "machine": "Manufacturer",
            "output_rate": 2.8125,
            "inputs": {"Modular Frame": 7.5, "Encased Industrial Beam": 9.375, "Steel Pipe": 33.75, "Concrete": 20.625},
        },
        "Standard": {
            "machine": "Manufacturer",
            "output_rate": 2.0,
            "inputs": {"Modular Frame": 10.0, "Steel Pipe": 40.0, "Encased Industrial Beam": 10.0, "Screws": 240.0},
        },
        "Heavy Flexible Frame": {
            "machine": "Manufacturer",
            "output_rate": 3.75,
            "inputs": {"Modular Frame": 18.75, "Encased Industrial Beam": 11.25, "Rubber": 75.0, "Screws": 390.0},
        },
    },
    "Radio Control Unit": {
        "Radio Control System": {
            "machine": "Manufacturer",
            "output_rate": 4.5,
            "inputs": {"Aluminum Casing": 90.0, "Crystal Oscillator": 1.5, "Circuit Board": 15.0, "Rubber": 45.0},
        },
        "Standard": {
            "machine": "Manufacturer",
            "output_rate": 2.5,
            "inputs": {"Aluminum Casing": 40.0, "Crystal Oscillator": 1.25, "Computer": 2.5},
        },
    },
    "Computer": {
        "Caterium Computer": {
            "machine": "Manufacturer",
            "output_rate": 3.75,
            "inputs": {"Circuit Board": 15.0, "Quickwire": 52.5, "Rubber": 22.5},
        },
        "Crystal Computer": {
            "machine": "Assembler",
            "output_rate": 3.3333,
            "inputs": {"Circuit Board": 5.0, "Crystal Oscillator": 1.6667},
        },
        "Standard": {
            "machine": "Manufacturer",
            "output_rate": 2.5,
            "inputs": {"Circuit Board": 10.0, "Cable": 20.0, "Plastic": 40.0},
        },
    },
    "High-Speed Connector": {
        "Silicon High-Speed Connector": {
            "machine": "Manufacturer",
            "output_rate": 3.0,
            "inputs": {"Quickwire": 90.0, "Silica": 37.5, "Circuit Board": 3.0},
        },
        "Standard": {
            "machine": "Manufacturer",
            "output_rate": 3.75,
            "inputs": {"Quickwire": 210.0, "Cable": 37.5, "Circuit Board": 3.75},
        },
    },

    # Tier 5-6
    "Motor": {
        "Electric Motor": {
            "machine": "Assembler",
            "output_rate": 7.5,
            "inputs": {"Electromagnetic Control Rod": 3.75, "Rotor": 7.5},
        },
        "Standard": {
            "machine": "Assembler",
            "output_rate": 5.0,
            "inputs": {"Rotor": 10.0, "Stator": 10.0},
        },
        "Rigor Motor": {
            "machine": "Manufacturer",
            "output_rate": 7.5,
            "inputs": {"Rotor": 3.75, "Stator": 3.75, "Crystal Oscillator": 1.25},
        },
    },
    "Heat Sink": {
        "Silicon Heat Sink": {
            "machine": "Assembler",
            "output_rate": 10.0,
            "inputs": {"Aluminum Casing": 60.0, "Silica": 60.0},
        },
        "Standard": {
            "machine": "Assembler",
            "output_rate": 7.5,
            "inputs": {"Alclad Aluminum Sheet": 37.5, "Copper Sheet": 22.5},
        },
    },
    "AI Limiter": {
        "Plastic AI Limiter": {
            "machine": "Assembler",
            "output_rate": 8.0,
            "inputs": {"Quickwire": 120.0, "Plastic": 28.0},
        },
        "Standard": {
            "machine": "Assembler",
            "output_rate": 5.0,
            "inputs": {"Copper Sheet": 25.0, "Quickwire": 100.0},
        },
    },
    "Circuit Board": {
        "Silicon Circuit Board": {
            "machine": "Assembler",
            "output_rate": 12.5,
            "inputs": {"Copper Sheet": 27.5, "Silica": 27.5},
        },
        "Caterium Circuit Board": {
            "machine": "Assembler",
            "output_rate": 8.75,
            "inputs": {"Plastic": 12.5, "Quickwire": 37.5},
        },
        "Standard": {
            "machine": "Assembler",
            "output_rate": 7.5,
            "inputs": {"Copper Sheet": 15.0, "Plastic": 30.0},
        },
        "Electrode Circuit Board": {
            "machine": "Assembler",
            "output_rate": 5.0,
            "inputs": {"Rubber": 20.0, "Petroleum Coke": 40.0},
        },
    },
    "Electromagnetic Control Rod": {
        "Electromagnetic Connection Rod": {
            "machine": "Assembler",
            "output_rate": 8.0,
            "inputs": {"Stator": 8.0, "High-Speed Connector": 4.0},
        },
        "Standard": {
            "machine": "Assembler",
            "output_rate": 4.0,
            "inputs": {"Stator": 6.0, "AI Limiter": 4.0},
        },
    },
    "Encased Industrial Beam": {
        "Encased Industrial Pipe": {
            "machine": "Assembler",
            "output_rate": 24.0,
            "inputs": {"Steel Pipe": 28.0, "Concrete": 20.0},
        },
        "Standard": {
            "machine": "Assembler",
            "output_rate": 6.0,
            "inputs": {"Steel Beam": 24.0, "Concrete": 30.0},
        },
    },
    "Crystal Oscillator": {
        "Insulated Crystal Oscillator": {
            "machine": "Manufacturer",
            "output_rate": 1.875,
            "inputs": {"Quartz Crystal": 18.75, "Rubber": 13.125, "AI Limiter": 1.875},
        },
        "Standard": {
            "machine": "Manufacturer",
            "output_rate": 1.0,
            "inputs": {"Quartz Crystal": 18.0, "Cable": 14.0, "Reinforced Iron Plate": 2.5},
        },
    },

    # Tier 3-4
    "Modular Frame": {
        "Steeled Frame": {
            "machine": "Assembler",
            "output_rate": 3.0,
            "inputs": {"Reinforced Iron Plate": 2.0, "Steel Pipe": 10.0},
        },
        "Standard": {
            "machine": "Assembler",
            "output_rate": 2.0,
            "inputs": {"Reinforced Iron Plate": 3.0, "Iron Rod": 12.0},
        },
        "Bolted Frame": {
            "machine": "Assembler",
            "output_rate": 5.0,
            "inputs": {"Reinforced Iron Plate": 7.5, "Screws": 140.0},
        },
    },
    "Reinforced Iron Plate": {
        "Stitched Iron Plate": {
            "machine": "Assembler",
            "output_rate": 5.625,
            "inputs": {"Iron Plate": 18.75, "Wire": 37.5},
        },
        "Adhered Iron Plate": {
            "machine": "Assembler",
            "output_rate": 3.75,
            "inputs": {"Iron Plate": 11.25, "Rubber": 3.75},
        },
        "Standard": {
            "machine": "Assembler",
            "output_rate": 5.0,
            "inputs": {"Iron Plate": 30.0, "Screws": 60.0},
        },
        "Bolted Iron Plate": {
            "machine": "Assembler",
            "output_rate": 15.0,
            "inputs": {"Iron Plate": 90.0, "Screws": 250.0},
        },
    },
    "Rotor": {
        "Steel Rotor": {
            "machine": "Assembler",
            "output_rate": 5.0,
            "inputs": {"Steel Pipe": 10.0, "Wire": 30.0},
        },
        "Copper Rotor": {
            "machine": "Assembler",
            "output_rate": 11.25,
            "inputs": {"Copper Sheet": 22.5, "Screws": 195.0},
        },
        "Standard": {
            "machine": "Assembler",
            "output_rate": 4.0,
            "inputs": {"Iron Rod": 20.0, "Screws": 100.0},
        },
    },
    "Stator": {
        "Quickwire Stator": {
            "machine": "Assembler",
            "output_rate": 8.0,
            "inputs": {"Steel Pipe": 16.0, "Quickwire": 60.0},
        },
        "Standard": {
            "machine": "Assembler",
            "output_rate": 5.0,
            "inputs": {"Steel Pipe": 15.0, "Wire": 40.0},
        },
    },
    "Smart Plating": {
        "Plastic Smart Plating": {
            "machine": "Assembler",
            "output_rate": 5.0,
            "inputs": {"Reinforced Iron Plate": 2.5, "Rotor": 2.5, "Plastic": 7.5},
        },
        "Standard": {
            "machine": "Assembler",
            "output_rate": 2.0,
            "inputs": {"Reinforced Iron Plate": 2.0, "Rotor": 2.0},
        },
    },
    "Versatile Framework": {
        "Standard": {
            "machine": "Assembler",
            "output_rate": 5.0,
            "inputs": {"Modular Frame": 2.5, "Steel Beam": 30.0},
        },
    },
    "Automated Wiring": {
        "Automated Speed Wiring": {
            "machine": "Manufacturer",
            "output_rate": 7.5,
            "inputs": {"Stator": 3.75, "Wire": 75.0, "High-Speed Connector": 1.875},
        },
        "Standard": {
            "machine": "Assembler",
            "output_rate": 2.5,
            "inputs": {"Stator": 2.5, "Cable": 50.0},
        },
    },
}

TIER_MAP = {
    "Turbo Motor": "Tier 8",
    "Supercomputer": "Tier 8",
    "Cooling System": "Tier 8",
    "Fused Modular Frame": "Tier 8",
    "Pressure Conversion Cube": "Tier 8",
    "Modular Engine": "Tier 8",
    "Adaptive Control Unit": "Tier 8",

    "Heavy Modular Frame": "Tier 7",
    "Radio Control Unit": "Tier 7",
    "Computer": "Tier 7",
    "High-Speed Connector": "Tier 7",

    "Motor": "Tier 5-6",
    "Heat Sink": "Tier 5-6",
    "AI Limiter": "Tier 5-6",
    "Circuit Board": "Tier 5-6",
    "Electromagnetic Control Rod": "Tier 5-6",
    "Encased Industrial Beam": "Tier 5-6",
    "Crystal Oscillator": "Tier 5-6",

    "Modular Frame": "Tier 3-4",
    "Reinforced Iron Plate": "Tier 3-4",
    "Rotor": "Tier 3-4",
    "Stator": "Tier 3-4",
    "Smart Plating": "Tier 3-4",
    "Versatile Framework": "Tier 3-4",
    "Automated Wiring": "Tier 3-4",
}


def load_live_unlocked_alternates(db_path: str = DEFAULT_DB_JSON) -> tuple[set[str], dict]:
    """Extract unlocked recipes directly from AuditBot live world state."""
    unlocked = set()
    meta = {}
    if os.path.exists(db_path):
        try:
            with open(db_path, "r", encoding="utf-8") as f:
                d = json.load(f)
            ws = d.get("lastWorldState", {})
            meta["saveName"] = ws.get("saveName", "unknown")
            meta["sessionName"] = ws.get("sessionName", "zinctoniaDos")
            meta["playDurationSeconds"] = ws.get("playDurationSeconds", 0)
            meta["playHours"] = round(meta["playDurationSeconds"] / 3600, 1)

            for s in ws.get("schematics", []):
                name = s.get("name", "")
                clean = re.sub(r"^Alternate:\s*", "", name).strip()
                unlocked.add(clean)
        except Exception as exc:
            meta["error"] = str(exc)

    return unlocked, meta


def pick_best_recipe(item_name: str, unlocked: set[str], no_screws: bool = True, overrides: dict[str, str] = None) -> tuple[str, dict]:
    """Select the best recipe for an item given unlocked alternates and doctrine."""
    overrides = overrides or {}
    available = RECIPE_REGISTRY.get(item_name, {})
    if not available:
        return ("Standard", {})

    if item_name in overrides and overrides[item_name] in available:
        return (overrides[item_name], available[overrides[item_name]])

    # Order of evaluation: alternates first if unlocked, standard as fallback
    for r_name, r_spec in available.items():
        if r_name == "Standard":
            continue
        # Check if this alternate is actually unlocked
        if r_name in unlocked:
            if no_screws and "Screws" in r_spec["inputs"]:
                continue
            return (r_name, r_spec)

    # If no valid alternate is unlocked, use standard
    std = available.get("Standard")
    if std:
        return ("Standard", std)

    first_key = next(iter(available))
    return (first_key, available[first_key])


def solve_phase4_targets(unlocked_alts: set[str], no_screws: bool = True, overrides: dict[str, str] = None) -> list[dict]:
    """Compute demand, whole-machine allocations, and surplus for all tiered items."""
    gross_demand = defaultdict(float)
    consumers = defaultdict(list)

    # Initial demands from Space Elevator Parts (Phase 4)
    gross_demand["Adaptive Control Unit"] += 1.5
    consumers["Adaptive Control Unit"].append("Assembly Director System (1.50)")

    gross_demand["Supercomputer"] += 0.75
    consumers["Supercomputer"].append("Assembly Director System (0.75)")

    gross_demand["Versatile Framework"] += 2.5
    consumers["Versatile Framework"].append("Magnetic Field Generator (2.50)")

    gross_demand["Electromagnetic Control Rod"] += 1.0
    consumers["Electromagnetic Control Rod"].append("Magnetic Field Generator (1.00)")

    gross_demand["Modular Engine"] += 2.5
    consumers["Modular Engine"].append("Thermal Propulsion Rocket (2.50)")

    gross_demand["Turbo Motor"] += 1.0
    consumers["Turbo Motor"].append("Thermal Propulsion Rocket (1.00)")

    gross_demand["Cooling System"] += 3.0
    consumers["Cooling System"].append("Thermal Propulsion Rocket (3.00)")

    gross_demand["Fused Modular Frame"] += 1.0
    consumers["Fused Modular Frame"].append("Thermal Propulsion Rocket (1.00)")

    gross_demand["Pressure Conversion Cube"] += 0.5
    consumers["Pressure Conversion Cube"].append("Nuclear Pasta (0.50)")

    order = [
        "Pressure Conversion Cube",
        "Turbo Motor",
        "Supercomputer",
        "Cooling System",
        "Fused Modular Frame",
        "Modular Engine",
        "Adaptive Control Unit",
        "Heavy Modular Frame",
        "Radio Control Unit",
        "Computer",
        "High-Speed Connector",
        "Motor",
        "Heat Sink",
        "AI Limiter",
        "Circuit Board",
        "Electromagnetic Control Rod",
        "Encased Industrial Beam",
        "Crystal Oscillator",
        "Modular Frame",
        "Reinforced Iron Plate",
        "Rotor",
        "Stator",
        "Smart Plating",
        "Versatile Framework",
        "Automated Wiring",
    ]

    results = []

    for item in order:
        demand = gross_demand[item]
        r_name, r_spec = pick_best_recipe(item, unlocked_alts, no_screws=no_screws, overrides=overrides)

        machine_type = r_spec["machine"]
        unit_rate = r_spec["output_rate"]
        machines = math.ceil(demand / unit_rate) if unit_rate > 0 else 0
        total_output = machines * unit_rate
        surplus = total_output - demand

        for input_item, input_rate in r_spec["inputs"].items():
            if input_item in RECIPE_REGISTRY:
                flow = input_rate * machines
                gross_demand[input_item] += flow
                consumers[input_item].append(f"{item} ({flow:.2f})")

        results.append({
            "name": item,
            "tier": TIER_MAP.get(item, "Tier 3-4"),
            "demand": demand,
            "output": total_output,
            "surplus": surplus,
            "machines": machines,
            "machine_type": machine_type,
            "recipe_used": r_name,
            "is_alternate": r_name != "Standard",
            "consumers": consumers[item],
        })

    return results


def main():
    parser = argparse.ArgumentParser(description="Satisfactory Production Targets Generator")
    parser.add_argument("--world", default="zinctoniaDos", help="World/session name")
    parser.add_argument("--db", default=DEFAULT_DB_JSON, help="Path to AuditBot live db.json")
    parser.add_argument("--no-screws", action="store_true", default=True, help="Enforce zero-screws doctrine")
    parser.add_argument("--allow-screws", dest="no_screws", action="store_false")
    parser.add_argument("--sort", choices=["abc", "tier"], default="abc", help="Sort order of output")
    parser.add_argument("--list-unlocked", action="store_true", help="List all unlocked alternate recipes from save")
    parser.add_argument("--unlocked", nargs="*", help="Manually inject unlocked alternates")
    parser.add_argument("--lock", nargs="*", help="Simulate a recipe being LOCKED (to see base standard)")
    parser.add_argument("--override", nargs="*", help="Recipe overrides (Item=RecipeName)")
    parser.add_argument("--json", action="store_true", help="Output raw JSON")

    args = parser.parse_args()

    unlocked, meta = load_live_unlocked_alternates(args.db)
    if args.unlocked:
        unlocked.update(args.unlocked)
    if args.lock:
        for lk in args.lock:
            unlocked.discard(lk)

    if args.list_unlocked:
        print(f"Unlocked Alternate Recipes on {meta.get('saveName', args.world)} ({meta.get('playHours', '?')}h playtime):")
        alt_candidates = []
        for cat in RECIPE_REGISTRY.values():
            for r in cat:
                if r != "Standard":
                    alt_candidates.append(r)
        for r in sorted(unlocked):
            is_relevant = " (Affects Tiered Targets)" if r in alt_candidates else ""
            print(f" • {r}{is_relevant}")
        return

    overrides = {}
    if args.override:
        for ov in args.override:
            if "=" in ov:
                k, v = ov.split("=", 1)
                overrides[k.strip()] = v.strip()

    targets = solve_phase4_targets(unlocked, no_screws=args.no_screws, overrides=overrides)

    if args.sort == "abc":
        targets.sort(key=lambda x: x["name"].lower())
    elif args.sort == "tier":
        tier_order = {"Tier 8": 0, "Tier 7": 1, "Tier 5-6": 2, "Tier 3-4": 3}
        targets.sort(key=lambda x: (tier_order.get(x["tier"], 9), x["name"].lower()))

    if args.json:
        print(json.dumps({"meta": meta, "targets": targets}, indent=2))
        return

    play_str = f"({meta['playHours']}h playtime)" if "playHours" in meta else ""
    save_str = meta.get("saveName", args.world)
    print(f"# Satisfactory Production Targets — World: {meta.get('sessionName', args.world)}")
    print(f"Live Save: `{save_str}` {play_str} | Doctrine: 100% Whole Machines | Zero-Screws: {args.no_screws}")
    alts_used = [t['recipe_used'] for t in targets if t['is_alternate']]
    print(f"Active Unlocked Alternates Applied ({len(alts_used)}): {', '.join(sorted(set(alts_used)))}\n")

    print("| Item Name | Minimum Demand | Target Output (100%) | Natural Surplus | Machines Allocated | Recipe Selected | Primary Consumers |")
    print("| :--- | :---: | :---: | :---: | :--- | :--- | :--- |")
    for t in targets:
        surplus_str = f"+{t['surplus']:.2f} / min" if t['surplus'] > 0.001 else "Exact"
        consumers_str = ", ".join(t["consumers"][:2]) + ("..." if len(t["consumers"]) > 2 else "")
        recipe_label = f"**{t['recipe_used']}**" if t['is_alternate'] else "Standard"
        print(f"| **{t['name']}** | {t['demand']:.2f} / min | **{t['output']:.2f} / min** | **{surplus_str}** | {t['machines']}× {t['machine_type']} | {recipe_label} | {consumers_str} |")


if __name__ == "__main__":
    main()
