"""``/api/factories``: the names the player gave, and the proposals for the rest.

Its own file because the two halves of the answer are one question -- a proposal whose
machines the player has already named is not a proposal, so the named set has to be built
before the proposed one can be filtered against it -- and because the box a label is flown
to is computed here rather than client-side: the page is never sent the anchor machines,
only their count.

WARNING: the function name is the operation_id -- rename it and the committed schema
churns. FastAPI's default id is ``{function_name}_{path}_{method}`` and ``api-schema.d.ts``
is generated off it.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from ....domain.factories import identity as fidentity
from ....domain.spatial import geo
from ..serial import _fail, _m, _state

__all__ = ["router"]

router = APIRouter(prefix="/api")


# ------------------------------------------------------------------ factories


@router.get("/factories")
def factories(request: Request, save: str | None = None, world: str | None = None) -> Any:
    """Named factories and the coherence-scored proposals for the unnamed rest.

    Each row carries ``bbox_m`` -- ``[x_min, y_min, x_max, y_max]`` in metres, game axes
    -- alongside its centroid, because a centroid alone cannot frame a viewport. The map
    turns a label into a button that flies to its factory, and "fly to the mean of 50
    machines" is not the same request as "show me all 50": the first picks a zoom out of
    the air, the second is decided by the extent. Computed here rather than client-side
    because the client is never sent the anchor machines, only their count.

    ``null`` when nothing in the set is still standing -- ``geo.bbox`` refuses to invent
    a zero box at the world centre, and so does this. A label whose machines were all
    demolished keeps its name and its remembered centroid; what it loses is the ability
    to be flown to, which is the honest report.

    A proposal whose machines the player has already named is not a proposal: the
    clusterer runs over the whole world, so it re-discovers every named factory, and
    sending those rows lets a machine-generated recipe string draw itself exactly on
    top of the player's own label. Any proposal in which named anchors are the majority
    is dropped here; ``index`` stays the position in the full proposal list, so a
    ``proposal:N`` selector still resolves to the same cluster in the MCP tools.
    """
    try:
        st = _state(request, save, world)
    except Exception as exc:
        return _fail(f"could not read save: {exc}", 404)

    placed = fidentity.positions(st.projection)

    def _bbox_m(machines) -> list[float] | None:
        box = geo.bbox([placed[m][:2] for m in machines if m in placed])
        return None if box is None else [_m(v) for v in box]

    named = [
        {
            "name": label.name,
            "centroid_m": [_m(label.centroid[0]), _m(label.centroid[1])],
            "bbox_m": _bbox_m(label.anchors),
            "machines": len(label.anchors),
            "notes": label.notes,
        }
        for label in sorted(st.labels.labels, key=lambda x: -len(x.anchors))
    ]

    labelled = {anchor for label in st.labels.labels for anchor in label.anchors}

    proposals = []
    for index, pr in enumerate(st.proposals):
        if pr.machines and 2 * sum(1 for m in pr.machines if m in labelled) > len(pr.machines):
            continue  # already named by the player; the label speaks for it
        cand = fidentity.describe(pr.machines, st.graph, st.game, st.projection, "proposal")
        proposals.append(
            {
                "index": index,
                "label": cand.name_hint(),
                "centroid_m": [_m(cand.centroid[0]), _m(cand.centroid[1])],
                "bbox_m": _bbox_m(pr.machines),
                "machines": pr.size,
                "score": round(pr.cohesion, 3),
                "spread_m": round(cand.spread_m, 1),
            }
        )
    return {"labels": named, "proposals": proposals}
