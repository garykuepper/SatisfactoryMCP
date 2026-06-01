"""The optimiser surface: plans, layouts, diffs, bills of materials.

Also plan persistence, since a stored plan is a stored planning REQUEST."""

from __future__ import annotations

from typing import Annotated

from pydantic import Field

from .. import render
from ..app import Limit, _item_id, _state, game, mcp
from ..graph.resolve import resolve_factory
from ..graph.select import SelectorError
from ..planning import bom as bom_mod
from ..planning import compare
from ..planning.carrier import resolve_tiers
from ..planning.commission import Tracking, commission, track
from ..planning.diff import NEIGHBOUR_RADIUS_M as DIFF_NEIGHBOUR_M
from ..planning.diff import build_diff
from ..planning.layout_service import LayoutReport, build_layout_report
from ..planning.prepare import prepare
from ..planning.recall import PLAN_DEFAULTS
from ..planning.recall import recall_plan as _plan_kwargs
from ..planning.report import build_plan_report
from ..planning.scenario import build_scenario
from ..planning.sensitivity import sweep_unlocks
from ..presenters.text import byproducts as byproducts_text
from ..presenters.text.bom import render_bom
from ..presenters.text.compare import render_comparison
from ..presenters.text.layout import render_layout
from ..presenters.text.plan_factory import render_plan_factory

#: The stored-argument defaults, re-exported under their old home for ``server``.
_ = (PLAN_DEFAULTS,)


@mcp.tool(structured_output=False)
def list_plans(save: str | None = None, world: str | None = None) -> str:
    """Plans saved for this world, and whether the world has moved under them."""
    try:
        st = _state(save, world)
    except Exception as exc:
        return f"could not read save: {exc}"
    if not st.plans.plans:
        return render.envelope(
            f"# no plans saved for world {st.plans.world_id!r}",
            "Pass save_as=<name> to plan_factory to store one.",
        )
    rows = []
    for stored in st.plans.plans:
        try:
            req = build_scenario(st.game, st, **stored.kwargs())
            drift = "" if req.plan_id == stored.plan_id else "world moved"
        except Exception as exc:  # a stored plan can outlive the thing it referenced
            drift = f"broken: {type(exc).__name__}"
        args = stored.args
        rows.append(
            (
                stored.name,
                args.get("objective", "max_mw"),
                args.get("target_item") or "-",
                ",".join(args.get("sources") or [])[:28] or "whole map",
                stored.factory or "-",
                drift,
                stored.notes[:30],
            )
        )
    return render.envelope(
        f"# {st.age_note}\n# {len(rows)} saved plan(s)",
        render.table(
            ("name", "objective", "target", "sources", "factory", "status", "notes"), rows
        ),
        [
            (
                "'world moved' means the plan is unchanged but the solve inputs are not "
                "-- an unlock, a freed node or a new building. Re-run it to see how"
            )
        ],
    )


@mcp.tool(structured_output=False)
def forget_plan(name: str, save: str | None = None, world: str | None = None) -> str:
    """Delete a saved plan. Nothing in the world is touched."""
    try:
        st = _state(save, world)
    except Exception as exc:
        return f"could not read save: {exc}"
    stored = st.plans.find(name)
    if stored is None:
        known = ", ".join(x.name for x in st.plans.plans) or "(none)"
        return f"! no saved plan named {name!r}. Saved: {known}"
    st.plans.remove(stored.name)
    st.plans.save()
    return f"forgot plan {stored.name!r}"


