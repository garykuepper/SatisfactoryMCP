"""Project Assembly phases and Power Shard budgeting.

These were spliced in under the resources banner during a parallel merge and
are tools, not resources -- both read the save and one takes a hypothetical."""

from __future__ import annotations

from typing import Annotated

from pydantic import Field

from .. import render
from ..app import Limit, _state, mcp
from ..docs.constants import CAPABILITY_SCHEMATICS, max_clock, shards_for_clock


@mcp.tool(structured_output=False)
def phase_requirements(save: str | None = None, world: str | None = None) -> str:
    """What the Space Elevator still wants, live record and deprecated record apart.

    The per-phase item table in the save is DEPRECATED and frozen, so it is shown
    labelled rather than believed. Read the header line first.
    """
    try:
        st = _state(save, world)
    except Exception as exc:
        return f"could not read save: {exc}"
    g = st.game
    req = st.phase_requirements()

    rows = []
    for row in req["phases"]:
        outstanding = row["outstanding"]
        rows.append(
            (
                row["phase"] or f"?({row['egp']})",
                row["egp"],
                row["stale"],
                len(outstanding),
                len(row["complete"]),
                " + ".join(
                    f"{render.num(a)} {g.item_name(i)}"
                    for i, a in sorted(outstanding.items(), key=lambda kv: -kv[1])
                )
                or "-",
            )
        )

    target_row = next((r for r in req["phases"] if r["phase"] == req["target_phase"]), None)
    outstanding_total = sum(target_row["outstanding"].values()) if target_row else 0
    paid = req["paid_off_target"]

    notes = [
        (
            "the per-phase amounts come from mGamePhaseCosts, which FGGamePhaseManager.h "
            "marks 'DEPRECATED Only kept for save compatibility'. It is FROZEN: identical "
            "across all 29 parseable saves of the reference world (180h-316h), including "
            "the session that completed a whole phase. stale=stale rows are wrong."
        ),
        (
            "stale=usable means the phase has never been delivered into "
            "(mTargetGamePhasePaidOffCosts is empty), so its untouched snapshot is still "
            "its true full cost. That is the only row safe to plan against."
        ),
        (
            "[UNVERIFIED for MidGame/LateGame/FoodCourt] the EGP_* -> GP_Project_Assembly_"
            "Phase_N mapping is not in Docs.json (the UFGGamePhase assets do not ship) nor "
            "joinable in the save (the legacy mGamePhase scalar is absent = EGP_NA = "
            "migrated). EGP_EndGame -> Phase_3 IS measured: at 180-244h play the same world "
            "had target=Phase_3 with paid_off={Versatile Framework 2500}, matching that "
            "key's single settled item. The rest follow by enum order from that anchor."
        ),
    ]
    if not paid:
        notes.append(
            "nothing has been delivered toward the target phase yet "
            "(mTargetGamePhasePaidOffCosts absent = empty)"
        )

    return render.envelope(
        "\n".join(
            [
                f"# {st.age_note}",
                render.kv(
                    [
                        ("current_phase", req["current_phase"]),
                        ("target_phase", req["target_phase"]),
                        ("outstanding_on_target", outstanding_total or "-"),
                    ]
                ),
                "delivered to target: "
                + (
                    " + ".join(f"{render.num(a)} {g.item_name(i)}" for i, a in sorted(paid.items()))
                    or "nothing"
                ),
            ]
        ),
        render.table(("phase", "legacy_key", "trust", "outstanding", "done", "items"), rows),
        notes,
    )


