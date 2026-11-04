---
name: satisfactory-production-targets
description: Calculate exact intermediate component production targets, machine counts, and natural surplus rates for Satisfactory Space Elevator Phase 4 (or custom quotas), dynamically adjusting depending on unlocked alternate recipes and the 100% whole-machine doctrine. Use when the user asks for item targets, production quotas for tiered intermediates (RIP, Modular Frames, Motors, Heavy Modular Frames, Computers, RCUs, etc.), asks how an alternate recipe changes factory sizing, or wants an alphabetical or tier-by-tier bill of materials.
---

# Satisfactory Production Targets Generator

Calculates exact production quotas, machine allocations, and natural construction surpluses for all **tiered intermediate items** (Tier 3 through Tier 8 components), automatically adjusting the supply chain graph based on the save's unlocked alternate recipes.

## Core Architectural Doctrine

1. **Whole-Machine 100% Clocking ($\lceil N \rceil$)**:
   No fractional underclocking (e.g. 23%, 40%, 53.3%). All processing machines run at 100% nominal speed. Machine counts round up to the nearest whole integer.
2. **Natural Construction Surplus**:
   Any excess production resulting from whole-machine rounding is harvested via Smart Splitters into **Dimensional Depots & Storage** before overflowing to the AWESOME Sink.
3. **Strict `--no-screws` Default**:
   Eliminates all screw-dependent recipes where alternatives exist (`Stitched Iron Plate`, `Steeled Frame`, `Steel Rotor`, `Encased Industrial Pipe`).
4. **Dynamic Alternate Adaptation**:
   When high-impact alternate recipes are unlocked (or toggled via CLI), downstream dependencies instantly rebalance:
   * `Radio Control System`: Bypasses Computers entirely for RCU production.
   * `Heavy Encased Frame`: Halves Modular Frame and RIP demands.
   * `Turbo Electric Motor`: Bypasses Cooling Systems for Turbo Motors.
   * `Silicon High-Speed Connector` / `Plastic AI Limiter`: Reduces building footprint and swaps input materials.

---

## How to Run It

From the repository or skill directory:

```bash
python3 skills/satisfactory-production-targets/scripts/production_targets.py \
  [--world NAME] [--save PATH] [--sort {abc,tier}] [--unlocked RECIPE ...] [--no-screws] [--json]
```

### Common Commands

* **Alphabetical Target List (Default)**:
  ```bash
  python3 /home/flynn/ai-stack/satisfactory-mcp/skills/satisfactory-production-targets/scripts/production_targets.py --sort abc
  ```

* **Grouped by Manufacturing Tier**:
  ```bash
  python3 /home/flynn/ai-stack/satisfactory-mcp/skills/satisfactory-production-targets/scripts/production_targets.py --sort tier
  ```

* **Test "What If" Alternate Unlocks**:
  ```bash
  # See what happens to Computer demand if Radio Control System is unlocked:
  python3 .../production_targets.py --unlocked "Radio Control System"

  # See what happens if Heavy Encased Frame is unlocked:
  python3 .../production_targets.py --unlocked "Heavy Encased Frame"
  ```

* **Raw JSON Output (for automated plan generators or webhooks)**:
  ```bash
  python3 .../production_targets.py --json
  ```

---

## Command-Line Arguments

| Flag | Default | Description |
| :--- | :--- | :--- |
| `--world NAME` | `zinctoniaDos` | Session / world name to scan for save metadata. |
| `--save PATH` | Newest save in server saves dir | Path to a specific `.sav` file. |
| `--sort {abc,tier}` | `abc` | Sort output alphabetically (A–Z) or grouped by Tier (Tier 8 down to Tier 3). |
| `--no-screws` | `True` | Prioritize screw-free alternate recipes across all components. |
| `--unlocked ...` | `None` | Pass additional unlocked alternate recipe names to test scenarios. |
| `--override ...` | `None` | Force a specific recipe for an item (e.g. `--override "Computer=Crystal Computer"`). |
| `--json` | `False` | Output structured JSON instead of a Markdown table. |

---

## How Alternate Recipes Shift Intermediate Targets

| Alternate Recipe | What It Bypasses or Changes | Impact on Factory Demands |
| :--- | :--- | :--- |
| **`Radio Control System`** | Replaces standard RCU *(Aluminum + Computers + Oscillators)* with *(Aluminum + Rubber + Circuit Boards + Oscillators)* | **Completely eliminates Computers from RCU production.** Drops Computer demand from 19.0/min (8 Manufacturers) down to **11.5/min (5 Manufacturers)**. |
| **`Heavy Encased Frame`** | Replaces standard HMF *(10 Mod Frames per 2 HMF)* with *(7.5 Mod Frames per 2.81 HMF)* | **Cuts Modular Frame demand in half** (from 30.0/min down to 15.0/min) and cuts RIP demand from 30.0/min down to 20.0/min. |
| **`Turbo Electric Motor`** | Replaces standard Turbo Motor with *(Motor + RCU + EM Control Rod + Rotor)* | **Bypasses Cooling Systems in Turbo Motors**, dropping Cooling System demand from 10.5/min down to just 3.0/min (TPR only). |
| **`Silicon High-Speed Connector`** | Swaps Cable for Silica | Cuts Quickwire usage from 210/min to 90/min per machine; saves Caterium ore. |
| **`Plastic AI Limiter`** | Swaps Copper Sheets for Plastic | Produces 8.0/min per Assembler instead of 5.0/min; reduces machine footprint. |
| **`Steeled Frame`** | Uses Steel Pipes instead of Iron Rods | Cuts RIP requirement per frame by 55%. |
| **`Stitched Iron Plate`** | Uses Wire instead of Screws | Produces 5.625 RIP/min with zero screws; pairs with Iron Wire or Caterium Wire. |
