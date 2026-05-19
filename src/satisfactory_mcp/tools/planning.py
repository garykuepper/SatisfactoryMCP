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
from ..planning import byproducts, compare, supply
from ..planning.diff import NEIGHBOUR_RADIUS_M as DIFF_NEIGHBOUR_M
from ..planning.diff import build_diff
from ..planning.layout import build_layout, fluid_head
from ..planning.optimize import MW, free_lunch_audit, solve
from ..planning.scenario import EXPORT_HELP, build_scenario, resolve_item
from ..spatial.select import SELECTOR_HELP

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
    )
    try:
        plan_kwargs, plan_name, plan_notes = _plan_kwargs(st, plan, supplied)
    except KeyError as exc:
        return f"! {exc.args[0]}"

    req = build_scenario(g, st, **plan_kwargs)
    sel, sc = req.selection, req.scenario
    if sel.errors and not sel.nodes:
        return render.envelope("# no sources selected", "", [*sel.errors, SELECTOR_HELP])
    if req.export_errors:
        return render.envelope("# unusable exports", "", [*req.export_errors, EXPORT_HELP])

    audit_ok, audit_val = free_lunch_audit(sc)
    sol = solve(sc)
    if not sol.ok:
        return render.envelope(
            f"# INFEASIBLE ({objective})",
            "",
            [
                *sol.warnings,
                *supply.describe(supply.diagnose(req, g, st.unlocked_building_ids), g),
                (
                    "with equality balances, infeasible usually means a byproduct has "
                    "no consumer and no legal sink -- try adding it to exports"
                ),
            ],
        )

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
    notes = [*sel.errors, *req.recipe_errors, *sol.warnings]

    # Water has no nodes, no purity and no geometry in any data this project can read,
    # so the extractor count is bounded by an ASSUMPTION rather than by the map. Say so
    # once it is large enough to matter: a measured plan wanted 105 extractors and
    # 12,400 m3/min -- more than its Fuel -- on a platform whose perimeter fits ~27.
    n_water = sum(
        p["machines"] for p in sol.processes if p.get("building_id") == "Build_WaterPump_C"
    )
    if n_water >= WATER_EXTRACTOR_WARN_AT and not plan_kwargs.get("water_extractors"):
        pump = g.buildings.get("Build_WaterPump_C")
        size = pump.footprint if pump else None
        space = ""
        if size:
            # Area is unambiguous; frontage assumes one line along a shore, which is how
            # they are actually placed, so both are given rather than one dressed up as
            # the answer.
            space = (
                f" Each is {size} ({size.foundations} foundations), so {n_water} of them "
                f"cover {n_water * size.area_m2:,.0f} m2 of water -- about "
                f"{n_water * max(size.width_m, size.depth_m):,.0f} m of shoreline if "
                "placed in a single line."
            )
        notes.append(
            f"{n_water} Water Extractor(s): siting is NOT modelled. Water comes from "
            "water volumes, which carry no node, purity or geometry here, so the count "
            "is capped by assumption, not by shoreline. It is also the only fluid that "
            f"must be sourced at sea level and cannot be gravity-fed.{space} Pass "
            "water_extractors=<what your site holds> to plan against the real limit"
        )
    # A SUCCESSFUL plan can still be answering a question it cannot answer. An export
    # nothing produces is now pinned to zero rather than conjured, but zero output is a
    # quiet answer, so the reason is said out loud on this path too -- not only when the
    # solve fails. Costs nothing: this branch of the diagnostic runs no probe.
    for line in supply.unmakeable(req, g):
        notes.append(line + " -- it is pinned to 0 in this plan")
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
    belt_tier: str = "Mk5",
    pipe_tier: str = "Mk2",
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
    "blocks" (every module with its size and rates) or "buses" (item flows).

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

    belts = {
        b.name.replace("Conveyor Belt ", ""): b.items_per_min
        for b in g.buildings.values()
        if b.items_per_min
    }
    pipes = {
        b.name.replace("Pipeline ", ""): b.flow_m3_min
        for b in g.buildings.values()
        if b.flow_m3_min
    }
    belt_ipm = belts.get(belt_tier, 780.0)
    pipe_m3min = pipes.get(pipe_tier, 600.0)

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
    )
    try:
        plan_kwargs, plan_name, plan_notes = _plan_kwargs(st, plan, supplied)
    except KeyError as exc:
        return f"! {exc.args[0]}"

    req = build_scenario(g, st, **plan_kwargs)
    sel, sc = req.selection, req.scenario
    if sel.errors and not sel.nodes:
        return render.envelope("# no sources selected", "", [*sel.errors, SELECTOR_HELP])
    if req.export_errors:
        return render.envelope("# unusable exports", "", [*req.export_errors, EXPORT_HELP])

    sol = solve(sc)
    if not sol.ok:
        return render.envelope(
            f"# INFEASIBLE ({objective}) -- nothing to lay out",
            "",
            [*sol.warnings, "see plan_factory for why"],
        )

    lay = build_layout(g, sol, belt_ipm=belt_ipm, pipe_m3min=pipe_m3min)
    production = [f for f in lay.floors if f.kind == "production"]
    logistics = [f for f in lay.floors if f.kind == "logistics"]

    # Floors follow CHAIN DEPTH, which keeps the schematic in build order but says
    # nothing about head. Chain depth tends to make every fluid climb; the model has no
    # terrain and no view of where crude arrives, so the cost is named, not optimised.
    climbing = [d for d in fluid_head(lay) if d["direction"] == "climbs"]

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

    notes = [*lay.warnings]
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
        notes.append('detail="blocks" for every module, detail="buses" for item flows')

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

    req = build_scenario(g, st, **plan_kwargs)
    sel = req.selection
    if sel.errors and not sel.nodes:
        return render.envelope("# no sources selected", "", [*sel.errors, SELECTOR_HELP])
    if req.export_errors:
        return render.envelope("# unusable exports", "", [*req.export_errors, EXPORT_HELP])

    sol = solve(req.scenario)
    if not sol.ok:
        # Hand back the plan's own reason. An empty diff table would read as "you
        # already have it", which is the opposite of what infeasible means.
        return render.envelope(
            f"# INFEASIBLE ({objective}) -- no plan to diff against",
            "",
            [*sol.warnings, "see plan_factory for why; there is nothing to change yet"],
        )

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
    if rep.deficit_mw > 0 and rep.slices > 1:
        parts.append(
            "# ORDER: an LP solution is a ray, so any fraction of the plan is itself "
            f"feasible and self-powered.\n# The build dips {render.num(rep.deficit_mw)} MW "
            f"against {render.num(rep.headroom_mw)} MW of headroom, so place it in "
            f">={rep.slices} proportional slices."
        )

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
    return byproducts.explain(
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
    return compare.render_comparison(result, limit=render.clamp(limit, default=10))


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
    return bom_mod.render_bom(result, limit=render.clamp(limit, default=20))