@mcp.tool(structured_output=False)
def power_shards(
    save: str | None = None,
    world: str | None = None,
    plan_machines: int = 0,
    plan_clock: float = 2.5,
    limit: Limit = 10,
) -> str:
    """Power Shards held, committed and free, plus what an overclock plan would cost.

    ``plan_machines`` machines at ``plan_clock`` costs shards per machine; the answer
    says whether the free pool covers it.
    """
    try:
        st = _state(save, world)
    except Exception as exc:
        return f"could not read save: {exc}"
    budget = st.shard_budget()
    per_shard = max(budget["shard_items"].values()) if budget["shard_items"] else 0.0
    ceiling = max_clock(per_shard)

    notes = []
    if not budget["measured"]:
        notes.append(
            "[UNVERIFIED] this projection predates schema 9 and carries no "
            "InventoryPotential data, so committed=0 means unknown, not zero"
        )
    notes.append(
        f"a shard adds {render.num(per_shard)} max clock (mExtraPotential, from "
        f"Docs.json) and a building takes {budget['slots_per_building']} of them, so "
        f"max clock is {render.num(ceiling)}. The slot count is the only hardcoded "
        "number here -- mPotentialShardSlots is 0 on every building in the dump. [WIKI]"
    )
    if budget["slugs"]:
        held = ", ".join(
            f"{render.num(s['held'])} {s['name']} x{s['each']:g}" for s in budget["slugs"]
        )
        notes.append(
            f"craftable = uncrafted slugs carried, in crates or in the Dimensional "
            f"Depot: {held}. The 1/2/5 ratios come from the Power Shard (1)/(2)/(5) "
            "recipes, not from game knowledge. Craftable is POTENTIAL, not free -- "
            "crafting is a manual step"
        )

    if budget["by_place"]:
        where = "; ".join(
            f"{place}: " + ", ".join(f"{render.num(v)} {k}" for k, v in sorted(held.items()))
            for place, held in budget["by_place"].items()
        )
        notes.append(f"where they are -- {where}")

    idle = sum(h["idle"] for h in budget["holders"])
    if idle:
        notes.append(
            f"{idle} shard(s) sit in slots the current clock does not need. A shard "
            "raises the MAXIMUM clock; the slider is set separately, so committed is "
            "read from InventoryPotential and never derived from clock"
        )

    if plan_machines:
        need_each = shards_for_clock(plan_clock, per_shard)
        need = need_each * plan_machines
        short = need - budget["free"]
        line = (
            f"plan: {plan_machines} machine(s) at clock {render.num(plan_clock)} needs "
            f"{need_each} shard(s) each = {need}; free {render.num(budget['free'])}"
        )
        if short <= 0:
            notes.append(line + " -> affordable now")
        elif short <= budget["craftable"]:
            # Craftable, not free: the slugs cover it but somebody has to press craft.
            notes.append(
                line + f" -> SHORT by {render.num(short)}, but "
                f"{render.num(budget['craftable'])} more are craftable from slugs you "
                "already hold, so it is affordable after crafting"
            )
        else:
            notes.append(
                line + f" -> SHORT by {render.num(short)}; even crafting every slug "
                f"({render.num(budget['craftable'])}) leaves you "
                f"{render.num(need - budget['potential'])} short"
            )

    rows = [
        (h["cls"], render.num(h["clock"]), h["slotted"], h["needed"], h["idle"] or "")
        for h in budget["holders"][: render.clamp(limit, default=10)]
    ]
    return render.envelope(
        f"# {st.age_note}\n"
        + render.kv(
            [
                ("free", render.num(budget["free"])),
                ("craftable_from_slugs", render.num(budget["craftable"])),
                ("potential", render.num(budget["potential"])),
                ("committed", budget["committed"]),
                ("owned", render.num(budget["owned"])),
                ("overclocked_buildings", len(budget["holders"])),
            ]
        ),
        render.table(
            ("building", "clock", "slotted", "needed", "idle"),
            rows,
            total=len(budget["holders"]),
            limit=limit,
        ),
        notes,
    )


