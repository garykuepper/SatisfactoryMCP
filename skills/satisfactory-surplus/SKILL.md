---
name: satisfactory-surplus
description: Measure how much surplus each build item really needs in a Satisfactory world, from its save history (the save folder plus any backup or bot-copy folders), and turn it into export rates and Dimensional Depot allocations for a factory plan. Use when the user asks how much surplus to build, how much extra to make "to fill storage", whether a plant is sized right for building, how fast they burn through concrete/steel/copper sheet/etc., how long their stock will last, what they burned or built last session (per-session storage change vs material placed), how to size Dimensional Depots, or whether any item in the save is falling short of or exceeding target inventory. Trigger even if they just say "add some surplus", "make extra for the build gun", or "how many depots do I need" -- don't guess a percentage, measure it.
---

# Satisfactory surplus & Dimensional Depot sizing

## Why this exists

A flat "+20%" is wrong in both directions: it gives 0.2 HMF/min (one stack every four hours)
but 48 Screws/min (more than anyone uses). What matters is how fast the players actually
drain each item while building, and that is recorded in the save history.

Three facts shape everything:

- **Factories only run while someone plays.** A world's clock stops between sessions
  (single player, or an on-demand dedicated server), so surplus is measured **per play hour**,
  never per wall-clock day.
- **Space Elevator parts get no surplus.** They are only consumed by the elevator: size
  those lines to the phase. Surplus is for items the Build Gun and crafting stations eat.
- **In Satisfactory 1.0, surplus feeds Dimensional Depots first.** Cloud storage makes
  central crate malls obsolete. Sizing surplus means sizing the upload stream into the
  depot network and ensuring cloud inventory never bottlenecks active construction.

## Run it

From the repository checkout or skill directory:

```bash
SATISFACTORY_DOCS=/path/to/Docs/en-US.json \
SATISFACTORY_SAVES=/path/to/server/saves \
uv run python path/to/skills/satisfactory-surplus/scripts/surplus_report.py \
  [--world NAME] [--item 'Copper Sheet' ...] [--sessions] [--json]
```

### Environment variables
- `SATISFACTORY_SAVES`: Save directory (e.g. `/home/flynn/game-servers/satisfactory-server/config/saved/server`
  on Linux servers, or `%LOCALAPPDATA%\FactoryGame\Saved\SaveGames` on Windows).
- `SATISFACTORY_DOCS`: Path to `CommunityResources/Docs/en-US.json` (exported Docs from Steam/Epic install,
  note: encoded in UTF-16 LE).
- `SATISFACTORY_SAVE_HISTORY`: Extra folders of old copies (backups, bot archives), separated by `;`
  on Windows or `:` on Linux. More history gives sharper consumption metrics.

Don't run it from a directory holding stray `.py` files. It takes ~30-60 seconds: each save is parsed
in its own process, read-only, from a temp copy; nothing is written next to a save.

`--sessions` prints each session's storage change per item next to how much of it was placed
in the world, so you can see how storage moved session by session.

## Read the output

| column | meaning |
|---|---|
| `worst/h` | biggest **net** fall in stock per play hour across rated sessions (production already netted out) |
| `built/h` | biggest fall per play hour that went **into building**: per session, min(stock fall, material placed) |
| `cover h` | play hours current stock lasts at the `built/h` rate before hitting zero (`-` if drain is 0) |
| `surplus/min` | recommended surplus: max(built/h, default stacks per play hour x stack size) / 60 |

Per session (`--sessions`, or `per_session` in `--json`):

| field | meaning |
|---|---|
| `stock_change` | storage at session end minus start |
| `built` | material in placed buildables at end minus start (dismantles refund, so it can be negative) |
| `factory_net` | `stock_change + built`: what production, hand-feeding and the elevator did on their own |

"Built" is every placed piece (machines, foundations, walls, pillars, belts, pipes...) times
its build-gun recipe from the Docs. Belts and pipes are charged once per 2 m of drawn length
(Docs `mMeshLength`); power lines one Cable each. Concrete placed and dismantled inside one session cancels out.

Stack sizes: 50 (HMF, Modular Frame, Computer, Motor), 100 (Reinforced Plate, Rotor, Stator, Encased Beam, HSC),
200 (Plates, Rods, Cable, Copper Sheet, Steel Pipe/Beam, Circuit Board, Plastic, Rubber), 500 (Screws, Wire, Quickwire, Concrete).
`--stacks-per-hour` sets the no-history default (1.0: one stack per play hour).

## The 4 Inventory Health Tiers

Use the report metrics (`stock_now`, `worst_build_drain_per_play_h`, and `hours_of_cover`) to categorize
items into operational tiers:

1. **Critical Deficit / Low Cover (`cover_h < 10h` with active build drain)**:
   - Current stock will be depleted in under 10 play hours at peak measured build rate.
   - **Action**: High-priority target for dedicated factory surplus lines and priority Dimensional Depot upload.
     Inspect whether alternate recipes can substitute stressed inputs (e.g. `Encased Industrial Pipe` using abundant steel pipes).
2. **Nascent / Unautomated (Zero or Token Stock)**:
   - High-tier items that have not yet been automated. Stock exists only from crash site loot or manual craft bench batches.
   - **Action**: Size new production lines directly to Space Elevator phase demand plus a standard 1 stack/hour depot trickle.
