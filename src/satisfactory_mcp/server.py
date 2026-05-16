"""FastMCP server. Tool registration only -- all logic lives in the sibling modules.

Every tool passes ``structured_output=False``: a tool annotated ``-> str`` otherwise
gets an outputSchema AND has its whole payload echoed into ``structuredContent``, a
measured ~1.96x wire-size tax for no benefit.
"""

from __future__ import annotations

import math
from functools import lru_cache
from typing import Annotated

from mcp.server.fastmcp import FastMCP
from pydantic import Field

from . import config, render
from .docs import search
from .docs.constants import max_clock, shards_for_clock
from .docs.loader import load_docs
from .docs.model import GameData
from .docs.normalize import normalize
from .graph.query import ASPECTS as QUERY_ASPECTS
from .graph.select import INDEX_WARNING as GRAPH_INDEX_WARNING
from .graph.select import SELECTOR_HELP as GRAPH_SELECTOR_HELP
from .graph.select import SelectorError
from .planning import advisor, byproducts, compare, supply
from .planning import bom as bom_mod
from .planning.diff import NEIGHBOUR_RADIUS_M as DIFF_NEIGHBOUR_M
from .planning.diff import build_diff
from .planning.layout import build_layout
from .planning.optimize import MW, free_lunch_audit, solve
from .planning.scenario import EXPORT_HELP, build_scenario, resolve_item
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


def _player_xy(st) -> tuple[float, float] | None:
    """Player XY for the near:me selector, or None if the save has no pawn."""
    here = st.player_position() if st else None
    return (here[0], here[1]) if here else None


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


# ============================================================ factories


def _cand_row(c, store, labelled: set[str]) -> tuple:
    named = {store.label_for(m).name for m in c.machines if store.label_for(m)}
    covered = sum(1 for m in c.machines if m in labelled)
    return (
        c.source,
        c.size,
        f"{int(c.centroid[0] / 100)},{int(c.centroid[1] / 100)}",
        f"{c.spread_m:.0f}m",
        f"{covered}/{c.size}" if covered else "-",
        ", ".join(sorted(named))[:40] or "-",
        c.name_hint()[:44],
    )


