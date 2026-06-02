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

Which wave you are in, read back from the save
----------------------------------------------
``track`` closes the loop. A wave is a partition of a stored plan, and ``build_diff``
already matches built machines against that plan by identity, so grouping its output by
wave says which stage the player is actually in without persisting anything.

**Built and energised are different states, and the save distinguishes them only in one
direction.** Measured on the reference save rather than assumed:

* ``uptime`` -- the 300 s productivity monitor -- is present on 517 of 566 machines,
  extractors and generators, and ``produce_s > 0`` PROVES the machine ran, which proves
  it had power. That is the only positive evidence of energisation in the file.
* ``produce_s == 0`` proves nothing. Unpowered, starved, blocked and merely idle are
  indistinguishable, and ``graph.health`` already separates the three that have a supply
  cause -- what is left is its ``stalled`` bucket, where an unpowered block would land
  along with a monitor that has not caught up.
* ``paused`` (``mIsProductionPaused``, 16 actors here) is a DIFFERENT thing: the player
  switched the machine off, and it is recorded per machine whatever the grid is doing.
* ``clock`` is a slider position, not a state, and does not move when power does.
* Power wires ARE in the projection (1,287 edges), so "wired to nothing" is knowable --
  but wired is not energised.
* The direct answers are not in the file at all. ``mHasPower`` and ``mCircuitID`` on
  ``UFGPowerInfoComponent`` carry no ``SaveGame`` specifier (checked in Headers.zip) and
  appear zero times in 44,307 objects; ``BP_CircuitSubsystem`` saves an EMPTY property
  set, so grid membership is rebuilt at load and never persisted. ``mIsSwitchOn`` IS a
  SaveGame property of ``AFGBuildableCircuitSwitch``, but this world has built no power
  switch at all and the projection does not read one.

So a stage reports what it can prove -- ``running`` -- and refuses to convert silence
into "unpowered". A fully built, wholly dark block is a valid and expected state under
the Q1 re-frame, not an anomaly, and is reported as such.
"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass, field

from ..core.gamedata.model import GameData
from ..domain.world.state import WorldState
from ..graph.health import assess
from .diff import DiffReport, group_key

__all__ = [
    "DARK_STATES",
    "MONITORED_STATES",
    "RUNNING_STATES",
    "Commissioning",
    "Energised",
    "Stage",
    "StageRow",
    "Tracking",
    "Wave",
    "commission",
    "live_feeders",
    "track",
]

#: ``graph.health`` states that PROVE a machine was energised. Both mean it produced
#: inside the last complete 300 s window, and a machine with no power produces nothing.
#: Every other state is silence, and silence has several causes.
RUNNING_STATES = frozenset({"saturated", "intermittent"})

#: The states where "no power" is still a live explanation. ``blocked`` and ``starved``
#: name a supply cause instead; ``stalled`` is health.py's own word for "has input,
#: output not full, still not running", which is exactly where an unpowered block lands
#: -- and also where a monitor that has not caught up lands, so it is never conclusive.
DARK_STATES = frozenset({"stalled", "unmonitored"})

#: States ``graph.health`` can only reach by reading the productivity monitor. If none of
#: a plan's machines land in one, the save carries no uptime evidence at all, and the
#: report has to say so instead of reading silence as "nothing is running". The committed
#: test projection is exactly that case: it predates the monitor being extracted.
MONITORED_STATES = frozenset({"saturated", "intermittent", "blocked", "starved", "stalled"})

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
    #: Solution process id, kept so a wave row can be joined back to the build job the
    #: diff matched against the save. Without it the two halves of "which stage am I in"
    #: would have to be re-derived from labels, which are display strings.
    pid: str = ""
    #: One cycle of this process at its own clock, in seconds. A recipe's duration or an
    #: extractor's extract cycle; 0 for a generator, which burns continuously and has no
    #: cycle to wait through.
    cycle_s: float = 0.0


