"""The MCP surface itself: registration, and the context-budget invariants.

These are cheap and catch two classes of silent regression -- a tool whose schema
stops enforcing its row cap, and a response that quietly grows.
"""

from __future__ import annotations

import asyncio

import pytest

from satisfactory_mcp import server as srv

pytestmark = pytest.mark.integration

#: Per-response ceiling in characters. Generous, but a tool that blows past it is
#: almost certainly returning a whole table.
BUDGET = 4000


def _run(coro):
    return asyncio.run(coro)


def test_tools_resources_prompts_registered():
    tools = _run(srv.mcp.list_tools())
    resources = _run(srv.mcp.list_resources())
    prompts = _run(srv.mcp.list_prompts())
    assert len(tools) >= 17
    names = {t.name for t in tools}
    for expected in (
        "search_resource_nodes",
        "rank_build_sites",
        "plan_factory",
        "list_regions",
        "advise_hard_drive_pick",
    ):
        assert expected in names, expected
    assert {str(r.uri) for r in resources} == {
        "satisfactory://docs/summary",
        "satisfactory://save/current",
        "satisfactory://map/regions",
    }
    assert {p.name for p in prompts} == {
        "design_factory",
        "plan_power_plant",
        "pick_hard_drive",
    }


def test_no_tool_advertises_structured_output():
    """A tool annotated -> str otherwise duplicates its whole payload into
    structuredContent, a measured ~1.96x wire-size tax."""
    for tool in _run(srv.mcp.list_tools()):
        assert not tool.outputSchema, tool.name


def test_row_limits_are_schema_enforced():
    """The model must not be able to ask for 291 rows."""
    for tool in _run(srv.mcp.list_tools()):
        limit = (tool.inputSchema.get("properties") or {}).get("limit")
        if not limit:
            continue
        flat = str(limit)
        assert "25" in flat, f"{tool.name} limit is not capped: {limit}"


def test_tool_descriptions_stay_short():
    """Tool descriptions are always resident, so their first line is the budget that
    matters. Procedure belongs in prompts."""
    for tool in _run(srv.mcp.list_tools()):
        first = (tool.description or "").strip().splitlines()[0]
        assert len(first) <= 120, (tool.name, first)


@pytest.mark.parametrize(
    "uri",
    [
        "satisfactory://docs/summary",
        "satisfactory://save/current",
        "satisfactory://map/regions",
    ],
)
def test_resources_are_small(uri):
    out = _run(srv.mcp.read_resource(uri))
    text = out[0].content if isinstance(out, list) else str(out)
    assert 0 < len(text) < 1000


def test_docs_summary_reports_no_normalisation_warnings():
    out = _run(srv.mcp.read_resource("satisfactory://docs/summary"))
    text = out[0].content if isinstance(out, list) else str(out)
    assert "warnings=0" in text


@pytest.mark.parametrize(
    "name,kwargs",
    [
        ("search_items", {"query": "fuel"}),
        ("search_recipes", {"query": "turbo"}),
        ("alternates_for_item", {"item": "Plastic"}),
        ("list_buildings", {"kind": "generator"}),
        ("list_regions", {}),
        ("describe_location", {"x_m": 239, "y_m": -1928}),
        ("world_summary", {}),
        ("power_report", {}),
        ("unlocked_recipes", {}),
        ("factory_sites", {"limit": 5}),
        ("search_resource_nodes", {"sources": ["north"], "resource": "Crude Oil"}),
        ("rank_build_sites", {"resource": "Crude Oil", "sources": ["north"]}),
        ("plan_factory", {"objective": "max_mw", "sources": ["region:Spire Coast"]}),
        ("list_pending_hard_drive_choices", {}),
        ("advise_hard_drive_pick", {"hard_drive_id": 34}),
    ],
)
def test_response_fits_the_context_budget(name, kwargs):
    out = getattr(srv, name)(**kwargs)
    assert isinstance(out, str)
    assert out.strip()
    assert len(out) < BUDGET, f"{name} returned {len(out)} chars"


def test_prompts_render_with_arguments():
    res = _run(
        srv.mcp.get_prompt("design_factory", {"target_item": "Rubber", "rate_per_min": "120"})
    )
    text = "\n".join(m.content.text for m in res.messages)
    assert "Rubber" in text
    assert "120" in text
    assert "INFEASIBLE" in text  # the byproduct escape hatch must be spelled out
