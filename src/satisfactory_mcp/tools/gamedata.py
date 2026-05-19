"""Game-data lookups: items, recipes, alternates, buildings.

Read-only over the normalized dump. Nothing here touches a save."""

from __future__ import annotations

from .. import render
from ..app import Limit, _item_id, _state, game, mcp
from ..docs import search


@mcp.tool(structured_output=False)
def search_items(query: str, limit: Limit = 10, offset: int = 0) -> str:
    """Find items by name. Returns form, energy and sink points."""
    g = game()
    q = query.casefold()
    hits = sorted(
        (i for i in g.items.values() if q in i.name.casefold() and i.form != "RF_INVALID"),
        key=lambda i: (not i.name.casefold().startswith(q), i.name),
    )
    page = hits[offset : offset + render.clamp(limit)]
    rows = [
        (i.name, "fluid" if i.is_fluid else "solid", render.num(i.energy_mj), i.sink_points)
        for i in page
    ]
    body = render.table(
        ("item", "form", "MJ", "sink_pts"), rows, total=len(hits), offset=offset, limit=limit
    )
    footer = render.ids_footer((i.name, i.cls) for i in page)
    return render.envelope(f"# {len(hits)} item(s) matching {query!r}", body + "\n" + footer)


@mcp.tool(structured_output=False)
def recipe_detail(recipe_id: str) -> str:
    """Exact numbers for one recipe: rates, machine, power, unlock source."""
    g = game()
    r = g.recipes.get(recipe_id)
    if r is None:
        return f"unknown recipe {recipe_id!r} -- use search_recipes to find the id"
    b = g.machine(r)
    ing = render.flows((g.item_name(f.item), f.per_min, False) for f in r.ingredients)
    out = render.flows((g.item_name(f.item), f.per_min, False) for f in r.products)
    unlocks = [g.schematics[s].name for s in r.unlocked_by if s in g.schematics]
    lines = [
        f"{r.name}  ({'ALTERNATE' if r.is_alternate else r.kind})",
        render.kv(
            [
                ("machine", b.name if b else "-"),
                ("cycle", f"{render.num(r.duration_s)}s"),
                ("power", f"{render.num(g.recipe_power_mw(r))}MW"),
            ]
        ),
        f"in/min : {ing}",
        f"out/min: {out}",
        f"unlock : {', '.join(unlocks) or '-'}",
    ]
    if r.is_variable_power:
        lines.append(
            f"variable power: {render.num(r.power_min_mw)}-{render.num(r.power_max_mw)} MW"
        )
    return "\n".join(lines)


@mcp.tool(structured_output=False)
def alternates_for_item(
    item: str,
    save: str | None = None,
    include_locked: bool = True,
) -> str:
    """Every automatable recipe that makes an item, alternates first.

    When a save is readable, each row is marked HAVE or LOCKED.
    """
    g = game()
    iid = _item_id(item)
    if iid is None:
        return f"no item matching {item!r}"
    producers = g.producers_of(iid, "part")
    have: set[str] = set()
    try:
        have = _state(save).available_recipe_ids
    except Exception:
        pass
    producers.sort(key=lambda r: (not r.is_alternate, r.name))
    rows = []
    for r in producers:
        status = "HAVE" if r.cls in have else ("LOCKED" if have else "-")
        if not include_locked and status == "LOCKED":
            continue
        b = g.machine(r)
        rows.append(
            (
                r.name,
                f"{b.name} {render.num(g.recipe_power_mw(r))}MW" if b else "-",
                render.flows((g.item_name(f.item), f.per_min, False) for f in r.ingredients),
                render.flows((g.item_name(f.item), f.per_min, False) for f in r.products),
                status,
            )
        )
    n_alt = sum(1 for r in producers if r.is_alternate)
    body = render.table(("recipe", "building", "in/min", "out/min", "status"), rows)
    footer = render.ids_footer((r.name, r.cls) for r in producers)
    return render.envelope(
        f"# {len(rows)} automatable recipe(s) make {g.item_name(iid)} "
        f"({n_alt} alternate). rates=/min at 100% clock, one machine.",
        body + "\n" + footer,
    )


