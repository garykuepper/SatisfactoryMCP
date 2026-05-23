"""Everything in the dump reachable, and a recipe findable by the name it prints under.

Three small refusals that each cost a caller a round trip or a wrong answer:
`list_buildings(kind="all")` matched nothing; the AWESOME Sink and both Pipeline Pumps
fell through every `kind`, so sink draw and pump head could not be checked against data
and got answered from general knowledge instead -- exactly the failure the rest of this
surface works to prevent; and `recipe_detail` refused a display name it could resolve.
"""

from __future__ import annotations

import pytest

from satisfactory_mcp import server as srv

pytestmark = pytest.mark.integration


def _rows(out: str) -> list[str]:
    return [line for line in out.splitlines() if "\t" in line and not line.startswith("#")][1:]


def test_all_reaches_every_building(game):
    out = srv.list_buildings(kind="all")
    assert len(_rows(out)) > 400


def test_an_unknown_kind_lists_the_kinds(game):
    out = srv.list_buildings(kind="bogus")
    assert out.startswith("! unknown kind")
    assert "logistics" in out and "all" in out


def test_the_sink_and_the_pumps_are_reachable(game):
    """The sink's 30 MW is the AWESOME_SINK_MW constant the optimizer charges, and pump
    head is what a fluid plan sizes risers with. Neither was listable."""
    everything = srv.list_buildings(kind="all")
    assert "AWESOME Sink" in everything
    logistics = srv.list_buildings(kind="logistics")
    assert "Pipeline Pump Mk.2" in logistics
    assert "lifts 50m head" in logistics


def test_recipe_detail_takes_a_display_name(game):
    out = srv.recipe_detail("Alternate: Heavy Oil Residue")
    assert "unknown recipe" not in out
    assert "Refinery" in out


def test_an_ambiguous_name_lists_the_candidates(game):
    """Ambiguous is not unknown, and listing what matched beats sending the caller back
    to search."""
    out = srv.recipe_detail("Residual")
    assert "matches 3 recipes" in out
    assert "Residual Plastic" in out


def test_a_class_id_still_works(game):
    assert "unknown recipe" not in srv.recipe_detail("Recipe_Alternate_HeavyOilResidue_C")
