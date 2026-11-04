#!/usr/bin/env python3
"""Satisfactory Production Targets Generator.

Generates production targets for intermediate and tiered items based on Space Elevator
goals (default Phase 4), dynamically adjusting recipes and item demands depending
on unlocked alternate recipes and the 100% whole-machine doctrine.

Usage:
  python3 production_targets.py [--world NAME] [--save PATH] [--no-screws] [--sort {abc,tier}] [--json]
"""

from __future__ import annotations

import argparse
import glob
import json
import math
import os
import re
import struct
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_SAVES_GLOB = "/home/flynn/game-servers/satisfactory-server/config/saved/server/*.sav"
DEFAULT_HISTORY_INDEX = "/home/flynn/game-servers/ficsit-auditbot/data/state/history/index.json"
DEFAULT_MODEL_DATA = "/home/flynn/satisfactory/data/derived/model_data.json"

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
        "Cooling Device": {
            "machine": "Production Amplifier",  # Special
            "output_rate": 6.0,
            "inputs": {"Heat Sink": 10.0, "Motor": 2.0, "Nitrogen Gas": 60.0},
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
        "Standard": {
            "machine": "Manufacturer",
            "output_rate": 2.0,
            "inputs": {"Modular Frame": 10.0, "Steel Pipe": 40.0, "Encased Industrial Beam": 10.0, "Screws": 240.0},
        },
        "Heavy Encased Frame": {
            "machine": "Manufacturer",
            "output_rate": 2.8125,
            "inputs": {"Modular Frame": 7.5, "Encased Industrial Beam": 9.375, "Steel Pipe": 33.75, "Concrete": 20.625},
        },
        "Heavy Flexible Frame": {
            "machine": "Manufacturer",
            "output_rate": 3.75,
            "inputs": {"Modular Frame": 18.75, "Encased Industrial Beam": 11.25, "Rubber": 75.0, "Screws": 390.0},
        },
    },
    "Radio Control Unit": {
        "Standard": {
            "machine": "Manufacturer",
            "output_rate": 2.5,
            "inputs": {"Aluminum Casing": 40.0, "Crystal Oscillator": 1.25, "Computer": 2.5},
        },
        "Radio Control System": {
            "machine": "Manufacturer",
            "output_rate": 4.5,
            "inputs": {"Aluminum Casing": 90.0, "Crystal Oscillator": 1.5, "Circuit Board": 15.0, "Rubber": 45.0},
        },
    },
    "Computer": {
        "Standard": {
            "machine": "Manufacturer",
            "output_rate": 2.5,
            "inputs": {"Circuit Board": 10.0, "Cable": 20.0, "Plastic": 40.0},
        },
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
    },
    "High-Speed Connector": {
        "Standard": {
            "machine": "Manufacturer",
            "output_rate": 3.75,
            "inputs": {"Quickwire": 210.0, "Cable": 37.5, "Circuit Board": 3.75},
        },
        "Silicon High-Speed Connector": {
            "machine": "Manufacturer",
            "output_rate": 3.0,
            "inputs": {"Quickwire": 90.0, "Silica": 37.5, "Circuit Board": 3.0},
        },
    },

    # Tier 5-6
    "Motor": {
        "Standard": {
            "machine": "Assembler",
            "output_rate": 5.0,
            "inputs": {"Rotor": 10.0, "Stator": 10.0},
        },
        "Electric Motor": {
            "machine": "Assembler",
            "output_rate": 7.5,
            "inputs": {"Electromagnetic Control Rod": 3.75, "Rotor": 7.5},
        },
        "Rigor Motor": {
            "machine": "Manufacturer",
            "output_rate": 7.5,
            "inputs": {"Rotor": 3.75, "Stator": 3.75, "Crystal Oscillator": 1.25},
        },
    },
    "Heat Sink": {
        "Standard": {
            "machine": "Assembler",
            "output_rate": 7.5,
            "inputs": {"Alclad Aluminum Sheet": 37.5, "Copper Sheet": 22.5},
        },
        "Silicon Heat Sink": {
            "machine": "Assembler",
            "output_rate": 10.0,
            "inputs": {"Aluminum Casing": 60.0, "Silica": 60.0},
        },
    },
    "AI Limiter": {
        "Standard": {
            "machine": "Assembler",
            "output_rate": 5.0,
            "inputs": {"Copper Sheet": 25.0, "Quickwire": 100.0},
        },
        "Plastic AI Limiter": {
            "machine": "Assembler",
            "output_rate": 8.0,
            "inputs": {"Quickwire": 120.0, "Plastic": 28.0},
        },
    },
    "Circuit Board": {
        "Standard": {
            "machine": "Assembler",
            "output_rate": 7.5,
            "inputs": {"Copper Sheet": 15.0, "Plastic": 30.0},
        },
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
        "Electrode Circuit Board": {
            "machine": "Assembler",
            "output_rate": 5.0,
            "inputs": {"Rubber": 20.0, "Petroleum Coke": 40.0},
        },
    },
    "Electromagnetic Control Rod": {
        "Standard": {
            "machine": "Assembler",
            "output_rate": 4.0,
            "inputs": {"Stator": 6.0, "AI Limiter": 4.0},
        },
        "Electromagnetic Connection Rod": {
            "machine": "Assembler",
            "output_rate": 8.0,
            "inputs": {"Stator": 8.0, "High-Speed Connector": 4.0},
        },
    },
    "Encased Industrial Beam": {
        "Standard": {
            "machine": "Assembler",
            "output_rate": 6.0,
            "inputs": {"Steel Beam": 24.0, "Concrete": 30.0},
        },
        "Encased Industrial Pipe": {
            "machine": "Assembler",
            "output_rate": 24.0,
            "inputs": {"Steel Pipe": 28.0, "Concrete": 20.0},
        },
    },
    "Crystal Oscillator": {
        "Standard": {
            "machine": "Manufacturer",
            "output_rate": 1.0,
            "inputs": {"Quartz Crystal": 18.0, "Cable": 14.0, "Reinforced Iron Plate": 2.5},
        },
        "Insulated Crystal Oscillator": {
            "machine": "Manufacturer",
            "output_rate": 1.875,
            "inputs": {"Quartz Crystal": 18.75, "Rubber": 13.125, "AI Limiter": 1.875},
        },
    },

    # Tier 3-4
    "Modular Frame": {
        "Standard": {
            "machine": "Assembler",
            "output_rate": 2.0,
            "inputs": {"Reinforced Iron Plate": 3.0, "Iron Rod": 12.0},
        },
        "Steeled Frame": {
            "machine": "Assembler",
            "output_rate": 3.0,
            "inputs": {"Reinforced Iron Plate": 2.0, "Steel Pipe": 10.0},
        },
        "Bolted Frame": {
            "machine": "Assembler",
            "output_rate": 5.0,
            "inputs": {"Reinforced Iron Plate": 7.5, "Screws": 140.0},
        },
    },
    "Reinforced Iron Plate": {
        "Standard": {
            "machine": "Assembler",
            "output_rate": 5.0,
            "inputs": {"Iron Plate": 30.0, "Screws": 60.0},
        },
        "Stitched Iron Plate": {
            "machine": "Assembler",
            "output_rate": 5.625,
            "inputs": {"Iron Plate": 18.75, "Wire": 37.5},
        },
        "Bolted Iron Plate": {
            "machine": "Assembler",
            "output_rate": 15.0,
            "inputs": {"Iron Plate": 90.0, "Screws": 250.0},
        },
        "Adhered Iron Plate": {
            "machine": "Assembler",
            "output_rate": 3.75,
            "inputs": {"Iron Plate": 11.25, "Rubber": 3.75},
        },
    },
    "Rotor": {
        "Standard": {
            "machine": "Assembler",
            "output_rate": 4.0,
            "inputs": {"Iron Rod": 20.0, "Screws": 100.0},
        },
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
    },
    "Stator": {
        "Standard": {
            "machine": "Assembler",
            "output_rate": 5.0,
            "inputs": {"Steel Pipe": 15.0, "Wire": 40.0},
        },
        "Quickwire Stator": {
            "machine": "Assembler",
            "output_rate": 8.0,
            "inputs": {"Steel Pipe": 16.0, "Quickwire": 60.0},
        },
    },
    "Smart Plating": {
        "Standard": {
            "machine": "Assembler",
            "output_rate": 2.0,
            "inputs": {"Reinforced Iron Plate": 2.0, "Rotor": 2.0},
        },
        "Plastic Smart Plating": {
            "machine": "Assembler",
            "output_rate": 5.0,
            "inputs": {"Reinforced Iron Plate": 2.5, "Rotor": 2.5, "Plastic": 7.5},
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
        "Standard": {
            "machine": "Assembler",
            "output_rate": 2.5,
            "inputs": {"Stator": 2.5, "Cable": 50.0},
        },
        "Automated Speed Wiring": {
            "machine": "Manufacturer",
            "output_rate": 7.5,
            "inputs": {"Stator": 3.75, "Wire": 75.0, "High-Speed Connector": 1.875},
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


def scan_unlocked_alternates_from_save(save_path: str) -> set[str]:
    """Extract unlocked alternate recipe names from save file or history."""
    unlocked = set()
    # 1. Check known history metadata first if available
    plan_meta_glob = "/home/flynn/satisfactory/plans/*/*/meta.json"
    for p in glob.glob(plan_meta_glob):
        try:
            with open(p) as f:
                d = json.load(f)
                unlocked.update(d.get("unlocked_alternates", []))
        except Exception:
            pass

    # 2. Add defaults that exist in this world
    unlocked.update([
        "Steeled Frame",
        "Stitched Iron Plate",
        "Steel Rotor",
        "Encased Industrial Pipe",
        "Silicon High-Speed Connector",
        "Plastic AI Limiter",
        "Solid Steel Ingot",
        "Heavy Flexible Frame",
        "Fine Concrete",
        "Wet Concrete",
    ])
    return unlocked


def pick_best_recipe(item_name: str, unlocked: set[str], no_screws: bool = True, overrides: dict[str, str] = None) -> tuple[str, dict]:
    """Select the best recipe for an item given unlocked alternates and doctrine."""
    overrides = overrides or {}
    available = RECIPE_REGISTRY.get(item_name, {})
    if not available:
        return ("Standard", {})

    if item_name in overrides and overrides[item_name] in available:
        return (overrides[item_name], available[overrides[item_name]])

    # Candidate ranking preferences
    preference_order = []
    if item_name == "Radio Control Unit":
        preference_order = ["Radio Control System", "Standard"]
    elif item_name == "Turbo Motor":
        preference_order = ["Standard", "Turbo Electric Motor", "Turbo Pressure Motor"]
    elif item_name == "Heavy Modular Frame":
        preference_order = ["Heavy Encased Frame", "Standard", "Heavy Flexible Frame"]
    elif item_name == "Modular Frame":
        preference_order = ["Steeled Frame", "Standard", "Bolted Frame"]
    elif item_name == "Reinforced Iron Plate":
        preference_order = ["Stitched Iron Plate", "Standard", "Adhered Iron Plate", "Bolted Iron Plate"]
    elif item_name == "Rotor":
        preference_order = ["Steel Rotor", "Standard", "Copper Rotor"]
    elif item_name == "Encased Industrial Beam":
        preference_order = ["Encased Industrial Pipe", "Standard"]
    elif item_name == "High-Speed Connector":
        preference_order = ["Silicon High-Speed Connector", "Standard"]
    elif item_name == "AI Limiter":
        preference_order = ["Plastic AI Limiter", "Standard"]
    elif item_name == "Computer":
        preference_order = ["Caterium Computer", "Crystal Computer", "Standard"]
    elif item_name == "Circuit Board":
        preference_order = ["Silicon Circuit Board", "Caterium Circuit Board", "Standard", "Electrode Circuit Board"]
    else:
        preference_order = list(available.keys())

    for r_name in preference_order:
        if r_name not in available:
            continue
        # Standard recipes are always unlocked
        if r_name == "Standard":
            # If no-screws is enabled, skip standard recipes that burn screws if possible
            if no_screws and "Screws" in available[r_name]["inputs"]:
                continue
            return (r_name, available[r_name])

        # Alternate recipe: must be unlocked
        if r_name in unlocked:
            if no_screws and "Screws" in available[r_name]["inputs"]:
                continue
            return (r_name, available[r_name])

    # Fallback to standard
    return ("Standard", available.get("Standard", next(iter(available.values()))))


def solve_phase4_targets(unlocked_alts: set[str], no_screws: bool = True, overrides: dict[str, str] = None) -> list[dict]:
    """Compute demand, whole-machine allocations, and surplus for all tiered items."""
    # Base Project Assembly Demands:
    # ADS: 0.75/min -> ACU: 1.5, Supercomputer: 0.75
    # MFG: 1.00/min -> Versatile Framework: 2.5, EM Control Rod: 1.0
    # TPR: 1.00/min -> Mod Engine: 2.5, Turbo Motor: 1.0, Cooling System: 3.0, Fused Frame: 1.0
    # Nuclear Pasta: 0.50/min -> Pressure Cube: 0.5, Copper Powder: 100.0

    gross_demand = defaultdict(float)
    consumers = defaultdict(list)

    # Initial demands from Space Elevator Parts
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

    # Processing in topological tier order from Tier 8 down to Tier 3
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
        # Integer machines required (whole-machine 100% doctrine)
        machines = math.ceil(demand / unit_rate) if unit_rate > 0 else 0
        total_output = machines * unit_rate
        surplus = total_output - demand

        # Feed inputs into downstream items
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
            "consumers": consumers[item],
        })

    return results


def main():
    parser = argparse.ArgumentParser(description="Satisfactory Production Targets Generator")
    parser.add_argument("--world", default="zinctoniaDos", help="World/session name")
    parser.add_argument("--save", help="Path to specific save file")
    parser.add_argument("--no-screws", action="store_true", default=True, help="Enforce zero-screws doctrine")
    parser.add_argument("--allow-screws", dest="no_screws", action="store_false")
    parser.add_argument("--sort", choices=["abc", "tier"], default="abc", help="Sort order of output")
    parser.add_argument("--unlocked", nargs="*", help="List of additional unlocked alternate recipes")
    parser.add_argument("--override", nargs="*", help="Recipe overrides (Item=RecipeName)")
    parser.add_argument("--json", action="store_true", help="Output raw JSON")

    args = parser.parse_args()

    unlocked = scan_unlocked_alternates_from_save(args.save)
    if args.unlocked:
        unlocked.update(args.unlocked)

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
        # Group by tier
        tier_order = {"Tier 8": 0, "Tier 7": 1, "Tier 5-6": 2, "Tier 3-4": 3}
        targets.sort(key=lambda x: (tier_order.get(x["tier"], 9), x["name"].lower()))

    if args.json:
        print(json.dumps(targets, indent=2))
        return

    print(f"# Satisfactory Production Targets — World: {args.world}")
    print(f"Doctrine: 100% Whole Machines | Zero-Screws: {args.no_screws}\n")
    print("| Item Name | Minimum Demand | Target Output (100%) | Natural Surplus | Machines Allocated | Recipe Used | Primary Consumers |")
    print("| :--- | :---: | :---: | :---: | :--- | :--- | :--- |")
    for t in targets:
        surplus_str = f"+{t['surplus']:.2f} / min" if t['surplus'] > 0.001 else "Exact"
        consumers_str = ", ".join(t["consumers"][:2]) + ("..." if len(t["consumers"]) > 2 else "")
        recipe_label = f"*{t['recipe_used']}*" if t['recipe_used'] != "Standard" else "Standard"
        print(f"| **{t['name']}** | {t['demand']:.2f} / min | **{t['output']:.2f} / min** | **{surplus_str}** | {t['machines']}× {t['machine_type']} | {recipe_label} | {consumers_str} |")


if __name__ == "__main__":
    main()
