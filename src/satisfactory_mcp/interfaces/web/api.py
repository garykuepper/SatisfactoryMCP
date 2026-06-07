"""The JSON endpoints. Parse the query, call a domain service, serialise.

The same shape as an MCP tool module and for the same reason: this layer decides
nothing. Every question answered here is answered by ``domain/`` -- the placement rows,
the power ledger, the node occupancy, the factory proposals -- and the only work done
in this file is turning a query string into arguments and a dataclass into JSON.

Two conventions run through the whole surface:

* **Metres, one decimal.** The save stores centimetres. Every coordinate that leaves
  this module has been divided by 100 and rounded, exactly as the text presenters do,
  because a reader who sees two units in one product will eventually mix them.
* **``?save=`` and ``?world=`` everywhere a state is read**, so a page can pin itself
  to one save while the game keeps autosaving over another.

Errors are ``{"error": "..."}`` with a 4xx, never a 200 with an empty list: a browser
that cannot tell "no nodes" from "no save" will draw an empty map and say nothing.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse

from ... import config
from ...core.gamedata.footprint import FOUNDATION_M
from ...core.saveio import projection as proj
from ...domain.collectibles.service import collect_view
from ...domain.factories import identity as fidentity
from ...domain.spatial import elevation as spatial_elevation
from ...domain.spatial import geo
from ...domain.spatial import nodes as spatial_nodes
from ...domain.spatial import regions as spatial_regions
from ...domain.world.state import WorldState

__all__ = ["DEFAULT_MAP_BOUNDS_M", "INSPECT_NEAREST", "INSPECT_RADIUS_M", "PING_SECONDS", "router"]

#: How long a quiet SSE stream waits before sending a comment. Proxies and browsers
#: both drop a connection that has said nothing for a while, and a comment line is the
#: cheapest thing that counts as having said something.
PING_SECONDS = 15.0

router = APIRouter(prefix="/api")


# --------------------------------------------------------------------- helpers


def _m(value: float | None) -> float | None:
    """Centimetres to metres, one decimal. The unit rule, in one place."""
    return None if value is None else round(float(value) / 100.0, 1)


def _xyz(pos: Any) -> dict[str, float | None]:
    """A projection ``pos`` triple as named metre fields."""
    if not pos:
        return {"x_m": None, "y_m": None, "z_m": None}
    p = list(pos) + [None, None, None]
    return {"x_m": _m(p[0]), "y_m": _m(p[1]), "z_m": _m(p[2])}


def _fail(message: str, status: int = 400) -> JSONResponse:
    return JSONResponse({"error": message}, status_code=status)


def _state(request: Request, save: str | None, world: str | None) -> WorldState:
    """The world a request is asking about. Raises whatever the loader raises."""
    return request.app.state.load_state(save, world)


def _label_json(label: spatial_regions.Label) -> dict | None:
    """A region lookup as JSON, or ``None`` for ocean and off-map.

    ``None`` rather than a nearest-land guess, which is the refusal ``label_for`` already
    makes and which this layer must not undo -- a page that printed the closest biome for
    a click in the sea would read exactly like a measurement.

    The confidence word travels with the name because the name alone cannot be trusted:
    the raster is 256 m per cell, so "Northern Forest, boundary" and "Northern Forest,
    interior" are different claims. ``certain`` is the domain's own reading of that word,
    computed here once so the page does not have to know the four codes.
    """
    if label.name is None:
        return None
    return {
        "name": label.name,
        "confidence": label.confidence,
        "accuracy_m": label.accuracy_m,
        "certain": label.certain,
        "text": label.describe(),
    }


def _record_row(st: WorldState, row: dict) -> dict:
    """One machine/extractor/generator, flattened for the map.

    ``clock`` and ``paused`` are read with ``get``: the projection only carries them
    for the records that have them, and an extractor at 250% and a constructor with no
    overclock property must both come out of here without a KeyError.

    ``w_m``/``l_m`` are the building's own footprint -- the X and Y extent of the union
    of its clearance boxes, which is what makes a Manufacturer draw bigger than a
    Constructor instead of both being the same nominal square. Only 69 of 539 buildings
    carry clearance data, so these are **null** for the rest (the two biomass burners on
    the reference save among them) rather than a guessed number: the client picks the
    fallback, because a fallback drawn here would be indistinguishable from a measurement.
    """
    cls = row.get("cls") or ""
    building = st.game.buildings.get(cls)
    footprint = getattr(building, "footprint", None) if building else None
    return {
        "instance_leaf": str(row.get("instance", "")).rsplit(".", 1)[-1],
        "cls": row.get("cls"),
        "name": building.name if building else cls,
        **_xyz(row.get("pos")),
        "recipe": row.get("recipe"),
        "clock": row.get("clock"),
        "paused": bool(row.get("paused", False)),
        # Footprint is already metres; the projection's coordinates are not.
        "w_m": round(footprint.width_m, 1) if footprint else None,
        "l_m": round(footprint.depth_m, 1) if footprint else None,
    }


# --------------------------------------------------------------------- worlds


@router.get("/worlds")
def worlds() -> Any:
    """Every world the save directory holds, newest first."""
    try:
        found, unsupported = proj.list_worlds()
    except Exception as exc:
        return _fail(f"could not scan saves: {exc}", 404)
    rows = []
    for w in found:
        newest = w.newest
        rows.append(
            {
                **asdict(w),
                "mtime": newest.get("mtime_ns", 0) / 1e9,
                "newest_filename": newest.get("filename"),
                "play_duration_s": w.max_play_duration_s,
            }
        )
    return {"worlds": rows, "unsupported": list(unsupported)}


# -------------------------------------------------------------------- summary


@router.get("/summary")
def summary(request: Request, save: str | None = None, world: str | None = None) -> Any:
    try:
        st = _state(request, save, world)
    except Exception as exc:
        return _fail(f"could not read save: {exc}", 404)
    return {
        "header": st.header,
        "age_note": st.age_note,
        "power": st.power_report(),
        "progression": st.progression(),
    }


# ---------------------------------------------------------------------- nodes


@router.get("/nodes")
def nodes(
    request: Request,
    resource: str | None = None,
    save: str | None = None,
    world: str | None = None,
) -> Any:
    """The resource node table, joined to what this save has built on it.

    The join is deliberately partial and says so: ``occupancy`` resolves only the
    extractors whose target is a node key, so ``occupied`` false means "no extractor
    known here", never "free". The map draws it as unknown-or-free and the popup
    carries the node id, which doubles as a ``node:`` selector for the MCP tools.

    The region name is joined here rather than in the browser because the raster lives on
    this side: sending 608 rows and then a 30x30 grid for the page to index into would put
    the orientation trap (row 0 is the NORTH edge) in two places. ``label_for_node`` rather
    than ``label_for`` -- it prefers the hand-verified override table, so the nodes someone
    actually checked come back as ``verified`` instead of as a 256 m cell's best guess.
    ``null`` for a node the raster calls void, which is the honest answer for the handful
    that sit on islands off the grid.
    """
    try:
        st = _state(request, save, world)
    except Exception as exc:
        return _fail(f"could not read save: {exc}", 404)
    try:
        table = spatial_nodes.load_nodes()
        rmap = spatial_regions.load_regions()
    except FileNotFoundError as exc:
        return _fail(str(exc), 404)

    taken = spatial_nodes.occupancy(st.projection)
    rows = table.by_resource(resource) if resource else table.nodes
    out = []
    for n in rows:
        held = taken.get(n["instance"])
        out.append(
            {
                "id": n["instance"],
                "resource": n["resource"],
                "name": str(n["instance"]).rsplit(".", 1)[-1],
                "kind": n["kind"],
                "purity": n["purity"],
                **_xyz((n["x"], n["y"], n["z"])),
                "occupied": held is not None,
                "occupant_cls": held["extractor"] if held else None,
                "region": _label_json(rmap.label_for_node(n)),
            }
        )
    return {"nodes": out, "resource": resource, "occupied": sum(1 for r in out if r["occupied"])}


# ----------------------------------------------------------- point inspector


#: How far a click looks for known elevations, metres. The same default
#: ``describe_location`` uses, so the map and the MCP tool answer one question one way.
INSPECT_RADIUS_M = 200.0

#: How many nodes a click reports. Enough to see what a site is next to; more would make
#: the popup a second copy of the node table.
INSPECT_NEAREST = 5


def _elevation_json(near: spatial_elevation.Elevation) -> dict:
    """A probe as JSON, with the reason for every number it declines to give.

    Ground and built stay separate populations all the way out to the page, because that
    is the whole point of the module they come from: a node rests on terrain, a foundation
    is wherever the player put it, and averaging them near a platform produces the
    platform's height wearing the word "ground".

    ``fill_m`` is ``null`` more often than not, and a null with no reason next to it reads
    as a bug. It has exactly two causes -- fewer than ``MIN_GROUND_SAMPLES`` nodes nearby,
    or nothing built nearby -- and ``fill_note`` names whichever one applied. Neither is
    ever rendered as 0: zero fill is a real, different measurement.
    """
    ground, built = near.ground, near.built
    # Derived from the samples actually present rather than from a hardcoded list, so a
    # new non-ground source in the domain module arrives here without an edit.
    built_sources = tuple(s for s in near.counts if s not in spatial_elevation.GROUND_SOURCES)

    fill = near.fill_m
    note = None
    if fill is None:
        if len(ground) < spatial_elevation.MIN_GROUND_SAMPLES:
            note = (
                f"not enough ground samples ({len(ground)} of "
                f"{spatial_elevation.MIN_GROUND_SAMPLES} within {near.radius_m:g} m)"
            )
        elif not built:
            note = f"nothing built within {near.radius_m:g} m"

    def _round(value: float | None) -> float | None:
        return None if value is None else round(value, 1)

    return {
        "radius_m": near.radius_m,
        "ground_m": _round(near.median(*spatial_elevation.GROUND_SOURCES)),
        "ground_spread_m": _round(near.spread(*spatial_elevation.GROUND_SOURCES)),
        "ground_count": len(ground),
        "built_m": _round(near.median(*built_sources)) if built_sources else None,
        "built_count": len(built),
        "fill_m": _round(fill),
        "fill_note": note,
        "counts": dict(near.counts),
    }


def _nearest_nodes(table, taken: dict, x: float, y: float, limit: int) -> list[dict]:
    """The closest ``limit`` nodes to a point, centimetres in, metres out."""
    ranked = sorted(
        ((geo.distance_m((x, y), (n["x"], n["y"])), n) for n in table.nodes),
        key=lambda pair: pair[0],
    )
    out = []
    for distance_m, n in ranked[:limit]:
        held = taken.get(n["instance"])
        out.append(
            {
                "id": n["instance"],
                "name": str(n["instance"]).rsplit(".", 1)[-1],
                "resource": n["resource"],
                "kind": n["kind"],
                "purity": n["purity"],
                **_xyz((n["x"], n["y"], n["z"])),
                "occupied": held is not None,
                "occupant_cls": held["extractor"] if held else None,
                "distance_m": round(distance_m, 1),
            }
        )
    return out


@router.get("/inspect")
def inspect(
    request: Request,
    x_m: float,
    y_m: float,
    save: str | None = None,
    world: str | None = None,
) -> Any:
    """What is at a coordinate: the region, the measured ground, and the nearest nodes.

    The three answers a site starts with, and none of them was on the map before. Every
    one comes straight out of ``domain.spatial`` -- this endpoint converts metres to the
    save's centimetres, calls three functions, and rounds.

    **A failed save is not a failed answer.** The node table is static, covers the whole
    map and needs no ``.sav`` at all, so a world whose save will not load still gets its
    region, its ground elevation and its nearest nodes; what it loses is the built
    population and the occupancy join, and ``save_error`` says so out loud rather than
    letting "no extractor here" quietly mean "no save here".

    Not cached, deliberately and by measurement: ``sample_points`` over the 320-hour
    reference world builds 9,525 samples in 2.0 ms and ``probe`` scans them in 0.8 ms, so
    a per-(world, save) cache would add an invalidation bug to save ~3 ms on a click.
    """
    try:
        table = spatial_nodes.load_nodes()
        rmap = spatial_regions.load_regions()
    except FileNotFoundError as exc:
        return _fail(str(exc), 404)

    st: WorldState | None = None
    save_error: str | None = None
    try:
        st = _state(request, save, world)
    except Exception as exc:
        save_error = f"could not read save: {exc}"

    x, y = x_m * 100.0, y_m * 100.0
    near = spatial_elevation.probe(
        x, y, spatial_elevation.sample_points(table, st), INSPECT_RADIUS_M
    )
    taken = spatial_nodes.occupancy(st.projection) if st is not None else {}
    return {
        "at": {"x_m": round(x_m, 1), "y_m": round(y_m, 1)},
        "region": _label_json(rmap.label_for(x, y)),
        "elevation": _elevation_json(near),
        "nearest": _nearest_nodes(table, taken, x, y, INSPECT_NEAREST),
        "save_error": save_error,
    }


# -------------------------------------------------------------------- regions


#: Where a user-supplied map render goes. Nothing here is ever committed: the endpoint
#: below is a loader and only a loader, because a rendered map of this world is someone
#: else's artwork and the licence posture of this repository is that we ship none of it.
#: Drop your own render at ``data/local/map.png`` and, if its corners are not the standard
#: in-game map square, ``data/local/map.json`` next to it.
LOCAL_DIR_NAME = "local"
MAP_IMAGE_NAME = "map.png"
MAP_BOUNDS_NAME = "map.json"

#: The corners of the in-game map square, metres, game axes. The playable content is
#: strictly inside it: ``frame.content_bbox`` in ``data/satisfactory_regions.json`` --
#: the min/max over 2,688 static world objects -- is x [-2988.4, 4065.6], y [-3141.0,
#: 3042.0], so an image pinned here cannot clip anything the map draws.
DEFAULT_MAP_BOUNDS_M = {
    "x_min_m": -3247.0,
    "x_max_m": 4253.0,
    "y_min_m": -3750.0,
    "y_max_m": 3750.0,
}


def _local_dir() -> Path:
    """The user's own files, read at call time so a test can point it somewhere else."""
    return config.data_dir() / LOCAL_DIR_NAME


