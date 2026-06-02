"""Save-state reporting: worlds, a world summary, unlocks, power, sites.

Everything here answers 'what does this save contain', with no planning."""

from __future__ import annotations

from .. import config, render
from ..app import Limit, _state, mcp
from ..core.saveio import projection as proj


@mcp.tool(structured_output=False)
def list_worlds() -> str:
    """List save games grouped by world, newest first.

    Unsupported files are reported separately rather than failing the scan --
    pre-1.0 saves cannot be parsed at all.
    """
    try:
        worlds, unsupported = proj.list_worlds()
    except Exception as exc:
        return f"could not scan saves: {exc}"
    if not worlds and not unsupported:
        return f"no saves found under {config.saves_root()}"
    rows = []
    for w in worlds:
        newest = w.newest
        rows.append(
            (
                w.session_name,
                len(w.saves),
                f"{w.max_play_duration_s / 3600:.0f}h",
                newest["filename"],
                newest["save_version"],
                w.world_id,
            )
        )
    notes = []
    if unsupported:
        reasons: dict[str, int] = {}
        for u in unsupported:
            reasons[u["reason"]] = reasons.get(u["reason"], 0) + 1
        notes.append(
            f"{len(unsupported)} file(s) unreadable: "
            + "; ".join(f"{n}x {r}" for r, n in reasons.items())
        )
    return render.envelope(
        f"# {len(worlds)} world(s), {sum(len(w.saves) for w in worlds)} readable save(s)",
        render.table(("world", "saves", "played", "newest", "saveVer", "world_id"), rows),
        notes,
    )


@mcp.tool(structured_output=False)
def world_summary(save: str | None = None, world: str | None = None) -> str:
    """Progress, power and problems for one world."""
    try:
        st = _state(save, world)
    except Exception as exc:
        return f"could not read save: {exc}"
    g = st.game
    p = st.progression()
    pw = st.power_report()
    notes = []
    unbuilt = st.unlocked_but_unbuilt()
    if unbuilt:
        notes.append("unlocked but never built: " + ", ".join(g.buildings[c].name for c in unbuilt))
    if st.misconfigured:
        kinds: dict[str, int] = {}
        for m in st.misconfigured:
            kinds[m["cls"]] = kinds.get(m["cls"], 0) + 1
        notes.append(
            "no recipe set: "
            + ", ".join(
                f"{n}x {g.buildings[c].name if c in g.buildings else c}" for c, n in kinds.items()
            )
        )
    if st.paused:
        notes.append(f"{len(st.paused)} building(s) paused by the player")
    gen_rows = [
        (v["name"], v["count"], render.num(v["mw"]))
        for v in sorted(pw["by_generator"].values(), key=lambda v: -v["mw"])
    ]
    return render.envelope(
        "\n".join(
            [
                f"# {st.age_note}",
                render.kv(
                    [
                        ("phase", p["game_phase"]),
                        ("target", p["target_phase"]),
                        ("tier_complete", p["highest_complete_tier"]),
                        ("recipes", p["available_recipes"]),
                        ("alternates", len(st.unlocked_alternates)),
                        ("hard_drives_pending", len(st.hard_drive_offers)),
                    ]
                ),
                render.kv(
                    [
                        ("power_gen_MW", render.num(pw["generation_mw"])),
                        ("draw_MW", render.num(pw["draw_mw"])),
                        ("headroom_MW", render.num(pw["headroom_mw"])),
                    ]
                ),
                "milestones/tier: "
                + " ".join(f"T{t}:{v}" for t, v in p["milestones_by_tier"].items()),
            ]
        ),
        render.table(("generator", "count", "MW"), gen_rows),
        notes,
    )


