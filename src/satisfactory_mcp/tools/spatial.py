"""Map queries: regions, coordinates, resource nodes, build sites, map links."""

from __future__ import annotations

from typing import Annotated

from pydantic import Field

from .. import render
from ..app import (
    Limit,
    _item_id,
    _origin_for,
    _player_xy,
    _state,
    game,
    mcp,
)
from ..spatial import elevation, geo
from ..spatial import nodes as nodes_mod
from ..spatial import ranking as ranking_mod
from ..spatial import regions as regions_mod
from ..spatial.select import SELECTOR_HELP, select_nodes


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
def describe_location(
    x_m: float,
    y_m: float,
    radius_m: Annotated[
        float, Field(description="how far to look for known elevations, metres")
    ] = 200.0,
    save: str | None = None,
    world: str | None = None,
) -> str:
    """Name the region at a coordinate, with confidence, and sample its elevation.

    Returns 'off-map or ocean' rather than guessing the nearest land region.

    There is no heightmap in any data this reads, so elevation is a SAMPLE and is
    reported with its count and spread rather than as one invented number. Resource
    nodes rest on terrain and are quoted as ground; foundations and buildings are quoted
    separately as built elevation, because a platform is wherever the player put it.
    Where the two disagree, the difference is the fill already stacked there.
    """
    rm = regions_mod.load_regions()
    x, y = x_m * 100, y_m * 100
    label = rm.label_for(x, y)

    # The node table alone covers the whole map and needs no save, so an unexplored
    # coordinate still gets an answer. A readable save adds the dense sources.
    st = None
    try:
        st = _state(save, world)
    except Exception:
        pass
    table = nodes_mod.load_nodes()
    near = elevation.probe(x, y, elevation.sample_points(table, st), radius_m)

    fields = [
        ("region", label.describe()),
        ("confidence", label.confidence),
        ("grid", geo.grid_cell(x, y)),
        ("direction_from_centre", geo.direction_of(x, y)),
        ("bearing_deg", render.num(geo.bearing_deg(x, y))),
    ]
    notes: list[str] = []
    if near.samples:
        for what, values in (("ground", near.ground), ("built", near.built)):
            if not values:
                continue
            mid = values[len(values) // 2]
            fields.append(
                (
                    f"{what}_elevation_m",
                    f"{mid:.0f} (median of {len(values)}, {min(values):.0f}..{max(values):.0f})",
                )
            )
        fields.append(
            (
                "samples",
                ", ".join(f"{n} {src}" for src, n in sorted(near.counts.items()))
                + f" within {radius_m:g}m",
            )
        )
        fill = near.fill_m
        if fill is not None and abs(fill) >= 1.0:
            notes.append(
                f"built surface sits {fill:+.0f}m relative to the nearest ground samples "
                "-- that gap is foundation already stacked here, not terrain"
            )
        notes.append(
            "elevation is SAMPLED, not a heightmap: there is no terrain data in the dump "
            "or the save. Resource nodes rest on the ground; foundations and buildings "
            "are wherever they were placed"
        )
    else:
        notes.append(
            f"no known elevation within {radius_m:g}m. Nothing is built here and no "
            "resource node is near, so the height is genuinely unknown -- widen radius_m "
            "or accept that this is unsurveyed ground"
        )
    # Ground elevation here IS node z, so a stale node row is a stale ground level -- and
    # it can flip the 1 m threshold the fill note above is quoted at. Only the nodes inside
    # the probe radius are in scope, so an untouched location stays silent.
    notes += nodes_mod.position_notes(
        nodes_mod.skew_for_save(st.header if st else None, table),
        [n["instance"] for n in table.filter(center=(x, y), radius_m=radius_m)],
    )
    return render.envelope(render.kv(fields), "", notes)


@mcp.tool(structured_output=False)
def search_resource_nodes(
    sources: list[str] | None = None,
    resource: str | None = None,
    purity: str | None = None,
    kind: str | None = None,
    only_free: bool = False,
    mode: Annotated[str, Field(description="fields | nodes | nearest")] = "fields",
    near: Annotated[
        str | None,
        Field(description="origin for mode=nearest: 'x,y' in metres, 'me', or a factory name"),
    ] = None,
    group: Annotated[str | None, Field(description="deprecated alias for mode")] = None,
    save: str | None = None,
    world: str | None = None,
    limit: Limit = 25,
) -> str:
    """Resource nodes, in one of three modes.

    - **fields** (default) clusters nodes within 200 m and ranks by yield -- "where is
      there a lot of iron".
    - **nodes** lists one row per node, ranked by yield, with ids reusable as selectors.
    - **nearest** lists one row per node ranked by DISTANCE from `near`, with the
      distance shown -- "what is closest". Requires `near`.

    `sources` is a list of selectors; locations union, filters intersect::

        ["north"]                          northern half of the map
        ["region:Northern Forest"]         one named region
        ["near:0,-2000,800"]               within 800 m of (0, -2000) metres
        ["grid:X3Y4"]                      one 1.024 km grid cell
        ["node:BP_ResourceNode26_99"]      one specific node
        ["north", "resource:Crude Oil"]    crude oil in the north
        ["bbox:-500,-2500,600,-1800"]      a rectangle, metres

    `near` accepts a coordinate in metres, `me` for the player, or the name of a
    labelled factory -- "the nearest free coal to the coal powerplant" needs no
    coordinates. Giving `near` in any mode adds a distance column.
    """
    g = game()
    table = nodes_mod.load_nodes()

    # `group` predates `mode` and meant the same thing. Accepted rather than broken,
    # since a stored call using it should keep working.
    mode = (group or mode or "fields").strip().casefold()
    mode = {"field": "fields", "node": "nodes"}.get(mode, mode)
    if mode not in ("fields", "nodes", "nearest"):
        return f"! unknown mode {mode!r}. Choose from: fields, nodes, nearest"

    spec = list(sources or [])
    for extra, value in (("resource", resource), ("purity", purity), ("kind", kind)):
        if value:
            spec.append(f"{extra}:{value}")

    st = None
    try:
        st = _state(save, world)
    except Exception:
        pass

    origin = None
    where = ""
    if near:
        try:
            origin, where = _origin_for(st, near)
        except ValueError as exc:
            return f"! {exc}"
    if mode == "nearest" and origin is None:
        return "! mode='nearest' needs near=<x,y | me | factory name> to measure from"

    sel = select_nodes(spec or None, table.nodes, resolve_resource=_item_id, player=_player_xy(st))
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
    if origin is not None:
        for r in rows_all:
            r["_d"] = geo.distance_m((r["x"], r["y"]), origin)
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
        # This tool quotes z per node AND joins the save by instance name, so both halves
        # of a stale table bite here. Scoped to the rows in THIS answer: a query that
        # returns none of the drifted rows says nothing at all.
        notes += nodes_mod.skew_notes(
            nodes_mod.skew_for_save(st.header, table),
            [r["instance"] for r in rows_all],
        )

    if mode in ("nodes", "nearest"):
        if mode == "nearest":
            rows_all.sort(key=lambda r: r["_d"])
        else:
            rows_all.sort(key=lambda r: (-r["rate"], r["instance"]))
        show_distance = origin is not None
        rows = [
            (
                r["instance"].rsplit(".", 1)[-1],
                g.item_name(r["resource"]) if mixed else r["purity"],
                *(
                    (f"{r['_d']:.0f}m",)
                    if show_distance
                    else (r["purity"] if mixed else r["kind"],)
                ),
                r["grid"],
                f"{int(r['x'] / 100)},{int(r['y'] / 100)}",
                f"{r['z'] / 100:.0f}",
                render.num(r["rate"]),
                "tapped" if r["tapped"] else ("LOCKED" if not r["reachable"] else "free"),
                rm.label_for_node(r).name or "-",
            )
            for r in rows_all[: render.clamp(limit, default=25)]
        ]
        headers = (
            "node_id",
            "resource" if mixed else "purity",
            f"dist to {where}" if show_distance else ("purity" if mixed else "kind"),
            "grid",
            "x,y(m)",
            "z(m)",
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
        notes.append('mode="nodes" lists individual nodes; mode="nearest" ranks by distance')

    # Elevation matters for fluids and nothing else: a pipe running downhill is free and
    # one running uphill needs head. The SPAN is reported, never a pump count -- head per
    # pump is a game rule this project has no data for, and guessing it would be the kind
    # of invented number the rest of this file exists to avoid.
    zs = [r["z"] / 100.0 for r in rows_all if "z" in r]
    head = ""
    if zs and any(g.items[r].is_fluid for r in resources if r in g.items):
        low, high = min(zs), max(zs)
        head = (
            f"\n# elevation {low:.0f}..{high:.0f}m (span {high - low:.0f}m); fluid, so "
            "uphill runs need pumps and downhill runs do not"
        )

    return render.envelope(
        f"# {sel.description}: {len(rows_all)} node(s), "
        f"{render.num(total)} {unit} total, {render.num(free)} free and reachable\n"
        f"# rates at 100% clock; coords in metres{head}",
        body,
        notes,
    )


@mcp.tool(structured_output=False)
def show_on_map(
    target: Annotated[
        str,
        Field(
            description="'x,y' in metres, 'me', a factory label, a node id, or a "
            "resource name like 'Crude Oil'"
        ),
    ],
    layers: Annotated[
        list[str] | None,
        Field(description="explicit sublayer tokens, overriding the guess"),
    ] = None,
    zoom: float = 4.75,
    save: str | None = None,
    world: str | None = None,
) -> str:
    """A satisfactory-calculator.com map link centred on something, with layers on.

    `target` accepts a coordinate in metres, `me`, one of your named factories, a node
    id from `search_resource_nodes`, or a resource name — the last centres on that
    resource's nodes and switches its overlays on.

    Only the Crude Oil layer tokens are confirmed; the rest follow the same pattern and
    are flagged. A wrong token still opens the map in the right place, just without that
    overlay.
    """
    from ..spatial import maplink

    g = game()
    try:
        st = _state(save, world)
    except Exception:
        st = None

    table = nodes_mod.load_nodes()
    notes: list[str] = []
    resources: list[str] = []
    text = target.strip()

    # A node id centres on that node and lights up its own resource.
    by_instance = {k.rsplit(".", 1)[-1]: v for k, v in table.by_instance().items()}
    node = by_instance.get(text)
    if node is not None:
        origin = (node["x"], node["y"])
        where = f"{text} ({g.item_name(node['resource'])}, {node['purity']})"
        resources = [node["resource"]]
    elif (item := _item_id(text)) and item in maplink.LAYERS:
        # A resource name: centre on its nodes so the link lands somewhere useful.
        rows = table.by_resource(item)
        if not rows:
            return f"! no {g.item_name(item)} nodes on the map"
        origin = (
            sum(r["x"] for r in rows) / len(rows),
            sum(r["y"] for r in rows) / len(rows),
        )
        where = f"all {len(rows)} {g.item_name(item)} node(s)"
        resources = [item]
        notes.append(
            "centred on the centroid of every node of that resource, which may be open "
            "water if they are spread across the map -- pass a node id or x,y to pin it"
        )
    else:
        try:
            origin, where = _origin_for(st, text)
        except ValueError as exc:
            return f"! {exc}"

    # Which variants a resource actually HAS, read from the node table rather than
    # assumed: Coal is node-only, so emitting coalWellPure would be a token invented for
    # something that does not exist. Only oil, nitrogen and water have wells.
    kinds = sorted(
        {
            "well" if r["kind"].startswith("well") else "node"
            for res in resources
            for r in table.by_resource(res)
        }
    )
    tokens = layers or maplink.layers_for(resources, kinds or None)

    # Identity only. A metre of drift is far below one pixel of a map link at any zoom
    # this emits, so the position note would be noise -- but "your save does not call it
    # that" is something the reader will hit again the next time they paste the id.
    if node is not None:
        notes += nodes_mod.identity_notes(
            nodes_mod.skew_for_save(st.header if st else None, table), [node["instance"]]
        )

    url = maplink.map_url(origin[0], origin[1], tokens, zoom=zoom)
    body = url
    if tokens:
        body += "\n# layers: " + ", ".join(tokens)
    return render.envelope(
        f"# {where} at {int(origin[0] / 100)},{int(origin[1] / 100)} (metres)", body, notes
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
    sel = select_nodes(spec, table.nodes, resolve_resource=_item_id, player=_player_xy(st))
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
    # `alt` is a node z minus a refinery z, and it decides whether a fluid run needs pumps.
    # Scoped to the candidate nodes, not the whole table.
    notes += nodes_mod.skew_notes(
        nodes_mod.skew_for_save(st.header, table), [r["instance"] for r in rows]
    )

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


@mcp.tool(structured_output=False)
def whereami(
    radius_m: float = 500.0,
    save: str | None = None,
    world: str | None = None,
    limit: Limit = 8,
) -> str:
    """Where the player is standing, and what is around them.

    Position comes from the Char_Player_C pawn in the save, so it is wherever you
    were when it was written -- an autosave can be several minutes stale. Use
    ``near:me,<radius>`` as a source selector in the planning tools to scope work to
    here.
    """
    g = game()
    try:
        st = _state(save, world)
    except Exception as exc:
        return f"could not read save: {exc}"

    here = st.player_position()
    if here is None:
        return "no player pawn in this save, so there is no position to report"
    x, y, z = here
    rm = regions_mod.load_regions()
    label = rm.label_for(x, y)

    table = nodes_mod.load_nodes()
    near = nodes_mod.annotate(
        table.filter(center=(x, y), radius_m=radius_m),
        g,
        st.projection,
        st.unlocked_building_ids,
    )
    near.sort(key=lambda n: geo.distance_m((n["x"], n["y"]), (x, y)))
    rows = [
        (
            g.item_name(n["resource"]),
            n["purity"],
            render.num(n["rate"]),
            f"{geo.distance_m((n['x'], n['y']), (x, y)):.0f}m",
            geo.direction_of(n["x"], n["y"], x, y),
            "tapped" if n["tapped"] else ("LOCKED" if not n["reachable"] else "free"),
        )
        for n in near[: render.clamp(limit, default=8)]
    ]

    builds = [r for r in st._all_records() if r.get("pos")]
    closest = min(
        builds,
        key=lambda r: geo.distance_m((r["pos"][0], r["pos"][1]), (x, y)),
        default=None,
    )
    notes = [f"use near:me,{radius_m:g} as a source selector to plan around here"]
    # Distances here are measured FROM the table's coordinates, so a stale row makes
    # "nearest node" quietly wrong. Scoped to what is actually within radius_m.
    notes += nodes_mod.skew_notes(
        nodes_mod.skew_for_save(st.header, table), [n["instance"] for n in near]
    )
    if closest is not None:
        d = geo.distance_m((closest["pos"][0], closest["pos"][1]), (x, y))
        name = g.buildings[closest["cls"]].name if closest["cls"] in g.buildings else closest["cls"]
        notes.append(f"nearest building: {name} at {d:.0f}m")
    if len(st.players) > 1:
        notes.append(f"{len(st.players)} pawns in this save; showing the one holding a build gun")

    return render.envelope(
        "\n".join(
            [
                f"# {st.age_note}",
                render.kv(
                    [
                        ("x,y,z(m)", f"{x / 100:.0f},{y / 100:.0f},{z / 100:.0f}"),
                        ("region", label.describe()),
                        ("grid", geo.grid_cell(x, y)),
                        ("from_map_centre", geo.direction_of(x, y)),
                    ]
                ),
                f"# {len(near)} node(s) within {radius_m:g}m",
            ]
        ),
        render.table(
            ("resource", "purity", "rate", "dist", "dir", "status"),
            rows,
            total=len(near),
            limit=limit,
        ),
        notes,
    )
