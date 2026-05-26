"""What has been collected off the map, grouped -- and the one group that cannot be split.

The world is not saved, so a save never mentions a slug that is still lying there. It records
the negative: which map-placed actors are **gone**. A count of those *is* a collected count,
and it is the only one available.

The grouping is by class-name prefix and nothing else, because these lists carry no class
path -- only an instance name, from which the sidecar recovers an approximate class. Two
things about that are load-bearing and both are pinned below:

* **first match wins.** ``BP_Crystal_mk3_C_10`` starts with ``BP_Crystal`` as well as
  ``BP_Crystal_mk3``, so ``REMOVED_GROUPS`` is an ordered tuple and the plain slug prefix is
  tried last. Reversed, every purple slug in the world reads as blue.
* **the artifacts cannot be split by prefix at all.** 68% of these names are level-placed
  actors whose instance number is glued straight onto the blueprint name with no separator, and
  a somersloop is ``BP_WAT1`` while a Mercer sphere is ``BP_WAT2`` -- one digit apart, exactly
  where the number lands. ``BP_WAT112`` is therefore undecidable, and the save also holds
  ``BP_WAT60``, ``BP_WAT73`` and ``BP_WAT84``, which no gluing of 1 or 2 produces. So the
  ``strict`` groups accept only a name that spells the class out with ``_C``, and everything
  else falls to ``artifact_unsplit`` -- a real answer where a confident number would be
  invention.

Numbers come from the committed projection, which is the reference save: 889 actors in 284
world-partition cells.
"""

from __future__ import annotations

import copy

import pytest

from satisfactory_mcp.save.state import WorldState
from satisfactory_mcp.tools import progression
from satisfactory_mcp.tools.progression import collected_from_world

pytestmark = pytest.mark.integration

#: Measured on the committed projection. Every one of these is a floor on what was collected
#: and not a fraction of a known total -- nothing in this project knows how many slugs the map
#: has. ``artifact_unsplit`` is 65 rather than 98 because the 6 somersloops and 27 Mercer
#: spheres whose names spell their class with ``_C`` ARE split out; the rest cannot be.
CENSUS = {
    "flora": 185,
    "dropped_pickup": 170,
    "slug_blue": 163,
    "artifact_unsplit": 65,
    "somersloop": 6,
    "mercer_sphere": 27,
    "mercer_shrine": 80,
    "crash_site": 55,
    "debris": 51,
    "slug_yellow": 50,
    "slug_purple": 37,
}


# ------------------------------------------------------------------ the census


def test_the_census_covers_every_actor_with_nothing_unmatched(state):
    """889 actors, 284 cells, and the groups add up to the total.

    The adding-up is the point: an actor no group matched would land in ``other``, which is
    reported rather than dropped, so a class nobody anticipated shows up as a number instead of
    quietly shrinking the census. On this save there are none, and that is worth pinning --
    it is what says the prefix list covers what the save actually contains.
    """
    out = state.removed_actors()
    assert out["total"] == 889
    assert out["cells"] == 284
    assert out["groups"] == dict(sorted(CENSUS.items(), key=lambda kv: -kv[1]))
    assert sum(out["groups"].values()) == out["total"]
    assert "other" not in out


def test_the_groups_come_back_biggest_first(state):
    """A census is read top-down, and the caller renders it in dict order."""
    counts = list(state.removed_actors()["groups"].values())
    assert counts == sorted(counts, reverse=True)


def test_an_unmatched_class_is_reported_rather_than_dropped(state):
    """The tolerance that keeps this honest when the game adds a collectible.

    A class no prefix matches must not vanish into the total, because "889 collected" with one
    kind silently missing is a confidently wrong answer. It goes to ``other`` with its real
    name so the next person can add a prefix.
    """
    projection = copy.deepcopy(state.projection)
    # Injected as INSTANCES, which is what the census counts. Adding to `counts` instead would
    # change nothing, and a test that passed by editing the ignored field would be worthless.
    projection["removed"]["instances"] += [[0, "BP_SomethingNew_7"]] * 3
    out = WorldState(projection=projection, game=state.game).removed_actors()
    assert out["other"] == {"BP_SomethingNew": 3}
    assert out["total"] == 892
    assert sum(out["groups"].values()) == 889


def test_a_projection_with_no_removed_key_reports_nothing_rather_than_failing(state):
    """Anything cached before schema 11 has no such key. Zero is the wrong answer there and
    the tool says so in a note; what this pins is that it does not raise on the way."""
    projection = copy.deepcopy(state.projection)
    del projection["removed"]
    out = WorldState(projection=projection, game=state.game).removed_actors()
    assert out == {"total": 0, "groups": {}, "cells": 0}


