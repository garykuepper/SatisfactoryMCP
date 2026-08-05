"""``/api/events``: the server-sent event stream, and the only route that is not JSON.

The one endpoint that holds a connection open, and the one with no ``?save=``/``?world=``:
it never reads a world. It subscribes to the ``SaveWatcher`` the app's lifespan starts,
through ``request.app.state`` rather than through ``Depends`` -- a dependency would put a
parameter into ``/openapi.json`` for a stream that has no schema.

WARNING: the function name is the operation_id -- renaming it churns the committed schema.

Wire rules: docs/web-wire.md.
"""

from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

__all__ = ["PING_SECONDS", "router"]

#: How long a quiet SSE stream waits before sending a comment line. Proxies and browsers
#: both drop a connection that has said nothing for a while.
PING_SECONDS = 15.0

router = APIRouter(prefix="/api")


# --------------------------------------------------------------------- events


def _sse(event: str | None, data: str) -> bytes:
    if event is None:
        return f": {data}\n\n".encode()
    return f"event: {event}\ndata: {data}\n\n".encode()


@router.get("/events")
async def events(request: Request) -> StreamingResponse:
    """Server-sent events: one ``save`` event per observed write, plus keepalives.

    The stream carries the trigger, never the payload. A save event says which file moved
    and when; the page decides what to refetch, so a browser that missed one is a refetch
    behind rather than a resync behind.
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
