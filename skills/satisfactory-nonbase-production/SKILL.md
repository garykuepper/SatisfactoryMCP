---
name: satisfactory-nonbase-production
description: Track and report production targets, live machine rates, and fulfillment gaps for non-base (subassembly / composite) parts in Satisfactory saves. Filters out basic 1-ingredient Constructor parts (plates, wire, cable, concrete, pipes, beams) to focus strictly on multi-input assembled components (Reinforced Iron Plate, Rotor, Stator, Motor, Modular Frame, Encased Beam, Heavy Modular Frame, etc.) sized for whole-machine 100% nominal clocking and natural Dimensional Depot surplus according to the Factory Design Doctrine. Use when the user asks for non-base parts, subassembly production targets, assembled component rates, or progress toward combined elevator + depot quotas.
---

# Satisfactory Non-Base (Subassembly) Production Targets

## Why this exists

Single-input base materials (Iron Plates, Wire, Cable, Concrete, Steel Pipes, Steel Beams) are produced directly in Constructors from smelted ingots or raw resources. They represent bulk commodities that often sit in massive storage stockpiles.

In contrast, **non-base parts** (subassemblies / composite components):
1. **Require multiple ingredient streams**: Produced in **Assemblers** (2 inputs), **Manufacturers** (3–4 inputs), or **Blenders**.
2. **Drive factory bottlenecks**: Progress in Project Assembly and milestone tiers is almost always blocked by missing subassemblies (e.g. starved Rotor lines, missing Modular Frame assemblers).
3. **Follow the Whole-Machine 100% Clocking Doctrine**: Underclocking intermediate assemblers creates awkward decimal belts. Under the Factory Design Doctrine, subassembly lines are sized with $\lceil N \rceil$ whole machines clocked at 100%, harvesting the natural surplus into Dimensional Depots.

## Run it

From the repository checkout:

```bash
uv run python skills/satisfactory-nonbase-production/scripts/nonbase_report.py [--world NAME] [--all] [--json]
```


### Options
* `--world <NAME>`: Name of the world or save (defaults to `LFG`).
* `--all`: In addition to core build items, auto-discovers all multi-ingredient items in the save (including Project Assembly parts like Smart Plating, Versatile Framework, Automated Wiring).
* `--json`: Emits structured JSON for automated pipelines or frontend components.

## Core Integrated Target Reference

These targets reflect the combined Phase 4 Space Elevator demand plus continuous personal Dimensional Depot building surplus:

| Subassembly | Target / min | Machine Allocation | Recipe | Input Feeds |
|---|---:|---|---|---|
| **Reinforced Iron Plate** | 28.125 | 5× Assembler @ 100% | *Stitched Iron Plate* | Iron Plate + Wire |
| **Rotor** | 30.000 | 6× Assembler @ 100% | *Steel Rotor* | Steel Pipe + Wire |
| **Stator** | 30.000 | 6× Assembler @ 100% | *Standard Stator* | Steel Pipe + Wire |
| **Modular Frame** | 21.000 | 7× Assembler @ 100% | *Steeled Frame* | Reinforced Iron Plate + Steel Pipe |
| **Motor** | 15.000 | 3× Assembler @ 100% | *Standard Motor* | Rotor + Stator |
| **Encased Industrial Beam** | 20.000 | 5× Assembler @ 100% | *Encased Industrial Pipe* | Steel Pipe + Concrete |
| **Heavy Modular Frame** | 4.000 | 2× Manufacturer @ 100% | *Heavy Modular Frame* | Mod Frame + Encased Beam + Pipe + Concrete |

## Status Codes

* `OK`: Configured capacity matches or exceeds target rate.
* `BELOW_TARGET`: Configured machines produce less than target rate.
* `STARVED_OR_IDLE`: Machines exist and are configured, but produced near 0 items in the recorded 5-minute productivity window (indicates unpowered machines, starved belts, or paused miners).
* `NOT_AUTOMATED`: Zero machines placed in the save with this recipe.
