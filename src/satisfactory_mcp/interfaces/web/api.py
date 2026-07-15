"""The JSON endpoints. Parse the query, call a domain service, serialise.

The same shape as an MCP tool module and for the same reason: this layer decides
nothing. Every question answered here is answered by ``domain/`` -- the placement rows,
the power ledger, the node occupancy, the factory proposals -- and the only work done
in this file is turning a query string into arguments and a dataclass into JSON.

The two conventions this surface runs on -- metres at one decimal, and ``?save=``/
``?world=`` everywhere a state is read -- live with the helpers that enforce them, in
``serial.py``, which is where the error shape is written down too.

**This module is being emptied.** One concern at a time moves to ``routers/``, in the
order the handlers are decorated here, and ``app.py`` includes what is left AFTER them --
which is what keeps ``/openapi.json``'s path order, and therefore the committed
``api-schema.d.ts``, byte-identical while the file shrinks. Nothing new goes in here.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, TypedDict

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from ...domain.collectibles.service import collect_view
from ...domain.factories import floors as ffloors
from ...domain.factories import identity as fidentity
from ...domain.factories import select as fselect
from ...domain.spatial import geo
from ...domain.world.state import WorldState
from . import terrain
from .serial import _fail, _m, _state, _xyz

__all__ = ["PING_SECONDS", "router"]

#: How long a quiet SSE stream waits before sending a comment. Proxies and browsers
#: both drop a connection that has said nothing for a while, and a comment line is the
#: cheapest thing that counts as having said something.
PING_SECONDS = 15.0

router = APIRouter(prefix="/api")


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

    A proposal whose machines the player has already named is not a proposal: the
    clusterer runs over the whole world, so it re-discovers every named factory, and
    sending those rows lets a machine-generated recipe string draw itself exactly on
    top of the player's own label. Any proposal in which named anchors are the majority
    is dropped here; ``index`` stays the position in the full proposal list, so a
    ``proposal:N`` selector still resolves to the same cluster in the MCP tools.
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

    labelled = {anchor for label in st.labels.labels for anchor in label.anchors}

    proposals = []
    for index, pr in enumerate(st.proposals):
        if pr.machines and 2 * sum(1 for m in pr.machines if m in labelled) > len(pr.machines):
            continue  # already named by the player; the label speaks for it
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


# --------------------------------------------------------------------- floors
#
# **The one endpoint in this module that declares its response body, and the reason is that
# nothing has been written against it yet.** Every other endpoint here is annotated ``->
# dict``, so FastAPI publishes no response schema for it and ``/openapi.json`` types the
# rest of them ``unknown``; the map page fills that gap by hand in ``api-types.ts``, from
# observed payloads, which that file says at the top and which is honest about what it is --
# an observation, and an observation can be wrong in one direction.
#
# Converting the lot is a change to the server's whole public surface and belongs in its own
# commit. Converting THIS one is a different act: the floor view has no client at all yet, so
# there is no hand-written block to reconcile with and no drawing code whose guards are the
# evidence for a nullable. The types below are read off the serialisers three screens down
# rather than off a payload, ``npm run typegen`` turns them into the page's own types, and
# the client that gets written next is written against a declared contract instead of a
# further hand-maintained interface.
#
# TypedDict rather than a pydantic model, for the same reason the serialisers are functions
# returning dicts: this layer decides nothing and holds no state, and a class hierarchy here
# would invite behaviour into a module whose whole claim is that it has none.
#
# **Nullability is not decoration.** ``_m``, ``_xyz`` and ``_yaw`` all return ``float |
# None``, so every field they produce is declared that way even where the reference world
# has never produced a null -- a response_model is a validator as well as a schema, and a
# field declared ``float`` that arrives null is a 500 rather than a null.


class FloorDeck(TypedDict):
    """One band, identified. What a run's ``ends`` are made of."""

    platform: int
    ordinal: int
    top_m: float | None


class FloorBand(TypedDict):
    """One floor of one platform. See ``_band_json`` for what each field means."""

    ordinal: int
    top_m: float | None
    low_m: float | None
    high_m: float | None
    span_m: float | None
    pieces: int
    cells: int
    area_m2: float
    share: float
    minor: bool
    machines: list[str]
    attachments: list[str]
    deck_rows: list[int]
    machine_count: int
    attachment_count: int
    deck_row_count: int


