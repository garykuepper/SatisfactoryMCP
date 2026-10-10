---
name: satisfactory-alt-recipes
description: Rank a Satisfactory save's unlocked and pending alternate recipes, and recommend which pending MAM hard drives to pick or reroll. Use this whenever the user asks which alt recipe to pick, what a hard drive is offering, whether to reroll a hard drive, "rank my alt recipes", "what should I pick in the MAM", or shows a screenshot of the Hard Drive Library UI. Trigger even without those exact words -- any question about MAM rewards, alternate recipes, or hard-drive decoding should run this skill's script rather than eyeballing a published tier list, because a tier list misses screw dependency, locked buildings, and redundancy with recipes already owned.
---

# Satisfactory alt-recipe ranking

## Why this exists

A published tier list (u/wrigh516's 1.0 ranking is the usual one) scores every alt recipe
once, for a theoretical whole-map end-game factory. That score is a good starting point but
it is blind to three things that matter more in one specific save, right now:

1. **Screw dependency.** A recipe can be B-tier on the published list and still burn
   140-200 Screws/min per machine, which eats belt throughput out of proportion to what
   screws are worth. The score has no column for this. You have to read the recipe's
   actual ingredients.
2. **A building you haven't unlocked yet.** A recipe needing a Refinery is real, but
   useless today if Oil Processing isn't done. That's not disqualifying (it's fine to
   bank it), but it changes what "pick this now" means.
3. **Redundancy.** If you already run Steel Rotor (screw-free) for Rotor, being offered
   Copper Rotor (195 Screws/min, similar score) is not an upgrade, even though the raw
   number looks fine in isolation.

A score-only read of the tier list misses all three. Always run the script below instead
of hand-ranking.

## How to run it

From the satisfactory-mcp checkout:

```bash
uv run python skills/satisfactory-alt-recipes/scripts/rank_recipes.py \
  --ranking path/to/ranking.csv [--world NAME] [--no-screws]
```

- **Saves and game Docs** are found the way the planner finds them: `SATISFACTORY_SAVES` and
  `SATISFACTORY_DOCS` if set, else the Windows defaults. It reads the newest save (of
  `--world`, if given: the session name). The game autosaves every few minutes, so re-run it
  rather than trusting an answer from earlier in the conversation.
- **The ranking CSV is not shipped.** Pass `--ranking` or set `SATISFACTORY_RANKING_CSV`.
  Format: a header row, then `recipe,tier,score[,...]` per alt, display names without
  "Alternate:" (`Cast Screw` and `Cast Screws` both match). Transcribe it from the tier
  list you trust.
- **`--no-screws`** turns screw inputs from a caveat into a veto. Use it when the user (or
  the project's own instructions) has ruled screws out.

## Reading the output

The script prints three sections:

- **Currently unlocked alt recipes**: what you're already running, ranked, so you can
  sanity-check nothing you've claimed is quietly working against the house rules.
- **Pending hard drives: published ranking vs. this save's doctrine**: one row per drive,
  both options' published tier/score side by side, **sorted best drive first** (by the best
  non-vetoed option's score). This is the "where does the ranking put it" answer, shown
  even when a veto overrides it: the point is to show the correction, not hide the number
  being corrected.
- **Doctrine check + verdict per drive**: each option's flags, then one of:
  - `KEEP: pick X`: best live option is B-tier or better.
  - `REROLL`: a reroll is left and the best live option is below B-tier (score < 50, or
    unranked), or both options are vetoed. Declined options return to the pool, so
    rerolling never loses a recipe for good.
  - `PICK X (no rerolls left, least bad option)`: would reroll, but can't.

Report it as markdown tables (unlocked recipes, then drives in the script's order) with the
verdict as a column: a reroll call without the numbers behind it asks the user to trust the
flag instead of seeing it.

**List every unlocked alt on its own row** (`# | Recipe | Tier | Score`), ranked best score
first, exactly as the script prints them. Never collapse a tier into one comma-separated
row. Mark anything newly unlocked since the last check.

**Name each drive by its two options, not just its number.** The drive numbers are the
save's internal IDs; the in-game Hard Drive Library doesn't show them, so "reroll drive 17"
means nothing at the MAM. Say "the Molded Steel Pipe / Encased Industrial Pipe drive" (the
number can ride along in the table).

Flags come in two kinds:

- **Vetoes (dead flags)**: the option is worth nothing to this save:
  - Screws as an input, with `--no-screws`. Screw *producers* like Steel Screws are never
    vetoed.
  - The exact recipe is already unlocked.
  - No automatable recipe behind the name.
- **Caveats**: screw inputs without `--no-screws`, a *different* recipe already covering
  the same item, or a building that isn't unlocked. Not disqualifying: owning a second
  recipe never hurts, so these are surfaced in the verdict line, never used to flip the
  pick.

Lead with anything vetoed or rerolled: that's the correction to what the ranking table
alone would suggest.

## When the script's judgment and the situation disagree

The script encodes the mechanical checks (screws, locked buildings, redundancy) but not
situational context like which item is currently blocking a milestone (see `milestones()`
in satisfactory-mcp) or an item that needs screws on every recipe anyway (Heavy Modular
Frame: a screw veto on Heavy Flexible Frame picks a worse recipe for an item you can't make
screw-free). If that makes an otherwise-flagged recipe worth it, say so explicitly and
explain the tradeoff rather than silently overriding the script: reason from the same real
numbers the script used, but don't treat its output as unappealable.

## Self-check

After editing the script, run
`uv run python skills/satisfactory-alt-recipes/scripts/test_verdict.py` (prints `ok`).

## If the script errors

The script parses satisfactory-mcp's printed text (`recipe_detail()`,
`list_pending_hard_drive_choices()`) rather than its internal objects, since that's the
stable public surface. If a flag that should fire silently doesn't, or parsing raises, print
the raw tool output for the recipe in question and compare it to the regexes in
`scripts/rank_recipes.py`: format drift (once "machine=" vs "machine :") is the first thing
to check before assuming the doctrine logic is wrong.