# ---------------------------------------------------------------- the grouping


def test_the_strict_rule_refuses_to_guess_an_artifact(state):
    """THE test for this feature. ``BP_WAT1`` and ``BP_WAT2`` differ in the digit the game
    glues an instance number onto, so a name that does not spell its class is undecidable.

    ``BP_WAT112`` could be somersloop 12, sphere... nothing, or a blueprint literally named
    ``BP_WAT112``; the save holding ``BP_WAT60``, ``BP_WAT73`` and ``BP_WAT84`` proves the
    third reading is real. Calling it a somersloop would be a number a player could act on
    and would be made up.
    """
    assert state.removed_group("BP_WAT1_C_11") == "somersloop"
    assert state.removed_group("BP_WAT2_C_18") == "mercer_sphere"
    assert state.removed_group("BP_WAT112_14") == "artifact_unsplit"
    assert state.removed_group("BP_WAT60") == "artifact_unsplit"
    # Not merely "not somersloop": nothing may fall through the artifact prefixes entirely.
    for name in ("BP_WAT112_14", "BP_WAT60", "BP_WAT73", "BP_WAT84", "BP_WAT2_228"):
        assert state.removed_group(name) == "artifact_unsplit", name


def test_first_match_wins_so_a_purple_slug_is_never_blue(state):
    """``REMOVED_GROUPS`` is ordered, not a mapping, and this is why: every slug name starts
    with ``BP_Crystal``. Testing the plain prefix first would file all 250 slugs as blue and
    the tiers would silently disappear -- with the total still correct, which is what makes it
    a dangerous kind of wrong."""
    assert state.removed_group("BP_Crystal_mk3_C_10") == "slug_purple"
    assert state.removed_group("BP_Crystal_mk2_C_4") == "slug_yellow"
    assert state.removed_group("BP_Crystal_C_UAID_04421A9713F0395B01_1557158296") == "slug_blue"
    # The glued shape keeps its tier too, which is why gluing is harmless for slugs.
    assert state.removed_group("BP_Crystal_mk21_23") == "slug_yellow"
    assert state.removed_group("BP_Crystal_mk316") == "slug_purple"


def test_a_class_no_prefix_covers_is_none_rather_than_a_nearest_guess(state):
    """``None`` is what puts a class into ``other``. A nearest-prefix fallback would file a
    building under whatever collectible it happened to share three letters with."""
    assert state.removed_group("Build_Foundation_8x1_01_C") is None
    assert state.removed_group("") is None


def test_an_unknown_group_name_comes_back_as_an_error_not_an_exception(state):
    """The name reaches this from a tool argument, so a typo is a user event rather than a bug.

    It must answer with the census plus an ``error`` naming the known groups -- which the tool
    returns verbatim -- and never raise, because an MCP tool that raises gives the model a
    stack trace instead of the list of valid names.
    """
    out = state.removed_actors("no_such_group")
    assert "actors" not in out
    assert out["total"] == 889
    assert out["error"].startswith("unknown group 'no_such_group'; known: [")
    assert "'somersloop'" in out["error"]


# ------------------------------------------------------------- listing a group


def test_listing_a_group_names_the_actor_and_the_cell_it_was_in(state):
    """The cell is the only location these lists carry -- there is no transform on a destroyed
    actor -- and it is a real world-partition id, so it can be looked up. 25 base-36
    characters, which is what every cell id in the grid table is."""
    out = state.removed_actors("somersloop")
    assert out["group"] == "somersloop"
    assert len(out["actors"]) == 6
    for actor in out["actors"]:
        assert actor["actor"].startswith("BP_WAT1_C")
        assert len(actor["cell"]) == 25 and actor["cell"].isalnum()


def test_every_group_that_can_be_listed_lists_only_its_own(state):
    """The listing is filtered by the same ``removed_group`` the census uses, so a name that
    would land in two groups would appear twice and the parts would not add to the whole."""
    every = [leaf for _ix, leaf in state.projection["removed"]["instances"]]
    seen: list[str] = []
    for group in CENSUS:
        seen += [a["actor"] for a in state.removed_actors(group)["actors"]]
    assert len(seen) == len(every) == 889
    assert sorted(seen) == sorted(every)