@dataclass
class Wave:
    index: int
    rows: list[Energised] = field(default_factory=list)
    available_before: float = 0.0

    def fill_s(self) -> float:
        """Lower bound, in seconds, on the wait before this wave's generators produce.

        The wait is where the risk lives: the wave's draw is already being paid and
        nothing is coming back yet, so "wait" without a number is the least actionable
        instruction in the sequence.

        What is computed is the CYCLE chain. Every stage must finish at least one full
        cycle before the next stage sees anything, so the sum of the slowest cycle at each
        chain depth is a hard floor -- and each is divided by its clock, because a machine
        at 250% completes in 40% of the time.

        What is deliberately NOT in it, and why this is a floor rather than an estimate:
        **pipe transit**. A pipe's fluid volume is not in Docs.json -- the only dimension
        there is ``mRadius``, which is collision geometry, and turning that into litres
        would be a guess dressed as a measurement. Route lengths are unknown anyway
        (§8.5), so on a long run the transit dominates this number. Machine input buffers
        are out for a related reason: their capacity is per-BUILT-machine and these
        machines do not exist yet.
        """
        deepest: dict[int, float] = {}
        for row in self.rows:
            if row.cycle_s <= 0:
                continue
            deepest[row.depth] = max(deepest.get(row.depth, 0.0), row.cycle_s)
        return sum(deepest.values())

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


def _cycle_s(proc: dict, game: GameData) -> float:
    """How long one cycle of this process takes at the clock the plan runs it at.

    Clock divides: a machine at 250% finishes its cycle in 40% of the base time, which is
    exactly why an overclocked extractor is not what holds up a startup.
    """
    clock = proc.get("clock") or 1.0
    recipe = game.recipes.get(proc.get("recipe") or "")
    if recipe is not None and recipe.duration_s:
        return recipe.duration_s / clock
    building = game.buildings.get(proc.get("building_id") or "")
    if building is not None and building.extract_cycle_s:
        return building.extract_cycle_s / clock
    # Generators burn continuously; there is no cycle to wait through.
    return 0.0


def _depths(processes: list[dict]) -> dict[str, int]:
    """Chain depth per process id.

    One line of real work, because ``layout.chain_depth`` already does this and does it
    properly. This function used to relax depths iteratively with a cap, which looked
    equivalent and was not: item flow is genuinely cyclic -- Recycled Plastic and Recycled
    Rubber consume each other's output -- and relaxation drives a cycle to the cap instead
    of collapsing it. Measured on a 2-cycle of that exact shape, the relaxation returned
    plastic=7, rubber=8, sink=8 where the condensation returns 1, 1, 2. Cycle members
    landed on DIFFERENT stages, which is both wrong and unbuildable: they have to go up
    together.

    It also mattered across modules. ``diff`` computes build order with ``chain_depth``,
    and ``track`` joins a wave against a diff row, so two implementations meant the two
    halves of "which stage am I in" could order the same plant differently.

    ``MW`` is filtered out here rather than inside ``chain_depth``. Power is modelled as
    an item so the balance is just another row (see optimize), but as a dependency it
    would make every consumer depend on every generator and every generator on its fuel:
    one component, and no order at all. In practice a solution row never carries MW in
    ``rates`` -- it is kept in ``mw`` -- so this is belt and braces, and cheap.
    """
    from .layout import chain_depth
    from .optimize import MW

    depths = chain_depth(
        [
            (
                [i for i, rate in p["rates"].items() if rate < 0 and i != MW],
                [i for i, rate in p["rates"].items() if rate > 0 and i != MW],
            )
            for p in processes
        ]
    )
    return {p["pid"]: d for p, d in zip(processes, depths, strict=True)}


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
                    pid=pid,
                    label=p["label"],
                    kind=p["kind"],
                    cycle_s=_cycle_s(p, game),
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


# ------------------------------------------------- which stage am I actually in


