"""Cutting one solved plan into named sites, and reporting what crosses the boundaries.

This is deliberately NOT a joint multi-site solver, and the reason came from the planner
who asked for one:

> The thing that actually bit me wasn't optimisation across sites -- it was accounting
> across sites.

Their three-module split (rig / generator hall / resin plant) turned out to be
preference-driven, not physics-driven: only the rig's siting is forced, by water being
drawable at sea level. A joint LP with no per-site cap would collapse all three into one
site -- and that collapse would be *correct*, because nothing in the model prices distance.
Honouring a preference the user never expressed as a constraint would be the optimiser
being wrong.

So what is built here is the half that has a defensible answer: **declare a partition, and
report it.** Every flow crossing a boundary, as item, rate, direction and carrier count.

The error worth catching
------------------------
Reconciling the modules by hand, the planner computed generator count from the rig's total
fuel output -- assuming all 9,200 m3/min reached the generator hall. It did not; the resin
plant's plastic cycle consumed some of it. The number was wrong by a whole interface.

A partition cannot make that mistake, because the plan it cuts is already mass-balanced by
the LP's equality rows. What it CAN do is state where each flow lands -- 8,060 to the hall,
1,140 to the resin plant -- so the assumption is never available to make. And when a
partition is incomplete, the unassigned processes are named rather than quietly folded into
"external", because an unassigned refinery is precisely how a supplier goes missing.

A process cannot be split across sites
--------------------------------------
The unit of assignment is a PROCESS, not a machine, so shared infrastructure has to live
wholly in one site and export. This surfaced immediately on the reference plan: the design
says the resin plant draws its own ~1,100 m3/min from its own shore, but the partition put
all 64 Water Extractors in the rig, so the table reports `A-rig -> C-resin Water 1100`.
Both are physically valid builds and the written design had never said which was meant --
the tool exposing that ambiguity is it working.

The cost is real though: a water farm serving two coastal sites appears as an interface
that may not exist on the ground. Splitting a process would mean per-MACHINE assignment,
which is a much larger idea and would break the "a machine is in one place" rule that
makes `contested` meaningful. Stated here rather than worked around.

Why a zero interface is a row
-----------------------------
The decoupled design is characterised entirely by ONE flow being zero: no fuel returns from
the resin plant. That is the insight the whole architecture rests on, and a table that
printed only nonzero flows would omit it. Interfaces the caller names are reported at zero
rather than dropped.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ...core.gamedata.model import GameData
from .carrier import carrier_for
from .slice import PlanSlice, slice_of

__all__ = ["Interface", "Site", "SitePlan", "partition"]

#: Net rate below this is LP noise rather than a flow between two places.
_EPS = 1e-6


@dataclass
class Site:
    name: str
    patterns: list[str] = field(default_factory=list)
    slice: PlanSlice | None = None

    @property
    def machines(self) -> int:
        return self.slice.machines if self.slice else 0

    @property
    def net_mw(self) -> float:
        # Partial slices carry no sink share, so this is generation minus draw only.
        return self.slice.net_mw if self.slice else 0.0


@dataclass
class Interface:
    item: str
    name: str
    source: str
    target: str
    rate: float
    carrier: str
    lines: int

    @property
    def zero(self) -> bool:
        return abs(self.rate) <= _EPS


@dataclass
class SitePlan:
    sites: list[Site] = field(default_factory=list)
    interfaces: list[Interface] = field(default_factory=list)
    #: Processes matching no site. Named, never folded into "external": an unassigned
    #: refinery is exactly how a supplier goes missing from a hand reconciliation.
    unassigned: list[dict] = field(default_factory=list)
    #: Processes matching more than one site, with the sites that claimed them. A
    #: machine cannot be in two places, and first-wins would hide the ambiguity.
    contested: list[tuple[str, list[str]]] = field(default_factory=list)
    #: Items some site consumes that no site produces and no extractor supplies.
    unsupplied: list[tuple[str, str]] = field(default_factory=list)
    #: Declared sites that matched no process at all, and patterns that matched none.
    #: A site silently coming back empty is how a whole building's flows vanish from the
    #: interface table -- see the module docstring.
    empty: list[str] = field(default_factory=list)
    dead_patterns: list[tuple[str, str]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not (self.unassigned or self.contested or self.unsupplied or self.empty)


#: The spellings ``exports`` already accepts for grid power, so a site can be keyed on
#: the thing it exists to produce. A generator hall's product is MW -- the ``__MW__``
#: sentinel, which is no process label -- and a caller who wrote ``{"hall": ["MW"]}``
#: got an EMPTY site: 460 generators fell into `unassigned` and the 9,200 m3/min fuel
#: interface, the one flow the whole multi-building design turns on, vanished from the
#: table. Warning about the empty site was honest; refusing the obvious spelling was
#: still the gap.
_POWER_TOKENS = frozenset({"mw", "power", "__mw__"})


def _matches(proc: dict, pattern: str) -> bool:
    """Same widening match as ``exclude_recipes``: label, building name, building id --
    plus MW/power, which matches every generator, because power is a product a site can
    be defined by.

    Deliberately the grammar a caller already knows. "Fuel-Powered Generator" names a
    synthesised process that has no recipe at all, which is why matching on the recipe
    set alone would leave generators unassignable.
    """
    needle = pattern.strip().casefold()
    if not needle:
        return False
    if needle in _POWER_TOKENS:
        # Exact token, not substring: "power" as a substring would also claim every
        # "Coal-Powered Generator" LABEL -- true but redundant -- and, worse, any future
        # process whose label merely contains the word.
        return proc.get("kind") == "generator"
    return any(
        needle in str(proc.get(field_) or "").casefold()
        for field_ in ("label", "building", "building_id", "recipe")
    )


def partition(
    prepared,
    game: GameData,
    spec: dict[str, list[str]],
) -> SitePlan:
    """Cut a solved plan into named sites and report what crosses between them.

    ``spec`` maps a site name to patterns, matched exactly as ``exclude_recipes`` matches:
    against the process label, its building, or its recipe.
    """
    out = SitePlan()
    if prepared.solution is None:
        out.notes.append("no solved plan to partition")
        return out

    processes = prepared.solution.processes
    claims: dict[str, list[str]] = {}
    for proc in processes:
        owners = [
            name for name, patterns in spec.items() if any(_matches(proc, p) for p in patterns)
        ]
        if not owners:
            out.unassigned.append(proc)
        elif len(owners) > 1:
            out.contested.append((proc["label"], owners))
        claims[proc["pid"]] = owners

    for name, patterns in spec.items():
        site = Site(name=name, patterns=list(patterns))
        site.slice = slice_of(
            prepared,
            game,
            keep=lambda p, n=name: claims.get(p["pid"], []) == [n],
            label=name,
        )
        out.sites.append(site)

    # An item's producers and consumers across sites. The plan is mass-balanced by the
    # LP's equality rows, so within a COMPLETE partition every consumed item has a
    # producer; the interesting output is where each flow lands, not whether it balances.
    sc = prepared.request.scenario
    for item in sorted({i for s in out.sites if s.slice for i in s.slice.flows}):
        makers = [
            (s.name, s.slice.flows[item])
            for s in out.sites
            if s.slice and s.slice.flows.get(item, 0.0) > _EPS
        ]
        takers = [
            (s.name, -s.slice.flows[item])
            for s in out.sites
            if s.slice and s.slice.flows.get(item, 0.0) < -_EPS
        ]
        if not takers:
            continue
        if not makers:
            for taker, _ in takers:
                out.unsupplied.append((taker, game.item_name(item)))
            continue
        # Proportional attribution. The LP gives net balances, never who fed whom -- the
        # same reason `layout` models a bus rather than producer-consumer pairs. Splitting
        # a shared flow by share is the only answer that is not invented.
        supply = sum(v for _, v in makers)
        for taker, wanted in takers:
            for maker, made in makers:
                rate = wanted * (made / supply) if supply else 0.0
                if maker == taker:
                    continue
                line = carrier_for(game, item, sc.belt_ipm, sc.pipe_m3min)
                out.interfaces.append(
                    Interface(
                        item=item,
                        name=game.item_name(item),
                        source=maker,
                        target=taker,
                        rate=rate,
                        carrier=line.kind,
                        lines=line.lines_for(rate),
                    )
                )

    # A site that matched nothing, and a pattern that matched nothing. Both were silent,
    # and the silence cost a caller the entire fuel interface: they keyed a generator site
    # on the item it produces (MW), the site came back empty, its 460 generators landed in
    # `unassigned`, and the one flow the whole multi-building design turns on was missing
    # from the table. MW now matches generators (see `_POWER_TOKENS`), so this loudness is
    # for the patterns that still hit nothing -- a typo, or a recipe this plan does not run.
    for site in out.sites:
        if site.machines == 0:
            out.empty.append(site.name)
        for pattern in site.patterns:
            if not any(_matches(proc, pattern) for proc in processes):
                out.dead_patterns.append((site.name, pattern))

    out.interfaces.sort(key=lambda i: -i.rate)
    for name in out.empty:
        out.notes.append(
            f"site {name!r} matched NO process, so nothing it should contain is in the "
            "interface table. Patterns match a process LABEL, its building or its recipe "
            "-- plus 'MW' or 'power', which claims every generator -- never any other "
            "item a process produces"
        )
    for name, pattern in out.dead_patterns[:4]:
        out.notes.append(f"{name}: pattern {pattern!r} matches nothing in this plan")
    if out.unassigned:
        out.notes.append(
            f"{len(out.unassigned)} process(es) match no site: "
            + ", ".join(sorted({p["label"] for p in out.unassigned})[:5])
            + ". Their flows are NOT in the interface table, so it is incomplete -- an "
            "unassigned producer is exactly how a supplier goes missing"
        )
    for label, owners in out.contested[:4]:
        out.notes.append(f"{label} matches {' and '.join(owners)}; a machine is in one place")
    for site, item in out.unsupplied[:4]:
        out.notes.append(f"{site} consumes {item} and no site produces it")
    return out