@mcp.tool(structured_output=False)
def plan_factory(
    objective: str = "max_mw",
    target_item: str | None = None,
    sources: list[str] | None = None,
    exports: list[str] | None = None,
    export_minimums: dict[str, float] | None = None,
    only_free_nodes: bool = False,
    allow_sinks: bool = True,
    clocks: list[float] | None = None,
    extractor_clocks: list[float] | None = None,
    machine_cost_mw: float = 5.0,
    exclude_recipes: list[str] | None = None,
    only_recipes: list[str] | None = None,
    save: str | None = None,
    world: str | None = None,
    limit: Limit = 15,
    logistics_items: Annotated[
        list[str] | None,
        Field(description="items whose belt/pipe rows to pin, whatever their volume"),
    ] = None,
    water_extractors: Annotated[
        int | None,
        Field(description="how many Water Extractors your site can actually hold"),
    ] = None,
    sloops: Annotated[
        int,
        Field(description="Somersloops the plan may spend; 0 spends none"),
    ] = 0,
    recycle_once: Annotated[
        list[str] | None,
        Field(description="recipes that may run but must not feed each other, e.g. ['Recycled']"),
    ] = None,
    supplied: Annotated[
        dict[str, float] | None,
        Field(description="items another plan hands this one, {item: per-minute}"),
    ] = None,
    plan: Annotated[str | None, Field(description="recall a saved plan by name")] = None,
    save_as: Annotated[str | None, Field(description="store this request under a name")] = None,
    plan_notes_text: Annotated[str, Field(description="note stored with save_as")] = "",
    for_factory: Annotated[str, Field(description="factory label this plan is for")] = "",
) -> str:
    """Optimise a factory with an LP over this world's unlocked recipes.

    ``sources`` says which resource nodes may feed the plan, as a list of selectors --
    named regions, radii, grid cells, compass directions, or specific node ids::

        ["north"]                        everything in the northern half
        ["region:Northern Forest"]       one named region
        ["near:0,-2000,900"]             within 900 m of (0, -2000) metres
        ["node:BP_ResourceNode30_103"]   one exact node (repeatable)
        ["grid:X3Y4", "grid:X3Y5"]       specific grid cells
        ["north", "resource:Crude Oil"]  narrow a location to one resource

    Omit it and the whole map is in scope. Use search_resource_nodes to discover ids.

    Machine counts are whole buildings at a derived clock: a 52.8 machine-equivalent
    result is reported as 53 machines at 99.6%. That is exact, always a clean ratio,
    and provably the power-optimal way to run that throughput, so ordinary ratio
    underclocking is automatic and needs no parameter.

    ``extractor_clocks`` overclocks the SOURCE NODES only, e.g. [1.0, 1.5, 2.0, 2.5].
    That is the usual play: a node set is fixed, so speed is the only way to get more
    out of it, whereas overclocking production machines mostly burns power. Each
    machine above 100% needs Power Shards, which nothing here counts.

    ``clocks`` is only for asking a different question: passing [0.5, 1.0] lets the
    solver SPREAD throughput over more machines to save power, which is real but not
    free, so each machine is priced at ``machine_cost_mw`` (default 5 MW, just above
    the 2.58 MW/machine that trade was measured to be worth). Overclock modes are not
    offered by default because they consume Power Shards, which nothing here counts.

    objective: max_mw | max_item | min_raw | min_machines | min_power.
    Every item is balanced as an EQUALITY, so a byproduct with no consumer makes the
    plan infeasible rather than silently vanishing.

    ``exports`` is the whitelist of what may leave, and the single most load-bearing
    argument here; default is power only, which is often infeasible for crude oil::

        exports=["MW"]                        power out, plant must be self-powered
        exports=["Plastic", "Rubber"]         items out, NO power export
        exports=["MW", "Plastic", "Rubber"]   both -- MW must be listed explicitly

    Two things worth reading twice. The power token is **MW** (``mw``, ``power`` and
    ``Power`` all work too), not the item name of anything. And ``exports``
    **replaces** the default rather than extending it: naming an item drops MW, which
    is deliberate, because exporting MW also forbids drawing from the existing grid.
    A token matching no item is refused by name rather than solved around.

    ``sloops`` is a BUDGET, not a switch: it is how many Somersloops you will actually
    commit, and the solver spends up to that many wherever they buy the most. Default 0
    spends none, because only a fixed number exist on the whole map and a plan that
    quietly assumed them would be unbuildable. Each one costs 4x power for 2x output on
    its machine, so they are placed one at a time across many machines rather than
    filling one -- output is linear in sloops and power is quadratic, so spreading wins.

    ``logistics_items`` pins named items into the belt/pipe table however small their
    flow, as rows ADDED to the ``limit`` biggest by volume. Without it, a two-item
    question can fall off the bottom of a big plan's flow table.
    """
    g = game()
    try:
        st = _state(save, world)
    except Exception as exc:
        return f"could not read save: {exc}"

    supplied = dict(
        objective=objective,
        target_item=target_item,
        sources=sources,
        exports=exports,
        export_minimums=export_minimums,
        only_free_nodes=only_free_nodes,
        allow_sinks=allow_sinks,
        clocks=clocks,
        extractor_clocks=extractor_clocks,
        machine_cost_mw=machine_cost_mw,
        exclude_recipes=exclude_recipes,
        only_recipes=only_recipes,
        water_extractors=water_extractors,
        sloops=sloops,
        recycle_once=recycle_once,
        supplied=supplied,
    )
    try:
        plan_kwargs, plan_name, plan_notes = _plan_kwargs(st, plan, supplied)
    except KeyError as exc:
        return f"! {exc.args[0]}"

    report = build_plan_report(g, st, plan_kwargs, logistics_items, objective=objective)

    # Persistence is an interface side effect, not part of the answer: the plan is stored
    # here and the resulting sentence handed to the presenter like any other note.
    save_as_note = ""
    if save_as and report.prepared.failure is None:
        plan_id = report.prepared.request.plan_id
        stored = st.plans.put(
            save_as,
            plan_kwargs,
            plan_id,
            notes=plan_notes_text,
            factory=for_factory,
            when=str(st.header.get("save_datetime") or st.header.get("filename") or ""),
        )
        path = st.plans.save()
        save_as_note = (
            f"saved as {stored.name!r} (plan_id {plan_id}) in {path}. "
            f"Recall with plan={stored.name!r} on plan_factory, plan_layout or diff_vs_save"
        )

    return render_plan_factory(
        g,
        st,
        report,
        objective=objective,
        only_free_nodes=only_free_nodes,
        limit=limit,
        plan_name=plan_name,
        plan_notes=plan_notes,
        save_as_note=save_as_note,
    )


@mcp.tool(structured_output=False)
def plan_layout(
    objective: str = "max_mw",
    target_item: str | None = None,
    sources: list[str] | None = None,
    exports: list[str] | None = None,
    export_minimums: dict[str, float] | None = None,
    detail: str = "floors",
    only_free_nodes: bool = False,
    allow_sinks: bool = True,
    exclude_recipes: list[str] | None = None,
    only_recipes: list[str] | None = None,
    # Without these plan_layout re-solves at defaults and schematises a DIFFERENT plan
    # than the one being laid out -- measured at 15,043 MW against the 83,737 MW plan it
    # was asked to draw, because base extraction is a sixth of overclocked.
    clocks: list[float] | None = None,
    extractor_clocks: list[float] | None = None,
    machine_cost_mw: float = 5.0,
    water_extractors: Annotated[
        int | None,
        Field(description="how many Water Extractors your site can actually hold"),
    ] = None,
    sloops: Annotated[
        int,
        Field(description="Somersloops the plan may spend; 0 spends none"),
    ] = 0,
    sites: Annotated[
        dict[str, list[str]] | None,
        Field(description='detail="sites": {"rig": ["Heavy Oil Residue", ...], ...}'),
    ] = None,
    max_floor_foundations: Annotated[
        int,
        Field(description="cap a deck at this many 8m foundations; 0 = one stage per deck"),
    ] = 0,
    order_floors_by: Annotated[
        str, Field(description='"chain" (build order) or "head" (minimise fluid lift)')
    ] = "chain",
    belt_tier: Annotated[
        str, Field(description="belt tier name; blank = the fastest you have unlocked")
    ] = "",
    pipe_tier: Annotated[
        str, Field(description="pipe tier name; blank = the fastest you have unlocked")
    ] = "",
    save: str | None = None,
    world: str | None = None,
    limit: Limit = 20,
    plan: Annotated[str | None, Field(description="recall a saved plan by name")] = None,
    factory: Annotated[
        str | None,
        Field(description="fit the layout against this factory's existing platform"),
    ] = None,
) -> str:
    """Turn a plan into a buildable schematic: blocks, buses and floors.

    Same arguments as plan_factory, plus ``detail``: "floors" (default, the stack),
    "blocks" (every module with its size and rates), "buses" (item flows),
    "trunks" (which resource nodes share each pipe or belt run into the site),
    "materials" (what the whole thing costs to build, machines plus deck), or
    "sites" (cut the plan into named modules and report what crosses between them).

    This is a SCHEMATIC, not a blueprint. It gives modules, connections, floor
    assignment and a space budget. It deliberately does NOT give world coordinates or
    belt routing -- there is no terrain data here, so those would be invented.

    Blocks are split by throughput: 46 Refineries needing 1380 m3/min of crude cannot
    share one manifold when a Mk2 pipe carries 600, so that is 3 blocks. Floors follow
    chain depth, with a logistics deck between each pair of production floors.
    """
    g = game()
    try:
        st = _state(save, world)
    except Exception as exc:
        return f"could not read save: {exc}"

    tiers = resolve_tiers(g, st, belt_tier, pipe_tier)
    if tiers.errors:
        return render_layout(
            g,
            st,
            LayoutReport(prepared=None, tiers=tiers),
            objective=objective,
            detail=detail,
            limit=limit,
        )

    # Same solve-shaping arguments as plan_factory, so a layout can be asked for
    # directly rather than only via a saved plan.
    supplied = dict(
        objective=objective,
        target_item=target_item,
        sources=sources,
        exports=exports,
        export_minimums=export_minimums,
        only_free_nodes=only_free_nodes,
        allow_sinks=allow_sinks,
        exclude_recipes=exclude_recipes,
        only_recipes=only_recipes,
        clocks=clocks,
        extractor_clocks=extractor_clocks,
        machine_cost_mw=machine_cost_mw,
        water_extractors=water_extractors,
        sloops=sloops,
        # Into the SCENARIO, not just into build_layout. Passing a tier to the schematic
        # while the solve kept the default is the same drift 8.5a documents: the trunk
        # view reads sc.pipe_m3min, so pipe_tier="Mk1" changed the block split and left
        # the trunk count untouched, describing two different plants in one response.
        #
        # Only when the caller ASKED for a tier, though. Passing the resolved default
        # through made every recalled plan report "overridden this call: belt_ipm,
        # pipe_m3min" -- an override the user never made, which is exactly the kind of
        # noise that trains a reader to skip the override line that does matter.
        belt_ipm=tiers.belt_ipm if tiers.asked_belt else None,
        pipe_m3min=tiers.pipe_m3min if tiers.asked_pipe else None,
    )
    try:
        plan_kwargs, plan_name, plan_notes = _plan_kwargs(st, plan, supplied)
    except KeyError as exc:
        return f"! {exc.args[0]}"

    try:
        report = build_layout_report(
            g,
            st,
            plan_kwargs,
            tiers,
            objective=objective,
            detail=detail,
            sites=sites,
            max_floor_foundations=max_floor_foundations,
            order_floors_by=order_floors_by,
            factory=factory,
            plan=plan,
        )
    except SelectorError as exc:
        return f"! {exc}"

    return render_layout(
        g,
        st,
        report,
        objective=objective,
        detail=detail,
        limit=limit,
        plan_name=plan_name,
        plan_notes=plan_notes,
    )