@dataclass
class StageRow:
    """One build job's share of one stage, and what the save says about it."""

    stage: int
    label: str
    kind: str
    building: str
    #: Machines this stage energises, and the plan's total for the same job.
    machines: int
    total: int
    #: Machines in the save allotted to this stage. An interval only where identity is
    #: unavailable (Water Extractors, OQ5), where a single number would be a lie in
    #: whichever direction it fell.
    built: int = 0
    built_max: int = 0
    #: graph.health state -> how many of this stage's built machines are in it.
    by_state: Counter = field(default_factory=Counter)
    draw_mw: float = 0.0
    generation_mw: float = 0.0
    #: Carried from the diff so a per-stage table can still say what to do about the row.
    #: ``verb``/``free`` are the WHOLE plan's free action for this build job -- unpausing
    #: three pumps is one job however the waves split them -- so a stage renders them as
    #: an aside and never as its own instruction.
    verb: str = "OK"
    free: int = 0
    note: str = ""

    @property
    def running(self) -> int:
        """Machines PROVEN to have had power: they produced inside the last window."""
        return sum(n for s, n in self.by_state.items() if s in RUNNING_STATES)

    @property
    def to_build(self) -> int:
        return max(0, self.machines - self.built)


@dataclass
class Stage:
    """One startup wave, matched against the save."""

    index: int
    rows: list[StageRow] = field(default_factory=list)
    draw_mw: float = 0.0
    generation_mw: float = 0.0
    available_before: float = 0.0
    available_after: float = 0.0

    @property
    def machines(self) -> int:
        return sum(r.machines for r in self.rows)

    @property
    def built(self) -> int:
        return sum(r.built for r in self.rows)

    @property
    def built_max(self) -> int:
        return sum(r.built_max for r in self.rows)

    @property
    def running(self) -> int:
        return sum(r.running for r in self.rows)

    @property
    def by_state(self) -> Counter:
        total: Counter = Counter()
        for r in self.rows:
            total.update(r.by_state)
        return total

    @property
    def complete(self) -> bool:
        return self.built >= self.machines

    @property
    def fraction_built(self) -> float:
        return self.built / self.machines if self.machines else 1.0

    @property
    def dark(self) -> int:
        """Built machines with no proof of power, and no supply cause either.

        NOT the same as "unpowered". It is the residue after the save's own explanations
        -- paused, starved, blocked, no recipe, dead node -- have been taken out, and an
        unpowered block and a monitor that has not caught up both land here.
        """
        return sum(n for s, n in self.by_state.items() if s in DARK_STATES)


@dataclass
class Tracking:
    stages: list[Stage] = field(default_factory=list)
    ok: bool = True
    #: The stage the player is in: the first one not fully built. 0 when the whole plan
    #: stands, because at that point there is no build left to be partway through and
    #: the save cannot say which block is energised.
    current: int = 0
    #: Name of the stored plan this partition came from. Empty means the numbering was
    #: derived from arguments given on the call and will renumber when they change.
    plan_name: str = ""
    warnings: list[str] = field(default_factory=list)

    @property
    def machines(self) -> int:
        return sum(s.machines for s in self.stages)

    @property
    def built(self) -> int:
        return sum(s.built for s in self.stages)

    @property
    def running(self) -> int:
        return sum(s.running for s in self.stages)

    @property
    def monitored(self) -> int:
        """Built machines whose state was decided by reading the productivity monitor.

        Zero means the save yields NO evidence about energisation either way, which is a
        different report from "nothing is running" and must never be printed as one.
        """
        return sum(n for s in self.stages for st, n in s.by_state.items() if st in MONITORED_STATES)


def _states_for(row, health: dict[str, str]) -> list[str]:
    """This build job's matched machines, running ones first.

    The order is the whole modelling decision here, so it is stated rather than left to
    dict order. Identical machines are indistinguishable in the save -- nothing records
    which Refinery was meant for wave 2 -- so built machines are allotted to the EARLIEST
    wave that wants them, and within that, the ones proven to be running go first. Both
    halves say the same thing: progress is assumed to have been made in the order the
    startup sequence prescribes. Any other rule would need evidence the file does not have.
    """
    states = [health.get(name, "unmonitored") for name in row.have_instances]
    return sorted(states, key=lambda s: (s not in RUNNING_STATES, s))


