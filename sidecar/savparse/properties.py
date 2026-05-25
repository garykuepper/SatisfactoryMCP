"""Unreal's tagged property serialiser: the inside of one object's property block.

``objects.py`` hands this module a slice and the promise that the slice is exactly one
object's payload. This module turns that into ``[[name, value], ...]``, which is the shape
``extract_save.props()`` reads.

## The tag, and why there are two of them

Every property announces itself before its bytes, and the announcement is what makes an
unknown property survivable: it carries a **declared size**, so a type this module has
never heard of costs that one property and nothing else. Two layouts occur, keyed by the
object's own serialisation version (36, 52 and 60 all appear in one file -- see
``objects.py``).

Object version **60**, which is UE5's ``FPropertyTag``::

    str  name
    ...  type name TREE          str name, i32 param count, then that many type names
    i32  size                    bytes of payload, counted from after the flags byte
    u8   flags                   see TAG_* below
    i32  array index             only when flags & TAG_ARRAY_INDEX
    16B  property guid           only when flags & TAG_PROPERTY_GUID
    ...  size bytes of payload

Object version **36/52**, which is UE4's::

    str  name
    str  type
    i32  size
    i32  array index
    ...  type-specific tag data  the inner type of an array, a struct's name and guid,
                                 an enum's name, a bool's VALUE
    u8   has property guid
    16B  property guid           only when that byte is 1
    ...  size bytes of payload

The two are unified into one ``TypeName`` tree before any value is read, so there is
exactly one value reader per property type rather than two. On version 60 the tree comes
straight off the wire; on 36/52 it is assembled from the tag data, where UE4 kept the same
information in fixed positions.

## The flags byte, which is the single most useful field here

Version 60's flags byte identifies itself from data. Three bits were read off the bytes and
each is confirmed by a value the game displays:

* ``0x10`` on a ``BoolProperty`` whose size is 0 and which has nowhere else to put its
  value -- so this bit **is** the bool. That is why ``extract_save.truthy`` documents
  BoolProperty arriving as "a uint8 where 16 means True": 16 is this bit, and 1 is the
  separate UE4 value byte from a version-36 object in the same file.
* ``0x08`` on exactly the structs whose payload is raw numbers rather than a nested
  property list -- ``Box`` (six doubles and a validity byte), ``Vector``, ``Guid``,
  ``InventoryItem`` -- and never on ``InventoryStack``, ``FeetOffset`` or
  ``FactoryCustomizationData``, which are nested property lists. It means "this type
  serialises itself".
* ``0x01`` on the rare property that repeats at a nonzero array index, where an extra
  int32 then appears before the payload. Without it the payload would start four bytes
  late, which the per-property size check catches immediately.

The size check is what makes all of this safe: after reading a property's value the cursor
**must** be exactly ``size`` bytes past the payload start, or this raises with the offset.
Getting a width wrong therefore fails on that property instead of shifting every property
after it.

## What is skipped rather than guessed

``0x08`` says a struct serialises itself but not how. Structs whose layout is known are in
``_NATIVE_STRUCTS``; one that is not is handed back as raw bytes and recorded in
``warnings``. Same for an unrecognised property type. The reference save and both other
saveVersion 60 saves produce **zero** warnings.

saveVersion 52 saves produce exactly **three**, all in the same place and all structural
rather than a gap in the table: UE4's tag data for a map or a set names the element's
*property* type and not the struct behind it (see ``_is_unnamed_struct``), so the foliage
subsystem's ``mSaveData`` and the scanner's two sets of ``Guid`` cannot be told apart from
property lists and are skipped by their declared size. The projection reads none of the
three. ``mItemsPickedUp``, ``mActorsBuiltCount`` and ``mItemsManuallyCraftedCount`` are in
the same bind and *are* recovered, by ``attempt`` -- read them as property lists and keep
the result only because it lands exactly on the declared end byte.

## Deliberate shape decisions

The values here are shaped to match what the projection already reads, quirks included,
because the projection is the consumer and a shape change there is a silent behaviour
change:

* ``BoolProperty`` yields the raw flags/value **byte** (16, 1 or 0), not a ``bool``.
  ``truthy()`` folds all three correctly and every reader goes through it.
* ``ByteProperty`` yields ``[enumName or None, value]`` -- ``_phase_costs`` takes
  ``[-1]`` off exactly that.
* A struct serialised as a nested property list yields ``[values, types]``, which is the
  two-list shape ``struct_fields()`` unwraps.
* ``InventoryItem`` yields ``[itemClassPath, state]``: the item class as a bare path
  string, because ``_accumulate_inventory`` reads ``fields["Item"][0]`` and runs
  ``ref_class`` on it, which wants a string and not an ``ObjectReference``.

## The object payload around the property list

The property list is not the whole payload. An actor opens with its parent reference and
its child-component references; a component opens with nothing. Version 60 then adds one
byte before the list on both -- the object-reference migration flag.

After the ``"None"`` terminator come trailing bytes, and their count is what tells a plain
object from one with class-specific data. On the reference save: **4** bytes on 17,382
actors and 113 components, **8** on 20,700 components and 3,230 actors, and more on
**3,209** actors -- 1,889 conveyor chains, 1,297 power lines, and the lightweight-buildable
subsystem, whose 3.1 MB of foundations and walls live there and appear in no header at all.
Those bytes are handed on as an offset and a length; ``props()`` never sees them. Decoding
them is ``savparse.lightweight`` and ``savparse.trailers``, reached lazily through
``actorSpecificInfo`` -- see ``savparse.save`` for why not here.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .objects import ObjectSlice, ParseError
from .reader import Reader

__all__ = [
    "TAG_ARRAY_INDEX",
    "TAG_BOOL_TRUE",
    "TAG_NATIVE_SERIALIZE",
    "TAG_PROPERTY_GUID",
    "ObjectReference",
    "ParsedObject",
    "TypeName",
    "read_object",
]

#: An int32 array index follows the flags byte. Written only when the index is nonzero,
#: which is why version-60 tags are usually four bytes shorter than version-36 ones.
TAG_ARRAY_INDEX = 0x01
#: A 16-byte property guid follows. Never seen set on any of the 31 readable saves; read
#: anyway because guessing that it cannot happen is exactly the kind of assumption that
#: fails on a patch day.
TAG_PROPERTY_GUID = 0x02
#: Reserved-looking bit, never seen set. Refused rather than ignored, see _read_tag_60.
TAG_EXTENSIONS = 0x04
#: The payload is the type's own binary form, not a nested property list.
TAG_NATIVE_SERIALIZE = 0x08
#: A BoolProperty's value, and the only place it is stored on a version-60 object.
TAG_BOOL_TRUE = 0x10

#: UE writes "None" as the name of the tag that ends a property list. It is a real tag
#: name, not a sentinel byte, so the list is walked and not searched.
_TERMINATOR = "None"

#: Guard on how deep property lists may nest inside one another. The deepest real one in
#: any of the 31 readable saves is **4** -- a struct array inside a struct inside a
#: property list -- so 32 is eight times the observed maximum and still small enough to
#: raise a ``ParseError`` long before CPython's recursion limit turns the same bytes into a
#: ``RecursionError`` the sidecar can only report as a class name.
_MAX_NESTING = 32

#: Bytes between the property list's ``"None"`` terminator and the end of an object's
#: payload. Measured over **all 1,243,288 objects of all 31 readable saves**: a COMPONENT is
#: 4 (5,300 of them) or 8 (562,556) and never more; an ACTOR is 4 (500,350), 8 (87,016) or
#: more (88,066, in exactly eight classes -- the four ``FGConveyorChainActor`` sizes,
#: ``Build_PowerLine_C``, and once per save each ``FGLightweightBuildableSubsystem``,
#: ``BP_CircuitSubsystem_C`` and ``BP_PlayerState_C``). Nothing is ever below 4.
_TRAILER_SIZES = (4, 8)

#: Guard on the type-name tree's branching factor. A MapProperty has two parameters and
#: nothing seen has more; 16 is loose enough to survive a patch and tight enough that a
#: misaligned cursor reading a float as a count fails here instead of allocating.
_MAX_TYPE_PARAMS = 16


@dataclass(slots=True)
class ObjectReference:
    """A reference to another object: the level it lives in and its full path.

    ``pathName`` is the spelling the projection reads (``extract_save.ref_path``), and
    ``__str__`` returns it so that a reference formats as the thing it points at.
    """

    level_name: str
    path_name: str

    @property
    def pathName(self) -> str:
        return self.path_name

    @property
    def levelName(self) -> str:
        return self.level_name

    def __str__(self) -> str:
        return self.path_name


@dataclass(slots=True)
class TypeName:
    """A property's type as a tree: ``ArrayProperty(StructProperty(InventoryStack(...)))``.

    Version 60 writes this tree literally. Version 36/52 writes the same information as
    fixed tag-data fields, and ``_read_tag_old`` reassembles it into this shape so that
    every value reader below is version-agnostic.
    """

    name: str
    params: list[TypeName] = field(default_factory=list)

    @property
    def inner(self) -> TypeName:
        """First parameter, or a nameless one -- an array whose element type went missing.

        Returning a placeholder rather than raising is deliberate: the element reader
        then hits the unknown-type path, warns, and the property is skipped by its size.
        """
        return self.params[0] if self.params else TypeName("")

    def flat(self) -> list:
        """The tree as a flat list: each node is its name then its parameter count.

        This is a rendering choice, not a format fact. It differs from the vendored
        parser's -- which folds the innermost parameters into a nested list and omits some
        counts -- and it is unambiguous where that one is not: ``[name, n, ...]`` can be
        read back into the tree it came from. Nothing consumes it beyond
        ``struct_fields()``, which only needs each entry to start with a name.
        """
        if not self.params:
            return [self.name, 0]
        return [self.name, len(self.params), *(x for p in self.params for x in p.flat())]


@dataclass(slots=True)
class ParsedObject:
    """One object's property block, decoded.

    ``properties`` is the list of ``[name, value]`` pairs the projection consumes;
    ``property_types`` is the parallel list of types, kept because it is the only record
    of what a value *was* once it has been flattened into Python.
    """

    version: int
    #: Actors only: the object this one hangs off, and its component children.
    parent_reference: ObjectReference | None = None
    child_references: list[ObjectReference] = field(default_factory=list)
    properties: list[list] = field(default_factory=list)
    property_types: list[list] = field(default_factory=list)
    #: Absolute offset and length of everything after the property list's terminator:
    #: a 4- or 8-byte trailer, plus class-specific binary data on 3,209 of the reference
    #: save's actors. Not decoded here; ``savparse.save`` decodes the classes it knows.
    extra_offset: int = 0
    extra_length: int = 0
    #: The trailing class-specific bytes, decoded -- ``None`` when nothing knows the class.
    #: Filled on first access to ``actorSpecificInfo`` rather than during the parse, because
    #: decoding every conveyor chain costs 22% of a whole save's parse for data the projection
    #: never asks for. ``decode_trailer`` is what does it; ``save.py`` sets it.
    actor_specific_info: list | None = None
    #: Zero-argument decoder for this object's trailing bytes, or ``None`` when no reader
    #: exists for its class. Set at composition time, since only there is the class known.
    decode_trailer: object | None = None
    #: Anything skipped rather than understood, as ``(offset, what)``.
    warnings: list[tuple[int, str]] = field(default_factory=list)

    @property
    def actorSpecificInfo(self) -> list | None:
        """The trailing class-specific bytes, decoded on first access. The spelling the
        projection reads.

        ``None`` rather than an empty list when no reader exists for the class: an empty list
        is what a decoded blob holding nothing looks like, and "nobody taught this parser that
        class" must not be able to pass for it.
        """
        if self.actor_specific_info is None and self.decode_trailer is not None:
            self.actor_specific_info = self.decode_trailer()
        return self.actor_specific_info


def _expect(condition: bool, offset: int, message: str) -> None:
    if not condition:
        raise ParseError(f"at body offset {offset}: {message}")


# --------------------------------------------------------------- primitives


def _reference(r: Reader) -> ObjectReference:
    """A level name and a path name, in that order.

    An empty reference is two zero int32s rather than a flag, which is why an unset
    ``mConnectedComponent`` costs 8 bytes and reads back as ``("", "")``.
    """
    return ObjectReference(r.string(), r.string())


def _soft_reference(r: Reader) -> list:
    """FSoftObjectPath: a package name, an asset name, and a sub-path.

    THREE strings, not two, which is what distinguished it from ``ObjectProperty``: the
    icon database's ``mGlobalIconLibraries`` declares 76 bytes for one element and
    ``4 + (4+48) + (4+12) + 4`` is exactly 76 -- package
    ``/Game/FactoryGame/-Shared/Blueprint/IconLibrary``, asset ``IconLibrary``, and an
    empty sub-path. Reading it as a plain reference leaves 4 bytes over and fails the
    size check.
    """
    return [ObjectReference(r.string(), r.string()), r.string()]


def _references(r: Reader, limit: int) -> list[ObjectReference]:
    """A counted list of references, bounded by the object's own payload.

    ``limit`` is the end of the payload this list has to fit inside, and the bound is what
    it can hold: a reference is two length-prefixed strings, so the shortest possible one is
    the eight bytes of two zero lengths. A flat ceiling was here first and was too generous
    to be a check -- see ``_Decoder._count`` for the same mistake measured on a container
    count, where a torn file cost 13.4 s and 79 MB before the size check caught it.
    """
    count = r.i32()
    _expect(
        0 <= count <= (limit - r.pos) // 8,
        r.pos - 4,
        f"a reference list claims {count} entries with {limit - r.pos} bytes of payload "
        "left, and a reference is at least eight",
    )
    return [_reference(r) for _ in range(count)]


# ----------------------------------------------------------- struct bodies


def _vector(d: _Decoder) -> list[float]:
    """FVector: three doubles.

    UE5 widened FVector from float to double, so this could in principle need the object's
    version -- and it does not. Every save this project can read was written by the UE5
    game, and the width is a property of the writer, not of the object version stamped in
    the entry: ``mRemovedWorldLocations`` on a **version 52** object holds 24-byte vectors
    (3 elements in a 72-byte block), and so does ``RemovedLocations`` on a version-60 one.
    No 12-byte FVector occurs in any of the 31 readable saves, and if one ever does the
    enclosing property's size check fails by exactly a factor of two rather than returning
    coordinates in the wrong hemisphere.
    """
    r = d.r
    return [r.f64(), r.f64(), r.f64()]


def _quat(d: _Decoder) -> list[float]:
    """FQuat: four doubles. Confirmed by ``Transform.Rotation``, a 32-byte payload."""
    r = d.r
    return [r.f64(), r.f64(), r.f64(), r.f64()]


def _box(d: _Decoder) -> list:
    """FBox: min, max, and a validity byte, which is why the list has 7 entries.

    Verified against a blueprint's ``mLocalBounds``, which reads
    ``[-1600, -1600, -0.0001, 1600, 1600, 800.0001, True]`` -- a 32 m x 32 m footprint 8 m
    tall, exactly the blueprint designer's grid, and the -0.0001 is the foundation's own
    thickness rounding.
    """
    return [*_vector(d), *_vector(d), d.r.i8() != 0]


def _linear_color(d: _Decoder) -> list[float]:
    """FLinearColor stayed four *floats* through the UE5 upgrade; FVector did not.

    Pinned by the game state's ``mBuildableLightColorSlots``: 7 elements in 112 bytes, and
    the values are the light-colour palette the build menu shows -- white, red, yellow,
    green, cyan, blue, magenta, each with alpha 1.
    """
    r = d.r
    return [r.f32(), r.f32(), r.f32(), r.f32()]


def _guid(d: _Decoder) -> list[int]:
    """16 bytes, reported as two uint64s -- the spelling the vendored parser uses."""
    r = d.r
    return [r.u64(), r.u64()]


def _int_vector(d: _Decoder) -> list[int]:
    """FIntVector: a world-partition cell coordinate, e.g. the foliage grid's ``[-7,-25,-1]``."""
    r = d.r
    return [r.i32(), r.i32(), r.i32()]


