"""Project Assembly phases and Power Shard budgeting.

These were spliced in under the resources banner during a parallel merge and
are tools, not resources -- both read the save and one takes a hypothetical."""

from __future__ import annotations

from typing import Annotated

from pydantic import Field

from .. import render
from ..app import Limit, _origin_for, _state, mcp
from ..docs.constants import CAPABILITY_SCHEMATICS, max_clock, shards_for_clock
from ..spatial import geo


@mcp.tool(structured_output=False)
def phase_requirements(save: str | None = None, world: str | None = None) -> str:
    """What the Space Elevator still wants, live record and deprecated record apart.

    The per-phase item table in the save is DEPRECATED and frozen, so it is shown
    labelled rather than believed. Read the header line first.
    """
    try:
        st = _state(save, world)
    except Exception as exc:
        return f"could not read save: {exc}"
    g = st.game
    req = st.phase_requirements()

    rows = []
    for row in req["phases"]:
        outstanding = row["outstanding"]
        rows.append(
            (
                row["phase"] or f"?({row['egp']})",
                row["egp"],
                row["stale"],
                len(outstanding),
                len(row["complete"]),
                " + ".join(
                    f"{render.num(a)} {g.item_name(i)}"
                    for i, a in sorted(outstanding.items(), key=lambda kv: -kv[1])
                )
                or "-",
            )
        )

    target_row = next((r for r in req["phases"] if r["phase"] == req["target_phase"]), None)
    outstanding_total = sum(target_row["outstanding"].values()) if target_row else 0
    paid = req["paid_off_target"]

    notes = [
        (
            "the per-phase amounts come from mGamePhaseCosts, which FGGamePhaseManager.h "
            "marks 'DEPRECATED Only kept for save compatibility'. It is FROZEN: identical "
            "across all 29 parseable saves of the reference world (180h-316h), including "
            "the session that completed a whole phase. stale=stale rows are wrong."
        ),
        (
            "stale=usable means the phase has never been delivered into "
            "(mTargetGamePhasePaidOffCosts is empty), so its untouched snapshot is still "
            "its true full cost. That is the only row safe to plan against."
        ),
        (
            "[UNVERIFIED for MidGame/LateGame/FoodCourt] the EGP_* -> GP_Project_Assembly_"
            "Phase_N mapping is not in Docs.json (the UFGGamePhase assets do not ship) nor "
            "joinable in the save (the legacy mGamePhase scalar is absent = EGP_NA = "
            "migrated). EGP_EndGame -> Phase_3 IS measured: at 180-244h play the same world "
            "had target=Phase_3 with paid_off={Versatile Framework 2500}, matching that "
            "key's single settled item. The rest follow by enum order from that anchor."
        ),
    ]
    if not paid:
        notes.append(
            "nothing has been delivered toward the target phase yet "
            "(mTargetGamePhasePaidOffCosts absent = empty)"
        )

    return render.envelope(
        "\n".join(
            [
                f"# {st.age_note}",
                render.kv(
                    [
                        ("current_phase", req["current_phase"]),
                        ("target_phase", req["target_phase"]),
                        ("outstanding_on_target", outstanding_total or "-"),
                    ]
                ),
                "delivered to target: "
                + (
                    " + ".join(f"{render.num(a)} {g.item_name(i)}" for i, a in sorted(paid.items()))
                    or "nothing"
                ),
            ]
        ),
        render.table(("phase", "legacy_key", "trust", "outstanding", "done", "items"), rows),
        notes,
    )