def track(
    prepared,
    run: Commissioning,
    report: DiffReport,
    game: GameData,
    state: WorldState,
    plan_name: str = "",
) -> Tracking:
    """Group a diff by startup wave: which stage is built, and which is proven running.

    Takes both halves already computed rather than recomputing either. ``commission``
    owns the partition and ``build_diff`` owns the matching; this only joins them, on
    ``diff.group_key``, so the two can never disagree about what one build job is.
    """
    out = Tracking(plan_name=plan_name)
    if not run.ok or not run.waves:
        out.ok = False
        out.warnings.append(
            "no startup order exists at this headroom, so the plan has no stages to "
            "match the save against"
        )
        return out

    by_key = {r.key: r for r in report.rows if r.key}
    key_of_pid = {p["pid"]: group_key(p) for p in prepared.solution.processes}

    # One health pass over every machine the diff matched, anywhere in the plan. Split
    # per row it would rescan the whole projection once per build job.
    matched = [name for r in report.rows for name in r.have_instances]
    health = {m.instance: m.state for m in assess("plan", matched, game, state.projection).machines}

    # Remaining pool per build job, consumed wave by wave. `low` is the pessimistic
    # count: for a row whose machines cannot be attributed at all, only the ones standing
    # among the plan's own are certainly its own, and the rest may belong to any plant.
    pool: dict[tuple, list[str]] = {}
    low: dict[tuple, int] = {}
    for key, row in by_key.items():
        pool[key] = _states_for(row, health)
        low[key] = row.have if row.have_min is None else row.have_min

    for wave in run.waves:
        stage = Stage(
            index=wave.index,
            draw_mw=wave.draw_mw,
            generation_mw=wave.generation_mw,
            available_before=wave.available_before,
            available_after=wave.available_after,
        )
        for energised in wave.rows:
            key = key_of_pid.get(energised.pid, ())
            diff_row = by_key.get(key)
            take = pool.get(key, [])[: energised.machines]
            if key in pool:
                pool[key] = pool[key][energised.machines :]
            certain = min(energised.machines, low.get(key, 0))
            low[key] = max(0, low.get(key, 0) - energised.machines)
            stage.rows.append(
                StageRow(
                    stage=wave.index,
                    label=energised.label,
                    kind=energised.kind,
                    building=energised.building,
                    machines=energised.machines,
                    total=energised.total,
                    built=certain,
                    built_max=len(take),
                    by_state=Counter(take),
                    draw_mw=energised.draw_mw,
                    generation_mw=energised.generation_mw,
                    verb=diff_row.verb if diff_row else "OK",
                    free=diff_row.count if diff_row and diff_row.verb not in ("OK", "BUILD") else 0,
                    note=diff_row.note if diff_row else "",
                )
            )
        out.stages.append(stage)

    incomplete = [s.index for s in out.stages if not s.complete]
    out.current = incomplete[0] if incomplete else 0
    if not out.monitored:
        out.warnings.append(
            "this save carries no productivity monitor for any matched machine, so "
            "there is NO evidence either way about what is energised -- only what is built"
        )
    return out


def live_feeders(g, st, floor_mw: float = 1.0) -> list[tuple[str, float]]:
    """Built extractors whose output currently reaches a running generator.

    The cutover question a startup order cannot answer on its own: which of the machines
    already on the ground are load-bearing right now. On the reference save exactly ONE of
    sixteen Oil Extractors carries all 5,000 MW of running fuel generation, and the other
    fifteen carry nothing -- so "repipe the extractors" is fifteen safe moves and one that
    browns out the base.
    """
    from ..graph.trace import power_at_risk

    out: list[tuple[str, float]] = []
    for record in st.projection.get("extractors", ()):
        instance = record["instance"].rsplit(".", 1)[-1]
        mw, _, running = power_at_risk(st, g, [instance])
        if running and mw >= floor_mw:
            building = g.buildings.get(record.get("cls", ""))
            out.append((f"{building.name if building else record.get('cls')} {instance[-10:]}", mw))
    out.sort(key=lambda pair: -pair[1])
    return out