@mcp.tool(structured_output=False)
def factory_map(
    save: str | None = None,
    world: str | None = None,
    limit: Limit = 12,
    show: Annotated[str, Field(description="candidates | named | slabs | unlabelled | all")] = "all",
) -> str:
    """Proposed factories, from power islands and belt topology, plus what is named.

    Three independent signals are reported rather than one answer, because none is
    right alone: power islands separate outposts but leave a grown-together base as one
    476-machine blob; belt components shatter that blob into fragments; foundation slabs
    are the sharpest of the three but say nothing about the ground-built parts of a
    factory. Where they disagree, carve the difference with `name_factory` and a
    `product:`, `near:` or `slab:` selector.
    """
    try:
        st = _state(save, world)
    except Exception as exc:
        return f"could not read save: {exc}"
    from .graph import identity

    gr = st.graph
    store = st.labels
    base_c, line_c = identity.candidates(gr, st.game, st.projection)
    labelled = store.assigned()
    machines = set(gr.machines())
    n = render.clamp(limit)

    want = show.casefold()
    chunks: list[str] = []
    notes: list[str] = []

    if want in ("all", "named") and store.labels:
        rows = []
        for label in sorted(store.labels, key=lambda x: -len(x.anchors)):
            alive = set(label.anchors) & machines
            cand = identity.describe(sorted(alive), gr, st.game, st.projection, "label")
            rows.append(
                (
                    label.name,
                    len(label.anchors),
                    f"{len(alive)}/{len(label.anchors)}",
                    f"{int(cand.centroid[0] / 100)},{int(cand.centroid[1] / 100)}",
                    f"{cand.spread_m:.0f}m",
                    cand.name_hint()[:44],
                )
            )
        chunks.append(
            "## named\n"
            + render.table(("name", "machines", "alive", "x,y(m)", "spread", "makes"), rows)
        )
        for issue in store.review(machines):
            notes.append(
                f"{issue['name']}: {issue['missing']} anchor machine(s) gone "
                f"(recall {issue['recall']}) -- {issue['status']}"
            )

    if want in ("all", "candidates"):
        rows = [_cand_row(c, store, labelled) for c in base_c[:n]]
        chunks.append(
            "## power islands (bases)\n"
            + render.table(
                ("src", "n", "x,y(m)", "spread", "named", "labels", "makes"),
                rows,
                total=len(base_c),
                limit=n,
            )
        )
        fresh = [c for c in line_c if not set(c.machines) <= labelled]
        rows = [_cand_row(c, store, labelled) for c in fresh[:n]]
        chunks.append(
            "## belt components (lines), unnamed first\n"
            + render.table(
                ("src", "n", "x,y(m)", "spread", "named", "labels", "makes"),
                rows,
                total=len(fresh),
                limit=n,
            )
        )

    if want in ("all", "slabs"):
        sx = st.structures
        rows = []
        for group in sx.groups()[:n]:
            index = sx.slab_of[group[0]]
            slab = sx.slabs[index]
            cand = identity.describe(group, gr, st.game, st.projection, "structure")
            names = sorted({lbl.name for m in group if (lbl := store.label_for(m))})
            rows.append(
                (
                    index,
                    len(group),
                    slab.tiles,
                    f"{int(slab.centre[0] / 100)},{int(slab.centre[1] / 100)}",
                    f"{int(slab.extent[0] / 100)}x{int(slab.extent[1] / 100)}m",
                    ", ".join(names)[:34] or "-",
                    cand.name_hint()[:38],
                )
            )
        total = len(sx.groups())
        chunks.append(
            f"## foundation slabs ({len(sx.slabs)} platforms, "
            f"{len(sx.slab_of)} machines on one)\n"
            + render.table(
                ("slab", "machines", "tiles", "x,y(m)", "extent", "labels", "makes"),
                rows,
                total=total,
                limit=n,
            )
        )
        ground = len(machines) - len(sx.slab_of)
        if ground:
            notes.append(
                f"{ground} machine(s) stand on no foundation at all -- slabs cannot see "
                "them, so this signal is a candidate and never the arbiter."
            )

    if want in ("all", "unlabelled"):
        loose = identity.unassigned(gr, labelled)
        if loose:
            grouped = identity.describe(loose, gr, st.game, st.projection, "unlabelled")
            top = ", ".join(f"{name} {count}" for name, count in grouped.products.most_common(12))
            chunks.append(f"## unlabelled: {len(loose)} machine(s)\n{top or '(no recipes set)'}")

    if want in ("all", "candidates", "slabs"):
        notes.append(GRAPH_INDEX_WARNING)

    if base_c and base_c[0].size > 100:
        notes.append(
            f"the largest power island holds {base_c[0].size} machines across "
            f"{base_c[0].spread_m:.0f}m -- that is a grown-together base, not one factory. "
            "Carve it with name_factory(select=['product:Steel Ingot','near:x,y@150'])."
        )

    return render.envelope(
        f"# {st.age_note}\n# {len(machines)} machines; {len(base_c)} power island(s), "
        f"{len(line_c)} belt component(s); {len(store.labels)} named, "
        f"{len(labelled & machines)} machine(s) covered",
        "\n\n".join(chunks),
        notes,
    )


def _resolve_factory(st, factory: str):
    """A label name, a selector, or a proposal index -- in that order.

    Label first because that is what a player types. Falling through to the selector
    grammar means ``factory_query("proposal:3", ...)`` works before anything is named.
    """
    from .graph import select as gsel

    label = st.labels.find(factory)
    if label is not None:
        alive = set(st.graph.machines())
        return label.name, [m for m in label.anchors if m in alive]
    try:
        picked = gsel.select_machines(
            [factory],
            st.graph,
            st.game,
            st.projection,
            st.labels,
            structures=st.structures,
            proposals=st.proposals,
        )
    except gsel.SelectorError as exc:
        known = ", ".join(x.name for x in st.labels.labels) or "(none named yet)"
        raise gsel.SelectorError(f"{exc}. Named factories: {known}") from exc
    return factory, picked


