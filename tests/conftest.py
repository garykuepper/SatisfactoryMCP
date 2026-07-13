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


#: The node field every planner test in this suite plans over, and the reason it is a BOX.
#:
#: It used to be ``["region:Spire Coast"]``, and the reference plans -- 99,729.62 MW over 787
#: buildings, table B's 9,200 Fuel on 16 pipes -- were verified by hand against exactly the
#: nodes that selector returned. Then the region layer was re-derived from the game's own
#: ``FGMapAreaTexture``, and the game's Spire Coast turns out to be a 1.6 km2 coastal strip
#: rather than the 8.5 km2 ring the retired wiki trace drew across the whole north: 51 nodes
#: became 18, and 13 crude became 6. Nothing about the planner changed and every number in
#: thirty-three tests moved, which is the definition of a fixture depending on the wrong
#: thing. A region NAME is advisory by design -- ``domain.spatial.regions`` says so in its
#: first paragraph -- and a regression suite must not be built on one.
#:
#: So the field is stated geographically, once, and can never move again. The box is the
#: bounding box of the nodes the retired selector returned, in metres. It holds all 51 of
#: them and 17 more: 8 Limestone, 5 Iron, 2 Copper, 2 Raw Quartz -- measured, and every one
#: of them irrelevant to these plans, which maximise MW out of crude and coal and export
#: Plastic and Rubber. No extra crude, no extra coal, no extra nitrogen, no extra water. That
#: is why the hand-verified numbers reproduce unchanged over it rather than being re-baselined
#: against whatever the software now says, which would have thrown away the human measurement
#: the whole regression rests on.
REFERENCE_FIELD = ("bbox:-649.63,-3140.09,2465.02,-1080.3",)


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


#: What ``save_projection.json`` was cut from, so that re-cutting it is a recipe rather than
#: a reconstruction. Both halves are read straight out of the fixture's own ``header`` block,
#: which is where the next reader should check them rather than trusting this comment.
FIXTURE_SAVE = "Han Solo_280726-230847.sav"
FIXTURE_WORLD = "X2faPVKjX06VaRzClNv5KQ"


@pytest.fixture(scope="session")
def projection() -> dict:
    """The committed sidecar projection: no game install and no .sav needed.

    1.4 MB of one real save, committed because the 2.9 MB ``.sav`` is not, and it is what
    makes most of this suite reproducible on a machine with neither the game nor a save.

    HOW TO REGENERATE IT. One command, run from the repository root, writing over the file::

        uv run python -m satisfactory_mcp.core.saveio.extract \\
            "<saves>/Han Solo_280726-230847.sav" > tests/fixtures/save_projection.json

    ``-m``, not a path to ``extract.py``: that is how ``projection._run_sidecar`` invokes it
    in production, so the fixture is cut by exactly the code path the server uses. There is no
    flag and no post-processing -- the sidecar's stdout IS the fixture.

    FROM THE SAME WORLD, and this is the part that is not a formality. The save above is
    ``save_identifier`` ``X2faPVKjX06VaRzClNv5KQ`` ("Han Solo"), and a projection cut from any
    other world is a different factory: different machine counts, different node occupancy,
    different graph. A dozen modules in ``src`` justify a design decision by quoting a number
    measured on THIS fixture, and ``test_reference_counts.py`` exists solely to fail loudly
    when a re-cut moves one -- so a regeneration from a newer save of the same world is a
    normal thing to do and is expected to break that file, while a regeneration from a
    different world silently invalidates the reasoning rather than the numbers.

    So, after re-cutting: run ``uv run pytest -q``, expect ``test_reference_counts.py`` to
    fail, and update both the assertion there and the comment in ``src`` it names. Both.

    The world rule is asserted rather than only written down, because the failure it guards
    against does not look like a failure -- a fixture from another world produces a suite that
    fails in twenty places for twenty apparently unrelated reasons, and this says the one
    thing that explains all of them. The FILENAME is deliberately not asserted: a newer save
    of the same world is the ordinary, expected regeneration.
    """
    body = json.loads((FIXTURES / "save_projection.json").read_text(encoding="utf-8"))
    found = (body.get("header") or {}).get("save_identifier")
    assert found == FIXTURE_WORLD, (
        f"save_projection.json was cut from world {found!r}, not {FIXTURE_WORLD!r} -- this "
        f"suite measures one factory, and the reference save is {FIXTURE_SAVE}. Re-cut it "
        "from that world, or change both constants in conftest.py and re-measure every count "
        "test_reference_counts.py pins."
    )
    return body


@pytest.fixture(scope="session")
def state(game, projection) -> WorldState:
    return WorldState(projection=projection, game=game)