3. **Moderate & Healthy Buffers (`10h <= cover_h <= 50h` or `cover_h > 50h`)**:
   - Stock matches or comfortably exceeds historical building drains.
   - **Action**: Maintain current factory allocation; no emergency surplus expansion needed unless a massive expansion project is planned.
4. **Over-Target / Saturated Surplus (Massive Session Accumulation)**:
   - Inventories accumulating by tens of thousands per session with near-zero build consumption.
   - **Action**: Do not add production. Check local house rules (e.g. `--no-screws` doctrine) and look for alternate recipes that consume this surplus as a cheap feed.

*(Note: When `worst_build_drain_per_play_h` is 0, `cover_h` is reported as `-` / null. Check `stock_now`: large stock means healthy buffer; near-zero stock means nascent/unautomated).*

## Dimensional Depot Sizing (Satisfactory 1.0)

In Satisfactory 1.0, the Dimensional Depot fundamentally alters construction logistics:

### 1. Upload Bandwidth is PER DEPOT BUILDING, not global
- Every physical **Dimensional Depot Uploader** placed in the world operates independently at its full researched speed.
- Upload speeds (Alien Technology / Mercer Sphere MAM tree):
  - **Base**: 15 items/min
  - **Upgrade 1**: 30 items/min
  - **Upgrade 2**: 60 items/min
  - **Upgrade 3**: 120 items/min
  - **Upgrade 4**: 240 items/min
- Sizing formula:
  $$\text{Depot Uploaders Needed} = \left\lceil \frac{\text{Target Replenishment Rate (items/min)}}{\text{Researched Upload Speed per Depot}} \right\rceil$$
- Multiple uploaders on the same item **stack additively** into cloud storage. Note that total throughput
  is also strictly capped by upstream factory production and feeding belt capacity.

### 2. High-Drain Materials (Concrete)
- Foundations and walls consume large volumes of Concrete during construction bursts (often 300 to 600/min during rapid zooping).
- While the cloud buffer absorbs short bursts, sustained multi-hour paving rates can exceed lower-tier upload speeds (e.g. 60/min).
- **Rule**: Compare the measured `built/h` against your current research tier. If sustained placement exceeds a single uploader's speed,
  place **two parallel Dimensional Depots on Concrete** (e.g., providing 120 to 480/min total upload).

### 3. Standard Construction Materials (Plates, Beams, Pipes, Cable, Frames)
- Peak placement rates for machines and belts are typically under 30 items/min.
- A single Dimensional Depot (60 to 240/min) replenishes the cloud buffer significantly faster than manual placement velocity.
- **Rule**: One depot uploader is sufficient for standard construction items.

### 4. Sequential Splitter Standard (Protecting Downstream Production)
A single Smart Splitter cannot prioritize between two active outputs; if both are set to the item, they split evenly.
To strictly guarantee that downstream production lines never starve while keeping the depot and storage fed,
use **two sequential Smart Splitters with Overflow priority**:

```
[ Factory Output ]
        |
        v
[ Smart Splitter 1 ] --( Main Output: Item Name )--> [ Downstream Production Line ]
        |
   ( Overflow )
        |
        v
[ Smart Splitter 2 ] --( Main Output: Item Name )--> [ Dimensional Depot Uploader(s) ]
        |
   ( Overflow )
        |
        v
[ Industrial Storage Container ]
        |
        v
[ Smart Splitter 3 ] --( Overflow )-----------------> [ AWESOME Sink ]
```
- **Splitter 1**: Guarantees 100% of factory demand is satisfied first. Only unconsumed surplus passes to Overflow.
- **Splitter 2**: Directs surplus into the Dimensional Depot Uploader until the cloud capacity (1 to 5 stacks) and machine input buffer are full, then overflows.
- **Splitter 3**: Once local buffer storage is saturated, overflow routes to an AWESOME Sink to prevent upstream line lockup.

## Turn it into a plan

1. Plan export = **downstream demand + surplus/min** for the item, then round **up to whole
   machines at 100%** (you pay for the last machine anyway).
2. Because the drain is net of what already runs, a new plant whose surplus equals the worst
   drain stops stock falling in a heavy build session. A plant that replaces an existing
   source must also cover what that source made.
3. Pass the result to the renderer as `--export 'Item=<demand+surplus>'`, and record the
   split (demand vs surplus, and the basis) in the plan's `plan.md` Why section.
4. Route surplus using the sequential splitter standard: Downstream -> Depot -> Storage -> Sink.

## Judgement calls (say them out loud)

- **Hand-feeding vs building.** `built` separates them: a fall with little built (Motors and
  Modular Frame carried to the elevator, say) is logistics, and surplus is not sized off
  it. A big `worst/h` next to a small `built/h` means a supply line, not the build gun.
- **The belt/pipe rate is from the Docs, not checked in game.** If a belt's in-game cost
  disagrees, change `MESH_M` in the script.
- **A session that built a new factory** mixes new production with consumption, so treat
  measured numbers as floors, not exact.
- **Big stock, small drain** (`cover h` in the tens or hundreds) means no extra surplus
  needed: say so instead of padding the plan.
- **Saturated stockpiles**: Check if items with massive surpluses can replace strained inputs
  via alternate recipes. Always check world-specific house rules (e.g. `--no-screws` if applicable)
  before recommending recipes.
- **No history yet** (HMF, Computers, HSC before they're produced) uses the stack default.
  Re-run the report after a few sessions with the new plant and adjust.

Self-check after editing the session arithmetic:
`uv run python skills/satisfactory-surplus/scripts/test_surplus.py` prints `ok`.
