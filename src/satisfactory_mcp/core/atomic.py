"""Replace a file's contents, or leave the file exactly as it was.

``Path.write_text`` truncates first and writes second, so there is a window -- short, and
not zero -- in which the file on disk is empty or half a document. Anything that ends the
process inside it (a crash, a power cut, a full disk on the second write) leaves that state
behind, and the next read finds a truncated JSON file where a complete one used to be.

**Which files that matters for is a short list.** Two of them are files nothing can
reconstruct -- the factory names the player typed (``domain/factories/labels.py``) and the
plans they saved (``domain/planning/store.py``). Both belong to the reader rather than to
the program, and neither is derivable from a save, from the docs dump or from anything else
on the machine.

**The third is the projection cache, and this module used to say in so many words that it
did not belong here.** The argument was that the cache is regenerable, that a corrupt pickle
is already caught and deleted by ``saveio.projection``, and that losing one costs four
seconds of re-parsing. Every clause of that is still true and it answers the wrong question:
it reasons about ONE writer dying mid-write, where the recovery is to re-parse. It says
nothing about a SECOND process reading the file while the first is still writing it, which
is not a crash and has no recovery -- the reader gets a prefix of a pickle, and on Windows
its clean-up ``unlink`` of the "corrupt" file then fails with ``PermissionError`` because
the writer still holds the handle.

That second condition is now the normal one rather than the exotic one. The cache directory
is shared by the web server, by every CLI invocation and -- since the test suite began
running under ``pytest-xdist`` -- by up to a worker per core, all resolving the same newest
save and all missing the same key at the same moment. So the projection cache is written
through ``write_bytes`` below, and what the old paragraph called a four-second loss is now
simply not a thing that happens.

The fix is the same one ``core/gameassets/pyramid.py`` makes about a whole directory tree
and for the same reason: build the new thing somewhere else and put it into place with one
operation the filesystem cannot interrupt. ``os.replace`` is that operation -- atomic on
POSIX by definition, and on Windows a ``MoveFileEx`` with ``MOVEFILE_REPLACE_EXISTING``,
which is atomic with respect to readers of the destination. A reader either gets the old
file whole or the new file whole, and never nothing.

**On Windows the replace can lose a race it cannot corrupt**, and that is worth naming
because it is the one behaviour that differs between the platforms. ``MoveFileEx`` fails
with ``PermissionError`` if another process has the destination open, and CPython opens
files for reading without ``FILE_SHARE_DELETE``. So a writer can fail where a POSIX writer
would have succeeded. It fails having changed nothing -- the reader keeps reading the whole
old file, the temp is removed below -- so every caller here treats it as "the cache was not
updated this time", which is a cache's ordinary right.

**The temporary file is a sibling, not a temp-directory file**, because ``os.replace``
across filesystems is not a rename at all -- it degrades to a copy, which reintroduces
precisely the window this exists to close. It carries the process id AND a per-process
counter: the pid separates two servers writing one world's labels, and the counter separates
two threads of one process writing one path, which a pid alone does not and which the pooled
whole-folder passes in the test suite made reachable. If the process dies before the
replace, that temp is left behind: it is named nothing the loaders look for, so it is inert
rather than dangerous, and it is deleted here on any failure this code can see.
"""

from __future__ import annotations

import itertools
import os
from pathlib import Path

__all__ = ["write_bytes", "write_text"]

#: Distinguishes two temps written by two threads of one process for one target. ``count``
#: is documented as atomic with respect to the GIL, which is exactly the guarantee needed.
_SERIAL = itertools.count()


def _tmp_beside(target: Path) -> Path:
    return target.with_name(f"{target.name}.{os.getpid()}.{next(_SERIAL)}.tmp")


def _replace_through(target: Path, tmp: Path, mode: str, payload, encoding: str | None) -> Path:
    """Write ``payload`` to ``tmp`` and move it onto ``target``, or leave ``target`` alone.

    ``fsync`` before the replace, which is the half people leave out: the rename can reach
    the disk before the data it renames, and then a power cut leaves a file that is present,
    named right and empty.
    """
    try:
        with open(tmp, mode, encoding=encoding) as handle:
            handle.write(payload)
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


def write_text(path: str | Path, text: str, *, encoding: str = "utf-8") -> Path:
    """Write ``text`` to ``path`` as one indivisible step. Returns the path.

    A drop-in for ``Path.write_text``, including its newline handling -- the file is opened
    with the same default translation, so the bytes on disk are the ones the direct call
    produced and a file written by the old code and this one are identical.
    """
    target = Path(path)
    return _replace_through(target, _tmp_beside(target), "w", text, encoding)


def write_bytes(path: str | Path, data: bytes) -> Path:
    """Write ``data`` to ``path`` as one indivisible step. Returns the path.

    A drop-in for ``Path.write_bytes``. Separate from ``write_text`` rather than folded into
    it because the caller that needs this one is writing a pickle, and a pickle that went
    through a text mode's newline translation is not a pickle.
    """
    target = Path(path)
    return _replace_through(target, _tmp_beside(target), "wb", data, None)