@mcp.tool(structured_output=False)
def power_shards(
    save: str | None = None,
    world: str | None = None,
    plan_machines: int = 0,
    plan_clock: float = 2.5,
    limit: Limit = 10,
) -> str:
    """Power Shards held, committed and free, plus what an overclock plan would cost.

    ``plan_machines`` machines at ``plan_clock`` costs shards per machine; the answer
    says whether the free pool covers it.
    """
    try:
        st = _state(save, world)
    except Exception as exc:
        return f"could not read save: {exc}"
    budget = st.shard_budget()
    per_shard = max(budget["shard_items"].values()) if budget["shard_items"] else 0.0
    ceiling = max_clock(per_shard)

    notes = []
    if not budget["measured"]:
        notes.append(
            "[UNVERIFIED] this projection predates schema 9 and carries no "
            "InventoryPotential data, so committed=0 means unknown, not zero"
        )
    notes.append(
        f"a shard adds {render.num(per_shard)} max clock (mExtraPotential, from "
        f"Docs.json) and a building takes {budget['slots_per_building']} of them, so "
        f"max clock is {render.num(ceiling)}. The slot count is the only hardcoded "
        "number here -- mPotentialShardSlots is 0 on every building in the dump. [WIKI]"
    )
    if budget["slugs"]:
        held = ", ".join(
            f"{render.num(s['held'])} {s['name']} x{s['each']:g}" for s in budget["slugs"]
        )
        notes.append(
            f"craftable = uncrafted slugs carried, in crates or in the Dimensional "
            f"Depot: {held}. The 1/2/5 ratios come from the Power Shard (1)/(2)/(5) "
            "recipes, not from game knowledge. Craftable is POTENTIAL, not free -- "
            "crafting is a manual step"
        )

    if budget["by_place"]:
        where = "; ".join(
            f"{place}: " + ", ".join(f"{render.num(v)} {k}" for k, v in sorted(held.items()))
            for place, held in budget["by_place"].items()
        )
        notes.append(f"where they are -- {where}")

    idle = sum(h["idle"] for h in budget["holders"])
    if idle:
        notes.append(
            f"{idle} shard(s) sit in slots the current clock does not need. A shard "
            "raises the MAXIMUM clock; the slider is set separately, so committed is "
            "read from InventoryPotential and never derived from clock"
        )

    if plan_machines:
        need_each = shards_for_clock(plan_clock, per_shard)
        need = need_each * plan_machines
        short = need - budget["free"]
        line = (
            f"plan: {plan_machines} machine(s) at clock {render.num(plan_clock)} needs "
            f"{need_each} shard(s) each = {need}; free {render.num(budget['free'])}"
        )
        if short <= 0:
            notes.append(line + " -> affordable now")
        elif short <= budget["craftable"]:
            # Craftable, not free: the slugs cover it but somebody has to press craft.
            notes.append(
                line + f" -> SHORT by {render.num(short)}, but "
                f"{render.num(budget['craftable'])} more are craftable from slugs you "
                "already hold, so it is affordable after crafting"
            )
        else:
            notes.append(
                line + f" -> SHORT by {render.num(short)}; even crafting every slug "
                f"({render.num(budget['craftable'])}) leaves you "
                f"{render.num(need - budget['potential'])} short"
            )

    rows = [
        (h["cls"], render.num(h["clock"]), h["slotted"], h["needed"], h["idle"] or "")
        for h in budget["holders"][: render.clamp(limit, default=10)]
    ]
    return render.envelope(
        f"# {st.age_note}\n"
        + render.kv(
            [
                ("free", render.num(budget["free"])),
                ("craftable_from_slugs", render.num(budget["craftable"])),
                ("potential", render.num(budget["potential"])),
                ("committed", budget["committed"]),
                ("owned", render.num(budget["owned"])),
                ("overclocked_buildings", len(budget["holders"])),
            ]
        ),
        render.table(
            ("building", "clock", "slotted", "needed", "idle"),
            rows,
            total=len(budget["holders"]),
            limit=limit,
        ),
        notes,
    )