#: Said on every stage report, because it is the one thing about this feature that a
#: reader will otherwise get wrong. `built` is exact; `running` is the only positive
#: evidence of power the save carries, and its absence is not evidence of no power.
ENERGISED_CAVEAT = (
    "built and ENERGISED are different states and the save separates them only one way: "
    "a machine that produced inside the last 300s window certainly had power, while a "
    "machine that did not may be unpowered, starved, blocked or simply idle. mHasPower "
    "and the circuit id are not SaveGame properties and the circuit subsystem stores "
    "nothing, so grid membership is rebuilt at load and is NOT in the file. A fully "
    "built, wholly dark block is a valid state here, not an anomaly"
)

#: Emitted only when some row's built count is an interval. Without it "built 1..11,
#: running 11" reads as a contradiction; it is not, because the two columns have
#: different denominators.
RANGE_CAVEAT = (
    "a built count is a RANGE wherever a machine cannot be attributed to this plan "
    "(Water Extractors, OQ5): the low bound counts only the ones standing among the "
    "plan's own. 'running' is measured over every MATCHED machine, so it can sit above "
    "the low bound without contradicting it"
)


def _stage_state(stage) -> str:
    """One phrase per stage, saying only what the save supports."""
    if stage.built_max <= 0:
        return "not built"
    if not stage.complete:
        span = f"{stage.fraction_built:.0%}"
        if stage.built_max != stage.built and stage.machines:
            span = f"{span}-{stage.built_max / stage.machines:.0%}"
        return f"{span} built"
    if stage.running >= stage.machines:
        return "built, all running"
    if stage.running:
        return f"built, {stage.running} running"
    return "built, none running"


def _stage_overview(tracking: Tracking) -> tuple[str, list[str]]:
    """The whole partition against the save: which stage the player is in."""
    if not tracking.ok:
        return "", tracking.warnings
    rows = [
        (
            f"S{s.index}",
            s.machines,
            f"{s.built}..{s.built_max}" if s.built_max != s.built else s.built,
            s.running,
            f"{render.num(-s.draw_mw)}/+{render.num(s.generation_mw)}",
            f"{s.available_after:,.0f}",
            _stage_state(s),
        )
        for s in tracking.stages
    ]
    if tracking.current:
        done = tracking.current - 1
        here = next(s for s in tracking.stages if s.index == tracking.current)
        headline = (
            f"# you are in STAGE {tracking.current} of {len(tracking.stages)}: "
            + (f"stages 1-{done} complete, " if done > 1 else "stage 1 complete, " if done else "")
            + f"stage {tracking.current} is {here.fraction_built:.0%} built "
            f"({here.built}/{here.machines}) and {here.running} machine(s) in it are "
            "proven running"
        )
    else:
        headline = (
            f"# every stage is built ({tracking.built}/{tracking.machines} machines). "
            f"{tracking.running} are proven running; the rest may be built-and-unpowered, "
            "which is what this plan expects until you energise them"
        )
    body = (
        "# STAGES: the commission_plan startup order, matched against the save\n"
        + render.table(("stage", "on", "built", "running", "MW", "free after", "state"), rows)
        + "\n"
        + headline
    )
    notes = [*tracking.warnings, ENERGISED_CAVEAT]
    if any(s.built_max != s.built for s in tracking.stages):
        notes.append(RANGE_CAVEAT)
    if tracking.monitored:
        notes.append(
            f"{tracking.monitored} built machine(s) carry a productivity monitor, so "
            "'running' is measured for those and unknown for the rest. Pass stage=<n> "
            "for one stage's rows, or factory_health for why a machine is stopped"
        )
    if not tracking.plan_name:
        notes.append(
            "these stage numbers came from THIS CALL's arguments, not a stored plan, so "
            "they renumber whenever the arguments or the world move. Save the plan "
            "(plan_factory save_as=...) before treating a stage number as a milestone"
        )
    return body, notes


