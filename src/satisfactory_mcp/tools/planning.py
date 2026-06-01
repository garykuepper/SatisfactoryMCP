"""The optimiser surface: plans, layouts, diffs, bills of materials.

Also plan persistence, since a stored plan is a stored planning REQUEST."""

from __future__ import annotations

from typing import Annotated

from pydantic import Field

from .. import render
from ..app import Limit, _item_id, _resolve_factory, _state, game, mcp
from ..docs.constants import WATER_EXTRACTOR_WARN_AT
from ..graph.select import SelectorError
from ..planning import bom as bom_mod
from ..planning import compare
from ..planning.commission import Tracking, commission, track
from ..planning.diff import NEIGHBOUR_RADIUS_M as DIFF_NEIGHBOUR_M
from ..planning.diff import build_diff
from ..planning.layout import build_layout, fluid_head
from ..planning.materials import build_materials
from ..planning.optimize import MW
from ..planning.prepare import prepare
from ..planning.scenario import build_scenario, resolve_item
from ..planning.sensitivity import sweep_unlocks
from ..planning.sites import partition
from ..planning.slice import slice_of
from ..planning.trunks import plan_trunks
from ..presenters.text import byproducts as byproducts_text
from ..presenters.text.bom import render_bom
from ..presenters.text.compare import render_comparison

#: The declared default of every stored planning argument. Needed because MCP fills
#: defaults in before the tool sees them, so "objective" always arrives as "max_mw" and
#: a naive merge would clobber every recalled plan with it. A supplied value counts as an
#: override only when it DIFFERS from the default here.
#:
#: The cost is one honest limitation: recalling a plan cannot explicitly reset a
#: parameter back to its default. Edit the plan (save_as over the same name) for that.
PLAN_DEFAULTS: dict = {
    "objective": "max_mw",
    "target_item": None,
    "sources": None,
    "exports": None,
    "export_minimums": None,
    "only_free_nodes": False,
    "allow_sinks": True,
    "clocks": None,
    "extractor_clocks": None,
    "machine_cost_mw": 5.0,
    "exclude_recipes": None,
    "only_recipes": None,
    "water_extractors": None,
    "sloops": 0,
    "recycle_once": None,
    "supplied": None,
    # Carrier throughput SHAPES THE SOLVE -- belt_ipm prices sinks and both split blocks
    # and trunks -- so it belongs with the stored arguments, not with presentation.
    "belt_ipm": None,
    "pipe_m3min": None,
}


