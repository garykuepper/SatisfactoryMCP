"""Which build an artifact was cut from, and writing it so it can never say the wrong one.

Every table and raster under ``data/`` is derived from one particular installed build of
the game, and the project's standing rule is that a pinned artifact **announces drift
rather than answering silently wrong**. That rule needs three things, and they are the
three things here: read the build the machine has installed, read the build an artifact
already on disk says it came from, and put a new artifact in place all at once so no
reader ever meets half of one.

The last of those belongs beside the other two rather than in a file about directories.
A ``heightmap/`` holding three rasters from this build and a ``meta.json`` from the last
one is not a partial artifact -- it is an artifact that *lies about its provenance*, and
it answers questions instead of failing. The rename is what makes that impossible.

Two ways to read the installed build, because the game states it twice
---------------------------------------------------------------------
:func:`installed_build` reads ``Engine/Binaries/Win64/*-Win64-Shipping.version``, which is
JSON the build system wrote: ``Changelist`` and ``BranchName`` are fields rather than
something scanned out of a binary, so the pin string is a statement the engine makes about
itself. That is the one to use.

:func:`installed_build_from_exe` scans the shipping executable's version resource for
``++FactoryGame+rel-`` as UTF-16 and hands back what it finds verbatim -- on build 495413
that is ``++FactoryGame+rel-main-1.2.0-CL-495413``, the engine's own spelling of branch and
changelist in one string. It is a different fact from the pin, not a fallback for it, which
is why both are here: ``world_collectibles.json`` records that literal under
``_meta.source.placements.game_build``, and a literal read out of the binary is the thing a
reader can check against their own install without trusting this file's formatting.
"""

from __future__ import annotations

import json
import shutil
from collections.abc import Iterable, Mapping
from pathlib import Path

#: The engine's own version file, the one the build system writes beside the executable.
VERSION_GLOB = "Engine/Binaries/Win64/*-Win64-Shipping.version"

#: Where the executable's version resource states the branch, and how far past it to read
#: when the string is not terminated where it should be. Bytes, not characters: the scan is
#: over UTF-16, so 200 of them is 100 characters of branch name and nothing is ever that long.
BRANCH_MARK = "++FactoryGame+rel-"
BRANCH_MAX_BYTES = 200

#: What a directory is called while it is being written, and while it is being replaced.
#: Neither name is ever served: a reader looks for the real one and finds it whole or not
#: at all, and the next run deletes whatever an interrupted one left behind.
STAGING_SUFFIX = ".incoming"
RETIRED_SUFFIX = ".retired"


class InstallNotFound(Exception):
    """The directory given is not an installed copy of the game.

    Raised rather than exited on, and it states the fact without stating the fix: a
    generator knows it has a ``--game`` argument and this file does not. Same division as
    ``tools/_common.require_gen``, which is the other half of "what a generator needs
    before it can read anything" and is the half that owns the command line.
    """


def installed_build(game: Path) -> tuple[str, dict]:
    """The installed build as ``(pin string, the raw version JSON)``.

    The pin string is the shape every artifact records and every staleness guard compares,
    so a heightfield, a node table and a map sheet cut from one build are comparable on
    sight. It is built here rather than per generator precisely so that they cannot drift
    into two spellings of the same build.
    """
    found = sorted(game.glob(VERSION_GLOB))
    if not found:
        raise InstallNotFound(f"no {VERSION_GLOB} under {game}")
    raw = json.loads(found[0].read_text(encoding="utf-8"))
    pin = (
        f"buildVersion {raw.get('Changelist')} "
        f"(engine branch {raw.get('BranchName')}), the installed build"
    )
    return pin, raw


def installed_build_from_exe(game: Path) -> str | None:
    """The engine's own build string, out of the shipping executable's version resource.

    Scanned as UTF-16 rather than parsed as a PE resource: the launcher is 270 KB, the
    string is a fixed literal, and a version number is not worth a resource walker.
    ``None`` when no shipping executable carries it, because a caller that puts this in a
    sidecar would rather record nothing than record a guess.
    """
    needle = BRANCH_MARK.encode("utf-16-le")
    for candidate in sorted(game.glob("*/Binaries/Win64/*Shipping.exe")):
        blob = candidate.read_bytes()
        at = blob.find(needle)
        if at < 0:
            continue
        end = blob.find(b"\0\0", at)
        limit = at + BRANCH_MAX_BYTES
        stop = end + 1 if at < end < limit else limit
        return blob[at:stop].decode("utf-16-le", "replace")
    return None


def read_path(sidecar: object, path: Iterable[str]) -> object:
    """Walk nested dicts to whatever is at ``path``, or ``None`` the moment the walk fails.

    The primitive under every staleness guard in ``tools/``, and the reason it is not typed
    is that the guards do not all want a string: one wants the build tag, one wants a
    boolean saying whether a pyramid was upscaled, one wants an integer recipe number, and
    all three want the identical walk. They each had their own copy of this loop, and the
    copies had already drifted -- the integer one breaks out of the loop instead of
    returning, which is the same answer by a different route and one more shape to read.

    Deliberately incurious about what it finds: a sidecar written by an older generator, a
    truncated one, a JSON document that is not even an object -- all of them are "this path
    is not there", which is the answer that makes a guard refuse rather than crash. What it
    is NOT is a type check; that belongs to the caller, which is the only one that knows
    what a plausible value looks like.
    """
    node: object = sidecar
    for key in path:
        if not isinstance(node, dict):
            return None
        node = node.get(key)
    return node


def read_str_path(sidecar: object, path: Iterable[str]) -> str | None:
    """:func:`read_path`, refusing anything that is not a string.

    The build tag's shape, which is what most guards want: a pin is a sentence and a pin
    that arrived as a number or a nested object is a sidecar this reader does not
    understand, which is the same refusal as one that names no build at all.
    """
    node = read_path(sidecar, path)
    return node if isinstance(node, str) else None


def install_directory(out_dir: Path, payload: Mapping[str, bytes]) -> dict[str, int]:
    """Write a whole artifact directory into staging and rename it into place.

    The rename is the point: ``out_dir`` appears complete or not at all. Files that have
    to agree with each other -- rasters and the sidecar that georeferences them, tiles and
    the sidecar that says which build they were cut from -- are exactly the case where a
    half-written directory is worse than no directory, because a reader meeting a new
    raster and an old sidecar gets answers rather than an error.

    The previous directory is renamed aside rather than deleted first, so the window in
    which nothing is in place is a rename wide. Returns ``{name: bytes}`` for the sidecar
    to record.
    """
    staging = out_dir.with_name(out_dir.name + STAGING_SUFFIX)
    retired = out_dir.with_name(out_dir.name + RETIRED_SUFFIX)
    for stale in (staging, retired):
        if stale.exists():
            shutil.rmtree(stale)
    staging.mkdir(parents=True)
    written = {}
    for name, blob in payload.items():
        (staging / name).write_bytes(blob)
        written[name] = len(blob)
    if out_dir.exists():
        out_dir.rename(retired)
    staging.rename(out_dir)
    if retired.exists():
        shutil.rmtree(retired)
    return written