@mcp.tool(structured_output=False)
def mam_research(
    status: Annotated[
        str, Field(description="all | todo | affordable -- todo hides finished research")
    ] = "todo",
    search: Annotated[str | None, Field(description="filter by name, case-insensitive")] = None,
    save: str | None = None,
    world: str | None = None,
    limit: Limit = 25,
) -> str:
    """MAM research: what is left, what it costs, and what you can afford right now.

    The MAM is where CAPABILITIES live, as opposed to recipes -- the Dimensional Depot,
    the Power Augmenter, and Production Amplifier, which is the one that lets a
    Somersloop go into a machine at all.

    That last one has no flag in the save. `BP_UnlockSubsystem_C` records overclocking as
    `mIsBuildingOverclockUnlocked`, but nothing anywhere in the file records production
    amplification, so it is derived from the purchased-schematic set instead. Capability
    rows are marked LOCKS so it is obvious which research gates a tool argument rather
    than just adding a recipe.
    """
    try:
        st = _state(save, world)
    except Exception as exc:
        return f"could not read save: {exc}"

    g = st.game
    done = st.purchased_schematic_ids
    stock = st.stock()
    wanted = (status or "todo").strip().casefold()
    if wanted not in ("all", "todo", "affordable"):
        return f"! unknown status {status!r}. Choose from: all, todo, affordable"

    gates = {v: k for k, v in CAPABILITY_SCHEMATICS.items()}
    rows = []
    n_todo = 0
    for cls, s in sorted(g.schematics.items(), key=lambda kv: kv[1].name):
        if s.type != "EST_MAM":
            continue
        finished = cls in done
        if not finished:
            n_todo += 1
        if wanted != "all" and finished:
            continue
        if search and search.strip().casefold() not in (s.name or "").casefold():
            continue
        short = [(f, stock.get(f.item, 0.0)) for f in s.cost if stock.get(f.item, 0.0) < f.amount]
        if wanted == "affordable" and (short or finished):
            continue
        blocked = [
            g.schematics[d].name for d in s.dependencies if d in g.schematics and d not in done
        ]
        rows.append(
            (
                "DONE" if finished else ("BLOCKED" if blocked else ("short" if short else "READY")),
                s.name[:30],
                "LOCKS " + gates[cls] if cls in gates else "",
                ", ".join(f"{f.amount:g} {g.item_name(f.item)}" for f in s.cost)[:52] or "-",
                ", ".join(f"{f.amount - have:.0f} {g.item_name(f.item)}" for f, have in short)[:34],
                ", ".join(blocked)[:24],
            )
        )

    notes = [
        (
            "MAM research is where CAPABILITIES live, not just recipes -- 'LOCKS x' marks "
            "one that gates a feature of this MCP rather than adding a recipe"
        ),
        (
            "cost is checked against spendable stock only: carried, crates and the "
            "Dimensional Depot, never machine buffers"
        ),
    ]
    for name in CAPABILITY_SCHEMATICS:
        gate = st.research_gate(name)
        if gate is None:
            continue
        bill = ", ".join(f"{r['need']:g} {r['name']}" for r in gate["cost"])
        missing = ", ".join(f"{r['need'] - r['have']:.0f} {r['name']}" for r in gate["short"])
        verdict = "you can afford it now" if gate["affordable"] else f"short of {missing}"
        blocked = (
            " Blocked by " + ", ".join(gate["blocked_by"]) + " first." if gate["blocked_by"] else ""
        )
        notes.append(
            f"{name} is NOT researched: needs {gate['schematic_name']} in the MAM "
            f"({bill}) -- {verdict}.{blocked}"
        )

    return render.envelope(
        f"# {st.age_note}\n# {n_todo} MAM research node(s) outstanding, showing status={wanted}",
        render.table(
            ("status", "research", "capability", "cost", "short by", "blocked by"),
            rows[: render.clamp(limit, default=25)],
            total=len(rows),
            limit=limit,
        ),
        notes,
    )