def _fluid_box(d: _Decoder) -> float:
    """FFluidBox is one float: the litres currently in a pipe segment.

    Four bytes, and the value on a running Build_Pipeline reads 8.25 -- pipe contents, in
    the same units the projection's ``inventories`` reports fluids in.
    """
    return d.r.f32()


def _client_identity_info(d: _Decoder) -> list:
    """``[offlineId, [[platform, idBytes], ...]]`` -- who owns a player state.

    A 32-hex-character offline id, then a count, then one (platform byte, length-prefixed
    blob) per platform the account is linked to. The blob is left as bytes: it is an
    account identifier, it is not ours to interpret, and nothing reads it.
    """
    r = d.r
    offline_id = r.string()
    count = r.i32()
    _expect(0 <= count <= 64, r.pos - 4, f"a client identity claims {count} platforms")
    out = []
    for _ in range(count):
        platform = r.i8()
        out.append([platform, r.bytes(r.i32())])
    return [offline_id, out]


def _inventory_item(d: _Decoder) -> list:
    """FInventoryItem: ``[itemClassPath, state]``.

    The bytes are an object reference to the item descriptor (empty level name, then the
    asset path), then an int32 that is 1 when the stack carries per-item state and 0
    otherwise. State, when present, is another object reference naming the state class
    plus a **sized, nested property list** -- a rifle in the player's arm slot carries
    ``/Script/FactoryGame.FGWeaponItemState`` with ``CurrentAmmoCount`` 20 and the
    ammunition class, which is the game's own ammo counter and is why this is worth
    reading rather than skipping.

    What comes out at element 0 is the **path string**, not an ``ObjectReference``,
    because ``_accumulate_inventory`` does ``ref_class(fields["Item"][0])`` and
    ``ref_class`` resolves a bare path string but would take the ``repr`` of a reference
    object and find nothing.
    """
    r = d.r
    item_class = _reference(r)
    has_state = r.i32()
    if not has_state:
        # Version **36** items -- and only 36 -- write a second int32 here, 0 on every one
        # of them. Version 52 does not, which is the one place in this module where 36 and
        # 52 differ: they share a tag layout but not this struct. Assuming "old" meant both
        # over-read 87 version-52 pickups by exactly four bytes each, and the size check
        # named every one of them.
        if d.version < 52:
            r.i32()
        return [item_class.path_name, None]
    state_class = _reference(r)
    size = r.i32()
    _expect(
        0 <= size <= r.remaining,
        r.pos - 4,
        f"an item state claims {size} bytes with {r.remaining} left",
    )
    values, types = d.property_list(r.pos + size)
    return [item_class.path_name, [state_class.path_name, values, types]]


