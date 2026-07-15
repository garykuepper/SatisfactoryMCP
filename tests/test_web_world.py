"""``/api/worlds`` and ``/api/summary``: the save picker, and the header it opens onto.

``importorskip`` at module scope, not a marker: ``fastapi`` lives in the optional
``web`` extra, so an install without it must skip this file rather than fail collection.

Both loaders are injected by the ``client`` fixture in ``conftest.py``, so nothing here
spawns the sidecar or reads a ``.sav``. ``/api/worlds`` is the one route that scans the
save directory on its own, and its one test stubs the scanner.
"""

from __future__ import annotations

import pytest

fastapi = pytest.importorskip("fastapi")

from satisfactory_mcp.core.saveio.projection import World
from satisfactory_mcp.interfaces.web.routers import world as web_world


def test_worlds_lists_the_save_picker_rows(client, monkeypatch):
    """The picker's only source. ``list_worlds`` is stubbed so no save tree is scanned."""
    world = World(
        world_id="X2faPVKjX06VaRzClNv5KQ",
        session_name="Han Solo",
        saves=[{"filename": "a.sav", "mtime_ns": 1_700_000_000_000_000_000, "play_duration_s": 42}],
    )
    monkeypatch.setattr(web_world.proj, "list_worlds", lambda: ([world], [{"file": "old.sav"}]))
    body = client.get("/api/worlds").json()
    assert len(body["worlds"]) == 1
    row = body["worlds"][0]
    assert row["world_id"] == "X2faPVKjX06VaRzClNv5KQ"
    assert row["session_name"] == "Han Solo"
    assert row["newest_filename"] == "a.sav"
    assert row["mtime"] == pytest.approx(1.7e9)
    assert row["play_duration_s"] == 42
    assert body["unsupported"] == [{"file": "old.sav"}]


def test_summary_reports_the_header_power_and_progression(client, state):
    body = client.get("/api/summary").json()
    assert body["header"]["session_name"] == state.header["session_name"]
    assert body["age_note"] == state.age_note
    assert body["power"]["generation_mw"] == pytest.approx(state.power_report()["generation_mw"])
    assert body["progression"]["game_phase"] == state.progression()["game_phase"]
    # The you-are-here marker's only source. Metres, like everything else here.
    pos = state.player_position()
    if pos is None:
        assert body["player"] == {"x_m": None, "y_m": None, "z_m": None}
    else:
        assert body["player"]["x_m"] == pytest.approx(round(pos[0] / 100.0, 1))
        assert body["player"]["y_m"] == pytest.approx(round(pos[1] / 100.0, 1))
