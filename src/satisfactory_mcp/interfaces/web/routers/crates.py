"""``/api/crates``: the death and dismantle crates on the ground, and what is in each one.

Its own file, and its own toggle on the page, for the reason schema 18 gave the projection
its own key: **a crate is a situation, not infrastructure.** ``/api/storage`` next door
answers "where did I put the steel" over 151 boxes the player built and left standing; this
answers "what did I lose, and where", over a handful of actors that did not exist until
somebody died or dismantled something with a full inventory, and that delete themselves the
moment they are emptied. Two rows that are events do not belong among 151 that are places.

WARNING: the function name is the operation_id -- rename it and the committed schema
churns. FastAPI's default id is ``{function_name}_{path}_{method}`` and ``api-schema.d.ts``
is generated off it.

**Declaration order is wire order** for the TypedDicts below, and a ``response_model``
FILTERS -- routers/floors.py writes both rules out at length.
"""

from __future__ import annotations

from typing import Any, TypedDict

from fastapi import APIRouter, Request

from ....domain.world.state import WorldState
from ..serial import _fail, _state, _xyz, _yaw

__all__ = ["router"]

router = APIRouter(prefix="/api")


# --------------------------------------------------------------------- crates


#: How many kinds of thing a crate row spells out before it starts counting instead.
#:
#: Higher than ``STORAGE_ITEMS_SHOWN``, and the difference is measured rather than a taste.
#: A container holds one or two kinds because a player fills it deliberately; a death crate
#: holds whatever was in a pioneer's pockets, which on the fullest one in this machine's
#: saves is **38 kinds in 55 slots** -- rods, screws, a chainsaw, a hard drive, four kinds of
#: alien remains and a boom box. Six of thirty-eight is not a list, it is a hint. Twelve is
#: what a popup can show without scrolling, and the row still says how many it left out,
#: because a list that simply stops is a bug the reader has to notice.
CRATE_ITEMS_SHOWN = 12

#: What each ``kind`` means, in the words a reader wants rather than the enum's.
#:
#: Sent WITH the row rather than left to the client, because the third value is the one that
#: needs explaining and no client should have to rediscover why. ``none`` is the game's own
#: ``CT_None``: the crate predates the distinction between the two kinds, which the game
#: added in build 433351, so it genuinely does not say which it is -- and the reference
#: world holds one of each, which is exactly the pair that makes the field worth having.
CRATE_KIND_TEXT = {
    "death": "dropped where a pioneer died",
    "dismantle": "overflow from dismantling with a full inventory",
    "none": "kind not recorded -- this crate predates the game's death/dismantle distinction",
}


class CrateItem(TypedDict):
    """One kind of thing in a crate, resolved to a display name by the server.

    The same three fields as ``StoredItem`` in routers/storage.py, and deliberately NOT the
    same type. Sharing it would mean moving it to ``serial.py`` -- a router may not import
    another router, and rightly -- which would put a row shape into the module that holds
    the unit conversions, on the strength of a coincidence: these two are alike because both
    are a stack, and they are filled by two expressions with two different truncation limits.
    ``Region`` is in ``serial.py`` because ONE function builds it for two routers, which is
    the case this is not.

    ``count`` is an ``int``: a stack amount is a number of items, and declaring it ``float``
    would validate 15 into 15.0 and rewrite every row.
    """

    cls: str
    name: str
    count: int


class CrateRow(TypedDict):
    """One crate: what kind it is, where it is, and what is inside it.

    ``cls`` is not nullable and the coordinates are, which is the actor-record line
    routers/placements.py draws: a crate is an ordinary actor written out with its class, so
    the class is there; its TRANSFORM is what can fail to decode, and ``_xyz`` answers three
    nulls when it did. A container next door is declared on exactly these terms.

    ``kind`` is a plain ``str`` and NOT a ``Literal``, which is the opposite call from
    ``mode`` on ``/api/collectibles`` and for a stated reason: this value arrives from the
    PROJECTION, which is versioned and read off disk, and ``_crate_row`` uses ``.get`` on
    ``CRATE_KIND_TEXT`` precisely so that a projection cut by a later extractor that learned
    a fourth ``EFGCrateType`` is still served -- with the word it used and no gloss. A closed
    union here would turn that into a 500 on a key this build has not heard of.
    ``kind_text`` is the null that says so.

    ``slots`` is ``int | None`` on ``StorageSolid``'s terms: it is the inventory component's
    own slot count forwarded whole, and a projection that wrote none sends null rather than
    0. ``more``, ``item_kinds`` and ``total`` are counts and are ints -- ``more`` is 0 rather
    than null when the truncation left nothing off.
    """

    instance_leaf: str
    cls: str
    kind: str
    kind_text: str | None
    x_m: float | None
    y_m: float | None
    z_m: float | None
    yaw: float | None
    items: list[CrateItem]
    more: int
    item_kinds: int
    total: int
    slots: int | None