def test_the_strict_split_reaches_the_census_and_not_only_the_listing(state):
    """The invariant that should hold for every group: what you can list is what was counted.

    It holds for all nine groups the census reports, and fails for exactly the two strict
    ones. The arithmetic says the same thing twice: 6 somersloops plus 27 spheres plus the 65
    genuinely undecidable names is 98, which is the whole ``artifact_unsplit`` count.
    """
    census = state.removed_actors()["groups"]
    for group, _prefixes, _strict in state.REMOVED_GROUPS:
        listed = len(state.removed_actors(group)["actors"])
        assert listed == census.get(group, 0), group


def test_the_census_and_the_listing_agree_for_every_group(state):
    """Was a bounded test for a real defect, now the invariant that defect broke.

    The census used to be built from the projection's recovered CLASS names while the listing
    walked INSTANCE names. Those differ by a trailing ``_C``, which is precisely what a strict
    group tests for -- so somersloop and mercer_sphere could be listed individually while being
    counted under ``artifact_unsplit``, and the parts did not add up to the whole. Both now come
    from the instance names, so every group agrees, and this fails if the two paths ever diverge
    again.
    """
    census = state.removed_actors()["groups"]
    for group, _prefixes, _strict in state.REMOVED_GROUPS:
        listed = len(state.removed_actors(group)["actors"])
        assert listed == census.get(group, 0), group
    assert census["somersloop"] + census["mercer_sphere"] + census["artifact_unsplit"] == 98


# ------------------------------------------------------------------- the tool


@pytest.fixture
def tool(monkeypatch, state):
    """``collected_from_world`` against the committed projection rather than the live save.

    The tool resolves its own state, and the numbers here are exact, so the fixture is
    substituted for the autosave -- otherwise every count below would drift with play.
    """
    monkeypatch.setattr(progression, "_state", lambda save, world: state)
    return collected_from_world


def test_the_tool_reports_the_total_the_cells_and_the_table(tool):
    out = tool()
    assert "total_removed=889" in out
    assert "map_cells=284" in out
    assert "group\tcollected" in out
    for group, count in CENSUS.items():
        assert f"{group}\t{count}" in out


def test_the_tool_says_these_are_collected_and_not_remaining(tool):
    """The one way this output can mislead. "37 purple slugs" reads as a fraction of a known
    total, and there is no known total -- so the note is not decoration."""
    out = tool()
    assert "collected, not remaining" in out
    assert "not a fraction of a known total" in out


def test_the_tool_explains_dropped_pickup_because_it_is_not_a_collectible(tool):
    """170 of the 889 are loot the player dropped and picked back up. Counting them silently
    among the map collectibles would inflate the interesting number by a fifth."""
    assert "'dropped_pickup' is loot the player dropped" in tool()


def test_the_tool_lists_a_group_when_asked(tool):
    out = tool(group="somersloop")
    assert "actor\tcell" in out
    assert "BP_WAT1_C_11" in out
    assert "total, showing" not in out, "all 6 fit under the default limit"


def test_a_long_listing_is_truncated_and_says_by_how_much(tool):
    """65 rows would be most of a context budget. The count has to survive the truncation, or
    the model reads the shown rows as the whole answer."""
    out = tool(group="artifact_unsplit", limit=10)
    assert "(65 total, showing 10)" in out


def test_an_unknown_group_returns_the_known_names_and_no_table(tool):
    """A bare error line, so the model's next call can be right. It must not also print the
    census: a tool that answers a wrong question anyway teaches the model the argument was
    accepted."""
    out = tool(group="sloops")
    assert out.startswith("unknown group 'sloops'; known: [")
    assert "total_removed" not in out


def test_an_empty_removed_key_is_called_unreadable_rather_than_none(monkeypatch, state):
    """A projection cached before schema 11 has no list, and "0 collected" would be a
    confident wrong answer -- the player has certainly picked up a slug. The note has to say
    re-read the save."""
    projection = copy.deepcopy(state.projection)
    projection["removed"] = {"cells": [], "instances": [], "counts": {}}
    older = WorldState(projection=projection, game=state.game)
    monkeypatch.setattr(progression, "_state", lambda save, world: older)
    out = collected_from_world()
    assert "total_removed=0" in out
    assert "unreadable rather than none" in out


def test_an_unreadable_save_is_a_sentence_not_a_traceback(monkeypatch):
    """Every save-reading tool in this package answers that way, and a raise here would reach
    the MCP client as a protocol error instead of something the model can act on."""

    def boom(save, world):
        raise RuntimeError("no such save")

    monkeypatch.setattr(progression, "_state", boom)
    assert collected_from_world() == "could not read save: no such save"
