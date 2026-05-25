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

from .header import PACKAGE_FILE_TAG, SaveInfo, read_info, read_info_bytes
from .reader import Reader

__all__ = ["PACKAGE_FILE_TAG", "Reader", "SaveInfo", "read_info", "read_info_bytes"]
