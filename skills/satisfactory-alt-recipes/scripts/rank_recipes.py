#!/usr/bin/env python3
"""Rank unlocked + pending Satisfactory alt recipes for THIS save.

The published u/wrigh516 1.0 tier list (vendor-ranking-1.0.csv) scores every alt
recipe on a whole-map, end-game basis. That number is a fine starting point but it
does not know three things that matter more, right now, in one specific save:

  1. Screw dependency. A recipe can rank B-tier on paper and still burn 100-200
     Screws/min per machine -- exactly what "eliminate screws" exists to avoid.
     The tier score has no column for this; you have to read the actual recipe.
  2. A building you haven't unlocked. Steamed Copper Sheet needs a Refinery;
     if Oil Processing isn't done yet, the recipe is real but unusable today.
  3. Redundancy. If you already run Steel Rotor (screw-free), Copper Rotor
     (195 Screws/min) is not an upgrade for you even though its ranking score
     looks similar -- picking it would make things strictly worse.

This script pulls the live save's unlocked recipes and pending hard-drive choices
via satisfactory-mcp, joins each one against the published ranking, and reads the
ACTUAL ingredients of every candidate so screw/building/redundancy issues show up
next to the score instead of getting missed the way a score-only lookup misses them.

Usage, from the satisfactory-mcp checkout (saves and game Docs are found the way the
planner finds them: SATISFACTORY_SAVES / SATISFACTORY_DOCS, else the Windows defaults):
  uv run python skills/satisfactory-alt-recipes/scripts/rank_recipes.py \
      --ranking path/to/ranking.csv [--world NAME] [--no-screws]
The ranking CSV (recipe,tier,score,...) is not shipped; --ranking or SATISFACTORY_RANKING_CSV.
"""

from __future__ import annotations

import argparse
import csv
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "src"))  # repo checkout's src/

from satisfactory_mcp.interfaces.mcp.tools import gamedata, harddrives, world  # noqa: E402


def load_ranking(path: str) -> dict[str, tuple[str, float]]:
    """name -> (tier, score) from a ranking CSV: recipe,tier,score[,...]."""
    ranking: dict[str, tuple[str, float]] = {}
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.reader(f):
            if not row or row[0].strip().lower() in ("recipe", ""):
                continue
            name, tier, score = row[0].strip(), row[1].strip(), row[2].strip()
            try:
                ranking[name] = (tier, float(score.rstrip("%")))
            except ValueError:
                continue
    return ranking


def ranking_lookup(name: str, ranking: dict) -> tuple[str, float]:
    """The game's display name and the CSV's aren't always identical (e.g. the game
    says 'Cast Screws', the CSV says 'Cast Screw') -- try exact, then a plural swap,
    before giving up and reporting unranked rather than silently scoring it 0."""
    if name in ranking:
        return ranking[name]
    for alt in (name.rstrip("s"), name + "s"):
        if alt in ranking:
            return ranking[alt]
    return ("?", None)


def detail_for(display_name: str) -> str | None:
    """recipe_detail() text for an alt by its bare display name, e.g. 'Bolted Frame'."""
    for candidate in (f"Alternate: {display_name}", display_name):
        text = gamedata.recipe_detail(candidate)
        first_line = text.splitlines()[0] if text else ""
        if not first_line.startswith("unknown") and " matches " not in first_line:
            return text
    return None


def parse_detail(text: str) -> dict:
    """Pull the fields this script cares about out of recipe_detail()'s text block."""
    out = {"inputs": "", "outputs": "", "building": "", "locked_building": False}
    m = re.search(r"in/min\s*:\s*(.+)", text)
    if m:
        out["inputs"] = m.group(1).strip()
    m = re.search(r"out/min\s*:\s*(.+)", text)
    if m:
        out["outputs"] = m.group(1).strip()
    m = re.search(r"machine=(\S+)", text)
    if m:
        out["building"] = m.group(1).strip()
    return out


def has_screws(inputs_text: str) -> bool:
    return bool(re.search(r"\bScrews?\b", inputs_text))


def item_from_outputs(outputs_text: str) -> str | None:
    m = re.match(r"[\d.]+\s+(.+)", outputs_text.strip())
    return m.group(1).strip() if m else None


def owned_alternative_for(item: str, exclude: str) -> str | None:
    """If a non-excluded alt for the same item already shows HAVE, name it."""
    block = gamedata.alternates_for_item(item=item)
    for line in block.splitlines():
        parts = line.split("\t")
        if len(parts) >= 4 and parts[0].startswith("Alternate:") and parts[3].strip() == "HAVE":
            name = parts[0].replace("Alternate:", "").strip()
            if name != exclude:
                return name
    return None