@mcp.tool(structured_output=False)
def mam_research(
    status: Annotated[
        str, Field(description="all | todo | affordable -- todo hides finished research")
    ] = "todo",
    search: Annotated[str | None, Field(description="filter by name, case-insensitive")] = None,
    save: str | None = None,
    world: str | None = None,
    limit: Limit = 25,
) -> str:
    """MAM research: what is left, what it costs, and what you can afford right now.

    The MAM is where CAPABILITIES live, as opposed to recipes -- the Dimensional Depot,
    the Power Augmenter, and Production Amplifier, which is the one that lets a
    Somersloop go into a machine at all.

    That last one has no flag in the save. `BP_UnlockSubsystem_C` records overclocking as
    `mIsBuildingOverclockUnlocked`, but nothing anywhere in the file records production
    amplification, so it is derived from the purchased-schematic set instead. Capability
    rows are marked LOCKS so it is obvious which research gates a tool argument rather
    than just adding a recipe.
    """
    try:
        st = _state(save, world)
    except Exception as exc:
        return f"could not read save: {exc}"

    g = st.game
    done = st.purchased_schematic_ids
    stock = st.stock()
    wanted = (status or "todo").strip().casefold()
    if wanted not in ("all", "todo", "affordable"):
        return f"! unknown status {status!r}. Choose from: all, todo, affordable"

    gates = {v: k for k, v in CAPABILITY_SCHEMATICS.items()}
    rows = []
    n_todo = 0
    for cls, s in sorted(g.schematics.items(), key=lambda kv: kv[1].name):
        if s.type != "EST_MAM":
            continue
        finished = cls in done
        if not finished:
            n_todo += 1
        if wanted != "all" and finished:
            continue
        if search and search.strip().casefold() not in (s.name or "").casefold():
            continue
        short = [(f, stock.get(f.item, 0.0)) for f in s.cost if stock.get(f.item, 0.0) < f.amount]
        if wanted == "affordable" and (short or finished):
            continue
        blocked = [
            g.schematics[d].name for d in s.dependencies if d in g.schematics and d not in done
        ]
        rows.append(
            (
                "DONE" if finished else ("BLOCKED" if blocked else ("short" if short else "READY")),
                s.name[:30],
                "LOCKS " + gates[cls] if cls in gates else "",
                ", ".join(f"{f.amount:g} {g.item_name(f.item)}" for f in s.cost)[:52] or "-",
                ", ".join(f"{f.amount - have:.0f} {g.item_name(f.item)}" for f, have in short)[:34],
                ", ".join(blocked)[:24],
            )
        )

    notes = [
        (
            "MAM research is where CAPABILITIES live, not just recipes -- 'LOCKS x' marks "
            "one that gates a feature of this MCP rather than adding a recipe"
        ),
        (
            "cost is checked against spendable stock only: carried, crates and the "
            "Dimensional Depot, never machine buffers"
        ),
    ]
    for name in CAPABILITY_SCHEMATICS:
        gate = st.research_gate(name)
        if gate is None:
            continue
        bill = ", ".join(f"{r['need']:g} {r['name']}" for r in gate["cost"])
        missing = ", ".join(f"{r['need'] - r['have']:.0f} {r['name']}" for r in gate["short"])
        verdict = "you can afford it now" if gate["affordable"] else f"short of {missing}"
        blocked = (
            " Blocked by " + ", ".join(gate["blocked_by"]) + " first." if gate["blocked_by"] else ""
        )
        notes.append(
            f"{name} is NOT researched: needs {gate['schematic_name']} in the MAM "
            f"({bill}) -- {verdict}.{blocked}"
        )

    return render.envelope(
        f"# {st.age_note}\n# {n_todo} MAM research node(s) outstanding, showing status={wanted}",
        render.table(
            ("status", "research", "capability", "cost", "short by", "blocked by"),
            rows[: render.clamp(limit, default=25)],
            total=len(rows),
            limit=limit,
        ),
        notes,
    )


