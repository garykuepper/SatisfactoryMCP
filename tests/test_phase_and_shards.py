"""Space Elevator phase requirements, and Power Shard accounting.

Both features hang off save fields that say something different from what they look
like they say, so each test below pins the counter-example that corrected the code:

* the per-phase cost table in the save is DEPRECATED and frozen, and still bills the
  player for a phase they finished 70 hours of play ago;
* the 97 Power Shards in the save's "machine" inventory bucket are not shards on hand,
  they are the shards already screwed into machines;
* and the number of shards a machine holds is not recoverable from its clock.
"""

from __future__ import annotations

import copy

import pytest

from satisfactory_mcp.core.gamedata.constants import (
    POTENTIAL_SHARD_SLOTS,
    max_clock,
    shards_for_clock,
)
from satisfactory_mcp.domain.world.state import WorldState

pytestmark = pytest.mark.integration


def _with(state: WorldState, **progression) -> WorldState:
    """A copy of the state with progression fields overridden."""
    projection = copy.deepcopy(state.projection)
    projection["progression"].update(progression)
    return WorldState(projection=projection, game=state.game)


# ------------------------------------------------------------------ the EGP mapping


def test_endgame_maps_to_phase_3_which_is_the_one_anchor_that_is_measured(state):
    """THE anchor. Docs.json does not ship the UFGGamePhase assets, so nothing joins
    the deprecated EGP_* enum to GP_Project_Assembly_Phase_N there, and the save's own
    legacy mGamePhase scalar is absent (= EGP_NA = "we have migrated the save").

    What DOES join them is two epochs of this world. At 180-244 h play the save read
    mTargetGamePhase = Phase_3 with mTargetGamePhasePaidOffCosts =
    {Desc_SpaceElevatorPart_2_C: 2500} -- exactly one item settled. The EGP_EndGame row
    of the deprecated table describes those same three items with that same one item at
    zero remaining, and nothing can be paid into a phase that was never the target.
    """
    assert WorldState.EGP_TO_PHASE["EGP_EndGame"] == "GP_Project_Assembly_Phase_3"

    row = next(r for r in state.phase_requirements()["phases"] if r["egp"] == "EGP_EndGame")
    # Part_2 = Versatile Framework, the one item the live paid-off record accounted for.
    assert row["complete"] == ["Desc_SpaceElevatorPart_2_C"]
    assert set(row["outstanding"]) == {
        "Desc_SpaceElevatorPart_4_C",
        "Desc_SpaceElevatorPart_5_C",
    }


def test_the_four_stored_keys_are_contiguous_in_enum_order(state):
    """The other three phases are derived, not measured: EGamePhase is declared
    EarlyGame 0 < MidGame 1 < LateGame 2 < EndGame 3 < FoodCourt 4 in the shipped
    header, and the save stores exactly the four keys after EarlyGame. Ordering only
    carries the mapping if that block really is contiguous, so pin it -- a game patch
    that stores EGP_EarlyGame or skips a key must fail here rather than silently
    shift every phase number by one."""
    stored = list(state.projection["progression"]["phase_costs_remaining"])
    assert stored == ["EGP_MidGame", "EGP_LateGame", "EGP_EndGame", "EGP_FoodCourt"]
    phases = [WorldState.EGP_TO_PHASE[k] for k in stored]
    assert phases == [f"GP_Project_Assembly_Phase_{n}" for n in (1, 2, 3, 4)]


def test_an_unmapped_key_is_reported_rather_than_dropped(state):
    """EGP_Victory and EGP_EarlyGame exist in the enum and are not in the table. If a
    patch starts storing one, showing three rows where the save has four would be worse
    than showing a row labelled unmapped -- a silently missing phase reads as a phase
    with nothing outstanding."""
    st = _with(
        state,
        phase_costs_remaining={
            **state.projection["progression"]["phase_costs_remaining"],
            "EGP_Victory": {"Desc_SpaceElevatorPart_9_C": 7},
        },
    )
    rows = st.phase_requirements()["phases"]
    victory = next(r for r in rows if r["egp"] == "EGP_Victory")
    assert victory["phase"] is None
    assert victory["stale"] == "unmapped"
    assert victory["outstanding"] == {"Desc_SpaceElevatorPart_9_C": 7}


