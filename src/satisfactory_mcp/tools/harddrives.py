"""Hard-drive research: what is waiting to be picked, and which option to take.

Split from ``planning`` because the question is different in kind. The optimiser sizes a
factory you have decided to build; this ranks a one-off irreversible choice against the
factory you already have.
"""

from __future__ import annotations

from .. import render
from ..app import _state, mcp
from ..planning import advisor


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
        ["use advise_hard_drive_pick(hard_drive_id=N) to rank one drive's options"],
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
        ],
    )
