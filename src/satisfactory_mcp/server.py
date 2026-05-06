"""FastMCP server. Tool registration only -- all logic lives in the sibling modules.

Every tool passes ``structured_output=False``: a tool annotated ``-> str`` otherwise
gets an outputSchema AND has its whole payload echoed into ``structuredContent``, a
measured ~1.96x wire-size tax for no benefit.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated

from mcp.server.fastmcp import FastMCP
from pydantic import Field

from . import config, render
from .docs.loader import load_docs
from .docs.model import GameData
from .docs.normalize import normalize
from .planning import advisor
from .planning.diff import NEIGHBOUR_RADIUS_M as DIFF_NEIGHBOUR_M
from .planning.diff import build_diff
from .planning.layout import build_layout
from .planning.optimize import MW, free_lunch_audit, solve
from .planning.scenario import build_scenario, resolve_item
from .save import projection as proj
from .save.state import WorldState, load_state
from .spatial import geo
from .spatial import nodes as nodes_mod
from .spatial import ranking as ranking_mod
from .spatial import regions as regions_mod
from .spatial.select import SELECTOR_HELP, select_nodes

mcp = FastMCP("satisfactory")

Limit = Annotated[int, Field(default=10, ge=1, le=25, description="max rows (hard cap 25)")]


@lru_cache(maxsize=1)
def game() -> GameData:
    """Normalized game data. ~90 ms cold, so built once in-process, no disk cache."""
    return normalize(load_docs(config.docs_path()))


def _state(save: str | None = None, world: str | None = None) -> WorldState:
    return load_state(game(), path=save, world=world)


def _item_id(query: str) -> str | None:
    return resolve_item(game(), query)


# ============================================================ game data


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
    only_alternates: bool = False,
    limit: Limit = 10,
    offset: int = 0,
) -> str:
    """Search automatable recipes by name."""
    g = game()
    q = query.casefold()
    hits = [
        r
        for r in g.automatable()
        if (not q or q in r.name.casefold()) and (not only_alternates or r.is_alternate)
    ]
    hits.sort(key=lambda r: (not r.is_alternate, r.name))
    page = hits[offset : offset + render.clamp(limit)]
    rows = [
        (
            r.name,
            g.machine(r).name if g.machine(r) else "-",
            render.flows((g.item_name(f.item), f.per_min, False) for f in r.ingredients),
            render.flows((g.item_name(f.item), f.per_min, False) for f in r.products),
        )
        for r in page
    ]
    body = render.table(
        ("recipe", "building", "in/min", "out/min"),
        rows,
        total=len(hits),
        offset=offset,
        hint="or narrow the query.",
    )
    return render.envelope(
        f"# {len(hits)} recipe(s){' (alternates only)' if only_alternates else ''}",
        body + "\n" + render.ids_footer((r.name, r.cls) for r in page),
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
        rows.append(
            (b.name, f"{render.num(b.power_mw)}MW", render.num(b.max_clock), b.sloop_slots, detail)
        )
    return render.envelope(
        f"# {len(rows)} {kind} building(s)",
        render.table(("building", "power", "max_clock", "sloops", "detail"), rows)
        + "\n"
        + render.ids_footer((b.name, b.cls) for b in picks),
    )


# ============================================================ save state


@mcp.tool(structured_output=False)
def list_worlds() -> str:
    """List save games grouped by world, newest first.

    Unsupported files are reported separately rather than failing the scan --
    pre-1.0 saves cannot be parsed at all.
    """
    try:
        worlds, unsupported = proj.list_worlds()
    except Exception as exc:
        return f"could not scan saves: {exc}"
    if not worlds and not unsupported:
        return f"no saves found under {config.saves_root()}"
    rows = []
    for w in worlds:
        newest = w.newest
        rows.append(
            (
                w.session_name,
                len(w.saves),
                f"{w.max_play_duration_s / 3600:.0f}h",
                newest["filename"],
                newest["save_version"],
                w.world_id,
            )
        )
    notes = []
    if unsupported:
        reasons: dict[str, int] = {}
        for u in unsupported:
            reasons[u["reason"]] = reasons.get(u["reason"], 0) + 1
        notes.append(
            f"{len(unsupported)} file(s) unreadable: "
            + "; ".join(f"{n}x {r}" for r, n in reasons.items())
        )
    return render.envelope(
        f"# {len(worlds)} world(s), {sum(len(w.saves) for w in worlds)} readable save(s)",
        render.table(("world", "saves", "played", "newest", "saveVer", "world_id"), rows),
        notes,
    )


@mcp.tool(structured_output=False)
def world_summary(save: str | None = None, world: str | None = None) -> str:
    """Progress, power and problems for one world."""
    try:
        st = _state(save, world)
    except Exception as exc:
        return f"could not read save: {exc}"
    g = st.game
    p = st.progression()
    pw = st.power_report()
    notes = []
    unbuilt = st.unlocked_but_unbuilt()
    if unbuilt:
        notes.append("unlocked but never built: " + ", ".join(g.buildings[c].name for c in unbuilt))
    if st.misconfigured:
        kinds: dict[str, int] = {}
        for m in st.misconfigured:
            kinds[m["cls"]] = kinds.get(m["cls"], 0) + 1
        notes.append(
            "no recipe set: "
            + ", ".join(
                f"{n}x {g.buildings[c].name if c in g.buildings else c}" for c, n in kinds.items()
            )
        )
    if st.paused:
        notes.append(f"{len(st.paused)} building(s) paused by the player")
    gen_rows = [
        (v["name"], v["count"], render.num(v["mw"]))
        for v in sorted(pw["by_generator"].values(), key=lambda v: -v["mw"])
    ]
    return render.envelope(
        "\n".join(
            [
                f"# {st.age_note}",
                render.kv(
                    [
                        ("phase", p["game_phase"]),
                        ("target", p["target_phase"]),
                        ("tier_complete", p["highest_complete_tier"]),
                        ("recipes", p["available_recipes"]),
                        ("alternates", len(st.unlocked_alternates)),
                        ("hard_drives_pending", len(st.hard_drive_offers)),
                    ]
                ),
                render.kv(
                    [
                        ("power_gen_MW", render.num(pw["generation_mw"])),
                        ("draw_MW", render.num(pw["draw_mw"])),
                        ("headroom_MW", render.num(pw["headroom_mw"])),
                    ]
                ),
                "milestones/tier: "
                + " ".join(f"T{t}:{v}" for t, v in p["milestones_by_tier"].items()),
            ]
        ),
        render.table(("generator", "count", "MW"), gen_rows),
        notes,
    )


@mcp.tool(structured_output=False)
def unlocked_recipes(
    save: str | None = None,
    world: str | None = None,
    only_alternates: bool = True,
    limit: Limit = 25,
) -> str:
    """Which recipes this world has. Defaults to alternates, never all 872."""
    try:
        st = _state(save, world)
    except Exception as exc:
        return f"could not read save: {exc}"
    picks = st.unlocked_alternates if only_alternates else st.unlocked_recipes("part")
    picks = sorted(picks, key=lambda r: r.name)
    page = picks[: render.clamp(limit, default=25)]
    rows = [(r.name, st.game.machine(r).name if st.game.machine(r) else "-") for r in page]
    return render.envelope(
        f"# {st.age_note}\n"
        f"# {len(st.unlocked_alternates)} of {len(st.game.alternates())} alternates unlocked; "
        f"{len(st.unlocked_recipes('part'))} automatable recipes total",
        render.table(("recipe", "building"), rows, total=len(picks), limit=limit),
    )


@mcp.tool(structured_output=False)
def power_report(save: str | None = None, world: str | None = None) -> str:
    """Nameplate generation vs machine draw. Excludes paused buildings."""
    try:
        st = _state(save, world)
    except Exception as exc:
        return f"could not read save: {exc}"
    pw = st.power_report()
    rows = [
        (v["name"], v["count"], render.num(v["mw"]))
        for v in sorted(pw["by_generator"].values(), key=lambda v: -v["mw"])
    ]
    notes = ["nameplate only: fuel supply and uptime are not modelled"]
    if pw["unmodellable"]:
        notes.append(f"not in game data, excluded: {', '.join(pw['unmodellable'])}")
    return render.envelope(
        f"# {st.age_note}\n"
        + render.kv(
            [
                ("generation_MW", render.num(pw["generation_mw"])),
                ("draw_MW", render.num(pw["draw_mw"])),
                ("headroom_MW", render.num(pw["headroom_mw"])),
                ("paused", pw["paused_count"]),
            ]
        ),
        render.table(("generator", "count", "MW"), rows),
        notes,
    )


@mcp.tool(structured_output=False)
def factory_sites(save: str | None = None, world: str | None = None, limit: Limit = 10) -> str:
    """Built production buildings clustered into sites, largest first."""
    try:
        st = _state(save, world)
    except Exception as exc:
        return f"could not read save: {exc}"
    g = st.game
    sites = st.sites()
    rows = []
    for s in sites[: render.clamp(limit)]:
        top = sorted(s["buildings"].items(), key=lambda kv: -kv[1])[:4]
        rows.append(
            (
                s["direction"],
                s["grid"],
                f"{int(s['centroid'][0] / 100)},{int(s['centroid'][1] / 100)}",
                s["count"],
                f"{s['diameter_m']}m",
                ", ".join(f"{n}x {g.buildings[c].name if c in g.buildings else c}" for c, n in top),
            )
        )
    return render.envelope(
        f"# {st.age_note}\n# {len(sites)} site(s); coords in metres",
        render.table(
            ("dir", "grid", "x,y(m)", "buildings", "spread", "contents"),
            rows,
            total=len(sites),
            limit=limit,
        ),
    )


# ============================================================ spatial


@mcp.tool(structured_output=False)
def list_regions(with_resource: str | None = None) -> str:
    """Named map regions, optionally only those containing a given resource.

    Region names are ADVISORY: boundaries come from a hand-derived 256 m raster, since
    the game ships no biome geometry. Use them to talk about places, not to compute
    with -- every node row also carries an exact grid cell.
    """
    g = game()
    rm = regions_mod.load_regions()
    table = nodes_mod.load_nodes()
    rid = _item_id(with_resource) if with_resource else None
    if with_resource and rid is None:
        return f"no resource matching {with_resource!r}"

    pool = table.by_resource(rid) if rid else table.nodes
    rows = []
    for name in rm.names():
        info = rm.summary(name)
        hits = rm.filter_nodes(pool, name)
        if rid and not hits:
            continue
        cx, cy = info["centroid"]
        rows.append(
            (
                name,
                info["direction"],
                info["grid"],
                f"{int(cx / 100)},{int(cy / 100)}",
                render.num(info["area_km2"]),
                len(hits),
            )
        )
    rows.sort(key=lambda r: -r[5])
    scope = f" containing {g.item_name(rid)}" if rid else ""
    return render.envelope(
        f"# {len(rows)} region(s){scope}; centroid in metres",
        render.table(
            ("region", "dir", "grid", "centroid(m)", "km2", "nodes"), rows, total=len(rows)
        ),
        [
            f"names are advisory, ~{rm.meta.get('accuracy_m', 256)}m boundary accuracy",
            "any region name works as a source selector for plan_factory",
        ],
    )


@mcp.tool(structured_output=False)
def describe_location(x_m: float, y_m: float) -> str:
    """Name the region at a coordinate, with confidence.

    Returns 'off-map or ocean' rather than guessing the nearest land region.
    """
    rm = regions_mod.load_regions()
    x, y = x_m * 100, y_m * 100
    label = rm.label_for(x, y)
    return render.envelope(
        render.kv(
            [
                ("region", label.describe()),
                ("confidence", label.confidence),
                ("grid", geo.grid_cell(x, y)),
                ("direction_from_centre", geo.direction_of(x, y)),
                ("bearing_deg", render.num(geo.bearing_deg(x, y))),
            ]
        )
    )


@mcp.tool(structured_output=False)
def search_resource_nodes(
    sources: list[str] | None = None,
    resource: str | None = None,
    purity: str | None = None,
    kind: str | None = None,
    only_free: bool = False,
    group: str = "field",
    save: str | None = None,
    world: str | None = None,
    limit: Limit = 25,
) -> str:
    """Search resource nodes by region, radius, grid cell, direction, type or id.

    ``sources`` is a list of selectors; locations union, filters intersect::

        ["north"]                          northern half of the map
        ["region:Northern Forest"]         one named region
        ["near:0,-2000,800"]               within 800 m of (0, -2000) metres
        ["grid:X3Y4"]                      one 1.024 km grid cell
        ["node:BP_ResourceNode26_99"]      one specific node
        ["north", "resource:Crude Oil"]    crude oil in the north
        ["bbox:-500,-2500,600,-1800"]      a rectangle, metres

    ``group`` is "field" (200 m clusters, default) or "node" (one row per node, with
    ids you can feed straight back in as sources).
    """
    g = game()
    table = nodes_mod.load_nodes()

    spec = list(sources or [])
    for extra, value in (("resource", resource), ("purity", purity), ("kind", kind)):
        if value:
            spec.append(f"{extra}:{value}")

    st = None
    try:
        st = _state(save, world)
    except Exception:
        pass

    sel = select_nodes(spec or None, table.nodes, resolve_resource=_item_id)
    if sel.errors and not sel.nodes:
        return render.envelope("# no nodes selected", "", [*sel.errors, SELECTOR_HELP])

    rows_all = nodes_mod.annotate(
        sel.nodes,
        g,
        st.projection if st else None,
        st.unlocked_building_ids if st else None,
    )
    if only_free:
        rows_all = [r for r in rows_all if not r["tapped"]]
    if not rows_all:
        return render.envelope(
            f"# no nodes in {sel.description}",
            "",
            sel.errors or ["try widening the selector"],
        )

    rm = regions_mod.load_regions()
    resources = sorted({r["resource"] for r in rows_all})
    mixed = len(resources) > 1
    unit = "mixed" if mixed else ("m3/min" if g.items[resources[0]].is_fluid else "/min")
    total = sum(r["rate"] for r in rows_all)
    free = nodes_mod.capacity(rows_all, only_free=True)
    locked_rate = sum(r["rate"] for r in rows_all if not r["reachable"])

    notes = list(sel.errors)
    if st is None:
        notes.append("no save read: tapped/free unknown, everything shown as free")
    else:
        if locked_rate:
            notes.append(
                f"{render.num(locked_rate)} excluded from free: needs an extractor this "
                "world has not unlocked (marked LOCKED)"
            )
        unres = nodes_mod.unresolved_extractors(st.projection)
        if unres:
            notes.append(
                f"{len(unres)} extractor(s) unmatched to a node (mostly water pumps), "
                "so free may be overstated"
            )

    if group == "node":
        rows_all.sort(key=lambda r: (-r["rate"], r["instance"]))
        rows = [
            (
                r["instance"].rsplit(".", 1)[-1],
                g.item_name(r["resource"]) if mixed else r["purity"],
                r["purity"] if mixed else r["kind"],
                r["grid"],
                f"{int(r['x'] / 100)},{int(r['y'] / 100)}",
                render.num(r["rate"]),
                "tapped" if r["tapped"] else ("LOCKED" if not r["reachable"] else "free"),
                rm.label_for_node(r).name or "-",
            )
            for r in rows_all[: render.clamp(limit, default=25)]
        ]
        headers = (
            "node_id",
            "resource" if mixed else "purity",
            "purity" if mixed else "kind",
            "grid",
            "x,y(m)",
            "rate",
            "status",
            "region",
        )
        body = render.table(headers, rows, total=len(rows_all), limit=limit)
        notes.append("node_id doubles as a source selector: node:<id>")
    else:
        clusters = geo.cluster(rows_all, link_m=200.0)
        crows = []
        for c in clusters[: render.clamp(limit, default=25)]:
            cx, cy, _cz = c.centroid
            label = rm.label_for(cx, cy)
            c_free = sum(m["rate"] for m in c.members if not m["tapped"] and m["reachable"])
            crows.append(
                (
                    label.name or "ocean/off-map",
                    geo.grid_cell(cx, cy),
                    geo.direction_of(cx, cy),
                    f"{int(cx / 100)},{int(cy / 100)}",
                    c.size,
                    ",".join(f"{n}{k[0]}" for k, n in sorted(c.purities().items())),
                    render.num(sum(m["rate"] for m in c.members)),
                    render.num(c_free),
                    f"{c.diameter_m:.0f}m",
                    "" if all(m["reachable"] for m in c.members) else "LOCKED",
                )
            )
        headers = (
            "region",
            "grid",
            "dir",
            "centre(m)",
            "n",
            "purity",
            "total",
            "free",
            "spread",
            "note",
        )
        body = render.table(headers, crows, total=len(clusters), limit=limit)
        notes.append('group="node" lists individual nodes with reusable ids')

    return render.envelope(
        f"# {sel.description}: {len(rows_all)} node(s), "
        f"{render.num(total)} {unit} total, {render.num(free)} free and reachable\n"
        f"# rates at 100% clock; coords in metres",
        body,
        notes,
    )


@mcp.tool(structured_output=False)
def rank_build_sites(
    resource: str,
    sources: list[str] | None = None,
    top: int = 5,
    save: str | None = None,
    world: str | None = None,
) -> str:
    """Rank candidate fields for a new extraction site, best first.

    Scores untapped REACHABLE capacity against spread, distance to your existing
    buildings, and purity mix. Every raw component is shown so you can re-weight:
    the single score is a starting point, not a verdict.

    ``sources`` narrows the search area using the same selectors as
    search_resource_nodes; omit it to search the whole map.
    """
    g = game()
    rid = _item_id(resource)
    if rid is None:
        return f"no resource matching {resource!r}"
    table = nodes_mod.load_nodes()

    try:
        st = _state(save, world)
    except Exception as exc:
        return (
            f"could not read save: {exc} (site ranking needs a save to know what is already built)"
        )

    spec = [*(sources or []), f"resource:{rid}"]
    sel = select_nodes(spec, table.nodes, resolve_resource=_item_id)
    if sel.errors and not sel.nodes:
        return render.envelope("# no candidates", "", [*sel.errors, SELECTOR_HELP])

    rows = nodes_mod.annotate(sel.nodes, g, st.projection, st.unlocked_building_ids)
    clusters = geo.cluster(rows, link_m=200.0)
    scored = ranking_mod.rank_sites(
        clusters,
        infra=st.infra_points(),
        consumer_z=st.consumer_z(),
    )
    if not scored:
        return render.envelope(
            f"# no untapped {g.item_name(rid)} in {sel.description}",
            "",
            [
                "every reachable node here already has an extractor",
                *sel.errors,
            ],
        )

    rm = regions_mod.load_regions()
    unit = "m3/min" if g.items[rid].is_fluid else "/min"
    out_rows = []
    for sc in scored[: render.clamp(top, default=5)]:
        cx, cy, _cz = sc.centroid
        raw = sc.raw
        alt = raw["altitude_vs_consumer_m"]
        out_rows.append(
            (
                render.num(sc.score),
                rm.label_for(cx, cy).name or "ocean/off-map",
                geo.grid_cell(cx, cy),
                f"{int(cx / 100)},{int(cy / 100)}",
                raw["nodes"],
                render.num(raw["untapped_rate"]),
                f"{render.num(raw['spread_m'])}m",
                "-"
                if raw["distance_to_infra_m"] is None
                else f"{render.num(raw['distance_to_infra_m'])}m",
                render.num(raw["purity_quality"]),
                "-" if alt is None else f"{alt:+.0f}m",
            )
        )

    notes = [*sel.errors]
    notes.append(
        "weights: throughput 1.00, spread -0.35, distance -0.25, purity +0.20 "
        "(min-max normalised across these candidates only)"
    )
    notes.append(
        "alt is the field's height above your refineries: POSITIVE means fluid flows "
        "downhill to them and needs no pipeline pumps"
    )
    if st.consumer_z() is None:
        notes.append("no refineries found, so altitude is not shown")

    return render.envelope(
        f"# {len(scored)} candidate {g.item_name(rid)} field(s) in {sel.description}, "
        f"untapped and reachable only\n"
        f"# {st.age_note}\n# rates {unit} at 100% clock; coords in metres",
        render.table(
            (
                "score",
                "region",
                "grid",
                "centre(m)",
                "n",
                "untapped",
                "spread",
                "to_infra",
                "purity",
                "alt",
            ),
            out_rows,
            total=len(scored),
            limit=top,
        ),
        notes,
    )


# ============================================================ planning


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
    machine_cost_mw: float = 5.0,
    save: str | None = None,
    world: str | None = None,
    limit: Limit = 15,
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

    ``clocks`` is only for asking a different question: passing [0.5, 1.0] lets the
    solver SPREAD throughput over more machines to save power, which is real but not
    free, so each machine is priced at ``machine_cost_mw`` (default 5 MW, just above
    the 2.58 MW/machine that trade was measured to be worth). Overclock modes are not
    offered by default because they consume Power Shards, which nothing here counts.

    objective: max_mw | max_item | min_raw | min_machines | min_power.
    Every item is balanced as an EQUALITY, so a byproduct with no consumer makes the
    plan infeasible rather than silently vanishing. ``exports`` is the whitelist of
    what may leave; default is power only, which is often infeasible for crude oil.
    """
    g = game()
    try:
        st = _state(save, world)
    except Exception as exc:
        return f"could not read save: {exc}"

    req = build_scenario(
        g,
        st,
        objective=objective,
        target_item=target_item,
        sources=sources,
        exports=exports,
        export_minimums=export_minimums,
        only_free_nodes=only_free_nodes,
        allow_sinks=allow_sinks,
        clocks=clocks,
        machine_cost_mw=machine_cost_mw,
    )
    sel, sc = req.selection, req.scenario
    if sel.errors and not sel.nodes:
        return render.envelope("# no sources selected", "", [*sel.errors, SELECTOR_HELP])

    audit_ok, audit_val = free_lunch_audit(sc)
    sol = solve(sc)
    if not sol.ok:
        return render.envelope(
            f"# INFEASIBLE ({objective})",
            "",
            [
                *sol.warnings,
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
    notes = [*sel.errors, *sol.warnings]
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
    top_flows = [e for e in sol.logistics if e["rate"] > 0][:6]
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
    belt_tier: str = "Mk5",
    pipe_tier: str = "Mk2",
    save: str | None = None,
    world: str | None = None,
    limit: Limit = 20,
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

    req = build_scenario(
        g,
        st,
        objective=objective,
        target_item=target_item,
        sources=sources,
        exports=exports,
        export_minimums=export_minimums,
        only_free_nodes=only_free_nodes,
        allow_sinks=allow_sinks,
        belt_ipm=belt_ipm,
        pipe_m3min=pipe_m3min,
    )
    sel, sc = req.selection, req.scenario
    if sel.errors and not sel.nodes:
        return render.envelope("# no sources selected", "", [*sel.errors, SELECTOR_HELP])

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

    return render.envelope(summary, body, notes)


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
    machine_cost_mw: float = 5.0,
    save: str | None = None,
    world: str | None = None,
    limit: Limit = 20,
    show_cost: bool = True,
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

    req = build_scenario(
        g,
        st,
        objective=objective,
        target_item=target_item,
        sources=sources,
        exports=exports,
        export_minimums=export_minimums,
        only_free_nodes=only_free_nodes,
        allow_sinks=allow_sinks,
        clocks=clocks,
        machine_cost_mw=machine_cost_mw,
    )
    sel = req.selection
    if sel.errors and not sel.nodes:
        return render.envelope("# no sources selected", "", [*sel.errors, SELECTOR_HELP])

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

    rep = build_diff(g, st, sol, req)
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

    return render.envelope(summary, "\n".join(parts), notes)


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
    """
    g = game()
    try:
        st = _state(save, world)
    except Exception as exc:
        return f"could not read save: {exc}"

    table = nodes_mod.load_nodes()
    sel = select_nodes(sources, table.nodes, resolve_resource=_item_id)
    rows = nodes_mod.annotate(sel.nodes, g, st.projection, st.unlocked_building_ids)
    caps: dict[str, float] = {}
    for r in rows:
        if r["kind"] == "node" and r["rate"] > 0 and r["reachable"]:
            caps[r["resource"]] = caps.get(r["resource"], 0.0) + r["rate"]
    caps["Desc_Water_C"] = 24000.0

    try:
        results = advisor.advise_hard_drive(st, caps, hard_drive_id)
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
                f"# resource basket: {sel.description} at 100% clock",
                "# baseline: " + render.kv([(k, render.num(v)) for k, v in base.items()]),
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
            "deltas are marginal value vs this world's current recipes",
            "a 0 delta means the player already has a route that dominates it",
        ],
    )


# ============================================================ resources
#
# Resources are CLIENT-PULLED, so they cost zero context until something asks for
# them. That makes them right for stable orientation data and wrong for anything
# parameterised, which stays a tool.


@mcp.resource("satisfactory://docs/summary", mime_type="text/plain")
def docs_summary() -> str:
    """One-line census of the normalized game data, plus its content hash."""
    g = game()
    kinds: dict[str, int] = {}
    for r in g.recipes.values():
        kinds[r.kind] = kinds.get(r.kind, 0) + 1
    return render.kv(
        [
            ("items", len(g.items)),
            ("fluids", sum(1 for i in g.items.values() if i.is_fluid)),
            ("recipes", len(g.recipes)),
            ("automatable", kinds.get("part", 0)),
            ("alternates", len(g.alternates())),
            ("buildings", len(g.buildings)),
            ("schematics", len(g.schematics)),
            ("docs_sha256", g.docs_sha256[:16]),
            ("warnings", len(g.warnings)),
        ]
    )


@mcp.resource("satisfactory://save/current", mime_type="text/plain")
def current_save() -> str:
    """Which world and file the server would read right now, and its headline state."""
    try:
        st = _state()
    except Exception as exc:
        return f"no readable save: {exc}"
    p = st.progression()
    return render.kv(
        [
            ("file", st.header.get("filename")),
            ("world", st.header.get("session_name")),
            ("played_h", int((st.header.get("play_duration_s") or 0) / 3600)),
            ("save_version", st.header.get("save_version")),
            ("phase", p["game_phase"]),
            ("tier_complete", p["highest_complete_tier"]),
            ("recipes", p["available_recipes"]),
            ("alternates", len(st.unlocked_alternates)),
            ("hard_drives_pending", len(st.hard_drive_offers)),
        ]
    )


@mcp.resource("satisfactory://map/regions", mime_type="text/plain")
def map_regions() -> str:
    """Region names available as source selectors, with their accuracy caveat."""
    rm = regions_mod.load_regions()
    return (
        f"# {len(rm.names())} regions, advisory names, ~{rm.meta.get('accuracy_m', 256)}m "
        "boundary accuracy\n" + "\n".join(rm.names())
    )


# ============================================================ prompts
#
# Prompts also cost nothing until invoked, and they surface as slash commands. They
# are where multi-step PROCEDURE lives, which keeps tool descriptions to one line and
# the always-resident schema small.


@mcp.prompt(title="Design a factory")
def design_factory(target_item: str, rate_per_min: str = "300") -> str:
    """Plan a factory for a target item, respecting what this world has unlocked."""
    return (
        f"Design a factory producing {rate_per_min}/min of {target_item} in my current "
        "Satisfactory world.\n\n"
        "Work in this order:\n"
        f"1. alternates_for_item('{target_item}') to see every route and which I HAVE.\n"
        "2. world_summary() for tier, power headroom, and anything unlocked but never built.\n"
        f"3. plan_factory(objective='min_machines', target_item='{target_item}', "
        f"exports=['{target_item}'], export_minimums={{'{target_item}': {rate_per_min}}}) "
        "to get the real machine counts.\n"
        "4. If it comes back INFEASIBLE, that usually means a byproduct has no consumer. "
        "Identify it and either add it to exports or find a recipe that consumes it, then "
        "re-solve.\n\n"
        "Then tell me: the machine list, total power draw, raw inputs per minute, anything "
        "I must build first, and any byproduct needing an outlet. Flag it explicitly if the "
        "plan depends on exporting or sinking something."
    )


@mcp.prompt(title="Plan a power plant")
def plan_power_plant(fuel_resource: str = "Crude Oil", sources: str = "north") -> str:
    """Plan a power plant from a given resource and area."""
    return (
        f"Plan a power plant burning {fuel_resource} in my Satisfactory world, using "
        f"sources: {sources}.\n\n"
        f"1. search_resource_nodes(sources=['{sources}', 'resource:{fuel_resource}']) to see "
        "what is there and what is already tapped. Note that free capacity far from my "
        "existing base is not the same as usable capacity.\n"
        f"2. rank_build_sites('{fuel_resource}', sources=['{sources}']) if I need a new site.\n"
        f"3. plan_factory(objective='max_mw', sources=['{sources}'], exports=['MW']).\n"
        "4. If that abandons the resource or comes back infeasible, the byproducts have "
        "nowhere to go. Retry with exports=['MW','Plastic','Rubber'] and say plainly that "
        "the plant only works if those leave the site.\n\n"
        "Report net MW, the machine list, water demand, pipes and belts needed, what I must "
        "build first, and every binding constraint."
    )


@mcp.prompt(title="Which hard drive recipe?")
def pick_hard_drive(hard_drive_id: str = "") -> str:
    """Advise which alternate recipe to take from a pending hard drive."""
    which = (
        f"hard drive {hard_drive_id}"
        if hard_drive_id
        else "each pending hard drive worth deciding now"
    )
    return (
        f"Help me choose the alternate recipe for {which} in my Satisfactory world.\n\n"
        "1. list_pending_hard_drive_choices() to see the live offers and rerolls left.\n"
        "2. advise_hard_drive_pick(hard_drive_id=N) for the marginal value of each option.\n\n"
        "Read the deltas carefully. A 0 in d_MW means I already own a route that dominates "
        "it for power, NOT that the recipe is bad -- check d_own_output_mach, which measures "
        "it on what it actually makes. Tell me which to take and why, name the tradeoff "
        "rather than hiding it behind one score, and mention any new building type I would "
        "have to unlock or build."
    )


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
