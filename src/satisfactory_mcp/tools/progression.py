"""Project Assembly phases and Power Shard budgeting.

These were spliced in under the resources banner during a parallel merge and
are tools, not resources -- both read the save and one takes a hypothetical."""

from __future__ import annotations

from .. import render
from ..app import Limit, _state, mcp
from ..docs.constants import max_clock, shards_for_clock


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
