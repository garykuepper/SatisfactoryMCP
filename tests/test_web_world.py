"""``/api/worlds`` and ``/api/summary``: the save picker, and the header it opens onto.

``importorskip`` at module scope, not a marker: ``fastapi`` lives in the optional
``web`` extra, so an install without it must skip this file rather than fail collection.

Both loaders are injected by the ``client`` fixture in ``conftest.py``, so nothing here
spawns the sidecar or reads a ``.sav``. ``/api/worlds`` is the one route that scans the
save directory on its own, and its one test stubs the scanner.

The surface-wide "could not read save" refusal is pinned here too, on ``/api/summary``:
every handler makes it the same way through ``serial._state``, and this is the endpoint
the page opens with.
"""

from __future__ import annotations

import pytest
from conftest import _explode

fastapi = pytest.importorskip("fastapi")

from fastapi.testclient import TestClient

from satisfactory_mcp.core.saveio.projection import World
from satisfactory_mcp.interfaces.web.app import create_app
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


def test_a_save_that_cannot_be_read_is_a_404_with_a_reason(game):
    """The refusal ``_state`` makes, on the endpoint the page opens with.

    Every handler on this surface spends ``?save=``/``?world=`` through ``serial._state``
    and turns a loader failure into the same 404, so the shape is pinned once here rather
    than in a file per router -- and it is pinned on ``/api/summary`` because that is the
    first request the page makes, and the one whose failure the header has to explain.
    """
    app = create_app(state_loader=_explode, game_loader=lambda: game)
    with TestClient(app) as c:
        r = c.get("/api/summary")
    assert r.status_code == 404
    assert "sidecar produced no output" in r.json()["error"]