def _stage_detail(tracking: Tracking, index: int, limit: int) -> tuple[str, list[str]]:
    """One stage's own rows: what it energises, what stands, what is proven running."""
    stage = next((s for s in tracking.stages if s.index == index), None)
    if stage is None:
        available = ", ".join(f"{s.index}" for s in tracking.stages) or "(none)"
        return "", [f"no stage {index} in this plan; it has stages {available}"]
    rows = []
    for r in stage.rows:
        # The free action belongs to the whole build job, not to this slice of it: three
        # paused pumps are three dropdowns however the waves cut them. Rendering it as
        # this stage's verb would tell the player to unpause them twice.
        note = f"{r.verb} {r.free} first, plan-wide" if r.free else ""
        note = f"{note}; {r.note}" if note and r.note else note or r.note
        rows.append(
            (
                "BUILD" if r.to_build else "OK",
                r.machines,
                f"{r.built}..{r.built_max}" if r.built_max != r.built else r.built,
                r.running,
                r.label[:34],
                r.building[:18],
                render.num(-r.draw_mw) if r.draw_mw else f"+{render.num(r.generation_mw)}",
                note[:44],
            )
        )
    body = (
        f"# STAGE {index} of {len(tracking.stages)}: {stage.machines} machine(s), "
        f"{_stage_state(stage)}\n"
        + render.kv(
            [
                ("draw_MW", render.num(stage.draw_mw)),
                ("generation_MW", render.num(stage.generation_mw)),
                ("free_before_MW", render.num(stage.available_before)),
                ("free_after_MW", render.num(stage.available_after)),
            ]
        )
        + "\n"
        + render.table(
            ("act", "on", "built", "running", "process", "building", "MW", "note"),
            rows[: render.clamp(limit, default=20)],
            total=len(rows),
            limit=limit,
        )
    )
    notes = [
        *tracking.warnings,
        ENERGISED_CAVEAT,
        (
            "materials are NOT split by stage, and the cost table is left out here for "
            "that reason: a stage is a switch-on, not a build step, so the whole plant "
            "is built first and the bill belongs to the plan as a whole"
        ),
    ]
    if any(r.built_max != r.built for r in stage.rows):
        notes.append(RANGE_CAVEAT)
    if stage.dark:
        notes.append(
            f"{stage.dark} machine(s) in this stage are dark with no supply cause the "
            "save can name -- consistent with not being energised yet, but the file "
            "cannot confirm it"
        )
    return body, notes


