"""Deep links into satisfactory-calculator.com's interactive map.

The whole module rests on one working link the player supplied. The site returns 403 to
automated fetches, so nothing could be read from it -- which makes it important to be
precise about which parts are known and which are inferred.
"""

from __future__ import annotations

import pytest

from satisfactory_mcp.spatial import maplink

pytestmark = pytest.mark.integration

#: The link the player supplied, verbatim.
SAMPLE = (
    "https://satisfactory-calculator.com/en/interactive-map"
    "#4.75;40351;-208857|gameLayer|oilWellPure;oilNormal;oilWellNormal;"
    "oilImpure;oilWellImpure;oilPure"
)


def test_the_generated_shape_matches_the_supplied_link():
    """Same zoom, same coordinate, same group, same six oil tokens. Order differs and
    that is fine -- the site takes a set."""
    url = maplink.map_url(40351, -208857, maplink.layers_for(["Desc_LiquidOil_C"]))
    head, _, fragment = url.partition("#")
    zoom, x, y = fragment.split("|")[0].split(";")
    assert head == maplink.BASE
    assert (zoom, x, y) == ("4.75", "40351", "-208857")
    assert fragment.split("|")[1] == "gameLayer"

    sample_tokens = set(SAMPLE.split("|")[-1].split(";"))
    assert set(fragment.split("|")[2].split(";")) == sample_tokens


def test_coordinates_are_save_centimetres_not_metres():
    """Every other tool in this MCP quotes metres. Doing the conversion anywhere but the
    caller would put a metre value 1/100th of the way across the map, near the origin,
    which looks plausible and is wrong."""
    url = maplink.map_url(-106900, -127300)
    assert "#4.75;-106900;-127300|" in url


def test_a_coordinate_is_rounded_not_truncated():
    url = maplink.map_url(-109969.6, -124184.4)
    assert ";-109970;-124184|" in url


def test_both_node_and_well_variants_are_offered():
    """Crude Oil exists as nodes AND resource wells; a link showing one and hiding the
    other answers a narrower question than was asked."""
    tokens = maplink.layers_for(["Desc_LiquidOil_C"])
    assert "oilPure" in tokens and "oilWellPure" in tokens
    assert len(tokens) == 6, tokens


def test_every_purity_is_included():
    """Opening the map on one impure node while hiding the pure one beside it is worse
    than showing all three."""
    tokens = maplink.layers_for(["Desc_OreIron_C"])
    assert {"ironImpure", "ironNormal", "ironPure"} <= set(tokens)


def test_geysers_have_no_purity_variants():
    assert maplink.layers_for(["Desc_Geyser_C"]) == ["geyser"]


def test_only_crude_oil_is_claimed_as_verified():
    """The supplied link proves oil and nothing else. Marking more as known would be
    inventing confidence."""
    assert maplink.VERIFIED_RESOURCES == {"Desc_LiquidOil_C"}
    assert set(maplink.LAYERS) - maplink.VERIFIED_RESOURCES


def test_an_unknown_resource_contributes_no_token_rather_than_a_guess():
    assert maplink.layers_for(["Desc_NotAThing_C"]) == []


def test_the_fragment_delimiters_survive_escaping():
    """; and | are structural here; percent-encoding them would break the fragment."""
    url = maplink.map_url(1, 2, ["oilPure", "ironNormal"])
    assert "|gameLayer|oilPure;ironNormal" in url
    assert "%3B" not in url and "%7C" not in url


def test_well_variants_are_offered_only_where_wells_exist(game):
    """Coal is node-only. Emitting coalWellPure invents a token for something that does
    not exist -- harmless, since an unknown layer is ignored, but it is a guess with no
    need to be made: the node table already knows which resources have wells."""
    from satisfactory_mcp.spatial import nodes as nodes_mod

    table = nodes_mod.load_nodes()

    def kinds_of(res):
        return sorted(
            {"well" if r["kind"].startswith("well") else "node" for r in table.by_resource(res)}
        )

    assert kinds_of("Desc_Coal_C") == ["node"]
    assert kinds_of("Desc_NitrogenGas_C") == ["well"]
    assert kinds_of("Desc_LiquidOil_C") == ["node", "well"]

    assert maplink.layers_for(["Desc_Coal_C"], ["node"]) == [
        "coalImpure",
        "coalNormal",
        "coalPure",
    ]
    assert all("Well" in t for t in maplink.layers_for(["Desc_NitrogenGas_C"], ["well"]))