class FloorPlatform(TypedDict):
    """One 4-connected run of foundation cells, and the bands its tops fall into."""

    index: int
    cells: int
    pieces: int
    area_m2: float
    centre_m: list[float | None]
    extent_m: list[float | None]
    clean: float
    label: str | None
    slab: int | None
    bands: list[FloorBand]


class FloorRun(TypedDict):
    """One belt chain or one pipe, keyed by the join the belt and pipe payloads carry.

    ``ends`` is always two entries, head then tail, either of which may be ``null`` where
    that end is over no deck. A pair rather than a list is what it means, and JSON has no
    pair -- so the length is a promise the prose makes and the schema cannot.
    """

    kind: str
    key: int
    pieces: int
    lift: bool
    rise_m: float | None
    riser: bool
    ends: list[FloorDeck | None]


class FloorPlacement(TypedDict):
    """One thing that is NOT on a floor, and the reason it is not."""

    instance_leaf: str
    cls: str
    name: str
    kind: str
    x_m: float | None
    y_m: float | None
    z_m: float | None
    above_terrain_m: float | None


class FloorCounts(TypedDict):
    """The shape of the answer before the rows. Nested; see ``FloorReport.counts``."""

    platforms: int
    bands: int
    runs: int
    violations: int
    #: Keyed by ``ffloors.GROUPS`` and ``ffloors.MEMBERSHIPS``. Left as open maps rather
    #: than spelled out as four fields each: the two vocabularies are the domain's, they
    #: are exported from there, and restating them here would be a second place to update.
    placements: dict[str, int]
    membership: dict[str, int]


class FloorRules(TypedDict):
    """The thresholds the answer was produced with, in the units the answer is in."""

    tile_m: float | None
    cluster_tol_m: float | None
    band_eps_m: float | None
    min_band_pieces: int
    belt_height_m: float | None
    riser_m: float | None
    terrain_tol_m: float
    minor_share: float


class FloorsResponse(TypedDict):
    """What ``/api/floors`` sends on a 200. An error is a 4xx with ``{"error": ...}``.

    Field order matters here and is the emission order below, because a response_model
    serialises in declaration order: reordering these reorders the bytes on the wire.
    """

    note: str | None
    selection: str | None
    terrain_measured: bool
    counts: FloorCounts
    platforms: list[FloorPlatform]
    #: Keyed by ``ffloors.MEMBERSHIPS``, and ``placements`` by ``ffloors.GROUPS`` less
    #: ``band`` -- what landed on a floor is listed inside its own band, by id.
    runs: dict[str, list[FloorRun]]
    placements: dict[str, list[FloorPlacement]]
    violations: list[FloorRun]
    rules: FloorRules