@mcp.tool(structured_output=False)
def diff_vs_save(
    objective: str = "max_mw",
    target_item: str | None = None,
    sources: list[str] | None = None,
    exports: list[str] | None = None,
    export_minimums: dict[str, float] | None = None,
    only_free_nodes: bool = False,
    allow_sinks: bool = True,
    clocks: list[float] | None = None,
    extractor_clocks: list[float] | None = None,
    machine_cost_mw: float = 5.0,
    exclude_recipes: list[str] | None = None,
    only_recipes: list[str] | None = None,
    save: str | None = None,
    world: str | None = None,
    limit: Limit = 20,
    show_cost: bool = True,
    plan: Annotated[str | None, Field(description="recall a saved plan by name")] = None,
    stage: Annotated[
        int | None,
        Field(description="one startup stage's delta; 0 for the stage overview"),
    ] = None,
    factory: Annotated[
        str | None,
        Field(description="only count this factory's machines as already built"),
    ] = None,
) -> str:
    """What to change to get from the factory you have to the one plan_factory plans.

    Takes exactly plan_factory's arguments and re-solves, because the server keeps no
    state. Both tools print a plan id hashed over the arguments AND the save-derived
    solve inputs, so two responses carrying the same id are provably the same plan.

    Machines are matched by IDENTITY, never by position: a manufacturer on (building,
    recipe), a generator on its building alone since its fuel is piped in rather than
    set on the machine, an extractor on the node it occupies. A Refinery running some
    other recipe is busy, not spare, so it never counts toward the plan.

    Actions are ordered free-first -- UNPAUSE, then SETRECIPE on machines that produce
    nothing today, then BUILD. Stages follow the plan's own chain depth and the power
    arithmetic is INCREMENTAL, charging only the machines you have yet to place. Where
    a machine cannot be identified at all (Water Extractors have no recipe and no
    resolvable node) the answer is a RANGE, never a number.

    Recall a stored plan with ``plan=`` and the diff is also grouped by STARTUP STAGE --
    the same partition commission_plan emits -- so it answers "which stage am I in".
    ``stage=<n>`` narrows to one stage's delta; ``stage=0`` asks for the overview
    without a stored plan, at the cost that the numbering moves when the arguments do.

    Built and energised are DIFFERENT states and the save separates them in one
    direction only: a machine that produced in the last 300s window certainly had
    power, while one that did not may be unpowered, starved, blocked or idle. Grid
    membership is not persisted at all, so a stage is never reported as "unpowered" --
    only as built with nothing proven running, which is exactly what a finished but
    not-yet-energised block looks like.

    Saves are read-only: this never proposes writing one, and there is no dismantle
    action. Machines standing among the plan but not in it are listed for you to judge.
    """
    g = game()
    try:
        st = _state(save, world)
    except Exception as exc:
        return f"could not read save: {exc}"

    supplied = dict(
        objective=objective,
        target_item=target_item,
        sources=sources,
        exports=exports,
        export_minimums=export_minimums,
        only_free_nodes=only_free_nodes,
        allow_sinks=allow_sinks,
        clocks=clocks,
        extractor_clocks=extractor_clocks,
        machine_cost_mw=machine_cost_mw,
        exclude_recipes=exclude_recipes,
        only_recipes=only_recipes,
    )
    try:
        plan_kwargs, plan_name, plan_notes = _plan_kwargs(st, plan, supplied)
    except KeyError as exc:
        return f"! {exc.args[0]}"

    prepared = prepare(g, st, plan_kwargs, objective_label=objective, diagnose=False)
    if prepared.failure:
        # Hand back the plan's own reason. An empty diff table would read as "you
        # already have it", which is the opposite of what infeasible means.
        suffix = " -- no plan to diff against" if "INFEASIBLE" in prepared.failure.headline else ""
        return render.envelope(
            f"# {prepared.failure.headline}{suffix}",
            "",
            [*prepared.failure.notes, "see plan_factory for why; there is nothing to change yet"],
        )
    req, sol = prepared.request, prepared.solution
    sel = req.selection

    if not sol.processes:
        # Feasible but empty. Rendering an empty table would read as "nothing to do",
        # when what happened is that the objective walked away from the resource --
        # every crude route here emits Polymer Resin, and with MW as the only export
        # the LP abandons oil entirely.
        return render.envelope(
            f"# EMPTY PLAN ({objective} over {sel.description}) -- nothing to change",
            "",
            [
                *sol.warnings,
                (
                    "the solve chose to build nothing, which usually means a byproduct "
                    "has no outlet -- widen exports and re-run plan_factory first"
                ),
            ],
        )

    # A plan saved with for_factory carries its own scope, so `diff_vs_save(plan=...)`
    # already answers "how far along is THAT factory" without naming it again.
    scope_name = factory
    if scope_name is None and plan:
        stored = st.plans.find(plan)
        scope_name = (stored.factory or None) if stored else None

    scope = None
    if scope_name:
        try:
            resolved_name, machines = resolve_factory(st, scope_name)
        except SelectorError as exc:
            return f"! {exc}"
        if not machines:
            return f"! {scope_name!r} resolved to no machines that still exist in this save"
        scope = set(machines)
        plan_notes.append(
            f"scoped to {resolved_name!r} ({len(scope)} machines): everything outside it "
            "counts as not built, and nodes tapped by other factories are unavailable"
        )

    rep = build_diff(g, st, sol, req, scope=scope)
    pw = st.power_report()

    # Stage detection is the same partition commission_plan emits, matched against the
    # save -- nothing is stored and nothing is re-solved. It is off unless asked for,
    # because the numbering is only stable for a STORED plan and because a diff that
    # nobody asked a stage question of should not pay the context for one.
    tracking: Tracking | None = None
    if plan or stage is not None:
        tracking = track(
            prepared,
            commission(prepared, g, pw["headroom_mw"], "power_report, nameplate"),
            rep,
            g,
            st,
            plan_name=plan_name,
        )
        if plan_name and (stored := st.plans.find(plan_name)) and stored.plan_id != req.plan_id:
            # The same drift list_plans reports, said where it bites hardest: a stage
            # number is a milestone the player remembers, and a re-solve against a moved
            # world can renumber the whole partition under them.
            plan_notes.append(
                f"plan {plan_name!r} was saved against plan_id {stored.plan_id} and "
                f"re-solves to {req.plan_id} -- the WORLD moved, so these stage numbers "
                "may not be the ones you were given before"
            )

    if stage:
        body, stage_notes = _stage_detail(tracking, stage, limit)
        if not body:
            return render.envelope(
                f"# no stage {stage} [plan {req.plan_id}/save {rep.save_id}]",
                "",
                [*plan_notes, *stage_notes],
            )
        return render.envelope(
            "\n".join(
                [
                    (
                        f"# stage {stage} of plan {objective}|{sel.description} "
                        f"[plan {req.plan_id}/save {rep.save_id}]"
                    ),
                    f"# {st.age_note}",
                ]
            ),
            body,
            [*plan_notes, *stage_notes],
        )

    rows = []
    targets: list[str] = []
    for r in rep.rows[: render.clamp(limit, default=20)]:
        count = "" if r.verb == "OK" else render.num(r.count)
        if r.verb == "BUILD" and r.build_max is not None and r.build_max != r.build:
            count = f"{r.build}..{r.build_max}"
        note = r.note
        if r.targets:
            # Ids go in one footer, per the house rule -- a node instance name runs to
            # 51 characters and would crowd every other column off the row.
            spans = [t[1] / 1000 for t in r.targets]
            reach = (
                f"{min(spans):.2g}km"
                if max(spans) - min(spans) < 0.1
                else f"{min(spans):.2g}-{max(spans):.2g}km"
            )
            head = f"on {len(r.targets)} free node(s) @{reach}"
            note = f"{head}; {note}" if note else head
            targets += [t[0] for t in r.targets]
        rows.append(
            (
                r.stage,
                r.verb,
                count,
                r.process[:30],
                r.building[:20],
                r.have,
                render.where_bands(r.have_distances),
                note[:56],
            )
        )

    to_place = (
        render.num(rep.to_build)
        if rep.to_build_max == rep.to_build
        else f"{rep.to_build}..{rep.to_build_max}"
    )
    summary = "\n".join(
        [
            f"# diff vs plan {objective}|{sel.description} [plan {req.plan_id}/save {rep.save_id}]",
            f"# {st.age_note}",
            render.kv(
                [
                    ("target_MW", render.num(sol.net_mw)),
                    ("plan_buildings", render.num(sol.machines_total)),
                    ("to_place", to_place),
                    ("actionable", sum(1 for r in rep.rows if r.actionable)),
                ]
            ),
            render.kv(
                [
                    ("now_gen_MW", render.num(pw["generation_mw"])),
                    ("draw_MW", render.num(pw["draw_mw"])),
                    ("headroom_MW", render.num(pw["headroom_mw"])),
                ]
            ),
        ]
    )

    notes = [*rep.notes]
    for r in [r for r in rep.rows if r.build_max is not None and r.build_max != r.build][:2]:
        notes.append(
            f"{r.building}s cannot be matched to a job, so {r.need} needed vs {r.have} "
            f"built is a RANGE: build {r.build}..{r.build_max}"
        )
    spread = [t[1] for r in rep.rows for t in r.targets]
    if spread and max(spread) - min(spread) > 1000:
        notes.append(
            f"the plan's build targets span {min(spread) / 1000:.2g}-"
            f"{max(spread) / 1000:.2g}km from your plant -- this is one plan, not one site"
        )
    if any("plan budgets 100%" in r.note for r in rep.rows):
        notes.append(
            "matched machines running off 100% are noted, not actioned: the plan "
            "budgets 100%, so it understates what you already produce"
        )

    parts = [
        render.table(
            ("st", "act", "n", "process", "building", "have", "where(km)", "note"),
            rows,
            total=len(rep.rows),
            limit=limit,
        )
    ]
    if targets:
        parts.append(
            "# build targets, reusable as node: selectors -- "
            + " ".join(targets[:4])
            + (f" (+{len(targets) - 4} more)" if len(targets) > 4 else "")
        )
    if rep.neighbours:
        near = ", ".join(f"{n}x {label}" for label, n in rep.neighbours[:3])
        parts.append(
            f"# within {int(DIFF_NEIGHBOUR_M)}m and competing for the plan's own "
            f"materials, but NOT in it: {near}"
            "\n#   yours to keep or reclaim; no action proposed"
        )
    if show_cost and rep.cost:
        parts.append(
            "# cost of the build counts. stock is spendable only, never machine buffers."
            "\n"
            + render.table(
                ("item", "need", "stock", "your_lines"),
                [
                    (c.name[:24], render.num(c.need), render.num(c.stock), c.lines)
                    for c in rep.cost[:5]
                ],
            )
        )
    # Suppressed when the stage table is present: "place it in >=18 proportional slices"
    # is the answer from BEFORE the startup-order re-frame, and printing it beside the
    # startup order would tell the player to partition a build that is not partitioned.
    if rep.deficit_mw > 0 and rep.slices > 1 and tracking is None:
        parts.append(
            "# ORDER: an LP solution is a ray, so any fraction of the plan is itself "
            f"feasible and self-powered.\n# The build dips {render.num(rep.deficit_mw)} MW "
            f"against {render.num(rep.headroom_mw)} MW of headroom, so place it in "
            f">={rep.slices} proportional slices."
        )
    if tracking is not None:
        block, stage_notes = _stage_overview(tracking)
        if block:
            parts.append(block)
        notes += stage_notes

    if plan_name:
        plan_notes = [f"recalled saved plan {plan_name!r}", *plan_notes]

    return render.envelope(summary, "\n".join(parts), [*plan_notes, *notes])


