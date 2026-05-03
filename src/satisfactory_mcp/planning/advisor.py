"""Rank alternate recipes by MARGINAL VALUE, via counterfactual solves.

Solve the player's objective without the candidate, then with it, and report the
delta in real units. That is a far better answer than a tier list, because a
recipe's worth depends entirely on what the player already has -- e.g. both
turbofuel alternates can be worth nothing to someone who already owns Diluted Fuel.

Methodology rules, each learned from a wrong answer during design:

* The counterfactual must add **every new recipe of the schematic**. Two alternate
  schematics carry three recipes, not one.
* The baseline must include everything the player actually has. A demo that omitted
  the 32 coal generators they had already built concluded turbofuel was worthless.
* Report deltas per objective and never collapse them into one score without naming
  the tradeoff: a recipe can be useless for power and excellent for parts.
* Deltas ramp, they do not step. Near a binding constraint a coarse sweep reports a
  flat delta and then a cliff, both wrong.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..docs.model import GameData, Recipe
from ..save.state import WorldState
from .optimize import MW, Scenario, Solution, solve

__all__ = ["CandidateVerdict", "Objective", "advise_hard_drive", "evaluate_candidates"]


@dataclass
class Objective:
    """One yardstick to measure a candidate against."""

    key: str
    description: str
    scenario_kwargs: dict
    unit: str
    higher_is_better: bool = True

    def value_of(self, sol: Solution) -> float | None:
        if not sol.ok:
            return None
        return sol.objective_value


@dataclass
class CandidateVerdict:
    schematic: str
    name: str
    new_recipes: list[str]
    new_buildings: list[str]
    deltas: dict[str, float | None] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    dependency_missing: list[str] = field(default_factory=list)
    own_output_item: str | None = None

    @property
    def any_gain(self) -> bool:
        return any(v is not None and v > 1e-6 for v in self.deltas.values())


def standard_objectives(
    game: GameData, state: WorldState, resource_caps: dict[str, float]
) -> list[Objective]:
    """A small battery, so a candidate is never judged on power alone."""
    return [
        Objective(
            key="net_mw",
            description="max net MW from the given resource basket",
            unit="MW",
            scenario_kwargs=dict(
                objective="max_mw",
                exports=(MW,),
                raw_caps=resource_caps,
                allow_sinks=True,
            ),
        ),
        Objective(
            key="mw_with_products",
            description="max net MW while exporting Plastic and Rubber",
            unit="MW",
            scenario_kwargs=dict(
                objective="max_mw",
                exports=(MW, "Desc_Plastic_C", "Desc_Rubber_C"),
                raw_caps=resource_caps,
                allow_sinks=True,
            ),
        ),
        Objective(
            key="min_machines_for_plastic",
            description="fewest machines for 300 Plastic/min",
            unit="machines",
            higher_is_better=False,
            scenario_kwargs=dict(
                objective="min_machines",
                exports=("Desc_Plastic_C",),
                export_minimums={"Desc_Plastic_C": 300.0},
                raw_caps=resource_caps,
                allow_sinks=True,
                grid_import_mw=1e6,
            ),
        ),
    ]


def _new_recipes_for(state: WorldState, schematic_id: str) -> list[Recipe]:
    s = state.game.schematics.get(schematic_id)
    if s is None:
        return []
    return state._schematic_recipes(s)


def _solve_with(
    state: WorldState,
    baseline_recipes: list[str],
    extra: list[str],
    obj: Objective,
) -> Solution:
    return solve(
        Scenario(
            game=state.game,
            recipes=[*baseline_recipes, *extra],
            buildings_available=state.unlocked_building_ids | _needed_buildings(state, extra),
            **obj.scenario_kwargs,
        )
    )


def _needed_buildings(state: WorldState, recipe_ids: list[str]) -> set[str]:
    """Buildings a candidate's recipes require.

    Included so a candidate is not silently judged infeasible for needing a machine
    the player has unlocked but never placed -- that is reported as a note instead.
    """
    out: set[str] = set()
    for rid in recipe_ids:
        r = state.game.recipes.get(rid)
        if r is not None and r.machine:
            out.add(r.machine)
    return out


def _own_output_objective(
    game: GameData, recipes: list[Recipe], resource_caps: dict[str, float], rate: float = 300.0
) -> tuple[Objective, str] | None:
    """An objective measured on what the candidate ITSELF makes.

    Without this a fixed battery reports 0 for every candidate outside its scope --
    Coated Cable makes Cable, so a plastic-and-power battery can never see its value.
    The primary product is the highest-throughput one, which is what the recipe is
    'for'.
    """
    products: dict[str, float] = {}
    for r in recipes:
        for f in r.products:
            products[f.item] = products.get(f.item, 0.0) + f.per_min
    if not products:
        return None
    item = max(products, key=lambda i: products[i])
    caps = dict(resource_caps)
    caps.setdefault("Desc_Water_C", 24000.0)
    return (
        Objective(
            key="own_output_machines",
            description=f"fewest machines for {rate:g}/min {game.item_name(item)}",
            unit="machines",
            higher_is_better=False,
            scenario_kwargs=dict(
                objective="min_machines",
                exports=(item,),
                export_minimums={item: rate},
                raw_caps=caps,
                allow_sinks=True,
                grid_import_mw=1e6,
            ),
        ),
        item,
    )


def evaluate_candidates(
    state: WorldState,
    schematic_ids: list[str],
    resource_caps: dict[str, float],
    objectives: list[Objective] | None = None,
) -> tuple[list[CandidateVerdict], dict[str, float | None]]:
    """Return one verdict per candidate, plus the baseline values."""
    game = state.game
    baseline_recipes = [r.cls for r in state.unlocked_recipes("part")]
    objs = objectives or standard_objectives(game, state, resource_caps)

    base_values: dict[str, float | None] = {}
    for obj in objs:
        base_values[obj.key] = obj.value_of(_solve_with(state, baseline_recipes, [], obj))

    verdicts: list[CandidateVerdict] = []
    for sid in schematic_ids:
        s = game.schematics.get(sid)
        new = _new_recipes_for(state, sid)
        met, missing = state.dependencies_met(sid)
        v = CandidateVerdict(
            schematic=sid,
            name=s.name if s else sid,
            new_recipes=[r.name for r in new],
            new_buildings=[],
            dependency_missing=[] if met else missing,
        )
        if s is not None and s.grants_inventory_slots:
            v.notes.append(f"grants +{s.grants_inventory_slots} inventory slots, no recipe")
        if not new:
            v.notes.append("unlocks no new recipe for this save")
            verdicts.append(v)
            continue

        for r in new:
            if r.machine and not state.can_build(r.machine):
                v.new_buildings.append(f"{game.buildings[r.machine].name} (NOT unlocked)")
            elif r.machine and state.built(r.machine) == 0:
                v.new_buildings.append(f"{game.buildings[r.machine].name} (unlocked, 0 built)")

        extra = [r.cls for r in new]
        for obj in objs:
            after = obj.value_of(_solve_with(state, baseline_recipes, extra, obj))
            before = base_values[obj.key]
            if after is None or before is None:
                v.deltas[obj.key] = None
            else:
                delta = after - before
                v.deltas[obj.key] = round(delta if obj.higher_is_better else -delta, 3)

        # Plus an objective on the candidate's own product, so it is judged on what
        # it is actually for.
        own = _own_output_objective(game, new, resource_caps)
        if own is not None:
            obj, item = own
            before = obj.value_of(_solve_with(state, baseline_recipes, [], obj))
            after = obj.value_of(_solve_with(state, baseline_recipes, extra, obj))
            v.own_output_item = game.item_name(item)
            if before is None and after is not None:
                v.notes.append(f"makes {game.item_name(item)} possible where it was not")
                v.deltas["own_output_machines"] = None
            elif before is None or after is None:
                v.deltas["own_output_machines"] = None
            else:
                v.deltas["own_output_machines"] = round(before - after, 3)
        verdicts.append(v)

    verdicts.sort(key=lambda x: -max((d or 0.0) for d in x.deltas.values() or [0.0]))
    return verdicts, base_values


def advise_hard_drive(
    state: WorldState,
    resource_caps: dict[str, float],
    hard_drive_id: int | None = None,
) -> list[dict]:
    """Compare the options on one pending drive, or summarise every drive."""
    offers = state.hard_drive_offers
    if hard_drive_id is not None:
        offers = [o for o in offers if o.hard_drive_id == hard_drive_id]
        if not offers:
            raise ValueError(f"no unclaimed hard drive with id {hard_drive_id}")

    out: list[dict] = []
    for offer in offers:
        ids = [opt["schematic"] for opt in offer.options]
        verdicts, base = evaluate_candidates(state, ids, resource_caps)
        best = verdicts[0] if verdicts else None
        out.append(
            {
                "hard_drive_id": offer.hard_drive_id,
                "rerolls_left": offer.rerolls_left,
                "baseline": base,
                "options": [
                    {
                        "name": v.name,
                        "schematic": v.schematic,
                        "new_recipes": v.new_recipes,
                        "new_buildings": v.new_buildings,
                        "deltas": v.deltas,
                        "own_output_item": v.own_output_item,
                        "notes": v.notes,
                        "blocked_by": v.dependency_missing,
                    }
                    for v in verdicts
                ],
                "suggestion": best.name
                if best and best.any_gain
                else "neither moves any objective",
            }
        )
    return out