#: Structs whose payload is raw numbers rather than a nested property list. The flags
#: byte says *that* a struct serialises itself; only a table can say *how*. A struct
#: missing from here is handed back as raw bytes with a warning, never guessed at.
#:
#: Keyed by name because the flag cannot be trusted to be per-element: the foliage
#: subsystem's ``mSaveData`` is one MapProperty with the flag set whose keys are native
#: ``IntVector`` and whose values are nested property lists.
#:
#: Everything in here was confirmed by arithmetic on a real payload -- a declared size
#: that the fields add up to exactly -- and nothing is here on the strength of its name.
#: ``Vector_NetQuantize`` looks like it belongs and does not: the map markers' ``Location``
#: is 135 bytes of tagged ``X``/``Y``/``Z`` DoubleProperties, not 24 bytes of vector.
#: Self-serialising structs kept as raw bytes ON PURPOSE, so that they do not show up as
#: warnings and hide a real one. Both are identity handles the projection has no use for:
#: ``PlayerInfoHandle`` is a fixed 5 bytes and appears 164 times as a buildable's
#: ``BuiltBy`` (which player placed it) plus three times on the game state, and
#: ``UniqueNetIdRepl`` once, nested. The vendored parser also reports them as bytes, so
#: this is a shared shrug rather than a divergence.
_OPAQUE_STRUCTS = frozenset({"PlayerInfoHandle", "UniqueNetIdRepl"})

