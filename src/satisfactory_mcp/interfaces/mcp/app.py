"""The MCP app object, and the state accessors every tool group needs.

Split out so tool modules can register against one ``mcp`` without importing each other.
``server`` imports the tool packages purely for their decorator side effects.

The shared resolvers now live with their domains -- ``domain.factories.resolve`` and
``domain.spatial.origin`` -- and are re-bound here only so the old private names keep
resolving for the tool modules that spell them.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated

from mcp.server.fastmcp import FastMCP
from pydantic import Field

from ... import config
from ...core.gamedata.loader import load_docs
from ...core.gamedata.model import GameData
from ...core.gamedata.normalize import normalize
from ...domain.factories.resolve import resolve_factory as _resolve_factory
from ...domain.planning.scenario import resolve_item
from ...domain.spatial.origin import player_xy as _player_xy
from ...domain.spatial.origin import resolve_origin as _origin_for
from ...domain.world.state import WorldState, load_state

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


#: The domain resolvers under their old private names, for ``server`` and for tests.
_ = (_resolve_factory, _player_xy, _origin_for)
