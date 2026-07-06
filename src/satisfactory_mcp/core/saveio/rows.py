"""The three interned tables of the projection, decoded in one place.

``extract`` emits three tables as bare positional rows rather than as records, because a
record per piece would be megabytes: 8,347 lightweight buildables, 3,085 belt pieces and
503 pipes on the reference world, each row a short list of numbers with its class held once
in a ``classes`` list beside it. That shape is right, and it has a cost -- **every reader
has to decode it**, and until this module existed ten sites in five modules did, each with
its own hand-written ``isinstance``/``len``/``int()``/``float()`` guards copied from a
neighbour:

* ``domain/factories/floors.py`` -- three times, for foundations, belt runs and pipe runs;
* ``domain/factories/structure.py`` -- once more, for the slab weld;
* ``domain/spatial/elevation.py`` -- once, for ground samples;
* ``domain/world/flow.py`` -- twice, for the pipe-to-graph join;
* ``interfaces/web/api.py`` -- three times, one per endpoint.

Copied guards drift. They had already: one site resolved an out-of-range class index to
``""`` and another to ``None``; one checked that a point's coordinates were numbers and
another only that there were three of them; one dropped a row whose class index was not a
number and another let it raise. None of that was wrong, and all of it was the same
decision being made ten times, in four layers, where changing it means finding all ten.

**What these iterators are, and are not.** They are the projection's own prose contract
turned into code: raw centimetres, no rounding, no unit conversion, no display names, no
game concepts. A structure row comes out as the numbers the row holds. What ``foundation``
means, what a metre is and which class is a conveyor lift are all decisions for the layer
above, and each one is made in exactly the module that owns it.

The one lookup done here is the interned class index against the table's own ``classes``
list, which is decoding rather than naming: the index has no meaning outside the table it
was written with, and every caller resolved it anyway. ``None`` for an index the list cannot
answer, which is what ``/api/structures`` already sent; the caller that wants ``""`` says so.

**Tolerant of a short row, because the writer promises the columns are additive.** Schema 12
added a fifth structure column (yaw), 14 a fourth pipe column (the actor join) and 15 a
fourth belt and fifth pipe column (the spline tangents), and each of those docstrings states
that a reader predating the column sees a shorter row and must keep working. So the trailing
columns are read through a length check and default to "the projection does not carry it",
and the row is dropped only when a column the table cannot mean anything without -- the
class index, the position, the points -- will not decode.

**A skipped row still costs its ordinal, not its neighbours' ordinals.** ``pipe_flow`` and
``/api/pipes`` join to each other by a segment's POSITION in the table, so a torn row has to
leave a hole rather than shift everything after it up by one. That is why the segment
records carry ``index``: it is the row's position in the raw list, not a count of what
decoded, and a consumer building a positional array sizes it with `segment_count` and fills
it at ``record.index``.

Finally, ``*_ROW_WIDTH`` names how many columns each iterator reads. ``test_saveio_rows``
holds those numbers against the widest row the committed projection actually contains, so a
schema-17 column added to any of these three tables fails a test here until somebody has
decided whether these readers want it.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any, NamedTuple

__all__ = [
    "BELT_ROW_WIDTH",
    "PIPE_ROW_WIDTH",
    "STRUCTURE_ROW_WIDTH",
    "BeltSegment",
    "PipeSegment",
    "Structure",
    "belt_segment_count",
    "iter_belt_segments",
    "iter_pipe_segments",
    "iter_structures",
    "pipe_segment_count",
]

#: Columns of ``structures["instances"]``: ``[classIndex, x, y, z, yaw]``. The fifth arrived
#: in schema 12 and a projection cut before it is four columns long.
STRUCTURE_ROW_WIDTH = 5

#: Columns of ``belts["segments"]``: ``[chainIndex, classIndex, points, spans]``. The fourth
#: arrived in schema 15 and is emitted only for a run that actually bends, so a three-column
#: row is the ordinary case rather than an old projection.
BELT_ROW_WIDTH = 4

#: Columns of ``pipes["segments"]``: ``[networkIndex, classIndex, points, actorIndex, spans]``.
#: The fourth arrived in schema 14 and the fifth in schema 15, the latter again only where the
#: pipe bends.
PIPE_ROW_WIDTH = 5


class Structure(NamedTuple):
    """One lightweight buildable: where it stands, which way it faces, and what it is.

    ``yaw`` is ``None`` for both of the two ways a facing can be missing -- a projection
    older than schema 12, and schema 16's null for a rotation the parser could not read --
    because no reader here has ever distinguished them: both mean "draw it axis-aligned and
    do not claim a bearing".
    """

    class_index: int
    cls: str | None
    x: float
    y: float
    z: float
    yaw: float | None


class BeltSegment(NamedTuple):
    """One conveyor piece: its chain, its class, its polyline and its curve.

    ``points`` are the spline's control points in world centimetres, in travel order. There
    is at least one; there is usually more than one, and a caller wanting a route rather
    than a place is the one that says so -- ``extract._belts`` already drops the pieces it
    could reduce to a single point.

    ``spans`` is schema 15's tangent column exactly as stored -- one entry per span, ``0``
    for a straight one -- or ``None`` where the row carries no such column. Undecoded on
    purpose: the only reader of it is ``/api/belts``, which converts it to metres, and
    decoding it here would be inventing a shape for six numbers nothing else looks at.
    """

    index: int
    chain: int
    class_index: int
    cls: str | None
    points: list[list[float]]
    spans: Any | None


class PipeSegment(NamedTuple):
    """One fluid pipe: its network, its class, its polyline, its actor and its curve.

    ``actor_index`` points into ``graph["actors"]`` and is ``-1`` for a pipe that has none --
    a projection older than schema 14, or a pipe the graph does not name. ``network_index``
    points into ``pipes["networks"]`` and is likewise ``-1`` where no network claims the
    pipe; the network's fluid and id are left to the caller, which is the one endpoint that
    wants them.
    """

    index: int
    network_index: int
    class_index: int
    cls: str | None
    points: list[list[float]]
    actor_index: int
    spans: Any | None


def _table(projection: dict, key: str) -> dict:
    """One interned table, as a dict, whatever the projection carries in its place.

    ``{}`` for a missing key AND for a key holding something that is not a dict, so that a
    projection too old for ``pipes`` and one whose ``pipes`` is a stray list read the same
    way -- "this table has nothing in it" -- instead of the second raising.
    """
    payload = projection.get(key) if isinstance(projection, dict) else None
    return payload if isinstance(payload, dict) else {}


def _classes(table: dict) -> list:
    raw = table.get("classes")
    return raw if isinstance(raw, list) else []


def _rows(table: dict, key: str) -> list:
    raw = table.get(key)
    return raw if isinstance(raw, list) else []


def _class_at(classes: list, index: int) -> str | None:
    if 0 <= index < len(classes):
        name = classes[index]
        return name if isinstance(name, str) else None
    return None


def _points(raw: Any) -> list[list[float]]:
    """A segment's control points as ``[[x, y, z], ...]`` centimetres, guarded per point.

    A point that will not decode costs that point and not the segment: a belt whose third
    corner is unreadable is still a belt between the corners that read, which is the same
    trade every one of the call sites was already making.
    """
    out: list[list[float]] = []
    for point in raw if isinstance(raw, (list, tuple)) else ():
        if not isinstance(point, (list, tuple)) or len(point) < 3:
            continue
        try:
            out.append([float(point[0]), float(point[1]), float(point[2])])
        except (TypeError, ValueError):
            continue
    return out


def _column(row: Any, index: int) -> Any | None:
    """A trailing, additive column, or ``None`` where the row predates it."""
    return row[index] if len(row) > index else None


def iter_structures(projection: dict) -> Iterator[Structure]:
    """Every lightweight buildable in ``structures``, decoded, in the table's own order.

    A row is dropped when it is not a sequence, is shorter than four columns, or when its
    class index or any of its three coordinates will not read as a number. Everything else
    comes through, including a piece whose class index points past the ``classes`` list --
    that is a place with a real position and an unknown class, not a place that is not there.
    """
    table = _table(projection, "structures")
    classes = _classes(table)
    for row in _rows(table, "instances"):
        if not isinstance(row, (list, tuple)) or len(row) < 4:
            continue
        try:
            class_index = int(row[0])
            x, y, z = float(row[1]), float(row[2]), float(row[3])
        except (TypeError, ValueError):
            continue
        raw_yaw = _column(row, 4)
        try:
            yaw = None if raw_yaw is None else float(raw_yaw)
        except (TypeError, ValueError):
            yaw = None
        yield Structure(
            class_index=class_index,
            cls=_class_at(classes, class_index),
            x=x,
            y=y,
            z=z,
            yaw=yaw,
        )


def belt_segment_count(projection: dict) -> int:
    """How many rows ``belts["segments"]`` holds, decodable or not."""
    return len(_rows(_table(projection, "belts"), "segments"))


def iter_belt_segments(projection: dict) -> Iterator[BeltSegment]:
    """Every conveyor piece in ``belts``, decoded, in the table's own order.

    A row is dropped when it is not a sequence, is shorter than three columns, when its
    chain or class index will not read as an integer, or when not one of its points decodes
    -- a piece with no geometry is not a piece, which is what all four call sites said.
    """
    table = _table(projection, "belts")
    classes = _classes(table)
    for index, row in enumerate(_rows(table, "segments")):
        if not isinstance(row, (list, tuple)) or len(row) < 3:
            continue
        try:
            chain = int(row[0])
            class_index = int(row[1])
        except (TypeError, ValueError):
            continue
        points = _points(row[2])
        if not points:
            continue
        yield BeltSegment(
            index=index,
            chain=chain,
            class_index=class_index,
            cls=_class_at(classes, class_index),
            points=points,
            spans=_column(row, 3),
        )


def pipe_segment_count(projection: dict) -> int:
    """How many rows ``pipes["segments"]`` holds, decodable or not.

    What a positional array over the segments has to be sized with. ``pipe_flow`` promises
    ``/api/pipes`` one answer per ROW rather than one per readable row, so a torn row owes
    the array a slot it never fills.
    """
    return len(_rows(_table(projection, "pipes"), "segments"))


def iter_pipe_segments(projection: dict) -> Iterator[PipeSegment]:
    """Every fluid pipe in ``pipes``, decoded, in the table's own order.

    Dropped on the same terms as a belt segment. ``actor_index`` and ``network_index`` are
    normalised to ``-1`` rather than dropping the row: a pipe no network claims and a pipe
    the graph does not name are both ordinary, and both are drawn.
    """
    table = _table(projection, "pipes")
    classes = _classes(table)
    for index, row in enumerate(_rows(table, "segments")):
        if not isinstance(row, (list, tuple)) or len(row) < 3:
            continue
        try:
            network_index = int(row[0])
            class_index = int(row[1])
        except (TypeError, ValueError):
            continue
        points = _points(row[2])
        if not points:
            continue
        actor = _column(row, 3)
        yield PipeSegment(
            index=index,
            network_index=network_index,
            class_index=class_index,
            cls=_class_at(classes, class_index),
            points=points,
            # ``isinstance``, not ``int()``: an actor index is a position in a list the
            # projection also carries, so a float or a string here is a torn row rather
            # than a number in the wrong type, and -1 says "this pipe joins nothing".
            actor_index=actor if isinstance(actor, int) and actor >= 0 else -1,
            spans=_column(row, 4),
        )
