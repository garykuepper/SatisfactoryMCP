"""Hard-drive research: what is waiting to be picked, and which option to take.

Split from ``planning`` because the question is different in kind. The optimiser sizes a
factory you have decided to build; this ranks a one-off choice against the factory you
already have.

**The choice is not irreversible, and saying it was made this advice worse.** Confirmed by
the player in-game (2026-07-28), closing OQ2: the option you do NOT take **returns to the
pool** and can be offered by a later drive. Only the drive is spent, never the schematic.

That inverts the advice. Picking is low-stakes, so the right move is to take whatever helps
the factory you have now and let the other alternate come back around -- there is nothing
to agonise over and no reason to hoard an unclaimed drive waiting for a better roll. The
old framing ("irreversible") argued for exactly the opposite behaviour.
"""

from __future__ import annotations

from ....domain.planning import advisor
from ....presenters.text import primitives as render
from ..app import _state, mcp

#: Said on every hard-drive response, because it is the fact that decides how hard to
#: think about the choice, and it is not visible anywhere in the game's own UI.
POOL_RULE = (
    "the option you do NOT pick is not lost -- it returns to the pool and a later drive "
    "can offer it again. Only the drive is spent, so pick what helps now"
)


@mcp.tool(structured_output=False)
def list_pending_hard_drive_choices(save: str | None = None, world: str | None = None) -> str:
    """The pending hard-drive choices stored in the save, with rerolls left."""
    try:
        st = _state(save, world)
    except Exception as exc:
        return f"could not read save: {exc}"
    offers = st.hard_drive_offers
    rows = []
    for o in offers:
        opts = []
        for opt in o.options:
            tag = opt["name"]
            if opt["slots"]:
                tag += f" (+{opt['slots']} slots)"
            elif not opt["recipes"]:
                tag += " (nothing new)"
            opts.append(tag)
        rows.append((o.hard_drive_id, o.rerolls_left, " | ".join(opts)))
    return render.envelope(
        f"# {st.age_note}\n"
        f"# {len(offers)} unclaimed hard drive(s), each a live choice; "
        f"{st.spare_hard_drives()} unanalysed drive(s) on hand",
        render.table(("id", "rerolls", "options"), rows),
        [
            "use advise_hard_drive_pick(hard_drive_id=N) to rank one drive's options",
            POOL_RULE,
            # Measured across all 25 offers on the reference save: every drive shows
            # exactly 2 options and starts with exactly 1 reroll, which matches
            # mNumSchematicsPerHardDrive and mNumRerollsPerHardDrive in the headers.
            (
                "each drive offers 2 options and allows 1 reroll; a reroll can re-serve "
                "an excluded schematic when the pool is thin, so it is never simply wasted"
            ),
        ],
    )


@mcp.tool(structured_output=False)
def advise_hard_drive_pick(
    hard_drive_id: int,
    sources: list[str] | None = None,
    save: str | None = None,
    world: str | None = None,
) -> str:
    """Rank one pending drive's options by marginal value, via counterfactual LP.

    Each option is solved for and against across several objectives, because a
    recipe can be worthless for power yet excellent for parts. Deltas are reported
    per objective and never collapsed into one score.

    ``sources`` is plan_factory's selector list and means the same thing here, so the
    baseline printed is the same quantity plan_factory reports for the same nodes.
    """
    try:
        st = _state(save, world)
    except Exception as exc:
        return f"could not read save: {exc}"

    try:
        results = advisor.advise_hard_drive(st, sources, hard_drive_id)
    except ValueError as exc:
        return str(exc)
    if not results:
        return f"no unclaimed hard drive with id {hard_drive_id}"
    res = results[0]

    rows_out = []
    for opt in res["options"]:
        d = opt["deltas"]
        rows_out.append(
            (
                opt["name"],
                render.num(d.get("net_mw")),
                render.num(d.get("mw_with_products")),
                render.num(d.get("min_machines_for_plastic")),
                f"{render.num(d.get('own_output_machines'))} ({opt.get('own_output_item') or '-'})",
                ", ".join(opt["new_recipes"]) or "-",
                "; ".join(opt["new_buildings"] + opt["blocked_by"] + opt["notes"]) or "",
            )
        )
    base = res["baseline"]
    return render.envelope(
        "\n".join(
            [
                f"# hard drive {res['hard_drive_id']}, rerolls left {res['rerolls_left']}",
                f"# {st.age_note}",
                f"# sources: {res['basket']}",
                "# baseline: " + render.kv([(k, render.num(v)) for k, v in base.items()]),
                f"# {res['baseline_note']}",
                f"# suggestion: {res['suggestion']}",
            ]
        ),
        render.table(
            (
                "option",
                "d_MW",
                "d_MW+products",
                "d_plastic_mach",
                "d_own_output_mach",
                "new recipes",
                "caveats",
            ),
            rows_out,
        ),
        [
            *res.get("selector_errors", []),
            *res.get("notes", []),
            "deltas are marginal value vs this world's current recipes",
            "a 0 delta means the player already has a route that dominates it",
            POOL_RULE,
        ],
    )
