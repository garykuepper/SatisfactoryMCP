"""Replace a file's contents, or leave the file exactly as it was.

``Path.write_text`` truncates first and writes second, so there is a window -- short, and
not zero -- in which the file on disk is empty or half a document. Anything that ends the
process inside it (a crash, a power cut, a full disk on the second write) leaves that state
behind, and the next read finds a truncated JSON file where a complete one used to be.

**Which files that matters for is a short list, and it is not the obvious one.** The
projection cache is far bigger and is not here: it is regenerable, a corrupt pickle is
already caught and deleted by ``saveio.projection``, and the cost of losing it is four
seconds of re-parsing. What is here is the two files nothing can reconstruct -- the factory
names the player typed (``domain/factories/labels.py``) and the plans they saved
(``domain/planning/store.py``). Both belong to the reader rather than to the program, and
neither is derivable from a save, from the docs dump or from anything else on the machine.

The fix is the same one ``core/gameassets/pyramid.py`` makes about a whole directory tree
and for the same reason: build the new thing somewhere else and put it into place with one
operation the filesystem cannot interrupt. ``os.replace`` is that operation -- atomic on
POSIX by definition, and on Windows a ``MoveFileEx`` with ``MOVEFILE_REPLACE_EXISTING``,
which is atomic with respect to readers of the destination. A reader either gets the old
file whole or the new file whole, and never nothing.

**The temporary file is a sibling, not a temp-directory file**, because ``os.replace``
across filesystems is not a rename at all -- it degrades to a copy, which reintroduces
precisely the window this exists to close. It carries the process id so two servers writing
one world's labels cannot hand each other a half-written temp. If the process dies before
the replace, that temp is left behind: it is named nothing the loaders look for, so it is
inert rather than dangerous, and it is deleted here on any failure this code can see.
"""

from __future__ import annotations

import os
from pathlib import Path

__all__ = ["write_text"]


def write_text(path: str | Path, text: str, *, encoding: str = "utf-8") -> Path:
    """Write ``text`` to ``path`` as one indivisible step. Returns the path.

    A drop-in for ``Path.write_text``, including its newline handling -- the file is opened
    with the same default translation, so the bytes on disk are the ones the direct call
    produced and a file written by the old code and this one are identical.

    ``fsync`` before the replace, which is the half people leave out: the rename can reach
    the disk before the data it renames, and then a power cut leaves a file that is present,
    named right and empty. It costs a millisecond on files this size.
    """
    target = Path(path)
    tmp = target.with_name(f"{target.name}.{os.getpid()}.tmp")
    try:
        with open(tmp, "w", encoding=encoding) as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, target)
    except BaseException:
        # The temp is this function's litter whatever went wrong, including a
        # KeyboardInterrupt -- hence BaseException. The original is untouched either way,
        # which is the whole point, so nothing here has to repair anything.
        try:
            tmp.unlink()
        except OSError:
            pass
        raise
    return target
