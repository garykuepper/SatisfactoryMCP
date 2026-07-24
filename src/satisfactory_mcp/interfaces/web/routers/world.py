"""The two endpoints a page opens with: which worlds exist, and what one of them is.

``/api/worlds`` is the only route on the whole surface that does not read through the
injected loader -- it scans the save directory itself, because the picker's job is to say
what is there before anything has been chosen.

WARNING: the function name is the operation_id -- rename it and the committed schema
churns. FastAPI's default id is ``{function_name}_{path}_{method}`` and ``api-schema.d.ts``
is generated off it.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any, TypedDict

from fastapi import APIRouter, Request

from ....core.saveio import projection as proj
from ..serial import _fail, _state, _xyz

__all__ = ["router"]

router = APIRouter(prefix="/api")


# --------------------------------------------------------------------- worlds


# THE ONE ENDPOINT HERE WITH NO ``response_model``, and it is not an oversight.
#
# Its rows are not built by this layer: ``asdict(w)`` hands over the loader's ``World``,
# whose ``saves`` are the sidecar's save HEADERS -- thirteen keys apiece, the same thirteen
# ``/api/summary`` passes through as ``header`` -- and ``unsupported`` is whatever
# ``scan_saves`` chose to say about a file it could not read. Declaring those faithfully
# means ``dict[str, Any]``, which types the page ``unknown`` and buys it nothing; declaring
# them as the five fields the picker reads means a response_model DELETING the other eight
# from the wire, which is a body change and not a typing item.
#
# There is no total=False middle either, and it was measured rather than assumed: pydantic
# serialises a TypedDict in DECLARATION order and drops what is absent, so a partial row is
# re-keyed rather than passed through -- the capture harness's own stub row
# (``filename, mtime_ns, play_duration_s``) comes back in a different order from the one it
# went in as, which the byte-identity gate reads as the API moving.
#
# So ``WorldsResponse``/``WorldRow``/``SaveRow`` stay hand-written in ``api-types.ts``,
# where an observation of an opaque dict belongs and where that file's own header already
# says what such a declaration is worth. Converting this endpoint means changing what it
# SENDS -- building the picker's five fields explicitly instead of forwarding a header --
# and that is a commit about the body, with its own before-and-after.
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


class PlayerPosition(TypedDict):
    """Where the player last stood, or three nulls -- never a missing branch.

    ``_xyz`` answers ``{x_m: None, y_m: None, z_m: None}`` for a save with no pawn (a
    dedicated-server world has none) rather than dropping out, and the page branches on
    ``x_m === null`` to decide whether there is a you-are-here to draw at all. So all three
    are nullable and all three go null together.
    """

    x_m: float | None
    y_m: float | None
    z_m: float | None


class GeneratorTotal(TypedDict):
    """One generator class, counted and summed. A value of ``PowerSummary.by_generator``."""

    name: str
    count: int
    mw: float


class PowerSummary(TypedDict):
    """``WorldState.power_report()`` verbatim, because a response_model FILTERS.

    The page reads three of these eleven fields. The other eight are declared anyway --
    leaving one out would DELETE it from the wire, which is a change to the body and not a
    change to its types. That is the whole hazard of this work item, and this is the shape
    it takes on the endpoint with the biggest payload nobody looks at.

    Declaration order is the order ``domain/power/report.py`` returns them in; that literal
    is the wire order and this is a transcription of it.

    ``utilisation`` is never null: it is ``measured / draw``, and ``1.0`` when nothing draws
    at all -- a factory with nothing built is fully utilised in the only sense the ratio has.
    """

    generation_mw: float
    draw_mw: float
    headroom_mw: float
    measured_draw_mw: float
    measured_headroom_mw: float
    monitored: int
    unmonitored: int
    utilisation: float
    by_generator: dict[str, GeneratorTotal]
    #: Generator classes Docs carries no entry for -- the two biomass burners. Sorted, and
    #: empty on a world that has none, which is a measurement rather than a gap.
    unmodellable: list[str]
    paused_count: int


class ProgressionSummary(TypedDict):
    """``WorldState.progression()`` verbatim, on the same terms as ``PowerSummary``.

    ``game_phase`` and ``target_phase`` are ``null`` on the pre-1.0 saves that carry no
    phase at all, which is why the header omits the segment rather than printing a hole;
    ``highest_complete_tier`` is ``null`` when not one tier is finished, which is different
    from tier 0 and there is no tier 0.

    ``milestones_by_tier`` is keyed by the tier NUMBER, which JSON spells as a string. Left
    as open maps rather than nine fields: the tiers are the game's and restating them here
    would be a second place to update when a game update adds one.
    """

    game_phase: str | None
    target_phase: str | None
    phase_costs_remaining: dict[str, dict[str, int]]
    milestones_by_tier: dict[int, str]
    highest_complete_tier: int | None
    purchased_schematics: int
    available_recipes: int


class SummaryResponse(TypedDict):
    """What ``/api/summary`` sends on a 200. An error is a 4xx with ``{"error": ...}``.

    ``header`` is the save header the sidecar read, forwarded whole and typed as the open
    map it is. Thirteen keys today and it is the SIDECAR's contract rather than this
    layer's: spelling them out here would put the save format's own field list in the web
    adapter, and a response_model would then delete any fourteenth the parser learns to
    read. The page uses one of them, ``session_name``, and reaches into an open map to get
    it -- which is the honest cost of not restating somebody else's schema.

    ``power`` and ``progression`` are the opposite case and are spelled out in full: both
    are built by a literal ``return {...}`` in the domain with a fixed key set, so
    transcribing them costs nothing and filters nothing.

    Declaration order is wire order; see routers/floors.py.
    """

    header: dict[str, Any]
    age_note: str
    power: PowerSummary
    progression: ProgressionSummary
    player: PlayerPosition


@router.get("/summary", response_model=SummaryResponse)
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
        # Where the player last stood, so the map can draw a you-are-here. Nulls when
        # the save has no pawn, which _xyz already says honestly.
        "player": _xyz(st.player_position()),
    }