def _band_json(band: ffloors.Band) -> FloorBand:
    """One floor: where its deck is, how big it is, and what stands on it -- by id.

    ``machines`` and ``attachments`` are **instance ids, not geometry**, and that is the
    whole shape of this payload. The page already holds every machine, splitter, belt and
    pipe in the world from ``/api/machines``, ``/api/belts`` and ``/api/pipes``; what it
    cannot work out for itself is which floor each one is on. Re-serialising the positions
    here would ship the same 700 KB a second time so that a filter could be applied to it.

    ``deck_rows`` is the same idea for the concrete, by the only name a lightweight
    buildable has. The subsystem stores no instance ids at all -- that is what makes it
    lightweight -- so a deck is listed by its pieces' POSITIONS in ``/api/structures``,
    which every reader of that payload already has and which both sides derive from one
    ``saveio.rows`` walk in one order. Without it a client can only re-derive a deck from
    heights, and this world's 1 m and 2 m half-steps are exactly where that goes wrong.

    ``deck_rows`` is the pieces at the band's own LEVEL and ``pieces`` is the size of the
    cluster it was found in. Those are different questions and could differ where a cluster
    is wider than ``BAND_EPS_CM``; on the reference world they agree on all 93 bands, which
    is the same "the bands are exact" the epsilon sweep measured. Both are reported rather
    than reconciled, and a client that draws a deck wants ``deck_rows``.

    ``span_m`` is how much the band's own level is spread, which is 0.0 for every band on
    the reference world -- a band is a level, not a cluster. It is not the storey height:
    the distance to the floor above is the next band's ``top_m``, and a client that wants a
    ceiling can subtract two numbers it already has.
    """
    return {
        "ordinal": band.ordinal,
        "top_m": _m(band.top_cm),
        "low_m": _m(band.low_cm),
        "high_m": _m(band.high_cm),
        "span_m": _m(band.span_cm),
        "pieces": band.pieces,
        "cells": band.cells,
        "area_m2": round(band.area_m2, 1),
        # What keeps a six-cell mezzanine from being read in the same voice as a 218-cell
        # deck. The share is against the platform's own largest band, so it is a statement
        # about this platform rather than about the world.
        "share": round(band.share, 3),
        "minor": band.minor,
        "machines": band.machines,
        "attachments": band.attachments,
        "deck_rows": band.rows,
        "machine_count": len(band.machines),
        "attachment_count": len(band.attachments),
        "deck_row_count": len(band.rows),
    }


def _platform_json(platform: ffloors.Platform) -> FloorPlatform:
    """One platform, and the provenance of the decomposition that produced it."""
    return {
        "index": platform.index,
        "cells": platform.cells,
        "pieces": platform.pieces,
        "area_m2": round(platform.area_m2, 1),
        "centre_m": [_m(platform.centre_cm[0]), _m(platform.centre_cm[1])],
        "extent_m": [_m(platform.extent_cm[0]), _m(platform.extent_cm[1])],
        # The premise, per platform rather than averaged: the share of this platform's
        # foundation pieces that landed within epsilon of one of its own bands.
        "clean": round(platform.clean, 4),
        # Naming only. Neither took any part in deciding where the floors are.
        "label": platform.label,
        "slab": platform.slab,
        "bands": [_band_json(b) for b in platform.bands],
    }


def _deck_json(deck: ffloors.Deck | None) -> FloorDeck | None:
    if deck is None:
        return None
    return {"platform": deck.platform, "ordinal": deck.ordinal, "top_m": _m(deck.top_cm)}


def _run_json(run: ffloors.Run) -> FloorRun:
    """One belt chain or one pipe, keyed by the join a client already has.

    For a belt that is ``chain``, the field ``/api/belts`` puts on every piece. For a pipe
    it is the row's position in ``/api/pipes``, which is the same positional key
    ``domain.world.flow`` uses to attach a direction. Neither carries the polyline again.
    """
    return {
        "kind": run.kind,
        "key": run.key,
        "pieces": run.pieces,
        "lift": run.lift,
        "rise_m": _m(run.rise_cm),
        # Tall enough that it can only be a floor connector, which is a different claim
        # from being a lift: a quarter of lift chains are belt-height jogs on one deck.
        "riser": run.riser,
        "ends": [_deck_json(d) for d in run.ends],
    }


def _placement_json(st: WorldState, placement: ffloors.Placement) -> FloorPlacement:
    """One thing that is NOT on a floor, and the reason it is not."""
    return {
        "instance_leaf": placement.instance,
        "cls": placement.cls,
        "name": st.game.building_name(placement.cls),
        "kind": placement.kind,
        **_xyz(placement.pos_cm),
        "above_terrain_m": (
            None if placement.above_terrain_m is None else round(placement.above_terrain_m, 1)
        ),
    }