@mcp.tool(structured_output=False)
def factory_query(
    factory: Annotated[str, Field(description="a label name, or any selector e.g. 'proposal:3'")],
    of: Annotated[
        str, Field(description="comma-separated: " + ", ".join(QUERY_ASPECTS))
    ] = "summary",
    limit: Limit = 15,
    save: str | None = None,
    world: str | None = None,
) -> str:
    """Ask one thing about one factory: what it makes, needs, draws, or touches.

    `of` accepts several at once, e.g. "balance,power,links".

    - **summary** size, position, top recipes, net power
    - **balance** per-item produced vs consumed vs net -- the sign is the point
    - **outputs** net surplus: it leaves the factory, or it backs up
    - **inputs** net deficit: it has to be fed in from outside
    - **machines** every machine with its building, recipe and clock
    - **recipes** / **buildings** counts
    - **power** draw vs generation at saved clocks
    - **nodes** resource nodes its extractors sit on
    - **links** which other factories it exchanges material with
    - **issues** paused, recipe-less, or unresolved machines

    Rates are NAMEPLATE at each machine's saved clock, not measured throughput. A
    starved factory still reports its full rate.
    """
    from .graph.query import build_view

    try:
        st = _state(save, world)
    except Exception as exc:
        return f"could not read save: {exc}"
    try:
        name, machines = _resolve_factory(st, factory)
    except SelectorError as exc:
        return f"! {exc}"
    if not machines:
        return f"! {factory!r} resolved to no machines that still exist in this save"

    view = build_view(name, machines, st.graph, st.game, st.projection, st.labels)
    asked = [a.strip().casefold() for a in of.split(",") if a.strip()]
    unknown = [a for a in asked if a not in QUERY_ASPECTS]
    if unknown:
        return f"! unknown aspect(s) {unknown}. Choose from: {', '.join(QUERY_ASPECTS)}"

    n = render.clamp(limit)
    chunks: list[str] = []
    g = st.game

    def bname(cls: str) -> str:
        b = g.buildings.get(cls)
        return b.name if b else cls.replace("Build_", "").replace("_C", "")

    for aspect in asked:
        if aspect == "summary":
            head = render.kv(
                [
                    ("machines", view.size),
                    ("at", f"{int(view.centroid[0] / 100)},{int(view.centroid[1] / 100)}"),
                    ("spread", f"{view.spread_m:.0f}m"),
                    ("draw", f"{view.draw_mw:.0f} MW"),
                    ("generation", f"{view.generation_mw:.0f} MW" if view.generation_mw else ""),
                    ("recipes", len(view.recipes)),
                    ("issues", len(view.issues) or ""),
                ]
            )
            makes = ", ".join(f"{k} {v:.0f}/min" for k, v in view.outputs()[:5]) or "-"
            needs = ", ".join(f"{k} {v:.0f}/min" for k, v in view.inputs()[:5]) or "-"
            chunks.append(f"## summary\n{head}\nmakes: {makes}\nneeds: {needs}")
        elif aspect == "balance":
            rows = []
            for item in sorted(view.flows, key=lambda k: -abs(view.net(k))):
                f = view.flows[item]
                net = view.net(item)
                verdict = (
                    "surplus" if net > 1e-6 else "needs feeding" if net < -1e-6 else "internal"
                )
                rows.append(
                    (item, render.num(f["produced"]), render.num(f["consumed"]), f"{net:+.1f}", verdict)
                )
            chunks.append(
                "## balance (items/min at saved clocks)\n"
                + render.table(("item", "made", "used", "net", ""), rows[:n], total=len(rows), limit=n)
            )
        elif aspect in ("outputs", "inputs"):
            data = view.outputs() if aspect == "outputs" else view.inputs()
            chunks.append(
                f"## {aspect}\n"
                + render.table(
                    ("item", "per min"),
                    [(k, render.num(v)) for k, v in data[:n]],
                    total=len(data),
                    limit=n,
                )
            )
        elif aspect == "machines":
            rows = [
                (m.instance, bname(m.building), m.recipe or "-", f"{m.clock:.0%}",
                 "paused" if m.paused else "")
                for m in sorted(view.machines, key=lambda x: (x.building, x.recipe))
            ]
            chunks.append(
                "## machines\n"
                + render.table(
                    ("instance", "building", "recipe", "clock", ""),
                    rows[:n],
                    total=len(rows),
                    limit=n,
                )
            )
        elif aspect == "recipes":
            chunks.append(
                "## recipes\n"
                + render.table(
                    ("recipe", "machines"), view.recipes.most_common(n),
                    total=len(view.recipes), limit=n,
                )
            )
        elif aspect == "buildings":
            chunks.append(
                "## buildings\n"
                + render.table(
                    ("building", "count"),
                    [(bname(c), v) for c, v in view.buildings.most_common(n)],
                    total=len(view.buildings),
                    limit=n,
                )
            )
        elif aspect == "power":
            chunks.append(
                "## power (nameplate at saved clocks)\n"
                + render.kv(
                    [
                        ("draw", f"{view.draw_mw:.1f} MW"),
                        ("generation", f"{view.generation_mw:.1f} MW"),
                        ("net", f"{view.generation_mw - view.draw_mw:+.1f} MW"),
                    ]
                )
            )
        elif aspect == "nodes":
            chunks.append(
                "## resource nodes\n"
                + render.table(
                    ("node", "resource", "purity", "extractor", "clock", "left"),
                    [
                        (a, b, c, bname(d), f"{e:.0%}", f if f is not None else "-")
                        for a, b, c, d, e, f in view.nodes[:n]
                    ],
                    total=len(view.nodes),
                    limit=n,
                )
            )
        elif aspect == "links":
            chunks.append(
                "## material links across the boundary\n"
                "# machines reached on the far side, not an edge count -- asymmetric by\n"
                "# nature, since the first machine of a small set blocks the rest\n"
                + render.table(
                    ("other side", "machines reached"), view.links.most_common(n),
                    total=len(view.links), limit=n,
                )
            )
        elif aspect == "issues":
            body = render.bullets(view.issues[:n]) if view.issues else "none"
            chunks.append(f"## issues ({len(view.issues)})\n{body}")

    notes = []
    loose = view.links.get("(unlabelled)")
    if loose:
        notes.append(
            f"{loose} connection(s) cross into machines no label covers -- "
            "run propose_factories to see what they are"
        )
    return render.envelope(
        f"# {st.age_note}\n# {name}: {view.size} machines", "\n\n".join(chunks), notes
    )


