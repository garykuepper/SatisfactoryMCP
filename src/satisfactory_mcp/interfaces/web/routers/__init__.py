"""One module per concern, and the ORDER they are mounted in.

Each module here owns its own ``APIRouter(prefix="/api")`` instance. Several instances
deliberately: a single shared one would make registration order an accident of import
order, and registration order is not cosmetic here -- ``/openapi.json`` emits ``paths`` in
insertion order, the committed ``api-schema.d.ts`` inherits that order, and a router
mounted out of turn re-writes the generated file with a diff that means nothing.

``ALL_ROUTERS`` is therefore **append-only**. A new router goes at the end; nothing already
in the tuple ever moves. During the split this tuple is partial -- the sections not yet
extracted are still served by ``api.router``, which ``app.py`` includes AFTER these, which
is what keeps the extraction front-to-back and the path order unchanged.

**The function name is the operation id.** FastAPI's default is
``{function_name}_{path}_{method}`` -- the MODULE is not part of it -- which is exactly why
a handler can move between files for free. Rename one and the committed schema churns.
"""

from __future__ import annotations

from fastapi import APIRouter

from . import inspect, nodes, world

__all__ = ["ALL_ROUTERS"]

#: Mounted in this order, and this order is the decorator order ``api.py`` had.
ALL_ROUTERS: tuple[APIRouter, ...] = (
    world.router,
    nodes.router,
    inspect.router,
)