@mcp.tool(structured_output=False)
def somersloops(save: str | None = None, world: str | None = None) -> str:
    """Somersloops held, slotted and owned -- the sibling of power_shards.

    `sloop_budget` has existed since sloops became spendable and nothing exposed it, so
    the only way to learn how many you had was to guess a `sloops=` budget and read the
    shortfall warning: you had to guess the budget to discover the budget.

    Free and committed are both exact. Slotted ones live in `InventoryPotential`, the same
    component as Power Shards, so this counts slot contents rather than inverting a boost
    multiplier.
    """
    try:
        st = _state(save, world)
    except Exception as exc:
        return f"could not read save: {exc}"

    budget = st.sloop_budget()
    gate = st.research_gate("production_boost")
    rows = [
        (
            h["name"],
            h["instance"][-18:],
            f"{h['sloops']:.0f}",
            f"{h['boost']:g}x" if h["boost"] else "",
        )
        for h in budget["holders"][:20]
    ]
    notes = []
    if gate is not None:
        bill = ", ".join(f"{r['need']:g} {r['name']}" for r in gate["cost"])
        notes.append(
            f"PRODUCTION AMPLIFIER IS NOT RESEARCHED, so none of these can go in a machine "
            f"yet. Research {gate['schematic_name']} in the MAM ({bill})"
        )
    notes.append(
        "only FREE sloops can fund a plan; committed ones are counted so you know there "
        "is something to pull out, not added to what is spendable"
    )
    notes.append(
        "free pools carried, crates and the Dimensional Depot -- the same set as "
        "power_shards, and never machine buffers"
    )
    if not budget["committed_measured"]:
        notes.append(
            "this projection predates schema 10, so slotted sloops are unreadable and "
            "'committed' is unknown rather than zero"
        )
    notes.append(
        "Mercer Spheres share the WAT prefix and do nothing for production, so they are "
        "reported apart and never added in"
    )
    return render.envelope(
        f"# {st.age_note}\n"
        + render.kv(
            [
                ("free", f"{budget['free']:.0f}"),
                ("committed", f"{budget['committed']:.0f}"),
                ("owned", f"{budget['owned']:.0f}"),
                ("where", ", ".join(f"{k} {v:.0f}" for k, v in budget["by_place"].items()) or "-"),
                ("mercer_spheres", f"{budget['mercer_spheres']:.0f}"),
            ]
        ),
        render.table(("building", "instance", "sloops", "boost"), rows),
        notes,
    )


#: Group names this tool used to accept, and what to say instead. Every one of them was a
#: name-prefix bucket; the map's placement table resolves the classes those buckets guessed
#: at, so the answer is a rename in some cases and "that is not a collectible" in others.
RETIRED_GROUPS: dict[str, str] = {
    "slug_blue": "power_slug_blue",
    "slug_yellow": "power_slug_yellow",
    "slug_purple": "power_slug_purple",
    "artifact_unsplit": (
        "the map resolves every glued BP_WAT name, so nothing is unsplit any more: "
        "ask for 'somersloop' or 'mercer_sphere'"
    ),
    "flora": (
        "'flora' mixed four classes. The mushroom is 'mushroom'; berry and nut bushes "
        "REGROW and are deliberately untracked; a spore flower is a hazard, not a pickup"
    ),
    "debris": "crash-site scenery is not a collectible -- see the unresolved rows in the census",
    "crash_site": (
        "the pod itself is 'crashed_drop_pod'; the ship and debris beside it are scenery, "
        "and the parts scattered around it are 'loot_cache'"
    ),
    "dropped_pickup": (
        "a map-placed cache is 'loot_cache'; a pickup the PLAYER dropped is not map-placed "
        "at all and appears among the unresolved rows"
    ),
}


def _hazard_tokens(hazard: dict) -> str:
    """The hazard block as a few tokens, distances in metres.

    Every one of these is inference from map geometry plus a radius the other actor's class
    declares -- see the note the tool prints. Gas is presence-only: the volume's shape is
    level geometry and is not in any file this reads.
    """
    out = []
    if hazard.get("hostiles_nearby"):
        n = sum(hazard["hostiles_nearby"].values())
        out.append(f"hostiles{n}@{(hazard.get('nearest_hostile_cm') or 0) / 100:.0f}m")
    if hazard.get("spawns_here"):
        out.append("spawner")
    if hazard.get("inside_spore_flower_damage_sphere"):
        out.append("spore")
    elif hazard.get("nearest_gas_cm"):
        out.append(f"gas@{hazard['nearest_gas_cm'] / 100:.0f}m")
    if hazard.get("nearest_uranium_cm"):
        out.append(f"uranium@{hazard['nearest_uranium_cm'] / 100:.0f}m")
    if hazard.get("nearest_nuclear_hog_spawner_cm"):
        out.append("nuclear-hog")
    return " ".join(out)