@mcp.tool(structured_output=False)
def factory_health(
    factory: Annotated[
        str, Field(description="a label name, any selector, or 'all' for every named factory")
    ] = "all",
    limit: Limit = 15,
    save: str | None = None,
    world: str | None = None,
) -> str:
    """Measured uptime per machine, and WHY each stopped one is stopped.

    The only measured numbers in this MCP. Every manufacturing building keeps a fixed
    300-second productivity window; uptime is seconds-producing over that window.

    States, worst first: `paused`, `dead node` (extractor bound to no resource --
    a game update removed it), `no recipe`, `blocked` (output stack full),
    `starved` (input empty), `stalled` (has input, output has room, still not running --
    usually power), `intermittent`, `saturated`, `unmonitored`.

    **Blocked is not automatically a fault.** A base whose output nobody consumes fills
    its buffers and stops, which is what a mature factory at rest looks like. Starved,
    stalled and no-recipe are the actionable ones.
    """
    from .graph.health import STATES, assess, summarise
    from .graph.select import SelectorError

    try:
        st = _state(save, world)
    except Exception as exc:
        return f"could not read save: {exc}"

    alive = set(st.graph.machines())
    n = render.clamp(limit)

    if factory.strip().casefold() in ("all", "*"):
        if not st.labels.labels:
            return "! nothing named yet -- run propose_factories, then name_factory"
        rows, notes = [], []
        for label in sorted(st.labels.labels, key=lambda x: -len(x.anchors)):
            report = assess(label.name, [m for m in label.anchors if m in alive], st.game, st.projection)
            mean = report.mean_uptime
            actionable = sum(
                report.by_state[s]
                for s in ("dead node", "no recipe", "starved", "stalled")
            )
            rows.append(
                (
                    label.name,
                    len(report.machines),
                    "-" if mean is None else f"{mean:.0%}",
                    report.by_state["blocked"] or "",
                    report.by_state["starved"] or "",
                    report.by_state["stalled"] or "",
                    report.by_state["no recipe"] or "",
                    report.by_state["dead node"] or "",
                    report.by_state["paused"] or "",
                    actionable or "",
                )
            )
        # Sort on the accumulated values, not on a column position: inserting a column
        # once silently reordered this table by the wrong field.
        rows.sort(key=lambda r: (-(r[-1] or 0), r[2]))
        blocked_total = sum(r[3] or 0 for r in rows)
        if blocked_total:
            notes.append(
                f"{blocked_total} machine(s) are blocked -- their output stack is full. "
                "That is what a factory nobody is drawing from looks like, not a fault. "
                "Look at starved/stalled/no-recipe first."
            )
        return render.envelope(
            f"# {st.age_note}\n# uptime measured over a 300s window per machine",
            render.table(
                ("factory", "n", "uptime", "blocked", "starved", "stalled", "no recipe",
                 "dead node", "paused", "todo"),
                rows[:n],
                total=len(rows),
                limit=n,
            ),
            notes,
        )

    try:
        name, machines = _resolve_factory(st, factory)
    except SelectorError as exc:
        return f"! {exc}"
    if not machines:
        return f"! {factory!r} resolved to no machines that still exist in this save"

    report = assess(name, machines, st.game, st.projection)
    chunks = [summarise(report)]

    worst = report.worst(n)
    if worst:
        chunks.append(
            "## needs attention\n"
            + render.table(
                ("instance", "state", "uptime", "recipe", "cause"),
                [
                    (
                        m.instance,
                        m.state,
                        "-" if m.uptime is None else f"{m.uptime:.0%}",
                        m.recipe or m.building.replace("Build_", "").replace("_C", ""),
                        ", ".join(m.cause),
                    )
                    for m in worst
                ],
                total=sum(1 for m in report.machines if m.needs_attention),
                limit=n,
            )
        )
    if report.blocked_on:
        chunks.append(
            "## output backing up\n"
            + render.table(
                ("item", "machines blocked"), report.blocked_on.most_common(n)
            )
        )
    if report.starved_of:
        chunks.append(
            "## inputs not arriving\n"
            + render.table(
                ("ingredient", "machines starved"), report.starved_of.most_common(n)
            )
        )

    notes = []
    if report.by_state["blocked"]:
        notes.append(
            f"{report.by_state['blocked']} blocked: output stack full, so its consumer "
            "is the bottleneck -- or nothing is drawing from it at all"
        )
    if report.by_state["dead node"]:
        notes.append(
            f"{report.by_state['dead node']} extractor(s) sit on NO resource node -- "
            "the node was removed, so they can never produce and must be rebuilt elsewhere"
        )
    if report.by_state["stalled"]:
        notes.append(
            f"{report.by_state['stalled']} stalled: has input, output has room, still "
            "not producing. Check power before anything else"
        )
    if report.by_state["unmonitored"]:
        notes.append(
            f"{report.by_state['unmonitored']} machine(s) keep no productivity monitor, "
            "so their uptime is unknown rather than zero"
        )
    assert STATES
    return render.envelope(f"# {st.age_note}\n# {name}", "\n\n".join(chunks), notes)


