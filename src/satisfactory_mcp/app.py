"""The MCP app object, and the resolvers every tool group needs.

Split out so tool modules can register against one ``mcp`` without importing each other.
``server`` imports the tool packages purely for their decorator side effects.

The resolvers live here rather than with the tools that own them because more than one
group needs each: ``_resolve_factory`` is used by the factory tools, by ``diff_vs_save``
and by ``plan_layout``, and ``_origin_for`` by the map and node tools.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated

from mcp.server.fastmcp import FastMCP
from pydantic import Field

from . import config
from .docs.loader import load_docs
from .docs.model import GameData
from .docs.normalize import normalize
from .planning.scenario import resolve_item
from .save.state import WorldState, load_state
from .spatial import geo

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


def _player_xy(st) -> tuple[float, float] | None:
    """Player XY for the near:me selector, or None if the save has no pawn."""
    here = st.player_position() if st else None
    return (here[0], here[1]) if here else None


def _resolve_factory(st, factory: str):
    """A label name, a selector, or a proposal index -- in that order.

    Label first because that is what a player types. Falling through to the selector
    grammar means ``factory_query("proposal:3", ...)`` works before anything is named.
    """
    from .graph import select as gsel

    label = st.labels.find(factory)
    if label is not None:
        alive = set(st.graph.machines())
        return label.name, [m for m in label.anchors if m in alive]
    try:
        picked = gsel.select_machines(
            [factory],
            st.graph,
            st.game,
            st.projection,
            st.labels,
            structures=st.structures,
            proposals=st.proposals,
        )
    except gsel.SelectorError as exc:
        known = ", ".join(x.name for x in st.labels.labels) or "(none named yet)"
        raise gsel.SelectorError(f"{exc}. Named factories: {known}") from exc
    return factory, picked


def _origin_for(st, near: str) -> tuple[tuple[float, float], str]:
    """Resolve a location: "x,y" in metres, "me", or the name of a named factory.

    A factory name is the useful one now that factories exist -- "nearest coal to the
    coal powerplant" is the question actually being asked, and hand-copying a centroid
    out of another tool's output is how the wrong coordinate gets used.
    """
    text = near.strip()
    if "," in text:
        try:
            x_m, y_m = (float(v) for v in text.split(",", 1))
        except ValueError as exc:
            raise ValueError(f"{near!r} is not an x,y pair in metres") from exc
        return (x_m * 100.0, y_m * 100.0), f"{int(x_m)},{int(y_m)}"

    if text.casefold() in ("me", "player", "here"):
        here = _player_xy(st)
        if here is None:
            raise ValueError("this save has no player pawn, so 'me' cannot be resolved")
        return here, "you"

    label = st.labels.find(text) if st else None
    if label is None:
        known = ", ".join(x.name for x in st.labels.labels) if st else ""
        raise ValueError(
            f"{near!r} is neither an x,y pair, 'me', nor a named factory"
            + (f". Named: {known}" if known else "")
        )
    pos = {}
    for key in ("machines", "extractors", "generators"):
        for record in st.projection.get(key, ()):
            if record.get("pos"):
                pos[record["instance"].rsplit(".", 1)[-1]] = record["pos"]
    points = [pos[m][:2] for m in label.anchors if m in pos]
    if not points:
        raise ValueError(f"{label.name!r} has no machines left to centre on")
    return geo.centroid(points), label.name