@mcp.tool(structured_output=False)
def explain_byproducts(
    objective: str = "max_mw",
    target_item: str | None = None,
    item: str | None = None,
    sources: list[str] | None = None,
    exports: list[str] | None = None,
    export_minimums: dict[str, float] | None = None,
    allow_sinks: bool = True,
    exclude_recipes: list[str] | None = None,
    save: str | None = None,
    world: str | None = None,
    limit: Limit = 12,
) -> str:
    """Explain which byproducts stall a plan, and what can legally consume them.

    Every item balance is an equality, so a byproduct with no consumer makes a plan
    INFEASIBLE rather than silently vanishing. This says WHICH item is stuck, whether
    it can be sunk (solids only -- a fluid must be consumed exactly or packaged
    first), and which recipes would absorb it, split into ones this world has
    unlocked and ones it does not.

    Pass ``item`` to focus on one byproduct instead of the whole plan.
    """
    g = game()
    try:
        st = _state(save, world)
    except Exception as exc:
        return f"could not read save: {exc}"
    return byproducts_text.explain(
        g,
        st,
        objective=objective,
        target_item=target_item,
        item=_item_id(item) if item else None,
        sources=sources,
        exports=exports,
        export_minimums=export_minimums,
        allow_sinks=allow_sinks,
        exclude_recipes=exclude_recipes,
        limit=render.clamp(limit, default=12),
    )


@mcp.tool(structured_output=False)
def compare_recipe_options(
    item: str,
    rate: float = 100.0,
    per_resource: str | None = None,
    outlets: list[str] | None = None,
    allow_sinks: bool = True,
    save: str | None = None,
    world: str | None = None,
    limit: Limit = 10,
) -> str:
    """Rank whole ROUTES to make an item by what each actually costs.

    Not a recipe list -- alternates_for_item already does that. Each route is solved
    end to end with the LP, so the comparison is Crude -> Alt HOR -> Diluted Fuel
    against Crude -> Fuel, priced in raw resource per unit, whole buildings, net
    power, and byproducts needing an outlet.
    """
    g = game()
    try:
        st = _state(save, world)
    except Exception as exc:
        return f"could not read save: {exc}"
    iid = _item_id(item)
    if iid is None:
        return f"no item matching {item!r}"
    result = compare.compare_routes(
        g,
        st,
        iid,
        rate=rate,
        allow_sinks=allow_sinks,
        outlets=outlets,
        per_resource=_item_id(per_resource) if per_resource else None,
    )
    return render_comparison(result, limit=render.clamp(limit, default=10))


@mcp.tool(structured_output=False)
def bom(
    item: str,
    qty: float = 60.0,
    allow_sinks: bool = True,
    outlets: list[str] | None = None,
    exclude_recipes: list[str] | None = None,
    only_recipes: list[str] | None = None,
    save: str | None = None,
    world: str | None = None,
    limit: Limit = 20,
) -> str:
    """Flattened bill of materials: total raw and intermediate rates for qty/min of an item.

    ``qty`` is a RATE, per minute. Solved by the LP, never by expanding the recipe
    tree: Recycled Plastic and Recycled Rubber form a real 2-cycle, so an expansion
    has no correct depth limit. Every row names the recipe chosen for that item,
    because alternates change the totals materially.
    """
    g = game()
    try:
        st = _state(save, world)
    except Exception as exc:
        return f"could not read save: {exc}"
    try:
        result = bom_mod.build_bom(
            g,
            st,
            item,
            qty=qty,
            allow_sinks=allow_sinks,
            outlets=outlets,
            exclude_recipes=exclude_recipes,
            only_recipes=only_recipes,
        )
    except ValueError as exc:
        return str(exc)
    return render_bom(result, limit=render.clamp(limit, default=20))


def _live_feeders(g, st, floor_mw: float = 1.0) -> list[tuple[str, float]]:
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


