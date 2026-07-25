"""``/api/collectibles``: slugs, mercer spheres and the rest, filtered as the tool filters.

Thin on purpose, and the thinness is the point: every refusal this endpoint makes is
``collect_view``'s -- unknown mode, retired group, and the one that matters, that
``mode=remaining`` needs the generated placement table and without it the honest answer is
a refusal rather than a shorter list. Duplicating any of that here would give the map and
the MCP tool two different opinions about the same question.

WARNING: the function name is the operation_id -- rename it and the committed schema
churns. FastAPI's default id is ``{function_name}_{path}_{method}`` and ``api-schema.d.ts``
is generated off it.

**Declaration order is wire order** for the TypedDicts below, and a ``response_model``
FILTERS -- routers/floors.py writes both rules out at length. That filtering is why
``CollectiblesResponse`` spells out all seven top-level keys rather than the one the page
draws: ``api-types.ts`` declared ``rows`` alone, because ``rows`` is all markers.ts reads,
and a model that faithful would have DELETED the other six from the wire.
"""

from __future__ import annotations

from typing import Any, Literal, TypedDict

from fastapi import APIRouter, Request

from ....domain.collectibles.service import collect_view
from ..serial import _fail, _state, _xyz

__all__ = ["router"]

router = APIRouter(prefix="/api")


# ---------------------------------------------------------------- collectibles


class CollectibleRow(TypedDict):
    """One map placement, and what this save says about it.

    The three coordinates are NOT nullable, and this is the one field group here that had to
    be read back rather than copied: ``_xyz`` can answer three nulls, but its argument is
    ``removed.placements``' own ``(row["x"], row["y"], row["z"])`` off the generated
    placement table, where a row without all three does not exist. The same shape of claim
    ``/api/nodes`` and ``/api/structures`` already make about their triples.

    ``observed`` is the placement table's scan of every save on disk rather than of the
    loaded one, and it is null for two different reasons that mean the same thing here: a
    row this save has COLLECTED gets no observed state at all, and a state this build does
    not know is answered ``None`` rather than with a nearest guess.

    ``distance_m`` is populated only by ``mode=nearest``, which is the one mode that
    resolves an origin -- so it is null on every row of every other mode, and null is the
    honest "not measured from anywhere" rather than a zero.
    """

    category: str
    name: str
    x_m: float
    y_m: float
    z_m: float
    collected: bool
    observed: str | None
    distance_m: float | None


class CollectiblesResponse(TypedDict):
    """The view ``collect_view`` decided, in emission order.

    ``mode`` is a CLOSED union and ``kind`` on ``/api/crates`` deliberately is not, which is
    the same distinction read from two sides. This one is closed because the guard that
    fills it is next door and exhaustive: ``collect_view`` refuses anything outside these
    four before a view exists at all, so a fifth mode cannot reach the wire without
    ``service.py`` changing -- and then it should be loud here. A crate's ``kind`` comes
    from the PROJECTION, which is versioned and read from disk, so a fourth value must be
    served whole rather than 500ed on.

    ``rows`` is a list and never null. The view's own ``rows`` is ``None`` for
    ``mode=census``, which counts off the removed list instead of listing anything, and the
    handler spells that as the empty list it has always sent.

    ``counts`` is an open map on purpose: its keys are observed STATES -- ``standing``,
    ``never_streamed``, ``gone_in_a_later_save``, ``collected`` -- and a save whose rows are
    all in one of them sends a one-key object. Declaring the four would make a missing key
    look like a schema, when it is a tally. ``where`` is ``str`` and never null: it is
    ``""`` for every mode that measures no distance, which is the same "" the view defaults.
    """

    mode: Literal["census", "collected", "remaining", "nearest"]
    group: str | None
    rows: list[CollectibleRow]
    counts: dict[str, int]
    hidden_pedestals: int
    save_only: bool
    where: str


@router.get("/collectibles", response_model=CollectiblesResponse)
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
