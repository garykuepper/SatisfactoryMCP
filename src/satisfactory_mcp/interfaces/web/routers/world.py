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


# THE LAST ENDPOINT TO SAY WHAT IT SENDS, because saying so changed what it sends.
#
# Its rows are not built by this layer: ``asdict(w)`` hands over the loader's ``World``,
# whose ``saves`` are the sidecar's save HEADERS -- thirteen keys apiece, the same thirteen
# ``/api/summary`` passes through as ``header`` -- and ``unsupported`` is whatever
# ``scan_saves`` chose to say about a file it could not read. A faithful model for that is
# ``dict[str, Any]``, which types the page ``unknown`` and buys it nothing. The model below
# is the USEFUL one instead, and a response_model FILTERS: declaring the five fields the
# picker reads DELETES the other eight from every save row on the wire. That is a body
# change, made deliberately and in its own commit, after checking who reads the endpoint:
# the map page is its only consumer -- worlds.ts fetches it, state.ts stores it, and the
# fields below are exactly the frontend's own hand-written claim about the rows, read off
# real payloads back when the server declared nothing. The MCP surface never sees this
# route at all; it calls ``projection.list_worlds`` in-process.
#
# The handler still forwards the loader's dicts untouched. The model is what trims them,
# which is the same division of labour every other router here has: the layer underneath
# says everything it knows, and the declared body is the contract.


class SaveRow(TypedDict):
    """One save file, cut to the five keys the picker reads -- of the header's thirteen.

    The eight deleted, by the filter that this model is: ``save_identifier`` (already
    spent server-side -- it is how ``list_worlds`` grouped the rows, and ``world_id``
    carries it), ``save_header_version``, ``save_version``, ``build_version``,
    ``save_datetime_ticks``, ``is_modded``, ``is_creative`` and ``size``. Nothing on the
    page ever read any of them; a client that wants a save's full header asks
    ``/api/summary``, which forwards it whole.

    ``path`` is the pin (``?save=`` takes it back verbatim), ``filename`` is what the pin
    is spelled as in the URL fragment, ``mtime_ns`` orders the dropdown, and
    ``play_duration_s`` is an ``int`` because ``pioneersav``'s ``SaveInfo`` declares it
    one -- ``float`` here would rewrite the bytes on the wire.

    Declaration order is wire order (see routers/floors.py), and it is the header's own
    order with the deleted keys closed up.
    """

    path: str
    filename: str
    session_name: str
    play_duration_s: int
    mtime_ns: int


class WorldRow(TypedDict):
    """One world: ``asdict(World)``, plus the newest save's headline figures hoisted on.

    The three hoisted fields are built by the handler, not forwarded: ``mtime`` is the
    newest save's ``mtime_ns`` in SECONDS (a float, and the one place this surface speaks
    epoch seconds -- the picker's "newest first" is the server's sort, this is what it
    sorted by), and ``play_duration_s`` is the maximum across the world's saves, an
    ``int`` for the same reason the row's is.
    """

    world_id: str
    session_name: str
    saves: list[SaveRow]
    mtime: float
    newest_filename: str
    play_duration_s: int


class UnsupportedFile(TypedDict):
    """A file the scan could not read: which one, and the parser's own reason.

    The sidecar says five things about such a file; the page prints these two in its
    "no readable saves" diagnosis and nothing reads the rest, so ``path``, ``mtime_ns``
    and ``size`` are filtered off the wire on the same terms as the save rows' eight.
    """

    filename: str
    reason: str


class WorldsResponse(TypedDict):
    """What ``/api/worlds`` sends on a 200. An error is a 4xx with ``{"error": ...}``.

    Both keys are always present together: the only reply without them is the error
    branch, which returns a ``JSONResponse`` and skips this model entirely.
    """

    worlds: list[WorldRow]
    unsupported: list[UnsupportedFile]


@router.get("/worlds", response_model=WorldsResponse)
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
