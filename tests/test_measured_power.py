"""Measured power, water volumes, and pushing back on "not modelled".

`power_report` said "fuel supply and uptime are not modelled here" and reported pure
nameplate. The uptime was in the projection all along -- the 300 s productivity monitor,
on 520 of 566 records -- and the gap it hid was not small: nameplate draw 6,839 MW against
a measured 1,516, so headroom read 711 MW where roughly 6,034 was free. `commission_plan`
sized a startup against the wrong one.

OQ5 said water pumps "carry no node, purity or geometry" and could not be matched to
anything. The volume's SHAPE really is level geometry and absent, but its IDENTITY is in
every pump's `mExtractableResource`, which the sidecar had been storing in `node`.
"""

from __future__ import annotations

import pytest

from satisfactory_mcp import server as srv

pytestmark = pytest.mark.integration


@pytest.fixture
def live(game):
    from satisfactory_mcp.interfaces.mcp.app import _state

    return _state(None, None)


# ------------------------------------------------------------ measured power


def test_measured_draw_never_exceeds_nameplate(live):
    """Utilisation is a fraction, so weighting by it can only reduce. If this inverted,
    the weighting would be being applied the wrong way round."""
    pw = live.power_report()
    assert pw["measured_draw_mw"] <= pw["draw_mw"] + 1e-6
    assert pw["measured_headroom_mw"] >= pw["headroom_mw"] - 1e-6
    assert 0.0 <= pw["utilisation"] <= 1.0


def test_an_unmonitored_machine_is_charged_in_full(game):
    """Unknown utilisation must not read as idle, or a factory the save cannot see would
    look free. Charging it fully keeps the measured figure conservative."""
    from satisfactory_mcp.domain.world.state import WorldState

    machines = [
        {"instance": "L:P.a", "cls": "Build_ConstructorMk1_C", "recipe": None},
        {
            "instance": "L:P.b",
            "cls": "Build_ConstructorMk1_C",
            "recipe": None,
            "uptime": {"window_s": 300.0, "produce_s": 0.0},
        },
    ]
    st = WorldState(projection={"machines": machines}, game=game)
    pw = st.power_report()
    rated = game.buildings["Build_ConstructorMk1_C"].power_at(1.0)
    assert pw["draw_mw"] == pytest.approx(2 * rated)
    # The monitored one produced nothing, so only the UNMONITORED one is charged.
    assert pw["measured_draw_mw"] == pytest.approx(rated)
    assert (pw["monitored"], pw["unmonitored"]) == (1, 1)


def test_generation_is_capacity_on_both_figures(live):
    """Generators burn to meet demand rather than at a rate of their own, so weighting
    them by uptime would double-count the same idleness already seen on the draw side."""
    pw = live.power_report()
    assert pw["generation_mw"] > 0
    assert pw["measured_headroom_mw"] == pytest.approx(pw["generation_mw"] - pw["measured_draw_mw"])


def test_the_tool_shows_both_and_says_which_is_safe(game):
    out = srv.power_report()
    assert "headroom_MW_nameplate" in out
    assert "headroom_MW_measured" in out
    assert "SAFE bound" in out
    assert "not modelled" not in out


def test_commissioning_defaults_to_the_safe_bound_but_names_the_other(game, live):
    """Energising a block can un-starve idle machines, and the fuse blows on demand rather
    than on averages -- so the conservative figure is the default. Staying quiet about the
    real one would make a 4-wave answer look mandatory when it is not."""
    out = srv.commission_plan(
        objective="max_mw",
        sources=["region:Spire Coast"],
        exports=["MW"],
        extractor_clocks=[1, 1.5, 2, 2.5],
        limit=4,
    )
    pw = live.power_report()
    assert "source: power_report, nameplate" in out
    if pw["measured_headroom_mw"] > pw["headroom_mw"] * 1.2:
        assert "utilised" in out
        assert "headroom_mw=" in out


def test_a_bigger_headroom_really_does_mean_fewer_waves(game, live):
    """The reason the note is worth printing at all."""
    kw = dict(
        objective="max_mw",
        sources=["region:Spire Coast"],
        exports=["MW"],
        extractor_clocks=[1, 1.5, 2, 2.5],
        limit=4,
    )
    safe = srv.commission_plan(**kw)
    real = srv.commission_plan(headroom_mw=live.power_report()["measured_headroom_mw"], **kw)

    def waves(out: str) -> int:
        return int(out.split(" wave(s)")[0].rsplit(", ", 1)[1])

    assert waves(real) <= waves(safe)


# ------------------------------------------------------------ water volumes


def test_water_pumps_group_by_the_body_they_draw_from(live):
    """OQ5 concluded they could not be matched to anything. The volume object is level
    geometry and is not in the save -- so its shape and capacity are genuinely unknowable
    -- but mExtractableResource names it, and that is enough to count separate shorelines."""
    water = live.water_volumes()
    if not water["pumps"]:
        pytest.skip("no water extractors built")
    assert sum(water["volumes"].values()) == water["pumps"]
    assert all(k.startswith("FGWaterVolume") for k in water["volumes"])


def test_sea_level_is_measured_rather_than_asserted(live):
    """Every pump has to be at it, so the pumps ARE the measurement. It turns "water must
    be drawn at sea level" from a rule of thumb into a number deck ordering can check."""
    water = live.water_volumes()
    if not water["pumps"]:
        pytest.skip("no water extractors built")
    assert water["sea_level_m"] is not None
    # They are all at the same height, which is what makes it sea level and not a mean.
    assert water["sea_level_span_m"] < 1.0


def test_the_water_warning_reports_the_bodies_and_the_level(game):
    out = srv.plan_factory(
        sources=["region:Spire Coast"],
        objective="max_mw",
        exports=["MW"],
        extractor_clocks=[1, 1.5, 2, 2.5],
        limit=2,
    )
    line = next(x for x in out.splitlines() if "Water Extractor(s)" in x)
    assert "distinct water bod" in line
    assert "sea level" in line
    # What is genuinely unknown is still said plainly.
    assert "shape is level geometry and is not in the save" in line
