"""The serialisation vocabulary every JSON endpoint speaks, and the two conventions.

Split out of ``api.py`` so that a router module can be read on its own: these six helpers
were used by every section of that file, so every reader of any one endpoint had to know
them anyway. They decide nothing -- each one is a unit conversion, a refusal, or a lookup
-- and that is why they are shared rather than duplicated per router.

Two conventions run through the whole surface:

* **Metres, one decimal.** The save stores centimetres. Every coordinate that leaves
  this layer has been divided by 100 and rounded, exactly as the text presenters do,
  because a reader who sees two units in one product will eventually mix them.
* **``?save=`` and ``?world=`` everywhere a state is read**, so a page can pin itself
  to one save while the game keeps autosaving over another. ``_state`` is how a handler
  spends those two parameters, and it raises whatever the loader raises.

Errors are ``{"error": "..."}`` with a 4xx, never a 200 with an empty list: a browser
that cannot tell "no nodes" from "no save" will draw an empty map and say nothing.
That is what ``_fail`` is, and it is the only shape an error takes here.
"""

from __future__ import annotations

from typing import Any, TypedDict

from fastapi import Request
from fastapi.responses import JSONResponse

from ...domain.spatial import regions as spatial_regions
from ...domain.world.state import WorldState

__all__ = ["Region", "_fail", "_label_json", "_m", "_state", "_xyz", "_yaw"]


class Region(TypedDict):
    """What ``_label_json`` sends: a region lookup that never arrives without its doubt.

    Declared HERE rather than in a router because two routers publish it -- ``/api/nodes``
    hangs one off every node row and ``/api/inspect`` answers with one for the clicked
    point -- and they must publish the SAME schema. Two identical TypedDicts of this name in
    two modules would be two components with a mangled name apiece, which is the generated
    ``api-schema.d.ts`` inheriting a copy-paste.

    ``name`` is not nullable and the field is not optional: the whole dict is ``None`` for
    ocean and off-map, which is ``_label_json``'s own refusal and the one thing this layer
    must not soften. ``accuracy_m`` is an ``int`` because ``Label.accuracy_m`` is -- declaring
    it ``float`` would validate 256 into 256.0 and rewrite the bytes on the wire.

    Declaration order is wire order; see the note on ``FloorsResponse`` in routers/floors.py.
    """

    name: str
    confidence: str
    accuracy_m: int
    certain: bool
    text: str


def _m(value: float | None) -> float | None:
    """Centimetres to metres, one decimal. The unit rule, in one place."""
    return None if value is None else round(float(value) / 100.0, 1)


def _xyz(pos: Any) -> dict[str, float | None]:
    """A projection ``pos`` triple as named metre fields."""
    if not pos:
        return {"x_m": None, "y_m": None, "z_m": None}
    p = list(pos) + [None, None, None]
    return {"x_m": _m(p[0]), "y_m": _m(p[1]), "z_m": _m(p[2])}


def _yaw(value: Any) -> float | None:
    """A placement's rotation about world Z, degrees, one decimal.

    Positive turns +X towards +Y, so it is directly comparable with ``atan2(dy, dx)`` over
    two ``pos`` values -- which is how the projection's own convention was verified, and
    the one sentence a client needs to draw a rotated footprint.

    ``None``, never 0.0, when the projection carries no yaw at all: schema 12 added the
    field, and an absent one means "this projection predates it", which is a different
    claim from "this thing is axis-aligned". Both end up drawn the same way, and only one
    of them is a measurement.

    Rounded like every other number that leaves this module. 0.1 degrees swings the corner
    of an 8 m foundation by 7 mm.
    """
    if value is None:
        return None
    try:
        return round(float(value), 1)
    except (TypeError, ValueError):
        return None


def _fail(message: str, status: int = 400) -> JSONResponse:
    return JSONResponse({"error": message}, status_code=status)


def _state(request: Request, save: str | None, world: str | None) -> WorldState:
    """The world a request is asking about. Raises whatever the loader raises."""
    return request.app.state.load_state(save, world)


def _label_json(label: spatial_regions.Label) -> Region | None:
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
