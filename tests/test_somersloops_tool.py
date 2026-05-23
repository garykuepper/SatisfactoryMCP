"""The sloop inventory, which existed and was not exposed.

`sloop_budget` has been on WorldState since sloops became spendable, and nothing surfaced
it -- so the only way to learn how many you held was to guess a `sloops=` budget and read
the shortfall warning. You had to guess the budget to discover the budget.
"""

from __future__ import annotations

import pytest

from satisfactory_mcp import server as srv

pytestmark = pytest.mark.integration


@pytest.fixture
def live(game):
    from satisfactory_mcp.app import _state

    return _state(None, None)


def test_it_reports_free_committed_and_owned(game, live):
    out = srv.somersloops()
    budget = live.sloop_budget()
    assert f"free={budget['free']:.0f}" in out
    assert f"committed={budget['committed']:.0f}" in out
    assert f"owned={budget['owned']:.0f}" in out


def test_owned_is_free_plus_committed(live):
    budget = live.sloop_budget()
    assert budget["owned"] == pytest.approx(budget["free"] + budget["committed"])


def test_only_free_sloops_can_fund_a_plan(game):
    """Committed ones are counted so you know there is something to pull out, never added
    to what is spendable -- the same rule the shard budget follows for slotted shards."""
    out = srv.somersloops()
    assert "only FREE sloops can fund a plan" in out


def test_mercer_spheres_are_reported_apart(game, live):
    out = srv.somersloops()
    assert "mercer_spheres=" in out
    assert "do nothing for production" in out


def test_a_holder_row_names_the_machine_and_its_boost(game, live):
    """So "pull one out" is an instruction rather than a hint."""
    if not live.sloop_budget()["holders"]:
        pytest.skip("nothing slotted in this save")
    out = srv.somersloops()
    assert "building\tinstance\tsloops\tboost" in out
