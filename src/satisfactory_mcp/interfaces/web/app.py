"""The FastAPI application, and the two loaders every endpoint reads the world through.

``create_app`` takes them as arguments so the test suite can run the whole HTTP surface
against the committed fixture projection with no game install and no ``.sav`` on disk.
That is the only reason the seam exists, and it is worth the two parameters: without it
every endpoint test would be an integration test.

The default game loader is duplicated from ``interfaces.mcp.app`` rather than imported.
Two lines of ``lru_cache`` are cheaper than the coupling: the MCP surface and the web
surface are siblings over one domain, and a web request that reached in through the MCP
app would make the stdio server a dependency of the HTTP server for no gain.
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import asynccontextmanager
from functools import lru_cache
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from ... import config
from ...core.gamedata.loader import load_docs
from ...core.gamedata.model import GameData
from ...core.gamedata.normalize import normalize
from ...domain.world.state import WorldState, load_state
from . import api
from .watch import SaveWatcher

__all__ = ["STATIC_DIR", "app", "create_app"]

STATIC_DIR = Path(__file__).parent / "static"


@lru_cache(maxsize=1)
def _game() -> GameData:
    """Normalized game data. ~90 ms cold, so built once in-process, no disk cache."""
    return normalize(load_docs(config.docs_path()))


def create_app(
    state_loader: Callable[..., WorldState] | None = None,
    game_loader: Callable[[], GameData] | None = None,
) -> FastAPI:
    """Build the ASGI app.

    ``state_loader(save, world)`` returns the world a request asked for; ``game_loader()``
    returns the normalized docs. Both default to the real thing and are replaced wholesale
    in tests -- there is no half-injected state, which is what keeps the endpoints from
    growing a test-only branch.
    """
    load_game = game_loader or _game
    load = state_loader or (lambda save=None, world=None: load_state(load_game(), save, world))

    @asynccontextmanager
    async def lifespan(instance: FastAPI):
        await instance.state.watcher.start()
        try:
            yield
        finally:
            await instance.state.watcher.stop()

    instance = FastAPI(
        title="Satisfactory MCP web",
        summary="A JSON and map view of the same world the MCP tools plan against.",
        lifespan=lifespan,
    )
    instance.state.load_state = load
    instance.state.game = load_game
    instance.state.watcher = SaveWatcher()
    instance.include_router(api.router)

    # Mounted at the root and therefore LAST: a mount at "/" swallows every path that
    # did not already match, so the API router has to be registered above it.
    if STATIC_DIR.is_dir():
        instance.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
    return instance


#: The module-level instance ``uvicorn`` is pointed at. Built on import, which is what
#: an ASGI server expects; nothing here reads a save until a request arrives.
app = create_app()
