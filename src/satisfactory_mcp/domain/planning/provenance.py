"""What a stored plan's source selectors resolved to, and whether they still do.

A stored plan keeps the REQUEST and re-solves on recall, which is what makes it answer
about the world as it is now (see ``store``). ``plan_id`` catches the world moving --
an unlock, a freed node, a new building. It does NOT catch the selectors moving, and
they can: ``sources: ["region:Spire Coast"]`` is a name looked up in a table this
repository generates, and when that table was re-derived from the game's own
``FGMapAreaTexture`` the name went from 51 nodes to 18. Nothing about the plan changed;
the plan simply started planning over a different 1.6 km2 of the map, silently.

A region name is advisory BY DESIGN -- ``domain.spatial.regions`` says so in its first
paragraph, and that is not the bug. The bug is a STORED plan whose meaning moves without
saying so, which is the exact staleness class everything else here announces: the node
table declares what a game update moved (``spatial.nodes.TableSkew``), the tile probe
declares a recut. So a plan records what each selector actually resolved to, and a recall
re-resolves and reports the difference.

What is recorded, per LOCATION selector (filters are folded into each one, since they are
per-node predicates and distribute over the union -- see ``spatial.select.split_spec``):

``selector``
    the selector text exactly as the plan stores it.
``count`` / ``hash``
    how many nodes it returned, and a hash over their sorted leaf names. Either one
    changing is drift; the hash catches a swap that leaves the count alone.
``nodes``
    those leaf names, so a note can say WHICH appeared and vanished. Capped: a whole-map
    spec is 608 rows and 17 kB of names, which is not what a hand-written plan file is
    for. Past the cap the list is empty and ``count != len(nodes)`` says so, leaving the
    count and the hash to detect drift that then cannot be named node by node.
``bbox``
    the box those nodes occupied, in metres, and the most load-bearing field here. It is
    what makes the note ACTIONABLE rather than merely alarming: the fix for a selector
    that moved is to state the field geographically instead, and the box is that
    statement. ``tests/conftest.py`` did exactly this by hand after the region re-cut,
    for exactly this reason -- ``REFERENCE_FIELD`` is the bounding box of the nodes the
    retired selector returned, and it can never move again.

A plan saved before any of this has no record, and that is reported as "cannot be
checked" rather than as "unchanged". Absence of a record is not evidence of stability.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ...core.gamedata.model import GameData
from ..spatial.select import split_spec
from .scenario import select_for

if TYPE_CHECKING:  # pragma: no cover - import cycle only matters for type checkers
    from ..world.state import WorldState
    from .store import Plan

__all__ = [
    "LEAF_CAP",
    "SelectorDrift",
    "compare",
    "notes",
    "record",
    "recorded",
]

#: Shape of the recorded block, so a later reader can tell a v1 record from a v2 one
#: rather than guessing from which keys happen to be present.
PROVENANCE_SCHEMA = 1

#: How many node names one selector may store. Past this the set is counted and hashed
#: but not named: "all" is 608 rows, and a plan file is something a person opens.
LEAF_CAP = 250

#: How many names one note may print per side. A selector that lost 33 nodes is a fact;
#: 33 instance ids in a tool response is a wall.
NAME_CAP = 5


def _short(instance: str) -> str:
    return str(instance).rsplit(".", 1)[-1]


def _bbox(nodes: list[dict]) -> list[float] | None:
    """The box these nodes occupy, in METRES -- the form ``bbox:`` selectors take."""
    if not nodes:
        return None
    xs = [n["x"] / 100 for n in nodes]
    ys = [n["y"] / 100 for n in nodes]
    return [round(min(xs), 2), round(min(ys), 2), round(max(xs), 2), round(max(ys), 2)]


def _entry(selector: str, nodes: list[dict]) -> dict:
    leaves = sorted(_short(n["instance"]) for n in nodes)
    return {
        "selector": selector,
        "count": len(leaves),
        "hash": hashlib.sha256("\n".join(leaves).encode("utf-8")).hexdigest()[:12],
        "nodes": leaves if len(leaves) <= LEAF_CAP else [],
        "bbox": _bbox(nodes),
    }


def record(game: GameData, state: WorldState, sources: list[str] | None) -> dict:
    """Resolve ``sources`` selector by selector, as a block to store with the plan.

    A spec with no location selector is the whole map, so there is nothing selector-shaped
    to pin and the block is recorded EMPTY rather than not at all: an empty list means
    "checked, nothing to check", which is a different answer from a plan that predates the
    check entirely. Filters with no location ("resource:Crude Oil" alone) do narrow the
    map, so they are recorded as one entry under their own text.
    """
    locations, filters = split_spec(sources)
    entries = []
    if locations:
        for selector in locations:
            sel = select_for(game, state, [selector, *filters])
            entries.append(_entry(selector, sel.nodes))
    elif filters:
        sel = select_for(game, state, list(filters))
        entries.append(_entry(" + ".join(filters), sel.nodes))
    return {"schema": PROVENANCE_SCHEMA, "selectors": entries}


def recorded(plan: Plan) -> bool:
    """Whether this plan carries a resolved-set record at all."""
    return isinstance(plan.provenance, dict) and "selectors" in plan.provenance


@dataclass(frozen=True)
class SelectorDrift:
    """One selector that no longer resolves to what the plan was saved against."""

    selector: str
    then: int
    now: int
    #: Leaf names that were in the saved set and are not in it now, and the reverse.
    #: Empty when the saved set was past ``LEAF_CAP`` and so was never named.
    gone: tuple[str, ...] = ()
    appeared: tuple[str, ...] = ()
    #: Whether both sides were named node by node, so `gone`/`appeared` are complete.
    named: bool = True
    #: The box the SAVED nodes occupied, metres -- the rewrite this note suggests.
    bbox: list[float] | None = None

    @property
    def bbox_selector(self) -> str:
        if not self.bbox:
            return ""
        return "bbox:" + ",".join(f"{v:g}" for v in self.bbox)


def compare(game: GameData, state: WorldState, plan: Plan) -> list[SelectorDrift]:
    """Re-resolve every recorded selector; report only the ones that moved.

    Empty means "nothing to say" -- either every selector still resolves to the set it
    resolved to, or the plan carries no record. Ask ``recorded`` to tell those apart;
    conflating them is precisely the silence this module exists to remove.
    """
    if not recorded(plan):
        return []
    # Re-resolved by the very function that wrote the record, so the two sides can never
    # be built differently -- a comparison whose halves disagree about how a spec is split
    # would report drift that is really a refactor.
    fresh_by_selector = {
        e["selector"]: e for e in record(game, state, plan.kwargs().get("sources"))["selectors"]
    }
    out: list[SelectorDrift] = []
    for entry in plan.provenance.get("selectors") or ():
        selector = entry.get("selector") or ""
        fresh = fresh_by_selector.get(selector)
        if fresh is None:
            # The plan's own `sources` no longer contain this selector, so the ARGUMENTS
            # were edited rather than the world moving under them. `put` rewrites the
            # record whenever it stores arguments, so this is a stale half-record and
            # reporting it as drift would blame the map for an edit.
            continue
        if fresh["hash"] == entry.get("hash") and fresh["count"] == entry.get("count"):
            continue
        was = list(entry.get("nodes") or ())
        named = len(was) == entry.get("count") and len(fresh["nodes"]) == fresh["count"]
        out.append(
            SelectorDrift(
                selector=selector,
                then=int(entry.get("count") or 0),
                now=fresh["count"],
                gone=tuple(sorted(set(was) - set(fresh["nodes"]))) if named else (),
                appeared=tuple(sorted(set(fresh["nodes"]) - set(was))) if named else (),
                named=bool(named),
                bbox=entry.get("bbox"),
            )
        )
    return out


def _named(leaves: tuple[str, ...]) -> str:
    if not leaves:
        return "none"
    shown = ", ".join(leaves[:NAME_CAP])
    extra = len(leaves) - NAME_CAP
    return f"{shown} (+{extra} more)" if extra > 0 else shown


def notes(game: GameData, state: WorldState, plan: Plan) -> list[str]:
    """What to tell the reader on a recall. Empty when the field has not moved.

    Silence on an unchanged field is deliberate: a "checked, still fine" line on every
    recall is noise that trains a reader to skip the line that does matter.
    """
    if not recorded(plan):
        return _unrecorded_notes(game, state, plan)
    drifts = compare(game, state, plan)
    if not drifts:
        return []
    out = [
        (
            f"plan {plan.name!r} is planning over a DIFFERENT field than the one it was "
            "saved against -- the plan did not change, its selector did. Every number in "
            "this response is the NEW field's."
        )
    ]
    for d in drifts:
        line = f"{d.selector!r}: {d.then} node(s) when saved, {d.now} now."
        if d.named:
            line += f" gone: {_named(d.gone)}. appeared: {_named(d.appeared)}."
        else:
            line += " Too many to name: the saved set was counted and hashed, not listed."
        if d.bbox_selector:
            line += f" The saved nodes sat in {d.bbox_selector} (metres)."
        out.append(line)
    if any(d.bbox_selector for d in drifts):
        out.append(
            "to plan over the field that was actually saved, pass that box as sources= "
            "instead: a region name is advisory by design and can be re-cut under a stored "
            f"plan, a box cannot. To accept the new field, re-run with save_as={plan.name!r}."
        )
    return out


def _unrecorded_notes(game: GameData, state: WorldState, plan: Plan) -> list[str]:
    """The degraded path: no record, so say that, and say what the selectors mean NOW.

    Not a refusal and not a guess. The plan still recalls; what it cannot do is claim its
    field is the one it was drawn over. Stating today's resolution as a bbox gives the
    reader the one thing that settles it -- they know what they planned over, and can
    compare a box to a memory in a way they cannot compare a hash.
    """
    fresh = record(game, state, plan.kwargs().get("sources"))["selectors"]
    if not fresh:
        return []  # whole map: no selector that could have changed meaning
    out = [
        (
            f"plan {plan.name!r} records no resolved node set -- it was saved before plans "
            "kept one, so whether its selectors still mean what they meant then CANNOT be "
            f"checked. Re-run it with save_as={plan.name!r} to record what they resolve to "
            "from here on."
        )
    ]
    for entry in fresh:
        box = entry["bbox"]
        if box:
            tail = (
                " in bbox:"
                + ",".join(f"{v:g}" for v in box)
                + " (metres) -- pass that box as sources= if the plan was drawn over "
                "another field."
            )
        else:
            # No box because nothing matched. Saying "pass that box" would be pointing at
            # a rectangle that does not exist.
            tail = " -- it selects nothing in this world, so there is no field to compare."
        out.append(f"{entry['selector']!r} resolves to {entry['count']} node(s) HERE AND NOW{tail}")
    return out