@mcp.tool(structured_output=False)
def commission_plan(
    objective: str = "max_mw",
    target_item: str | None = None,
    sources: list[str] | None = None,
    exports: list[str] | None = None,
    export_minimums: dict[str, float] | None = None,
    only_free_nodes: bool = False,
    allow_sinks: bool = True,
    clocks: list[float] | None = None,
    extractor_clocks: list[float] | None = None,
    machine_cost_mw: float = 5.0,
    exclude_recipes: list[str] | None = None,
    only_recipes: list[str] | None = None,
    water_extractors: int | None = None,
    sloops: int = 0,
    headroom_mw: Annotated[
        float | None,
        Field(description="grid power free for startup; default reads it from the save"),
    ] = None,
    save: str | None = None,
    world: str | None = None,
    limit: Limit = 60,
    plan: Annotated[str | None, Field(description="recall a saved plan by name")] = None,
) -> str:
    """In what order to switch a built plant on, without blowing the fuse.

    This is a STARTUP order, not a build order, and the difference removes most of the
    problem. Building costs materials, not power -- a machine draws only when it runs --
    so the whole plant can be constructed at leisure, drawing nothing, and then energised
    block by block. Nothing here tells you what to build first.

    The constraint is one line, and it is hard: at every step, energised consumer draw
    must stay under the headroom plus generation from generators already burning fuel.
    Exceeding it in Satisfactory does not degrade gracefully -- the fuse blows and the
    whole grid stops until it is reset by hand, including the plant that was feeding it.

    Generators are free to energise (0 MW draw, read from the dump), so a wave costs its
    consumers and refunds its generators, and that refund pays for the next wave.

    Takes plan_factory's arguments, or recall a saved plan with ``plan=``.
    """
    g = game()
    try:
        st = _state(save, world)
    except Exception as exc:
        return f"could not read save: {exc}"

    supplied = dict(
        objective=objective,
        target_item=target_item,
        sources=sources,
        exports=exports,
        export_minimums=export_minimums,
        only_free_nodes=only_free_nodes,
        allow_sinks=allow_sinks,
        clocks=clocks,
        extractor_clocks=extractor_clocks,
        machine_cost_mw=machine_cost_mw,
        exclude_recipes=exclude_recipes,
        only_recipes=only_recipes,
        water_extractors=water_extractors,
        sloops=sloops,
    )
    try:
        plan_kwargs, plan_name, plan_notes = _plan_kwargs(st, plan, supplied)
    except KeyError as exc:
        return f"! {exc.args[0]}"

    prepared = prepare(g, st, plan_kwargs, objective_label=objective, diagnose=False)
    if prepared.failure:
        return render.envelope(
            f"# {prepared.failure.headline} -- nothing to commission",
            "",
            [*prepared.failure.notes, "see plan_factory for why"],
        )

    # Headroom is an INPUT and is printed as one. A sequence computed against a save
    # that has since moved is then visibly stale rather than quietly wrong -- the same
    # reason phase_requirements labels its rows instead of filtering them.
    power = st.power_report()
    if headroom_mw is None:
        # Nameplate on purpose. Measured headroom is usually much larger -- 6,034 MW
        # against 711 on the reference save, because most of that factory is idle -- but
        # energising a block can un-starve the very machines that are idle, and the fuse
        # blows on demand, not on averages. The safe bound is the default; the measured
        # one is reported so a player who knows their base is quiet can pass it in.
        head, source = power["headroom_mw"], "power_report, nameplate"
    else:
        head, source = float(headroom_mw), "given by caller"

    plan_run = commission(prepared, g, head, source)
    rows = []
    for w in plan_run.waves:
        # A summary line per wave, because the numbers that decide whether the sequence
        # is safe -- what it costs and what it hands back -- belong to the wave and not
        # to any row in it.
        rows.append(
            (
                f"W{w.index}",
                "",
                w.machines,
                "",
                f"-- switch on {w.machines}, wait >={w.fill_s():.0f}s, then next wave --",
                f"{render.num(-w.draw_mw)} then +{render.num(w.generation_mw)}",
                f"{w.available_after:,.0f}",
            )
        )
        for r in w.rows:
            rows.append(
                (
                    "",
                    f"d{r.depth}",
                    r.machines,
                    f"{r.cumulative}/{r.total}",
                    r.label[:34],
                    render.num(-r.draw_mw) if r.draw_mw else f"+{render.num(r.generation_mw)}",
                    "",
                )
            )
    # Truncation is applied to the WHOLE sequence, never per wave. Chopping each wave at
    # `limit` silently dropped its generator rows -- they sort last by chain depth -- and
    # those are the only rows that pay for the next wave.
    body = render.table(
        ("wave", "chain", "on", "cum", "process", "MW", "free after"),
        rows[: render.clamp(limit, default=40)],
        total=len(rows),
        limit=limit,
    )

    summary = "\n".join(
        [
            f"# startup order for {objective}"
            + (f" ({plan_name})" if plan_name else "")
            + f", {len(plan_run.waves)} wave(s)",
            f"# {st.age_note}",
            (
                f"headroom_MW={head:,.0f} (source: {source})  "
                f"plant_draw_MW={plan_run.plant_draw_mw:,.0f}  "
                f"plant_generation_MW={plan_run.plant_generation_mw:,.0f}"
            ),
            (
                f"minimum_slice_MW={plan_run.minimum_slice_mw:,.0f} "
                "(one machine of every process -- the floor no order can go under)"
            ),
        ]
    )

    notes = [*plan_notes, *plan_run.warnings]
    if headroom_mw is None and power["measured_headroom_mw"] > head * 1.2:
        notes.append(
            f"your grid is only {power['utilisation']:.0%} utilised, so measured headroom "
            f"is {power['measured_headroom_mw']:,.0f} MW against the {head:,.0f} MW "
            "nameplate used here. Nameplate is the safe bound -- energising a block can "
            "un-starve idle machines and the fuse blows on demand, not on averages -- but "
            "if you know your base is quiet, pass headroom_mw= to plan against the real "
            "figure and get far fewer waves"
        )
    if plan_run.ok:
        notes.append(
            "build EVERYTHING first, unpowered: a machine draws only when it runs, so "
            "construction is never the constraint. These waves are switch-ons"
        )
        # What the sequence is standing on. A wave that repipes an extractor already
        # feeding live generators takes that power down mid-startup, which is exactly the
        # moment the plan has least headroom to spare. Read from the save's own
        # connections rather than assumed, and only PROVEN-running generators are charged.
        live = _live_feeders(g, st)
        if live:
            notes.append(
                "CUTOVER RISK -- these are already feeding running generators, so "
                "repiping one mid-startup takes that power out at the worst moment: "
                + "; ".join(f"{name} ({mw:,.0f} MW)" for name, mw in live[:4])
                + ". trace_upstream on any of them shows what hangs off it"
            )
        notes.append(
            "wire one Power Switch per block before starting. Energising is then a "
            "switch flip, and a block that misbehaves can be isolated -- without one, "
            "an overload blows the fuse on the WHOLE grid and stops the plant feeding it"
        )
        waits = [w.index for w in plan_run.waves if w.waits_for_fill]
        if waits:
            notes.append(
                "wave(s) "
                + ", ".join(f"W{i}" for i in waits[:6])
                + " energise consumers and the generators they feed: let the pipes fill "
                "and the generators come up to speed BEFORE starting the next wave. A "
                "wave's own generation is not counted until it completes, so the free-MW "
                "column is what you have during the wait, not after it"
            )
        slowest = max((w.fill_s() for w in plan_run.waves), default=0.0)
        notes.append(
            f"the wait is a LOWER bound (>={slowest:.0f}s on the longest wave): it sums "
            "one full cycle at each chain depth, which every stage must finish before the "
            "next sees anything. It does NOT include pipe transit -- a pipe's fluid volume "
            "is not in the dump (only mRadius, which is collision geometry) and route "
            "lengths are unknown -- so on a long run the real wait is longer, and the "
            "deficit is carried for all of it"
        )
        notes.append(
            "waves are power-ordered, not ratio-balanced -- whole machines cannot hit "
            "the plan's ratios at the bottom of the ramp, so early waves run starved. "
            "That is safe: a starved machine idles and draws less than modelled"
        )
    return render.envelope(summary, body, notes)