def _map_bounds() -> dict[str, float]:
    """Where to pin the map image, defaults overridden by ``local/map.json`` if present.

    A malformed override is ignored rather than fatal: the picture is decoration, and a
    typo in an optional sidecar must not take the endpoint that serves it down with it.
    """
    bounds = dict(DEFAULT_MAP_BOUNDS_M)
    path = _local_dir() / MAP_BOUNDS_NAME
    try:
        override = json.loads(path.read_text(encoding="utf-8"))
        bounds.update({k: float(override[k]) for k in DEFAULT_MAP_BOUNDS_M if k in override})
    except (OSError, ValueError, TypeError):
        return bounds
    return bounds


@router.get("/regions")
def regions() -> Any:
    """The biome raster: a 30x30 character grid, its legend, and each region's extent.

    No ``?save``/``?world``: this is the world's own geography, identical for every save,
    which is why it is cacheable and fetched once per page load.

    The raster comes through ``domain.spatial.regions``, which reads
    ``data/region_names.json``. Two committed files carry a 30x30 grid and they differ:
    this one is the file whose per-region bounding boxes are derived from its own grid, so
    every cell provably lies inside the box of the region it names -- which is exactly
    what the drawing client is checked against. ``satisfactory_regions.json``'s boxes come
    from a coarser 1.024 km grid and do not agree with its raster cell for cell, so
    painting from it would leave nothing to verify orientation with.

    The one thing a drawing client gets wrong is orientation, so it is stated here rather
    than left to be inferred. Game +X is east and game **+Y is south**; ``y0_m`` is the
    smallest y, so **grid row 0 is the northern edge** and column 0 the western one. Cell
    ``(i, j)`` spans x ``[x0_m + i*cell_m, x0_m + (i+1)*cell_m]`` and y ``[y0_m + j*cell_m,
    ...]``, and a page that plots ``[-y, x]`` has to flip those y bounds to draw it. The
    ``.`` cells are ocean or off-map and carry no name: left unpainted they are the
    coastline.
    """
    try:
        rmap = spatial_regions.load_regions()
    except FileNotFoundError as exc:
        return _fail(str(exc), 404)
    payload = {
        "grid": list(rmap.grid),
        "legend": dict(rmap.legend),
        "cell_m": _m(rmap.cell),
        "x0_m": _m(rmap.x0),
        "y0_m": _m(rmap.y0),
        "regions": {
            name: {
                "centroid_m": [_m(entry["centroid"][0]), _m(entry["centroid"][1])],
                "bbox_m": [_m(v) for v in entry["bbox"]],
            }
            for name, entry in rmap.regions.items()
        },
    }
    return JSONResponse(payload, headers={"Cache-Control": "max-age=3600"})


