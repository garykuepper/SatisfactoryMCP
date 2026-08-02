"""Everything ``plan_factory`` has to LOOK UP before anything can be said about a plan.

Solving is ``prepare``; billing is ``slice_of``. What was left in the tool between those
two and the text was a third thing: the world lookups a plan implies -- how many water
pumps it wants and what this world's pumps already say about sea level, whether the shard
and somersloop budgets cover it, whether the research that makes a somersloop spendable
has even been bought, which buildings do not exist yet, and which named items the caller
wanted pinned into the logistics table.

None of that is presentation and none of it is solving, so it lives here, returns data,
and the presenter decides how much of it is worth a sentence.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ...core.gamedata.constants import WATER_EXTRACTOR_WARN_AT
from ...core.gamedata.model import GameData
from ..world.state import WorldState
from .optimize import MW, build_processes
from .prepare import PreparedPlan, prepare
from .scenario import resolve_item
from .slice import PlanSlice, slice_of

__all__ = ["PlanFactoryReport", "build_plan_report"]


@dataclass
class PlanFactoryReport:
    """A solved plan plus every world fact needed to comment on it."""

    prepared: PreparedPlan
    #: Shards, sloops and power for the whole plan. ``None`` when the plan failed.
    bill: PlanSlice | None = None
    #: Water Extractors this plan wants, whether or not that is worth warning about.
    water_pumps: float = 0.0
    #: ``water_volumes()`` merged with the pump footprint and its packings, and only
    #: when the count is large enough to warn about and the caller set no cap.
    water: dict | None = None
    shard_budget: dict | None = None
    sloop_budget: dict | None = None
    #: The Production Amplifier research, when it is still in the way of the budget asked.
    sloop_gate: dict | None = None
    #: Somersloops the request was allowed to spend, which is not what it spent.
    sloops_asked: int = 0
    #: Logistics entries that actually move something.
    flows: list = field(default_factory=list)
    #: Item ids from ``logistics_items`` that resolved AND appear in the flows.
    pins: list[str] = field(default_factory=list)
    #: Why the others did not, one sentence each, in the order they were asked for.
    pin_errors: list[str] = field(default_factory=list)
    #: Building classes this plan uses and this world has never built.
    needed_buildings: set[str] = field(default_factory=set)
    #: Processes above 100%, production machines first and extractors last.
    overclocked: list = field(default_factory=list)
    #: Items the caller NAMED in exports that the solution exports at zero, each with
    #: what the LP can say about why. An export is a whitelist, not a demand, so "766
    #: Plastic and 0 Rubber" is a legal optimum -- but it answered a question the caller
    #: did not ask, and silence here cost a real session exactly that.
    zero_exports: list[dict] = field(default_factory=list)


def build_plan_report(
    g: GameData,
    st: WorldState,
    plan_kwargs: dict,
    logistics_items: list[str] | None = None,
    *,
    objective: str = "",
) -> PlanFactoryReport:
    """Solve ``plan_kwargs`` and gather what this world says about the result.

    On failure the report carries ``prepared.failure`` and nothing else: every field
    below it is derived from a solution there is not one of.
    """
    prepared = prepare(g, st, plan_kwargs, objective_label=objective, audit=True)
    report = PlanFactoryReport(prepared=prepared)
    if prepared.failure:
        return report
    sol = prepared.solution

    # Water has no nodes, no purity and no geometry in any data this project can read, so
    # the extractor count is bounded by an ASSUMPTION rather than by the map.
    #
    # What that assumption stands in for was WRONG until the player corrected it: pumps go
    # on foundation platforms built out over open water, so shoreline frontage plays no
    # part. This note used to quote "m of shoreline if placed in a single line", which
    # made a perfectly ordinary 105-pump plan look impossible. Area and concrete are the
    # honest costs, and both are small.
    report.water_pumps = sum(
        p["machines"] for p in sol.processes if p.get("building_id") == "Build_WaterPump_C"
    )
    n_water = report.water_pumps
    if n_water >= WATER_EXTRACTOR_WARN_AT and not plan_kwargs.get("water_extractors"):
        pump = g.buildings.get("Build_WaterPump_C")
        size = pump.footprint if pump else None
        # Both shapes, because the length is the number you lay platforms against and
        # the block is the cheapest way to buy it. Packed, not n x footprint: that
        # product ignores shared edges and overstates the concrete by about a third.
        block = size.pack(n_water) if size else None
        pier = size.pack(n_water, columns=1) if size else None
        # OQ5 said water pumps carry no geometry and could not be matched to anything.
        # The volume SHAPE is level geometry and genuinely absent, but its IDENTITY is in
        # every pump's mExtractableResource and the sidecar has stored it all along. Sea
        # level falls out of the same rows, which turns "must be drawn at sea level" from
        # a rule of thumb into a number.
        report.water = {**st.water_volumes(), "size": size, "block": block, "pier": pier}

    # Shards and sloops, from the clocks the plan already chose. Hand-totalling this is
    # error-prone in a specific way: a shard raises the MAXIMUM clock by 0.5, so a
    # machine at 150% needs one and only a machine at 250% needs three. Assuming three
    # apiece overstates a mixed plan badly.
    report.bill = bill = slice_of(prepared, g)
    if bill.shard_rows:
        report.shard_budget = st.shard_budget()
    report.sloops_asked = int(plan_kwargs.get("sloops") or 0)
    # A sloop budget is only spendable if Production Amplifier is researched. Same class
    # of check as the unlocked recipe set and the buildable set, which build_scenario
    # already applies -- a plan using a locked capability is not a plan. It is a note
    # rather than a refusal because planning ahead of the research is legitimate, and the
    # research itself is cheap; what is not acceptable is being silent about it.
    if report.sloops_asked:
        report.sloop_gate = st.research_gate("production_boost")
    if bill.sloop_used_rows:
        report.sloop_budget = st.sloop_budget()

    # A POWER-BLIND objective drives clocks up, and the cost is invisible in its own
    # answer. Phase 2 pins the goal and minimises MACHINE COUNT, so among all solutions
    # that hit the target it picks the fewest machines -- which means the highest clocks,
    # and power goes as clock**1.32. Measured while chaining modules: a rig solved for
    # max_item Fuel ran 31 Water Extractors at 247% for 2,052 MW where 64 at 120% cost
    # 1,625, and the whole 344 MW "cost of decomposition" turned out to be this and
    # nothing else. Re-solved with min_power, the chain BEAT the single plan.
    report.overclocked = [
        p for p in sol.processes if p["clock"] > 1.01 and p["kind"] != "extractor"
    ] + [p for p in sol.processes if p["clock"] > 1.01 and p["kind"] == "extractor"]

    report.needed_buildings = {
        p["building_id"]
        for p in sol.processes
        if p["building_id"] and st.built(p["building_id"]) == 0
    }

    # An export the caller NAMED that comes out at zero. Legal -- exports is a
    # whitelist and every balance is an equality, so zero is often the optimum -- but a
    # session asked for Plastic and Rubber, got 766 Plastic and 0 Rubber, and nothing
    # said so. Three causes the LP can distinguish, cheapest evidence first: everything
    # made was eaten as an intermediate (or sunk), nothing in scope CAN make it (the
    # unmakeable note names the missing recipe or machine), or nothing rewarded making
    # it, since only the objective and export_minimums give an export value.
    zero_named = [
        item
        for item in prepared.request.scenario.exports
        if item != MW and sol.exports.get(item, 0.0) <= 1e-6
    ]
    if zero_named:
        produced: dict[str, float] = {}
        for p in sol.processes:
            for item, rate in p["rates"].items():
                if rate > 0:
                    produced[item] = produced.get(item, 0.0) + rate
        can_make = {
            item
            for proc in build_processes(prepared.request.scenario)
            for item, rate in proc.rates.items()
            if rate > 0
        }
        report.zero_exports = [
            {
                "item": item,
                "name": g.item_name(item),
                "produced": produced.get(item, 0.0),
                "sunk": sol.sunk.get(item, 0.0),
                "makeable": item in can_make,
            }
            for item in zero_named
        ]

    # Rows rank by volume, and a two-item question usually lives in the tail: at the
    # old hard cap of 6, Plastic and Rubber fell off the bottom of a large oil plan --
    # the exact pair that plan existed to size. So the cap is the caller's `limit`,
    # and named items are pinned above it whatever they rank.
    report.flows = [e for e in sol.logistics if e["rate"] > 0]
    for name in logistics_items or []:
        item_id = resolve_item(g, name)
        if item_id is None:
            report.pin_errors.append(f"logistics_items: no item matches {name!r}")
        elif not any(e["item"] == item_id for e in report.flows):
            report.pin_errors.append(
                f"logistics: nothing moves {g.item_name(item_id)} in this plan"
            )
        else:
            report.pins.append(item_id)
    return report