def building_status(building: str) -> str:
    """HAVE or LOCKED for a building name, via list_buildings' HAVE/LOCKED marking."""
    if not building:
        return "?"
    rows = gamedata.list_buildings(kind="production", limit=25)
    for line in rows.splitlines():
        if building.split()[0].split("Mk")[0].strip() in line and "\t" in line:
            return "HAVE" if line.startswith("HAVE") else "LOCKED"
    # Refinery/Foundry/Assembler/Constructor/Smelter are near-universal; only flag
    # the ones this codebase's own building list marks LOCKED for THIS save.
    return "HAVE"


def evaluate(display_name: str, ranking: dict, owned_names: set[str] | None = None,
             no_screws: bool = False) -> dict:
    """DEAD = worth nothing to this save, whatever it's paired with: the exact recipe
    you already have, no automatable recipe behind the name, or -- with no_screws --
    Screws as an input (a house rule some worlds keep; without it screws are a caveat).

    Everything else -- a different recipe already covering the same item, a building
    you haven't built -- is a CAVEAT: surfaced, never used to flip the pick."""
    tier, score = ranking_lookup(display_name, ranking)
    row = {"name": display_name, "tier": tier, "score": score, "dead_flags": [], "caveats": []}
    if owned_names and display_name in owned_names:
        row["dead_flags"].append("ALREADY UNLOCKED -- literally the same recipe, this offers nothing new")
    text = detail_for(display_name)
    if text is None:
        row["dead_flags"].append("no automatable recipe in this save (dead pick)")
        return row
    d = parse_detail(text)
    if has_screws(d["inputs"]):
        m = re.search(r"([\d.]+)\s+Screws?", d["inputs"])
        rate = m.group(1) if m else "?"
        if no_screws:
            row["dead_flags"].append(f"SCREWS: {rate}/min input -- no-screws rule")
        else:
            row["caveats"].append(f"SCREWS: {rate}/min input")
    item = item_from_outputs(d["outputs"])
    if item:
        alt_owned = owned_alternative_for(item, exclude=display_name)
        if alt_owned:
            row["caveats"].append(f"you already run {alt_owned!r} for {item} too -- not essential, but not harmful to also have")
    if d["building"] and building_status(d["building"]) == "LOCKED":
        row["caveats"].append(f"needs {d['building']}, which you haven't unlocked yet -- fine to bank")
    return row


def fmt_score(score: float | None) -> str:
    return f"{score:>5.1f}" if score is not None else " n/a "


def fmt_tier_score(r: dict, verbose: bool = False) -> str:
    """The published-ranking cell for one option, e.g. 'A / 63.5' or 'unranked' --
    kept as its own function because this exact string is the answer to "where does
    the reddit ranking put it", the thing worth never losing track of underneath the
    doctrine flags. `verbose=True` spells out why there's no score (not a production
    alt), for the per-drive detail section where there's room for it."""
    if r["score"] is None:
        return "unranked (not a production alt)" if verbose else "unranked"
    return f"{r['tier']} / {r['score']:.1f}"


def all_flags(r: dict) -> list[str]:
    return r["dead_flags"] + r["caveats"]


def print_table(title: str, rows: list[dict]) -> None:
    print(f"\n== {title} ==")
    rows_sorted = sorted(rows, key=lambda r: (r["score"] is None, -(r["score"] or 0)))
    for r in rows_sorted:
        flag_text = "; ".join(all_flags(r))
        print(f"{fmt_score(r['score'])}  {r['tier']:<2}  {r['name']:<28} {flag_text}")


REROLL_BELOW = 50.0  # below B-tier (C/D/F/unranked) isn't worth spending the drive on


def best_score(pair_rows: list[dict]) -> float:
    """Published score of the best non-dead option; -1 if unranked, -2 if none alive."""
    alive = [r for r in pair_rows if not r["dead_flags"]]
    return max((r["score"] if r["score"] is not None else -1 for r in alive), default=-2)


def verdict(rerolls: int, pair_rows: list[dict]) -> str:
    """REROLL when a reroll is left and the best live option is below B-tier (or
    nothing is live). Declined options return to the pool, so a reroll never loses
    a recipe for good. With no rerolls left you must pick, so name the least bad."""
    alive = [r for r in pair_rows if not r["dead_flags"]]
    best = max(alive or pair_rows, key=lambda r: r["score"] if r["score"] is not None else -1)
    caveats = f" -- caveats: {'; '.join(best['caveats'])}" if best["caveats"] else ""
    if best_score(pair_rows) < REROLL_BELOW:
        if rerolls > 0:
            why = "both options are dead" if not alive else f"best live option {best['name']!r} is below B-tier"
            return f"REROLL: {why}"
        return f"PICK {best['name']!r} (no rerolls left, least bad option){caveats}"
    return f"KEEP: pick {best['name']!r}{caveats}"