@router.api_route("/mapimage", methods=["GET", "HEAD"])
def mapimage() -> Any:
    """A map render the *user* dropped in, if they dropped one in. Never shipped.

    HEAD is routed alongside GET on purpose: the page probes with HEAD before it builds
    an ``imageOverlay``, and FastAPI -- unlike bare Starlette -- does not add HEAD to a
    GET route by itself, so a probe would come back 405 and read as "no image".

    The corners travel with the file in ``X-Map-Bounds-M`` (``x_min,y_min,x_max,y_max``,
    metres, game axes) so the one probe the page already makes answers both questions.
    """
    path = _local_dir() / MAP_IMAGE_NAME
    if not path.is_file():
        return _fail(
            f"no map image: put a map render at {path}; it is only ever read locally, "
            "never uploaded and never committed. Optionally pin its corners with "
            f"{path.with_name(MAP_BOUNDS_NAME)} "
            '{"x_min_m":…,"x_max_m":…,"y_min_m":…,"y_max_m":…}',
            404,
        )
    b = _map_bounds()
    return FileResponse(
        path,
        headers={
            "X-Map-Bounds-M": "{x_min_m},{y_min_m},{x_max_m},{y_max_m}".format(**b),
            "Cache-Control": "no-cache",
        },
    )


# ------------------------------------------------------------------- machines


