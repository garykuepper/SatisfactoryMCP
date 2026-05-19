"""Startup order, and why it is not a build order.

The re-frame that shrinks the whole problem: building costs materials, not power, and a
machine draws only when it runs. So the plant is constructed unpowered at leisure and the
question is what to switch on first. That removes the search over build partitions and
leaves a fixed machine set plus one hard constraint.

Hard, not advisory: exceeding available power in Satisfactory blows the fuse and stops the
WHOLE grid until it is reset by hand -- including the plant that was feeding it. Every
assertion about power below is an inequality that must never be violated, not a preference.
"""

from __future__ import annotations

import pytest

from satisfactory_mcp import server as srv
from satisfactory_mcp.planning.commission import commission
from satisfactory_mcp.planning.prepare import prepare

pytestmark = pytest.mark.integration

SPIRE = dict(
    objective="max_mw",
    sources=["region:Spire Coast"],
    exports=["MW"],
    extractor_clocks=[1.0, 1.5, 2.0, 2.5],
)
HEADROOM = 711.49


@pytest.fixture
def plan(game, state):
    return prepare(game, state, dict(SPIRE))


@pytest.fixture
def run(plan, game):
    return commission(plan, game, HEADROOM, "test")


# --------------------------------------------------------- the game rule


def test_generators_cost_nothing_to_energise(game):
    """The load-bearing fact, read from the dump rather than assumed. If generators drew
    power there would be no bootstrap at all."""
    gen = game.buildings["Build_GeneratorFuel_C"]
    assert gen.power_mw == 0.0
    assert gen.power_production_mw == 250.0
    assert game.buildings["Build_WaterPump_C"].power_mw == 20.0


# --------------------------------------------------------- the hard constraint


def test_no_wave_ever_draws_more_than_is_available(run):
    """The whole point. One megawatt over and the fuse blows on everything."""
    assert run.ok
    for w in run.waves:
        assert w.draw_mw <= w.available_before + 1e-6, (w.index, w.draw_mw, w.available_before)


def test_a_wave_never_pays_for_itself_with_its_own_generation(run):
    """Fill time. Between energising a wave's refineries and its generators actually
    burning fuel, the pipes are filling and nothing is coming back. Counting a wave's own
    generation against its own draw is what makes a sequence that trips halfway."""
    available = run.headroom_mw
    for w in run.waves:
        assert w.available_before == pytest.approx(available)
        assert w.draw_mw <= available + 1e-6
        available = available - w.draw_mw + w.generation_mw


def test_available_power_climbs_monotonically(run):
    """If it did not, the sequence would be walking backwards and could never finish."""
    seen = [w.available_after for w in run.waves]
    assert seen == sorted(seen)
    assert seen[-1] > run.headroom_mw


def test_it_converges_in_a_handful_of_waves(run):
    """Generation outruns draw by ~8x on this plan, so each wave pays for a much larger
    next one. Measured at four."""
    assert 1 <= len(run.waves) <= 6


# --------------------------------------------------------- partition


def test_every_machine_is_energised_exactly_once(run, plan):
    """A partition, not a re-solve. The union of the waves is the plan."""
    total = sum(p["machines"] for p in plan.solution.processes)
    assert run.machines == total
    for w in run.waves:
        for r in w.rows:
            assert r.machines > 0
    final = {}
    for w in run.waves:
        for r in w.rows:
            final[r.label] = (r.cumulative, r.total)
    assert all(cum == tot for cum, tot in final.values())


def test_cumulative_counts_never_exceed_the_plan(run):
    for w in run.waves:
        for r in w.rows:
            assert 0 < r.cumulative <= r.total


# --------------------------------------------------------- ordering


def test_upstream_comes_on_before_downstream_within_a_wave(run):
    """Rows are ordered by chain depth so the fluid is already moving when the next block
    lights. Extraction is depth 0."""
    for w in run.waves:
        assert [r.depth for r in w.rows] == sorted(r.depth for r in w.rows)
    assert any(r.depth == 0 and r.kind == "extractor" for r in run.waves[0].rows)