@mcp.tool(structured_output=False)
def propose_factories(
    save: str | None = None,
    world: str | None = None,
    limit: Limit = 15,
    max_span_m: Annotated[float, Field(description="cap on a proposal's diameter, metres")] = 250.0,
    unnamed_only: bool = False,
) -> str:
    """One coherence score over every signal, agglomerated into proposed factories.

    Combines foundation slabs, proximity, belt connectivity, shared products and
    supply links. Validated leave-one-factory-out against the player's twelve
    hand-named factories: precision 1.000, recall 0.945, and precision was 1.000 on
    every fold -- it never merges two factories, it only ever splits one.

    Use `name_factory` on what it proposes. `unnamed_only=True` answers "what have I
    built and not named".
    """
    try:
        st = _state(save, world)
    except Exception as exc:
        return f"could not read save: {exc}"
    from .graph import cohere, identity

    store = st.labels
    proposals = (
        st.proposals
        if max_span_m == cohere.MAX_SPAN_M
        else cohere.propose(
            st.graph, st.game, st.projection, st.structures, max_span_m=max_span_m
        )
    )
    rows = []
    shown = 0
    for k, pr in enumerate(proposals):
        names = sorted({lbl.name for m in pr.machines if (lbl := store.label_for(m))})
        if unnamed_only and names:
            continue
        shown += 1
        if shown > render.clamp(limit):
            continue
        cand = identity.describe(pr.machines, st.graph, st.game, st.projection, "proposal")
        rows.append(
            (
                k,
                pr.size,
                f"{int(cand.centroid[0] / 100)},{int(cand.centroid[1] / 100)}",
                f"{cand.spread_m:.0f}m",
                "+".join(str(x) for x in pr.parts) if len(pr.parts) > 1 else pr.size,
                "+".join(n for n, _ in pr.evidence.most_common(3)),
                ", ".join(names)[:26] or "-",
                cand.name_hint()[:34],
            )
        )
    total = shown
    covered = sum(1 for pr in proposals for m in pr.machines if store.label_for(m))
    return render.envelope(
        f"# {st.age_note}\n# {len(proposals)} proposal(s) over "
        f"{len(st.graph.machines())} machines; {covered} already named",
        render.table(
            ("#", "machines", "x,y(m)", "spread", "parts", "evidence", "labels", "makes"),
            rows,
            total=total,
            limit=limit,
        ),
        [
            (
                f"clusters only LINK within {max_span_m:.0f}m, so a sprawling factory is "
                "offered in pieces -- raise max_span_m if yours is bigger"
            ),
            (
                "a 'parts' column with more than one number means dependents were "
                "absorbed: a cluster whose belts and pipes lead almost only into one "
                "other factory joins it, however far away it sits"
            ),
            GRAPH_INDEX_WARNING,
        ],
    )