_NATIVE_STRUCTS = {
    "Vector": _vector,
    "Quat": _quat,
    "Box": _box,
    "LinearColor": _linear_color,
    "Guid": _guid,
    "IntVector": _int_vector,
    "FluidBox": _fluid_box,
    "ClientIdentityInfo": _client_identity_info,
    "InventoryItem": _inventory_item,
}


# ---------------------------------------------------------------- the tags


def _is_unnamed_struct(type_name: TypeName) -> bool:
    """A struct the bytes never name, which is only possible on version 36/52.

    UE4's tag data for a map or a set carries the element's *property* type -- literally
    the string ``"StructProperty"`` -- and stops there. The struct's own name is not in the
    file, because the engine got it from reflection. Nothing then distinguishes a native
    ``IntVector`` key (12 raw bytes) from a struct written as a property list, and both
    occur in the very same map: the foliage subsystem's ``mSaveData`` on a saveVersion 52
    save has ``IntVector`` cell coordinates as keys and property lists as values.

    Guessing costs the object; the map's declared size is right there, so it is skipped
    instead. Version 60 has no such problem -- the type tree names the struct -- and
    nothing above this reads the foliage data on any version.
    """
    return type_name.name == "StructProperty" and not type_name.params


def _read_type_name(r: Reader, depth: int = 0) -> TypeName:
    """Version 60's type tree: a name, a parameter count, then that many subtrees.

    ``StructProperty(InventoryStack(/Script/FactoryGame))`` is how a struct carries both
    its name and its package, and the package is a parameter of the struct name rather
    than of the property. That nesting is what identified this as a tree and not a flat
    list of extra fields.
    """
    at = r.pos
    name = r.string()
    count = r.i32()
    _expect(
        0 <= count <= _MAX_TYPE_PARAMS,
        r.pos - 4,
        f"type name {name!r} at {at} claims {count} parameters; a MapProperty has two "
        "and nothing seen has more, so the cursor is not on a tag",
    )
    _expect(depth < 8, at, f"type name {name!r} nested more than 8 deep")
    return TypeName(name, [_read_type_name(r, depth + 1) for _ in range(count)])


@dataclass(slots=True)
class _Tag:
    name: str
    type: TypeName
    size: int
    index: int
    flags: int
    #: Version 36/52 only: a BoolProperty keeps its value in the tag, where version 60
    #: keeps it in the flags byte. Normalised into ``flags`` by the reader.
    bool_value: int = 0


def _read_tag_60(r: Reader) -> _Tag:
    """Version 60's tag. The terminator is a bare name with no type after it.

    That asymmetry cost the first run of this module 16,445 objects: reading the type tree
    before checking the name turned the four trailing bytes after ``"None"`` into a string
    length and a parameter count, and every property size after it was then measured from
    the wrong place.
    """
    name = r.string()
    if name == _TERMINATOR:
        return _Tag(name=name, type=TypeName(""), size=0, index=0, flags=0)
    tag = _Tag(name=name, type=_read_type_name(r), size=0, index=0, flags=0)
    tag.size = r.i32()
    _expect(tag.size >= 0, r.pos - 4, f"property {tag.name!r} declares size {tag.size}")
    # The bit is checked before the fields it would move, so the offset in the message is
    # the flags byte itself. Checking it afterwards named an offset 4 or 20 bytes later --
    # the end of the array index or of the guid -- and an offset that points at the wrong
    # field is worse than none in a format where every field is positional.
    at_flags = r.pos
    tag.flags = r.i8()
    _expect(
        not tag.flags & TAG_EXTENSIONS,
        at_flags,
        f"property {tag.name!r} sets tag bit 0x04, which is unset on every property of "
        "every save checked and whose payload is therefore unknown",
    )
    if tag.flags & TAG_ARRAY_INDEX:
        tag.index = r.i32()
    if tag.flags & TAG_PROPERTY_GUID:
        r.skip(16)
    return tag