# ------------------------------------------------- the deprecated table is frozen


def test_a_completed_phase_still_bills_the_player_and_is_flagged_stale(state):
    """THE correction, and the reason this tool exists at all.

    mGamePhaseCosts is byte-identical across all 29 parseable saves of this world, 180 h
    to 316 h -- a span that includes the session where mCurrentGamePhase advanced
    Phase_2 -> Phase_3. Completing an entire Space Elevator phase moved nothing in it.
    So it still shows 500 Modular Engine and 100 Adaptive Control Unit outstanding on
    Phase 3, which the player has already delivered. Any planner that reads those
    amounts as work remaining plans the wrong factory.
    """
    req = state.phase_requirements()
    assert req["current_phase"] == "GP_Project_Assembly_Phase_3"

    phase3 = next(r for r in req["phases"] if r["phase"] == "GP_Project_Assembly_Phase_3")
    assert phase3["outstanding"] == {
        "Desc_SpaceElevatorPart_4_C": 500,  # Modular Engine
        "Desc_SpaceElevatorPart_5_C": 100,  # Adaptive Control Unit
    }
    # Not filtered away -- shown, and labelled as not to be believed.
    assert phase3["stale"] == "stale"


def test_the_untouched_target_row_is_the_only_one_marked_usable(state):
    """Frozen does not mean wrong for every row. Nothing has been delivered toward
    Phase 4 (mTargetGamePhasePaidOffCosts is absent, i.e. empty), so its snapshot has
    never had a chance to drift and still equals the full cost. That is a checkable
    condition, not an assumption, and it is the one row a planner may use."""
    req = state.phase_requirements()
    assert req["paid_off_target"] == {}
    trust = {r["phase"]: r["stale"] for r in req["phases"]}
    assert trust["GP_Project_Assembly_Phase_4"] == "usable"
    assert [p for p, t in trust.items() if t == "usable"] == ["GP_Project_Assembly_Phase_4"]


def test_the_target_row_stops_being_usable_the_moment_anything_is_delivered(state):
    """The usable claim rests entirely on the phase never having been paid into. One
    delivery and the frozen snapshot is stale like all the others -- it will keep
    reporting the full 4000 while the live record says 4000 are already in."""
    st = _with(state, paid_off_target={"Desc_SpaceElevatorPart_7_C": 4000})
    req = st.phase_requirements()
    phase4 = next(r for r in req["phases"] if r["phase"] == "GP_Project_Assembly_Phase_4")
    assert phase4["stale"] == "stale"
    assert req["paid_off_target"] == {"Desc_SpaceElevatorPart_7_C": 4000}


def test_absent_paid_off_record_is_empty_not_missing(state):
    """UE omits empty SaveGame TArrays, so a projection with no paid_off_target at all
    means nothing delivered -- a real answer. Defaulting it to None and rendering a
    "-" would hide that Phase 4 is untouched, which is exactly the fact that makes its
    snapshot usable."""
    projection = copy.deepcopy(state.projection)
    projection["progression"].pop("paid_off_target", None)
    st = WorldState(projection=projection, game=state.game)
    assert st.phase_requirements()["paid_off_target"] == {}


# --------------------------------------------------------------- the shard rule


def test_a_shard_adds_max_clock_from_docs_not_from_a_hardcoded_2_point_5(game):
    """mExtraPotential = 0.5 is in Docs.json under FGPowerShardDescriptor. Only the
    slot count is game knowledge, so the 2.5 ceiling is computed, not written down."""
    shards = game.clock_shards()
    assert shards == {"Desc_CrystalShard_C": 0.5}
    assert max_clock(shards["Desc_CrystalShard_C"]) == 2.5


def test_the_somersloop_shares_the_native_class_and_must_not_count_as_a_shard(game):
    """Desc_WAT1_C sits in the same FGPowerShardDescriptor group as the Power Shard, so
    selecting the group would count Somersloops as overclocking capacity. It has
    mExtraPotential 0 and mExtraProductionBoost 1, and filtering on the field excludes
    it without either class being named in code."""
    assert game.items["Desc_WAT1_C"].extra_potential == 0.0
    assert "Desc_WAT1_C" not in game.clock_shards()


