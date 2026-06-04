"""Recalling a stored plan: what its arguments were, and what this call overrode.

A stored plan is a stored planning REQUEST, so merging one back in is a planning
decision rather than an argument-parsing detail of the tool that happens to take it.
"""

from __future__ import annotations

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


def recall_plan(st, plan: str | None, supplied: dict) -> tuple[dict, str, list[str]]:
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