def _read_tag_old(r: Reader) -> _Tag:
    """UE4's tag. Same information, different places -- see the module docstring.

    The one field that has no version-60 counterpart is the type-specific tag data, and
    it is read here rather than in the value readers so that everything below this line
    sees a single tag shape.
    """
    name = r.string()
    if name == _TERMINATOR:
        return _Tag(name=name, type=TypeName(""), size=0, index=0, flags=0)
    type_name = r.string()
    size = r.i32()
    _expect(size >= 0, r.pos - 4, f"property {name!r} declares size {size}")
    index = r.i32()

    params: list[TypeName] = []
    bool_value = 0
    if type_name in ("ArrayProperty", "SetProperty", "ByteProperty", "EnumProperty"):
        params = [TypeName(r.string())]
    elif type_name == "MapProperty":
        params = [TypeName(r.string()), TypeName(r.string())]
    elif type_name == "StructProperty":
        params = [TypeName(r.string())]
        r.skip(16)  # the struct's guid, zero on every occurrence seen
    elif type_name == "BoolProperty":
        bool_value = r.i8()

    has_guid = r.i8()
    _expect(
        has_guid in (0, 1),
        r.pos - 1,
        f"property {name!r} has a property-guid flag of {has_guid}; UE4 writes 0 or 1 "
        "there, so the tag data for this type is longer than assumed",
    )
    if has_guid:
        r.skip(16)
    # No native-serialise bit exists in this layout -- UE4 decided a struct's shape from
    # the struct itself, so `_NATIVE_STRUCTS` is the whole authority on version 36/52 and
    # an unrecognised struct is read as a property list. Claiming the bit here instead
    # skipped 1,590 creature spawners' `SpawnData`, which is a plain property list.
    return _Tag(
        name=name,
        type=TypeName(type_name, params),
        size=size,
        index=index,
        flags=0,
        bool_value=bool_value,
    )


# -------------------------------------------------------------- the values