@mcp.tool(structured_output=False)
def select_machines(
    select: Annotated[list[str], Field(description=f"selector terms, ANDed. {GRAPH_SELECTOR_HELP}")],
    save: str | None = None,
    world: str | None = None,
    split: Annotated[bool, Field(description="keep only the largest spatial cluster")] = False,
    expand: Annotated[bool, Field(description="pull in everything belted to the result")] = False,
) -> str:
    """Preview which machines a selector picks, before naming them.

    Worth running first on anything product-based: 17 machines make Concrete on the
    reference save, but 15 of them are a construction feed inside the steel site and
    only one is the player's "concrete setup".
    """
    try:
        st = _state(save, world)
    except Exception as exc:
        return f"could not read save: {exc}"
    from .graph import identity
    from .graph import select as gsel

    try:
        picked = gsel.select_machines(
            select, st.graph, st.game, st.projection, st.labels,
            split=split, expand=expand, structures=st.structures,
            proposals=st.proposals,
        )
    except gsel.SelectorError as exc:
        return f"! {exc}"
    if not picked:
        return "! that selector matched no machines"

    cand = identity.describe(picked, st.graph, st.game, st.projection, "selector")
    groups = identity.cluster_machines(picked, st.projection)
    parts = [
        render.kv(
            [
                ("machines", cand.size),
                ("at", f"{int(cand.centroid[0] / 100)},{int(cand.centroid[1] / 100)}"),
                ("spread", f"{cand.spread_m:.0f}m"),
                ("clusters", len(groups)),
            ]
        ),
        "products: " + (", ".join(f"{k} {v}" for k, v in cand.products.most_common(10)) or "-"),
        "buildings: " + ", ".join(f"{v}x {k.replace('Build_', '')}" for k, v in cand.buildings.most_common(8)),
    ]
    if len(groups) > 1:
        sub = identity.describe(groups[0], st.graph, st.game, st.projection, "selector")
        parts.append(
            f"! {len(groups)} separate sites {[len(g) for g in groups]}; the largest is "
            f"{sub.size} at {int(sub.centroid[0] / 100)},{int(sub.centroid[1] / 100)}. "
            "Pass split=true to keep only that one."
        )
    clashes = {lbl.name for m in picked if (lbl := st.labels.label_for(m))}
    if clashes:
        parts.append("already named: " + ", ".join(sorted(clashes)))
    return render.envelope(f"# {st.age_note}", "\n".join(parts))