@mcp.tool(structured_output=False)
def unlocked_recipes(
    save: str | None = None,
    world: str | None = None,
    only_alternates: bool = True,
    limit: Limit = 25,
) -> str:
    """Which recipes this world has. Defaults to alternates, never all 872."""
    try:
        st = _state(save, world)
    except Exception as exc:
        return f"could not read save: {exc}"
    picks = st.unlocked_alternates if only_alternates else st.unlocked_recipes("part")
    picks = sorted(picks, key=lambda r: r.name)
    page = picks[: render.clamp(limit, default=25)]
    rows = [(r.name, st.game.machine(r).name if st.game.machine(r) else "-") for r in page]
    return render.envelope(
        f"# {st.age_note}\n"
        f"# {len(st.unlocked_alternates)} of {len(st.game.alternates())} alternates unlocked; "
        f"{len(st.unlocked_recipes('part'))} automatable recipes total",
        render.table(("recipe", "building"), rows, total=len(picks), limit=limit),
    )


@mcp.tool(structured_output=False)
def power_report(save: str | None = None, world: str | None = None) -> str:
    """Generation capacity vs machine draw, nameplate AND measured.

    Nameplate is what everything built would draw running at once. Measured weights each
    machine by the 300 s productivity monitor the save already carries, which on a factory
    with idle blocks is a very different number -- and it is the one that says what is free
    right now. Both are shown because they answer different questions.
    """
    try:
        st = _state(save, world)
    except Exception as exc:
        return f"could not read save: {exc}"
    pw = st.power_report()
    rows = [
        (v["name"], v["count"], render.num(v["mw"]))
        for v in sorted(pw["by_generator"].values(), key=lambda v: -v["mw"])
    ]
    # This note used to read "nameplate only: fuel supply and uptime are not modelled".
    # The uptime was in the projection the whole time, on 520 of 566 records.
    notes = [
        (
            "nameplate headroom assumes every built machine runs at once -- the SAFE "
            f"bound. Measured weights {pw['monitored']} machine(s) by the last complete "
            "300s window and is what is free right now; utilisation is "
            f"{pw['utilisation']:.0%}"
        ),
        (
            f"{pw['unmonitored']} machine(s) carry no productivity monitor and are charged "
            "in FULL on both figures -- unknown utilisation must not read as idle"
        ),
        (
            "generation is capacity on both, because generators burn to meet demand "
            "rather than at a rate of their own"
        ),
    ]
    if pw["unmodellable"]:
        notes.append(f"not in game data, excluded: {', '.join(pw['unmodellable'])}")
    return render.envelope(
        f"# {st.age_note}\n"
        + render.kv(
            [
                ("generation_MW", render.num(pw["generation_mw"])),
                ("draw_MW_nameplate", render.num(pw["draw_mw"])),
                ("draw_MW_measured", render.num(pw["measured_draw_mw"])),
                ("headroom_MW_nameplate", render.num(pw["headroom_mw"])),
                ("headroom_MW_measured", render.num(pw["measured_headroom_mw"])),
                ("paused", pw["paused_count"]),
            ]
        ),
        render.table(("generator", "count", "MW"), rows),
        notes,
    )


@mcp.tool(structured_output=False)
def factory_sites(save: str | None = None, world: str | None = None, limit: Limit = 10) -> str:
    """Built production buildings clustered into sites, largest first."""
    try:
        st = _state(save, world)
    except Exception as exc:
        return f"could not read save: {exc}"
    g = st.game
    sites = st.sites()
    rows = []
    for s in sites[: render.clamp(limit)]:
        top = sorted(s["buildings"].items(), key=lambda kv: -kv[1])[:4]
        rows.append(
            (
                s["direction"],
                s["grid"],
                f"{int(s['centroid'][0] / 100)},{int(s['centroid'][1] / 100)}",
                s["count"],
                f"{s['diameter_m']}m",
                ", ".join(f"{n}x {g.buildings[c].name if c in g.buildings else c}" for c, n in top),
            )
        )
    return render.envelope(
        f"# {st.age_note}\n# {len(sites)} site(s); coords in metres",
        render.table(
            ("dir", "grid", "x,y(m)", "buildings", "spread", "contents"),
            rows,
            total=len(sites),
            limit=limit,
        ),
    )