class _Decoder:
    """Reads one object's properties. Holds the version and the warnings list.

    A class rather than a pile of functions taking six arguments each: the object version
    picks float widths and tag layout, and it has to reach every nested reader.
    """

    __slots__ = ("depth", "old", "r", "version", "warnings")

    def __init__(self, r: Reader, version: int, warnings: list[tuple[int, str]]) -> None:
        self.r = r
        self.version = version
        self.warnings = warnings
        self.old = version < 60
        self.depth = 0

    # -- the list ---------------------------------------------------------

    def property_list(self, limit: int) -> tuple[list[list], list[list]]:
        """Read tags until the ``"None"`` terminator, refusing to run past ``limit``.

        ``limit`` is the end of the enclosing block or struct. A property list that is
        not terminated inside it means a size was wrong somewhere above, and stopping at
        the limit turns that into one loud failure instead of a wander through the next
        object's bytes.

        The depth guard is the other half of that, and it is here rather than in
        ``struct`` because every route back into a nested list goes through this method --
        a struct, an array element, a map value, an ``InventoryItem``'s weapon state.
        Without it a payload holding nothing but nested ``StructProperty`` tags recurses
        once per tag and dies of ``RecursionError`` instead of ``ParseError``: measured at
        29,010 crafted bytes, and written into a real save it came out of the sidecar as
        ``{"error": "RecursionError", "detail": "maximum recursion depth exceeded"}`` with a
        traceback on stderr and **no byte offset** -- precisely the report ``errors.py``
        exists to abolish. ``_read_type_name`` already guards its own recursion the same
        way; this is the guard it was missing a partner for.
        """
        _expect(
            self.depth < _MAX_NESTING,
            self.r.pos,
            f"property lists nested more than {_MAX_NESTING} deep; the deepest in any of "
            "the 31 readable saves is 4, so the cursor is not on a property tag",
        )
        self.depth += 1
        try:
            return self._property_list(limit)
        finally:
            self.depth -= 1

    def _property_list(self, limit: int) -> tuple[list[list], list[list]]:
        """The loop itself. Split out only so the depth guard can wrap it."""
        r = self.r
        values: list[list] = []
        types: list[list] = []
        while True:
            _expect(
                r.pos < limit,
                r.pos,
                f"a property list ran to {limit} without its {_TERMINATOR!r} terminator",
            )
            tag = _read_tag_old(r) if self.old else _read_tag_60(r)
            if tag.name == _TERMINATOR:
                return values, types
            _expect(
                r.pos + tag.size <= limit,
                r.pos,
                f"property {tag.name!r} declares {tag.size} bytes, which runs "
                f"{r.pos + tag.size - limit} past the end of its block",
            )
            start = r.pos
            end = start + tag.size
            value = self.value(tag, end)
            _expect(
                r.pos == end,
                r.pos,
                f"property {tag.name!r} of type {tag.type.name!r} declared {tag.size} "
                f"bytes but {r.pos - start} were read",
            )
            values.append([tag.name, value])
            types.append([tag.name, *tag.type.flat(), tag.flags])

    # -- one property -----------------------------------------------------

    def value(self, tag: _Tag, end: int):
        """Dispatch on the type name. ``end`` is where the payload must stop."""
        r = self.r
        name = tag.type.name
        reader = _SCALARS.get(name)
        if reader is not None:
            return reader(r)
        if name == "BoolProperty":
            # Version 60 keeps the value in the flags byte and writes no payload at all;
            # version 36/52 keeps it in the tag data. Either way the raw byte is what
            # comes out -- 16, 1 or 0 -- because `truthy()` is what reads it.
            return tag.bool_value if self.old else (tag.flags & TAG_BOOL_TRUE)
        if name in ("ObjectProperty", "InterfaceProperty"):
            return _reference(r)
        if name == "SoftObjectProperty":
            return _soft_reference(r)
        if name == "ByteProperty":
            return self.byte_value(tag)
        if name == "EnumProperty":
            return [self.enum_name(tag), r.string()]
        if name == "StructProperty":
            native = bool(tag.flags & TAG_NATIVE_SERIALIZE)
            return self.struct(tag.type.inner, native_hint=native, end=end)
        if name == "ArrayProperty":
            return self.array(tag, end)
        if name == "SetProperty":
            return self.set_(tag, end)
        if name == "MapProperty":
            return self.map_(tag, end)
        if name == "TextProperty":
            return self.text(end)
        return self.unknown(f"property type {name!r}", end)

    def unknown(self, what: str, end: int):
        """Skip to ``end`` and say so. The escape hatch the whole design rests on.

        ``end`` is always a **declared** end -- a property's own size, or the size of the
        container an untagged element sits in -- so skipping forwards to it is honest and
        costs one property. Skipping *backwards* is not honest, and it is the one way this
        escape hatch can produce plausible nonsense rather than a gap: it means the cursor
        is already past the end this skip was handed, which happens when an untagged
        element of a container was skipped by the whole container's length and the elements
        after it were then read from the wrong place. Left unchecked the cursor is dragged
        back to ``end``, the container lands exactly on its declared end, the size check is
        satisfied, and a map comes back with fabricated keys and ``None`` values. A
        hand-built ``map<int, array<Vector>>`` did exactly that -- two pairs, both values
        ``None``, the second key read out of the terminator's length prefix, no error.

        No save on this disk reaches it: over 1,243,288 objects the only types that ever
        arrive at ``element`` are ``ObjectProperty``, ``StructProperty``, ``IntProperty``
        and ``Int64Property``, all of which have readers, and a set's elements are read by
        ``array`` where the skip is bounded by the set's own size and is honest. So this is
        the guard for the patch that puts a container inside a container, and what it buys
        is that the failure arrives as an offset instead of as a shorter factory.
        """
        r = self.r
        _expect(
            end >= r.pos,
            r.pos,
            f"cannot skip {what}: the cursor is already {r.pos - end} bytes past the end "
            "this skip was given, so an untagged element earlier in the same container was "
            "skipped by the container's own length and everything after it was read from "
            "the wrong place",
        )
        self.warnings.append((r.pos, f"skipped {end - r.pos} bytes: {what}"))
        r.pos = end

    def attempt(self, what: str, end: int, decode):
        """Try ``decode``, and keep the result only if it consumed the block exactly.

        The one place in this module where something is *guessed*, and it is a guess with a
        referee: the property declared its length, so a reading that lands on the declared
        end byte-for-byte over hundreds of elements is right, and one that does not is
        discarded and skipped instead. Used for version-36/52 maps and sets, whose element
        struct types the bytes do not name (see ``_is_unnamed_struct``) -- reading them as
        property lists recovers ``mItemsPickedUp`` and ``mActorsBuiltCount``, and correctly
        gives up on ``mSaveData``, whose keys are raw ``IntVector``.
        """
        start = self.r.pos
        mark = len(self.warnings)
        try:
            value = decode()
        except ValueError:
            pass
        else:
            if self.r.pos == end:
                return value
        del self.warnings[mark:]
        self.r.pos = start
        return self.unknown(what, end)

    # -- enums ------------------------------------------------------------

    def enum_name(self, tag: _Tag) -> str | None:
        """The enum a Byte/Enum property is typed by, or ``None`` for a plain byte.

        UE4 writes the literal string ``"None"`` there for a byte that is not an enum; UE5
        writes no parameter at all. Both collapse to Python ``None`` so that the same field
        has the same shape whichever version an object was written at. That is 19,492
        properties across the 25 saveVersion 52 saves, nearly all of them ``FeetIndex`` on
        building legs, where the vendored parser reports the string and this reports
        ``None``. Element 0 is unread: ``_phase_costs`` takes ``[-1]``.
        """
        inner = tag.type.inner.name
        return inner if inner and inner != _TERMINATOR else None

    def byte_value(self, tag: _Tag):
        """``[enumName, value]`` -- a name when the byte is an enum, else a raw byte.

        Both shapes are real and both are read: ``mLastAutoSaveId`` is a plain byte
        (``[None, 2]``) while ``mGamePhaseCosts[].gamePhase`` is an ``EGamePhase``
        (``['EGamePhase', 'EGP_MidGame']``), and ``_phase_costs`` takes ``[-1]`` off
        whichever it gets.
        """
        enum = self.enum_name(tag)
        return [enum, self.r.string() if enum else self.r.i8()]

    # -- structs ----------------------------------------------------------

    def struct(self, struct_type: TypeName, *, native_hint: bool, end: int):
        """A struct: raw numbers if its name is in the table, else a property list.

        The NAME decides, not the flags bit. The bit says a struct serialises itself but
        not how, and it is not reliably per-element: the foliage subsystem's ``mSaveData``
        is one MapProperty with the bit set whose keys are native ``IntVector`` and whose
        values are nested property lists. It is used only as the second opinion that turns
        an unrecognised struct into a skip instead of a misparse.
        """
        native = _NATIVE_STRUCTS.get(struct_type.name)
        if native is not None:
            return native(self)
        if native_hint:
            # Hand back the bytes rather than None: the caller can see what it got, the
            # enclosing size check still balances, and it is what the vendored parser
            # reports for the same structs (`mPublicTodoListLastEditedBy` reads
            # b'\x06\x00\x00\x00\x00' in both).
            if struct_type.name not in _OPAQUE_STRUCTS:
                self.warnings.append(
                    (self.r.pos, f"struct {struct_type.name!r} serialises itself, kept as bytes")
                )
            return self.r.bytes(end - self.r.pos)
        return list(self.property_list(end))

    # -- containers -------------------------------------------------------

    def _count(self, what: str, end: int) -> int:
        """An element count, bounded by the bytes its own block has left.

        The tag's declared size is checked *before* the payload is read, but the count
        lives INSIDE the payload, so on a file the game is halfway through rewriting it can
        be any int32 at all. The smallest element of any container is one byte -- a
        ``BoolProperty`` element is exactly that -- so a count larger than the bytes left in
        the block cannot be true, and refusing it here is the difference between a message
        with an offset and a walk through the rest of the body.

        Measured, because the old bound of 10,000,000 sounded strict and was not: a count
        of 9,000,000 planted in a 12-byte array property inside a 40 MB body read
        36,000,004 bytes of the *following* objects, allocated 79 MB, and took **13.4 s**
        before the per-property size check noticed. With the bound it fails on the count.
        """
        r = self.r
        count = r.i32()
        _expect(
            0 <= count <= end - r.pos,
            r.pos - 4,
            f"{what} claims {count} elements with {end - r.pos} bytes left in its block, "
            "and no element of a container is shorter than one byte",
        )
        return count

    def array(self, tag: _Tag, end: int):
        """``i32 count`` then the elements, with no tag of their own.

        The element type comes from the array's own type tree, so a 200-element array of
        floats costs 4 bytes of framing and not 200 tags. Structs are the one exception
        on version 36/52, which repeats a struct header once for the whole array.
        """
        r = self.r
        count = self._count(f"array {tag.name!r}", end)
        inner = tag.type.inner
        if inner.name == "StructProperty":
            return self.struct_array(tag, count, end)
        element = _SCALARS.get(inner.name)
        if element is not None:
            return [element(r) for _ in range(count)]
        if inner.name in ("ObjectProperty", "InterfaceProperty"):
            return [_reference(r) for _ in range(count)]
        if inner.name == "SoftObjectProperty":
            return [_soft_reference(r) for _ in range(count)]
        if inner.name == "BoolProperty":
            return [r.i8() for _ in range(count)]
        if inner.name == "ByteProperty":
            # An array of bytes is bytes, with no per-element enum name to consult.
            return list(r.bytes(count))
        if inner.name == "EnumProperty":
            return [r.string() for _ in range(count)]
        if inner.name == "TextProperty":
            return [self.text(end) for _ in range(count)]
        return self.unknown(f"array of {inner.name!r}", end)

    def struct_array(self, tag: _Tag, count: int, end: int):
        """The elements of a struct array, which the two versions frame differently.

        Version 60 writes the struct's type in the array's type tree and then nothing but
        the elements. Version 36/52 writes a full property tag *inside* the payload --
        name, ``StructProperty``, the total size of all elements, the struct name and a
        guid -- and only then the elements. That inner tag is where a version-36 array
        keeps the struct's name, so it has to be read rather than skipped.
        """
        r = self.r
        struct_type = tag.type.inner.inner
        native = bool(tag.flags & TAG_NATIVE_SERIALIZE)
        if self.old:
            inner = _read_tag_old(r)
            _expect(
                inner.type.name == "StructProperty",
                r.pos,
                f"array {tag.name!r} of structs has an inner tag of type "
                f"{inner.type.name!r}, expected StructProperty",
            )
            struct_type = inner.type.inner
            native = False
            end = min(end, r.pos + inner.size)
        if native and struct_type.name not in _NATIVE_STRUCTS:
            # One unknown element cannot be skipped -- elements have no size of their own
            # -- but the array does have one, so the whole array goes and the object
            # survives. This is the case a game patch adding a struct would land in.
            return self.unknown(f"array of self-serialising {struct_type.name!r}", end)
        return [self.struct(struct_type, native_hint=native, end=end) for _ in range(count)]

    def set_(self, tag: _Tag, end: int):
        """``i32 removed, i32 count`` then the elements, reported as ``[type, values]``.

        The leading int32 is UE's "keys to remove" list, which a save never has content
        for -- it is 0 on every set in every save checked -- and is refused rather than
        skipped so that the day it is not, this says so.
        """
        inner = tag.type.inner
        if self.old and _is_unnamed_struct(inner):
            return self.attempt(
                f"version-{self.version} set {tag.name!r} of unnamed structs",
                end,
                lambda: self._set_body(tag, inner, end),
            )
        return self._set_body(tag, inner, end)

    def _set_body(self, tag: _Tag, inner: TypeName, end: int):
        """Split out from ``set_`` only so ``attempt`` can run it and throw it away."""
        r = self.r
        removed = r.i32()
        _expect(
            removed == 0,
            r.pos - 4,
            f"set {tag.name!r} declares {removed} removed elements; a saved set has no "
            "removal list and every one checked writes 0 here",
        )
        values = self.array(
            _Tag(tag.name, TypeName("ArrayProperty", [inner]), 0, 0, tag.flags), end
        )
        return [inner.name, values]

    def map_(self, tag: _Tag, end: int):
        """``i32 removed, i32 count`` then key/value pairs, reported as ``[[k, v], ...]``.

        Keys and values each carry their own type from the map's type tree and neither has
        a tag, so this is the one container where the element readers have to be called
        with a type that came from two levels up.

        What the elements are, measured over all 31 saves: ``ObjectProperty -> Int64Property``
        (``BuiltPerPlayer``), ``ObjectProperty -> StructProperty``
        (``mItemsManuallyCraftedCount``), ``ObjectProperty -> IntProperty``,
        ``StructProperty -> StructProperty`` (the foliage subsystem's ``mSaveData``) and
        ``IntProperty -> IntProperty``. So a map value is never itself a container:
        ``mItemsPickedUp`` looks like a map of maps and is not -- its value is a struct, and
        the inner map is a normal tagged property inside that struct's property list, which
        is why ``element`` needs no container branch. If one ever appears it now raises with
        an offset instead of being skipped by the whole map's length; see ``unknown``.
        """
        r = self.r
        _expect(
            len(tag.type.params) == 2,
            r.pos,
            f"map {tag.name!r} has {len(tag.type.params)} type parameters, expected a "
            "key type and a value type",
        )
        key_type, value_type = tag.type.params
        if self.old and (_is_unnamed_struct(key_type) or _is_unnamed_struct(value_type)):
            return self.attempt(
                f"version-{self.version} map {tag.name!r} of unnamed structs",
                end,
                lambda: self._map_body(tag, key_type, value_type, end),
            )
        return self._map_body(tag, key_type, value_type, end)

    def _map_body(self, tag: _Tag, key_type: TypeName, value_type: TypeName, end: int):
        """Split out from ``map_`` only so ``attempt`` can run it and throw it away."""
        r = self.r
        removed = r.i32()
        _expect(
            removed == 0,
            r.pos - 4,
            f"map {tag.name!r} declares {removed} removed keys; every map checked writes 0",
        )
        count = self._count(f"map {tag.name!r}", end)
        out = []
        for _ in range(count):
            key = self.element(key_type, end)
            value = self.element(value_type, end)
            out.append([key, value])
        return out

    def element(self, type_name: TypeName, end: int):
        """One untagged value of a known type: a map key or a map value.

        The map's flags byte is deliberately NOT passed on. It is set when *either* side
        serialises itself, so on ``mSaveData`` -- native ``IntVector`` keys, property-list
        values -- passing it on skipped 448 cells' worth of removed foliage. The name
        table is the authority, and a struct that is native but unlisted fails loudly
        here rather than being silently dropped.
        """
        r = self.r
        scalar = _SCALARS.get(type_name.name)
        if scalar is not None:
            return scalar(r)
        if type_name.name in ("ObjectProperty", "InterfaceProperty"):
            return _reference(r)
        if type_name.name == "SoftObjectProperty":
            return _soft_reference(r)
        if type_name.name == "StructProperty":
            return self.struct(type_name.inner, native_hint=False, end=end)
        if type_name.name == "ByteProperty":
            return r.i8()
        if type_name.name == "EnumProperty":
            return r.string()
        return self.unknown(f"untagged {type_name.name!r}", end)

    # -- text -------------------------------------------------------------

    def text(self, end: int):
        """FText, reported as ``[flags, historyType, hasCultureInvariant, string]``.

        Only history type 0xFF (none) occurs -- 576 texts over all 31 saves and not one
        other value -- because every ``mBlueprintName`` and sign label is a plain string
        the player typed rather than a localised lookup. Any other history type is skipped
        by size rather than half-decoded into a wrong label.

        The skip goes through ``unknown`` for its refusal to move backwards, which is what
        makes the skip safe inside an ARRAY of texts: an array element has no size of its
        own, so a foreign history on the *first* of several eats the rest of the array and
        the second element then trips that guard with an offset. A one-element array lands
        exactly on its declared end and is still recovered. Before the guard, an array of
        two texts came back as ``[[0, 3], [5, 78]]`` -- both entries invented, no warning
        that anything was wrong beyond the two history bytes it could not read.
        """
        r = self.r
        flags = r.i32()
        history = r.i8()
        if history != 0xFF:
            self.unknown(f"FText history type {history}", end)
            return [flags, history]
        has_invariant = r.i32()
        return [flags, history, has_invariant, r.string() if has_invariant else None]