@mcp.tool(structured_output=False)
def name_factory(
    name: str,
    select: Annotated[list[str], Field(description=f"selector terms, ANDed. {GRAPH_SELECTOR_HELP}")],
    notes: str = "",
    save: str | None = None,
    world: str | None = None,
    split: Annotated[bool, Field(description="keep only the largest spatial cluster")] = False,
    expand: Annotated[bool, Field(description="pull in everything belted to the result")] = False,
    dry_run: bool = False,
) -> str:
    """Name a set of machines and persist it for this world.

    The label stores the machine instance ids, which are stable across saves, so it
    survives moving machines, adding to the factory, and autosave rotation. Calling
    this again with the same name re-anchors it to the current selection.
    """
    try:
        st = _state(save, world)
    except Exception as exc:
        return f"could not read save: {exc}"
    from .graph import identity
    from .graph import select as gsel

    try:
        picked = gsel.select_machines(
            select, st.graph, st.game, st.projection, st.labels,
            split=split, expand=expand, structures=st.structures,
            proposals=st.proposals,
        )
    except gsel.SelectorError as exc:
        return f"! {exc}"
    if not picked:
        return "! that selector matched no machines; nothing named"

    store = st.labels
    stolen: dict[str, int] = {}
    for machine in picked:
        other = store.label_for(machine)
        if other and other.name.casefold() != name.strip().casefold():
            stolen[other.name] = stolen.get(other.name, 0) + 1

    cand = identity.describe(picked, st.graph, st.game, st.projection, "label")
    existing = store.find(name)
    verb = "would name" if dry_run else ("re-anchored" if existing else "named")
    head = (
        f"{verb} {cand.size} machine(s) as {name!r} at "
        f"{int(cand.centroid[0] / 100)},{int(cand.centroid[1] / 100)} "
        f"(spread {cand.spread_m:.0f}m): {cand.name_hint()}"
    )
    warn = [f"overlaps {other!r} on {n} machine(s)" for other, n in sorted(stolen.items())]
    if existing and not dry_run:
        kept = len(set(existing.anchors) & set(picked))
        warn.append(
            f"was {len(existing.anchors)} machine(s), {kept} kept, "
            f"{len(existing.anchors) - kept} dropped"
        )

    if dry_run:
        return render.envelope(f"# {head}", "", warn + ["dry run: nothing written"])

    when = st.header.get("save_datetime") or st.header.get("filename") or ""
    label = store.put(name, picked, notes=notes, when=str(when))
    label.centroid = cand.centroid
    label.signature = dict(cand.buildings)
    path = store.save()
    return render.envelope(f"# {head}", f"stored in {path}", warn)


@mcp.tool(structured_output=False)
def list_factories(save: str | None = None, world: str | None = None) -> str:
    """Named factories for this world, with how much of each is still standing."""
    try:
        st = _state(save, world)
    except Exception as exc:
        return f"could not read save: {exc}"
    from .graph import identity

    store = st.labels
    if not store.labels:
        return render.envelope(
            f"# {st.age_note}\n# no factories named yet for world {store.world_id!r}",
            "Run factory_map to see candidates, then name_factory to persist one.",
        )
    machines = set(st.graph.machines())
    rows = []
    for label in sorted(store.labels, key=lambda x: -len(x.anchors)):
        alive = sorted(set(label.anchors) & machines)
        cand = identity.describe(alive, st.graph, st.game, st.projection, "label")
        rows.append(
            (
                label.name,
                len(label.anchors),
                f"{len(alive)}/{len(label.anchors)}",
                f"{int(cand.centroid[0] / 100)},{int(cand.centroid[1] / 100)}",
                f"{cand.spread_m:.0f}m",
                cand.name_hint()[:40],
                label.notes[:40],
            )
        )
    loose = len(identity.unassigned(st.graph, store.assigned()))
    return render.envelope(
        f"# {st.age_note}\n# {len(store.labels)} named, {loose} machine(s) unlabelled",
        render.table(
            ("name", "anchors", "alive", "x,y(m)", "spread", "makes", "notes"), rows
        ),
        [
            f"{d['name']}: {d['status']} (recall {d['recall']})"
            for d in store.review(machines)
        ],
    )


