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

**What this does not do.** The trailing class-specific bytes after an object's property list
are handed on as ``(extra_offset, extra_length)`` and left undecoded -- 3,209 actors on the
reference save carry them, mostly conveyor chains, power lines and the lightweight-buildable
subsystem's 3.1 MB. There is deliberately no ``actorSpecificInfo`` attribute here: the
projection reads that name through ``getattr(obj, "actorSpecificInfo", None)``, so its
absence is what makes ``lightweight_counts`` and ``structures`` come out empty rather than
wrong. Those two fields are the only ones in the projection that still need the vendored
parser, and inventing a half-decoded attribute would turn a visible gap into an invisible
one.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from .chunks import decompress_body
from .header import SaveInfo, read_info_bytes
from .objects import ActorHeader, ComponentHeader, read_body
from .properties import ParsedObject, read_object

__all__ = ["ParsedLevel", "ParsedSave", "read_full_save", "read_full_save_bytes"]


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
    body = decompress_body(data, info.body_offset)
    parsed = read_body(body)
    warnings = list(parsed.warnings)
    levels = []
    for level in parsed.levels:
        objects = []
        for header, slot in zip(level.headers, level.objects, strict=True):
            obj = read_object(body, slot, actor=isinstance(header, ActorHeader))
            if obj.warnings:
                warnings.extend(obj.warnings)
            objects.append(obj)
        levels.append(ParsedLevel(name=level.name, headers=level.headers, objects=objects))

    return ParsedSave(info=info, body=body, levels=levels, warnings=warnings)


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
