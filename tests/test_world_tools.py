"""What the world-state tools answer, judged against the frozen fixture.

They read the newest save on the machine, so each test here points them at the committed
projection instead: the numbers below are that world's, and a machine with a different
save in it must not change them.
"""

from __future__ import annotations

import pytest

from satisfactory_mcp.interfaces.mcp.tools import world as world_tools

pytestmark = pytest.mark.integration


@pytest.fixture
def tools(state, monkeypatch):
    monkeypatch.setattr(world_tools, "_state", lambda save=None, world=None: state)
    return world_tools


# ------------------------------------------------------- picking up where you left off


def test_the_summary_says_what_the_player_was_last_working_on(tools):
    """mLastActiveSchematic and mLastUsedHardDriveID had no consumer at all, and they are
    the two facts an assistant resuming a session cannot derive from any count: the goal
    the HUB is tracking, and the drive whose choice was settled last."""
    out = tools.world_summary()
    assert "working_on=Logistics Mk.5" in out
    assert "last_hard_drive_spent=36" in out


def test_a_save_naming_neither_prints_neither(state, game, monkeypatch):
    """An empty continuity line is worse than no line: it reads as "nothing in progress"
    where the truth is that the save did not say."""
    from copy import deepcopy

    from satisfactory_mcp.domain.world.state import WorldState

    projection = deepcopy(state.projection)
    projection["progression"].pop("last_active_schematic", None)
    projection["research"].pop("last_used_hard_drive_id", None)
    bare = WorldState(projection=projection, game=game)
    monkeypatch.setattr(world_tools, "_state", lambda save=None, world=None: bare)
    out = world_tools.world_summary()
    assert "working_on" not in out
    assert "last_hard_drive_spent" not in out