@router.get("/machines")
def machines(request: Request, save: str | None = None, world: str | None = None) -> Any:
    try:
        st = _state(request, save, world)
    except Exception as exc:
        return _fail(f"could not read save: {exc}", 404)
    p = st.projection
    return {
        kind: [_record_row(st, row) for row in p.get(kind, ())]
        for kind in ("machines", "extractors", "generators")
    }


# ----------------------------------------------------------------- structures


@router.get("/structures")
def structures(request: Request, save: str | None = None, world: str | None = None) -> Any:
    """Every lightweight buildable the player placed: foundations, ramps, walls, catwalks.

    These are the only record of what was physically BUILT -- they appear in no actor
    header, which is why the projection interns them separately as
    ``{"classes": [...], "instances": [[class_index, x, y, z], ...]}`` in centimetres.
    Read guarded field by field, exactly as ``domain.spatial.elevation`` reads them: this
    is raw projection data and a malformed row should cost one piece, not the endpoint.

    Two things the projection does **not** carry, and neither is invented here:

    * **Rotation.** The instance transform's quaternion is dropped at extraction. A
      client can only draw these axis-aligned.
    * **Per-class size.** None of these classes has clearance data, so ``footprint`` is
      ``None`` for all eighteen of them. They are all built on the same grid instead,
      whose edge ``tile_m`` reports from ``FOUNDATION_M`` so the page does not hardcode 8.

    Positions are piece centres: on the reference save consecutive foundations of one
    slab sit exactly ``tile_m`` apart.

    A world with nothing built answers ``{"structures": [], "count": 0}`` -- an empty
    list is a real answer here, unlike a save that could not be read at all.

    Sent one row per piece, ungrouped. Measured on the reference world -- 8,347 pieces,
    610 KB, 0.52 s over loopback -- which is the same order as ``/api/collectibles``
    already ships (3,455 rows, 547 KB, 0.52 s). Grouping into grid cells would halve a
    payload that is not the bottleneck and would cost the per-piece class the popup and
    the point inspector read.
    """
    try:
        st = _state(request, save, world)
    except Exception as exc:
        return _fail(f"could not read save: {exc}", 404)

    raw = st.projection.get("structures") or {}
    classes = list(raw.get("classes") or ())
    rows = []
    for inst in raw.get("instances") or ():
        if not isinstance(inst, (list, tuple)) or len(inst) < 4:
            continue
        try:
            index = int(inst[0])
            x, y, z = float(inst[1]), float(inst[2]), float(inst[3])
        except (TypeError, ValueError):
            continue
        rows.append(
            {
                "cls": classes[index] if 0 <= index < len(classes) else None,
                "x_m": _m(x),
                "y_m": _m(y),
                "z_m": _m(z),
            }
        )
    return {"structures": rows, "count": len(rows), "tile_m": FOUNDATION_M}


