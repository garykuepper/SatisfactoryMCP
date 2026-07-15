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
from typing import Any

from fastapi import APIRouter, Request

from ....core.saveio import projection as proj
from ..serial import _fail, _state, _xyz

__all__ = ["router"]

router = APIRouter(prefix="/api")


# --------------------------------------------------------------------- worlds


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


@router.get("/summary")
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
