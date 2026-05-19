"""Bringing a plant online without blowing the fuse.

The re-frame that shrinks this whole problem: **it is not a build order, it is a startup
order.** Building costs materials, not power -- a machine draws only when it runs -- so the
entire 821-building plant can be constructed at leisure, drawing nothing, and then
energised block by block. There is no power-constrained construction sequence to search
for. There is a fixed machine set and a question of what to switch on first.

That makes the constraint one line:

    at every step, sum(energised consumer draw) <= headroom + generation from generators
    already receiving fuel

**Generators are free to energise.** Read from the dump, not assumed: a Fuel-Powered
Generator has ``power_mw == 0`` and ``power_production_mw == 250``. Only consumers --
extractors, refineries, blenders, water pumps -- spend headroom. So a wave costs its
consumers and refunds its generators, and the refund is what pays for the next wave.

Why the bound is hard
---------------------
Exceeding available power in Satisfactory does not degrade gracefully. The fuse blows and
the **whole grid stops** until it is reset by hand -- including the plant that was feeding
it. So a sequence that overshoots by 1 MW is not slightly worse, it is a manual recovery,
and every step here is checked rather than merely reported.

Fill time is why generation is counted late
-------------------------------------------
Between energising a wave's refineries and its generators actually burning fuel, the pipes
are filling and nothing is coming back. The deficit is carried for a real interval. So a
wave's own generation is NEVER used to pay for that same wave: `available` only grows once
the wave is complete. This is the difference between a sequence that works and one that
looks fine on paper and trips halfway through.

Waves are power-ordered, not ratio-balanced
-------------------------------------------
A wave energises whole machines, and at the bottom of the ramp exact ratios are not
reachable -- the first wave can afford one Water Extractor and two Refineries, where the
plan's ratio wants 0.67 of one. So early waves run starved, which is fine and
self-correcting, and it errs the safe way: a machine with no input idles and draws close
to nothing, so real draw comes in UNDER the modelled figure. The bound stays conservative
precisely because the ratios are imperfect. Said out loud rather than dressed up as a
balanced mini-plant.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from ..docs.model import GameData

__all__ = ["Commissioning", "Energised", "Wave", "commission"]

#: How many waves to attempt before giving up. A plant whose generation exceeds its draw
#: converges geometrically -- the measured Spire Coast plan needs three -- so a run that
#: reaches this many is not converging and should say so rather than spin.
MAX_WAVES = 24


@dataclass
class Energised:
    """One process, and how much of it comes on in this wave."""

    label: str
    kind: str
    building: str
    #: Machines switched on in THIS wave, and the running total against the plan.
    machines: int
    cumulative: int
    total: int
    draw_mw: float
    generation_mw: float
    #: Distance from raw extraction along the item chain. Decides switch-on order within
    #: a wave: upstream first, so the fluid is already moving when the next block lights.
    depth: int = 0


@dataclass
class Wave:
    index: int
    rows: list[Energised] = field(default_factory=list)
    available_before: float = 0.0

    @property
    def draw_mw(self) -> float:
        return sum(r.draw_mw for r in self.rows)

    @property
    def generation_mw(self) -> float:
        return sum(r.generation_mw for r in self.rows)

    @property
    def available_after(self) -> float:
        return self.available_before - self.draw_mw + self.generation_mw

    @property
    def machines(self) -> int:
        return sum(r.machines for r in self.rows)

    @property
    def waits_for_fill(self) -> bool:
        """True when this wave energises both consumers and the generators they feed.

        The pause between them is not optional and is not instant, so it is named.
        """
        return any(r.generation_mw > 0 for r in self.rows) and any(r.draw_mw > 0 for r in self.rows)


@dataclass
class Commissioning:
    headroom_mw: float = 0.0
    #: Where the headroom figure came from, printed as a labelled input so a sequence
    #: computed against a stale save is visibly stale rather than quietly wrong.
    headroom_source: str = ""
    waves: list[Wave] = field(default_factory=list)
    plant_draw_mw: float = 0.0
    plant_generation_mw: float = 0.0
    #: Cheapest slice that keeps every stage of the chain fed: one machine of every
    #: process. If this does not fit the headroom, no startup order exists at this scope.
    minimum_slice_mw: float = 0.0
    ok: bool = True
    warnings: list[str] = field(default_factory=list)

    @property
    def machines(self) -> int:
        return sum(w.machines for w in self.waves)


def _depths(processes: list[dict]) -> dict[str, int]:
    """Chain depth per process id, by relaxation.

    ``MW`` is excluded from the dependency graph on purpose. It is modelled as an item so
    the power balance is just another row (see optimize), but treating it as one here
    would make every consumer depend on every generator and every generator depend on its
    fuel -- one strongly connected component, and no order at all.

    Relaxation rather than a topological sort because item flow is not always acyclic:
    refinery loops like Residual Rubber legitimately feed themselves. A cycle settles at
    the cap instead of raising.
    """
    from .optimize import MW

    makers: dict[str, list[str]] = {}
    for p in processes:
        for item, rate in p["rates"].items():
            if rate > 0 and item != MW:
                makers.setdefault(item, []).append(p["pid"])

    depth = {p["pid"]: 0 for p in processes}
    for _ in range(len(processes)):
        changed = False
        for p in processes:
            inputs = [i for i, rate in p["rates"].items() if rate < 0 and i != MW]
            best = 0
            for item in inputs:
                for src in makers.get(item, ()):
                    if src != p["pid"]:
                        best = max(best, depth[src] + 1)
            if best > depth[p["pid"]]:
                depth[p["pid"]] = best
                changed = True
        if not changed:
            break
    return depth


def commission(
    prepared, game: GameData, headroom_mw: float, headroom_source: str = ""
) -> Commissioning:
    """Order the plan's machines into waves that can each be switched on safely."""
    out = Commissioning(headroom_mw=headroom_mw, headroom_source=headroom_source)
    if prepared.solution is None:
        out.ok = False
        return out

    procs = [p for p in prepared.solution.processes if p["machines"] > 0]
    if not procs:
        out.ok = False
        out.warnings.append("plan has no machines to energise")
        return out

    depth = _depths(procs)
    # Per MACHINE, because a wave energises whole machines. p["mw"] is the exact figure
    # for the whole row at its derived clock, so dividing is right and rounding is not.
    per: dict[str, tuple[float, float]] = {}
    for p in procs:
        each = p["mw"] / p["machines"]
        per[p["pid"]] = (max(0.0, -each), max(0.0, each))
        out.plant_draw_mw += max(0.0, -p["mw"])
        out.plant_generation_mw += max(0.0, p["mw"])

    # One machine of every consuming process: the cheapest slice that still feeds the
    # whole chain. Generators are free, so they are not part of the floor.
    out.minimum_slice_mw = sum(draw for draw, _ in per.values())
    if out.minimum_slice_mw > headroom_mw + 1e-6:
        out.ok = False
        out.warnings.append(
            f"no startup order exists at this scope: one machine of every process draws "
            f"{out.minimum_slice_mw:,.0f} MW and only {headroom_mw:,.0f} MW is free. "
            "Plan a smaller sub-plant (fewer nodes) and commission that first, or add "
            "generation before starting"
        )
        return out

    totals = {p["pid"]: p["machines"] for p in procs}
    done = {p["pid"]: 0 for p in procs}
    by_pid = {p["pid"]: p for p in procs}
    available = headroom_mw

    while sum(done.values()) < sum(totals.values()):
        if len(out.waves) >= MAX_WAVES:
            out.ok = False
            out.warnings.append(
                f"gave up after {MAX_WAVES} waves with "
                f"{sum(totals.values()) - sum(done.values())} machine(s) unstarted -- "
                "the sequence is not converging, which means generation is not "
                "outrunning draw"
            )
            return out

        wave = Wave(index=len(out.waves) + 1, available_before=available)
        # Largest fraction of the remaining plant this wave's power can carry, then
        # shrunk until the WHOLE-MACHINE bill actually fits. Ceil keeps the chain fed:
        # flooring 0.64 of an extractor to zero would light six refineries with nothing
        # to refine, which is a plausible-looking sequence that starves.
        fraction = 1.0 if out.plant_draw_mw <= 0 else min(1.0, available / out.plant_draw_mw)
        for _ in range(60):
            take = {
                pid: min(
                    totals[pid] - done[pid],
                    max(1, math.ceil(fraction * totals[pid])) if totals[pid] > done[pid] else 0,
                )
                for pid in totals
            }
            cost = sum(per[pid][0] * n for pid, n in take.items())
            if cost <= available + 1e-6:
                break
            fraction *= 0.75
        else:
            out.ok = False
            out.warnings.append("could not fit a whole-machine wave inside the headroom")
            return out

        for pid, n in sorted(take.items(), key=lambda kv: (depth[kv[0]], kv[0])):
            if n <= 0:
                continue
            done[pid] += n
            p = by_pid[pid]
            wave.rows.append(
                Energised(
                    label=p["label"],
                    kind=p["kind"],
                    building=p["building"],
                    machines=n,
                    cumulative=done[pid],
                    total=totals[pid],
                    draw_mw=per[pid][0] * n,
                    generation_mw=per[pid][1] * n,
                    depth=depth[pid],
                )
            )
        if not wave.rows:
            out.ok = False
            out.warnings.append("a wave came out empty; nothing further can be energised")
            return out
        out.waves.append(wave)
        # Only NOW does this wave's generation count. It is not available during the
        # wave: the pipes are still filling and the generators are not burning yet.
        available = wave.available_after

    return out
