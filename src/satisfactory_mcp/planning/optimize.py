"""LP/MILP factory optimizer.

Formulation
-----------
One variable per *process* -- a recipe at a fixed (clock, sloops) mode, an extractor
on a given resource+purity, a generator on a given fuel, or a sink. Plus one
variable per raw input, export and sink flow.

Power is modelled as a pseudo-item ``__MW__`` so the power balance is just another
mass-balance row. That keeps every objective linear.

**Every item's balance is an equality.** ``net >= 0`` is wrong: a byproduct with no
consumer does not vanish, it fills a pipe and stalls the line. Exports and sinks are
explicit whitelists, so anything not exportable or sinkable must be consumed exactly.

Two-phase lexicographic solve is mandatory: with machine counts only bounded below,
any larger count is equally optimal, and an unguarded solve returns counts of 1e12.
Phase 1 optimises the goal; phase 2 pins it and minimises machines.

Clocks are discrete modes, never a continuous variable: power is ``c**1.32``, which
is non-convex, and for fixed throughput power strictly decreases in machine count,
so a min-power objective would drive machines to infinity.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from scipy.optimize import LinearConstraint, milp

from ..docs.constants import AWESOME_SINK_MW
from ..docs.model import GameData

__all__ = ["MW", "Process", "Scenario", "Solution", "free_lunch_audit", "solve"]

MW = "__MW__"
_EPS = 1e-7


@dataclass
class Process:
    """One column of the matrix."""

    pid: str
    kind: str  # recipe | extractor | generator
    label: str
    rates: dict[str, float]  # item -> net per-minute for ONE unit
    mw: float  # net MW for one unit: negative consumes, positive generates
    building: str | None = None
    recipe: str | None = None
    clock: float = 1.0
    sloops: int = 0
    max_count: float | None = None

    def net(self, item: str) -> float:
        return self.rates.get(item, 0.0)


@dataclass
class Scenario:
    """Inputs to a solve."""

    game: GameData
    recipes: list[str]  # allowed recipe ids
    objective: str = "max_mw"  # max_mw | max_item | min_raw | min_machines | min_power
    target_item: str | None = None
    #: Items that may leave the system. MW-only is the default for a power plant, but
    #: it makes a crude-oil plant infeasible, because every crude->fuel route emits
    #: Polymer Resin and resin only terminates in plastic or rubber.
    exports: tuple[str, ...] = (MW,)
    export_minimums: dict[str, float] = field(default_factory=dict)
    raw_caps: dict[str, float] = field(default_factory=dict)
    extractor_nodes: dict[tuple[str, str, str], int] = field(default_factory=dict)
    allow_sinks: bool = True
    clocks: tuple[float, ...] = (1.0,)
    sloop_budget: int = 0
    max_machines: float | None = None
    belt_ipm: float = 780.0  # Mk5; used to price sink and logistics lines
    pipe_m3min: float = 600.0
    integral: bool = False
    buildings_available: set[str] | None = None
    #: MW the plant may draw from the existing grid.
    #:
    #: Without this the power row forces generation == consumption, i.e. every plan
    #: must be fully self-powered -- which silently reports 0 output for any factory
    #: that has no on-site generator able to burn its own byproducts.
    #: Ignored (forced to 0) when MW is an export, since a power plant that imports
    #: power to export it is unbounded.
    grid_import_mw: float | None = None


@dataclass
class Solution:
    status: str
    objective_value: float
    net_mw: float
    processes: list[dict]
    raw_used: dict[str, float]
    exports: dict[str, float]
    sunk: dict[str, float]
    machines_total: float
    grid_import_mw: float = 0.0
    warnings: list[str] = field(default_factory=list)
    binding: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.status == "optimal"


# --------------------------------------------------------------------- processes


def recipe_processes(sc: Scenario) -> list[Process]:
    g = sc.game
    out: list[Process] = []
    for rid in sc.recipes:
        r = g.recipes.get(rid)
        if r is None or r.kind != "part":
            continue
        b = g.machine(r)
        if b is None:
            continue
        if sc.buildings_available is not None and b.cls not in sc.buildings_available:
            continue
        sloop_options = [0]
        if sc.sloop_budget and b.can_boost:
            sloop_options = sorted({0, b.sloop_slots})
        for clock in sc.clocks:
            for sloops in sloop_options:
                boost = b.boost_for(sloops)
                rates: dict[str, float] = {}
                for f in r.ingredients:
                    rates[f.item] = rates.get(f.item, 0.0) - f.per_min * clock
                for f in r.products:
                    rates[f.item] = rates.get(f.item, 0.0) + f.per_min * clock * boost
                mw = -g.recipe_power_mw(r, clock, sloops)
                suffix = ""
                if clock != 1.0:
                    suffix += f"@{clock:g}"
                if sloops:
                    suffix += f"+{sloops}sl"
                out.append(
                    Process(
                        # The resource must be in the pid. A pid built from
                        # building+purity alone silently merged coal and sulfur
                        # miners into one column that produced both.
                        pid=f"r:{rid}{suffix}",
                        kind="recipe",
                        label=f"{r.name}{' ' + suffix if suffix else ''}",
                        rates=rates,
                        mw=mw,
                        building=b.cls,
                        recipe=rid,
                        clock=clock,
                        sloops=sloops,
                    )
                )
    return out


def extractor_processes(sc: Scenario) -> list[Process]:
    g = sc.game
    out: list[Process] = []
    for (building, resource, purity), count in sc.extractor_nodes.items():
        b = g.buildings.get(building)
        if b is None or not b.base_extract_rate or count <= 0:
            continue
        if sc.buildings_available is not None and building not in sc.buildings_available:
            continue
        for clock in sc.clocks:
            rate = b.extract_rate(purity, clock)
            out.append(
                Process(
                    pid=f"x:{building}:{resource}:{purity}@{clock:g}",
                    kind="extractor",
                    label=f"{b.name} on {purity} {g.item_name(resource)}",
                    rates={resource: rate},
                    mw=-b.power_at(clock),
                    building=building,
                    clock=clock,
                    max_count=count,
                )
            )
    return out


def generator_processes(sc: Scenario) -> list[Process]:
    g = sc.game
    out: list[Process] = []
    for cls, b in g.buildings.items():
        if not b.is_generator or not b.power_production_mw:
            continue
        if sc.buildings_available is not None and cls not in sc.buildings_available:
            continue
        for fuel in b.fuels:
            item = g.items.get(fuel.fuel_class)
            if item is None or not item.energy_mj:
                continue
            rates = {fuel.fuel_class: -b.fuel_rate_per_min(item)}
            if b.requires_supplemental:
                supp = fuel.supplemental_class or "Desc_Water_C"
                rates[supp] = rates.get(supp, 0.0) - b.supplemental_m3_min()
            if fuel.byproduct_class and fuel.byproduct_amount:
                burn_s = item.energy_mj / b.power_production_mw
                rates[fuel.byproduct_class] = (
                    rates.get(fuel.byproduct_class, 0.0) + fuel.byproduct_amount * 60 / burn_s
                )
            out.append(
                Process(
                    pid=f"g:{cls}:{fuel.fuel_class}",
                    kind="generator",
                    label=f"{b.name} on {item.name}",
                    rates=rates,
                    mw=b.power_production_mw,
                    building=cls,
                )
            )
    return out


def build_processes(sc: Scenario) -> list[Process]:
    procs = [*recipe_processes(sc), *extractor_processes(sc), *generator_processes(sc)]
    seen: dict[str, Process] = {}
    for p in procs:
        if p.pid in seen:
            # Guard, not a nicety: a duplicate pid merges two columns and produces a
            # plausible, mass-balanced, WRONG answer.
            raise AssertionError(f"duplicate process id {p.pid!r}")
        seen[p.pid] = p
    return procs


# ------------------------------------------------------------------------ solve


def _sinkable(sc: Scenario, item_id: str) -> bool:
    if not sc.allow_sinks:
        return False
    it = sc.game.items.get(item_id)
    return bool(it and it.sinkable)


def solve(sc: Scenario) -> Solution:
    g = sc.game
    procs = build_processes(sc)
    if not procs:
        return Solution(
            "infeasible", 0.0, 0.0, [], {}, {}, {}, 0.0, 0.0, ["no processes available"]
        )

    items = sorted({i for p in procs for i in p.rates})
    raw_items = sorted(sc.raw_caps)
    export_items = sorted(set(sc.exports) | set(sc.export_minimums))
    sink_items = sorted(i for i in items if _sinkable(sc, i))

    exporting_power = MW in export_items
    # A power plant must be self-contained; anything else may draw from the grid.
    grid_cap = (
        0.0 if exporting_power else (np.inf if sc.grid_import_mw is None else sc.grid_import_mw)
    )

    nP, nR, nE, nS = len(procs), len(raw_items), len(export_items), len(sink_items)
    n = nP + nR + nE + nS + 1  # trailing column: grid import

    def col_p(i: int) -> int:
        return i

    def col_r(i: int) -> int:
        return nP + i

    def col_e(i: int) -> int:
        return nP + nR + i

    def col_s(i: int) -> int:
        return nP + nR + nE + i

    col_grid = n - 1

    rows: list[np.ndarray] = []
    rhs: list[float] = []

    # ---- per-item equality balance (the crux) --------------------------
    for item in items + [i for i in raw_items if i not in items]:
        row = np.zeros(n)
        for i, p in enumerate(procs):
            row[col_p(i)] = p.net(item)
        if item in raw_items:
            row[col_r(raw_items.index(item))] = 1.0
        if item in export_items:
            row[col_e(export_items.index(item))] = -1.0
        if item in sink_items:
            row[col_s(sink_items.index(item))] = -1.0
        rows.append(row)
        rhs.append(0.0)

    # ---- power balance, as just another item ---------------------------
    power_row = np.zeros(n)
    for i, p in enumerate(procs):
        power_row[col_p(i)] = p.mw
    # Sinking costs power: 30 MW per AWESOME Sink, one sink per belt line.
    for j, item in enumerate(sink_items):
        power_row[col_s(j)] = -AWESOME_SINK_MW / max(sc.belt_ipm, 1.0)
    power_row[col_grid] = 1.0  # grid import supplies MW
    if exporting_power:
        power_row[col_e(export_items.index(MW))] = -1.0
    rows.append(power_row)
    rhs.append(0.0)

    A_eq = np.vstack(rows)
    constraints = [LinearConstraint(A_eq, np.array(rhs), np.array(rhs))]

    lb = np.zeros(n)
    ub = np.full(n, np.inf)
    for i, p in enumerate(procs):
        if p.max_count is not None:
            ub[col_p(i)] = p.max_count
    for j, item in enumerate(raw_items):
        ub[col_r(j)] = sc.raw_caps[item]
    for j, item in enumerate(export_items):
        if item in sc.export_minimums:
            lb[col_e(j)] = sc.export_minimums[item]
    ub[col_grid] = grid_cap

    # ---- somersloop budget --------------------------------------------
    if sc.sloop_budget:
        row = np.zeros(n)
        used = False
        for i, p in enumerate(procs):
            if p.sloops:
                row[col_p(i)] = p.sloops
                used = True
        if used:
            constraints.append(LinearConstraint(row, -np.inf, sc.sloop_budget))

    if sc.max_machines is not None:
        row = np.zeros(n)
        for i in range(nP):
            row[col_p(i)] = 1.0
        constraints.append(LinearConstraint(row, -np.inf, sc.max_machines))

    integrality = np.zeros(n)
    if sc.integral:
        for i in range(nP):
            integrality[col_p(i)] = 1

    # ---- phase 1: the goal --------------------------------------------
    c = np.zeros(n)
    if sc.objective == "max_mw":
        if MW not in export_items:
            return Solution(
                "infeasible",
                0.0,
                0.0,
                [],
                {},
                {},
                {},
                0.0,
                0.0,
                ["objective max_mw requires __MW__ in exports"],
            )
        c[col_e(export_items.index(MW))] = -1.0
    elif sc.objective == "max_item":
        if not sc.target_item or sc.target_item not in export_items:
            return Solution(
                "infeasible",
                0.0,
                0.0,
                [],
                {},
                {},
                {},
                0.0,
                0.0,
                [f"objective max_item requires {sc.target_item!r} in exports"],
            )
        c[col_e(export_items.index(sc.target_item))] = -1.0
    elif sc.objective == "min_raw":
        for j in range(nR):
            c[col_r(j)] = 1.0
    elif sc.objective == "min_machines":
        for i in range(nP):
            c[col_p(i)] = 1.0
    elif sc.objective == "min_power":
        for i, p in enumerate(procs):
            if p.mw < 0:
                c[col_p(i)] = -p.mw
    else:
        return Solution(
            "infeasible",
            0.0,
            0.0,
            [],
            {},
            {},
            {},
            0.0,
            0.0,
            [f"unknown objective {sc.objective!r}"],
        )

    res = milp(c=c, constraints=constraints, integrality=integrality, bounds=(lb, ub))
    if not res.success or res.x is None:
        return Solution(
            "infeasible",
            0.0,
            0.0,
            [],
            {},
            {},
            {},
            0.0,
            0.0,
            [f"phase 1 infeasible: {res.message}"],
        )
    goal = float(c @ res.x)

    # ---- phase 2: pin the goal, minimise machines ----------------------
    warnings: list[str] = []
    x = res.x
    if sc.objective not in ("min_machines",):
        pin = LinearConstraint(c.reshape(1, -1), goal - 1e-6, goal + 1e-6)
        c2 = np.zeros(n)
        for i in range(nP):
            c2[col_p(i)] = 1.0
        res2 = milp(
            c=c2,
            constraints=[*constraints, pin],
            integrality=integrality,
            bounds=(lb, ub),
        )
        if res2.success and res2.x is not None:
            x = res2.x
        else:
            warnings.append("phase 2 (minimise machines) failed; counts are not minimal")

    # ---- read out -----------------------------------------------------
    out_procs = []
    machines_total = 0.0
    for i, p in enumerate(procs):
        v = float(x[col_p(i)])
        if v <= _EPS:
            continue
        machines_total += v
        out_procs.append(
            {
                "pid": p.pid,
                "kind": p.kind,
                "label": p.label,
                "building": g.buildings[p.building].name
                if p.building in g.buildings
                else p.building,
                "building_id": p.building,
                "recipe": p.recipe,
                "machines": round(v, 4),
                "clock": p.clock,
                "sloops": p.sloops,
                "mw": round(v * p.mw, 2),
            }
        )
    out_procs.sort(key=lambda d: -abs(d["mw"]))

    raw_used = {raw_items[j]: round(float(x[col_r(j)]), 4) for j in range(nR) if x[col_r(j)] > _EPS}
    exports = {
        export_items[j]: round(float(x[col_e(j)]), 4) for j in range(nE) if x[col_e(j)] > _EPS
    }
    sunk = {sink_items[j]: round(float(x[col_s(j)]), 4) for j in range(nS) if x[col_s(j)] > _EPS}
    grid_draw = round(float(x[col_grid]), 2)
    net_mw = exports.get(MW, 0.0) - grid_draw

    binding = []
    for i, p in enumerate(procs):
        if p.max_count is not None and x[col_p(i)] >= p.max_count - 1e-6 and p.max_count > 0:
            binding.append(f"{p.label}: all {p.max_count:g} available")
    for j, item in enumerate(raw_items):
        if x[col_r(j)] >= sc.raw_caps[item] - 1e-6:
            binding.append(f"{g.item_name(item)} capped at {sc.raw_caps[item]:g}")

    if sunk:
        warnings.append(
            "plan sinks "
            + ", ".join(f"{v:g} {g.item_name(k)}/min" for k, v in sunk.items())
            + " -- needs a belt to an AWESOME Sink or the line stalls"
        )
    if grid_draw > _EPS:
        warnings.append(
            f"plan draws {grid_draw:g} MW from the existing grid (it is not self-powered)"
        )
    if any(p["clock"] < 1.0 for p in out_procs):
        warnings.append(
            "gain depends on sub-100% clocks: underclocking trades many more machines "
            "for less power per unit"
        )

    return Solution(
        status="optimal",
        objective_value=round(-goal if sc.objective.startswith("max") else goal, 4),
        net_mw=net_mw,
        processes=out_procs,
        raw_used=raw_used,
        exports=exports,
        sunk=sunk,
        machines_total=round(machines_total, 3),
        grid_import_mw=grid_draw,
        warnings=warnings,
        binding=binding,
    )


def free_lunch_audit(sc: Scenario) -> tuple[bool, float]:
    """Strip every matter source and maximise MW. Must return exactly 0.

    A non-zero result means some recipe cycle creates matter from nothing -- which a
    mass-balanced model can still do if a column is malformed. Run on every solve.
    """
    probe = Scenario(
        game=sc.game,
        recipes=sc.recipes,
        objective="max_mw",
        exports=(MW,),
        raw_caps={},
        extractor_nodes={},
        allow_sinks=False,
        clocks=sc.clocks,
        buildings_available=sc.buildings_available,
    )
    sol = solve(probe)
    value = sol.net_mw if sol.ok else 0.0
    return (abs(value) < 1e-6), value
