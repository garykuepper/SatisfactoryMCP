"""``/api/factories``: the names the player gave, and the proposals for the rest.

Its own file because the two halves of the answer are one question -- a proposal whose
machines the player has already named is not a proposal, so the named set has to be built
before the proposed one can be filtered against it -- and because the box a label is flown
to is computed here rather than client-side: the page is never sent the anchor machines,
only their count.

WARNING: the function name is the operation_id -- rename it and the committed schema
churns. FastAPI's default id is ``{function_name}_{path}_{method}`` and ``api-schema.d.ts``
is generated off it.

**Declaration order is wire order** for the TypedDicts below, and a ``response_model``
FILTERS -- routers/floors.py writes both rules out at length.

**THE BOXES ARE TUPLES, NOT LISTS**, and that is what the page's label card is built on:
``centroid_m`` is exactly two numbers and ``bbox_m`` exactly four, so pydantic emits
``prefixItems`` and typegen turns them into ``[number, number]`` and
``[number, number, number, number]`` -- which labels.ts then indexes without a length
guard. Declared ``list[float]`` they would arrive as ``number[]``, and every ``b[3]`` in
the fly-to code would be an unchecked read the compiler waved through. ``RegionExtent`` in
routers/regions.py is the same device one file over.
"""

from __future__ import annotations

from typing import Any, TypedDict

from fastapi import APIRouter, Request

from ....domain.factories import identity as fidentity
from ....domain.spatial import geo
from ..serial import _fail, _m, _state

__all__ = ["router"]

router = APIRouter(prefix="/api")


# ------------------------------------------------------------------ factories


class FactoryRow(TypedDict):
    """A factory the player named, and the extent of the machines it is anchored to.

    ``centroid_m`` is never null: ``Label.centroid`` is a ``tuple[float, float]`` with a
    default, so a label always remembers where it was even when nothing it named is still
    standing. ``bbox_m`` IS null in exactly that case -- ``geo.bbox`` refuses to invent a
    zero box at the world centre for an empty set, and this layer does not undo the refusal.
    The pair is the honest report: a demolished factory keeps its name and its remembered
    middle, and loses only the ability to be flown to.

    ``notes`` is not nullable either. ``Label.notes`` is ``str = ""`` in the label store, so
    an unannotated factory sends the empty string, which popup() drops for the same reason
    it drops a null.
    """

    name: str
    centroid_m: tuple[float, float]
    bbox_m: tuple[float, float, float, float] | None
    machines: int
    notes: str


class ProposalRow(TypedDict):
    """A cluster the coherence pass found that no label speaks for.

    ``index`` is the position in the FULL proposal list rather than in this filtered one, so
    a ``proposal:N`` selector resolves to the same cluster here and in the MCP tools; it is
    an ``int`` because it is a list position.

    ``score`` and ``spread_m`` are floats and had to be checked rather than assumed: they are
    ``round(Proposal.cohesion, 3)`` and ``round(Candidate.spread_m, 1)``, both declared
    ``float = 0.0`` in domain/factories -- and a proposal whose weakest internal link is
    exactly 0.0 sends ``0.0``, which is what declaring them ``int`` would have truncated.
    """

    index: int
    label: str
    centroid_m: tuple[float, float]
    bbox_m: tuple[float, float, float, float] | None
    machines: int
    score: float
    spread_m: float


class FactoriesResponse(TypedDict):
    labels: list[FactoryRow]
    proposals: list[ProposalRow]


@router.get("/factories", response_model=FactoriesResponse)
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