@mcp.tool(structured_output=False)
def rank_unlocks(
    objective: str = "max_mw",
    target_item: str | None = None,
    sources: list[str] | None = None,
    exports: list[str] | None = None,
    export_minimums: dict[str, float] | None = None,
    only_free_nodes: bool = False,
    allow_sinks: bool = True,
    clocks: list[float] | None = None,
    extractor_clocks: list[float] | None = None,
    machine_cost_mw: float = 5.0,
    exclude_recipes: list[str] | None = None,
    only_recipes: list[str] | None = None,
    water_extractors: int | None = None,
    sloops: int = 0,
    search: Annotated[
        str | None, Field(description="only test alternates whose name matches")
    ] = None,
    save: str | None = None,
    world: str | None = None,
    limit: Limit = 15,
    plan: Annotated[str | None, Field(description="recall a saved plan by name")] = None,
) -> str:
    """What every locked alternate recipe would be worth to THIS plan.

    One counterfactual per candidate: solve the plan, solve it again with the recipe
    added, report the difference. It answers "which unlock should I chase" with a number
    in the plan's own units instead of a tier list, because a recipe's worth depends
    entirely on what you already have.

    A zero is an answer. Most candidates change nothing, and "you are not missing anything
    here" is a decision -- it is otherwise reached by walking the recipe tree by hand.

    Deltas are an UPPER bound: a candidate needing a machine you have not built is judged
    as if you had it, and the machine is named. Alternates currently offered by a pending
    hard drive are flagged, which is the difference between "worth having" and "claimable
    now".
    """
    g = game()
    try:
        st = _state(save, world)
    except Exception as exc:
        return f"could not read save: {exc}"

    supplied = dict(
        objective=objective,
        target_item=target_item,
        sources=sources,
        exports=exports,
        export_minimums=export_minimums,
        only_free_nodes=only_free_nodes,
        allow_sinks=allow_sinks,
        clocks=clocks,
        extractor_clocks=extractor_clocks,
        machine_cost_mw=machine_cost_mw,
        exclude_recipes=exclude_recipes,
        only_recipes=only_recipes,
        water_extractors=water_extractors,
        sloops=sloops,
    )
    try:
        plan_kwargs, plan_name, plan_notes = _plan_kwargs(st, plan, supplied)
    except KeyError as exc:
        return f"! {exc.args[0]}"

    prepared = prepare(g, st, plan_kwargs, objective_label=objective, diagnose=False)
    if prepared.failure:
        return render.envelope(
            f"# {prepared.failure.headline} -- nothing to rank against",
            "",
            [*prepared.failure.notes, "see plan_factory for why"],
        )

    pool = st.locked_alternates
    if search:
        needle = search.strip().casefold()
        pool = [r for r in pool if needle in r.name.casefold()]
        if not pool:
            return f"! no LOCKED alternate matches {search!r}"

    sweep = sweep_unlocks(prepared.request, st, pool)
    # Which of these you could claim today. A recipe worth 14,540 MW that is sitting in a
    # pending drive is a different instruction from one that needs a drive you have not
    # found yet.
    on_offer: dict[str, int] = {}
    for offer in st.hard_drive_offers:
        for option in offer.options:
            for recipe in option["recipes"]:
                on_offer[recipe.cls] = offer.hard_drive_id

    movers = sweep.movers
    rows = [
        (
            render.num(r.gain),
            f"{r.gain / sweep.baseline:+.1%}" if sweep.baseline else "",
            r.name[:34],
            render.num(r.machines),
            f"drive {on_offer[r.recipe]}" if r.recipe in on_offer else "",
            ", ".join(r.needs)[:18],
            ", ".join(r.activates)[:40],
        )
        for r in movers[: render.clamp(limit, default=15)]
    ]
    notes = [*plan_notes]
    if plan_name:
        notes.insert(0, f"recalled saved plan {plan_name!r}")
    notes.append(
        f"{sweep.tried} locked alternate(s) tested, {len(movers)} changed this plan. "
        "The rest are worth nothing HERE -- which is a result, not a gap: it is the "
        "answer you would otherwise get by walking the tree by hand"
    )
    notes.append(
        "deltas are an UPPER bound: a candidate is solved as if any machine it needs "
        "already existed, and that machine is named in 'needs'"
    )
    notes.append(
        "'activates' is what the gain DEPENDS on -- processes the counterfactual switches "
        "on that this plan does not currently use. A headline number that turns on "
        "reintroducing a chain you deleted is a decision, not a free win"
    )
    # The trap this tool set for its own author. Run with ad-hoc arguments it measures a
    # DIFFERENT plant from the one saved, and the answers genuinely differ: Turbo Blend
    # Fuel is worth +13.6% against unconstrained Spire Coast and exactly nothing against
    # the saved plan, which bans Turbofuel and coal generators.
    if not plan and st.plans.plans:
        notes.append(
            "measured against the ARGUMENTS GIVEN, not against a saved plan. This world "
            f"has {len(st.plans.plans)} saved plan(s) ("
            + ", ".join(x.name for x in st.plans.plans[:3])
            + ") whose exclusions may forbid these gains -- pass plan=<name> to rank "
            "against the architecture you actually chose"
        )
    claimable = [r for r in movers if r.recipe in on_offer]
    if claimable:
        notes.append(
            "claimable NOW from a pending hard drive: "
            + ", ".join(f"{r.name} (drive {on_offer[r.recipe]})" for r in claimable[:4])
            + " -- use advise_hard_drive_pick for that drive's full comparison"
        )
    return render.envelope(
        "\n".join(
            [
                f"# unlock value for {objective}" + (f" ({plan_name})" if plan_name else ""),
                f"# {st.age_note}",
                (
                    f"baseline={render.num(sweep.baseline)}  candidates={sweep.tried}  "
                    f"movers={len(movers)}"
                ),
            ]
        ),
        render.table(
            ("gain", "vs base", "alternate", "machines", "on offer", "needs", "activates"),
            rows,
            total=len(movers),
            limit=limit,
        ),
        notes,
    )