def _holds(row: dict, g) -> str:
    """What is in this one, where the map records it.

    A looted drop pod is reported as LOOTED and nothing else: its ``mUnlockCost`` is still
    on the actor after it has given up its hard drive, and quoting a price for something
    already taken is the kind of true-but-useless line a player acts on by mistake.
    """
    contents = row.get("contents") or {}
    if contents.get("item"):
        return f"{contents.get('count', 0):g} {g.item_name(contents['item'])}"
    if row.get("looted"):
        return "LOOTED"
    cost = row.get("unlock_cost") or {}
    if cost.get("item"):
        return f"wants {cost.get('amount', 0):g} {g.item_name(cost['item'])}"
    #: An unlooted pod that does not serialise mUnlockCost holds its class default, which
    #: the map cannot read. Still worth saying it is unlooted.
    return "unlooted, cost unknown" if row.get("looted") is False else ""


def _placement_table(rows: list[dict], g, distance: bool, total: int, limit: int) -> str:
    """One row per placement, with the empty optional columns dropped.

    Names are never truncated: ``(cell, name)`` is the only identity a placement has, and
    half a key joins to nothing. ``hazard`` and ``holds`` are annotations rather than
    identity, so a slug listing -- where both are always blank -- does not carry them.
    """
    hazard = [_hazard_tokens(r["hazard"]) for r in rows]
    holds = [_holds(r, g) for r in rows]
    optional = [("hazard", hazard), ("holds", holds)]
    shown = [(head, values) for head, values in optional if any(values)]
    body = [
        (
            r["category"],
            r["name"],
            *((f"{r['distance_m']:.0f}m",) if distance else ()),
            r["observed"] or ("collected" if r["collected"] else "-"),
            geo.grid_cell(r["pos"][0], r["pos"][1]),
            f"{int(r['pos'][0] / 100)},{int(r['pos'][1] / 100)}",
            f"{r['pos'][2] / 100:.0f}",
            *(values[i] for _head, values in shown),
        )
        for i, r in enumerate(rows)
    ]
    headers = (
        "category",
        "name",
        *(("dist",) if distance else ()),
        "observed",
        "grid",
        "x,y",
        "z",
        *(head for head, _values in shown),
    )
    return render.table(headers, body, total=total, limit=limit)


@mcp.tool(structured_output=False)
def collected_from_world(
    group: Annotated[
        str | None,
        Field(description="one category, e.g. 'power_slug_blue'. Omit to see them all"),
    ] = None,
    mode: Annotated[
        str,
        Field(description="census | collected | remaining | nearest"),
    ] = "census",
    near: Annotated[
        str | None,
        Field(
            description="origin for mode=nearest: 'x,y' in metres, 'me', or a factory name. "
            "Defaults to where the player is standing"
        ),
    ] = None,
    save: str | None = None,
    world: str | None = None,
    limit: Limit = 25,
) -> str:
    """Map collectibles: how many exist, how many you took, what is left and what is closest.

    Slugs, somersloops, Mercer spheres and their shrines, mushrooms, drop pods and the loot
    caches around them. Two sources, and neither is asked the other's question:

    * **the map** says what exists and where, read from the installed game's own cooked
      packages, so ``placed`` is exact and every coordinate is exact;
    * **the save** says what is gone. The world is not saved -- a save never mentions a slug
      still lying there -- so its destroyed-actor list *is* the collected list, and it is
      exact too. ``remaining`` is the subtraction of the two.

    Modes: ``census`` (default) counts every category; ``collected`` and ``remaining`` list
    individual placements with coordinates; ``nearest`` lists the remaining ones by distance
    from ``near``, defaulting to the player.

    A placement in a cell no save has ever loaded is counted as remaining and reported as
    ``never_streamed``. It is never called present -- the map says where it is and nothing
    on disk says whether it is still there.
    """
    try:
        st = _state(save, world)
    except Exception as exc:
        return f"could not read save: {exc}"

    wanted = (mode or "census").strip().casefold()
    if wanted not in ("census", "collected", "remaining", "nearest"):
        return f"! unknown mode {mode!r}. Choose from: census, collected, remaining, nearest"

    table = st.collectibles
    if group:
        # Category names are lowercase snake_case, so folding the argument is a
        # normalisation and not a guess. A retired bucket is renamed where the map has the
        # same thing under a new name, and refused where it does not.
        group = group.strip().casefold()
        hint = RETIRED_GROUPS.get(group)
        if hint and table is not None and hint in table.by_category:
            group = hint
        elif hint and table is not None:
            return f"! '{group}' is no longer a category: {hint}"

    if table is None and wanted in ("remaining", "nearest"):
        # An explicit refusal, not the census: answering a narrower question than the one
        # asked would teach the caller that the argument worked.
        return (
            f"! mode={wanted!r} needs the map's own placement table and "
            "data/world_collectibles.json is absent, so nothing here knows how many "
            "collectibles exist or where they are. Only mode=census and mode=collected "
            "work from a save alone. Regenerate with tools/gen_world_collectibles.py"
        )

    removed = st.removed_actors(group)
    if "error" in removed:
        return "! " + removed["error"]

    if table is None:
        return _save_only(st, removed, group, limit)
    if wanted == "census":
        return _census(st, removed, table, group, limit)
    return _listing(st, removed, table, group, wanted, near, limit)


