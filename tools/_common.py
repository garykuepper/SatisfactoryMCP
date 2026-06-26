"""What every generator in this directory needs before it can read the installed game.

**The ``gen`` extra.** Three third-party packages stand between a generator and the game's
own container: ``ooz`` (from ``pyooz``) decompresses the container's blocks,
``texture2ddecoder`` unpacks BC1 texture blocks, and Pillow writes the PNGs. They used to
be installed into a throwaway venv reached through a ``--pyooz-path`` argument, which put
that venv's ``site-packages`` on ``sys.path`` at runtime. They are an optional extra of
this project instead, pinned exactly in ``pyproject.toml``, and the invocation is::

    uv run --extra gen python tools/gen_world_collectibles.py

Optional still means optional **at import time**: none of the three is imported at module
scope anywhere in this repository, so the server, the parser, the domain and the test
suite all run with none of them installed -- the same posture ``interfaces/web`` has
towards fastapi, and enforced the same way in ``tests/test_architecture.py``. What changed
is who installs them and how they are pinned, not whether anything depends on them.

The pins are exact because these three decide the BYTES a generator writes: a silent
upgrade would be a silent redraw of a map, or a silently different table, with nothing in
the output to say so. The versions each artifact was cut with are recorded in its sidecar,
which is what ``require_gen`` hands back.
"""

from __future__ import annotations

import argparse
import importlib
import sys
from pathlib import Path

#: Where Steam puts the game. Overridable; the container is the only thing read from it.
DEFAULT_GAME = Path("G:/SteamLibrary/steamapps/common/Satisfactory")

#: Import name -> the distribution that provides it. The distribution name is what the
#: sidecars record, because it is what you install; the import name is what fails.
GEN_MODULES = {
    "ooz": "pyooz",
    "texture2ddecoder": "texture2ddecoder",
    "PIL.Image": "pillow",
}


def gen_invocation() -> str:
    """The command line that fixes a missing ``gen`` extra, naming the running script."""
    script = Path(sys.argv[0]).name if sys.argv and sys.argv[0] else ""
    return f"uv run --extra gen python tools/{script or 'gen_<tool>.py'}"


def require_gen(*names: str) -> dict[str, str]:
    """Prove the ``gen`` extra's modules import, or say how to get them and exit 2.

    Returns ``{distribution: version}`` for the names asked for -- what the generators
    record in their sidecars, so that a picture or a table says which decoder drew it. The
    modules themselves are imported by the caller at the point of use: this proves they
    are there and turns ``No module named 'ooz'`` into the one line that fixes it.

    Exits rather than raising, because there is nothing for a generator to do about it and
    a traceback would suggest there is. 2, the code the old missing-import paths returned.
    """
    versions: dict[str, str] = {}
    missing: list[str] = []
    for name in names:
        distribution = GEN_MODULES[name]
        try:
            importlib.import_module(name)
        except ImportError:
            missing.append(distribution)
        else:
            versions[distribution] = _installed_version(distribution)
    if missing:
        print(
            f"{', '.join(missing)} not importable, so the game's own container cannot be "
            "opened. These are the `gen` extra: generation-time tools, imported at module "
            "scope by nothing in this repository, and installed by asking for them:\n"
            f"    {gen_invocation()}"
        )
        raise SystemExit(2)
    return versions


def base_parser(description: str) -> argparse.ArgumentParser:
    """A parser carrying the argument every generator that opens the container takes.

    ``--game`` and nothing else: the destination, the force flag and the rest differ per
    tool in default and in meaning, and a shared parser that flattened those differences
    would be a worse lie than four honest ``add_argument`` calls.
    """
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument(
        "--game",
        type=Path,
        default=DEFAULT_GAME,
        help="Satisfactory install directory (the one holding FactoryGame/ and Engine/)",
    )
    return parser


def _installed_version(distribution: str) -> str:
    """The installed version, or ``"unknown"`` -- a sidecar field, never a control flow."""
    try:
        from importlib.metadata import version

        return version(distribution)
    except Exception:
        return "unknown"