@router.get("/floors", response_model=FloorsResponse)
def floors_view(
    request: Request,
    factory: str | None = None,
    platform: int | None = None,
    save: str | None = None,
    world: str | None = None,
) -> Any:
    """What is built, one storey at a time: the floor decomposition of a world.

    Nothing in the save says "floor". ``domain.factories.floors`` recovers them from the
    geometry -- 4-connected platforms of 8 m foundation cells, then a per-platform cluster
    of deck heights -- and every constant it uses was measured before it was written. This
    endpoint parses the query, calls it once, and rounds.

    **It ships ids, not geometry, and that is the design.** A client already has every
    machine, splitter, belt and pipe from the four endpoints above; the one thing it cannot
    derive is which floor each of them is on. So a band lists ``machines`` and
    ``attachments`` as instance leaves, and a run is keyed by its belt ``chain`` or its pipe
    row position -- the joins those payloads already carry. Sending the coordinates again
    would double 1.3 MB so that a filter could be applied to the copy.

    **The runs are grouped by what they do to a floor**, not listed flat:

    * ``same-deck`` -- both ends over one band. 84.8% of belt runs, and the set a floor
      filter draws.
    * ``connector`` -- the ends are on two different bands. This is how you leave a floor,
      and it is where the lifts and risers are.
    * ``terrain`` -- neither end is over a deck. 44.5% of pipes, because plumbing hugs the
      ground.
    * ``mixed`` -- one end on a deck, one on the ground.

    **``placements`` is only what did NOT land on a floor.** Things that did are listed by
    id inside their own band, so listing them here as well would be the same 1,252 rows
    twice. What is here is the three honest ways of not being on a floor: ``exempt`` (a
    miner stands on a resource node and a water extractor on water -- by native class, not
    by a substring), ``terrain`` (measured against the heightfield) and ``off-deck``.

    **``terrain_measured`` says whether the ground was consulted at all.** The 1 m
    heightfield is derived from the reader's own game install and most machines have none,
    in which case nothing can be in the ``terrain`` group and an empty one would otherwise
    read as "nothing is on the ground here".

    ``?factory=`` takes a label the player gave a factory, or any selector the MCP tools
    take, and narrows the answer to the platforms that factory stands on. ``?platform=`` is
    the index this endpoint hands out, which is stable across calls. Either narrows
    placements and runs to that footprint -- including the ones underneath it, since "what
    is under this deck" is part of the question.

    A save too old to carry ``FGLightweightBuildableSubsystem`` is a **200 with a
    ``note``**, not an error and not an empty list: the world has floors, this file cannot
    show them, and those are different sentences.
    """
    try:
        st = _state(request, save, world)
    except Exception as exc:
        return _fail(f"could not read save: {exc}", 404)

    try:
        report = ffloors.floor_decomposition(
            st, platform=platform, label=factory, terrain_field=terrain.field()
        )
    except fselect.SelectorError as exc:
        return _fail(str(exc))

    if report.note and (platform is not None or factory is not None) and not report.platforms:
        # A selection that matched nothing is a bad request; a save that cannot carry the
        # data at all is not, and falls through to the 200 with its note below.
        return _fail(report.note, 404)

    return {
        "note": report.note,
        "selection": report.selection,
        "terrain_measured": report.terrain_measured,
        "counts": report.counts(),
        "platforms": [_platform_json(p) for p in report.platforms],
        "runs": {
            membership: [_run_json(r) for r in report.runs_of(membership)]
            for membership in ffloors.MEMBERSHIPS
        },
        "placements": {
            group: [_placement_json(st, p) for p in report.group(group)]
            for group in ffloors.GROUPS
            if group != "band"
        },
        # A riser that lands both ends on one band cannot happen -- 0 of 89 on the reference
        # world, 0 of 75 on the oldest save that can carry the data -- so one here is a
        # symptom of the decomposition drifting, and it is reported rather than swallowed.
        "violations": [_run_json(r) for r in report.violations],
        # The thresholds the answer was produced with, in the units the answer is in, so a
        # reader never has to go and look up what "clean" was measured against.
        "rules": {
            "tile_m": _m(ffloors.CELL_CM),
            "cluster_tol_m": _m(ffloors.CLUSTER_TOL_CM),
            "band_eps_m": _m(ffloors.BAND_EPS_CM),
            "min_band_pieces": ffloors.MIN_BAND_PIECES,
            "belt_height_m": _m(ffloors.BELT_HEIGHT_CM),
            "riser_m": _m(ffloors.RISER_CM),
            "terrain_tol_m": ffloors.TERRAIN_TOL_M,
            "minor_share": ffloors.MINOR_SHARE,
        },
    }


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