@mcp.tool(structured_output=False)
def search_recipes(
    query: str = "",
    consumes: str | None = None,
    produces: str | None = None,
    kind: str = "part",
    only_alternates: bool = False,
    include_events: bool = False,
    save: str | None = None,
    limit: Limit = 10,
    offset: int = 0,
) -> str:
    """Search recipes by name, or by what they consume/produce. Marks HAVE/LOCKED.

    ``consumes="Rubber"`` is the reverse lookup: every recipe that eats an item.
    ``kind`` is "part" (default), "building" (build-gun costs), "manual" or "all" --
    and the header counts EVERY kind over the whole recipe table whatever ``kind``
    is set to, so a part-only view still says how many buildings eat the item.
    """
    g = game()
    notes: list[str] = []
    consumes_id = produces_id = None
    if consumes:
        consumes_id = _item_id(consumes)
        if consumes_id is None:
            return f"no item matching {consumes!r}"
    if produces:
        produces_id = _item_id(produces)
        if produces_id is None:
            return f"no item matching {produces!r}"
    if consumes_id and produces_id:
        notes.append("consumes and produces are ANDed: this is the loop test, not a union")

    have: set[str] | None = None
    try:
        have = _state(save).available_recipe_ids
    except Exception:
        pass

    hits, census = search.search(
        g,
        query=query,
        consumes=consumes_id,
        produces=produces_id,
        kind=kind,
        only_alternates=only_alternates,
        include_events=include_events,
        unlocked=have,
    )
    subject = "matching " + repr(query) if query else "in the game"
    column = ""
    if consumes_id:
        subject = f"consume {g.item_name(consumes_id)}"
        column = f"uses {g.item_name(consumes_id)}"
    elif produces_id:
        subject = f"produce {g.item_name(produces_id)}"
        column = f"makes {g.item_name(produces_id)}"
    if query and (consumes_id or produces_id):
        subject += f" and match {query!r}"
    if only_alternates:
        subject += " (alternates only)"
    return search.render_search(
        g, hits, census, subject, column, limit=limit, offset=offset, kind=kind, notes=notes
    )


@mcp.tool(structured_output=False)
def list_buildings(kind: str = "production") -> str:
    """Buildings by kind: production, extractor, generator, logistics."""
    g = game()
    picks = []
    for b in g.buildings.values():
        if (
            kind == "production"
            and b.is_manufacturer
            or kind == "extractor"
            and b.is_extractor
            or kind == "generator"
            and b.is_generator
            or kind == "logistics"
            and (b.items_per_min or b.flow_m3_min)
        ):
            picks.append(b)
    picks.sort(key=lambda b: b.name)
    rows = []
    for b in picks:
        detail = ""
        if b.is_extractor and b.base_extract_rate:
            detail = f"{render.num(b.extract_rate('normal'))}/min @normal"
        elif b.is_generator:
            detail = f"{render.num(b.power_production_mw)}MW out"
            if b.requires_supplemental:
                detail += f", {render.num(b.supplemental_m3_min())} m3/min water"
        elif b.items_per_min:
            detail = f"{render.num(b.items_per_min)} items/min"
        elif b.flow_m3_min:
            detail = f"{render.num(b.flow_m3_min)} m3/min"
        fp = b.footprint
        rows.append(
            (
                b.name,
                f"{render.num(b.power_mw)}MW",
                render.num(b.max_clock),
                b.sloop_slots,
                str(fp) if fp else "-",
                fp.foundations if fp else "-",
                detail,
            )
        )

    notes = [
        (
            "size is the axis-aligned clearance box (WxDxH); 'found' is the 8m "
            "foundations one machine covers, ignoring edges shared with a neighbour, "
            "so a row of N machines needs somewhat fewer than N x found"
        )
    ]
    unknown = [b.name for b in picks if not b.footprint]
    if unknown:
        notes.append(
            f"no clearance data, so no size: {', '.join(sorted(unknown))}. "
            "plan_layout leaves these out of its space budget rather than guessing"
        )

    return render.envelope(
        f"# {len(rows)} {kind} building(s)",
        render.table(("building", "power", "max_clock", "sloops", "size", "found", "detail"), rows)
        + "\n"
        + render.ids_footer((b.name, b.cls) for b in picks),
        notes,
    )
