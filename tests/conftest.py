from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from satisfactory_mcp import config
from satisfactory_mcp.docs.loader import load_docs
from satisfactory_mcp.docs.normalize import normalize
from satisfactory_mcp.save.state import WorldState

FIXTURES = Path(__file__).parent / "fixtures"

# The sidecar is a subprocess, not a package, so it is not importable by default. Its
# own save parser is ordinary code and wants ordinary tests.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "sidecar"))


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
