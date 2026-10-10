---
name: satisfactory-surplus
description: Measure how much surplus each build item really needs in a Satisfactory world, from its save history (the save folder plus any backup or bot-copy folders), and turn it into export rates for a factory plan. Use this whenever the user asks how much surplus to build, how much extra to make "to fill storage", whether a plant is sized right for building, how fast they burn through concrete/steel/copper sheet/etc., how long their stock will last, what they burned or built last session (per-session storage change vs material placed), or before drawing any new plan for an item they build with (HMF, Computers, Circuit Boards, Copper Sheet, Steel, Concrete, Wire, Screws...). Trigger even if they just say "add some surplus" or "make extra for the build gun" -- don't guess a percentage, measure it.
---

# Satisfactory surplus sizing

## Why this exists

A flat "+20%" is wrong in both directions: it gives 0.2 HMF/min (one stack every four hours)
but 48 Screws/min (more than anyone uses). What matters is how fast the players actually
drain each item while building, and that is in the save history.

Two facts shape everything:

- **Factories only run while someone plays.** A world's clock stops between sessions
  (single player, or an on-demand server), so surplus is measured **per play hour**, never
  per wall-clock day.
- **Space Elevator parts get no surplus.** They are only consumed by the elevator: size
  those lines to the phase. Surplus is for items the build gun eats.

## Run it

From the satisfactory-mcp checkout:

```bash
uv run python skills/satisfactory-surplus/scripts/surplus_report.py \
  [--world NAME] [--item 'Copper Sheet' ...] [--sessions] [--json]
```

Saves and the game Docs are found the way the planner finds them: `SATISFACTORY_SAVES` and
`SATISFACTORY_DOCS` if set, else the Windows defaults (`%LOCALAPPDATA%\FactoryGame\Saved\SaveGames`,
searched recursively, and the Steam install's `CommunityResources\Docs`). `--world` is the
session name (the save filename before `_autosave_N`); the default is the newest save's.
More history makes better numbers: put extra folders of old copies (backups, a Discord
bot's history) in `SATISFACTORY_SAVE_HISTORY`, separated by `;` on Windows or `:` elsewhere.

Don't run it from a directory holding stray `.py` files (a `/tmp/attr.py` once shadowed a
library). It takes a minute or two: each save is parsed in its own process, read-only, from
a temp copy; nothing is written next to a save.

`--sessions` prints each session's storage change per item next to how much of it was
placed in the world, so you can see how storage moved session by session.

## Read the output

| column | meaning |
|---|---|
| `worst/h` | biggest **net** fall in stock per play hour across rated sessions (production already netted out) |
| `built/h` | biggest fall per play hour that went **into building**: per session, min(stock fall, material placed) |
| `cover h` | play hours the current stock lasts at the `built/h` rate |
| `surplus/min` | recommended surplus: max(built/h, default stacks per play hour x stack size) / 60 |

Per session (`--sessions`, or `per_session` in `--json`):

| field | meaning |
|---|---|
| `stock_change` | storage at session end minus start |
| `built` | material in placed buildables at end minus start (dismantles refund, so it can be negative) |
| `factory_net` | `stock_change + built`: what production, hand-feeding and the elevator did on their own |

"Built" is every placed piece (machines, foundations, walls, pillars, belts, pipes...) times
its build-gun recipe from the Docs. Belts and pipes are charged once per 2 m of drawn length
(Docs `mMeshLength`); power lines one Cable each (no length in the save, so a floor).
Concrete placed and dismantled inside one session cancels out.
| `basis` | `measured`, `stack default` (no real history), or Space Elevator part |

Stack sizes come from the game Docs: 50 (HMF, Modular Frame, Computer, Motor), 100
(Reinforced Plate, Rotor, Stator, Encased Beam, HSC), 200 (plates, rods, Cable, Copper Sheet,
Steel Pipe/Beam, Circuit Board, Plastic, Rubber), 500 (Screws, Wire, Quickwire, Concrete).
`--stacks-per-hour` sets the no-history default (1.0: one stack per play hour).

## Turn it into a plan

1. Plan export = **downstream demand + surplus/min** for the item, then round **up to whole
   machines at 100%** (you pay for the last machine anyway).
2. Because the drain is net of what already runs, a new plant whose surplus equals the worst
   drain stops stock falling in a heavy build session. A plant that replaces an existing
   source must also cover what that source made.
3. Pass the result to the renderer as `--export 'Item=<demand+surplus>'`, and record the
   split (demand vs surplus, and the basis) in the plan's `plan.md` Why section.
4. Physically: the last splitter's overflow goes to Industrial Storage -> AWESOME Sink.

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
- **No history yet** (HMF, Computers, HSC before they're produced) uses the stack default.
  Re-run the report after a few sessions with the new plant and adjust.

Self-check after editing the session arithmetic:
`uv run python skills/satisfactory-surplus/scripts/test_surplus.py` prints `ok`.
