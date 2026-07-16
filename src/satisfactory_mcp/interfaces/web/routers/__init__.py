"""One module per concern, and the ORDER they are mounted in.

Each module here owns its own ``APIRouter(prefix="/api")`` instance. Several instances
deliberately: a single shared one would make registration order an accident of import
order, and registration order is not cosmetic here -- ``/openapi.json`` emits ``paths`` in
insertion order, the committed ``api-schema.d.ts`` inherits that order, and a router
mounted out of turn re-writes the generated file with a diff that means nothing.

``ALL_ROUTERS`` is therefore **append-only**. A new router goes at the end; nothing already
in the tuple ever moves. It is now the WHOLE surface -- ``api.py`` is deleted and ``app.py``
includes nothing but this tuple, in this order -- which is what the split was front-to-back
for: the last append left ``/openapi.json``'s path order exactly where it started.

**The function name is the operation id.** FastAPI's default is
``{function_name}_{path}_{method}`` -- the MODULE is not part of it -- which is exactly why
a handler can move between files for free. Rename one and the committed schema churns.
"""

from __future__ import annotations

from fastapi import APIRouter

from . import (
    collectibles,
    events,
    factories,
    floors,
    inspect,
    nodes,
    placements,
    power,
    regions,
    routes_layer,
    storage,
    tiles,
    world,
)

__all__ = ["ALL_ROUTERS"]

#: Mounted in this order, and this order is the decorator order ``api.py`` had.
#:
#: ``regions`` before ``tiles`` because that is what the surface says: the baseline manifest
#: records ``/api/regions`` at index 4 of ``/openapi.json``'s ``paths``, ahead of
#: ``/api/mapimage`` and the two ``/api/maptiles`` formats. The FILE order in ``api.py`` was
#: never the guide -- the tile constants and helpers sat above ``regions()`` under a section
#: banner that named it -- and only the DECORATOR order registers a route.
#:
#: The four appended after ``tiles`` sit at indices 8..13 of that same manifest, in one
#: unbroken run: ``/api/machines``, ``/api/structures`` (``placements``), ``/api/belts``,
#: ``/api/pipes`` (``routes_layer``), ``/api/storage``, ``/api/power``. Two endpoints per file
#: for the first two, so the tuple's order is checked against the PATH order rather than
#: against the file list -- the same rule ``regions``/``tiles`` was settled by.
#:
#: The last four are indices 14..17, the tail of that same manifest and one endpoint each:
#: ``/api/factories``, ``/api/floors``, ``/api/collectibles``, ``/api/events``. With them the
#: tuple is the whole surface, and ``path_order`` is the list this order produces.
ALL_ROUTERS: tuple[APIRouter, ...] = (
    world.router,
    nodes.router,
    inspect.router,
    regions.router,
    tiles.router,
    placements.router,
    routes_layer.router,
    storage.router,
    power.router,
    factories.router,
    floors.router,
    collectibles.router,
    events.router,
)
