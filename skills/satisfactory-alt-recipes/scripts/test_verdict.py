"""Run from the repo: uv run python skills/satisfactory-alt-recipes/scripts/test_verdict.py
(prints ok; the last check reads the game Docs)."""
import os, sys
sys.path.insert(0, os.path.dirname(__file__))
from rank_recipes import evaluate, verdict

r = lambda name, score, dead=(): {"name": name, "tier": "?", "score": score, "dead_flags": list(dead), "caveats": []}

assert verdict(1, [r("A", 60), r("B", 40)]).startswith("KEEP: pick 'A'")
assert verdict(1, [r("A", 46), r("B", 42)]).startswith("REROLL")
assert verdict(0, [r("A", 46), r("B", 42)]).startswith("PICK 'A'")
assert verdict(1, [r("A", 65, ["SCREWS"]), r("B", 55)]).startswith("KEEP: pick 'B'")
assert verdict(1, [r("A", 65, ["SCREWS"]), r("B", None)]).startswith("REROLL")
# live recipe data: Bolted Frame takes Screws -- vetoed with no_screws, a caveat without
assert any("SCREWS" in f for f in evaluate("Bolted Frame", {}, no_screws=True)["dead_flags"])
bolted = evaluate("Bolted Frame", {})
assert not bolted["dead_flags"] and any("SCREWS" in c for c in bolted["caveats"]), bolted
print("ok")