@mcp.tool(structured_output=False)
def forget_factory(name: str, save: str | None = None, world: str | None = None) -> str:
    """Delete a factory label. The machines themselves are untouched."""
    try:
        st = _state(save, world)
    except Exception as exc:
        return f"could not read save: {exc}"
    store = st.labels
    label = store.find(name)
    if label is None:
        known = ", ".join(x.name for x in store.labels) or "(none)"
        return f"! no label named {name!r}. Known: {known}"
    store.remove(label.name)
    store.save()
    return f"forgot {label.name!r} ({len(label.anchors)} machine(s) released)"


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
            r["_d"] = math.dist((r["x"], r["y"]), origin) / 100.0
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
                *((f"{r['_d']:.0f}m",) if show_distance else (r["purity"] if mixed else r["kind"],)),
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
            f"dist to {where}" if show_distance else ("purity" if mixed else "kind"),
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
        notes.append('mode="nodes" lists individual nodes; mode="nearest" ranks by distance')

    return render.envelope(
        f"# {sel.description}: {len(rows_all)} node(s), "
        f"{render.num(total)} {unit} total, {render.num(free)} free and reachable\n"
        f"# rates at 100% clock; coords in metres",
        body,
        notes,
    )


def _origin_for(st, near: str) -> tuple[tuple[float, float], str]:
    """Resolve a location: "x,y" in metres, "me", or the name of a named factory.

    A factory name is the useful one now that factories exist -- "nearest coal to the
    coal powerplant" is the question actually being asked, and hand-copying a centroid
    out of another tool's output is how the wrong coordinate gets used.
    """
    text = near.strip()
    if "," in text:
        try:
            x_m, y_m = (float(v) for v in text.split(",", 1))
        except ValueError as exc:
            raise ValueError(f"{near!r} is not an x,y pair in metres") from exc
        return (x_m * 100.0, y_m * 100.0), f"{int(x_m)},{int(y_m)}"

    if text.casefold() in ("me", "player", "here"):
        here = _player_xy(st)
        if here is None:
            raise ValueError("this save has no player pawn, so 'me' cannot be resolved")
        return here, "you"

    label = st.labels.find(text) if st else None
    if label is None:
        known = ", ".join(x.name for x in st.labels.labels) if st else ""
        raise ValueError(
            f"{near!r} is neither an x,y pair, 'me', nor a named factory"
            + (f". Named: {known}" if known else "")
        )
    pos = {}
    for key in ("machines", "extractors", "generators"):
        for record in st.projection.get(key, ()):
            if record.get("pos"):
                pos[record["instance"].rsplit(".", 1)[-1]] = record["pos"]
    points = [pos[m][:2] for m in label.anchors if m in pos]
    if not points:
        raise ValueError(f"{label.name!r} has no machines left to centre on")
    return (
        (sum(p[0] for p in points) / len(points), sum(p[1] for p in points) / len(points)),
        label.name,
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


# ============================================================ planning


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

    # plan_layout declares fewer knobs than plan_factory; only the ones it has are
    # offered as overrides, and the rest come from the stored plan untouched.
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
        from .planning.fit import assess_fit

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


# ============================================================ resources
#
# Resources are CLIENT-PULLED, so they cost zero context until something asks for
# them. That makes them right for stable orientation data and wrong for anything
# parameterised, which stays a tool.


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
        verdict = "affordable" if need <= budget["free"] else "SHORT"
        notes.append(
            f"plan: {plan_machines} machine(s) at clock {render.num(plan_clock)} needs "
            f"{need_each} shard(s) each = {need}; free {render.num(budget['free'])} -> "
            f"{verdict}"
            + (f", short by {render.num(need - budget['free'])}" if need > budget["free"] else "")
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