#: Census columns beyond the four every save has. Rendered only when some row is non-zero:
#: ``gone_later`` needs an older save than the newest on disk, and ``unstated`` needs a table
#: newer than this code. Both are silent when they have nothing to say, and neither is
#: dropped from the arithmetic when they do.
_CONDITIONAL_COLUMNS: tuple[tuple[str, str], ...] = (
    ("gone_in_a_later_save", "gone_later"),
    ("unstated", "unstated"),
)


def _census(st, removed: dict, table, group: str | None, limit: int) -> str:
    """The per-category table: placed, collected, remaining, and how much is observed.

    ``group`` narrows the table to one category and takes its notes with it. The summary
    line stays whole-world and says so, because a scoped count under an unscoped header is
    how a category total gets read as a world total.
    """
    census = [r for r in removed["census"] if group is None or r["category"] == group]
    extra = [(key, head) for key, head in _CONDITIONAL_COLUMNS if any(r[key] for r in census)]
    rows = [
        (
            row["category"],
            row["placed"],
            row["collected"],
            "-" if row["remaining"] is None else row["remaining"],
            row["standing"],
            row["never_streamed"],
            *(row[key] for key, _head in extra),
        )
        for row in census
    ]

    notes = [
        (
            "placed is the MAP's own count and collected is THIS save's own destroyed list; "
            "both are exact, and remaining is their subtraction. Wherever remaining is a "
            "number, standing + never_streamed (+ any further column) adds up to it"
        ),
        (
            "never_streamed is a placement in a cell no save on disk has ever loaded. It "
            "counts as remaining because nothing collected it, and it is NOT present: the "
            "map says where it is and nothing says whether it is still there"
        ),
    ]
    untracked = [r for r in census if not r["state_tracked"]]
    if untracked:
        notes.append(
            "remaining is withheld (-) for "
            + ", ".join(f"{r['category']} ({r['cls']}, {r['placed']} placed)" for r in untracked)
            + ": no save on disk mentions that class at all, live or gone, so taking one "
            "would leave nothing to read and placed-minus-collected would be a fabrication"
        )
    pedestals = [r for r in census if r["pedestal_of"]]
    if pedestals:
        notes.append(
            "never add categories together: "
            + "; ".join(
                f"{r['category']} is the base {r['pedestal_of']} stands on, paired 1:1 by the "
                f"map's own AttachParent, so the two are {r['placed']} finds and not "
                f"{r['placed'] * 2}"
                for r in pedestals
            )
        )
    pods = next((r for r in census if r["looted_and_standing"]), None)
    if pods:
        notes.append(
            f"{pods['category']}: {pods['looted_and_standing']} of the {pods['standing']} "
            "standing ones are already LOOTED. A pod stays in the world after it is emptied "
            "-- only a dismantled one is destroyed -- so its remaining is not a count of "
            f"hard drives left. Use mode='remaining' group='{pods['category']}' to see which"
        )
    if removed["unresolved"]:
        notes.append(
            f"{removed['unresolved']} of the {removed['total']} destroyed records join no "
            "placement: either a class the map table excludes on purpose (crash-site "
            "scenery, regrowing berry and nut bushes, resource nodes) or an actor the map "
            "never placed -- which is what a pickup the PLAYER dropped is, and it shares its "
            "native class with the loot caches"
        )
    if (st.header.get("save_version") or 99) < 52:
        notes.append(
            "this save predates world partition, so its destroyed actors are keyed through "
            "Persistent_Level and the map places only 32 rows there: nearly everything will "
            "read as unresolved. Load a newer save of the same world for a real census"
        )

    if group and (note := table.note_for(group)):
        notes.append(f"{group}: {note}")

    body = [
        render.table(
            (
                "category",
                "placed",
                "collected",
                "remaining",
                "standing",
                "never_streamed",
                *(head for _key, head in extra),
            ),
            rows,
        )
    ]
    if group is None:
        stems = [
            (k, v, table.excluded_reason(k) or "") for k, v in removed["unresolved_stems"].items()
        ]
        body.append(
            render.table(
                ("name_stem", "destroyed", "why the map table has no row for it"),
                [(k, v, why[:96]) for k, v, why in stems[: render.clamp(limit, default=25)]],
                total=len(stems),
                limit=limit,
            )
        )
    body.append(render.ids_footer([(r["category"], r["cls"]) for r in census], "classes"))
    return render.envelope(
        f"# {st.age_note}\n"
        f"# map table: {len(table)} placements, {table.build}\n"
        # Whole-world figures, labelled as such: they do not narrow with `group`, and a
        # scoped table under an unscoped total is how one gets read as the other.
        + render.kv(
            [
                ("whole_world_collected", removed["resolved"]),
                ("destroyed_records", removed["total"]),
                ("unresolved", removed["unresolved"]),
                ("save_cells", removed["cells"]),
                ("showing", group or "every category"),
            ]
        ),
        "\n\n".join(b for b in body if b),
        notes,
    )