def test_power_is_kept_out_of_the_dependency_graph(game, state):
    """MW is modelled as an item so the power balance is just another row. Left in the
    dependency graph it would make every consumer depend on every generator and every
    generator on its fuel -- one component, and no order at all."""
    from satisfactory_mcp.planning.commission import _depths

    prepared = prepare(game, state, dict(SPIRE))
    depths = _depths(prepared.solution.processes)
    assert len(set(depths.values())) > 1
    extractors = [p["pid"] for p in prepared.solution.processes if p["kind"] == "extractor"]
    assert extractors and all(depths[pid] == 0 for pid in extractors)


def test_a_refinery_loop_does_not_hang_the_depth_pass(game, state):
    """Item flow is not always acyclic -- Residual Rubber legitimately feeds itself -- so
    depth is relaxed with a cap rather than topologically sorted."""
    from satisfactory_mcp.planning.commission import _depths

    procs = [
        {"pid": "a", "rates": {"x": 1.0, "y": -1.0}},
        {"pid": "b", "rates": {"y": 1.0, "x": -1.0}},
    ]
    depths = _depths(procs)
    assert set(depths) == {"a", "b"}


# --------------------------------------------------------- the floor


def test_the_minimum_slice_is_reported(run):
    """One machine of every process: the cheapest thing that still feeds the whole chain,
    and the floor no startup order can go under. Measured at 631 MW against 711 free --
    which is close enough that a player deserves to see the number."""
    assert run.minimum_slice_mw > 0
    assert run.minimum_slice_mw <= run.headroom_mw
    assert run.waves[0].draw_mw >= run.minimum_slice_mw - 1e-6


def test_too_little_headroom_refuses_rather_than_inventing_an_order(plan, game):
    """There is genuinely no answer below the floor, and the useful response is to say so
    and name the number -- not to emit a sequence that blows the fuse on step one."""
    run = commission(plan, game, 10.0, "test")
    assert not run.ok
    assert not run.waves
    assert any("no startup order exists" in w for w in run.warnings)
    assert any("smaller sub-plant" in w for w in run.warnings)


def test_a_bigger_headroom_needs_no_more_waves(plan, game):
    small = commission(plan, game, HEADROOM, "test")
    large = commission(plan, game, 50_000.0, "test")
    assert large.ok
    assert len(large.waves) <= len(small.waves)


# --------------------------------------------------------- the tool


def test_the_tool_prints_the_sequence(game):
    out = srv.commission_plan(**SPIRE)
    assert not out.startswith("! ")
    assert "wave\tchain\ton" in out
    assert "W1" in out


def test_headroom_is_printed_as_a_labelled_input(game):
    """So a sequence computed against a save that has since moved is visibly stale rather
    than quietly wrong."""
    out = srv.commission_plan(**SPIRE)
    assert "headroom_MW=" in out
    assert "source: power_report" in out
    given = srv.commission_plan(headroom_mw=5000.0, **SPIRE)
    assert "source: given by caller" in given


def test_the_tool_says_to_build_everything_first(game):
    """The re-frame, stated where it matters. A reader who takes these for build stages
    would sequence the construction for no reason."""
    out = srv.commission_plan(**SPIRE)
    assert "build EVERYTHING first" in out
    assert "switch-ons" in out


def test_the_tool_recommends_a_power_switch_per_block(game):
    """It has to be built in from the start, so it belongs with the sequence rather than
    in a footnote after the plant is up."""
    out = srv.commission_plan(**SPIRE)
    assert "Power Switch per block" in out
    assert "WHOLE grid" in out


def test_generator_rows_survive_truncation(game):
    """They sort last by chain depth, so a per-wave limit silently dropped exactly the
    rows that pay for the next wave."""
    out = srv.commission_plan(limit=12, **SPIRE)
    assert "Generator" in out
    # And truncation is announced rather than silent, so a short table does not read as
    # the whole sequence.
    assert "more: call again with offset" in out
