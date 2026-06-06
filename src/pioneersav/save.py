"""One call that reads a whole .sav: the entry point ``extract_save.py`` actually uses.

The four layers below this one each stop at a boundary that made them testable in
isolation -- ``header`` reads a 64 KiB prefix, ``chunks`` inflates, ``objects`` walks to the
property blocks, ``properties`` decodes one block. Nothing composed them, so the sidecar
could not switch parsers. This does, in the exact shape the projection was written against:
``save.levels[i].actorAndComponentObjectHeaders`` parallel to ``save.levels[i].objects``,
each object carrying ``properties`` as ``[name, value]`` pairs.

**Why the composition is its own module rather than a few lines in the sidecar.** The body
has to stay alive for the whole parse -- every ``ObjectSlice`` and every
``ParsedObject.extra_offset`` is an absolute index into it, and nothing is copied, which is
what keeps a 44 MB body at a fifth of a second. That is a lifetime rule, and a lifetime rule
belongs somewhere it can be written down next to the thing it constrains.

**Nothing here translates exceptions, and the reason is worth keeping.** ``header`` and
``chunks`` predate ``ParseError`` and used to raise plain ``ValueError``, so this module was
first written to catch and re-raise them with the stage named. That was the wrong fix: half
the parser's failures come out of ``Reader._take``, the bottom layer, where no stage name is
in scope at all -- and those are the commonest ones, because a walk running off the end is
what a file the game is rewriting looks like. ``ParseError`` moved into ``errors``, below the
reader; every layer raises it, and every layer's messages already say which layer they are
("saveHeaderType 14, saveVersion 60: ...", "chunk at 453 ...", "at body offset 71578: ...").

**Cost, measured on the reference save in a fresh process** (2.9 MB on disk, 44.4 MB
inflated, 44,634 objects): read + header 0.001 s, inflate 0.048 s, level walk 0.228 s, all
the property blocks 1.72 s -- **1.99 s** against the vendored parser's 2.32 s for the same
work. Whole sidecar including interpreter start, 2.24 s against 2.99 s. The properties are
86% of it, so "decompression is not the cost" holds; inflate is 2%.

A third of the property time is *retention*, not parsing: 1.08 s if each block is discarded
as it is read, 1.72 s and ~136 MB to keep all 44,634. The projection makes exactly one pass,
so a streaming version of this would pay for itself -- recorded rather than done, because it
would change the shape the trailing-bytes stage builds on.

**The trailing class-specific bytes are decoded lazily**, and this module is where the class
is matched to a reader -- ``pioneersav.lightweight`` for the subsystem holding every foundation,
``pioneersav.trailers`` for the other seven classes. Nothing is decoded during the parse: the
conveyor chains alone would add 22% to it (0.46 s on the reference save) for data no
projection field reads, so ``ParsedObject.actorSpecificInfo`` decodes on first access instead.
A class with no reader leaves it ``None`` rather than an empty list, so "nobody taught this
parser that class" cannot be mistaken for "decoded, and there was nothing in it".
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import partial

from .chunks import decompress_body
from .header import SaveInfo, read_info_bytes
from .lightweight import LIGHTWEIGHT_SUBSYSTEM, read_lightweight
from .objects import ActorHeader, ComponentHeader, read_body
from .properties import ParsedObject, read_object
from .trailers import TRAILER_READERS, read_trailer
from .versions import FIRST_MODERN_BODY

__all__ = [
    "PLAIN_TRAILER",
    "UNDECODED_TRAILER_CLASSES",
    "ParsedLevel",
    "ParsedSave",
    "read_full_save",
    "read_full_save_bytes",
]


@dataclass
class ParsedLevel:
    """One level's headers and decoded objects, as two parallel lists.

    ``header[i]`` describes ``object[i]``. They are separate length-prefixed blocks in the
    file, so the pairing is a fact about the format rather than a convenience here, and
    ``objects`` is decoded in header order so the two stay aligned.
    """

    name: str
    headers: list[ActorHeader | ComponentHeader]
    objects: list[ParsedObject]

    @property
    def actorAndComponentObjectHeaders(self) -> list[ActorHeader | ComponentHeader]:
        """The spelling ``extract_save.iter_objects`` reads."""
        return self.headers


@dataclass
class ParsedSave:
    """A whole save: its header, its levels, and the bytes they point into.

    ``body`` is retained on purpose. ``ParsedObject.extra_offset``/``extra_length`` are
    absolute indices into it, so dropping it would leave every one of the 3,209 actors
    carrying class-specific data pointing at nothing. It is also the only copy: the slices
    are views by intent, not by accident.
    """

    info: SaveInfo
    body: bytes
    levels: list[ParsedLevel]
    #: Every actor the save records as gone, as ``(level cell, actor path)``. The world's
    #: collectibles -- slugs, mushrooms, Mercer spheres, somersloops, looted drop pods -- are
    #: placed by the map and not saved, so this negative record is the only thing that says
    #: which of them the player has taken. Merged from the three lists the save keeps; see
    #: ``objects.SaveBody.destroyed_actors``.
    destroyed_actors: list[tuple[str, str]] = field(default_factory=list)
    #: Everything skipped rather than understood, as ``(body offset, what)``, merged from the
    #: level walk and every object's property list. Measured across the 31 readable saves on
    #: the author's disk: empty on all **6** at saveVersion 60, and exactly **3** on each of
    #: the 25 at saveVersion 52 -- the struct elements inside a UE4 map or set, which are
    #: genuinely ambiguous. Diagnostics, not projection data: nothing above reads the fields
    #: they name, and the sidecar prints them to stderr rather than into the projection,
    #: where they would make the two parsers differ for a reason that is not a disagreement.
    warnings: list[tuple[int, str]] = field(default_factory=list)

    @property
    def object_count(self) -> int:
        return sum(len(lv.objects) for lv in self.levels)


#: What an actor leaves after its property list when its class writes nothing of its own:
#: 500,350 actors leave 4 bytes and 87,016 leave 8, over all 31 readable saves, and nothing
#: leaves anything else. Which of the two an actor gets is not established.
PLAIN_TRAILER = (4, 8)


#: Class paths that carry their own bytes after the property list on a save below
#: ``FIRST_MODERN_BODY``, and that nothing here decodes.
#:
#: Pre-1.0 there is no ``FGConveyorChainActor``: **every belt and lift carries the items on it
#: itself**, which is 50,532 actors and 39.5 MB across the 35 files. The list is here so that
#: ``_attach_trailer``'s check survives on those saves -- without it every one of those actors
#: would produce a warning, 50,532 of them would drown out a real one, and gating the check off
#: entirely below saveVersion 52 would throw away the only thing that notices a property list
#: which stopped early.
#:
#: Measured, not guessed: these eleven are **exactly** the classes leaving anything other than
#: 4 or 8 bytes across all 35 old bodies, 49,929 actors in total. Being on this list means
#: "known to write class-specific bytes, not decoded" -- ``actorSpecificInfo`` stays ``None``,
#: which is what says nobody has read them.
#:
#: ``ConveyorBeltMk4``/``Mk5`` and the matching lifts are deliberately absent: the player had
#: not unlocked them in 2021-2023, so no bytes on this disk say what they leave, and a save that
#: has one should produce a warning rather than a silent pass. The same goes for the modern
#: ``FGConveyorChainActor``, which does not exist pre-1.0 at all.
UNDECODED_TRAILER_CLASSES = frozenset(
    {
        "/Game/FactoryGame/Buildable/Factory/PowerLine/Build_PowerLine.Build_PowerLine_C",
        "/Game/FactoryGame/Buildable/Factory/ConveyorBeltMk1/Build_ConveyorBeltMk1.Build_ConveyorBeltMk1_C",
        "/Game/FactoryGame/Buildable/Factory/ConveyorBeltMk2/Build_ConveyorBeltMk2.Build_ConveyorBeltMk2_C",
        "/Game/FactoryGame/Buildable/Factory/ConveyorBeltMk3/Build_ConveyorBeltMk3.Build_ConveyorBeltMk3_C",
        "/Game/FactoryGame/Buildable/Factory/ConveyorLiftMk1/Build_ConveyorLiftMk1.Build_ConveyorLiftMk1_C",
        "/Game/FactoryGame/Buildable/Factory/ConveyorLiftMk2/Build_ConveyorLiftMk2.Build_ConveyorLiftMk2_C",
        "/Game/FactoryGame/Buildable/Factory/ConveyorLiftMk3/Build_ConveyorLiftMk3.Build_ConveyorLiftMk3_C",
        "/Game/FactoryGame/Character/Player/BP_PlayerState.BP_PlayerState_C",
        "/Game/FactoryGame/-Shared/Blueprint/BP_CircuitSubsystem.BP_CircuitSubsystem_C",
        "/Game/FactoryGame/-Shared/Blueprint/BP_GameState.BP_GameState_C",
        "/Game/FactoryGame/-Shared/Blueprint/BP_GameMode.BP_GameMode_C",
    }
)


def _attach_trailer(
    body: bytes,
    header: ActorHeader,
    obj: ParsedObject,
    warnings: list[tuple[int, str]],
    save_version: int = FIRST_MODERN_BODY,
) -> None:
    """Arrange for this object's trailing class-specific bytes to be decodable, and notice
    when there are bytes nothing can account for.

    Nothing is decoded here. Only the class is known at this point, and only here, so what
    gets attached is the *ability* to decode -- ``ParsedObject.actorSpecificInfo`` calls it on
    first access. Eager decoding would add 22% to a save's parse time for the conveyor chains
    alone, which no projection field reads.

    **The check is the point of knowing all eight classes.** A component's trailer has always
    been length-checked; an actor's could not be, because an actor of one of these classes
    legitimately leaves megabytes. Now that every such class has a reader, an actor that is
    neither one of them nor leaving a plain 4 or 8 bytes is worth saying out loud -- that is
    what a property list which stopped early looks like, and it used to be silent. It is a
    warning rather than a refusal because a modded or future class carrying its own data is
    the other thing it looks like, and that should not cost the save.
    """
    class_path = getattr(header, "typePath", None)
    if save_version < FIRST_MODERN_BODY:
        # No trailer reader has been verified against pre-1.0 bytes, and one of them would
        # otherwise be attached wrongly: `Build_PowerLine_C` has the same class path in 2021 as
        # in 2026, so the modern reader would be handed 2021 bytes and produce numbers nobody
        # has checked. Leaving `decode_trailer` unset keeps `actorSpecificInfo` None, which is
        # this parser's way of saying "not decoded" rather than "decoded, and empty".
        unexplained = (
            class_path is not None
            and obj.extra_length not in PLAIN_TRAILER
            and class_path not in UNDECODED_TRAILER_CLASSES
        )
        if unexplained:
            what = (
                f"{class_path.rsplit('.', 1)[-1]} left {obj.extra_length} trailing bytes on a "
                f"saveVersion {save_version} save, and no class is known to"
            )
            warnings.append((obj.extra_offset, what))
        return
    if class_path == LIGHTWEIGHT_SUBSYSTEM:
        obj.decode_trailer = partial(read_lightweight, body, obj.extra_offset, obj.extra_length)
    elif class_path in TRAILER_READERS:
        obj.decode_trailer = partial(
            read_trailer, class_path, body, obj.extra_offset, obj.extra_length
        )
    elif class_path is not None and obj.extra_length not in PLAIN_TRAILER:
        plain = " or ".join(map(str, PLAIN_TRAILER))
        what = (
            f"{class_path.rsplit('.', 1)[-1]} left {obj.extra_length} trailing bytes; "
            f"no reader knows this class and a plain actor leaves {plain}"
        )
        warnings.append((obj.extra_offset, what))


def read_full_save_bytes(data: bytes) -> ParsedSave:
    """Parse a complete .sav already in memory.

    Split out from ``read_full_save`` so the tests can assemble a file from the committed
    fixtures -- a header prefix plus a body re-compressed into chunks -- and exercise the
    whole composition with no game install.

    Every raise below is a ``ParseError``, which is the one type the sidecar catches at the
    save boundary. Anything else escaping here is a bug in this parser rather than a problem
    with the file, and should not be dressed up as one.
    """
    info = read_info_bytes(data)
    # Everything below this line is version-gated on ONE number, read once, here. The header is
    # the only part of a save that can be parsed without knowing which format it is -- its own
    # `save_header_type` says -- so this is the only place the choice can be made, and passing
    # it down beats each layer sniffing for itself and two of them disagreeing.
    old = info.save_version < FIRST_MODERN_BODY
    body = decompress_body(data, info.body_offset, old=old)
    # The changelist check is armed here: read_body compares the body's own build against
    # the header's and WARNS on a mismatch -- see objects._read_archive_header for why a
    # refusal would be wrong when the identity rests on a single build.
    parsed = read_body(body, info.save_version, info.build_version)
    warnings = list(parsed.warnings)
    levels = []
    for level in parsed.levels:
        objects = []
        for header, slot in zip(level.headers, level.objects, strict=True):
            actor = isinstance(header, ActorHeader)
            obj = read_object(body, slot, actor=actor, save_version=info.save_version)
            if obj.warnings:
                warnings.extend(obj.warnings)
            # Only actors carry class-specific trailing bytes, and the 562,556 components in
            # these 31 saves outnumber the actors -- so the cheapest thing to do with them is
            # nothing. Calling _attach_trailer for every object instead cost 5% of the parse.
            if actor:
                _attach_trailer(body, header, obj, warnings, info.save_version)
            objects.append(obj)
        levels.append(ParsedLevel(name=level.name, headers=level.headers, objects=objects))

    return ParsedSave(
        info=info,
        body=body,
        levels=levels,
        warnings=warnings,
        destroyed_actors=parsed.destroyed_actors,
    )


def read_full_save(path: str | os.PathLike[str]) -> ParsedSave:
    """Read and fully parse the save at ``path``.

    The whole file is read at once rather than streamed. A save is 1-3 MB on disk against
    44 MB inflated, so the read is 1 ms of the two seconds this takes, and reading it in one
    go is also the closest thing available to an atomic snapshot of a file the running game
    rewrites every few minutes.

    An unreadable *path* is deliberately left as ``OSError`` rather than turned into a
    ``ParseError``. A file that is missing or locked is not a save that cannot be parsed, and
    the sidecar reports the two differently -- ``FileNotFoundError`` names the real problem
    where "parse_error" would send the next person looking at the format.
    """
    with open(path, "rb") as fh:
        data = fh.read()
    return read_full_save_bytes(data)