def _listing(st, removed: dict, table, group: str | None, mode: str, near, limit: int) -> str:
    """Individual placements: collected, remaining, or remaining by distance."""
    g = st.game
    origin = None
    where = ""
    if mode == "nearest":
        if near:
            try:
                origin, where = _origin_for(st, near)
            except ValueError as exc:
                return f"! {exc}"
        else:
            here = st.player_position()
            if here is None:
                return (
                    "! mode='nearest' needs an origin and this save has no player pawn: "
                    "pass near='x,y' in metres or a named factory"
                )
            origin, where = (here[0], here[1]), "you"
        rows = st.nearest_placements(origin, group)
    elif mode == "remaining":
        rows = st.placements(group, remaining_only=True)
        rows.sort(key=lambda r: (r["category"], r["name"]))
    else:
        rows = [p for p in st.placements(group) if p["collected"]]
        rows.sort(key=lambda r: (r["category"], r["name"]))

    # A shrine is the base its artifact stands on, so an unfiltered listing would show it as
    # a second row a metre from the sphere -- the double-count the census warns about, in
    # listing form. Dropped only when no group was asked for: group='mercer_shrine' means the
    # caller wants the pedestals.
    pedestals = sorted({c for c in table.by_category if table.pedestal_of(c)})
    hidden = 0
    if group is None:
        before = len(rows)
        rows = [r for r in rows if r["category"] not in pedestals]
        hidden = before - len(rows)

    shown = render.clamp(limit, default=25)
    counts: dict[str, int] = {}
    for r in rows:
        counts[r["observed"] or "collected"] = counts.get(r["observed"] or "collected", 0) + 1

    notes = []
    if mode == "collected":
        notes.append(
            "these are gone -- the coordinates say where they WERE. The map is the only "
            "source of a position here; a destroyed record carries none"
        )
    else:
        notes.append(
            "never_streamed rows are placements no save has ever loaded: the position is the "
            "map's and is exact, the state is unobserved. They are still remaining"
        )
    if origin is not None:
        notes.append(
            f"distance is planar metres from {where}, straight-line and not a walk: nothing "
            "here knows about cliffs, and a slug 80 m away can be 80 m up"
        )
    if any(r["hazard"] for r in rows[:shown]):
        notes.append(
            "the hazard column is INFERENCE, not placement: geometry between this placement "
            "and other map actors, plus the radii those actors' own classes declare. Gas is "
            "presence-only -- the volume's shape is level geometry and is in no file read here"
        )
    if group and (note := table.note_for(group)):
        notes.append(f"{group}: {note}")
    if group is None:
        notes.append(
            "every category at once. Pass group= to narrow it -- the names are in mode='census'"
        )
    if hidden:
        notes.append(
            f"{hidden} {'/'.join(pedestals)} row(s) are not shown: a shrine is the base its "
            "artifact stands on, 1:1 by the map's own AttachParent, so listing both would put "
            "two rows a metre apart for one find. Ask for it by group to see them"
        )

    return render.envelope(
        f"# {st.age_note}\n"
        f"# mode={mode}"
        + (f" group={group}" if group else " all categories")
        + (f" from {where}" if origin else "")
        + "\n"
        + render.kv([("rows", len(rows)), *sorted(counts.items())]),
        _placement_table(rows[:shown], g, origin is not None, total=len(rows), limit=limit),
        notes,
    )