# ------------------------------------------------------------------ factories


@router.get("/factories")
def factories(request: Request, save: str | None = None, world: str | None = None) -> Any:
    """Named factories and the coherence-scored proposals for the unnamed rest.

    Each row carries ``bbox_m`` -- ``[x_min, y_min, x_max, y_max]`` in metres, game axes
    -- alongside its centroid, because a centroid alone cannot frame a viewport. The map
    turns a label into a button that flies to its factory, and "fly to the mean of 50
    machines" is not the same request as "show me all 50": the first picks a zoom out of
    the air, the second is decided by the extent. Computed here rather than client-side
    because the client is never sent the anchor machines, only their count.

    ``null`` when nothing in the set is still standing -- ``geo.bbox`` refuses to invent
    a zero box at the world centre, and so does this. A label whose machines were all
    demolished keeps its name and its remembered centroid; what it loses is the ability
    to be flown to, which is the honest report.
    """
    try:
        st = _state(request, save, world)
    except Exception as exc:
        return _fail(f"could not read save: {exc}", 404)

    placed = fidentity.positions(st.projection)

    def _bbox_m(machines) -> list[float] | None:
        box = geo.bbox([placed[m][:2] for m in machines if m in placed])
        return None if box is None else [_m(v) for v in box]

    named = [
        {
            "name": label.name,
            "centroid_m": [_m(label.centroid[0]), _m(label.centroid[1])],
            "bbox_m": _bbox_m(label.anchors),
            "machines": len(label.anchors),
            "notes": label.notes,
        }
        for label in sorted(st.labels.labels, key=lambda x: -len(x.anchors))
    ]

    proposals = []
    for index, pr in enumerate(st.proposals):
        cand = fidentity.describe(pr.machines, st.graph, st.game, st.projection, "proposal")
        proposals.append(
            {
                "index": index,
                "label": cand.name_hint(),
                "centroid_m": [_m(cand.centroid[0]), _m(cand.centroid[1])],
                "bbox_m": _bbox_m(pr.machines),
                "machines": pr.size,
                "score": round(pr.cohesion, 3),
                "spread_m": round(cand.spread_m, 1),
            }
        )
    return {"labels": named, "proposals": proposals}


