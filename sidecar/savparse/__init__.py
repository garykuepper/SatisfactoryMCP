"""A parser for Satisfactory .sav files, covering what this project reads.

Written to replace a vendored GPL-3.0 parser whose licence would otherwise reach the whole
project. It implements the FILE FORMAT -- a fact about what the game writes -- rather than
reproducing that library's code, and it is verified black-box against it: same file in,
same values out, across every save on disk.

Scope is deliberately narrow. The old library parses everything; this parses the parts the
projection actually uses, which is three entry points' worth. Anything it does not
understand is skipped by length rather than guessed at, so an unknown property costs that
property and not the save.
"""

from .chunks import CHUNK_TAG, decompress_body
from .errors import ParseError
from .header import PACKAGE_FILE_TAG, SaveInfo, read_info, read_info_bytes
from .lightweight import LIGHTWEIGHT_SUBSYSTEM, read_lightweight
from .objects import (
    ActorHeader,
    ComponentHeader,
    Level,
    ObjectSlice,
    SaveBody,
    read_body,
)
from .properties import ObjectReference, ParsedObject, TypeName, read_object
from .reader import Reader
from .save import ParsedLevel, ParsedSave, read_full_save, read_full_save_bytes

__all__ = [
    "CHUNK_TAG",
    "LIGHTWEIGHT_SUBSYSTEM",
    "PACKAGE_FILE_TAG",
    "ActorHeader",
    "ComponentHeader",
    "Level",
    "ObjectReference",
    "ObjectSlice",
    "ParseError",
    "ParsedLevel",
    "ParsedObject",
    "ParsedSave",
    "Reader",
    "SaveBody",
    "SaveInfo",
    "TypeName",
    "decompress_body",
    "read_body",
    "read_full_save",
    "read_full_save_bytes",
    "read_info",
    "read_info_bytes",
    "read_lightweight",
    "read_object",
]