def test_clock_2_0_needs_two_shards_not_three_despite_float_saves(game):
    """Saved clocks are floats and 2.0 arrives as 1.9999999 often enough to matter. A
    bare ceil() on (clock - 1) / 0.5 rounds that up to 3 and reports a 200% machine as
    holding a shard it does not."""
    assert shards_for_clock(2.0, 0.5) == 2
    assert shards_for_clock(1.9999999, 0.5) == 2
    assert shards_for_clock(1.5, 0.5) == 1
    assert shards_for_clock(2.5, 0.5) == 3


def test_underclocking_and_100_percent_need_no_shards(game):
    """5 of the 46 clocked buildings on the reference save are UNDERclocked (down to
    0.333). Treating any clock != 1.0 as an overclock would invent shards for them."""
    assert shards_for_clock(1.0, 0.5) == 0
    assert shards_for_clock(0.333333, 0.5) == 0


def test_shards_needed_never_exceeds_the_slot_count(game):
    """A clock above the ceiling cannot happen in game, but a modded or future save
    could carry one, and reporting "needs 5 shards" for a 3-slot building is worse than
    reporting the cap."""
    assert shards_for_clock(10.0, 0.5) == POTENTIAL_SHARD_SLOTS


# ------------------------------------------------------------- the shard budget


def test_committed_shards_are_read_from_slots_never_derived_from_clock(state):
    """THE correction for this feature. Two buildings on this save run at clock 2.0
    while holding 3 shards: a shard raises the MAXIMUM clock and the slider is set
    separately, so the player left a slot filled and pulled the clock back.

    Summing shards_for_clock over the 42 overclocked buildings gives 98. Reading
    InventoryPotential gives 100. The 2 missing ones are really spent and really not
    available to build with.
    """
    budget = state.shard_budget()
    derived = sum(h["needed"] for h in budget["holders"])
    assert derived == 98
    assert budget["committed"] == 100
    assert budget["measured"] is True

    slack = [h for h in budget["holders"] if h["idle"]]
    assert len(slack) == 2
    assert all(h["clock"] == 2.0 and h["slotted"] == 3 for h in slack)


def test_shards_on_hand_exclude_the_ones_already_inside_machines(state):
    """The save's "machine" inventory bucket holds 100 Power Shards and every one of
    them is in an InventoryPotential component, i.e. already installed. Reading that
    total as shards on hand overstates the free pool by more than 5x -- the player can
    actually spend 19, all of them in the Dimensional Depot."""
    assert state.projection["inventories"]["machine"]["Desc_CrystalShard_C"] == 100
    budget = state.shard_budget()
    assert budget["free"] == 19.0
    assert budget["committed"] == 100
    assert budget["owned"] == 119.0


def test_every_overclocked_building_holds_shards_and_no_other_building_does(state):
    """A cross-check that the two halves agree: 42 buildings have clock > 1.0 and 42
    hold shards, and they are the same 42. An underclocked building holding a shard, or
    an overclocked one holding none, would mean the slot inventory is not what it looks
    like."""
    budget = state.shard_budget()
    overclocked = {r["instance"].rsplit(".", 1)[-1] for r in state.overclocked if r["clock"] > 1.0}
    holders = {h["instance"] for h in budget["holders"]}
    assert len(overclocked) == 42
    assert holders == overclocked


def test_a_projection_without_slot_data_reports_unmeasured_rather_than_zero(state):
    """Schema 9 added potential_slots. An older cached projection has none, and
    reporting "0 shards committed" for it would be a confident wrong answer where
    "unknown" is the true one."""
    projection = copy.deepcopy(state.projection)
    for key in ("machines", "extractors", "generators"):
        for record in projection[key]:
            record.pop("potential_slots", None)
    st = WorldState(projection=projection, game=state.game)
    budget = st.shard_budget()
    assert budget["measured"] is False
    assert budget["committed"] == 0
