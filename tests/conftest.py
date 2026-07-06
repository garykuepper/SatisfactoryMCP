from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from satisfactory_mcp import config
from satisfactory_mcp.core.gamedata.loader import load_docs
from satisfactory_mcp.core.gamedata.normalize import normalize
from satisfactory_mcp.core.saveio.projection import SaveError
from satisfactory_mcp.domain.world.state import WorldState

FIXTURES = Path(__file__).parent / "fixtures"

#: The repository root, put on ``sys.path`` so that ``tools`` imports as the package it is.
#:
#: ``satisfactory_mcp`` and ``pioneersav`` are installed into the environment; ``tools/`` is
#: deliberately not -- the generators read the reader's own game install and are never
#: shipped in the wheel -- and pytest puts only ``tests/`` on the path. The tests that hold a
#: generator against the server it feeds therefore used to load those files BY PATH, which
#: executes the module afresh on every call and lands a second copy of it in the process
#: beside the ``tools.gen_map_image`` that ``tools/gen_map_renders.py`` imports by name.
#: One line here buys ``from tools import gen_map_image`` instead, in every test module.
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _docs_available() -> bool:
    return config.docs_path().is_file()


requires_docs = pytest.mark.skipif(
    not _docs_available(), reason="needs the game install (set SATISFACTORY_DOCS)"
)


@pytest.fixture(scope="session")
def game():
    if not _docs_available():
        pytest.skip("needs the game install")
    return normalize(load_docs(config.docs_path()))


@pytest.fixture
def live(game) -> WorldState:
    """The world as it stands on THIS machine, or a skip -- eight modules wrote this out.

    Not the same thing as the ``state`` fixture below, and the difference is the point:
    ``state`` is the committed projection, frozen, which is what makes most of the suite
    reproducible; ``live`` is whatever save is newest right now, which is what a test
    measuring the tool's real answer has to read.

    So it needs a save, and a clone has none. Eight modules built this fixture by hand and
    none of them guarded the call, so on a machine with the game installed and no ``.sav``
    the suite reported **61 errors** -- an error, not a skip, which reads as the code being
    broken rather than as the machine being unequipped. ``SaveError`` is exactly and only the
    "could not produce a projection" signal, so catching it here converts the one condition
    a clone is actually in, and leaves a genuine parser fault raising.

    Function-scoped, matching the fixtures it replaces. It costs nothing to re-enter: the
    projection is cached in-process by ``load_projection`` and this is a dictionary lookup
    after the first test in a session pays the parse.
    """
    from satisfactory_mcp.interfaces.mcp.app import _state

    try:
        return _state(None, None)
    except SaveError as exc:
        pytest.skip(f"needs a readable save: {exc}")


@pytest.fixture(scope="session")
def projection() -> dict:
    """The committed sidecar projection: no game install and no .sav needed."""
    return json.loads((FIXTURES / "save_projection.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def state(game, projection) -> WorldState:
    return WorldState(projection=projection, game=game)
