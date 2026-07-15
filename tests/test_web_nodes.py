"""``/api/nodes``: the static node table, joined to what this save built on it.

``importorskip`` at module scope, not a marker: ``fastapi`` lives in the optional
``web`` extra, so an install without it must skip this file rather than fail collection.

Every test here injects both loaders -- through the ``client`` fixture in ``conftest.py``
or by building its own app -- so nothing in this file spawns the sidecar or reads a
``.sav``.
"""

from __future__ import annotations

import pytest
from conftest import _explode

fastapi = pytest.importorskip("fastapi")

from fastapi.testclient import TestClient

from satisfactory_mcp.interfaces.web.app import create_app


def test_nodes_carry_the_occupancy_join_and_a_reusable_selector(client, state):
    body = client.get("/api/nodes").json()
    rows = body["nodes"]
    assert rows, "the committed node table is not empty"
    row = rows[0]
    assert set(row) >= {"id", "resource", "name", "purity", "x_m", "y_m", "z_m", "occupied"}
    # The id must survive intact: the page offers it as a `node:` selector for the MCP
    # tools, and a shortened one would not resolve there.
    assert row["id"].startswith("Persistent_Level:")
    assert row["name"] == row["id"].rsplit(".", 1)[-1]
    # Occupancy resolution is partial by design, but this save has extractors placed.
    assert body["occupied"] > 0
    assert any(r["occupant_cls"] for r in rows if r["occupied"])


def test_nodes_survive_a_save_that_cannot_be_read(game):
    """The node table is static and needs no ``.sav`` -- the rule /api/inspect already
    follows. A failed save costs the occupancy join, never the geography, and the loss
    is said out loud: ``save_error`` set, ``occupied`` null rather than a measured 0."""
    app = create_app(state_loader=_explode, game_loader=lambda: game)
    with TestClient(app) as c:
        r = c.get("/api/nodes")
    assert r.status_code == 200
    body = r.json()
    assert "sidecar produced no output" in body["save_error"]
    assert body["occupied"] is None
    assert len(body["nodes"]) > 500
    assert all(row["occupied"] is False for row in body["nodes"])


def test_nodes_resolve_their_occupant_to_a_display_name(client):
    """One popup, one vocabulary: 'occupied by Miner Mk.2', not Build_MinerMk2_C."""
    rows = client.get("/api/nodes").json()["nodes"]
    held = [r for r in rows if r["occupied"]]
    assert held
    for row in held:
        assert row["occupant_name"], row
        assert not row["occupant_name"].startswith("Build_")
    assert all(r["occupant_name"] is None for r in rows if not r["occupied"])


def test_nodes_can_be_filtered_by_resource(client):
    body = client.get("/api/nodes", params={"resource": "Desc_OreIron_C"}).json()
    assert body["resource"] == "Desc_OreIron_C"
    assert body["nodes"]
    assert {r["resource"] for r in body["nodes"]} == {"Desc_OreIron_C"}


def test_nodes_carry_the_region_they_sit_in(client):
    """Joined server-side, so the raster -- and its orientation trap -- lives in one place.

    ``verified`` used to appear here and does not any more. It meant "a human read this
    node's region off the wiki's biome image", which was the best answer available while the
    region geometry was a trace of that image; the geometry is the game's own map areas now,
    so the override table it justified is gone and every node is named by where it stands.
    ``unnamed`` is the word that replaced it in this set: the game leaves the outer coast to
    ``No Man's Land``, and nodes stand there.
    """
    rows = client.get("/api/nodes").json()["nodes"]
    named = [r for r in rows if r["region"]]
    assert len(named) > 500, "the raster covers the nodes; a handful of nulls is the limit"
    known = set(client.get("/api/regions").json()["regions"])
    assert {r["region"]["name"] for r in named} <= known
    # The confidence word travels with the name, or a boundary guess reads as a fact.
    assert {r["region"]["confidence"] for r in named} <= {
        "unnamed",
        "boundary",
        "interior",
    }
    assert "verified" not in {r["region"]["confidence"] for r in named}, (
        "verified was a claim about a wiki image and there is no wiki image any more"
    )
    assert any(r["region"]["confidence"] == "interior" for r in named)
