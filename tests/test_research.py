"""MAM research, and the capabilities that have no flag in the save.

Overclocking records itself as `mIsBuildingOverclockUnlocked`. Production amplification —
the research that lets a Somersloop go into a machine at all — records nothing. Probed
directly: `BP_UnlockSubsystem_C` carries thirteen properties and none of them is about
boosting, and no key containing "Boost", "Amplif" or "Sloop" appears anywhere in the
save's 44,307 objects.

So the capability is derived from the purchased-schematic set, which is read and exact.
The tests below pin that derivation and, more importantly, that the planner refuses to be
silent when a plan spends a capability the player has not got.
"""

from __future__ import annotations

import pytest

from satisfactory_mcp import server as srv
from satisfactory_mcp.docs.constants import CAPABILITY_SCHEMATICS

pytestmark = pytest.mark.integration


# ------------------------------------------------------- the register


def test_every_gate_names_a_schematic_that_exists(game):
    """A typo here would silently report a capability as permanently locked."""
    assert CAPABILITY_SCHEMATICS
    for capability, cls in CAPABILITY_SCHEMATICS.items():
        assert cls in game.schematics, capability
        assert game.schematics[cls].type == "EST_MAM"


def test_production_boost_is_gated_by_production_amplifier(game):
    schematic = game.schematics[CAPABILITY_SCHEMATICS["production_boost"]]
    assert schematic.name == "Production Amplifier"
    costs = {game.item_name(f.item): f.amount for f in schematic.cost}
    assert costs == {"Somersloop": 1, "SAM Fluctuator": 100, "Circuit Board": 50}


# ------------------------------------------------------- reading it from a save


def test_a_capability_is_read_from_purchased_schematics(game, state):
    """Not from a flag, because there is no flag. This asserts the derivation runs and
    agrees with the purchased set rather than asserting a particular answer, so it holds
    whichever side of the research the reference save is on."""
    for capability, cls in CAPABILITY_SCHEMATICS.items():
        assert state.has_capability(capability) == (cls in state.purchased_schematic_ids)


def test_an_unknown_capability_is_locked_rather_than_crashing(game, state):
    assert state.has_capability("no_such_capability") is False
    assert state.research_gate("no_such_capability") is None


def test_a_researched_capability_has_no_gate(game, state):
    """`research_gate` returns None once done, so a truthy result always means 'still in
    the way' and a caller needs no second check."""
    for capability in CAPABILITY_SCHEMATICS:
        if state.has_capability(capability):
            assert state.research_gate(capability) is None


def test_a_gate_prices_itself_against_spendable_stock(game, state):
    gate = state.research_gate("production_boost")
    if gate is None:
        pytest.skip("production boost already researched on this save")
    assert gate["schematic_name"] == "Production Amplifier"
    assert gate["cost"] and all("need" in r and "have" in r for r in gate["cost"])
    assert gate["affordable"] == (not gate["short"])
    stock = state.stock()
    for row in gate["cost"]:
        assert row["have"] == stock.get(row["item"], 0.0)


# ------------------------------------------------------- the planner gate


def test_spending_sloops_without_the_research_is_called_out(game, state):
    """The same class of check as the unlocked recipe set: a plan using a locked
    capability is not a plan. It warns rather than refusing, because planning ahead of
    cheap research is legitimate — but silence would print an unbuildable plan."""
    kw = dict(
        sources=["region:Spire Coast"],
        objective="max_mw",
        exports=["MW"],
        extractor_clocks=[1, 1.5, 2, 2.5],
        limit=2,
    )
    out = srv.plan_factory(sloops=16, **kw)
    if state.has_capability("production_boost"):
        assert "NOT RESEARCHED" not in out
        return
    assert "PRODUCTION AMPLIFIER IS NOT RESEARCHED" in out
    assert "not buildable as printed" in out
    # And it says what to do about it, with the bill.
    assert "SAM Fluctuator" in out


def test_the_gate_only_fires_when_sloops_are_actually_budgeted(game):
    """It is about SPENDING them. A plan that spends none is buildable today, and a
    warning there would be noise on every single call."""
    out = srv.plan_factory(
        sources=["region:Spire Coast"],
        objective="max_mw",
        exports=["MW"],
        extractor_clocks=[1, 1.5, 2, 2.5],
        limit=2,
    )
    assert "NOT RESEARCHED" not in out


# ------------------------------------------------------- the tool


def test_mam_research_lists_outstanding_nodes(game):
    out = srv.mam_research()
    assert not out.startswith("! ")
    assert "status\tresearch\tcapability" in out
    assert "outstanding" in out


def test_it_marks_which_research_gates_a_capability(game, state):
    """The point of the column: 'LOCKS production_boost' is why a reader should care about
    that row rather than treating the MAM as a pile of optional recipes."""
    # Narrowed with `search` rather than a big limit: Limit is schema-capped at 25 and
    # there are 120 MAM nodes, so the row would fall off the bottom of an unfiltered call.
    out = srv.mam_research(status="all", search="Production Amplifier")
    if state.has_capability("production_boost"):
        pytest.skip("already researched, so the row is not listed as a gate to clear")
    assert "LOCKS production_boost" in out


def test_a_locked_capability_gets_a_note_with_its_bill(game, state):
    out = srv.mam_research()
    if state.has_capability("production_boost"):
        return
    line = next(x for x in out.splitlines() if "production_boost is NOT researched" in x)
    assert "Production Amplifier" in line
    assert "Somersloop" in line


def test_affordable_narrows_to_what_can_be_done_now(game):
    everything = srv.mam_research(status="all", limit=80)
    ready = srv.mam_research(status="affordable", limit=80)
    assert "DONE" in everything
    # Status is the FIRST column of a data row. Matching the bare word would hit the
    # "short by" column HEADER, which is always present and says nothing about the rows.
    statuses = {r.split("\t")[0] for r in ready.splitlines() if "\t" in r}
    assert "READY" in statuses
    assert statuses <= {"status", "READY", "BLOCKED"}, statuses


def test_an_unknown_status_lists_the_choices(game):
    assert "all, todo, affordable" in srv.mam_research(status="bogus")


def test_search_filters_by_name(game):
    out = srv.mam_research(status="all", search="amplifier")
    rows = [x for x in out.splitlines() if "\t" in x][1:]
    assert rows
    assert all("mplifier" in r for r in rows)