#: Types whose payload is one fixed-width value, read the same way everywhere -- as a
#: tagged property, an array element, a map key -- because none of them has any framing
#: beyond its own width.
#:
#: Counts on the reference save: Int 41,135, Float 9,508, UInt32 1,550, Int64 656, Int8 167,
#: Str 26, Double 18 and Name 4. ``UInt64Property`` is here for symmetry with the observed
#: ``UInt32Property`` and has never appeared; the 16-bit variants are deliberately absent,
#: since an unrecognised type is skipped with a warning and that is a better outcome than a
#: reader nobody has ever run against real bytes.
#:
#: ``Int8Property`` yields raw ``bytes`` rather than an int, matching what the vendored
#: parser reports. Nothing in the projection reads one -- the only occurrences are
#: ``mSelectedPoleVersion`` on conveyor poles -- so this is compatibility, not a claim that
#: bytes are the right type for a signed byte.
_SCALARS = {
    "IntProperty": Reader.i32,
    "Int64Property": Reader.i64,
    "UInt64Property": Reader.u64,
    "UInt32Property": Reader.u32,
    "Int8Property": lambda r: r.bytes(1),
    "FloatProperty": Reader.f32,
    "DoubleProperty": Reader.f64,
    "StrProperty": Reader.string,
    "NameProperty": Reader.string,
}