def _plan_kwargs(st, plan: str | None, supplied: dict) -> tuple[dict, str, list[str]]:
    """Merge a stored plan's arguments with anything explicitly overridden this call.

    Returns (kwargs, resolved plan name, notes).
    """
    clean = {k: v for k, v in supplied.items() if k in PLAN_DEFAULTS}
    if not plan:
        return clean, "", []
    stored = st.plans.find(plan)
    if stored is None:
        known = ", ".join(x.name for x in st.plans.plans) or "(none saved yet)"
        raise KeyError(f"no saved plan named {plan!r}. Saved: {known}")

    overrides = {k: v for k, v in clean.items() if v != PLAN_DEFAULTS.get(k)}
    merged = {**PLAN_DEFAULTS, **stored.kwargs(), **overrides}
    notes = []
    if stored.notes:
        notes.append(f"{stored.name}: {stored.notes}")
    changed = sorted(k for k, v in overrides.items() if stored.kwargs().get(k) != v)
    if changed:
        notes.append(
            f"plan {stored.name!r} overridden this call: {', '.join(changed)} "
            "(not saved -- pass save_as to keep it)"
        )
    return merged, stored.name, notes


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

    prepared = prepare(g, st, plan_kwargs, objective_label=objective, audit=True)
    if prepared.failure:
        hint = (
            "with equality balances, infeasible usually means a byproduct has no "
            "consumer and no legal sink -- try adding it to exports"
        )
        notes = (
            [*prepared.failure.notes, hint]
            if "INFEASIBLE" in prepared.failure.headline
            else prepared.failure.notes
        )
        return render.envelope(f"# {prepared.failure.headline}", "", notes)
    req, sol = prepared.request, prepared.solution
    sel = req.selection
    sel = req.selection
    audit_ok, audit_val = prepared.audit_ok, prepared.audit_value

    rows_out = [
        (
            p["machines"],
            f"{p['clock'] * 100:.4g}%",
            p["label"][:42],
            p["building"][:20],
            render.num(p["mw"]),
            "BUILD" if p["building_id"] and st.built(p["building_id"]) == 0 else "",
        )
        for p in sol.processes[: render.clamp(limit, default=15)]
    ]
    notes = [*sel.errors, *req.recipe_errors, *prepared.notes]

    # Water has no nodes, no purity and no geometry in any data this project can read, so
    # the extractor count is bounded by an ASSUMPTION rather than by the map.
    #
    # What that assumption stands in for was WRONG until the player corrected it: pumps go
    # on foundation platforms built out over open water, so shoreline frontage plays no
    # part. This note used to quote "m of shoreline if placed in a single line", which
    # made a perfectly ordinary 105-pump plan look impossible. Area and concrete are the
    # honest costs, and both are small.
    n_water = sum(
        p["machines"] for p in sol.processes if p.get("building_id") == "Build_WaterPump_C"
    )
    if n_water >= WATER_EXTRACTOR_WARN_AT and not plan_kwargs.get("water_extractors"):
        pump = g.buildings.get("Build_WaterPump_C")
        size = pump.footprint if pump else None
        space = ""
        if size:
            # Both shapes, because the length is the number you lay platforms against and
            # the block is the cheapest way to buy it. Packed, not n x footprint: that
            # product ignores shared edges and overstates the concrete by about a third.
            block, pier = size.pack(n_water), size.pack(n_water, columns=1)
            space = (
                f" Each is {size}, so {n_water} of them pack into {block} "
                f"({block.foundations:,} foundations, {block.foundations * 5:,} Concrete), "
                f"or a single pier {pier.width_m:,.0f}x{pier.depth_m:,.0f} m "
                f"({pier.foundations:,} foundations). Counting each machine's "
                f"{size.foundations} foundations separately would say "
                f"{n_water * size.foundations:,} -- that ignores shared edges."
            )
        # OQ5 said water pumps carry no geometry and could not be matched to anything.
        # The volume SHAPE is level geometry and genuinely absent, but its IDENTITY is in
        # every pump's mExtractableResource and the sidecar has stored it all along. Sea
        # level falls out of the same rows, which turns "must be drawn at sea level" from
        # a rule of thumb into a number.
        water = st.water_volumes()
        existing = ""
        if water["pumps"]:
            bodies = ", ".join(f"{k} ({n})" for k, n in list(water["volumes"].items())[:3])
            existing = (
                f" You already run {water['pumps']} pump(s) across "
                f"{len(water['volumes'])} distinct water bod(ies): {bodies}."
            )
            if water["sea_level_m"] is not None:
                existing += (
                    f" They all sit at {water['sea_level_m']:.1f}m"
                    f" (spread {water['sea_level_span_m']:.2f}m), which is this world's"
                    " sea level and the height any new pump has to be drawn at."
                )
        notes.append(
            f"{n_water} Water Extractor(s): the COUNT is capped by assumption -- a water "
            "volume's shape is level geometry and is not in the save, so nothing here "
            "knows how many a body of water holds. Shoreline is NOT the limit: pumps sit "
            "on platforms built out over open water, so only area and concrete cost "
            "anything. What does bind is vertical -- water is the only fluid that must be "
            f"drawn at sea level and cannot be gravity-fed, so it sets deck order.{space}"
            f"{existing} Pass water_extractors=<what your site holds> for a measured limit"
        )
    # Shards and sloops, from the clocks the plan already chose. Hand-totalling this is
    # error-prone in a specific way: a shard raises the MAXIMUM clock by 0.5, so a
    # machine at 150% needs one and only a machine at 250% needs three. Assuming three
    # apiece overstates a mixed plan badly.
    bill = slice_of(prepared, g)
    if bill.shard_rows:
        budget = st.shard_budget()
        detail = ", ".join(
            f"{r.machines}x {r.label.split(' on ')[0]} @{r.clock:.0%} = {r.total}"
            for r in bill.shard_rows[:4]
        )
        verdict = (
            "already free"
            if bill.shards <= budget["free"]
            else "affordable after crafting slugs"
            if bill.shards <= budget["potential"]
            else f"SHORT by {bill.shards - budget['potential']:.0f}"
        )
        notes.append(
            f"power shards: {bill.shards} needed ({detail}); you hold "
            f"{budget['free']:.0f} free + {budget['craftable']:.0f} craftable "
            f"= {budget['potential']:.0f} -- {verdict}"
        )
    aside = (
        f" A further {bill.unboostable_slots} slot(s) sit in generators and extractors, "
        "which this model cannot production-boost, so they are not counted as capacity."
        if bill.unboostable_slots
        else ""
    )
    budget = int(plan_kwargs.get("sloops") or 0)
    # A sloop budget is only spendable if Production Amplifier is researched. Same class
    # of check as the unlocked recipe set and the buildable set, which build_scenario
    # already applies -- a plan using a locked capability is not a plan. It is a note
    # rather than a refusal because planning ahead of the research is legitimate, and the
    # research itself is cheap; what is not acceptable is being silent about it.
    gate = st.research_gate("production_boost") if budget else None
    if gate is not None:
        bill_line = ", ".join(f"{r['need']:g} {r['name']}" for r in gate["cost"])
        verdict = (
            "you can afford that now"
            if gate["affordable"]
            else "short of "
            + ", ".join(f"{r['need'] - r['have']:.0f} {r['name']}" for r in gate["short"])
        )
        notes.append(
            f"sloops={budget} but PRODUCTION AMPLIFIER IS NOT RESEARCHED, so no somersloop "
            f"can go in a machine yet and this plan is not buildable as printed. Research "
            f"{gate['schematic_name']} in the MAM ({bill_line}) -- {verdict}. "
            "The save carries no flag for this; it is read from your purchased schematics"
        )
    if bill.sloop_used_rows:
        spent = ", ".join(
            f"{r.machines}x{r.slots_each} in {r.label[:26]} = {r.total} ({r.boost:g}x)"
            for r in bill.sloop_used_rows[:4]
        )
        held = st.sloop_budget()
        # The overshoot guard. The LP spends sloops against machine-EQUIVALENTS and the
        # build table rounds those up to whole machines, so an honest bill can exceed the
        # budget it was solved under. Same shape as the extractor cap that reported 64
        # machines under a limit of 54; caught here rather than left for the player to
        # discover at the workbench.
        over = (
            f" -- ROUNDING UP to whole machines needs {bill.sloops_used - budget} more "
            f"than the budget of {budget}; drop a machine or raise it"
            if bill.sloops_used > budget
            else ""
        )
        short = (
            f" You hold {held['free']:.0f} free, so this is SHORT by "
            f"{bill.sloops_used - held['free']:.0f}."
            if bill.sloops_used > held["free"]
            else f" You hold {held['free']:.0f} free."
        )
        # Committed sloops are read from InventoryPotential, the same component as shards.
        # Only FREE ones can pay for this plan, so the committed count is context and never
        # added in -- but it is worth showing, because pulling one out of a machine is a
        # legitimate way to fund a plan and the player cannot do that if nobody says where
        # they are.
        unmeasured = (
            f" A further {held['committed']:.0f} sit in {len(held['holders'])} machine(s) "
            "and would have to be pulled out first."
            if held["committed_measured"] and held["committed"]
            else ""
            if held["committed_measured"]
            else " Slotted sloops are unreadable on this projection (pre-schema-10), so "
            "'held' may undercount what you own."
        )
        spare = (
            f" {bill.sloop_slots} boostable slot(s) are still empty, so a bigger budget "
            "has somewhere to go."
            if bill.sloop_slots
            else ""
        )
        notes.append(
            f"somersloops: {bill.sloops_used} spent ({spent}){over}.{short}{unmeasured}"
            f"{spare}{aside}"
        )
    elif bill.sloop_rows:
        top = bill.sloop_rows[0]
        why = (
            " The budget bought nothing here: every boost costs 4x power for 2x output, "
            "and this plan is power-limited."
            if budget
            else " Reported, not spent -- pass sloops=<how many you will commit> to "
            "let the solver use them"
        )
        notes.append(
            f"somersloops: {bill.sloop_slots} boostable slot(s), none used. Filling "
            f"{top.label[:28]} ({top.machines}x{top.slots_each}={top.total}) would run "
            f"it at {top.boost:g}x output for 4x power, halving that block."
            f"{aside}{why}"
        )

    # Supplied items are FREE here, which is the point and also the trap. advisor.py
    # records what it costs to forget: feeding a basket in as free raw inflated a
    # northern baseline from 92,269 MW to 171,882. Correct for a MODULE, whose inputs are
    # paid for in the plan that makes them, and badly wrong for a whole-plant comparison.
    if req.scenario.raw_caps:
        given = ", ".join(
            f"{v:g} {g.item_name(k)}/min" for k, v in sorted(req.scenario.raw_caps.items())
        )
        notes.append(
            f"MODULE PLAN -- {given} arrive free, so this net_MW is NOT comparable with a "
            "whole-plant plan: the cost of making them is charged wherever they are made. "
            "Whatever produces them must export at least these rates, or the chain does "
            "not balance and the surplus you think you have is not there"
        )
    # A POWER-BLIND objective drives clocks up, and the cost is invisible in its own
    # answer. Phase 2 pins the goal and minimises MACHINE COUNT, so among all solutions
    # that hit the target it picks the fewest machines -- which means the highest clocks,
    # and power goes as clock**1.32. Measured while chaining modules: a rig solved for
    # max_item Fuel ran 31 Water Extractors at 247% for 2,052 MW where 64 at 120% cost
    # 1,625, and the whole 344 MW "cost of decomposition" turned out to be this and
    # nothing else. Re-solved with min_power, the chain BEAT the single plan.
    overclocked = [p for p in sol.processes if p["clock"] > 1.01 and p["kind"] != "extractor"] + [
        p for p in sol.processes if p["clock"] > 1.01 and p["kind"] == "extractor"
    ]
    if objective in ("max_item", "min_raw", "min_machines") and overclocked and sol.net_mw < 0:
        worst = max(overclocked, key=lambda p: p["clock"])
        notes.append(
            f"objective {objective!r} does not price POWER, and phase 2 breaks ties by "
            f"minimising machines -- so clocks are pushed up ({worst['machines']}x "
            f"{worst['label'][:28]} at {worst['clock']:.0%}) and power rises as clock^1.32. "
            "If this plan feeds a power plant, solve it as min_power with "
            "export_minimums instead; the same output on more machines can cost "
            "hundreds of MW less"
        )
    if req.excluded:
        notes.append("excluded by request: " + ", ".join(req.excluded))
    if not audit_ok:
        notes.append(f"GUARD FAILED: free-lunch audit returned {audit_val} MW, not 0")
    for b in sol.binding[:6]:
        notes.append(f"binding: {b}")
    needed = {
        p["building_id"]
        for p in sol.processes
        if p["building_id"] and st.built(p["building_id"]) == 0
    }
    if needed:
        notes.append(
            "must build first: "
            + ", ".join(g.buildings[c].name for c in needed if c in g.buildings)
        )

    # Water is modelled as unlimited (it effectively is on this map), so the honest
    # thing is to surface the extractor count and pipe lines it implies rather than
    # let that burden hide inside a ratio.
    water_extractors = sum(
        p["machines"] for p in sol.processes if p["building_id"] == "Build_WaterPump_C"
    )
    # Rows rank by volume, and a two-item question usually lives in the tail: at the
    # old hard cap of 6, Plastic and Rubber fell off the bottom of a large oil plan --
    # the exact pair that plan existed to size. So the cap is the caller's `limit`,
    # and named items are pinned above it whatever they rank.
    flows = [e for e in sol.logistics if e["rate"] > 0]
    pin: list[str] = []
    for name in logistics_items or []:
        item_id = resolve_item(g, name)
        if item_id is None:
            notes.append(f"logistics_items: no item matches {name!r}")
        elif not any(e["item"] == item_id for e in flows):
            notes.append(f"logistics: nothing moves {g.item_name(item_id)} in this plan")
        else:
            pin.append(item_id)
    # Pins are ADDITIVE to the limit, not carved out of it: naming two small items
    # must not silently drop two big ones, or the fix trades one blind spot for another.
    rest = [e for e in flows if e["item"] not in pin]
    top_flows = [e for e in flows if e["item"] in pin] + rest[: render.clamp(limit, default=15)]
    if len(top_flows) < len(flows):
        notes.append(
            f"logistics: showing {len(top_flows)} of {len(flows)} flows by volume "
            "-- raise limit, or name items in logistics_items to pin them"
        )
    logistics_block = ""
    if top_flows:
        logistics_block = "\n# logistics (lines at Mk5 belt / Mk2 pipe)\n" + render.table(
            ("item", "rate", "carrier", "lines"),
            [
                (e["name"], f"{render.num(e['rate'])}{e['unit']}", e["carrier"], e["lines"])
                for e in top_flows
            ],
        )

    summary = "\n".join(
        [
            (
                f"# {objective} over {sel.description} "
                f"({'free nodes only' if only_free_nodes else 'all nodes'})"
            ),
            f"# {st.age_note}",
            render.kv(
                [
                    ("net_MW", render.num(sol.net_mw)),
                    ("buildings", render.num(sol.machines_total)),
                    ("water_extractors", water_extractors or None),
                    ("grid_import_MW", render.num(sol.grid_import_mw)),
                ]
            ),
            "exports: "
            + render.kv(
                [
                    ("MW" if k == MW else g.item_name(k), render.num(v))
                    for k, v in sol.exports.items()
                ]
            ),
            "raw: " + render.kv([(g.item_name(k), render.num(v)) for k, v in sol.raw_used.items()]),
            "sunk: "
            + (
                render.kv([(g.item_name(k), render.num(v)) for k, v in sol.sunk.items()])
                or "nothing"
            ),
        ]
    )
    if plan_name:
        summary = f"# recalled plan {plan_name!r}\n" + summary
    notes = [*plan_notes, *notes]

    if save_as:
        stored = st.plans.put(
            save_as,
            plan_kwargs,
            req.plan_id,
            notes=plan_notes_text,
            factory=for_factory,
            when=str(st.header.get("save_datetime") or st.header.get("filename") or ""),
        )
        path = st.plans.save()
        notes.append(
            f"saved as {stored.name!r} (plan_id {req.plan_id}) in {path}. "
            f"Recall with plan={stored.name!r} on plan_factory, plan_layout or diff_vs_save"
        )

    return render.envelope(
        summary,
        render.table(
            ("build", "clock", "process", "building", "MW", "note"),
            rows_out,
            total=len(sol.processes),
            limit=limit,
        )
        + logistics_block,
        notes,
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

    # Keyed on a NORMALISED tier token, because these lookups were dead. The keys used to
    # be `name.replace("Conveyor Belt ", "")`, which yields "Mk.5" -- while the parameter
    # defaults were "Mk5" and "Mk2". Nothing ever matched, every call silently fell back to
    # the hardcoded 780/600, and it looked correct only because those were the same
    # numbers. Passing belt_tier="Mk3" would have quietly planned at Mk5 speed.
    def _tier(name: str, prefix: str) -> str:
        return name.replace(prefix, "").replace(".", "").strip().casefold()

    belts = {
        _tier(b.name, "Conveyor Belt "): b.items_per_min
        for b in g.buildings.values()
        if b.native == st.BELT_NATIVE
    }
    pipes = {
        _tier(b.name, "Pipeline "): b.flow_m3_min
        for b in g.buildings.values()
        if b.native == st.PIPE_NATIVE and "Clean" not in b.name
    }
    tier_errors = [
        f"unknown {what}_tier {given!r}; known: {', '.join(sorted(table))}"
        for what, given, table in (("belt", belt_tier, belts), ("pipe", pipe_tier, pipes))
        if given and _tier(given, "") not in table
    ]
    if tier_errors:
        return render.envelope("# unknown carrier tier", "", tier_errors)
    asked_belt, asked_pipe = bool(belt_tier), bool(pipe_tier)
    belt_tier, pipe_tier = _tier(belt_tier, ""), _tier(pipe_tier, "")
    # Blank means "what this save can actually build". A hardcoded Mk5/Mk2 default ran
    # unverified through an entire design session; it happened to be right, which is not
    # the same as being checked.
    best_belt, best_pipe = st.best_belt(), st.best_pipe()
    belt_ipm = belts.get(belt_tier) or (best_belt[1] if best_belt else 780.0)
    pipe_m3min = pipes.get(pipe_tier) or (best_pipe[1] if best_pipe else 600.0)
    belt_tier = belt_tier or (g.buildings[best_belt[0]].name if best_belt else "Mk5")
    pipe_tier = pipe_tier or (g.buildings[best_pipe[0]].name if best_pipe else "Mk2")
    tier_note = (
        f"carriers are the fastest you have UNLOCKED: {belt_tier} at "
        f"{render.num(belt_ipm)}/min and {pipe_tier} at {render.num(pipe_m3min)} m3/min. "
        "Pass belt_tier/pipe_tier to plan against a different one -- an unlocked tier "
        "assumed rather than checked changes every line count in this schematic"
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
        belt_ipm=belt_ipm if asked_belt else None,
        pipe_m3min=pipe_m3min if asked_pipe else None,
    )
    try:
        plan_kwargs, plan_name, plan_notes = _plan_kwargs(st, plan, supplied)
    except KeyError as exc:
        return f"! {exc.args[0]}"

    prepared = prepare(g, st, plan_kwargs, objective_label=objective, diagnose=False)
    if prepared.failure:
        suffix = " -- nothing to lay out" if "INFEASIBLE" in prepared.failure.headline else ""
        return render.envelope(
            f"# {prepared.failure.headline}{suffix}",
            "",
            [*prepared.failure.notes, "see plan_factory for why"],
        )
    req, sol = prepared.request, prepared.solution
    sel = req.selection

    lay = build_layout(
        g,
        sol,
        belt_ipm=belt_ipm,
        pipe_m3min=pipe_m3min,
        max_floor_foundations=max_floor_foundations,
        order_floors_by=order_floors_by,
    )
    production = [f for f in lay.floors if f.kind == "production"]
    logistics = [f for f in lay.floors if f.kind == "logistics"]

    # Floors follow CHAIN DEPTH, which keeps the schematic in build order but says
    # nothing about head. Chain depth tends to make every fluid climb; the model has no
    # terrain and no view of where crude arrives, so the cost is named, not optimised.
    # The best pump the player can build, so a riser count is against a real tier.
    pump = max(
        (b for c, b in g.buildings.items() if b.head_lift_m and c in st.unlocked_building_ids),
        key=lambda b: b.head_lift_m,
        default=None,
    )
    pump_head = pump.head_lift_m if pump else 0.0
    pump_name = pump.name if pump else "pump"
    climbing = [d for d in fluid_head(lay, pump_head) if d["direction"] == "climbs"]

    summary = "\n".join(
        [
            f"# layout for {objective} over {sel.description}",
            f"# {st.age_note}",
            render.kv(
                [
                    ("net_MW", render.num(sol.net_mw)),
                    ("machines", lay.machines),
                    ("blocks", len(lay.blocks)),
                    ("floors", f"{len(production)} production + {len(logistics)} logistics"),
                    ("stack_height", f"{lay.height_m:g}m"),
                ]
            ),
            render.kv(
                [
                    ("peak_floor_foundations", lay.foundations),
                    ("site", f"~{lay.site_side_m():g}x{lay.site_side_m():g}m"),
                    (
                        "carriers",
                        (
                            f"{belt_tier} belt {render.num(belt_ipm)}/min, "
                            f"{pipe_tier} pipe {render.num(pipe_m3min)}m3/min"
                        ),
                    ),
                ]
            ),
        ]
    )

    notes = [*lay.warnings, tier_note]
    notes.append(
        "schematic only: no world coordinates or belt routing -- there is no terrain "
        "data available, so those would be invented"
    )
    needed = {b.building_id for b in lay.blocks if b.building_id and st.built(b.building_id) == 0}
    if needed:
        notes.append(
            "must build first: "
            + ", ".join(g.buildings[c].name for c in needed if c in g.buildings)
        )

    if detail == "blocks":
        rows = [
            (
                b.name[:36],
                f"F{b.stage}",
                b.machines,
                f"{b.clock * 100:.4g}%",
                f"{b.width_m:g}x{b.depth_m:g}",
                b.foundations,
                ", ".join(
                    f"{render.num(v)} {g.item_name(k)}"
                    for k, v in sorted(b.inputs.items(), key=lambda kv: -kv[1])[:2]
                )
                or "-",
                ", ".join(
                    f"{render.num(v)} {g.item_name(k)}"
                    for k, v in sorted(b.outputs.items(), key=lambda kv: -kv[1])[:2]
                )
                or "-",
            )
            for b in sorted(lay.blocks, key=lambda b: (b.stage, -b.machines))[
                : render.clamp(limit, default=20)
            ]
        ]
        body = render.table(
            ("block", "floor", "n", "clock", "each(m)", "found", "in/min", "out/min"),
            rows,
            total=len(lay.blocks),
            limit=limit,
        )
    elif detail == "sites":
        if not sites:
            return render.envelope(
                "# detail='sites' needs sites=",
                "",
                [
                    (
                        'sites maps a name to patterns, e.g. {"rig": ["Heavy Oil '
                        'Residue", "Diluted Fuel", "Water Extractor"], "hall": '
                        '["Fuel-Powered Generator"]}'
                    ),
                    (
                        "patterns match the same way exclude_recipes does: process "
                        "label, building name, or recipe"
                    ),
                ],
            )
        sp = partition(prepared, g, sites)
        rows = [
            (
                i.source[:14],
                "->",
                i.target[:14],
                i.name[:20],
                render.num(i.rate),
                f"{i.lines}x {i.carrier}",
            )
            for i in sp.interfaces[: render.clamp(limit, default=20)]
        ]
        body = render.table(
            ("from", "", "to", "item", "rate", "carrier"),
            rows,
            total=len(sp.interfaces),
            limit=limit,
        )
        body = (
            render.table(
                ("site", "machines", "net_MW"),
                [(x.name, x.machines, render.num(x.net_mw)) for x in sp.sites],
            )
            + "\n\n"
            + body
        )
        notes.extend(sp.notes)
        if sp.ok:
            notes.append(
                "every process is assigned to exactly one site, so this interface table "
                "is complete: each flow's destination is stated rather than assumed"
            )
        else:
            notes.append(
                "the partition is INCOMPLETE, so the interface table is missing flows. "
                "This is the error a hand reconciliation makes -- a rig's whole fuel "
                "output looks like it reaches the generators until you notice something "
                "else was drinking it"
            )
        notes.append(
            "a shared flow is split between consumers by SHARE. The LP gives net balances "
            "and never who fed whom, so any exact producer-consumer pairing would be "
            "invented -- the same reason a layout models a bus rather than pairs"
        )
        notes.append(
            "site net_MW excludes the AWESOME Sink charge, which belongs to the plan as a "
            "whole and cannot be attributed to one site"
        )
    elif detail == "materials":
        # Foundations live here and nowhere else -- they are not machines, so no build
        # table counts them, and at 5 Concrete each a big deck outweighs most of the
        # machine bill. This is why the construction bill hangs off plan_layout rather
        # than plan_factory: only the layout knows how many tiles the plan stands on.
        #
        # TOTAL, not `lay.foundations`. That property is the PEAK floor, which is what
        # sizes the site -- floors stack, so the ground you need is the biggest one. But
        # you pour concrete for every floor, so charging the peak would understate the
        # deck by however many storeys the stack has.
        # Risers are part of the build and were missing entirely, so a fluid-heavy plan's
        # bill understated itself. Counted from the floors the fluid actually crosses,
        # priced at the best pump this save can place.
        riser_pumps = sum(
            row["pumps"] for row in fluid_head(lay, pump_head) if row["direction"] == "climbs"
        )
        extra = (
            [{"building_id": pump.cls, "machines": riser_pumps}]
            if pump is not None and riser_pumps
            else []
        )
        bill = build_materials(g, [*sol.processes, *extra], st.stock(), lay.total_foundations)
        rows = [
            (
                line.name[:26],
                render.num(line.needed),
                render.num(line.held),
                render.num(line.short) if line.short else "",
                ", ".join(line.wanted_by)[:38],
            )
            for line in bill.lines[: render.clamp(limit, default=20)]
        ]
        body = render.table(
            ("item", "need", "have", "short", "for"),
            rows,
            total=len(bill.lines),
            limit=limit,
        )
        biggest = sorted(bill.buildings, key=lambda b: -b.items)[:3]
        body = (
            f"machines={bill.machines}  foundations={bill.foundations}  "
            f"distinct_parts={len(bill.lines)}\n"
            + "costliest: "
            + ", ".join(f"{b.count}x {b.name} = {b.items:,} parts" for b in biggest)
            + "\n\n"
            + body
        )
        notes.extend(bill.notes)
        short = bill.shortfall
        notes.append(
            "you can afford every part of this from stock"
            if not short
            else "short of "
            + ", ".join(f"{render.num(x.short)} {x.name}" for x in short[:4])
            + (f", and {len(short) - 4} more" if len(short) > 4 else "")
        )
        notes.append(
            "construction cost only, and NOT the same question as diff_vs_save's cost "
            "table: this prices the WHOLE plan, that one prices what is left to place "
            "and lists only what you are short of"
        )
        notes.append(
            "stock is spendable only -- carried, crates and the Dimensional Depot -- "
            "never machine buffers, which are not carryable"
        )
        if riser_pumps:
            notes.append(
                f"includes {riser_pumps} {pump_name}(s) for the fluid risers -- these were "
                "missing entirely, so a fluid-heavy plan used to understate its own bill"
            )
        notes.append(
            "belts and pipes are NOT costed: their cost is per metre and there is no "
            "route, so a length here would be invented. Use detail='buses' for line "
            "counts and detail='trunks' for a straight-line lower bound on the runs"
        )
        notes.append(
            "these are build-gun components, not ore. Call bom on any row to expand it "
            "-- flattening here would have to guess a depth through the Recycled loop"
        )
    elif detail == "trunks":
        # The destination decides which end of each chain is "far", so it decides the
        # sign of every lift. A named factory is the honest answer when there is one;
        # otherwise the field's own centroid, said out loud rather than assumed.
        target, target_label = None, "the node field's centroid"
        if factory:
            try:
                resolved_name, machines = _resolve_factory(st, factory)
            except SelectorError as exc:
                return f"! {exc}"
            pts = [m["pos"] for m in machines if m.get("pos")]
            if pts:
                target = (
                    sum(p[0] for p in pts) / len(pts),
                    sum(p[1] for p in pts) / len(pts),
                )
                target_label = resolved_name
        tp = plan_trunks(prepared, g, target, target_label)
        # The best pump the player can actually build, not the best that exists: a Mk2
        # lifts 50 m against a Mk1's 20, so quoting Mk2 to someone who has not unlocked it
        # understates the build by more than half.
        pumps = [
            b for c, b in g.buildings.items() if b.head_lift_m and c in st.unlocked_building_ids
        ]
        best = max(pumps, key=lambda b: b.head_lift_m, default=None)
        pump_head = best.head_lift_m if best else 0.0
        pump_name = best.name if best else "pump"
        rows = []
        for i, t in enumerate(tp.trunks, 1):
            # Head is a FLUID concern only. A belt does not care that its coal climbs
            # 218 m, and printing a number there invites a pump that cannot exist.
            climb = ""
            if t.carrier == "pipe" and abs(t.lift_m) >= 1.0:
                climb = f"{'down' if t.lift_m > 0 else 'UP'} {abs(t.lift_m):.0f}m"
                # A real count now: mDesignPressure is the pump's head lift in metres.
                need = t.pumps(pump_head)
                if need:
                    climb += f" ({need}x {pump_name})"
            rows.append(
                (
                    f"T{i}",
                    t.name[:16],
                    len(t.members),
                    f"{render.num(t.rate)}/{render.num(t.capacity)}",
                    f"{t.used:.0%}",
                    f"{t.run_m:.0f}m",
                    climb,
                    ", ".join(f"{m.short}:{m.purity[:3]}" for m in t.members[:4]),
                )
            )
        body = render.table(
            ("trunk", "item", "nodes", "rate", "full", "run", "head", "nodes tapped"),
            rows,
            total=len(tp.trunks),
            limit=limit,
        )
        notes.extend(tp.notes)
        notes.append(
            f"trunks converge on {tp.destination_label}. `run` is the straight-line chain "
            "node to node, so it is a LOWER BOUND on pipe -- no terrain data exists here. "
            "`head` is the climb from the far end inward: UP needs pumping, down does not. "
            f"Pump counts assume {pump_name} at {pump_head:.0f}m head (mDesignPressure) and "
            "are a LOWER bound: pipe friction and the head a full pipe holds on its own are "
            "not modelled"
        )
        for name, rate, count in tp.placeless:
            notes.append(
                f"{count}x {name} extractor(s) carrying {render.num(rate)}/min sit on no "
                "node, so they get no trunk -- water comes from water volumes, which "
                "carry no geometry here. Site them at the shore and pipe inward"
            )
    elif detail == "buses":
        rows = [
            (
                b.name[:24],
                f"{render.num(b.rate)}{b.unit}",
                b.carrier,
                b.lines,
                f"F{b.from_stage}->F{b.to_stage}",
                len(b.producers),
                len(b.consumers),
                "leaves site" if b.external else "",
            )
            for b in lay.buses[: render.clamp(limit, default=20)]
        ]
        body = render.table(
            ("item", "rate", "carrier", "lines", "flow", "from", "to", "note"),
            rows,
            total=len(lay.buses),
            limit=limit,
        )
    else:
        rows = []
        for f in lay.floors:
            if f.kind == "production":
                contents = ", ".join(
                    f"{b.machines}x {b.label[:22]}"
                    for b in sorted(f.blocks, key=lambda b: -b.machines)[:2]
                )
                rows.append(
                    (
                        f"F{f.index}",
                        f"stage {f.stage}",
                        len(f.blocks),
                        f.machines,
                        f"{f.height_m:g}m",
                        f.foundations,
                        contents,
                    )
                )
            else:
                rows.append(
                    (
                        f"L{f.index}",
                        "logistics",
                        len(f.buses),
                        "",
                        f"{f.height_m:g}m",
                        "",
                        ", ".join(f"{b.name} {b.lines}x{b.carrier}" for b in f.buses[:4]),
                    )
                )
        body = render.table(
            ("floor", "kind", "n", "machines", "height", "found", "contents"),
            rows,
            total=len(lay.floors),
            limit=limit,
        )
        notes.append(
            'detail="blocks" for every module, detail="buses" for item flows, '
            'detail="trunks" for which nodes share a pipe, detail="materials" for '
            "what it costs to build"
        )

    if plan_name:
        plan_notes = [f"recalled saved plan {plan_name!r}", *plan_notes]

    scope_name = factory
    if scope_name is None and plan:
        stored = st.plans.find(plan)
        scope_name = (stored.factory or None) if stored else None
    if scope_name:
        from ..planning.fit import assess_fit

        try:
            resolved_name, machines = _resolve_factory(st, scope_name)
        except SelectorError as exc:
            return f"! {exc}"
        fit = assess_fit(resolved_name, machines, lay, st.structures, st.projection)
        still = ", ".join(fit.to_build[:8]) if fit.to_build else ""
        head = [
            f"## fit against {resolved_name}",
            fit.headline(),
            (
                f"blocks: {len(fit.standing)} standing ({fit.machines_standing} machines), "
                f"{len(fit.to_build)} to build ({fit.machines_to_build} machines)"
            ),
        ]
        if still:
            head.append(f"still to build: {still}")
        body = "\n".join(head) + "\n\n" + body
        plan_notes = [*plan_notes, *fit.notes]

    pumps_total = sum(row["pumps"] for row in climbing)
    if pumps_total:
        notes.append(
            f"risers need at least {pumps_total} {pump_name}(s): "
            + ", ".join(
                f"{d['item']} {d['lines']}x pipe up {d['metres']:.0f}m = {d['pumps']}"
                for d in climbing
                if d["pumps"]
            )
            + ". A LOWER bound -- pipe friction and the head a full pipe holds are not "
            "modelled"
        )
    if climbing:
        notes.append(
            "floors follow chain depth, not fluid head: "
            + ", ".join(
                f"{d['item']} climbs {d['floors']} floor(s) at {render.num(d['rate'])}{d['unit']}"
                for d in climbing[:4]
            )
            + ". Water can only be drawn at sea level, so putting its extractors at the "
            "bottom with consumers above lets the rest of the stack fall instead"
        )

    return render.envelope(summary, body, [*plan_notes, *notes])


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
            resolved_name, machines = _resolve_factory(st, scope_name)
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