@mcp.tool(structured_output=False)
def somersloops(save: str | None = None, world: str | None = None) -> str:
    """Somersloops held, slotted and owned -- the sibling of power_shards.

    `sloop_budget` has existed since sloops became spendable and nothing exposed it, so
    the only way to learn how many you had was to guess a `sloops=` budget and read the
    shortfall warning: you had to guess the budget to discover the budget.

    Free and committed are both exact. Slotted ones live in `InventoryPotential`, the same
    component as Power Shards, so this counts slot contents rather than inverting a boost
    multiplier.
    """
    try:
        st = _state(save, world)
    except Exception as exc:
        return f"could not read save: {exc}"

    budget = st.sloop_budget()
    gate = st.research_gate("production_boost")
    rows = [
        (
            h["name"],
            h["instance"][-18:],
            f"{h['sloops']:.0f}",
            f"{h['boost']:g}x" if h["boost"] else "",
        )
        for h in budget["holders"][:20]
    ]
    notes = []
    if gate is not None:
        bill = ", ".join(f"{r['need']:g} {r['name']}" for r in gate["cost"])
        notes.append(
            f"PRODUCTION AMPLIFIER IS NOT RESEARCHED, so none of these can go in a machine "
            f"yet. Research {gate['schematic_name']} in the MAM ({bill})"
        )
    notes.append(
        "only FREE sloops can fund a plan; committed ones are counted so you know there "
        "is something to pull out, not added to what is spendable"
    )
    notes.append(
        "free pools carried, crates and the Dimensional Depot -- the same set as "
        "power_shards, and never machine buffers"
    )
    if not budget["committed_measured"]:
        notes.append(
            "this projection predates schema 10, so slotted sloops are unreadable and "
            "'committed' is unknown rather than zero"
        )
    notes.append(
        "Mercer Spheres share the WAT prefix and do nothing for production, so they are "
        "reported apart and never added in"
    )
    return render.envelope(
        f"# {st.age_note}\n"
        + render.kv(
            [
                ("free", f"{budget['free']:.0f}"),
                ("committed", f"{budget['committed']:.0f}"),
                ("owned", f"{budget['owned']:.0f}"),
                ("where", ", ".join(f"{k} {v:.0f}" for k, v in budget["by_place"].items()) or "-"),
                ("mercer_spheres", f"{budget['mercer_spheres']:.0f}"),
            ]
        ),
        render.table(("building", "instance", "sloops", "boost"), rows),
        notes,
    )


@mcp.tool(structured_output=False)
def collected_from_world(
    group: Annotated[
        str | None,
        Field(description="list the individual actors of one group, e.g. 'somersloop'"),
    ] = None,
    save: str | None = None,
    world: str | None = None,
    limit: Limit = 25,
) -> str:
    """What has been picked up off the map: slugs, somersloops, spheres, looted crash sites.

    The world's collectibles are placed by the map and never written into a save, so nothing
    in a save says a slug *exists*. What it records is the negative -- which map-placed actors
    are **gone** -- and that list is therefore an exact record of what has been collected.

    Pass ``group`` to list the actors of one kind with the world-partition cell each was in.
    """
    try:
        st = _state(save, world)
    except Exception as exc:
        return f"could not read save: {exc}"
    removed = st.removed_actors(group)
    if "error" in removed:
        return removed["error"]

    notes = [
        (
            "collected, not remaining: this project ships no table of where every slug is, "
            "so these are absolute counts and not a fraction of a known total"
        ),
        "'dropped_pickup' is loot the player dropped and re-collected, not a map collectible",
    ]
    if removed.get("other"):
        notes.append(
            "'other' is classes no group matched, reported rather than dropped: "
            + ", ".join(f"{k} {v}" for k, v in list(removed["other"].items())[:6])
        )
    if not removed["total"]:
        notes.append(
            "nothing recorded as removed. On a projection older than schema 11 that means "
            "unreadable rather than none -- re-read the save"
        )

    body = [
        render.kv(
            [
                ("total_removed", str(removed["total"])),
                ("map_cells", str(removed["cells"])),
            ]
        ),
        render.table(
            ("group", "collected"),
            [(g, str(n)) for g, n in removed["groups"].items()],
        ),
    ]
    if group is not None:
        rows = [(a["actor"], a["cell"]) for a in removed["actors"][:limit]]
        body.append(
            render.table(("actor", "cell"), rows)
            + (
                f"\n({len(removed['actors'])} total, showing {len(rows)})"
                if len(removed["actors"]) > len(rows)
                else ""
            )
        )
    return render.envelope(f"# {st.age_note}\n" + body[0], "\n\n".join(body[1:]), notes)
