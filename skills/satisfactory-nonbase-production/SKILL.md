---
name: satisfactory-nonbase-production
description: Production targets and live fulfillment for non-base (assembled, multi-input) parts in a Satisfactory save -- Reinforced Iron Plate, Rotor, Stator, Motor, Modular Frame, Encased Industrial Beam, Heavy Modular Frame, Computer, Radio Control Unit, up to Adaptive Control Unit and Turbo Motor. Solves Space Elevator Phase 4 demand into whole-machine 100% counts and natural surplus, picks recipes from the save's unlocked alternates, and compares against the machines actually running. Never targets plates, wire, cable, concrete, pipes or beams. Use when the user asks for item targets, production quotas for tiered intermediates, subassembly rates, what an alternate recipe does to factory sizing, a tier-by-tier bill of materials, or how far the factory is from its targets.
---

# Satisfactory Non-Base (Subassembly) Production Targets

One skill, two scripts: `production_targets.py` solves the targets, `nonbase_report.py` compares them with the live save.

## Why non-base only

Single-input materials (Iron Plate, Wire, Cable, Concrete, Steel Pipe, Steel Beam) come straight out of Constructors and sit in big stockpiles. Progress is blocked by the **non-base parts**: multi-input items built in Assemblers, Manufacturers or Blenders. Those are the targets.

## Doctrine

1. **Whole machines at 100%.** No underclocking. Machine counts round up; the rounding excess is the natural surplus.
2. **Natural surplus goes to Dimensional Depots and storage** (Smart Splitters), then the AWESOME Sink.
3. **`--no-screws` is the default** (owner decision): screw recipes are skipped where an alternate exists.
4. **Alternates are read from the live AuditBot world state** (`db.json`), so targets move when new drives are picked.

## Run it

```bash
cd ~/ai-stack/satisfactory-mcp
export SATISFACTORY_SAVES=~/game-servers/satisfactory-server/config/saved/server \
  SATISFACTORY_DOCS=~/game-servers/ficsit-auditbot/data/docs/en-US.json

# Targets vs what is actually built and running (status per item)
uv run python skills/satisfactory-nonbase-production/scripts/nonbase_report.py [--world NAME] [--all] [--json]

# Targets only: demand, machines, surplus, consumers (no save machines needed)
python3 skills/satisfactory-nonbase-production/scripts/production_targets.py [--sort {abc,tier}] [--json]
```

### `nonbase_report.py`
* `--world NAME`: default `zinctoniaDos`.
* `--all`: also list other multi-input items found running in the save (no target for these).
* `--json`: structured output.

### `production_targets.py`
| Flag | Default | Description |
| :--- | :--- | :--- |
| `--sort {abc,tier}` | `abc` | A-Z, or ordered Tier 8 down to Tier 3-4 (no tier column in the table; `--json` has `tier`). |
| `--unlocked R ...` | none | Pretend these alternates are unlocked ("what if"). |
| `--lock R ...` | none | Pretend these are locked. |
| `--override "Item=Recipe"` | none | Force a recipe, e.g. `"Computer=Crystal Computer"`. |
| `--allow-screws` | off | Allow screw recipes. |
| `--list-unlocked` | off | List the save's unlocked alternates, flagging the ones that change a target. |
| `--json` | off | JSON instead of a table. |

## Status codes (`nonbase_report.py`)

* `OK`: configured capacity meets the target.
* `BELOW_TARGET`: configured machines make less than the target.
* `STARVED_OR_IDLE`: machines exist but produced almost nothing in the 5-minute window (unpowered, starved belts).
* `NOT_AUTOMATED`: no machine in the save makes it.

"Built" counts every machine with a recipe producing the item, whatever the recipe, so it can exceed the target machine count.

## How alternates move the targets

| Alternate | Effect |
| :--- | :--- |
| `Radio Control System` | RCU skips Computers: Computer demand 19.0 down to 11.5/min. |
| `Heavy Encased Frame` | Halves Modular Frame demand and cuts RIP demand by a third. |
| `Turbo Electric Motor` | Turbo Motor skips Cooling Systems. |
| `Silicon High-Speed Connector` | Swaps Cable for Silica; Quickwire use drops from 210 to 90/min per machine. |
| `Plastic AI Limiter` | Plastic for Copper Sheet; 8.0/min per Assembler instead of 5.0. |
| `Steeled Frame` | Steel Pipes instead of Iron Rods; cuts RIP per frame by 55%. |
| `Stitched Iron Plate` | Wire instead of Screws; 5.625 RIP/min with zero screws. |

The rates above were measured against the stock recipe table; the script is authoritative for the current save.