def _save_only(st, removed: dict, group: str | None, limit: int) -> str:
    """The census a save can build alone: collected counts by name prefix, and wrong.

    Reached only when ``data/world_collectibles.json`` is absent. It is untracked, so a
    fresh clone lands here, and the honest thing is to answer with what the save does know
    while naming everything this costs.
    """
    notes = [
        (
            "DEGRADED: data/world_collectibles.json is absent, so there is no map table to "
            "join against. Groups below come from a name-prefix rule over the destroyed "
            "actors' instance names, which is measurably wrong -- on the reference save it "
            "misfiles 51 of 713 (40 yellow slugs read as blue) and leaves 65 undecidable. "
            "Regenerate the table with tools/gen_world_collectibles.py"
        ),
        (
            "collected, not remaining: without the map table nothing here knows how many of "
            "anything exists, so these are absolute counts and not a fraction of a total. "
            "mode=remaining and mode=nearest need the table and are unavailable"
        ),
        (
            "'artifact_unsplit' is names of the shape BP_WAT<n>, where the placement counter "
            "is glued onto a stem that is either BP_WAT1 (somersloop) or BP_WAT2 (Mercer "
            "sphere). Splitting them by name would be invention; the map table splits them"
        ),
        "'dropped_pickup' is loot the player dropped and re-collected, not a map collectible",
    ]
    if removed.get("other"):
        notes.append(
            "'other' is classes no prefix matched, reported rather than dropped: "
            + ", ".join(f"{k} {v}" for k, v in list(removed["other"].items())[:6])
        )
    if not removed["total"]:
        notes.append(
            "nothing recorded as removed. On a projection older than schema 11 that means "
            "unreadable rather than none -- re-read the save"
        )

    body = [
        render.table(("group", "collected"), [(k, str(v)) for k, v in removed["groups"].items()])
    ]
    if group is not None:
        rows = [
            (a["name"], a["cell"]) for a in removed["actors"][: render.clamp(limit, default=25)]
        ]
        body.append(
            render.table(("actor", "cell"), rows, total=len(removed["actors"]), limit=limit)
        )
    return render.envelope(
        f"# {st.age_note}\n"
        + render.kv([("total_removed", removed["total"]), ("map_cells", removed["cells"])]),
        "\n\n".join(body),
        notes,
    )
