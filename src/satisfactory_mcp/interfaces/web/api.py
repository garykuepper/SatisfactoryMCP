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
from ...core.saveio import projection as proj
from ...domain.collectibles.service import collect_view
from ...domain.factories import identity as fidentity
from ...domain.spatial import nodes as spatial_nodes
from ...domain.spatial import regions as spatial_regions
from ...domain.world.state import WorldState

__all__ = ["DEFAULT_MAP_BOUNDS_M", "PING_SECONDS", "router"]

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


def _building_name(st: WorldState, cls: str) -> str:
    building = st.game.buildings.get(cls)
    return building.name if building else cls


def _record_row(st: WorldState, row: dict) -> dict:
    """One machine/extractor/generator, flattened for the map.

    ``clock`` and ``paused`` are read with ``get``: the projection only carries them
    for the records that have them, and an extractor at 250% and a constructor with no
    overclock property must both come out of here without a KeyError.
    """
    return {
        "instance_leaf": str(row.get("instance", "")).rsplit(".", 1)[-1],
        "cls": row.get("cls"),
        "name": _building_name(st, row.get("cls") or ""),
        **_xyz(row.get("pos")),
        "recipe": row.get("recipe"),
        "clock": row.get("clock"),
        "paused": bool(row.get("paused", False)),
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
    """
    try:
        st = _state(request, save, world)
    except Exception as exc:
        return _fail(f"could not read save: {exc}", 404)
    try:
        table = spatial_nodes.load_nodes()
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
            }
        )
    return {"nodes": out, "resource": resource, "occupied": sum(1 for r in out if r["occupied"])}


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


# ------------------------------------------------------------------ factories


@router.get("/factories")
def factories(request: Request, save: str | None = None, world: str | None = None) -> Any:
    """Named factories and the coherence-scored proposals for the unnamed rest."""
    try:
        st = _state(request, save, world)
    except Exception as exc:
        return _fail(f"could not read save: {exc}", 404)

    named = [
        {
            "name": label.name,
            "centroid_m": [_m(label.centroid[0]), _m(label.centroid[1])],
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
