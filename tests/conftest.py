from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from satisfactory_mcp import config
from satisfactory_mcp.core.gamedata.loader import load_docs
from satisfactory_mcp.core.gamedata.normalize import normalize
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


@pytest.fixture(scope="session")
def projection() -> dict:
    """The committed sidecar projection: no game install and no .sav needed."""
    return json.loads((FIXTURES / "save_projection.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def state(game, projection) -> WorldState:
    return WorldState(projection=projection, game=game)