# ---------------------------------------------------------------- collectibles


@router.get("/collectibles")
def collectibles(
    request: Request,
    group: str | None = None,
    mode: str = "remaining",
    near: str | None = None,
    save: str | None = None,
    world: str | None = None,
) -> Any:
    """Map placements, filtered exactly the way the MCP tool filters them.

    ``collect_view`` owns every refusal -- unknown mode, retired group, and the one
    that matters here: ``mode=remaining`` needs the generated placement table, and
    without it the honest answer is the refusal rather than a shorter list.
    """
    try:
        st = _state(request, save, world)
    except Exception as exc:
        return _fail(f"could not read save: {exc}", 404)

    view = collect_view(st, group, mode, near)
    if view.error:
        return _fail(view.error)

    rows = [
        {
            "category": r["category"],
            "name": r["name"],
            **_xyz(r["pos"]),
            "collected": r["collected"],
            "observed": r["observed"],
            "distance_m": round(r["distance_m"], 1) if r.get("distance_m") is not None else None,
        }
        for r in (view.rows or ())
    ]
    return {
        "mode": view.mode,
        "group": view.group,
        "rows": rows,
        "counts": view.counts,
        "hidden_pedestals": view.hidden,
        "save_only": view.save_only,
        "where": view.where,
    }


# --------------------------------------------------------------------- events


def _sse(event: str | None, data: str) -> bytes:
    if event is None:
        return f": {data}\n\n".encode()
    return f"event: {event}\ndata: {data}\n\n".encode()


@router.get("/events")
async def events(request: Request) -> StreamingResponse:
    """Server-sent events: one ``save`` event per observed write, plus keepalives.

    The stream carries the trigger, never the payload. A save event says which file
    moved and when; the page decides what to refetch. That keeps this endpoint O(1) in
    the size of the world and means a browser that missed an event is one refetch, not
    one resync, behind.
    """
    watcher = request.app.state.watcher
    queue = watcher.subscribe()

    async def stream():
        try:
            if watcher.latest is not None:
                yield _sse("save", json.dumps(watcher.latest.as_dict()))
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=PING_SECONDS)
                except TimeoutError:
                    yield _sse(None, "ping")
                    continue
                yield _sse("save", json.dumps(event.as_dict()))
        finally:
            watcher.unsubscribe(queue)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
