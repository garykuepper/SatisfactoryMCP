"""The inflated body: preamble, world-partition grids, levels and object headers.

This is the layer between ``chunks.decompress_body`` and the property serialiser. It
answers one question -- *where is every object's property blob* -- and answers it by
walking, never by searching. Every region here declares its own length, and the walk
asserts that each declared length lands exactly where the next structure begins. That is
the whole safety argument: a save torn mid-write by an autosave fails on the first size
that does not add up, with the byte offset, instead of yielding a shorter factory.

## The shape, as derived from the bytes

::

    i64  body size                 len(body) - 8, self-describing
    59B  archive version header    saveVersion 60 only -- see ARCHIVE_HEADER_LEN
    i32  custom version count      60 only; then that many (16-byte GUID, i32 version)
    i32  grid count                then that many world-partition grids
    i32  sub-level count           3123 on the reference save
    ...  sub-level records         each named after a partition cell
    ...  the persistent level      SAME record, but with NO name string
    ...  a trailing destroyed-actor table, keyed by level name

A level record::

    str  name                      (absent on the persistent level)
    i64  toc size ; [ i32 header count ][ headers ][ trailing bytes ]
    i64  data size ; [ i32 object count ][ object entries ]
    i32  version (52 or 60)        \\
    i32  destroyed count ; refs     |  absent on the persistent level, whose trailing
    i32  archive-follows flag      /   table is the body's final structure instead

**Two layouts, not one.** saveVersion 52 bodies -- 25 of the 31 readable saves on the
author's disk -- have no archive version header, no custom versions, and no per-level
archive headers, so no flag announcing one either. The grid table starts right after the
body size. The two are told apart by looking for the header's ``(0, 522, 1017)`` signature
at the cursor, which cannot be confused with a 52 body: that would need a save declaring
zero grids, then 522 levels, then a 1017-byte level name, and no save has fewer than six
grids. Everything from the grid table down is identical between the two.

## Two more layouts below saveVersion 52, and what they drop

35 files on the author's disk are pre-1.0. They are the same walk with fields removed, gated
on ``save_version`` -- see ``versions.py`` for the thresholds and for why they are thresholds
rather than one constant per field. **Below saveVersion 52** the body has no archive version
header, no custom versions and **no grid table**, its size and every block size is an
``int32``, an object header has no ``EObjectFlags`` word, an object entry is a bare ``int32
size`` with no version and no flag, a level trailer has no version int32 and no archive flag,
and the persistent level's destroyed-actor list is the same bare shape as a sub-level's rather
than grouped by cell. **Below saveVersion 30** there is no level list at all::

    i32  body size            len(body) - 4
    i32  object header count   25,385 on Vanilla_210421-011602
    ...  headers              one flat run
    i32  object count         == the header count, checked
    ...  object entries
    i32  destroyed count ; refs        <- closes the body

Each header names its own level in ``root_object``, and three values occur --
``Persistent_Level``, ``Persistent_Exploration``, ``Persistent_Exploration_2``. This module
groups by that field in first-appearance order, because it is the only reading under which
every level has the name the bytes give it; one flat level would have to be *called* something,
and calling it ``Persistent_Level`` would be wrong for the objects rooted elsewhere.

The referee is unchanged and it is the whole argument: **566 levels and 851,682 objects across
the 35 files, every declared length landing exactly, and zero bytes left over in any body.**
Corroboration from values the game constrains: 596,799 actors with **0 non-unit quaternions**,
``need_transform`` and ``was_placed_in_level`` never anything but 0 or 1, and positions inside
the map's real extent.

**What closes an old body is read, not explained.** On a shape-B save (saveVersion 30/36) the
tail is ``[i32 0][i32 5][5 refs]`` and exactly two bare lists land on the last byte -- three do
not and one does not. That reads equally well as *the unnamed persistent record carrying a
trailer like every other level, then one closing list* (which is what this module does, and it
makes the two old shapes close identically) or as *two ungrouped closing lists*. The first list
is empty on all 14 saves, so the bytes cannot distinguish them and no value depends on the
choice. Those 447 closing bytes are byte-identical on all 14 saves across seven months of play
and name 5 actors in ``Persistent_Exploration_2``, a level with no record in the body at all.

## Wrong turns worth recording

**The "774 KB partition table" in the earlier notes does not exist.** That figure came
from measuring to the first literal ``Persistent_Level\\0`` in the body, at offset 774709.
The grid table actually ends at 71578 and is ~71 KB; the 774709 hit is just the first
sub-level whose cell happens to contain an object whose ``rootObject`` is the persistent
level. Nothing here needs to skip 774 KB, and nothing here searches for a literal.

**A level's headers and its objects are two separate blocks, both length-prefixed.** The
TOC block holds the headers; the data block holds the property blobs. They are parallel
lists -- ``header[i]`` describes ``object[i]`` -- but they are not interleaved, so a
parser that reads a header and then its body would be reading the wrong bytes.

## Object entries, and the one version-dependent field

An object entry is ``i32 version, i32 flag, i32 size``, then ``size`` bytes of property
data, and objects written at **version 60 add a trailing int32 after the payload** -- 0 on
all 39,015 of them in the reference save.

Where that extra int32 sits took three wrong guesses. Reading it as a fourth *head* field
(or as the size having widened to int64) fits every block boundary in the file, because
either way the entry is ``16 + size`` bytes long: the two readings are indistinguishable
from lengths alone. What settles it is the payload's own content. Parsing each version-60
actor's prefix -- parent reference, then its child-component references -- and asking
where the first property name begins puts the payload start at ``+12``, not ``+16``, on
18,369 actors, and never at ``+16``. So the size counts from immediately after itself, as
it does for every other version, and the extra int32 is at the far end.

Versions are per object, not per save: an untouched world-partition cell keeps the bytes
it was written with, so 36, 52 and 60 all appear in the same file.

One measurement for the property serialiser, taken while pinning the above down: a
**version-60 payload has one extra byte immediately before its property list** -- after
the reference lists on an actor, at the very start on a component. Confirmed on 18,369
actors (property name one byte later than on version 36/52) and 20,646 components (whose
payload is otherwise nothing but a property list). It is inside the slice, so it belongs
to whoever parses the slice.

## Destroyed actors, which the save keeps three lists of

The world is not saved. Every power slug, mushroom, Mercer sphere and crashed drop pod sits
where the map put it, so a save cannot record collectibles by listing what exists -- it
records the **negative**: which map-placed actors are gone. Three lists hold that, and they
are three different lists rather than one written thrice:

* trailing each level's **header block**, after the headers -- ``[i32 count][refs]`` on a
  sub-level, and grouped by partition cell on the persistent level
  (``[i32 groups][str cell][i32 count][refs]``). 859 actors on the reference save, 97,250
  bytes, and this was the last thing in the body still skipped rather than read.
* in each sub-level's **trailer**, after its version. 884 actors.
* the **table that closes the body**, keyed by cell, two lists per group: looted drop pods
  and crashed ships (5 on the reference save), then Mercer shrines (7).

Measured overlap on the reference save: 854 actors are in both the first two, 5 only in the
header blocks, 30 only in the trailers, and all 12 of the closing table's are already in one
of the others. **889 distinct actors** -- ``SaveBody.destroyed_actors``. Which of the three a
given actor lands in is not established; that they must be merged rather than picked from is.

Verified against the vendored parser, which exposes the same three under different names: our
header-block list equals its ``collectables1`` exactly, our trailer list its ``collectables2``,
and our closing table its ``dropPodObjectReferenceList`` plus ``extraObjectReferenceList``.
Same 889-actor union, no entry on either side that the other lacks.

The reading is checked by landing: after the list, the cursor must be exactly on the header
block's declared end. Two independent lengths agreeing -- the block's, and the list's own
counts -- is what makes this read rather than a guess.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .errors import ParseError
from .reader import Reader
from .versions import FIRST_LEVEL_LIST, FIRST_MODERN_BODY

__all__ = [
    "ARCHIVE_HEADER_LEN",
    "ActorHeader",
    "BodyPreamble",
    "ComponentHeader",
    "Grid",
    "Level",
    "ObjectSlice",
    "ParseError",
    "SaveBody",
    "read_body",
]

#: Bytes of the archive version header: four int32s, a 6-byte block, the tag, and the
#: engine branch string. Fixed only because that string has a fixed length; it is read
#: field by field rather than skipped blindly, and its trailing null is what pins it.
ARCHIVE_HEADER_LEN = 59

#: The two int32s that open the archive header, on every occurrence in every save
#: checked. They are the signature used to recognise the header when a level record says
#: one follows -- a positional check, not a search.
_ARCHIVE_MARK = (0, 522)


@dataclass
class ObjectSlice:
    """One object's property block, as a slice into the inflated body.

    The property serialiser is the next layer; this one only has to hand it the right
    bytes. ``offset``/``length`` are absolute into the same ``bytes`` object that
    ``read_body`` was given, so nothing is copied for a 44 MB save.
    """

    #: Save version this object was serialised at: 36, 52 and 60 all occur.
    version: int
    #: Second int32 of the entry. 1 on version 36/52 objects, 0 on version 60 ones.
    flag: int
    #: Absolute offset of the first property byte.
    offset: int
    #: Byte length of the property block, exactly as the entry declared it.
    length: int

    @property
    def end(self) -> int:
        return self.offset + self.length


@dataclass
class ActorHeader:
    """An actor: a placed thing with a transform.

    ``rotation`` is a quaternion in x, y, z, w order -- checked by summing squares over
    the reference save's 11,970 persistent-level actors, all 1.0 to float32 precision.
    ``position`` is centimetres in the game's world frame, which is what the projection
    reports and what ``describe_location`` turns into map coordinates.
    """

    type_path: str
    root_object: str
    instance_name: str
    #: ``None`` below saveVersion 52, where the word is simply not in the file.
    object_flags: int | None
    need_transform: int
    rotation: tuple[float, float, float, float]
    position: tuple[float, float, float]
    scale: tuple[float, float, float]
    was_placed_in_level: int

    # The adapter reads these three names off whatever the parser returns. Keeping them
    # as aliases rather than renaming the fields keeps this module readable and the
    # adapter unchanged.
    @property
    def typePath(self) -> str:
        return self.type_path

    @property
    def instanceName(self) -> str:
        return self.instance_name


@dataclass
class ComponentHeader:
    """A component: no transform, and a parent actor it hangs off.

    Inventories, power connections and power info are all components -- 20,813 of the
    reference save's 44,634 objects -- which is why the projection has to cope with a
    header that has no ``typePath``.

    The bytes DO name a component's class, and this exposes it as ``class_path``, NOT as
    ``typePath``. That is a compatibility decision, not an oversight: ``iter_objects``
    reads ``getattr(header, "typePath", "")`` and the projection's class-based branching is
    built on components resolving to the empty string. Adding the attribute would silently
    change the output for every inventory and power component in the save.
    """

    class_path: str
    root_object: str
    instance_name: str
    #: ``None`` below saveVersion 52, where the word is simply not in the file.
    object_flags: int | None
    parent_actor_name: str

    @property
    def instanceName(self) -> str:
        return self.instance_name


@dataclass
class Grid:
    """One world-partition grid: a cell size and the cells that have saved content."""

    name: str
    cell_size: int
    content_id: int
    cell_names: list[str] = field(default_factory=list)


@dataclass
class BodyPreamble:
    """Everything before the level list, kept because it is cheap and diagnostic.

    ``version_fields`` through ``custom_versions`` come from the archive version header,
    which **only saveVersion 60 bodies have**: on a 52 body the grid table starts
    immediately after the size. They are ``None``/empty there rather than faked.
    """

    declared_size: int
    grids: list[Grid]
    version_fields: tuple[int, int, int, int] | None = None
    unknown_six: bytes = b""
    tag: int = 0
    branch: str = ""
    custom_versions: list[tuple[bytes, int]] = field(default_factory=list)

    @property
    def has_archive_header(self) -> bool:
        return self.version_fields is not None


@dataclass
class Level:
    """One level: parallel lists of headers and property-block slices.

    ``name`` is a 25-character partition-cell id for a sub-level and
    ``"Persistent_Level"`` for the one unnamed record at the end. That last name is the
    single place this parser's output differs from the vendored one, which reports ``None``
    there; nothing in the projection reads level names, and a name beats a ``None`` that
    every caller has to guard.
    """

    name: str
    headers: list[ActorHeader | ComponentHeader]
    objects: list[ObjectSlice]

    #: Bytes of the TOC block left over after the headers -- the destroyed-actor list.
    #: Kept as a number so a caller can see it is nonzero even though it is now read.
    toc_extra_bytes: int = 0
    #: Actors the save records as gone: ``(level cell, actor path)`` pairs from this level's
    #: header block. See ``_read_destroyed_block``.
    destroyed: list[tuple[str, str]] = field(default_factory=list)

    @property
    def actorAndComponentObjectHeaders(self) -> list[ActorHeader | ComponentHeader]:
        """The name the adapter reads."""
        return self.headers


@dataclass
class SaveBody:
    preamble: BodyPreamble
    levels: list[Level]
    #: Anything skipped rather than understood, as ``(offset, what)``. Empty on all 31
    #: readable saves on the author's disk; a future patch that adds a structure should
    #: show up here rather than as silently wrong output.
    warnings: list[tuple[int, str]] = field(default_factory=list)
    #: Destroyed actors from the sub-level trailers, and from the table that closes the body.
    #: Kept apart from ``Level.destroyed`` because the three are three different lists and
    #: whether they hold the same actors is a measurement, not an assumption -- see
    #: ``destroyed_actors``.
    trailer_destroyed: list[tuple[str, str]] = field(default_factory=list)
    closing_destroyed: list[tuple[str, str]] = field(default_factory=list)

    @property
    def object_count(self) -> int:
        return sum(len(lv.objects) for lv in self.levels)

    @property
    def destroyed_actors(self) -> list[tuple[str, str]]:
        """Every actor the save records as gone, from all three lists, deduplicated.

        The save keeps three: one trailing each level's header block, one in each sub-level's
        trailer, and one closing the body. Deduplicated by ``(cell, path)`` because they do
        overlap -- see ``docs/savparse-notes.md`` for the measured split.
        """
        seen: dict[tuple[str, str], None] = {}
        for lv in self.levels:
            for ref in lv.destroyed:
                seen[ref] = None
        for ref in self.trailer_destroyed:
            seen[ref] = None
        for ref in self.closing_destroyed:
            seen[ref] = None
        return list(seen)

    @property
    def skipped_toc_bytes(self) -> int:
        """Destroyed-actor bytes stepped over by declared length, summed over levels.

        A number rather than 3,082 warning strings, which is what a per-level warning
        came to on the reference save. It is expected to be nonzero.
        """
        return sum(lv.toc_extra_bytes for lv in self.levels)


def _expect(condition: bool, offset: int, message: str) -> None:
    if not condition:
        raise ParseError(f"at body offset {offset}: {message}")


def _at_archive_header(r: Reader) -> bool:
    """Is an archive version header at the cursor?

    Needed because saveVersion 52 bodies have none -- their grid table starts right after
    the body size -- and one function has to read both. The signature is three int32s,
    ``(0, 522, 1017)``. Mistaking a 52 body for a 60 one would need it to declare zero
    grids, then 522 levels, then a 1017-byte level name; no save has fewer than six grids.
    """
    if r.remaining < 12:
        return False
    look = Reader(r.data, r.pos)
    return (look.i32(), look.i32(), look.i32()) == (*_ARCHIVE_MARK, 1017)


def _read_archive_header(r: Reader) -> tuple[tuple[int, int, int, int], bytes, int, str]:
    """The 59-byte version header. Read field by field so a change is caught here.

    The same header appears at the front of the body and again between level records --
    1,906 times on the reference save -- so it is one function rather than an inline
    skip, and the branch string is compared each time.
    """
    start = r.pos
    fields = (r.i32(), r.i32(), r.i32(), r.i32())
    _expect(
        fields[:2] == _ARCHIVE_MARK,
        start,
        f"expected an archive version header starting {_ARCHIVE_MARK}, found {fields[:2]}",
    )
    six = r.bytes(6)
    tag = r.u32()
    branch = r.string()
    _expect(
        r.pos - start == ARCHIVE_HEADER_LEN,
        start,
        f"archive header read {r.pos - start} bytes, expected {ARCHIVE_HEADER_LEN} "
        f"(branch string {branch!r} changed length?)",
    )
    return fields, six, tag, branch


def _read_custom_versions(r: Reader) -> list[tuple[bytes, int]]:
    """UE's custom-version array: a count, then (GUID, version) pairs.

    The count is what terminates it -- the earlier notes left this open and guessed at a
    sentinel. There is none: 13 pairs on the reference save's outer header, and 0, 5, 6
    or 8 on the per-level ones.
    """
    count = r.i32()
    _expect(0 <= count <= 4096, r.pos - 4, f"custom version count {count} is not plausible")
    return [(r.bytes(16), r.i32()) for _ in range(count)]


def _read_grids(r: Reader) -> list[Grid]:
    """The world-partition table: 7 grids on a saveVersion 60 body, 6 on a 52 one.

    Shape per grid: name, cell size, a u32, then a count of cells, each a
    25-character base-36 id and a u32 of its own. The reference save has
    ``MainGrid`` at 12800 uu with 1,288 cells, ``ExplorationGrid`` at 20480 with 758,
    ``ExplorationGridFar`` with 43, and four grids with none (a 52 body has all of those
    but ``ExplorationGridFar``).

    Reading these ids rather than skipping the region is what makes the level walk
    checkable: of the 1,624 levels that actually contain objects, **1,623 are named here**
    -- the one exception being the persistent level, which is not a partition cell. The
    converse does not hold (1,500 levels are empty and most are absent from the table),
    so this is a one-way correspondence and not a set equality.
    """
    count = r.i32()
    _expect(0 <= count <= 256, r.pos - 4, f"grid count {count} is not plausible")
    grids = []
    for _ in range(count):
        name = r.string()
        cell_size = r.i32()
        content_id = r.u32()
        n_cells = r.i32()
        _expect(
            0 <= n_cells <= 1_000_000,
            r.pos - 4,
            f"grid {name!r} claims {n_cells} cells",
        )
        cells = []
        for _ in range(n_cells):
            cells.append(r.string())
            r.u32()  # per-cell content id; unused, and unread by anything above this
        grids.append(Grid(name=name, cell_size=cell_size, content_id=content_id, cell_names=cells))
    return grids


def _read_header(r: Reader, save_version: int) -> ActorHeader | ComponentHeader:
    """One object header. The leading int32 says which of the two kinds it is.

    Both kinds open the same way -- class path, root object, instance name, then UE's
    ``EObjectFlags``.

    **Which kind it is comes from the leading int32, not from the flags**, and that matters
    because an earlier version of this docstring claimed the flags were a two-value invariant --
    ``0x280008`` on actors, ``0x2C0008`` on components, differing by ``RF_DefaultSubObject``.
    Measured over 1,243,288 objects there are **eight** distinct values, and that pair covers
    324,765 of 675,432 actors and just 6,648 of 567,856 components. The commonest are
    ``0x40008`` (334,988, components only), ``0x280008`` (324,765, actors only) and ``0x8``
    (310,011, actors only). The bit does separate the two kinds in the common case; it is not
    the invariant it was written up as, and nothing here depends on it.

    **The flags word arrives at saveVersion 52.** Below it the transform follows the instance
    name directly, and reading four bytes anyway put a quaternion component into
    ``need_transform`` and shifted every float by one slot -- caught not by the value but by the
    next header's class-path length, four bytes into a rotation. It is ``None`` rather than 0
    there, because 0 is a legal flags word and "absent" must not read as "no flags set".
    """
    at = r.pos
    kind = r.i32()
    class_path = r.string()
    root_object = r.string()
    instance_name = r.string()
    flags = r.u32() if save_version >= FIRST_MODERN_BODY else None
    if kind == 1:
        need_transform = r.i32()
        rotation = (r.f32(), r.f32(), r.f32(), r.f32())
        position = (r.f32(), r.f32(), r.f32())
        scale = (r.f32(), r.f32(), r.f32())
        return ActorHeader(
            type_path=class_path,
            root_object=root_object,
            instance_name=instance_name,
            object_flags=flags,
            need_transform=need_transform,
            rotation=rotation,
            position=position,
            scale=scale,
            was_placed_in_level=r.i32(),
        )
    if kind == 0:
        return ComponentHeader(
            class_path=class_path,
            root_object=root_object,
            instance_name=instance_name,
            object_flags=flags,
            parent_actor_name=r.string(),
        )
    raise ParseError(
        f"at body offset {at}: object header kind {kind}, expected 0 (component) or "
        f"1 (actor). The class path read as {class_path!r}"
    )


def _read_object_entry(r: Reader, save_version: int) -> ObjectSlice:
    """One object entry: three int32s, the payload, and on version 60 a trailing int32.

    The payload is NOT parsed here. Its boundaries are the deliverable: the property
    serialiser gets ``[offset, offset + length)`` and must consume it exactly, which is
    a check it could not make if this layer had guessed.

    ``r`` is left at the FIRST payload byte. The caller steps over the payload and reads
    the version-60 trailer, because only the caller knows where the block ends and can
    therefore refuse a size that would run past it.

    **Below saveVersion 52 the entry is a bare size.** There is no version int32 and no flag
    int32, which means the object's serialisation version is *not in the bytes* and has to be
    taken from the save. That is the one place in this parser where a version is supplied
    rather than read, and it is why ``read_body`` takes ``save_version`` at all.
    """
    if save_version >= FIRST_MODERN_BODY:
        version = r.i32()
        flag = r.i32()
    else:
        version, flag = save_version, 0
    size = r.i32()
    _expect(size >= 0, r.pos - 4, f"object entry declares a negative payload size {size}")
    return ObjectSlice(version=version, flag=flag, offset=r.pos, length=size)


def _read_destroyed_refs(r: Reader, where: str, limit: int) -> list[tuple[str, str]]:
    """A count, then that many ``(level name, actor path)`` pairs."""
    at = r.pos
    count = r.i32()
    _expect(
        0 <= count <= 1_000_000 and r.pos + count * 8 <= limit,
        at,
        f"{where}: {count} destroyed actors do not fit in the {limit - r.pos} bytes left",
    )
    return [(r.string(), r.string()) for _ in range(count)]


def _read_destroyed_block(
    r: Reader, name: str, end: int, *, grouped: bool
) -> list[tuple[str, str]]:
    """The destroyed-actor list trailing a level's header block.

    Two shapes, and which one appears is decided by the level rather than by a flag in the
    file: a sub-level writes one bare list, and the persistent level writes the list grouped
    by the world-partition cell the actors lived in. Reading the wrong shape lands off the
    block's declared end, which the caller checks -- so this is verified, not assumed.
    """
    if not grouped:
        return _read_destroyed_refs(r, f"level {name!r}", end)
    at = r.pos
    groups = r.i32()
    _expect(
        0 <= groups <= 100_000,
        at,
        f"level {name!r}: its destroyed-actor list claims {groups} cell groups",
    )
    out: list[tuple[str, str]] = []
    for _ in range(groups):
        cell = r.string()
        out.extend(_read_destroyed_refs(r, f"level {name!r} cell {cell!r}", end))
    return out


def _read_block_size(r: Reader, save_version: int) -> int:
    """A level's TOC or data size: int32 below saveVersion 52, int64 at and above it.

    That the old width is 4 and not 8 is excluded outright rather than argued: on a 174-byte
    TOC the four bytes after the size are the header count ``1``, so an int64 reading would
    have to call ``174 + (1 << 32)`` a plausible block length.
    """
    return r.i64() if save_version >= FIRST_MODERN_BODY else r.i32()


def _read_level(r: Reader, *, named: bool, save_version: int) -> Level:
    """One level record. ``named=False`` is the persistent level at the very end.

    The two block sizes are the load-bearing part. Headers are walked one by one and
    must finish inside the TOC block; objects are walked one by one and must finish
    exactly ON the data block's declared end. The first catches a header layout change,
    the second catches a payload size that lies.
    """
    name = r.string() if named else "Persistent_Level"

    toc_size_at = r.pos
    toc_size = _read_block_size(r, save_version)
    toc_start = r.pos
    toc_end = toc_start + toc_size
    _expect(
        0 <= toc_size <= r.remaining,
        toc_size_at,
        f"level {name!r} declares a {toc_size}-byte header block, {r.remaining} left",
    )
    header_count = r.i32()
    _expect(
        0 <= header_count <= 10_000_000,
        r.pos - 4,
        f"level {name!r} claims {header_count} object headers",
    )
    headers: list[ActorHeader | ComponentHeader] = []
    for _ in range(header_count):
        _expect(
            r.pos < toc_end,
            r.pos,
            f"level {name!r}: header {len(headers)} of {header_count} starts past the "
            f"end of its {toc_size}-byte block",
        )
        headers.append(_read_header(r, save_version))
    extra = toc_end - r.pos
    _expect(
        extra >= 0,
        r.pos,
        f"level {name!r}: {header_count} headers overran the header block by {-extra} bytes",
    )
    # Whatever is left is a destroyed-actor list, in one of two shapes. It is read rather
    # than stepped over, and having read it the cursor must land exactly on the block's
    # declared end -- which is what says the shape is right and not merely plausible.
    #
    # Grouped by partition cell on the persistent level -- but only from saveVersion 52. There
    # is no world partition below that, so the old persistent record writes the same bare list
    # a sub-level does, and reading it as grouped turns the count into a string length.
    grouped = not named and save_version >= FIRST_MODERN_BODY
    destroyed = _read_destroyed_block(r, name, toc_end, grouped=grouped) if extra else []
    _expect(
        r.pos == toc_end,
        r.pos,
        f"level {name!r}: its destroyed-actor list ended at {r.pos}, but the header block "
        f"declared {toc_end}. The list's shape is wrong, not its length",
    )

    data_size_at = r.pos
    data_size = _read_block_size(r, save_version)
    data_start = r.pos
    data_end = data_start + data_size
    _expect(
        0 <= data_size <= r.remaining,
        data_size_at,
        f"level {name!r} declares a {data_size}-byte object block, {r.remaining} left",
    )
    object_count = r.i32()
    _expect(
        object_count == header_count,
        r.pos - 4,
        f"level {name!r} has {header_count} headers but {object_count} objects. They are "
        "parallel lists; a mismatch means one of the two blocks was misread",
    )
    objects = []
    for _ in range(object_count):
        slot = _read_object_entry(r, save_version)
        _expect(
            slot.end <= data_end,
            slot.offset - 4,
            f"level {name!r}: object {len(objects)} declares {slot.length} bytes, which "
            f"runs {slot.end - data_end} past the end of its block",
        )
        objects.append(slot)
        r.pos = slot.end
        if slot.version >= 60:
            trailing = r.i32()
            _expect(
                trailing == 0,
                r.pos - 4,
                f"level {name!r}: version {slot.version} object {len(objects) - 1} is "
                f"followed by {trailing}, and every one of the 39,015 in the reference "
                "save is followed by 0. Something after the payload is not understood",
            )
    _expect(
        r.pos == data_end,
        r.pos,
        f"level {name!r}: {object_count} object payloads ended at {r.pos}, but the block "
        f"declared {data_end}. One payload size is wrong",
    )

    return Level(
        name=name,
        headers=headers,
        objects=objects,
        toc_extra_bytes=extra,
        destroyed=destroyed,
    )


def _read_level_trailer(
    r: Reader, name: str, *, save_version: int, versioned_archive: bool
) -> list[tuple[str, str]]:
    """A sub-level's trailer: a version, a destroyed-actor list, and maybe a flag.

    On a saveVersion 60 body the flag is 1 on 1,905 of the reference save's 3,123
    sub-levels and an archive version header follows every one of them; it is 0 on the
    other 1,218 and none follows. That correlation is exact, and it is checked here
    rather than trusted: the header's own signature has to be there, or this raises.

    On a saveVersion 52 body there is no flag and no per-level archive headers -- the
    next level's name follows the destroyed-actor list directly. Reading the flag anyway
    consumed the name's length prefix and failed at the level after, which is how the
    difference showed up: "trailer flag 26, expected 0 or 1", 26 being a 25-character
    cell id plus its terminator.

    Below saveVersion 52 there is no version int32 either: the trailer is nothing but the
    destroyed-actor list. Over the 14 old saves that have level records, the header-block list
    and the trailer list are the same set on 12 and differ on 2 -- which is why both are read
    and merged rather than one being taken as authoritative.
    """
    if save_version >= FIRST_MODERN_BODY:
        version = r.i32()
        _expect(
            version in (52, 60),
            r.pos - 4,
            f"level {name!r} trailer version {version}, expected 52 or 60",
        )
    destroyed = _read_destroyed_refs(r, f"level {name!r} trailer", len(r.data))
    if not versioned_archive:
        return destroyed
    flag = r.i32()
    _expect(flag in (0, 1), r.pos - 4, f"level {name!r} trailer flag {flag}, expected 0 or 1")
    if flag:
        _read_archive_header(r)
        _read_custom_versions(r)
    return destroyed


def _read_final_destroyed_table(
    r: Reader, warnings: list[tuple[int, str]], save_version: int
) -> list[tuple[str, str]]:
    """The body's last structure: destroyed actors, grouped by level name.

    Parsed rather than skipped for one reason -- it is the only thing that can prove the
    whole walk consumed the file. The reference save's 1,229 closing bytes read as one
    group, ``Persistent_Level``, with a list of 5 references and then a list of 7, and
    they end on byte 44,376,211 with **nothing left over**. Getting the level count, 3,124
    block sizes and 44,634 payload sizes all wrong in a way that still lands exactly on
    the last byte is not a thing that happens by accident.

    Below saveVersion 52 it is one bare ``[i32 count][refs]`` list rather than a table grouped
    by level: 45 to 53 refs on a saveVersion 25 save, 0 to 18 on a 28, and 5 on every 30/36.
    Exactly one bare list lands on the last byte of all 21 shape-A bodies and exactly two on
    all 14 shape-B ones -- one list too few leaves bytes over and one too many runs off the
    end, so the count is measured rather than chosen.
    """
    if save_version < FIRST_MODERN_BODY:
        out = _read_destroyed_refs(r, "the closing destroyed-actor list", len(r.data))
        if r.remaining:
            warnings.append((r.pos, f"{r.remaining} bytes after the closing destroyed-actor list"))
        return out
    at = r.pos
    groups = r.i32()
    _expect(0 <= groups <= 100_000, at, f"the closing table claims {groups} level groups")
    out: list[tuple[str, str]] = []
    for _ in range(groups):
        name = r.string()
        for which in (1, 2):
            out.extend(
                _read_destroyed_refs(r, f"closing table, level {name!r}, list {which}", len(r.data))
            )
    if r.remaining:
        warnings.append((r.pos, f"{r.remaining} bytes after the closing destroyed-actor table"))
    return out


def _read_flat_levels(r: Reader, save_version: int) -> list[Level]:
    """Every object in a body written before the level list existed, grouped by its own level.

    Below saveVersion 30 the body holds one run of headers and one run of entries and says
    nothing about levels -- no count, no names, no block sizes. The level is in each header's
    ``root_object`` instead, and three values occur across the 21 such saves:
    ``Persistent_Level``, ``Persistent_Exploration`` and ``Persistent_Exploration_2``.

    Grouping by that field is a reading of the bytes, not an invention: the alternative is one
    level that has to be *called* something, and any name chosen for it would be wrong for the
    objects rooted elsewhere. First-appearance order, and order within a group is the file's, so
    ``header[i]`` still describes ``object[i]`` in every group.

    Neither run is bounded by a declared size here -- there is no block to end -- so the
    referee is the whole-body one: the closing destroyed-actor list has to land on the last
    byte, which it does on all 21.
    """
    at = r.pos
    header_count = r.i32()
    # Bounded by the bytes left rather than by a flat ceiling, because there is no enclosing
    # block to bound it here: an object header is an int32 kind plus three length-prefixed
    # strings, so the shortest conceivable one is 16 bytes. Same reasoning as ``_references``,
    # and the same reason -- a flat ceiling on a count that lives inside the payload lets a torn
    # file allocate for millions of records before anything notices.
    _expect(
        0 <= header_count <= (r.remaining) // 16,
        at,
        f"the body claims {header_count} object headers with {r.remaining} bytes left, and a "
        "header is at least sixteen",
    )
    headers = [_read_header(r, save_version) for _ in range(header_count)]

    at = r.pos
    object_count = r.i32()
    _expect(
        object_count == header_count,
        at,
        f"the body has {header_count} headers but {object_count} objects. They are parallel "
        "lists; a mismatch means the header run was misread",
    )
    slots = []
    for index in range(object_count):
        slot = _read_object_entry(r, save_version)
        _expect(
            slot.end <= len(r.data),
            slot.offset - 4,
            f"object {index} declares {slot.length} bytes, which runs "
            f"{slot.end - len(r.data)} past the end of the body",
        )
        slots.append(slot)
        r.pos = slot.end

    grouped: dict[str, Level] = {}
    for header, slot in zip(headers, slots, strict=True):
        level = grouped.get(header.root_object)
        if level is None:
            level = grouped[header.root_object] = Level(
                name=header.root_object, headers=[], objects=[]
            )
        level.headers.append(header)
        level.objects.append(slot)
    return list(grouped.values())


def read_body(body: bytes, save_version: int = FIRST_MODERN_BODY) -> SaveBody:
    """Walk the inflated body up to (not into) the property blocks.

    ``body`` is the concatenation of the inflated chunks. The returned slices index into
    it, so it must stay alive for as long as they are used -- nothing is copied, which is
    what keeps a 44 MB body at a quarter of a second.

    ``save_version`` picks which fields are there; see ``versions.py``. It is a parameter and
    not a sniff because **an old body does not say**: below saveVersion 52 an object entry
    carries no version of its own, so nothing in these bytes could supply one. The two modern
    layouts, 52 and 60, still tell themselves apart from the bytes, which is why the default is
    the modern one and why every existing caller keeps working unchanged.
    """
    old = save_version < FIRST_MODERN_BODY
    size_width = 4 if old else 8
    r = Reader(body)
    # A save truncated to exactly its header inflates to an EMPTY body -- `decompress_body`
    # has no chunks to walk and legitimately returns b"" -- and the size field below then
    # reported "read of 8 at 0 runs past end (0)", an offset of 0 in a buffer of 0 with no
    # hint that the body rather than the file is meant. That is the shape of a save the game
    # created and had not finished, so it is worth a sentence of its own.
    _expect(
        len(body) >= size_width,
        0,
        f"the inflated body is {len(body)} bytes, too short to hold the int{size_width * 8} "
        "size field it opens with -- a save truncated to its header inflates to nothing at all",
    )
    declared = r.i32() if old else r.i64()
    _expect(
        declared == len(body) - size_width,
        0,
        f"the body says it is {declared} bytes; {len(body) - size_width} follow the size field",
    )
    preamble = BodyPreamble(declared_size=declared, grids=[])
    if not old:
        # No world partition and no archive versioning below saveVersion 52: the level list --
        # or, below 30, the header run -- starts straight after the size.
        if _at_archive_header(r):
            fields, six, tag, branch = _read_archive_header(r)
            preamble.version_fields = fields
            preamble.unknown_six = six
            preamble.tag = tag
            preamble.branch = branch
            preamble.custom_versions = _read_custom_versions(r)
        preamble.grids = _read_grids(r)

    warnings: list[tuple[int, str]] = []
    levels: list[Level] = []
    trailer_destroyed: list[tuple[str, str]] = []

    if save_version < FIRST_LEVEL_LIST:
        levels = _read_flat_levels(r, save_version)
    else:
        sub_count = r.i32()
        _expect(
            0 <= sub_count <= 1_000_000,
            r.pos - 4,
            f"the body claims {sub_count} sub-levels",
        )
        for _ in range(sub_count):
            level = _read_level(r, named=True, save_version=save_version)
            levels.append(level)
            trailer_destroyed += _read_level_trailer(
                r,
                level.name,
                save_version=save_version,
                versioned_archive=preamble.has_archive_header,
            )

        # The persistent level closes the list: the same record with no name, and no trailer.
        # The body's last structure is a destroyed-actor table keyed by level name, which
        # takes its place.
        levels.append(_read_level(r, named=False, save_version=save_version))
        if old:
            # ...except on an old body, where the unnamed record is followed by one more bare
            # list before the closing one. Read here as that record's trailer, like every other
            # level's; see the module docstring for why the alternative reading -- two
            # ungrouped closing lists -- cannot be told apart from it on these 14 saves.
            trailer_destroyed += _read_destroyed_refs(
                r, "the persistent level's trailer", len(body)
            )

    closing_destroyed = _read_final_destroyed_table(r, warnings, save_version)

    return SaveBody(
        preamble=preamble,
        levels=levels,
        warnings=warnings,
        trailer_destroyed=trailer_destroyed,
        closing_destroyed=closing_destroyed,
    )