class CratesResponse(TypedDict):
    """The list and the three numbers a header wants, in emission order.

    ``deaths`` is the one a player cares about and is not a length of anything: it is how
    many of these rows are somebody's death, which on a world of crates that all predate
    ``mCrateType`` is 0 against a non-zero ``count`` -- the same "there is nothing to say"
    against "there is nothing here" pair ``/api/power``'s ``edge_count`` makes.
    """

    crates: list[CrateRow]
    count: int
    deaths: int
    items_total: int


def _crate_row(st: WorldState, row: dict) -> CrateRow:
    """One crate: what kind it is, where it is, and what is inside it.

    One record shape, unlike ``/api/storage``'s two: there is no fluid crate. What replaces
    that split is ``kind``, which is the field the whole endpoint exists around -- a client
    drawing these wants a skull on one and a wrench on the other, and a third glyph for the
    old ones that cannot say.

    ``items`` is resolved against the docs dump like every other class on this surface, so a
    popup never shows a reader a ``Desc_…_C``, and it is TRUNCATED with a count of the
    remainder on ``/api/storage``'s terms exactly: ``item_kinds`` and ``total`` are of the
    whole crate either way, so a client that wants to say "and 26 more" has the numbers.

    **No footprint, and that is not an omission.** A crate is not a buildable and has no
    ``Build_`` class, so the docs dump carries no clearance for it and none is invented here
    -- the same refusal ``/api/storage`` makes for the four container classes the dump does
    not describe. A client draws these at a fixed marker size, which is what they are.
    """
    raw = [e for e in row.get("items") or () if isinstance(e, (list, tuple)) and len(e) >= 2]
    items = [
        {"cls": str(e[0]), "name": st.game.item_name(str(e[0])), "count": e[1]}
        for e in raw[:CRATE_ITEMS_SHOWN]
    ]
    kind = str(row.get("kind") or "none")
    return {
        "instance_leaf": str(row.get("instance", "")).rsplit(".", 1)[-1],
        "cls": row.get("cls"),
        "kind": kind,
        # ``.get``, not ``[]``: a projection cut by a later extractor that learned a fourth
        # EFGCrateType value must still be served, with the word it used and no gloss, rather
        # than 500ing on a key this build has not heard of.
        "kind_text": CRATE_KIND_TEXT.get(kind),
        **_xyz(row.get("pos")),
        "yaw": _yaw(row.get("yaw")),
        "items": items,
        # What was left off the list above, so a client can say "and 26 more" rather than
        # showing twelve of thirty-eight and implying thirty-eight is twelve.
        "more": max(0, len(raw) - len(items)),
        "item_kinds": len(raw),
        "total": sum(e[1] for e in raw if isinstance(e[1], (int, float))),
        "slots": row.get("slots"),
    }


@router.get("/crates", response_model=CratesResponse)
def crates(request: Request, save: str | None = None, world: str | None = None) -> Any:
    """Every crate lying on the ground, what kind it is, and what is inside it.

    New in schema 18, and the gap it fills is the last inventory in the world nothing could
    show. A crate is not a buildable, so it was never in ``building_counts``; it runs no
    recipe and draws no power, so it is not a machine; it is an ordinary actor, so it is not
    a lightweight piece; and ``/api/storage`` joins a written-down list of container classes
    that ``BP_Crate_C`` is not on, deliberately. The only trace of one has been its contents
    summed anonymously into ``inventories["machine"]`` alongside the smelter buffers, where
    they read as material that exists and cannot be spent.

    **Three kinds, and the third is an answer.** ``death`` is where a pioneer died,
    ``dismantle`` is what would not fit in a full inventory, and ``none`` is a crate that
    predates the game's own distinction between the two -- ``mCrateType`` is a save property
    the game added in build 433351, so a crate made before it carries no type and never
    will. 125 of the 170 crates in this machine's 67 saves are in that state, and the
    reference world holds exactly one of each. Reporting them as deaths would be inventing
    the one fact the save withheld.

    **Whose crate it is, the save does not say.** ``mCrateType`` is the actor's only saved
    property -- no owning player, no timestamp, no cause. In a single-player world every
    death crate is the player's by construction; in a co-op world nothing here can say
    whose, and a field that guessed would arrive indistinguishable from a reading.

    **Contents are the crate's own**, joined from the inventory component it owns, resolved
    to display names and truncated with a count -- the join and the truncation
    ``/api/storage`` makes, at a higher limit, because a death crate holds a whole pioneer's
    pockets rather than one deliberate kind of thing.

    Tiny: 2 rows on the reference world against ``/api/storage``'s 151, sorted by kind so a
    client's first row is the interesting one. Sent in one payload, ungrouped, the posture
    every placement endpoint here takes.
    """
    try:
        st = _state(request, save, world)
    except Exception as exc:
        return _fail(f"could not read save: {exc}", 404)

    rows = [
        _crate_row(st, row) for row in st.projection.get("crates") or () if isinstance(row, dict)
    ]
    return {
        "crates": rows,
        "count": len(rows),
        # The two numbers a header wants, and the first is the one a player cares about:
        # how many of these are somebody's death, and how much stuff is lying out there.
        "deaths": sum(1 for r in rows if r["kind"] == "death"),
        "items_total": sum(r["total"] for r in rows),
    }