def read_object(body: bytes, slot: ObjectSlice, *, actor: bool) -> ParsedObject:
    """Decode one object's property block.

    ``body`` and ``slot`` are what ``read_body`` produced; ``actor`` comes from the
    object's header, because the payload's opening reference lists exist only on actors
    and nothing in the payload itself says which kind this is.
    """
    r = Reader(body, slot.offset)
    end = slot.end
    out = ParsedObject(version=slot.version)

    if actor:
        out.parent_reference = _reference(r)
        out.child_references = _references(r, end)
    if slot.version >= 60:
        # The object-reference migration flag. One byte, 0 on every object of every save
        # checked, and the reason a version-60 payload is one byte longer than a
        # version-52 one holding the same properties.
        r.i8()

    decoder = _Decoder(r, slot.version, out.warnings)
    out.properties, out.property_types = decoder.property_list(end)
    out.extra_offset = r.pos
    out.extra_length = end - r.pos

    # The one check this layer was missing, and the only one that looks at the payload as a
    # WHOLE rather than one property at a time. Every property is size-checked, so a wrong
    # width fails on the property that has it -- but nothing was checking that the list
    # ended where the payload does, because the leftover is a legitimate 4 or 8 bytes on an
    # ordinary object and megabytes on a buildable that carries class-specific data. So the
    # leftover absorbed anything: overwriting one property's NAME with the list's ``"None"``
    # terminator (nine bytes) made a ``Build_ConstructorMk1`` read as 2 properties instead
    # of 13 with the remaining 1,352 bytes silently filed as trailing data, no error and no
    # warning. Through the sidecar that came out as **exit 0 and a complete projection** --
    # 44,634 objects, 438 machines, and one constructor with no recipe and no inventories.
    # A save that reads cleanly and reports a different factory is the single outcome this
    # parser is not allowed to produce.
    #
    # What can be checked without inventing structure is bounded by `_TRAILER_SIZES`:
    #
    # * nothing has a trailer shorter than 4, over 1.24 million objects, so a list that
    #   terminates ON the payload's end byte is wrong whatever the object is;
    # * a component's trailer is 4 or 8 and never more, over 567,856 of them, so for
    #   components -- 46% of a save -- the check is exact.
    #
    # An ACTOR's is deliberately left unchecked. Bounding it needs the eight class names
    # that legitimately carry more, and that whitelist belongs to whoever decodes those
    # bytes rather than to a guard written before them: refusing a save because a patch
    # taught a ninth class to carry data would be a worse failure than the one being
    # prevented. `_TRAILER_SIZES` is where to extend this once they are decoded.
    _expect(
        out.extra_length >= _TRAILER_SIZES[0],
        r.pos,
        f"the property list of {'an actor' if actor else 'a component'} ended "
        f"{out.extra_length} bytes before its {slot.length}-byte payload does, and every "
        f"one of 1,243,288 objects leaves at least {_TRAILER_SIZES[0]}. A property name was "
        "read as the list terminator, so the properties after it are missing",
    )
    if not actor:
        _expect(
            out.extra_length in _TRAILER_SIZES,
            r.pos,
            f"a component's property list left {out.extra_length} bytes of its "
            f"{slot.length}-byte payload unread; every one of 567,856 components in the 31 "
            f"readable saves leaves exactly {' or '.join(map(str, _TRAILER_SIZES))}, so the "
            "list terminated early and the properties after that point are missing",
        )
    return out