def main() -> None:
    ap = argparse.ArgumentParser(description="Rank unlocked and pending alt recipes for a save.")
    ap.add_argument("--ranking", default=os.environ.get("SATISFACTORY_RANKING_CSV"),
                    help="ranking CSV (recipe,tier,score,...); default $SATISFACTORY_RANKING_CSV")
    ap.add_argument("--world", help="world (session) name; default: the newest save's")
    ap.add_argument("--no-screws", action="store_true", help="veto any alt that takes Screws")
    a = ap.parse_args()
    if not a.ranking:
        ap.error("no ranking CSV: pass --ranking or set SATISFACTORY_RANKING_CSV")
    ranking = load_ranking(a.ranking)

    print("# fetching live save state (unlocked recipes, pending hard drives)...")
    # ponytail: --world scopes these two reads; recipe detail and building checks use the
    # newest save. Fine while you play the world you ask about.
    unlocked_text = world.unlocked_recipes(world=a.world, limit=500)
    pending_text = harddrives.list_pending_hard_drive_choices(world=a.world, limit=500)
    print(unlocked_text.splitlines()[0])  # save identity line, so output names its source

    owned_names = []
    for line in unlocked_text.splitlines():
        if line.startswith("Alternate:"):
            owned_names.append(line.split("\t")[0].replace("Alternate:", "").strip())

    owned_set = set(owned_names)
    owned_rows = [evaluate(n, ranking, no_screws=a.no_screws) for n in owned_names]
    print_table("Currently unlocked alt recipes", owned_rows)

    # Every drive is a straight A-vs-B choice, and "where does the published ranking
    # put it" is a question worth answering in one glance -- so this table always
    # carries BOTH options' published tier/score up front, before any doctrine flags,
    # rather than only showing the score of whichever option happens to win.
    print("\n== Pending hard drives: published ranking vs. this save's doctrine ==")
    header = f"{'Drive':<6} {'Option A':<24} {'Ranking':<24} {'Option B':<24} {'Ranking':<24}"
    print(header)
    print("-" * len(header))
    drives = []
    for line in pending_text.splitlines():
        m = re.match(r"^(\d+)\t(\d+)\t(.+)$", line)
        if not m:
            continue
        drive_id, rerolls, options_text = m.groups()
        options = [o.strip() for o in options_text.split("|")]
        pair_rows = []
        for opt in options:
            name_m = re.match(r"Alternate:\s*([^(]+)", opt)
            if not name_m:
                # Non-recipe unlock (inventory slots, etc.): live but unranked --
                # dropping it used to hide the whole drive from the report.
                name = opt.split(" (")[0].strip()
                pair_rows.append({"name": name, "tier": "?", "score": None, "dead_flags": [],
                                  "caveats": [f"not a recipe: {opt[len(name):].strip()}"]})
                continue
            name = name_m.group(1).strip()
            pair_rows.append(evaluate(name, ranking, owned_names=owned_set, no_screws=a.no_screws))
        if len(pair_rows) != 2:
            continue
        drives.append((drive_id, int(rerolls), pair_rows))
    # Best drive first: ranked by its best non-dead option's published score.
    drives.sort(key=lambda d: -best_score(d[2]))
    for drive_id, _, pair_rows in drives:
        a, b = sorted(pair_rows, key=lambda r: -(r["score"] if r["score"] is not None else -1))
        print(f"{drive_id:<6} {a['name']:<24} {fmt_tier_score(a):<24} {b['name']:<24} {fmt_tier_score(b):<24}")

    print("\n== Doctrine check + verdict per drive ==")
    for drive_id, rerolls, pair_rows in drives:
        print(f"\ndrive {drive_id} (rerolls left: {rerolls})")
        for r in sorted(pair_rows, key=lambda r: (r["score"] is None, -(r["score"] or 0))):
            flag_text = "; ".join(all_flags(r)) if all_flags(r) else "clean"
            print(f"  [{fmt_tier_score(r, verbose=True)}]  {r['name']:<24} {flag_text}")
        print(f"  -> {verdict(rerolls, pair_rows)}")

if __name__ == "__main__":
    main()
